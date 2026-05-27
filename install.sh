#!/usr/bin/env bash
# coire-ansic — installer
#
# Self-managing free-tier LLM router targeting opencode+omo as the harness.
# Aggregates 10+ free-tier providers behind one OpenAI-compatible endpoint
# at strip-shim (:4002). Bifrost handles cascade routing; omo handles agent
# orchestration; ops layer (./scripts/ops + .opencode/skills) handles
# monitoring + diagnostics.
#
# Optional adapters (opt-in via flags):
#   --with-camofox     Anti-detect browser (free web browsing for omo)
#   --with-searxng     Self-hosted meta-search (omo librarian backend)
#   --with-firecrawl   Local web-extract backend (omo librarian)
#   --all              Above + dashboard (camofox needs source — see camofox/README.md)
#
# Idempotent — safe to re-run. Skips steps already done.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ─── flag parsing ─────────────────────────────────────────────────────────
WITH_CAMOFOX=0
WITH_SEARXNG=0
WITH_FIRECRAWL=0
for arg in "$@"; do
  case "$arg" in
    --with-camofox)   WITH_CAMOFOX=1 ;;
    --with-searxng)   WITH_SEARXNG=1 ;;
    --with-firecrawl) WITH_FIRECRAWL=1 ;;
    # --all excludes --with-camofox: Camoufox upstream ships a Python lib
    # (no REST server), so the docker service needs an out-of-band fork
    # cloned into ./camofox/src/. Users who want it must opt in explicitly.
    --all)            WITH_SEARXNG=1; WITH_FIRECRAWL=1 ;;
    -h|--help)
      grep -E "^# " "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "unknown flag: $arg (try --help)"; exit 64 ;;
  esac
done

step() { printf "\n\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }
die()  { printf "  \033[1;31m✗\033[0m %s\n" "$*"; exit 1; }

echo
echo "coire-ansic installer"
echo "  core: ✓ (always — bifrost + strip-shim + dashboard)"
echo "  camofox:    $([ $WITH_CAMOFOX -eq 1 ] && echo on || echo off)"
echo "  searxng:    $([ $WITH_SEARXNG -eq 1 ] && echo on || echo off)"
echo "  firecrawl:  $([ $WITH_FIRECRAWL -eq 1 ] && echo on || echo off)"

# ════════════════════════════════════════════════════════════════════════════
# CORE
# ════════════════════════════════════════════════════════════════════════════

# ─── core 1. .env ─────────────────────────────────────────────────────────
step "[core] .env check"
[ -f .env ] || { cp .env.example .env; warn "created .env from template — edit it with your keys, then re-run"; exit 1; }
set -a; source .env; set +a
[ -n "${BIFROST_API_KEY:-}" ] && [ "$BIFROST_API_KEY" != "sk-CHANGE-ME-32-random-bytes" ] || die "set BIFROST_API_KEY in .env"
if [ -z "${BIFROST_PASS:-}" ]; then
  GEN_PASS=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
  if grep -q '^BIFROST_PASS=' .env; then
    sed -i "s|^BIFROST_PASS=.*|BIFROST_PASS=$GEN_PASS|" .env
  else
    printf '\nBIFROST_PASS=%s\n' "$GEN_PASS" >> .env
  fi
  export BIFROST_PASS="$GEN_PASS"
  ok "generated BIFROST_PASS"
fi
PROVIDER_COUNT=0
for v in GROQ_API_KEY GEMINI_API_KEY MISTRAL_API_KEY CEREBRAS_API_KEY NVIDIA_API_KEY \
         CLOUDFLARE_API_KEY OPENROUTER_API_KEY DEEPSEEK_API_KEY SAMBANOVA_API_KEY \
         GITHUB_MODELS_TOKEN COHERE_API_KEY ZAI_API_KEY; do
  val="${!v:-}"
  [ -n "$val" ] && PROVIDER_COUNT=$((PROVIDER_COUNT + 1))
done
[ "$PROVIDER_COUNT" -ge 1 ] || die "no provider keys set in .env — set at least one of GROQ/GEMINI/MISTRAL/CEREBRAS/..."
ok ".env validated ($PROVIDER_COUNT provider key(s))"

# ─── core 2. docker stack ─────────────────────────────────────────────────
step "[core] docker compose up — bifrost + strip-shim + dashboard"
mkdir -p bifrost/data
chmod 777 bifrost/data 2>/dev/null || true

PROFILES="dashboard"
if [ $WITH_CAMOFOX -eq 1 ]; then
  if [ -f camofox/src/Dockerfile.ci ]; then
    PROFILES="$PROFILES,camofox"
  else
    warn "--with-camofox set but camofox/src/ not populated. Skipping."
    warn "  Clone Camoufox-browser source into ./camofox/src/ then re-run."
    WITH_CAMOFOX=0
  fi
fi
[ $WITH_SEARXNG -eq 1 ] && PROFILES="$PROFILES,searxng"

COMPOSE_PROFILES="$PROFILES" docker compose up -d --build
for i in {1..60}; do
  [ "$(docker inspect coire-bifrost --format '{{.State.Health.Status}}' 2>/dev/null)" = "healthy" ] && break
  sleep 2
done
ok "docker stack up (profiles: $PROFILES)"

# ─── core 3. seed bifrost ─────────────────────────────────────────────────
step "[core] seeding bifrost (providers + initial pools)"
command -v jq >/dev/null || { warn "installing jq"; sudo apt-get install -y jq >/dev/null; }
./bifrost/seed.sh
ok "seeded"

# ─── core 4. apply tuned snapshot + pool weights ──────────────────────────
step "[core] applying pool config"
if [ -f bifrost/snapshot/routing-rules.json ]; then
  python3 bifrost/apply_snapshot.py 2>&1 | tail -8 || warn "apply_snapshot failed (non-fatal)"
fi
python3 bifrost/sync_key_models.py 2>&1 | tail -5 || warn "sync_key_models failed"
python3 scripts/runtime/apply_pool_weights.py 2>&1 | tail -8 || warn "apply_pool_weights failed"
ok "pool config applied"

# ─── core 5. ops tools (CLI + opencode skills) ────────────────────────────
step "[core] ops tools — deploy ~/coire-tools/ + opencode skills"
if [ -x scripts/ops/deploy.sh ]; then
  # deploy.sh handles ssh to .93 by default. For local install, copy to
  # local ~/coire-tools/ instead.
  TARGET="${OPS_TARGET:-localhost}"
  if [ "$TARGET" = "localhost" ]; then
    mkdir -p "$HOME/coire-tools"
    for f in scripts/ops/coire-health scripts/ops/coire-kill-opencode \
             scripts/ops/coire-restart scripts/ops/coire-cascade-show \
             scripts/ops/coire-check-quotas scripts/ops/coire-diagnose; do
      cp "$f" "$HOME/coire-tools/$(basename $f)"
      chmod +x "$HOME/coire-tools/$(basename $f)"
    done
    mkdir -p "$HOME/.config/opencode/skills" "$HOME/.config/opencode/command"
    cp -r .opencode/skills/* "$HOME/.config/opencode/skills/"
    cp -r .opencode/command/* "$HOME/.config/opencode/command/"
    ln -sf "$HOME/.config/opencode/skills/coire-monitor/scripts/monitor.py" "$HOME/coire-tools/coire-monitor"
    ln -sf "$HOME/.config/opencode/skills/coire-probe/scripts/probe.py" "$HOME/coire-tools/coire-probe"
    ok "ops tools deployed locally to ~/coire-tools + ~/.config/opencode/"
  else
    TARGET="$TARGET" bash scripts/ops/deploy.sh 2>&1 | tail -5
    ok "ops tools deployed to $TARGET"
  fi
fi

# ════════════════════════════════════════════════════════════════════════════
# OPTIONAL ADAPTERS
# ════════════════════════════════════════════════════════════════════════════

if [ $WITH_FIRECRAWL -eq 1 ]; then
  step "[adapter] firecrawl (local web_extract for omo)"
  if docker ps --filter name=firecrawl-api --format '{{.Names}}' | grep -q firecrawl-api; then
    ok "firecrawl already running"
  else
    bash scripts/install/install_firecrawl.sh || warn "firecrawl install failed"
  fi
fi

# ─── verify ───────────────────────────────────────────────────────────────
step "verify"
[ -x "$HOME/coire-tools/coire-health" ] && "$HOME/coire-tools/coire-health" 2>&1 | grep -E "✓|✗" | head -10 || true

cat <<MSG

────────────────────────────────────────────────────────────────────
coire-ansic is up. Core services:
  • Bifrost AI gateway   http://localhost:4001  (admin / \$BIFROST_PASS)
  • Strip-shim proxy     http://localhost:4002  ← clients connect here
  • Dashboard            http://localhost:9118

$([ $WITH_CAMOFOX -eq 1 ]   && echo "  • Camofox browser      http://localhost:9378")
$([ $WITH_SEARXNG -eq 1 ]   && echo "  • SearXNG search       http://localhost:8891")
$([ $WITH_FIRECRAWL -eq 1 ] && echo "  • Firecrawl extract    http://localhost:3002")

Use opencode+omo (or any OpenAI-compat client) pointed at :4002.

Ops layer:
  • ~/coire-tools/*       — CLI tools (coire-health, coire-monitor, ...)
  • /<slash> in opencode  — same tools as skills (/coire-monitor, /coire-probe, ...)
  • See .opencode/README.md for full triage flow

Pool topology source-of-truth: scripts/runtime/pool_weights.yaml
────────────────────────────────────────────────────────────────────
MSG
