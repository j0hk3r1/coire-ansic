#!/usr/bin/env python3
"""Populate each provider key's `models` list from snapshot routing-rules.

Bifrost rejects requests with "no keys found that support model: X" if the
provider's key has an empty `models` list. seed.sh adds keys without a models
list, so on fresh install no rules work. Run this after seed + apply_snapshot
to allow each key the union of models referenced for that provider.

Idempotent: only PUTs when models set differs.
"""
from __future__ import annotations
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "snapshot" / "routing-rules.json"

BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
USER = os.environ.get("BIFROST_USER", "admin")
PASS = os.environ.get("BIFROST_PASS", "")
if not PASS:
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
    r = urllib.request.Request(
        f"{BIFROST_URL}/api{path}",
        data=data,
        method=method,
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
    )
    raw = urllib.request.urlopen(r, timeout=15).read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Older bifrost versions return HTML 404 for keys endpoint; signal skip.
        raise RuntimeError(f"non-JSON response from {path} (older bifrost?)")


def collect_models_per_provider(snap):
    by_provider: dict[str, set[str]] = {}
    for rule in snap.get("rules", []):
        for tgt in rule.get("targets", []) or []:
            p, m = tgt.get("provider"), tgt.get("model")
            if p and m:
                by_provider.setdefault(p, set()).add(m)
        for fb in rule.get("fallbacks", []) or []:
            if isinstance(fb, str) and "/" in fb:
                p, _, m = fb.partition("/")
                by_provider.setdefault(p, set()).add(m)
    return {k: sorted(v) for k, v in by_provider.items()}


def main():
    # Pull current routing rules from LIVE bifrost — the snapshot file is a
    # frozen seed-time view and drifts the moment apply_pool_weights.py
    # mutates a pool. Using live data means: any pool/target change picked
    # up next sync run without manual snapshot regeneration.
    try:
        live = req("GET", "/governance/routing-rules")
    except Exception as e:
        print(f"FAIL: cannot fetch live routing-rules: {e}", file=sys.stderr)
        sys.exit(1)
    wanted = collect_models_per_provider(live)
    if not wanted:
        # Fall back to snapshot file (fresh-install path: rules not applied yet)
        if SNAPSHOT.exists():
            print(f"  live had 0 rules — falling back to snapshot file")
            snap = json.loads(SNAPSHOT.read_text())
            wanted = collect_models_per_provider(snap)
        else:
            print(f"FAIL: no live rules and {SNAPSHOT} missing", file=sys.stderr)
            sys.exit(1)
    print(f"wanted: {sum(len(v) for v in wanted.values())} model slots across {len(wanted)} providers")

    providers_resp = req("GET", "/providers")
    live_providers = {p["name"] for p in providers_resp.get("providers", [])}
    updated = 0
    skipped = 0
    # Bifrost upstream removed the /providers/<name>/keys subresource (405
    # on POST, non-JSON on GET against newer versions). The supported way
    # to update key.models is now: GET /providers/<name> (full config),
    # mutate the keys array, PUT /providers/<name> back.
    for provider, models in wanted.items():
        if provider not in live_providers:
            print(f"  - {provider}: provider missing — skip")
            continue
        try:
            cur = req("GET", f"/providers/{provider}")
        except RuntimeError as e:
            print(f"  - {provider}: {e} — skip")
            continue
        existing_keys = cur.get("keys", [])
        if not existing_keys:
            print(f"  - {provider}: no keys configured — skip")
            continue
        # Build updated keys list — set `models` on every key, preserve
        # everything else. Re-send the full provider config on PUT (bifrost
        # treats PUT as replace; omitting fields nulls them).
        new_keys = []
        any_change = False
        for k in existing_keys:
            current = sorted(k.get("models") or [])
            if current == models:
                skipped += 1
                new_keys.append(k)
                continue
            any_change = True
            kc = dict(k)
            kc["models"] = models
            new_keys.append(kc)
        if not any_change:
            continue
        body = dict(cur)
        body["keys"] = new_keys
        # On older bifrost responses, providers GET returns 'name' but PUT
        # body wants 'provider'. Set both to be safe.
        body["provider"] = cur.get("name", provider)
        try:
            req("PUT", f"/providers/{provider}", body)
            print(f"  ~ {provider}: {len(new_keys)} key(s) updated with {len(models)} models")
            updated += 1
        except urllib.error.HTTPError as e:
            print(f"  X {provider}: {e.code} {e.read().decode()[:200]}")

    print(f"\n{updated} provider(s) updated, {skipped} keys unchanged")


if __name__ == "__main__":
    main()
