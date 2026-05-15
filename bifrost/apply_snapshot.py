#!/usr/bin/env python3
"""Apply bifrost/snapshot/routing-rules.json to live Bifrost.

Idempotent — diffs current state, only PUTs rules that changed. Skips providers
since those carry secrets (managed via .env / one-time UI setup).

Run on the host where Bifrost runs (usually .93).
"""
from __future__ import annotations
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT_DIR = HERE / "snapshot"

BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
USER = os.environ.get("BIFROST_USER", "admin")
PASS = os.environ.get("BIFROST_PASS", "")
if not PASS:
    # Try to read from .env
    env_file = HERE.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("BIFROST_PASS="):
                PASS = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not PASS:
        print("FAIL: BIFROST_PASS not set in env or .env", file=sys.stderr)
        sys.exit(1)

AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

def req(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(f"{BIFROST_URL}/api{path}", data=data, method=method,
        headers={"Authorization": AUTH, "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=15).read())
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {e.code} {method} {path}: {err_body}") from None


def normalize_rule(r):
    """Strip volatile fields for diff comparison."""
    drop = {"id", "config_hash", "created_at", "updated_at", "_meta"}
    return {k: v for k, v in r.items() if k not in drop}


def filter_rule_targets(rule, configured_providers):
    """Drop targets + fallbacks referencing providers not in `configured_providers`,
    then re-normalize remaining target weights to sum=1.0 (bifrost requirement).

    Without this, a partial-provider install (user set only GROQ_API_KEY) gets
    400 errors trying to PUT rules with cf-openai/cerebras/etc targets that
    bifrost doesn't know about. Skip silently — apply_pool_weights.py will
    fill in the canonical config from pool_weights.yaml afterward anyway.
    """
    rule = dict(rule)
    filtered = [t for t in rule.get("targets", []) if t.get("provider") in configured_providers]
    total = sum(float(t.get("weight", 0)) for t in filtered)
    if total > 0 and abs(total - 1.0) > 0.0001:
        filtered = [{**t, "weight": round(float(t["weight"]) / total, 4)} for t in filtered]
        # Fix rounding drift
        diff = round(1.0 - sum(t["weight"] for t in filtered), 4)
        if abs(diff) > 0.0001 and filtered:
            filtered[0] = {**filtered[0], "weight": round(filtered[0]["weight"] + diff, 4)}
    rule["targets"] = filtered
    rule["fallbacks"] = [
        fb for fb in rule.get("fallbacks", [])
        if "/" in fb and fb.split("/", 1)[0] in configured_providers
    ]
    return rule

def main():
    snapshot_path = SNAPSHOT_DIR / "routing-rules.json"
    if not snapshot_path.exists():
        print(f"FAIL: {snapshot_path} missing", file=sys.stderr)
        sys.exit(1)
    snap = json.loads(snapshot_path.read_text())
    snap_rules = snap.get("rules") or snap.get("routing_rules") or []
    print(f"snapshot: {len(snap_rules)} rules")

    live = req("GET", "/governance/routing-rules").get("rules", [])
    by_name = {r["name"]: r for r in live}

    # Build set of providers currently configured in bifrost. If a snapshot
    # rule references a missing provider, drop just that target/fallback
    # (don't fail the whole apply).
    configured = {p["name"] for p in req("GET", "/providers").get("providers", [])}

    changed = 0
    created = 0
    skipped = 0
    # Sort by target priority desc so priority shuffles don't collide
    # (bifrost enforces unique priority per scope).
    ordered = sorted(snap_rules, key=lambda r: r.get("priority", 0), reverse=True)
    for sr in ordered:
        name = sr["name"]
        filtered = filter_rule_targets(sr, configured)
        if not filtered.get("targets"):
            print(f"  - {name}: skipped (no configured-provider targets)")
            skipped += 1
            continue
        live_r = by_name.get(name)
        target = normalize_rule(filtered)
        if live_r:
            current = normalize_rule(live_r)
            if current == target:
                print(f"  = {name}: unchanged")
                continue
            body = {**target}
            req("PUT", f"/governance/routing-rules/{live_r['id']}", body)
            print(f"  ~ {name}: updated")
            changed += 1
        else:
            req("POST", "/governance/routing-rules", target)
            print(f"  + {name}: created")
            created += 1

    # Detect rules in live that aren't in snapshot — orphans, warn but don't delete
    snap_names = {sr["name"] for sr in snap_rules}
    for ln in by_name:
        if ln not in snap_names:
            print(f"  ! {ln}: in live but NOT in snapshot — manual delete if undesired")

    suffix = f", {skipped} skipped (missing providers)" if skipped else ""
    print(f"\n{changed} updated, {created} created{suffix}")


if __name__ == "__main__":
    main()
