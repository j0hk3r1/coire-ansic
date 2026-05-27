#!/usr/bin/env bash
# Deploy coire-tools to .93. Run from .68:
#   ./scripts/ops/deploy.sh
# Or with target override:
#   TARGET=jkr@host ./scripts/ops/deploy.sh

set -euo pipefail
TARGET="${TARGET:-jkr@192.168.1.93}"
SRC="$(cd "$(dirname "$0")" && pwd)"

echo "→ deploying ops scripts from $SRC to $TARGET:~/coire-tools/"
ssh "$TARGET" 'mkdir -p ~/coire-tools'

# scp + chmod
for f in coire-health coire-kill-opencode coire-restart coire-cascade-show coire-check-quotas coire-diagnose coire-snapshot-sync; do
  echo "  - $f"
  scp -q "$SRC/$f" "$TARGET:~/coire-tools/$f"
  ssh "$TARGET" "chmod +x ~/coire-tools/$f"
done

# Also symlink monitor + probe skill scripts as CLI tools
echo "  - monitor (symlink to skill)"
ssh "$TARGET" 'ln -sf ~/.config/opencode/skills/coire-monitor/scripts/monitor.py ~/coire-tools/coire-monitor && chmod +x ~/.config/opencode/skills/coire-monitor/scripts/monitor.py'

echo "  - probe (symlink to skill)"
ssh "$TARGET" 'ln -sf ~/.config/opencode/skills/coire-probe/scripts/probe.py ~/coire-tools/coire-probe && chmod +x ~/.config/opencode/skills/coire-probe/scripts/probe.py'

echo
echo "✓ deployed. Verify:"
ssh "$TARGET" 'ls -la ~/coire-tools/'
