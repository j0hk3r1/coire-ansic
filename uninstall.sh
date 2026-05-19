#!/usr/bin/env bash
# Tear down everything install.sh set up. Keeps your .env + bifrost data
# unless --purge passed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

echo "▶ stopping operator timers (pi-op + cb-deadman + op-rebalance/discover)"
for unit in cb-deadman.timer pi-op-react.timer pi-op-health.timer pi-op-queue.timer pi-op-patch.timer op-rebalance.timer op-discover.timer; do
  systemctl --user disable --now "$unit" 2>/dev/null || true
done
for unit in cb-deadman pi-op-react pi-op-health pi-op-queue pi-op-patch op-rebalance op-discover; do
  rm -f "$HOME/.config/systemd/user/${unit}.service" "$HOME/.config/systemd/user/${unit}.timer" 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/timers.target.wants/${unit}.timer" 2>/dev/null || true
done

echo "▶ stopping circuit-breaker"
systemctl --user disable --now circuit-breaker.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/circuit-breaker.service" 2>/dev/null || true
systemctl --user daemon-reload 2>/dev/null || true

echo "▶ stopping gateway"
PATH="$HOME/hermes-agent/venv/bin:$PATH"
hermes gateway stop 2>/dev/null || true
hermes gateway uninstall 2>/dev/null || true
# Remove our drop-in (hermes won't, it owns only the main unit file)
rm -rf "$HOME/.config/systemd/user/hermes-gateway.service.d" 2>/dev/null || true
systemctl --user daemon-reload 2>/dev/null || true

echo "▶ docker compose down"
docker compose down -v 2>/dev/null || true

if [ "$PURGE" = "1" ]; then
  echo "▶ purging bifrost data + camofox src + pi-mono configs"
  rm -rf bifrost/data/* camofox/src
  rm -rf "$HOME/.pi/agent/skills" "$HOME/.pi/agent/prompt-templates" \
         "$HOME/.pi/agent/settings.json" "$HOME/.pi/agent/models.json"
  echo "▶ unlinking ~/.hermes"
  if [ -L "$HOME/.hermes/.env" ]; then
    rm "$HOME/.hermes/.env"
  fi
  echo "▶ NOT removing ~/.hermes (sessions/memory/operator-logs preserved). Run: rm -rf ~/.hermes if you want clean slate."
fi

echo "✓ done"
