#!/usr/bin/env python3
"""Capture current Bifrost config state to snapshot/ for version control.

Run on the host where Bifrost runs (typically .93). Resulting JSON files
should be committed to the repo so .68 stays in sync with deployed state.
"""
from __future__ import annotations
import base64
import json
import os
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT_DIR = HERE / "snapshot"
SNAPSHOT_DIR.mkdir(exist_ok=True)

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
        PASS = ""

AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

def fetch(path):
    r = urllib.request.Request(f"{BIFROST_URL}/api{path}", headers={"Authorization": AUTH})
    return json.loads(urllib.request.urlopen(r, timeout=15).read())

# Routing rules — full
rules = fetch("/governance/routing-rules")
# Strip volatile fields per rule
DROP = {"id", "config_hash", "created_at", "updated_at"}
clean_rules = []
for r in rules.get("rules") or rules.get("routing_rules") or []:
    clean_rules.append({k: v for k, v in r.items() if k not in DROP})
(SNAPSHOT_DIR / "routing-rules.json").write_text(
    json.dumps({"rules": clean_rules}, indent=2)
)
print(f"  routing-rules.json: {len(clean_rules)} rules")

# Providers — strip raw key values + embedded secrets in network_config.
# Bearer tokens often live in `network_config.extra_headers.Authorization`
# AND account IDs leak in `network_config.base_url` /
# `custom_provider_config.request_path_overrides.*`. Substitute placeholders
# so the committed snapshot is safe.
import re
from typing import Any

# env-var name per provider (matches .env.example)
PROVIDER_TO_ENV = {
    "groq":          "GROQ_API_KEY",
    "gemini":        "GEMINI_API_KEY",
    "mistral":       "MISTRAL_API_KEY",
    "cerebras":      "CEREBRAS_API_KEY",
    "nvidia-nim":    "NVIDIA_API_KEY",
    "cloudflare":     "CLOUDFLARE_API_KEY",
    "openrouter":    "OPENROUTER_API_KEY",
    "deepseek":      "DEEPSEEK_API_KEY",
    "sambanova":     "SAMBANOVA_API_KEY",
    "github-models": "GITHUB_MODELS_TOKEN",
    "cohere":        "COHERE_API_KEY",
}

def _sanitize_url(s: str) -> str:
    if not isinstance(s, str): return s
    # Cloudflare account-IDs (32-hex) embedded in URL path
    return re.sub(r"(accounts/)[a-f0-9]{32}", r"\1${CLOUDFLARE_ACCOUNT_ID}", s)

def _sanitize_provider(p: dict) -> dict:
    pp: dict[str, Any] = {}
    env_name = PROVIDER_TO_ENV.get(p.get("name", ""), "PROVIDER_API_KEY")
    for k, v in p.items():
        if k in ("keys", "config_hash", "status"):
            continue
        if k == "network_config" and isinstance(v, dict):
            nc = dict(v)
            if isinstance(nc.get("extra_headers"), dict):
                eh = dict(nc["extra_headers"])
                if "Authorization" in eh:
                    eh["Authorization"] = f"Bearer ${{{env_name}}}"
                nc["extra_headers"] = eh
            if "base_url" in nc:
                nc["base_url"] = _sanitize_url(nc["base_url"])
            pp[k] = nc
        elif k == "custom_provider_config" and isinstance(v, dict):
            cpc = dict(v)
            if isinstance(cpc.get("request_path_overrides"), dict):
                cpc["request_path_overrides"] = {
                    rk: _sanitize_url(rv) for rk, rv in cpc["request_path_overrides"].items()
                }
            pp[k] = cpc
        else:
            pp[k] = v
    pp["_keys_count"] = len(p.get("keys", []))
    return pp

provs = fetch("/providers")
clean_provs = [_sanitize_provider(p) for p in provs.get("providers", [])]
(SNAPSHOT_DIR / "providers.json").write_text(
    json.dumps({"providers": clean_provs}, indent=2)
)
print(f"  providers.json: {len(clean_provs)} providers (keys + bearer tokens + account-ids redacted)")

print("\nSnapshot saved. Commit to repo to version-control deployed state.")
