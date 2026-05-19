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

        # Build filtered target list FIRST so we can both
        #   (a) skip pools with zero valid targets, and
        #   (b) include targets in the initial CREATE body (bifrost rejects
        #       routing-rule POST with empty targets — 400 "at least one
        #       target is required").
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
            if rule and rule.get("id") and not dry_run:
                try:
                    req("DELETE", f"/governance/routing-rules/{rule['id']}")
                    print(f"  [DELETED] pool '{pool_name}' — {reason}", file=sys.stderr)
                except urllib.error.HTTPError as e:
                    print(f"  [SKIP] pool '{pool_name}' — {reason} (delete failed: HTTP {e.code})", file=sys.stderr)
            else:
                print(f"  [SKIP] pool '{pool_name}' — {reason}", file=sys.stderr)
            continue
        # Safety: warn (don't fail) if a pool has fewer than 3 primaries
        # after CB / provider-not-configured filtering. Some pools may
        # legitimately have 3 (e.g. ops before 5-primary expansion) but
        # going below 3 means the pool has no cascade depth — single bad
        # day can empty it entirely. Surface for human attention.
        if len(new_targets) < 3:
            print(f"  [WARN] pool '{pool_name}' has only {len(new_targets)} "
                  f"primary target(s) after filtering — pool intent (depth=3+) "
                  f"degraded", file=sys.stderr)
        # Cross-check against the freeness-probe digest if present
        # (~/.coire/curator-pool/free_tier_probe.json, written by
        # scripts/runtime/probe_free_tier.py). Surface a WARN when a
        # primary target is known-dead on the user's free tier — e.g.
        # gemini-3-pro returns 'limit: 0', github-models gpt-4o has 8k
        # context cap, deepseek/* has insufficient balance. Doesn't
        # auto-mutate (config is a human decision) but makes drift visible.
        try:
            probe_path = Path.home() / ".coire" / "curator-pool" / "free_tier_probe.json"
            if probe_path.exists():
                probe = json.loads(probe_path.read_text()).get("results", {})
                bad = ("free_zero", "needs_balance", "not_available")
                for t in new_targets:
                    k = f"{t['provider']}/{t['model']}"
                    summary = (probe.get(k) or {}).get("summary")
                    if summary in bad:
                        print(f"  [WARN] pool '{pool_name}' primary {k} "
                              f"classified '{summary}' by last probe — consider "
                              f"moving to fallback or removing", file=sys.stderr)
        except Exception as e:
            print(f"  [info] probe-json check failed: {e}", file=sys.stderr)
        new_targets = normalize(new_targets)
        new_fallbacks = [
            fb for fb in (pool_plan.get("fallbacks") or (rule.get("fallbacks", []) if rule else []))
            if fb not in demoted
            and (not configured or ("/" in fb and fb.split("/", 1)[0] in configured))
        ]

        if is_new:
            print(f"\n=== {pool_name} (CREATING NEW) ===")
            for t in new_targets: print(f"    {t['weight']:.2f}  {t['provider']}/{t['model']}")
            if dry_run:
                continue
            create_body = {
                "name": pool_name,
                "description": pool_plan.get("description", f"Pool '{pool_name}' from pool_weights.yaml"),
                "enabled": True,
                "cel_expression": f'model == "{pool_name}"',
                "targets": new_targets,
                "fallbacks": new_fallbacks,
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
            continue  # CREATE delivered targets+fallbacks; no PUT needed

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
    ap.add_argument("--skip-sync", action="store_true",
                    help="don't auto-run sync_key_models after applying")
    args = ap.parse_args()
    plan_path = Path(args.plan)
    if not plan_path.exists():
        sys.exit(f"plan not found: {plan_path}")
    plan = load_plan(plan_path)
    apply(plan, dry_run=args.dry_run)
    # After any pool change (CREATE or PUT), key.models[] may have models
    # that no key supports yet. sync_key_models.py reads the LIVE routing
    # rules and PUTs each provider key with the model set it should serve.
    # Skip on dry-run (no actual mutations to sync).
    if not args.dry_run and not args.skip_sync:
        import subprocess
        sync = Path(__file__).resolve().parent.parent.parent / "bifrost" / "sync_key_models.py"
        if sync.exists():
            print()
            print("→ auto-running sync_key_models (use --skip-sync to suppress)")
            try:
                subprocess.run([sys.executable, str(sync)], check=False, timeout=120)
            except Exception as e:
                print(f"  sync_key_models failed: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
