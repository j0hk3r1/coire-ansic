#!/usr/bin/env python3
"""Adaptive weight rebalancer — adjusts pool_weights.yaml daily based on
24h utilization. Bounded ±10% per cycle to avoid oscillation.

Rules:
- Provider util >75% of daily cap → reduce all its target weights ×0.9
- Provider util <5% of daily cap (when cap >100) → increase weights ×1.05
- Renormalize each pool to sum=1.0
- Never reduce a target below 0.02 (floor) or raise above 0.30 (ceiling)
- Skip provider entries that aren't in PROVIDER_QUOTAS (no cap data)

Idempotent. Writes pool_weights.yaml in place (backup .bak). Logs diff
to ~/.hermes/curator-pool/rebalance_history.jsonl. Apply via
apply_pool_weights.py.

Designed to run as systemd timer (op-rebalance.timer, daily at ~01:30 UTC
after midnight resets settle).
"""
from __future__ import annotations
import base64, datetime as dt, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "scripts" / "runtime" / "pool_weights.yaml"
HISTORY = Path.home() / ".hermes" / "curator-pool" / "rebalance_history.jsonl"

BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:9118")

FLOOR_WEIGHT = 0.02
CEIL_WEIGHT = 0.30
RAISE_FACTOR = 1.05
LOWER_FACTOR = 0.90
HIGH_UTIL_THRESHOLD = 0.75  # >=75% → reduce
LOW_UTIL_THRESHOLD = 0.05   # <=5% → raise (only if cap meaningful)
SATURATED_DEMOTE_COUNT = 2  # if >= N models of a provider are daily-cap-demoted,
                             # treat the whole provider as saturated and do NOT raise.

CB_STATE_PATH = Path.home() / ".hermes" / "curator-pool" / "circuit_state.json"


def fetch_usage(max_attempts: int = 4):
    """GET /api/usage_estimates from dashboard. Retries on transient
    network errors (timeout, 5xx) with exponential backoff.

    Dashboard occasionally times out under load (e.g. computing usage from
    cached bifrost logs takes >15s). Previous version failed the entire
    rebalance run on first timeout — now retries 4x: 2s, 5s, 15s, 30s.
    """
    delays = [2, 5, 15, 30]
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        try:
            req = urllib.request.Request(f"{DASHBOARD_URL}/api/usage_estimates")
            d = json.loads(urllib.request.urlopen(req, timeout=30).read())
            return d.get("estimates", [])
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            if attempt + 1 < max_attempts:
                wait = delays[attempt]
                print(f"  fetch_usage attempt {attempt+1}/{max_attempts} failed ({e!s:.60}); retry in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"fetch_usage gave up after {max_attempts} attempts; last error: {last_err}")


def saturated_providers() -> set:
    """Read CB state directly — return set of providers with >= N daily-quota
    demoted targets. These have hit a hidden cap (e.g. cf-openai neurons, not
    visible in request-count usage data) and must NOT be weight-raised even if
    their RPD utilization looks low."""
    if not CB_STATE_PATH.exists():
        return set()
    try:
        d = json.loads(CB_STATE_PATH.read_text())
    except Exception:
        return set()
    counts = {}
    for k, v in d.get("demoted", {}).items():
        if not v.get("daily_quota"):
            continue
        prov = k.split("/", 1)[0]
        counts[prov] = counts.get(prov, 0) + 1
    return {p for p, n in counts.items() if n >= SATURATED_DEMOTE_COUNT}


def load_plan():
    try:
        import yaml
    except ImportError:
        sys.exit("FAIL: PyYAML required")
    return yaml.safe_load(PLAN_PATH.read_text())


def save_plan(plan):
    try:
        import yaml
    except ImportError:
        sys.exit("FAIL: PyYAML required")
    # Preserve top comment block
    text = PLAN_PATH.read_text()
    head_end = text.find("\npools:")
    header = text[:head_end + 1] if head_end > 0 else ""
    body = yaml.safe_dump({"pools": plan["pools"]}, sort_keys=False, default_flow_style=False)
    # Backup
    bak = PLAN_PATH.with_suffix(".yaml.bak")
    bak.write_text(text)
    PLAN_PATH.write_text(header + body if header else body)


def classify_providers(estimates, saturated):
    """Return dict: provider -> 'high' | 'low' | None (no action).

    `saturated` overrides "low" classification — when CB has demoted multiple
    daily-quota models from a provider, treat the provider as exhausted
    regardless of what request-count utilization suggests.
    """
    out = {}
    for e in estimates:
        prov = e["provider"]
        cap = e.get("cap_estimated") or 0
        used = e.get("used_24h", 0)
        if prov in saturated:
            out[prov] = "high"  # treat as if already over-utilized
            continue
        if not cap or cap <= 0:
            out[prov] = None  # no cap → can't reason
            continue
        util = used / cap
        if util >= HIGH_UTIL_THRESHOLD:
            out[prov] = "high"
        elif util <= LOW_UTIL_THRESHOLD and cap >= 100:
            out[prov] = "low"
        else:
            out[prov] = None
    return out


def adjust(plan, classifications):
    """Apply ±factor to each target's weight by provider class. Renormalize per pool."""
    changes = []  # list of (pool, provider, model, old_w, new_w)
    for pool_name, pool in plan["pools"].items():
        for t in pool["targets"]:
            prov = t["provider"]
            cls = classifications.get(prov)
            if cls is None:
                continue
            old_w = t["weight"]
            if cls == "high":
                new_w = max(FLOOR_WEIGHT, round(old_w * LOWER_FACTOR, 4))
            elif cls == "low":
                new_w = min(CEIL_WEIGHT, round(old_w * RAISE_FACTOR, 4))
            else:
                continue
            if abs(new_w - old_w) < 0.001:
                continue
            t["weight"] = new_w
            changes.append((pool_name, prov, t["model"], old_w, new_w))
        # Renormalize pool to sum=1.0
        total = sum(t["weight"] for t in pool["targets"])
        if total > 0 and abs(total - 1.0) > 0.001:
            for t in pool["targets"]:
                t["weight"] = round(t["weight"] / total, 4)
            # Fix rounding drift on largest entry
            diff = round(1.0 - sum(t["weight"] for t in pool["targets"]), 4)
            if abs(diff) > 0.0001:
                pool["targets"].sort(key=lambda x: -x["weight"])
                pool["targets"][0]["weight"] = round(pool["targets"][0]["weight"] + diff, 4)
    return changes


def log_history(changes, classifications):
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "classifications": classifications,
        "changes": [
            {"pool": c[0], "provider": c[1], "model": c[2], "old": c[3], "new": c[4]}
            for c in changes
        ],
        "change_count": len(changes),
    }
    with HISTORY.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    estimates = fetch_usage()
    sat = saturated_providers()
    classifications = classify_providers(estimates, sat)
    print("=== utilization classification ===")
    if sat:
        print(f"  saturated (CB daily-quota demotes): {sorted(sat)}")
    for prov, cls in sorted(classifications.items()):
        flag = cls or "skip"
        print(f"  {prov:15} {flag}")
    if not any(c in ("high", "low") for c in classifications.values()):
        print("\nNo providers in high/low band — no rebalance needed.")
        log_history([], classifications)
        return

    plan = load_plan()
    changes = adjust(plan, classifications)
    if not changes:
        print("\nNo weight changes after clamping (all near floor/ceiling).")
        log_history([], classifications)
        return

    print(f"\n=== {len(changes)} weight adjustments ===")
    for pool, prov, model, old, new in changes:
        arrow = "↓" if new < old else "↑"
        print(f"  {pool:8} {prov:14}/{model:35} {old:.3f} {arrow} {new:.3f}")

    save_plan(plan)
    log_history(changes, classifications)
    print(f"\nPlan written to {PLAN_PATH}")
    print(f"Run: python3 scripts/runtime/apply_pool_weights.py")


if __name__ == "__main__":
    main()
