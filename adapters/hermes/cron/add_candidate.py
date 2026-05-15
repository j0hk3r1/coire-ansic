#!/usr/bin/env python3
"""Append a single candidate to candidate_providers.json.

Usage (typically called via Hermes' terminal tool):
  python3 ~/.hermes/scripts/add_candidate.py '<json>'

Where <json> is a single candidate object. The script:
  - Reads ~/.hermes/curator-pool/candidate_providers.json
  - Adds defaults (discovered_via, discovered_at) if missing
  - Skips if a candidate with same provider id is already present (idempotent)
  - Writes back atomically (tempfile + rename)
  - Prints "added: <provider>" or "skipped: already present"

Why: the Hermes agent kept hitting output-length truncation when asked to
re-emit the entire JSON file via file_write. Per-candidate appends keep
output budget tiny (one tool call per addition).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

CANDIDATES_FILE = Path.home() / ".hermes" / "curator-pool" / "candidate_providers.json"


def main():
    if len(sys.argv) < 2:
        print("usage: add_candidate.py '<json-object>'", file=sys.stderr)
        sys.exit(2)

    try:
        new = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(f"FAIL: invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(new, dict) or not new.get("provider"):
        print("FAIL: candidate must be an object with 'provider' key", file=sys.stderr)
        sys.exit(1)

    new.setdefault("discovered_via", "hermes-scout")
    new.setdefault("discovered_at", dt.datetime.now(dt.timezone.utc).isoformat())
    new.setdefault("models", [])
    new.setdefault("notes", "")

    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CANDIDATES_FILE.exists():
        d = json.loads(CANDIDATES_FILE.read_text())
    else:
        d = {"candidates": [], "updated_at": None}

    cands = d.get("candidates", [])
    if any(c.get("provider") == new["provider"] for c in cands):
        print(f"skipped: '{new['provider']}' already present")
        return

    cands.append(new)
    d["candidates"] = cands
    d["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    tmp = CANDIDATES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2))
    tmp.replace(CANDIDATES_FILE)
    print(f"added: {new['provider']}")


if __name__ == "__main__":
    main()
