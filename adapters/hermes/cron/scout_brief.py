#!/usr/bin/env python3
"""Brief scout context for Hermes cron.

Stdout = compact text snapshot piped into Hermes' agent prompt:
  - configured providers (skip these)
  - already-discovered candidates (skip these too)
  - last-N excluded models (don't re-suggest)

Hermes' job is to find 1-3 NEW free-inference providers not in either
list, verify the free tier, and append to candidate_providers.json.
"""
from __future__ import annotations
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / ".env"
CANDIDATES_FILE = Path.home() / ".hermes" / "curator-pool" / "candidate_providers.json"
EXCLUDES_FILE = ROOT / "bifrost" / "excluded_models.json"
BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")


def env(name: str) -> str:
    v = os.environ.get(name, "")
    if v:
        return v
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def configured_providers() -> list[str]:
    try:
        auth = "Basic " + base64.b64encode(f"admin:{env('BIFROST_PASS')}".encode()).decode()
        req = urllib.request.Request(f"{BIFROST_URL}/api/providers",
                                     headers={"Authorization": auth})
        d = json.loads(urllib.request.urlopen(req, timeout=10).read())
        return sorted(p["name"] for p in d.get("providers", []))
    except Exception as e:
        return [f"(fetch failed: {e})"]


def existing_candidates() -> list[str]:
    if not CANDIDATES_FILE.exists():
        return []
    try:
        d = json.loads(CANDIDATES_FILE.read_text())
        return sorted({c["provider"] for c in d.get("candidates", [])
                       if not str(c.get("provider", "")).startswith("search-hit:")})
    except Exception:
        return []


def excluded() -> list[str]:
    if not EXCLUDES_FILE.exists():
        return []
    try:
        d = json.loads(EXCLUDES_FILE.read_text())
        return [e["id"] for e in d.get("excluded", [])]
    except Exception:
        return []


def main():
    print("=== SCOUT CONTEXT — your job: find 1–3 NEW free-inference providers ===\n")
    print(f"CANDIDATES file (write here): {CANDIDATES_FILE}\n")
    print("Already CONFIGURED in Bifrost (skip these):")
    for p in configured_providers():
        print(f"  - {p}")
    print()
    cands = existing_candidates()
    print(f"Already DISCOVERED candidates (skip — {len(cands)} entries):")
    for p in cands[:30]:
        print(f"  - {p}")
    if len(cands) > 30:
        print(f"  … +{len(cands)-30} more")
    print()
    print("EXCLUDED models (avoid suggesting providers whose top model is one of these):")
    for e in excluded()[:10]:
        print(f"  - {e}")


if __name__ == "__main__":
    main()
