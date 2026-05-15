#!/usr/bin/env bash
# Patch local hermes-agent so the TUI status bar shows the resolved
# provider/model (sourced from chunk.model) instead of the configured pool
# name like "best".
#
# Two changes:
#   1. run_agent.py — set self.last_response_model whenever a stream chunk
#      reports its model.
#   2. cli.py — _get_status_bar_snapshot prefers agent.last_response_model
#      over agent.model.
#
# Idempotent: silently skips if anchors already patched.
# Re-run after `hermes update` if the TUI status bar reverts to "best".

set -euo pipefail

HERMES_DIR="${HERMES_DIR:-$HOME/hermes-agent}"
[ -f "$HERMES_DIR/run_agent.py" ] || { echo "hermes-agent not at $HERMES_DIR — set HERMES_DIR"; exit 1; }

patch_run_agent() {
  python3 - "$HERMES_DIR/run_agent.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
if "self.last_response_model = chunk.model" in s:
    print("  run_agent.py: already patched"); sys.exit(0)
a1 = ("                    if hasattr(chunk, \"model\") and chunk.model:\n"
      "                        model_name = chunk.model\n"
      "                    # Usage comes in the final chunk with empty choices")
b1 = ("                    if hasattr(chunk, \"model\") and chunk.model:\n"
      "                        model_name = chunk.model\n"
      "                        self.last_response_model = chunk.model\n"
      "                    # Usage comes in the final chunk with empty choices")
a2 = ("                delta = chunk.choices[0].delta\n"
      "                if hasattr(chunk, \"model\") and chunk.model:\n"
      "                    model_name = chunk.model")
b2 = ("                delta = chunk.choices[0].delta\n"
      "                if hasattr(chunk, \"model\") and chunk.model:\n"
      "                    model_name = chunk.model\n"
      "                    self.last_response_model = chunk.model")
assert a1 in s, "anchor 1 missing"
assert a2 in s, "anchor 2 missing"
open(p, "w").write(s.replace(a1, b1, 1).replace(a2, b2, 1))
print("  run_agent.py: patched")
PY
}

patch_cli() {
  python3 - "$HERMES_DIR/cli.py" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
if "last_response_model" in s:
    print("  cli.py: already patched"); sys.exit(0)
old = ('        agent = getattr(self, "agent", None)\n'
       '        model_name = (getattr(agent, "model", None) or self.model or "unknown")')
new = ('        agent = getattr(self, "agent", None)\n'
       '        model_name = (getattr(agent, "last_response_model", None) '
       'or getattr(agent, "model", None) or self.model or "unknown")')
assert old in s, "cli.py anchor missing"
open(p, "w").write(s.replace(old, new, 1))
print("  cli.py: patched")
PY
}

echo "→ patching $HERMES_DIR for TUI provider/model display"
patch_run_agent
patch_cli
rm -rf "$HERMES_DIR/__pycache__" 2>/dev/null || true
echo "  done — restart hermes processes (gateway, TUI) for changes to load"
