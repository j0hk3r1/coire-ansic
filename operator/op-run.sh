#!/usr/bin/env bash
# Generic operator runner. Wraps `pi -p` with a prompt template + bifrost-ops skill.
#
# Usage:
#   op-run.sh <template-name> [extra args...]
#
# Examples:
#   op-run.sh op-health
#   op-run.sh op-react
#   op-run.sh op-integrate ~/.hermes/operator/incoming_keys/cohere.txt
#   op-run.sh op-patch-hermes
#
# Sources .env so pi (and child curl probes) see provider keys + BIFROST_PASS.
# Pipes through `pi -p` (non-interactive). Pool defaults to "code" (set in
# ~/.pi/agent/settings.json). Logs to ~/.hermes/operator/logs/<date>.log.

set -euo pipefail

TEMPLATE="${1:-}"
shift || true
EXTRA_ARGS=("$@")

if [ -z "$TEMPLATE" ]; then
  echo "usage: $0 <template-name> [extra args]" >&2
  exit 64
fi

HFC_DIR="${HFC_DIR:-$HOME/hermes-free-cloud}"
OP_DIR="${OP_DIR:-$HFC_DIR/operator}"
PI_AGENT_DIR="${PI_AGENT_DIR:-$HOME/.pi/agent}"
LOG_DIR="${LOG_DIR:-$HOME/.hermes/operator/logs}"

TEMPLATE_PATH="$OP_DIR/prompt-templates/$TEMPLATE.md"
SKILL_PATH="$OP_DIR/skills/bifrost-ops"

[ -f "$TEMPLATE_PATH" ] || { echo "missing template: $TEMPLATE_PATH" >&2; exit 65; }
[ -d "$SKILL_PATH" ]   || { echo "missing skill dir: $SKILL_PATH" >&2; exit 65; }
[ -f "$HFC_DIR/.env" ] || { echo "missing .env at $HFC_DIR" >&2; exit 65; }

mkdir -p "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
TS=$(date +%H:%M:%S)
LOG_FILE="$LOG_DIR/${DATE}-${TEMPLATE}.log"

# Source .env so pi children inherit BIFROST_PASS + provider keys
set -a; source "$HFC_DIR/.env"; set +a

# Ensure pi binary is in PATH
export PATH="$HOME/.npm-global/bin:$PATH"

# User message: pass extra args as additional context, or empty
USER_MSG="${EXTRA_ARGS[*]:-Run the task described in the system prompt.}"

echo "=== operator run @ $DATE $TS — template=$TEMPLATE ===" | tee -a "$LOG_FILE"

# pi -p   : non-interactive print mode
# --skill : load bifrost-ops skill
# --append-system-prompt : load the per-job template as system instructions
# --no-session : ephemeral (we have our own log file)
# --thinking : medium for code-pool, low for ops-pool (smaller model)
RUN_OUT=$(mktemp)
START_EPOCH=$(date +%s)
set +e
pi -p \
  --provider hermes-bifrost \
  --append-system-prompt "$TEMPLATE_PATH" \
  --skill "$SKILL_PATH" \
  --no-session \
  --thinking medium \
  "$USER_MSG" 2>&1 | tee -a "$LOG_FILE" | tee "$RUN_OUT" > /dev/null
RC=${PIPESTATUS[0]}
set -e
END_EPOCH=$(date +%s)
DUR=$((END_EPOCH - START_EPOCH))

echo "=== operator done @ $(date +%H:%M:%S) ===" | tee -a "$LOG_FILE"

# Always write deterministic JSONL audit line (pi's templates also call op-log
# but pi is inconsistent — this guarantees every run leaves an audit trail).
# Captured fields: rc, duration_sec, output bytes, first line of pi output (for
# at-a-glance flavor in jq filtering).
OUT_BYTES=$(wc -c < "$RUN_OUT" 2>/dev/null || echo 0)
FIRST_LINE=$(head -c 200 "$RUN_OUT" 2>/dev/null | tr '\n' ' ' | sed 's/[[:cntrl:]]//g')
if [ -x "$OP_DIR/bin/op-log" ]; then
  python3 -c "
import json, sys
sys.stdout.write(json.dumps({
    'job': 'op-run',
    'template': '$TEMPLATE',
    'rc': $RC,
    'duration_sec': $DUR,
    'out_bytes': $OUT_BYTES,
    'first_line': '''$(printf '%s' "$FIRST_LINE" | head -c 180)''',
}))" | "$OP_DIR/bin/op-log" - > /dev/null || echo "  (op-log JSONL write failed)" >&2
fi
rm -f "$RUN_OUT"
