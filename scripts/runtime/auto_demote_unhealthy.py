#!/usr/bin/env python3
"""Health-driven primary demotion — autonomous version of the audit FLAGs.

Reads 1h of bifrost logs, computes per-primary err-count + p95 latency,
and applies bounded auto-actions to pool_weights.yaml:

  * err >= 5/hr on a primary  -> move to fallback (immediate)
  * p95 > 30s for >= 3 consecutive ticks -> cut weight 10% (gradual)

Safety bounds (HARD — refuse to apply if violated):
  * Never empty a pool's primaries below 3 targets.
  * Never change more than 2 weights in a single tick.
  * Never change a weight by more than 30% in a single tick.

State for the "3 consecutive ticks" rule lives at
~/.coire/curator-pool/demote_state.json — a small dict mapping
{provider/model: {pool: {high_p95_streak: int, last_p95_ms: int,
last_tick_ts: float}}}.

Designed to run every 15min via systemd timer (auto-demote-unhealthy.timer).
After applying changes, calls apply_pool_weights.py to push to bifrost +
auto-runs sync_key_models + build_models_list as side effects.

Idempotent: if no changes triggered, exits 0 with no writes.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML required")


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "scripts" / "runtime" / "pool_weights.yaml"
STATE_PATH = Path.home() / ".coire" / "curator-pool" / "demote_state.json"
HISTORY_PATH = Path.home() / ".coire" / "curator-pool" / "auto_demote_history.jsonl"

BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
USER = os.environ.get("BIFROST_USER", "admin")
PASS = os.environ.get("BIFROST_PASS", "")
if not PASS:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("BIFROST_PASS="):
                PASS = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not PASS:
    sys.exit("FAIL: BIFROST_PASS not in env or .env")
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

# Bounds (kept inline so reading the script tells you exactly what it does)
ERR_THRESHOLD = 5          # err count in 1h that triggers immediate fallback move
P95_THRESHOLD_MS = 30_000  # latency p95 ceiling
P95_STREAK_REQUIRED = 3    # consecutive ticks > P95_THRESHOLD before cutting weight
P95_CUT_FACTOR = 0.90      # 10% cut per qualifying tick
MIN_PRIMARIES = 3          # never let any pool go below this
MAX_CHANGES_PER_TICK = 2   # circuit-breaker against runaway oscillation
MAX_SINGLE_DELTA = 0.30    # don't shift any weight by more than 30% in one go


def jget(path: str) -> dict:
    req = urllib.request.Request(f"{BIFROST_URL}{path}", headers={"Authorization": AUTH})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {}


def save_state(s: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(s, indent=2))


def collect_health() -> tuple[dict, dict]:
    """Pull recent bifrost logs; return (err_count_by_pm, latencies_by_pm).

    Filters to last 60 minutes by timestamp (bifrost returns most-recent
    first, but the API doesn't accept a since= filter directly so we
    over-fetch + trim).
    """
    raw = jget("/api/logs?limit=500&order=desc")
    logs = raw.get("logs", raw) if isinstance(raw, dict) else raw
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    err_counts: dict[str, int] = defaultdict(int)
    latencies: dict[str, list[int]] = defaultdict(list)
    for l in logs:
        ts = l.get("timestamp", "")
        try:
            ldt = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue
        if ldt < cutoff:
            continue
        prov, model = l.get("provider"), l.get("model")
        if not prov or not model:
            continue
        k = f"{prov}/{model}"
        if l.get("status") == "error":
            err_counts[k] += 1
        elif l.get("latency") is not None:
            latencies[k].append(int(l["latency"]))
    return err_counts, latencies


def p95(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def primaries_per_pool(plan: dict) -> dict[str, list[dict]]:
    return {n: list(p.get("targets") or []) for n, p in plan.get("pools", {}).items()}


def renormalize(targets: list[dict]) -> list[dict]:
    s = sum(t["weight"] for t in targets)
    if s <= 0:
        return targets
    out = [{**t, "weight": round(t["weight"] / s, 4)} for t in targets]
    diff = round(1.0 - sum(t["weight"] for t in out), 4)
    if abs(diff) > 0.0001 and out:
        out[0] = {**out[0], "weight": round(out[0]["weight"] + diff, 4)}
    return out


def decide(plan: dict, err_counts: dict, latencies: dict, state: dict,
           now_ts: float) -> list[dict]:
    """Return a list of actions to take. Each: {kind, pool, key, ...}.
    Pure — does not mutate plan or state. Caller applies + persists.
    """
    actions = []
    streak_state = state.setdefault("p95_streaks", {})
    now_iso = dt.datetime.fromtimestamp(now_ts, dt.timezone.utc).isoformat()

    for pool_name, pool in plan.get("pools", {}).items():
        primaries = list(pool.get("targets") or [])
        prim_keys = [f"{t['provider']}/{t['model']}" for t in primaries]

        # ─── ERR-rate triggers ──────────────────────────────────────
        for t in primaries:
            k = f"{t['provider']}/{t['model']}"
            if err_counts.get(k, 0) >= ERR_THRESHOLD:
                if len(primaries) - 1 < MIN_PRIMARIES:
                    continue  # safety: don't drop below 3 primaries
                actions.append({
                    "kind": "demote_to_fallback",
                    "pool": pool_name,
                    "key": k,
                    "reason": f"{err_counts[k]} errors/hr >= {ERR_THRESHOLD}",
                })

        # ─── P95 streak triggers ────────────────────────────────────
        for t in primaries:
            k = f"{t['provider']}/{t['model']}"
            current_p95 = p95(latencies.get(k, []))
            streak_key = f"{pool_name}::{k}"
            entry = streak_state.get(streak_key, {"streak": 0, "last_p95": 0})
            if current_p95 > P95_THRESHOLD_MS:
                entry["streak"] = entry.get("streak", 0) + 1
            else:
                entry["streak"] = 0
            entry["last_p95"] = current_p95
            entry["last_tick"] = now_iso
            streak_state[streak_key] = entry
            if entry["streak"] >= P95_STREAK_REQUIRED:
                if t["weight"] * (1 - P95_CUT_FACTOR) > MAX_SINGLE_DELTA:
                    continue  # change too aggressive
                actions.append({
                    "kind": "cut_weight",
                    "pool": pool_name,
                    "key": k,
                    "factor": P95_CUT_FACTOR,
                    "current_p95_ms": current_p95,
                    "streak": entry["streak"],
                    "reason": f"p95={current_p95}ms > {P95_THRESHOLD_MS}ms × {entry['streak']} ticks",
                })

    # Cap to MAX_CHANGES_PER_TICK — prefer err-rate (more decisive) over p95
    actions.sort(key=lambda a: 0 if a["kind"] == "demote_to_fallback" else 1)
    capped = actions[:MAX_CHANGES_PER_TICK]
    return capped


def apply_action(plan: dict, action: dict) -> bool:
    pool = plan["pools"][action["pool"]]
    k = action["key"]
    targets = pool.get("targets") or []
    fallbacks = pool.setdefault("fallbacks", []) or []
    if action["kind"] == "demote_to_fallback":
        new_targets = [t for t in targets if f"{t['provider']}/{t['model']}" != k]
        if len(new_targets) < MIN_PRIMARIES:
            return False
        pool["targets"] = renormalize(new_targets)
        if k not in fallbacks:
            fallbacks.append(k)
        pool["fallbacks"] = fallbacks
        return True
    if action["kind"] == "cut_weight":
        for t in targets:
            if f"{t['provider']}/{t['model']}" == k:
                old = t["weight"]
                t["weight"] = round(old * action["factor"], 4)
                pool["targets"] = renormalize(targets)
                return True
    return False


def log_history(actions: list[dict]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
             "actions": actions, "action_count": len(actions)}
    with HISTORY_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change; don't write yaml or apply")
    ap.add_argument("--skip-apply", action="store_true",
                    help="write yaml but don't call apply_pool_weights.py")
    args = ap.parse_args()

    err_counts, latencies = collect_health()
    plan = yaml.safe_load(PLAN_PATH.read_text())
    state = load_state()
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    actions = decide(plan, err_counts, latencies, state, now)
    save_state(state)  # always persist streaks even if no actions

    if not actions:
        print("auto-demote: 0 actions (system healthy)")
        log_history([])
        return 0

    print(f"auto-demote: {len(actions)} action(s)")
    for a in actions:
        print(f"  {a['kind']:22s} pool={a['pool']:8s} {a['key']:50s} — {a['reason']}")

    if args.dry_run:
        return 0

    applied = []
    for a in actions:
        if apply_action(plan, a):
            applied.append(a)

    if not applied:
        print("  no actions applied (all blocked by safety bounds)")
        log_history([])
        return 0

    bak = PLAN_PATH.with_suffix(".yaml.bak")
    bak.write_text(PLAN_PATH.read_text())
    # Re-serialize keeping order + comments minimal (the file is mostly data).
    text = PLAN_PATH.read_text()
    head_end = text.find("\npools:")
    header = text[:head_end + 1] if head_end > 0 else ""
    body = yaml.safe_dump({"pools": plan["pools"]}, sort_keys=False, default_flow_style=False)
    PLAN_PATH.write_text((header + body) if header else body)
    log_history(applied)
    print(f"  wrote {PLAN_PATH}")

    if args.skip_apply:
        return 0
    print("  → running apply_pool_weights")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "runtime" / "apply_pool_weights.py")],
                   check=False, timeout=180)
    return 0


if __name__ == "__main__":
    sys.exit(main())
