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
#   --with-camofox     Anti-detect Firefox (auto-clones redf0x1/camofox-browser, MIT)
#   --with-searxng     Self-hosted meta-search (omo librarian backend)
#   --with-firecrawl   Local web-extract backend (omo librarian)
#   --all              searxng + firecrawl + camofox (camofox clones ~50MB + downloads ~150MB browser on first run)
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
    # --all now includes camofox since install.sh auto-clones redf0x1/camofox-browser.
    # Skip --with-camofox if you don't want the ~150MB browser binary download.
    --all)            WITH_SEARXNG=1; WITH_FIRECRAWL=1; WITH_CAMOFOX=1 ;;
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
  # Auto-fetch redf0x1/camofox-browser (MIT — REST API wrapping Camoufox engine,
  # port 9377). Clone if missing, pull if present.
  if [ ! -d camofox/src/.git ]; then
    rm -rf camofox/src 2>/dev/null
    git clone --depth 1 https://github.com/redf0x1/camofox-browser camofox/src 2>&1 | tail -3 \
      || { warn "git clone redf0x1/camofox-browser failed — skipping camofox"; WITH_CAMOFOX=0; }
  else
    (cd camofox/src && git pull --ff-only 2>&1 | tail -2) || warn "camofox/src git pull failed (continuing)"
  fi
  if [ $WITH_CAMOFOX -eq 1 ] && [ -f camofox/src/Dockerfile ]; then
    PROFILES="$PROFILES,camofox"
    # CAMOFOX_API_KEY auto-generated if missing
    if [ -z "${CAMOFOX_API_KEY:-}" ]; then
      GEN_CFK=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
      if grep -q '^CAMOFOX_API_KEY=' .env; then
        sed -i "s|^CAMOFOX_API_KEY=.*|CAMOFOX_API_KEY=$GEN_CFK|" .env
      else
        printf '\nCAMOFOX_API_KEY=%s\n' "$GEN_CFK" >> .env
      fi
      export CAMOFOX_API_KEY="$GEN_CFK"
      ok "generated CAMOFOX_API_KEY"
    fi
    # Camofox container runs as UID 1000 (node user). Pre-chown so writes
    # to the bind-mounted profile dir don't EACCES on first start.
    mkdir -p camofox/data
    if [ "$(stat -c %u camofox/data)" != "1000" ]; then
      sudo chown -R 1000:1000 camofox/data 2>/dev/null || warn "could not chown camofox/data — container may fail on first write"
    fi
  else
    warn "camofox/src/Dockerfile not present after clone — skipping"
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

# ─── core 5. opencode + omo config ────────────────────────────────────────
step "[core] opencode + omo config — deploy ~/.config/opencode/"
mkdir -p "$HOME/.config/opencode/skills" "$HOME/.config/opencode/command"

# opencode.json — merge mode if user already has one (preserves LSP + extra MCPs)
if [ -f "$HOME/.config/opencode/opencode.json" ]; then
  # User already has opencode.json — merge coire provider in, leave rest alone.
  cp "$HOME/.config/opencode/opencode.json" "$HOME/.config/opencode/opencode.json.bak.$(date +%s)"
  python3 - <<'PY'
import json, pathlib
src = pathlib.Path("adapters/opencode/opencode.json.template")
dst = pathlib.Path.home() / ".config" / "opencode" / "opencode.json"
template = json.loads(src.read_text())
current = json.loads(dst.read_text())
# Replace coire provider section (and any subsections we own)
current.setdefault("provider", {})["coire"] = template["provider"]["coire"]
# Merge MCPs by key (don't overwrite user's own MCPs)
for k, v in template.get("mcp", {}).items():
    current.setdefault("mcp", {}).setdefault(k, v)
# Ensure omo plugin in plugin list
plugins = current.setdefault("plugin", [])
omo = "oh-my-openagent@latest"
if not any(p.startswith("oh-my-openagent") for p in plugins):
    plugins.append(omo)
dst.write_text(json.dumps(current, indent=2))
print(f"  merged coire provider into {dst}")
PY
  ok "opencode.json merged (backed up existing)"
else
  cp adapters/opencode/opencode.json.template "$HOME/.config/opencode/opencode.json"
  ok "opencode.json deployed (fresh)"
fi

# omo agent → pool mapping
cp adapters/omo/oh-my-openagent.json "$HOME/.config/opencode/oh-my-openagent.json"
ok "oh-my-openagent.json deployed"

# ─── core 6. ops tools (CLI + opencode skills) ────────────────────────────
step "[core] ops tools — deploy ~/coire-tools/ + opencode skills"
mkdir -p "$HOME/coire-tools"
for f in scripts/ops/coire-health scripts/ops/coire-kill-opencode \
         scripts/ops/coire-restart scripts/ops/coire-cascade-show \
         scripts/ops/coire-check-quotas scripts/ops/coire-diagnose \
         scripts/ops/coire-snapshot-sync; do
  cp "$f" "$HOME/coire-tools/$(basename $f)"
  chmod +x "$HOME/coire-tools/$(basename $f)"
done
cp -r .opencode/skills/* "$HOME/.config/opencode/skills/"
cp -r .opencode/command/* "$HOME/.config/opencode/command/"
ln -sf "$HOME/.config/opencode/skills/coire-monitor/scripts/monitor.py" "$HOME/coire-tools/coire-monitor"
ln -sf "$HOME/.config/opencode/skills/coire-probe/scripts/probe.py" "$HOME/coire-tools/coire-probe"
chmod +x "$HOME/.config/opencode/skills/coire-monitor/scripts/monitor.py"
chmod +x "$HOME/.config/opencode/skills/coire-probe/scripts/probe.py"
ok "ops tools deployed (~/coire-tools + ~/.config/opencode/{skills,command})"

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
