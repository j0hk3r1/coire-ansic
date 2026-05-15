#!/usr/bin/env bash
# Deploy hermes-free-cloud from a dev workstation to a remote host running
# the docker stack + operator. Most users won't need this — `install.sh`
# runs locally and is sufficient. Use this only if you develop on one
# machine and the bifrost stack runs on another.
#
# Usage:
#   ./deploy.sh                # full deploy: rsync + restart + apply config
#   ./deploy.sh --dry           # show what would change, no writes
#   ./deploy.sh --no-restart    # rsync only, no service restart
#   ./deploy.sh --pools         # apply pools.json only
#
# Env (REQUIRED — no defaults):
#   DEPLOY_HOST=user@host                    SSH target running the stack
#   DEPLOY_PATH=/home/user/hermes-free-cloud   Remote install path
set -euo pipefail

HOST="${DEPLOY_HOST:?set DEPLOY_HOST=user@host (e.g. DEPLOY_HOST=alice@10.0.0.5)}"
RPATH="${DEPLOY_PATH:-/home/$(echo "$HOST" | cut -d@ -f1)/hermes-free-cloud}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DRY=""
NO_RESTART=""
POOLS_ONLY=""

for arg in "$@"; do
  case "$arg" in
    --dry) DRY="--dry-run" ;;
    --no-restart) NO_RESTART=1 ;;
    --pools) POOLS_ONLY=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

if [ -n "$POOLS_ONLY" ]; then
  echo "→ applying pools.json to bifrost on $HOST"
  ssh "$HOST" "BIFROST_PASS='$(grep BIFROST_PASS "$HERE/.env" 2>/dev/null | cut -d= -f2)' python3 -" \
    < "$HERE/bifrost/apply_snapshot.py"
  exit 0
fi

echo "→ rsync .68 → $HOST:$RPATH ${DRY:+(dry-run)}"
rsync -avz --delete $DRY \
  --exclude '.git' \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'bifrost/data' \
  --exclude 'camofox/src' \
  --exclude '.claude' \
  --exclude '*.bak.*' \
  --exclude 'circuit_state.json' \
  --exclude 'circuit_history.jsonl' \
  --exclude 'pool_history.jsonl' \
  "$HERE/" "$HOST:$RPATH/"

if [ -n "$DRY" ]; then
  echo "→ dry run complete, no changes applied"
  exit 0
fi

echo "→ ensure systemd units installed (path-substituted)"
ssh "$HOST" "
  mkdir -p ~/.config/systemd/user
  sed 's|__HFC_ROOT__|$RPATH|g' $RPATH/systemd/circuit-breaker.service > ~/.config/systemd/user/circuit-breaker.service
  # Operator-layer timers (pi-op + cb-deadman + op-rebalance + op-discover)
  if [ -d $RPATH/operator/systemd ]; then
    for u in $RPATH/operator/systemd/*.service $RPATH/operator/systemd/*.timer; do
      [ -e \"\$u\" ] && cp \"\$u\" ~/.config/systemd/user/
    done
  fi
  systemctl --user daemon-reload
"

echo "→ sync pi-mono configs (operator agent)"
ssh "$HOST" "
  if [ -d $RPATH/operator ] && command -v pi >/dev/null; then
    mkdir -p \$HOME/.pi/agent
    cp $RPATH/operator/pi-settings.json \$HOME/.pi/agent/settings.json
    cp $RPATH/operator/pi-models.json   \$HOME/.pi/agent/models.json
    rm -rf \$HOME/.pi/agent/skills \$HOME/.pi/agent/prompt-templates
    cp -r $RPATH/operator/skills          \$HOME/.pi/agent/skills
    cp -r $RPATH/operator/prompt-templates \$HOME/.pi/agent/prompt-templates
    echo '  pi-mono configs synced'
  else
    echo '  pi-mono not installed — skipping operator config sync'
  fi
"

if [ -z "$NO_RESTART" ]; then
  echo "→ restart core services"
  ssh "$HOST" "
    cd $RPATH
    mkdir -p \$HOME/.hermes bifrost/data
    docker compose --profile dashboard up -d --build 2>&1 | tail -5
    systemctl --user enable --now circuit-breaker 2>&1 | tail -2 || true
    systemctl --user restart circuit-breaker
    systemctl --user restart hermes-gateway 2>&1 | tail -2 || true
    # Enable operator timers if present
    for t in cb-deadman pi-op-react pi-op-health pi-op-queue pi-op-patch op-rebalance op-discover; do
      [ -f \$HOME/.config/systemd/user/\${t}.timer ] && systemctl --user enable --now \${t}.timer 2>/dev/null || true
    done
  "
fi

echo "→ apply bifrost pool snapshot + sync key models + tune timeouts + pool weights"
ssh "$HOST" "
  cd $RPATH
  python3 bifrost/apply_snapshot.py
  python3 bifrost/sync_key_models.py
  python3 scripts/runtime/bifrost_tune_timeouts.py 2>&1 | tail -10
  # pool_weights.yaml is the source of truth — creates missing rules (e.g. ops)
  python3 scripts/runtime/apply_pool_weights.py 2>&1 | tail -8
"

echo "→ patch hermes-agent TUI for resolved model display"
ssh "$HOST" "
  cd $RPATH
  if [ -d \$HOME/hermes-agent ]; then
    bash scripts/install/patch_hermes_tui_model.sh 2>&1 | sed 's/^/  /'
  fi
"

echo "→ smoke test"
ssh "$HOST" "
  curl -sf http://localhost:4001/api/providers > /dev/null && echo '  bifrost:   ok' || echo '  bifrost:   FAIL'
  curl -sf http://localhost:4002/health       > /dev/null && echo '  shim:      ok' || echo '  shim:      FAIL'
  curl -sf http://localhost:9118/             > /dev/null && echo '  dashboard: ok' || echo '  dashboard: FAIL'
  for u in circuit-breaker hermes-gateway cb-deadman.timer pi-op-react.timer pi-op-health.timer pi-op-queue.timer pi-op-patch.timer op-rebalance.timer op-discover.timer; do
    state=\$(systemctl --user is-active \"\$u\" 2>/dev/null || echo missing)
    printf '  %-32s %s\n' \"\$u\" \"\$state\"
  done
"

echo "✓ deploy complete"
