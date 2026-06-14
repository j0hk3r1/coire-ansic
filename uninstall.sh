#!/usr/bin/env bash
# Tear down the coire-ansic stack. Keeps .env + bifrost/data unless --purge.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"

PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

echo "▶ docker compose down (core + all profiles)"
docker compose --profile searxng --profile camofox \
  down --remove-orphans -v 2>/dev/null || true

if [ "$PURGE" = "1" ]; then
  echo "▶ purging bifrost/data (rendered config.json + DB) + camofox src/data"
  rm -rf bifrost/data/* camofox/src camofox/data 2>/dev/null || true
  echo "▶ keeping .env — run 'rm .env' yourself for a clean slate"
fi

echo "✓ done"
