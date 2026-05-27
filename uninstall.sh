#!/usr/bin/env bash
# Tear down everything install.sh set up. Keeps your .env + bifrost data
# unless --purge passed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

echo "▶ docker compose down"
docker compose down -v 2>/dev/null || true

echo "▶ removing local ops tools (~/coire-tools/)"
rm -rf "$HOME/coire-tools" 2>/dev/null || true

echo "▶ removing opencode skill/command links"
for f in coire-monitor coire-probe coire-health coire-diagnose coire-cascade-show coire-check-quotas coire-snapshot-sync; do
  rm -f "$HOME/.config/opencode/command/$f.md" 2>/dev/null || true
  rm -rf "$HOME/.config/opencode/skills/$f" 2>/dev/null || true
done

if [ "$PURGE" = "1" ]; then
  echo "▶ purging bifrost data + camofox src"
  rm -rf bifrost/data/* camofox/src
  echo "▶ NOT removing .env. Run: rm .env if you want clean slate."
fi

echo "✓ done"
