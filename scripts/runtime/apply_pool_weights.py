#!/usr/bin/env python3
"""Apply a desired weights+fallbacks plan to bifrost routing rules.

Reads a YAML plan with target weights per pool, fetches existing rules from
bifrost, replaces targets with the planned set (only models present in the
plan), preserves cel_expression/priority/scope, and PUTs the updated rule.

This is reused by the daily curator and by ops one-shots when capacity-aware
re-weighting is needed (e.g. lift cerebras/nvidia-nim, lower mistral churn).

Plan file path defaults to scripts/runtime/pool_weights.yaml relative to repo
root; override with --plan PATH.
"""
from __future__ import annotations
import argparse, base64, json, os, sys, urllib.request, urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN = ROOT / "scripts" / "runtime" / "pool_weights.yaml"
CB_STATE = Path.home() / ".hermes" / "curator-pool" / "circuit_state.json"

BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
BIFROST_USER = os.environ.get("BIFROST_USER", "admin")
_BPASS = os.environ.get("BIFROST_PASS")
if not _BPASS:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("BIFROST_PASS="):
                _BPASS = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not _BPASS:
    sys.exit("FAIL: BIFROST_PASS not in env or .env")

AUTH = "Basic " + base64.b64encode(f"{BIFROST_USER}:{_BPASS}".encode()).decode()

def req(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(f"{BIFROST_URL}/api{path}", data=data, method=method,
        headers={"Authorization": AUTH, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=20).read())

def load_plan(path: Path):
    text = path.read_text()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)

def normalize(targets):
    s = sum(t["weight"] for t in targets)
    if s <= 0: return targets
    out = [{**t, "weight": round(t["weight"] / s, 4)} for t in targets]
    diff = round(1.0 - sum(t["weight"] for t in out), 4)
    if abs(diff) > 0.0001 and out:
        out[0] = {**out[0], "weight": round(out[0]["weight"] + diff, 4)}
    return out

def load_demoted_keys() -> set:
    """Returns set of 'provider/model' strings currently demoted by circuit
    breaker. Entries flagged daily_quota or pruned should NOT be re-added to
    pools — they will just generate 429/error traffic until their cooldown
    elapses. Daemon's own restore path handles re-adding via half-weight ramp.
    """
    if not CB_STATE.exists():
        return set()
    try:
        d = json.loads(CB_STATE.read_text())
        return set(d.get("demoted", {}).keys())
    except Exception:
        return set()

def load_configured_providers() -> set:
    """Providers actually registered in bifrost. Pool targets / fallbacks
    referencing a not-yet-configured provider are skipped — otherwise
    bifrost cheerfully creates rules with missing-provider targets, then
    500s when traffic gets routed to them."""
    try:
        return {p["name"] for p in req("GET", "/providers").get("providers", [])}
    except Exception:
        return set()

def apply(plan, dry_run=False):
    rules = {r["name"]: r for r in req("GET", "/governance/routing-rules").get("rules", [])}
    demoted = load_demoted_keys()
    configured = load_configured_providers()
    if demoted:
        print(f"  [info] circuit-breaker has {len(demoted)} demoted target(s) — they will be filtered from this plan:")
        for k in sorted(demoted): print(f"    skip: {k}")
    if configured:
        print(f"  [info] {len(configured)} provider(s) configured in bifrost: {sorted(configured)}")
    changed = 0
    created = 0
    for pool_name, pool_plan in plan.get("pools", {}).items():
        rule = rules.get(pool_name)
        is_new = rule is None
        if is_new:
            # Create new routing rule with sensible defaults; apply loop below
            # will then PUT the planned weights into it.
            print(f"\n=== {pool_name} (CREATING NEW) ===")
            if dry_run:
                continue
            create_body = {
                "name": pool_name,
                "description": pool_plan.get("description", f"Pool '{pool_name}' from pool_weights.yaml"),
                "enabled": True,
                "cel_expression": f'model == "{pool_name}"',
                "targets": [],  # filled in below
                "fallbacks": [],
                "scope": "global",
                "scope_id": None,
                "priority": 0,
            }
            try:
                res = req("POST", "/governance/routing-rules", create_body)
                rule = res.get("rule") or res
                created += 1
                print(f"  ✓ created rule id={rule.get('id')}")
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")[:400]
                print(f"  ✗ CREATE FAIL: HTTP {e.code} {err_body}", file=sys.stderr)
                continue
        new_targets = []
        for t in pool_plan.get("targets", []):
            key = f"{t['provider']}/{t['model']}"
            if key in demoted:
                continue  # CB has this demoted — skip, daemon will re-add when smoke passes
            if configured and t["provider"] not in configured:
                continue  # provider not registered in bifrost (no key in .env) — skip
            new_targets.append({"provider": t["provider"], "model": t["model"], "weight": float(t["weight"])})
        if not new_targets:
            reason = "no configured-provider targets" if configured else "all planned targets are CB-demoted"
            # If an EXISTING rule has no valid targets after filter, delete it
            # so routing fails fast instead of 500-ing on missing-provider targets.
            if rule and rule.get("id") and not dry_run:
                try:
                    req("DELETE", f"/governance/routing-rules/{rule['id']}")
                    print(f"  [DELETED] pool '{pool_name}' — {reason}", file=sys.stderr)
                except urllib.error.HTTPError as e:
                    print(f"  [SKIP] pool '{pool_name}' — {reason} (delete failed: HTTP {e.code})", file=sys.stderr)
            else:
                print(f"  [SKIP] pool '{pool_name}' — {reason}", file=sys.stderr)
            continue
        new_targets = normalize(new_targets)
        new_fallbacks = [
            fb for fb in (pool_plan.get("fallbacks") or rule.get("fallbacks", []))
            if fb not in demoted
            and (not configured or ("/" in fb and fb.split("/", 1)[0] in configured))
        ]

        # Compare as sets — bifrost stores numeric weights with reduced
        # precision (0.30 → 0.3) and reorders ties by internal heuristics.
        # Round weights to 4 decimals and ignore order for idempotency.
        def _norm(targets):
            return sorted((t["provider"], t["model"], round(float(t["weight"]), 4)) for t in targets)
        if _norm(rule["targets"]) == _norm(new_targets) and rule.get("fallbacks", []) == new_fallbacks:
            print(f"  [unchanged] {pool_name}")
            continue
        old_targets = [(t["provider"], t["model"], t["weight"]) for t in rule["targets"]]
        nxt_targets = [(t["provider"], t["model"], t["weight"]) for t in new_targets]
        print(f"\n=== {pool_name} ===")
        print(f"  OLD targets:")
        for p, m, w in old_targets: print(f"    {w:.2f}  {p}/{m}")
        print(f"  NEW targets:")
        for p, m, w in nxt_targets: print(f"    {w:.2f}  {p}/{m}")
        if pool_plan.get("fallbacks"):
            print(f"  fallbacks: {len(new_fallbacks)} entries")
        if dry_run:
            continue
        body = {
            "name": rule["name"], "description": rule.get("description", ""),
            "enabled": rule.get("enabled", True), "cel_expression": rule["cel_expression"],
            "targets": new_targets, "fallbacks": new_fallbacks,
            "scope": rule.get("scope", "global"), "scope_id": rule.get("scope_id"),
            "priority": rule.get("priority", 0),
        }
        try:
            req("PUT", f"/governance/routing-rules/{rule['id']}", body)
            print(f"  ✓ applied")
            changed += 1
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")[:400]
            print(f"  ✗ FAIL: HTTP {e.code} {err_body}", file=sys.stderr)
    suffix = f" / {created} created" if created else ""
    print(f"\n{'DRY-RUN' if dry_run else 'APPLIED'}: {changed} pool(s) updated{suffix}")
    return changed

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", default=str(DEFAULT_PLAN), help="path to YAML plan")
    ap.add_argument("--dry-run", action="store_true", help="show diff but do not PUT")
    args = ap.parse_args()
    plan_path = Path(args.plan)
    if not plan_path.exists():
        sys.exit(f"plan not found: {plan_path}")
    plan = load_plan(plan_path)
    apply(plan, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
