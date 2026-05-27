#!/usr/bin/env bash
# Install Firecrawl OSS self-hosted as the local web_extract backend.
#
# Hermes-agent ships a firecrawl backend; we just need a Firecrawl API
# reachable at FIRECRAWL_API_URL. Their official compose stands up 5
# services (api, worker, playwright, redis, rabbitmq, postgres) — heavy
# but drop-in. Idempotent: skip clone/setup if already running.
#
# RAM ~2GB while idle, peaks ~3GB during heavy crawls. Disk ~4GB.
#
# Run after install.sh's main stack is up.

set -euo pipefail

FIRECRAWL_DIR="${FIRECRAWL_DIR:-$HOME/firecrawl}"
COMPOSE_URL="https://raw.githubusercontent.com/mendableai/firecrawl/main/docker-compose.yaml"
ENV_URL="https://raw.githubusercontent.com/mendableai/firecrawl/main/apps/api/.env.example"

step() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }

# Idempotency check — already running?
if docker ps --filter name=firecrawl-api --format '{{.Names}}' | grep -q firecrawl-api; then
  ok "firecrawl already running"
  exit 0
fi

step "fetch firecrawl compose"
mkdir -p "$FIRECRAWL_DIR"
cd "$FIRECRAWL_DIR"
[ -f docker-compose.yaml ] || curl -sSL -o docker-compose.yaml "$COMPOSE_URL"
[ -f .env ] || curl -sSL -o .env "$ENV_URL"
ok "compose fetched"

step "configure"
# Disable Supabase auth (we self-host without it).
sed -i "s/USE_DB_AUTHENTICATION=true/USE_DB_AUTHENTICATION=false/" .env

# Rabbitmq needs an explicit volume w/ correct ownership (uid 999) — the
# default anonymous volume gets created with root:root mode 700 on first
# run, then rabbitmq (uid 999) can't read .erlang.cookie next launch.
mkdir -p ./rabbit_data
sudo chown -R 999:999 ./rabbit_data
sudo chmod 700 ./rabbit_data

# Write a docker-compose.override.yaml instead of mutating upstream's
# docker-compose.yaml in place. Compose merges this automatically.
# - Swap build → prebuilt ghcr.io images (lighter + faster than local build)
# - Use `!reset null` to drop upstream `build:` so compose doesn't rebuild
# - Add rabbitmq volume for cookie ownership fix
cat > docker-compose.override.yaml <<'YAML'
services:
  api:
    image: ghcr.io/firecrawl/firecrawl:latest
    build: !reset null
  playwright-service:
    image: ghcr.io/firecrawl/playwright-service:latest
    build: !reset null
  nuq-postgres:
    image: ghcr.io/firecrawl/nuq-postgres:latest
    build: !reset null
  rabbitmq:
    volumes:
      - ./rabbit_data:/var/lib/rabbitmq
YAML
ok "configured (override.yaml written; upstream files unmodified)"

step "pull images (~3GB)"
docker compose pull 2>&1 | tail -3
ok "pulled"

step "bring up"
# First attempt often fails with 'dependency rabbitmq failed to start:
# unhealthy' because rabbitmq takes 30-60s to pass its healthcheck on
# cold boot. Tolerate the failure — retry loop below picks up the
# pieces. The `|| true` is necessary because of `set -e` at top.
docker compose up -d 2>&1 | tail -5 || true

# Re-run `compose up -d` until no containers stuck in 'created' state.
# Each retry waits for any deps that have come healthy since last try.
for i in $(seq 1 18); do
  sleep 5
  PENDING=$(docker compose ps --status created -q 2>/dev/null | wc -l)
  if [ "$PENDING" -gt 0 ]; then
    docker compose up -d > /dev/null 2>&1 || true
  else
    break
  fi
done
docker ps --filter name=firecrawl --format "  {{.Names}}: {{.Status}}"
ok "up"

step "smoke test"
# Poll for API readiness up to 90s
for i in $(seq 1 18); do
  if curl -sf -o /dev/null http://localhost:3002/; then
    ok "firecrawl API @ http://localhost:3002 ready"
    break
  fi
  sleep 5
  [ "$i" -eq 18 ] && warn "API not responding after 90s — check 'docker logs firecrawl-api-1'"
done

cat <<MSG

Firecrawl is up at http://localhost:3002 (no-auth on local LAN).
Point omo's librarian web_extract at: http://172.17.0.1:3002
MSG
