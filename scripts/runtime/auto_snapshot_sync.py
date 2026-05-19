#!/usr/bin/env python3
"""Pull fresh bifrost snapshot from .93 and commit locally if changed.

Snapshot drift between .93's live bifrost state and the repo's
bifrost/snapshot/*.json causes regressions on re-install: install.sh
re-applies the stale snapshot, wiping recent CB demotes, weight tweaks,
and pool additions. This script closes that loop:

  1. ssh into BIFROST_HOST (default .93), run bifrost/snapshot.py to
     write fresh JSON.
  2. rsync the snapshot dir back to the local repo.
  3. git add + commit locally if the diff is non-empty. NO push — the
     caller (me) reviews + pushes manually per the goal.

Intended to run from the repo host (.68) via cron, systemd timer, or
on-demand after any operator action that mutated bifrost. Idempotent:
identical snapshot = no commit.
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = ROOT / "bifrost" / "snapshot"
DEFAULT_HOST = os.environ.get("BIFROST_HOST", "jkr@192.168.1.93")
REMOTE_REPO = os.environ.get("BIFROST_REMOTE_REPO", "~/coire-ansic")


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None,
                          text=True)


def regen_remote(host: str) -> None:
    """Trigger snapshot.py on the bifrost host. Reads its own .env for auth."""
    cmd = [
        "ssh", host,
        f"cd {REMOTE_REPO} && "
        "PASS=$(grep ^BIFROST_PASS= .env | cut -d= -f2) "
        f"BIFROST_PASS=$PASS python3 bifrost/snapshot.py",
    ]
    print(f"  → regen on {host}")
    run(cmd)


def rsync_back(host: str) -> None:
    cmd = ["rsync", "-az", "--delete",
           f"{host}:{REMOTE_REPO}/bifrost/snapshot/",
           f"{SNAPSHOT_DIR}/"]
    print(f"  → rsync {host} → {SNAPSHOT_DIR}")
    run(cmd)


def commit_if_diff(message: str | None = None) -> bool:
    """Returns True if a commit was made."""
    # Anything changed?
    diff = run(["git", "diff", "--quiet", "--", str(SNAPSHOT_DIR)], check=False)
    if diff.returncode == 0:
        # Also check for untracked new files
        status = run(["git", "status", "--porcelain", str(SNAPSHOT_DIR)],
                     capture=True)
        if not (status.stdout or "").strip():
            print("  snapshot unchanged — no commit")
            return False
    # Secret guard: refuse to commit if a known key pattern appears in diff
    diff_text = run(["git", "diff", "--", str(SNAPSHOT_DIR)],
                    capture=True).stdout or ""
    suspect = [t for t in ("sk-", "nvapi-", "gsk_", "csk-", "github_pat_",
                            "Bearer ") if t in diff_text]
    if suspect:
        print(f"  ABORT: suspect token pattern in diff ({suspect}) — "
              "snapshot.py should have redacted; refusing to commit",
              file=sys.stderr)
        sys.exit(2)
    run(["git", "add", str(SNAPSHOT_DIR)])
    msg = message or "auto(snapshot): drift sync from live bifrost"
    run(["git", "commit", "-m", msg])
    print(f"  ✓ committed: {msg}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--skip-regen", action="store_true",
                    help="rsync existing snapshot from remote without re-running snapshot.py")
    ap.add_argument("--message", help="override commit message")
    ap.add_argument("--no-commit", action="store_true",
                    help="rsync only, don't commit")
    args = ap.parse_args()
    os.chdir(ROOT)
    if not args.skip_regen:
        regen_remote(args.host)
    rsync_back(args.host)
    if args.no_commit:
        print("  --no-commit: skipping git step")
        return 0
    commit_if_diff(args.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
