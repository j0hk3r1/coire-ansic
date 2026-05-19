#!/usr/bin/env bash
# CoireAnsic — installer
#
# Coire Ansic ("the un-dry cauldron") — self-managing free-tier LLM router
# with adaptive routing, circuit breaker, and dashboard. Pair it with any
# OpenAI-compatible client (yours, hermes-agent, OMP, anything).
#
# Optional adapters (opt-in via flags):
#   --with-hermes      Install Nous hermes-agent (CLI + gateway + scout cron)
#   --with-telegram    Pair telegram bot   (requires --with-hermes)
#   --with-camofox     Anti-detect browser  (free web browsing)
#   --with-searxng     Self-hosted meta-search
#   --with-firecrawl   Local web-extract backend
#   --all              All adapters above
#
# Idempotent — safe to re-run. Skips steps already done.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ─── flag parsing ─────────────────────────────────────────────────────────
WITH_HERMES=0
WITH_TELEGRAM=0
WITH_CAMOFOX=0
WITH_SEARXNG=0
WITH_FIRECRAWL=0
WITH_WEBUI=0
for arg in "$@"; do
  case "$arg" in
    --with-hermes)    WITH_HERMES=1 ;;
    --with-telegram)  WITH_TELEGRAM=1; WITH_HERMES=1 ;;
    --with-camofox)   WITH_CAMOFOX=1 ;;
    --with-searxng)   WITH_SEARXNG=1 ;;
    --with-firecrawl) WITH_FIRECRAWL=1 ;;
    --with-webui)     WITH_WEBUI=1 ;;
    # --all excludes --with-camofox: Camoufox upstream ships a Python lib
    # (no REST server), so the docker service needs an out-of-band fork
    # cloned into ./camofox/src/. Users who want it must opt in explicitly.
    --all)            WITH_HERMES=1; WITH_TELEGRAM=1; WITH_SEARXNG=1; WITH_FIRECRAWL=1; WITH_WEBUI=1 ;;
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
echo "CoireAnsic installer"
echo "  core: ✓ (always)"
echo "  hermes-agent:  $([ $WITH_HERMES -eq 1 ] && echo on || echo off)"
echo "  telegram:      $([ $WITH_TELEGRAM -eq 1 ] && echo on || echo off)"
echo "  camofox:       $([ $WITH_CAMOFOX -eq 1 ] && echo on || echo off)"
echo "  searxng:       $([ $WITH_SEARXNG -eq 1 ] && echo on || echo off)"
echo "  firecrawl:     $([ $WITH_FIRECRAWL -eq 1 ] && echo on || echo off)"
echo "  webui:         $([ $WITH_WEBUI -eq 1 ] && echo on || echo off)"

# ════════════════════════════════════════════════════════════════════════════
# CORE  (always installed)
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
# Count configured provider keys — warn if zero (router would be useless)
PROVIDER_COUNT=0
for v in GROQ_API_KEY GEMINI_API_KEY MISTRAL_API_KEY CEREBRAS_API_KEY NVIDIA_API_KEY \
         CLOUDFLARE_API_KEY OPENROUTER_API_KEY DEEPSEEK_API_KEY SAMBANOVA_API_KEY \
         GITHUB_MODELS_TOKEN COHERE_API_KEY; do
  val="${!v:-}"
  [ -n "$val" ] && PROVIDER_COUNT=$((PROVIDER_COUNT + 1))
done
[ "$PROVIDER_COUNT" -ge 1 ] || die "no provider keys set in .env — set at least one of GROQ/GEMINI/MISTRAL/CEREBRAS/..."
ok ".env validated ($PROVIDER_COUNT provider key(s))"

# ─── core 2. docker stack (bifrost + shim + dashboard) ────────────────────
step "[core] docker compose up — bifrost + strip-shim + dashboard"
# Pre-create all ~/.coire subdirs that docker-compose mounts BEFORE docker
# starts — otherwise docker creates them root-owned, and the host-side
# systemd-user circuit-breaker daemon can't write to its own state dir.
mkdir -p "$HOME/.coire/curator-pool" "$HOME/.coire/operator/incoming_keys" \
         "$HOME/.coire/operator/logs" "$HOME/.coire/operator/discoveries" \
         bifrost/data
chmod 777 bifrost/data 2>/dev/null || true

# Build docker compose profile list based on flags
PROFILES="dashboard"
if [ $WITH_CAMOFOX -eq 1 ]; then
  if [ -f camofox/src/Dockerfile.ci ]; then
    PROFILES="$PROFILES,camofox"
  else
    warn "--with-camofox set but camofox/src/ not populated. Skipping camofox profile."
    warn "  Camoufox source is not bundled (different license, optional fork). Clone your"
    warn "  Camoufox-browser source into ./camofox/src/ then re-run install."
    WITH_CAMOFOX=0
  fi
fi
[ $WITH_SEARXNG -eq 1 ] && PROFILES="$PROFILES,searxng"
[ $WITH_WEBUI   -eq 1 ] && PROFILES="$PROFILES,webui"

COMPOSE_PROFILES="$PROFILES" docker compose up -d --build
# wait for bifrost healthy
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
python3 scripts/runtime/bifrost_tune_timeouts.py 2>&1 | tail -8 || warn "tune_timeouts failed"
# Apply pool_weights.yaml (source of truth — creates ops + any new pools)
python3 scripts/runtime/apply_pool_weights.py 2>&1 | tail -8 || warn "apply_pool_weights failed"
ok "pool config applied"

# ─── core 5. circuit-breaker daemon ───────────────────────────────────────
step "[core] circuit-breaker daemon"
mkdir -p "$HOME/.config/systemd/user"
sed "s|__HFC_ROOT__|$ROOT|g" systemd/circuit-breaker.service > "$HOME/.config/systemd/user/circuit-breaker.service"
systemctl --user daemon-reload
systemctl --user enable --now circuit-breaker.service 2>&1 | tail -2 || warn "circuit-breaker enable failed"
ok "circuit-breaker installed"

# ─── core 6. operator (pi-mono + 7 timers) ────────────────────────────────
# Self-managing layer: cb-deadman + pi-op-{react,health,queue,patch} +
# op-rebalance + op-discover. Isolated to 'ops' bifrost pool so they don't
# eat your user-facing free-tier provider budgets.
step "[core] operator (pi-mono + 7 systemd timers)"

if ! command -v pi >/dev/null; then
  if command -v npm >/dev/null; then
    mkdir -p "$HOME/.npm-global"
    npm config set prefix "$HOME/.npm-global" >/dev/null 2>&1
    export PATH="$HOME/.npm-global/bin:$PATH"
    npm install -g @earendil-works/pi-coding-agent 2>&1 | tail -3 || warn "pi-mono install failed"
    if command -v pi >/dev/null; then
      ok "pi-mono installed at $(command -v pi)"
    else
      warn "pi-mono not on PATH — add \$HOME/.npm-global/bin to PATH"
    fi
  else
    warn "npm not installed — operator timers will be inactive. Install nodejs+npm + re-run for self-management."
  fi
fi

if [ -d operator ] && command -v pi >/dev/null; then
  mkdir -p "$HOME/.pi/agent"
  cp operator/pi-settings.json "$HOME/.pi/agent/settings.json"
  cp operator/pi-models.json   "$HOME/.pi/agent/models.json"
  rm -rf "$HOME/.pi/agent/skills" "$HOME/.pi/agent/prompt-templates"
  cp -r operator/skills          "$HOME/.pi/agent/skills"
  cp -r operator/prompt-templates "$HOME/.pi/agent/prompt-templates"
  ok "pi-mono configs deployed"
fi

if [ -d operator/systemd ]; then
  for unit in operator/systemd/*.service operator/systemd/*.timer; do
    [ -e "$unit" ] || continue
    cp "$unit" "$HOME/.config/systemd/user/"
  done
  systemctl --user daemon-reload
  for timer in operator/systemd/*.timer; do
    name=$(basename "$timer")
    systemctl --user enable --now "$name" >/dev/null 2>&1 || warn "$name enable failed"
  done
  ok "operator timers enabled"
fi

mkdir -p "$HOME/.coire/operator/incoming_keys" "$HOME/.coire/operator/logs" \
         "$HOME/.coire/operator/discoveries" "$HOME/.coire/curator-pool"
ok "operator state dirs created"

# ════════════════════════════════════════════════════════════════════════════
# OPTIONAL ADAPTERS
# ════════════════════════════════════════════════════════════════════════════

# ─── adapter: firecrawl (web_extract backend) ─────────────────────────────
if [ $WITH_FIRECRAWL -eq 1 ]; then
  step "[adapter] firecrawl (local web_extract)"
  if docker ps --filter name=firecrawl-api --format '{{.Names}}' | grep -q firecrawl-api; then
    ok "firecrawl already running"
  else
    bash scripts/install/install_firecrawl.sh || warn "firecrawl install failed"
  fi
fi

# ─── adapter: hermes-agent ────────────────────────────────────────────────
if [ $WITH_HERMES -eq 1 ]; then
  step "[adapter] hermes-agent host install"
  export PATH="$HOME/.local/bin:$HOME/hermes-agent/venv/bin:$HOME/.hermes/hermes-agent/venv/bin:$PATH"
  if ! command -v hermes >/dev/null; then
    curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
    hash -r
    command -v hermes >/dev/null || die "hermes not on PATH after install — check ~/.local/bin or ~/hermes-agent/venv/bin"
    ok "installed at $(command -v hermes)"
  else
    ok "already installed at $(command -v hermes)"
  fi

  step "[adapter] hermes config"
  mkdir -p "$HOME/.hermes"
  envsubst < adapters/hermes/config.yaml.template > "$HOME/.hermes/config.yaml"
  ok "wrote ~/.hermes/config.yaml"

  # firecrawl is the supported web_extract backend — patch in if available
  if [ $WITH_FIRECRAWL -eq 1 ]; then
    python3 - <<'PYEOF' || warn "could not auto-set extract_backend"
import yaml, pathlib
p = pathlib.Path.home() / ".hermes" / "config.yaml"
if not p.exists(): exit(0)
d = yaml.safe_load(p.read_text()) or {}
d.setdefault("web", {})["extract_backend"] = "firecrawl"
yaml.safe_dump(d, p.open("w"), sort_keys=False)
PYEOF
  fi

  # camofox auto-enables hermes 'browser' toolset (browser_navigate / click /
  # snapshot / etc.) — otherwise the camofox container runs but hermes never
  # talks to it. Only flip on when both adapters present.
  if [ $WITH_CAMOFOX -eq 1 ]; then
    python3 - <<'PYEOF' || warn "could not enable browser toolset for hermes"
import yaml, pathlib
p = pathlib.Path.home() / ".hermes" / "config.yaml"
if not p.exists(): exit(0)
d = yaml.safe_load(p.read_text()) or {}
ts = d.setdefault("toolsets", [])
if "browser" not in ts:
    ts.append("browser")
    print("  ✓ added 'browser' to hermes toolsets")
# Ensure camofox engine + URL wired
b = d.setdefault("browser", {})
b.setdefault("engine", "camofox")
yaml.safe_dump(d, p.open("w"), sort_keys=False)
PYEOF
  fi

  step "[adapter] centralise .env via symlink"
  if [ -e "$HOME/.hermes/.env" ] && [ ! -L "$HOME/.hermes/.env" ]; then
    mv "$HOME/.hermes/.env" "$HOME/.hermes/.env.bak.$(date +%s)"
    warn "backed up existing real .env"
  fi
  ln -sf "$ROOT/.env" "$HOME/.hermes/.env"
  ok "linked"

  step "[adapter] patch hermes-agent TUI status bar (provider/model)"
  if [ -d "$HOME/hermes-agent" ]; then
    bash adapters/hermes/patch_hermes_tui_model.sh || warn "TUI patch failed — status bar will show pool name only"
  fi

  step "[adapter] hermes free-provider scout cron (Mondays 04:00)"
  mkdir -p "$HOME/.hermes/scripts"
  cp adapters/hermes/cron/scout_brief.py     "$HOME/.hermes/scripts/"
  cp adapters/hermes/cron/add_candidate.py   "$HOME/.hermes/scripts/"
  chmod +x "$HOME/.hermes/scripts/scout_brief.py" "$HOME/.hermes/scripts/add_candidate.py"
  if command -v hermes >/dev/null && hermes cron list 2>/dev/null | grep -q "free-provider-scout"; then
    ok "hermes cron 'free-provider-scout' already present"
  elif command -v hermes >/dev/null; then
    PROMPT=$(tr "\n" " " < adapters/hermes/cron/scout_hermes_prompt.txt | tr -s " ")
    DELIVER_FLAG=""
    [ $WITH_TELEGRAM -eq 1 ] && DELIVER_FLAG="--deliver telegram"
    hermes cron create "0 4 * * 1" "$PROMPT" \
      --name free-provider-scout --script scout_brief.py $DELIVER_FLAG \
      2>&1 | tail -3 || warn "hermes cron create failed — add manually later"
    ok "scout cron scheduled"
  fi

  step "[adapter] hermes skills hub"
  hermes skills list >/dev/null 2>&1 || true
  ok "skills hub"
fi

# ─── adapter: telegram pairing ────────────────────────────────────────────
if [ $WITH_TELEGRAM -eq 1 ]; then
  step "[adapter] telegram gateway"
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    warn "TELEGRAM_BOT_TOKEN unset in .env — skipping gateway install"
  else
    hermes gateway install 2>&1 | grep -v "already installed" || true
    hermes gateway start 2>&1 | tail -3 || true
    ok "gateway up — pair via: send /start to your bot, then 'hermes pairing list && hermes pairing approve telegram <code>'"
  fi
fi

# ─── verify ───────────────────────────────────────────────────────────────
step "verify"
[ $WITH_HERMES -eq 1 ] && hermes doctor 2>&1 | grep -E "(✓|⚠)" | head -10 || true

cat <<MSG

────────────────────────────────────────────────────────────────────
CoireAnsic is up. Core services:
  • Bifrost AI gateway   http://localhost:4001  (admin / \$BIFROST_PASS)
  • Strip-shim proxy     http://localhost:4002  ← clients connect here
  • Dashboard            http://localhost:9118
  • Circuit-breaker      systemctl --user status circuit-breaker
  • Operator timers      systemctl --user list-timers | grep -E 'pi-op|cb-deadman|op-'

$([ $WITH_HERMES -eq 1 ] && echo "Optional (installed):
  • Hermes CLI           hermes -z \"test\"
  • Scout cron           Mondays 04:00 — adds new free providers")
$([ $WITH_TELEGRAM -eq 1 ] && echo "  • Telegram gateway     journalctl --user -u hermes-gateway -f")
$([ $WITH_CAMOFOX -eq 1 ] && echo "  • Camofox browser      http://localhost:9378")
$([ $WITH_SEARXNG -eq 1 ] && echo "  • SearXNG search       http://localhost:8891")
$([ $WITH_FIRECRAWL -eq 1 ] && echo "  • Firecrawl extract    http://localhost:3002")
$([ $WITH_WEBUI -eq 1 ] && echo "  • Open WebUI chat      http://localhost:${OPENWEBUI_PORT:-3030}  (1st user becomes admin)")

Drop new API keys in ~/.coire/operator/incoming_keys/<name>.txt — pi-op-queue
auto-integrates them within 5 minutes.

Pool topology source-of-truth: scripts/runtime/pool_weights.yaml
────────────────────────────────────────────────────────────────────
MSG
