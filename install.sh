#!/usr/bin/env bash
# CoireAnsic — installer (Phase 0 core: bifrost + pools + env)
#
# Brings up the free-tier router from a single declarative config:
#   bifrost/config.json  (git-tracked; secrets are env. refs, no keys inside)
#       │  install renders ${CLOUDFLARE_ACCOUNT_ID} -> bifrost/data/config.json (gitignored)
#       ▼
#   bifrost (:4011)  reads /app/data/config.json on startup → providers + pools live
#       ▲
#   strip-shim :4001  the always-on front door — normalizes every request, then
#                     forwards to bifrost. Both are core (come up by default).
#
# Clients point at http://<host>:4001/v1 (OpenAI-compat, via the shim) — see docs/connect/.
#
# Optional Layer-2 services (opt-in): --with-searxng --with-camofox
# Idempotent — safe to re-run.

set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"

PROFILES=""
for arg in "$@"; do
  case "$arg" in
    --with-shim)      ;;  # deprecated no-op: strip-shim is now a core service
    --with-searxng)   PROFILES="$PROFILES,searxng" ;;
    --with-camofox)   PROFILES="$PROFILES,camofox" ;;
    -h|--help) grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $arg (try --help)"; exit 64 ;;
  esac
done
PROFILES="${PROFILES#,}"

step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[1;31m✗\033[0m %s\n' "$*"; exit 1; }

# ── 1. .env ────────────────────────────────────────────────────────────────
step ".env"
[ -f .env ] || { cp .env.example .env; warn "created .env from template — add your provider keys, then re-run"; exit 1; }
set -a; source .env; set +a

PROVIDER_COUNT=0
for v in GROQ_API_KEY GEMINI_API_KEY MISTRAL_API_KEY CEREBRAS_API_KEY NVIDIA_API_KEY \
         CLOUDFLARE_API_KEY OPENROUTER_API_KEY SAMBANOVA_API_KEY GITHUB_MODELS_TOKEN \
         COHERE_API_KEY OPENCODE_ZEN_API_KEY ZAI_API_KEY; do
  [ -n "${!v:-}" ] && PROVIDER_COUNT=$((PROVIDER_COUNT+1))
done
[ "$PROVIDER_COUNT" -ge 1 ] || die "no provider keys in .env — set at least one (GROQ/GEMINI/MISTRAL/CEREBRAS/...)"

# BIFROST_PASS guards the admin/management API. Auto-generate if absent.
if [ -z "${BIFROST_PASS:-}" ]; then
  GEN=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
  if grep -q '^BIFROST_PASS=' .env; then sed -i "s|^BIFROST_PASS=.*|BIFROST_PASS=$GEN|" .env
  else printf '\nBIFROST_PASS=%s\n' "$GEN" >> .env; fi
  export BIFROST_PASS="$GEN"; ok "generated BIFROST_PASS (admin API)"
fi
ok ".env validated ($PROVIDER_COUNT provider key(s))"
# NOTE: inference is unauthenticated by default (trusted-LAN deploy). BIFROST_API_KEY is
# optional — only used if you enable client.enforce_auth_on_inference + a virtual key.

# ── 2. render config.json ────────────────────────────────────────────────────
step "render bifrost/config.json → bifrost/data/config.json"
command -v envsubst >/dev/null || { warn "installing gettext-base (envsubst)"; sudo apt-get install -y gettext-base >/dev/null; }
mkdir -p bifrost/data
if [ -n "${CLOUDFLARE_API_KEY:-}" ] && [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  warn "CLOUDFLARE_API_KEY set but CLOUDFLARE_ACCOUNT_ID missing — cloudflare provider will not work"
fi
# Only ${CLOUDFLARE_ACCOUNT_ID} is a shell-style token; bifrost's own env.KEY refs are left intact.
envsubst '${CLOUDFLARE_ACCOUNT_ID}' < bifrost/config.json > bifrost/data/config.json
python3 -c "import json;json.load(open('bifrost/data/config.json'))" || die "rendered config.json is not valid JSON"
ok "config rendered ($(python3 -c "import json;c=json.load(open('bifrost/data/config.json'));print(len(c['providers']),'providers,',len(c['governance']['routing_rules']),'pools')"))"

# ── 3. bring up bifrost + shim ───────────────────────────────────────────────
# config.json is the source of truth, but bifrost's sqlite config_store caches it on first
# boot and will NOT re-read an edited config.json afterwards. So drop the cache here to force
# a clean re-import every install — otherwise "edit config.json → re-run install.sh" silently
# no-ops. Safe: the whole config is declarative (no runtime-only state worth keeping).
step "docker compose up — bifrost + strip-shim${PROFILES:+ (+profiles: $PROFILES)}"
docker compose stop bifrost 2>/dev/null || true
rm -f bifrost/data/config.db bifrost/data/config.db-wal bifrost/data/config.db-shm
COMPOSE_PROFILES="$PROFILES" docker compose up -d --build
for _ in $(seq 1 60); do
  [ "$(docker inspect coire-bifrost --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
[ "$(docker inspect coire-bifrost --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] || die "bifrost did not become healthy — check: docker logs coire-bifrost"
ok "bifrost healthy ($(curl -sf http://localhost:4011/api/providers | python3 -c "import json,sys;print(len(json.load(sys.stdin).get('providers',[])),'providers loaded')"))"
# Shim is the public front door — wait for it before smoke-testing :4001.
for _ in $(seq 1 30); do
  [ "$(docker inspect coire-strip-shim --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
[ "$(docker inspect coire-strip-shim --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] || die "strip-shim did not become healthy — check: docker logs coire-strip-shim"
ok "strip-shim healthy (front door :4001 → bifrost)"

# ── 4. smoke-test each pool ──────────────────────────────────────────────────
step "smoke-test pools"
SMOKE_FAIL=0
for pool in coire-main coire-fast coire-vision; do
  # max_tokens generous: reasoning-model primaries spend tokens thinking before content
  body=$(printf '{"model":"%s","messages":[{"role":"user","content":"Reply with exactly: OK"}],"max_tokens":256}' "$pool")
  resp=$(curl -s -m 60 http://localhost:4001/v1/chat/completions -H "Content-Type: application/json" -d "$body")
  if echo "$resp" | python3 -c "import json,sys;d=json.load(sys.stdin);c=(d.get('choices') or [{}])[0].get('message',{}).get('content');sys.exit(0 if c else 1)" 2>/dev/null; then
    ok "$pool → $(echo "$resp" | python3 -c "import json,sys;print('served by',json.load(sys.stdin).get('model','?'))")"
  else
    warn "$pool → no content: $(echo "$resp" | head -c 160)"; SMOKE_FAIL=1
  fi
done
[ "$SMOKE_FAIL" -eq 0 ] && ok "all pools routing" || warn "some pools failed — check provider keys / quotas"

cat <<MSG

────────────────────────────────────────────────────────────────────
CoireAnsic core is up.
  • Front door (shim) http://localhost:4001        ← point your harness here
  • OpenAI-compat     http://localhost:4001/v1     (normalized, then → bifrost)
  • Anthropic-compat  http://localhost:4001/anthropic
  • Bifrost admin     http://localhost:4011        (admin: admin / \$BIFROST_PASS, loopback)
  • Pools (models)    coire-main · coire-fast · coire-vision

Connect a harness: see docs/connect/  (opencode · pi · hermes · claude-code)
Edit providers/pools: bifrost/config.json → re-run ./install.sh
────────────────────────────────────────────────────────────────────
MSG
