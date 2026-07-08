#!/usr/bin/env python3
"""Render bifrost/config.json → bifrost/data/config.json, key-aware.

Called by install.sh (after it sources .env). What it does:

- Substitutes shell-style ${VAR} tokens inside provider configs from the
  environment (e.g. ${CLOUDFLARE_ACCOUNT_ID}); bifrost's own `env.KEY`
  secret refs are left intact for bifrost to resolve at runtime.
- Drops providers with no usable key: every key whose `env.NAME` var is
  unset/empty is removed, and a provider with zero keys left is pruned.
  A provider whose config still contains an unresolvable ${VAR} is pruned too.
- Prunes each pool (routing rule) to surviving providers. If a pool loses all
  weighted targets, the first surviving fallback is promoted to primary. A pool
  with no members left is disabled — so an install with a single provider key
  still works: every pool that CAN route does, the rest are switched off.
- Emits bifrost/data/models.json for the strip-shim: pool aliases + every
  direct provider/model target (tagged primary/fallback), so /v1/models
  reflects exactly what this install can serve.

stdout: space-separated names of enabled pools (consumed by install.sh's
smoke test). All human-readable logging goes to stderr.
"""
import copy
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "bifrost", "config.json")
OUT_DIR = os.path.join(ROOT, "bifrost", "data")
OUT_CONFIG = os.path.join(OUT_DIR, "config.json")
OUT_MODELS = os.path.join(OUT_DIR, "models.json")

VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def log(msg):
    print(msg, file=sys.stderr)


def env_ok(name):
    return bool(os.environ.get(name, "").strip())


def render_provider(name, prov):
    """Return rendered provider dict, or None (with a logged reason) to prune."""
    keyless = (prov.get("custom_provider_config") or {}).get("is_key_less") is True
    keys = []
    for key in prov.get("keys", []):
        value = key.get("value", "")
        if value.startswith("env."):
            if env_ok(value[4:]):
                keys.append(key)
        elif value:
            keys.append(key)  # literal key (discouraged, but honor it)
    if not keys and not keyless:
        log(f"  - {name}: no key in .env — pruned")
        return None

    prov = copy.deepcopy(prov)
    if keys:
        prov["keys"] = keys
    else:
        prov.pop("keys", None)  # keyless provider (e.g. kilo) — no keys block

    # Resolve ${VAR} tokens (base_url, request_path_overrides, …).
    blob = json.dumps(prov)
    missing = sorted({v for v in VAR_RE.findall(blob) if not env_ok(v)})
    if missing:
        log(f"  - {name}: key present but {', '.join(missing)} unset — pruned")
        return None
    blob = VAR_RE.sub(lambda m: os.environ[m.group(1)].strip(), blob)
    return json.loads(blob)


def prune_rule(rule, kept_providers):
    """Prune a routing rule in place to surviving providers. Returns True if
    the rule still has at least one member (and stays enabled)."""
    targets = [t for t in rule.get("targets", []) if t.get("provider") in kept_providers]
    fallbacks = [f for f in rule.get("fallbacks", [])
                 if f.split("/", 1)[0] in kept_providers]

    if not targets and fallbacks:
        promoted = fallbacks.pop(0)
        prov, model = promoted.split("/", 1)
        targets = [{"provider": prov, "model": model, "weight": 1}]
        log(f"  - {rule['name']}: primary provider missing — promoted {promoted}")

    rule["targets"] = targets
    rule["fallbacks"] = fallbacks
    if not targets:
        rule["enabled"] = False
        log(f"  - {rule['name']}: no members with available keys — DISABLED")
        return False
    return True


def build_models_doc(rules):
    """models.json for the shim: pool aliases first, then direct targets."""
    now = int(time.time())
    entries, by_id = [], {}

    def add(mid, role, pool=None):
        entry = by_id.get(mid)
        if entry is None:
            entry = {"id": mid, "object": "model", "created": now,
                     "owned_by": "coire-ansic", "coire_role": role}
            by_id[mid] = entry
            entries.append(entry)
        if pool:
            pools = entry.setdefault("coire_pools", [])
            if pool not in pools:
                pools.append(pool)

    for rule in rules:
        if rule.get("enabled"):
            add(rule["name"], "pool")
    for rule in rules:
        if not rule.get("enabled"):
            continue
        for t in rule.get("targets", []):
            add(f"{t['provider']}/{t['model']}", "primary", rule["name"])
        for f in rule.get("fallbacks", []):
            add(f, "fallback", rule["name"])
    return {"object": "list", "data": entries}


def main():
    with open(SRC) as f:
        config = json.load(f)

    log("render: providers")
    providers = {}
    for name, prov in config.get("providers", {}).items():
        rendered = render_provider(name, prov)
        if rendered is not None:
            providers[name] = rendered
    if not providers:
        log("  ✗ no provider has a usable key — check .env")
        sys.exit(1)
    config["providers"] = providers
    log(f"  = {len(providers)} provider(s) kept: {', '.join(sorted(providers))}")

    log("render: pools")
    rules = config.get("governance", {}).get("routing_rules", [])
    enabled = [r["name"] for r in rules if prune_rule(r, set(providers))]
    if not enabled:
        log("  ✗ every pool ended up empty — check .env")
        sys.exit(1)
    log(f"  = enabled pools: {', '.join(enabled)}")

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_CONFIG, "w") as f:
        json.dump(config, f, indent=2)
    with open(OUT_MODELS, "w") as f:
        json.dump(build_models_doc(rules), f, indent=2)
    log(f"  = wrote {os.path.relpath(OUT_CONFIG, ROOT)} + {os.path.relpath(OUT_MODELS, ROOT)}")

    print(" ".join(enabled))


if __name__ == "__main__":
    main()
