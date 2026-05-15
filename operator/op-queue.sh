#!/usr/bin/env bash
# Process incoming_keys/ queue — one file per provider, one shot each.
# Files matching *.txt or *.env get dispatched to op-integrate.
# After processing, file is moved to done/ (with SUCCESS-/FAIL- prefix by the template).
#
# Runs every 5 min via systemd timer. Skips if queue is empty.

set -euo pipefail

OP_DIR="${OP_DIR:-$HOME/coire-ansic/operator}"
QUEUE_DIR="${QUEUE_DIR:-$HOME/.coire/operator/incoming_keys}"

mkdir -p "$QUEUE_DIR"
shopt -s nullglob
FILES=("$QUEUE_DIR"/*.txt "$QUEUE_DIR"/*.env)
shopt -u nullglob

if [ ${#FILES[@]} -eq 0 ]; then
  # Nothing to do — exit silently
  exit 0
fi

echo "[$(date -Iseconds)] queue: ${#FILES[@]} incoming keys"

for f in "${FILES[@]}"; do
  echo "[$(date -Iseconds)] dispatching: $f"
  "$OP_DIR/op-run.sh" op-integrate "$f" || echo "[$(date -Iseconds)] FAILED on $f (rc=$?)"
done
