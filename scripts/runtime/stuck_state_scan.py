#!/usr/bin/env python3
"""Scan for stuck system states + auto-fix the safe ones.

Detection targets:
  * CB entries demoted > 48h that aren't pruned yet (probably forgotten —
    daemon may be probing them indefinitely without making progress).
  * yaml ↔ live bifrost-routing-rules drift (a target in yaml that isn't
    in the live rule, or vice-versa). Most common after a manual bifrost
    UI edit or a crashed apply_pool_weights run.
  * Live rules where targets[].weight does NOT sum to 1.0 (rounding drift
    or partial-apply state). bifrost still routes by relative weight but
    the imbalance creates surprising fallback hit patterns.
  * sync_key_models drift — a key.models[] missing a model that any pool
    routes to it. Caught at runtime as "no keys found that support model"
    errors.

Auto-fix where SAFE:
  * Pool sum != 1.0 (rounding drift) → renormalize via apply_pool_weights
    on the next run (just flag — apply already idempotently renormalizes).
  * sync_key_models drift → call bifrost/sync_key_models.py directly.
Stuck CB entries are FLAGGED, never auto-acted on (could mask a real
provider outage from the operator).

Output goes to:
  ~/.coire/curator-pool/stuck_state.json — last scan digest
  ~/.coire/operator/logs/<date>.jsonl    — append flags via op-log

Designed to run via systemd timer ~ every 30min on .93.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML required")


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "scripts" / "runtime" / "pool_weights.yaml"
SYNC_KEY_SCRIPT = ROOT / "bifrost" / "sync_key_models.py"
CB_STATE = Path.home() / ".coire" / "curator-pool" / "circuit_state.json"
DIGEST = Path.home() / ".coire" / "curator-pool" / "stuck_state.json"
LOG_DIR = Path.home() / ".coire" / "operator" / "logs"

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
    sys.exit("FAIL: BIFROST_PASS not set in env or .env")
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()


STUCK_AGE_SEC = 48 * 3600
SUM_TOLERANCE = 0.001


def jget(path: str) -> dict:
    req = urllib.request.Request(f"{BIFROST_URL}{path}", headers={"Authorization": AUTH})
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def scan_stuck_cb() -> list[dict]:
    if not CB_STATE.exists():
        return []
    try:
        d = json.loads(CB_STATE.read_text())
    except Exception:
        return []
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    out = []
    for k, info in d.get("demoted", {}).items():
        if info.get("pruned"):
            continue
        first = info.get("first_demoted_at", now)
        age = now - first
        if age > STUCK_AGE_SEC:
            out.append({
                "key": k,
                "age_hours": round(age / 3600, 1),
                "fail_count": info.get("fail_count", 0),
                "daily_quota": info.get("daily_quota", False),
                "restore_at_iso": dt.datetime.fromtimestamp(
                    info.get("restore_at", 0), dt.timezone.utc).isoformat(),
            })
    return out


def scan_pool_sum_drift() -> list[dict]:
    try:
        rules = jget("/api/governance/routing-rules").get("rules", [])
    except Exception as e:
        return [{"pool": "_fetch_failed", "error": str(e)[:200]}]
    out = []
    for r in rules:
        targets = r.get("targets") or []
        if not targets:
            continue
        s = sum(t["weight"] for t in targets)
        if abs(s - 1.0) > SUM_TOLERANCE:
            out.append({
                "pool": r["name"],
                "sum": round(s, 4),
                "target_count": len(targets),
                "drift": round(abs(s - 1.0), 4),
            })
    return out


def scan_yaml_live_drift() -> list[dict]:
    """Compare yaml plan against live bifrost rules. Reports targets present
    in one but not the other (set-difference). Doesn't flag weight diffs —
    those are routine (CB / rebalance mutates them between apply runs)."""
    if not PLAN_PATH.exists():
        return []
    plan = yaml.safe_load(PLAN_PATH.read_text())
    try:
        rules = {r["name"]: r for r in jget("/api/governance/routing-rules").get("rules", [])}
    except Exception:
        return [{"_fetch_failed": True}]
    diffs = []
    for pool_name, pool_plan in plan.get("pools", {}).items():
        rule = rules.get(pool_name)
        if not rule:
            diffs.append({"pool": pool_name, "kind": "missing_live_rule"})
            continue
        plan_keys = {f"{t['provider']}/{t['model']}" for t in pool_plan.get("targets") or []}
        live_keys = {f"{t['provider']}/{t['model']}" for t in rule.get("targets") or []}
        only_in_plan = sorted(plan_keys - live_keys)
        only_in_live = sorted(live_keys - plan_keys)
        if only_in_plan or only_in_live:
            diffs.append({
                "pool": pool_name,
                "only_in_yaml": only_in_plan,
                "only_in_live": only_in_live,
            })
    return diffs


def scan_key_model_drift() -> list[dict]:
    """For every primary+fallback referenced in live rules, check that the
    matching provider's key.models[] includes the model. A model in a
    rule that isn't in any key.models[] triggers 'no keys found' errors
    at request time."""
    try:
        rules = jget("/api/governance/routing-rules").get("rules", [])
        providers = jget("/api/providers").get("providers", [])
    except Exception:
        return [{"_fetch_failed": True}]
    # provider -> union of all key.models[]
    served: dict[str, set[str]] = {}
    for p in providers:
        models: set[str] = set()
        for k in p.get("keys") or []:
            for m in k.get("models") or []:
                models.add(m)
        served[p["name"]] = models
    out = []
    for r in rules:
        for t in r.get("targets") or []:
            prov, model = t.get("provider"), t.get("model")
            if prov in served and model not in served[prov]:
                out.append({"pool": r["name"], "missing": f"{prov}/{model}",
                            "where": "primary"})
        for fb in r.get("fallbacks") or []:
            if isinstance(fb, str) and "/" in fb:
                prov, _, model = fb.partition("/")
                if prov in served and model not in served[prov]:
                    out.append({"pool": r["name"], "missing": fb,
                                "where": "fallback"})
    return out


def maybe_auto_fix_key_drift(drifts: list[dict], dry_run: bool) -> bool:
    """sync_key_models.py is idempotent; safe to invoke whenever key drift
    is detected. Returns True if invoked."""
    if not drifts or dry_run or not SYNC_KEY_SCRIPT.exists():
        return False
    print("  → key drift detected — running sync_key_models")
    try:
        subprocess.run([sys.executable, str(SYNC_KEY_SCRIPT)],
                       check=False, timeout=120)
        return True
    except Exception as e:
        print(f"  sync_key_models failed: {e}", file=sys.stderr)
        return False


def log_findings(digest: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "job": "stuck_state_scan",
        "stuck_cb": len(digest["stuck_cb"]),
        "pool_sum_drift": len(digest["pool_sum_drift"]),
        "yaml_live_drift": len(digest["yaml_live_drift"]),
        "key_model_drift": len(digest["key_model_drift"]),
    }
    with (LOG_DIR / f"{date}.jsonl").open("a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="scan only, don't run sync_key_models even if drift found")
    args = ap.parse_args()

    digest = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stuck_cb": scan_stuck_cb(),
        "pool_sum_drift": scan_pool_sum_drift(),
        "yaml_live_drift": scan_yaml_live_drift(),
        "key_model_drift": scan_key_model_drift(),
    }

    print(f"stuck-state scan @ {digest['ts']}")
    print(f"  stuck CB entries (>{STUCK_AGE_SEC//3600}h): {len(digest['stuck_cb'])}")
    for e in digest["stuck_cb"][:8]:
        print(f"    {e['key']}  age={e['age_hours']}h  fail={e['fail_count']}  "
              f"daily_q={e['daily_quota']}")
    print(f"  pool sum drift: {len(digest['pool_sum_drift'])}")
    for e in digest["pool_sum_drift"][:8]:
        print(f"    {e}")
    print(f"  yaml <-> live drift: {len(digest['yaml_live_drift'])}")
    for e in digest["yaml_live_drift"][:8]:
        print(f"    {e}")
    print(f"  key.models[] drift: {len(digest['key_model_drift'])}")
    for e in digest["key_model_drift"][:8]:
        print(f"    {e}")

    DIGEST.parent.mkdir(parents=True, exist_ok=True)
    DIGEST.write_text(json.dumps(digest, indent=2))

    # Auto-fix key drift (safe — sync_key_models is idempotent)
    maybe_auto_fix_key_drift(digest["key_model_drift"], args.dry_run)

    log_findings(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
