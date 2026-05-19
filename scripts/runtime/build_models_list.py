#!/usr/bin/env python3
"""Generate ~/.coire/models.json from live bifrost + CB + probe state.

Output is consumed by strip-shim's /v1/models endpoint so OpenAI-compatible
clients (Open WebUI etc.) see both pool aliases (best/code/mid/fast/compress/
vision/ops) AND every direct provider/model target that bifrost is configured
to route to. Status tags in `description` let power users avoid known-bad
targets while still being able to pin them when needed.

Sources (read-only):
  GET  /api/governance/routing-rules  -> pool aliases + which targets belong
  GET  /api/providers                 -> registered keys + .models[]
  ~/.coire/curator-pool/circuit_state.json  -> currently CB-demoted targets
  ~/.coire/curator-pool/free_tier_probe.json -> probe classifications

Output: ~/.coire/models.json — OpenAI list format with description-tags.

Run hooks (caller responsibility):
  * install.sh first-run     — after bifrost seed completes
  * apply_pool_weights.py    — after sync_key_models runs
  * probe_free_tier.py       — after probe digest written
"""
from __future__ import annotations
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = Path.home() / ".coire" / "models.json"
CB_STATE = Path.home() / ".coire" / "curator-pool" / "circuit_state.json"
PROBE_PATH = Path.home() / ".coire" / "curator-pool" / "free_tier_probe.json"
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


def jget(path: str) -> dict:
    r = urllib.request.Request(f"{BIFROST_URL}{path}", headers={"Authorization": AUTH})
    return json.loads(urllib.request.urlopen(r, timeout=15).read())


def safe_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def main() -> int:
    try:
        rules = jget("/api/governance/routing-rules").get("rules", [])
    except urllib.error.URLError as e:
        sys.exit(f"FAIL: bifrost unreachable at {BIFROST_URL}: {e}")
    providers = jget("/api/providers").get("providers", [])
    cb = safe_json(CB_STATE).get("demoted", {})
    probe_results = safe_json(PROBE_PATH).get("results", {})

    # Map target_key -> set of pools where this target is a PRIMARY
    primary_of: dict[str, list[str]] = {}
    fallback_of: dict[str, list[str]] = {}
    pool_aliases: list[dict] = []
    now = int(time.time())

    for rule in rules:
        pname = rule.get("name", "")
        if not pname or not rule.get("enabled", True):
            continue
        target_names: list[str] = []
        for t in rule.get("targets") or []:
            key = f"{t['provider']}/{t['model']}"
            primary_of.setdefault(key, []).append(pname)
            target_names.append(key)
        for fb in rule.get("fallbacks") or []:
            if isinstance(fb, str) and "/" in fb:
                fallback_of.setdefault(fb, []).append(pname)
        n_primaries = len(rule.get("targets") or [])
        desc = f"pool · {n_primaries} primary target(s)"
        if rule.get("description"):
            desc = rule["description"][:120]
        pool_aliases.append({
            "id": pname,
            "object": "model",
            "created": now,
            "owned_by": "coire-ansic",
            "description": desc,
        })

    # Collect every provider/model pair from registered keys
    seen: set[str] = set()
    direct_entries: list[dict] = []
    for prov in providers:
        pname = prov.get("name")
        if not pname:
            continue
        # Union models across all keys for this provider
        models: set[str] = set()
        for k in prov.get("keys") or []:
            for m in k.get("models") or []:
                models.add(m)
        for m in sorted(models):
            key = f"{pname}/{m}"
            if key in seen:
                continue
            seen.add(key)
            tags = []
            if key in primary_of:
                tags.append("primary · " + ",".join(sorted(set(primary_of[key]))))
            elif key in fallback_of:
                tags.append("fallback · " + ",".join(sorted(set(fallback_of[key]))))
            else:
                tags.append("unrouted")
            if key in cb:
                cb_info = cb[key]
                if cb_info.get("daily_quota"):
                    tags.append("cb:daily-quota")
                else:
                    tags.append("cb:demoted")
            probe = probe_results.get(key) or {}
            psum = probe.get("summary")
            if psum and psum not in ("ok",):
                tags.append(f"probe:{psum}")
            direct_entries.append({
                "id": key,
                "object": "model",
                "created": now,
                "owned_by": pname,
                "description": " · ".join(tags),
            })

    # Pool aliases first (clients usually pick top of list)
    out = {
        "object": "list",
        "data": sorted(pool_aliases, key=lambda d: d["id"]) + direct_entries,
        "generated_at": now,
        "generated_by": "build_models_list.py",
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2))
    n_pools = len(pool_aliases)
    n_direct = len(direct_entries)
    print(f"wrote {OUT_PATH} — {n_pools} pool alias(es) + {n_direct} direct target(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
