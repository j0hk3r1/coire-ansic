# CoireAnsic

> *Coire Ansic* — Irish: "the un-dry cauldron." The Dagda's magic cauldron
> from the Tuatha Dé Danann mythology that never emptied; nobody left
> hungry, no matter how many came to feast.

A **free-tier LLM router** for [opencode](https://github.com/sst/opencode) +
[oh-my-openagent (omo)](https://github.com/code-yeongyu/oh-my-openagent).
Aggregates 12+ free-tier providers behind one OpenAI-compatible endpoint
with cascade routing — so omo's Sisyphus orchestrator + sub-agents always
have a path to a working model even when individual providers saturate.

Point any OpenAI-compat client (opencode, plain `curl`, anything) at
`localhost:4002` and get cheap-or-free model access with automatic failover.

## Why

Free-tier inference is great but fragile:
- Every provider has different RPM/RPD/TPM caps
- Caps shift monthly (new providers, EOL'd models)
- A single hammered provider takes down your whole agent
- Omo's multi-agent orchestration burns quota fast — needs deep cascade

This project routes across all of them via [Bifrost](https://github.com/maximhq/bifrost),
fails over automatically when one saturates, and provides ops skills/CLI
tools so you can monitor, diagnose, and rebalance without leaving opencode.

## Architecture

```
opencode + omo (TUI / web)
    │
    ▼
strip-shim :4002       — OpenAI-compat normalizer (reasoning_content, tool-call IDs, retry layers)
    │
    ▼
bifrost :4001          — LLM gateway, cascade routing over 12 providers
    │
    ▼
free-tier providers    — cerebras, cloudflare, gemini, mistral, nvidia-nim,
                         openrouter, sambanova, groq, cohere, github-models,
                         opencode-zen, z.ai
```

## Core (always installed)

| Component | Port | Role |
|---|---|---|
| **Bifrost** | 4001 | LLM gateway with 12 providers + 3 omo pools (omo-main, omo-utility, omo-gemini) + cascade fallback |
| **strip-shim** | 4002 | OpenAI-compat normalizer — strips reasoning_content, rewrites tool-call IDs, retries on known provider bugs (NVIDIA unhashable, max_tokens, param-rejection), Z.ai path proxy |
| **dashboard** | 9118 | Pool health, latency P50/P95, per-provider usage bars, model picker |

## Pools (`scripts/runtime/pool_weights.yaml`)

| Pool | Intent | Used by (omo agents) |
|---|---|---|
| `omo-main` | Sisyphus orchestration + subagent work | sisyphus, sisyphus-junior, atlas, metis, prometheus, hephaestus, oracle, momus |
| `omo-utility` | Small queries (search, explore) | librarian, explore |
| `omo-gemini` | Vision + multimodal | multimodal-looker |

Each pool has 1 primary + 9-12 fallback targets ordered by reliability +
arena MT score. Bifrost walks the cascade automatically on errors.

## Optional adapters (opt-in)

| Flag | What it adds |
|---|---|
| `--with-firecrawl` | Local web_extract backend for omo librarian |
| `--with-searxng` | Self-hosted meta-search backend for omo librarian |
| `--with-camofox` | Anti-detect Firefox — **BYO source**, see [camofox/README.md](camofox/README.md) |
| `--all` | searxng + firecrawl (camofox stays opt-in) |

## Ops layer

Two-tier ops infrastructure for ongoing maintenance:

**opencode skills + slash commands** (use from within opencode):
- `/coire-monitor [window]` — snapshot bifrost activity + categorize errors
- `/coire-probe MODEL` — test tool-calling + latency + rate-limit on a model
- `/coire-health` — stack health (containers, APIs, recent activity, resources)
- `/coire-diagnose` — deep stuck-session detection (orphan streams, error loops)
- `/coire-cascade-show` — live routing rules + arena scores + quota classes
- `/coire-check-quotas` — per-provider live quota headers

**Standalone CLI** (works when opencode is dead):
- `~/coire-tools/coire-health` — same as skill, runs directly
- `~/coire-tools/coire-monitor [1h]`
- `~/coire-tools/coire-probe MODEL`
- `~/coire-tools/coire-cascade-show`
- `~/coire-tools/coire-check-quotas`
- `~/coire-tools/coire-diagnose`
- `~/coire-tools/coire-restart [svc]` — restart docker container (destructive, CLI-only)
- `~/coire-tools/coire-kill-opencode [--tui|--web|--all]` — kill hung opencode (destructive, CLI-only)

Canonical source: `scripts/ops/` in this repo. See [`.opencode/README.md`](.opencode/README.md) for full triage flow.

## Install

```bash
git clone <this-repo> coire-ansic
cd coire-ansic
cp .env.example .env
$EDITOR .env                  # paste at least one provider key
./install.sh                   # core only
./install.sh --with-firecrawl  # + firecrawl
./install.sh --all             # + searxng + firecrawl
```

Idempotent — re-run safely.

### Remote install

```bash
ssh user@server
git clone https://github.com/<owner>/coire-ansic
cd coire-ansic && cp .env.example .env && $EDITOR .env
./install.sh --all
```

By default ops tools deploy to `~/coire-tools/` on the local host. To deploy
to a remote host instead: `OPS_TARGET=user@host ./install.sh ...`.

## Required keys

At least one provider key + `BIFROST_API_KEY`. All free-tier:

| Provider | Sign-up |
|---|---|
| Cerebras | https://cloud.cerebras.ai |
| Cloudflare Workers AI | https://dash.cloudflare.com → AI tab |
| Gemini (Google AI Studio) | https://aistudio.google.com |
| Mistral | https://console.mistral.ai |
| NVIDIA NIM | https://build.nvidia.com |
| OpenRouter | https://openrouter.ai |
| Groq | https://console.groq.com |
| SambaNova | https://cloud.sambanova.ai |
| GitHub Models | https://github.com/marketplace/models (PAT with `models` scope) |
| Cohere | https://dashboard.cohere.com |
| OpenCode Zen | https://opencode.ai (paid-tier preview) |
| Z.ai (Zhipu) | https://z.ai/manage-apikey/apikey-list |

See `.env.example` for the full variable list.

## Using it

### Direct (any OpenAI-compat client)

```bash
curl http://localhost:4002/v1/chat/completions \
  -H "Authorization: Bearer $BIFROST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"omo-main","messages":[{"role":"user","content":"hi"}]}'
```

`model` is the pool name (`omo-main` / `omo-utility` / `omo-gemini`) OR
a direct `provider/model` spec (e.g. `cerebras/zai-glm-4.7`).

### Opencode + omo

Configure opencode's `coire` provider to point at strip-shim:

```json
{
  "provider": {
    "coire": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {"baseURL": "http://localhost:4002/v1"},
      "models": {
        "omo-main":    {"name": "omo-main"},
        "omo-utility": {"name": "omo-utility"},
        "omo-gemini":  {"name": "omo-gemini"}
      }
    }
  }
}
```

Then map omo agents to pools in `~/.config/opencode/oh-my-openagent.json`
(see `adapters/omo/oh-my-openagent.json` for canonical template).

## Extending — add your own provider / pool / model

1. **Probe the model first**: `/coire-probe <provider>/<model>` to verify
   tool-calling + latency + rate-limit headers
2. **Add provider** (if new): see `bifrost/seed.sh` for POST template, or
   POST to `/api/providers` directly (see `.opencode/skills/coire-add-provider`
   when built)
3. **Slot into pool**: edit `scripts/runtime/pool_weights.yaml`, place
   based on observed reliability (tier A=workhorses, D=last-resort slow)
4. **Apply**: `python3 scripts/runtime/apply_pool_weights.py`
5. **Snapshot**: `python3 bifrost/snapshot.py` + commit (or use the
   `coire-snapshot-sync` skill)

## Tear down

```bash
./uninstall.sh             # stops services, keeps data + .env
./uninstall.sh --purge     # also wipe bifrost data + camofox src
```

## Tree

```
coire-ansic/
├── README.md
├── install.sh                          # ~200 lines, idempotent
├── uninstall.sh
├── docker-compose.yml                  # bifrost + shim + dashboard + searxng/camofox profiles
├── .env.example
├── .opencode/                          # opencode skills + slash commands (deployed to ~/.config/opencode/)
│   ├── README.md
│   ├── command/                        # 6 slash commands
│   └── skills/                         # 6 skill defs
├── adapters/
│   └── omo/oh-my-openagent.json        # omo agent → pool mapping
├── bifrost/                            # provider config + snapshots
│   ├── seed.sh
│   ├── apply_snapshot.py
│   ├── snapshot.py
│   ├── sync_key_models.py
│   ├── candidate_providers.json
│   ├── excluded_models.json
│   └── snapshot/{providers.json, routing-rules.json}
├── strip-shim/                         # OpenAI-compat proxy
│   ├── app.py
│   └── Dockerfile
├── dashboard/                          # monitoring UI
│   ├── app.py
│   ├── Dockerfile
│   ├── templates/dashboard.html
│   ├── static/
│   └── tests/
├── scripts/
│   ├── ops/                            # 8 CLI tools + deploy.sh
│   ├── runtime/
│   │   ├── apply_pool_weights.py
│   │   ├── build_models_list.py
│   │   └── pool_weights.yaml           # ★ cascade source-of-truth
│   └── install/install_firecrawl.sh
├── camofox/README.md                   # opt-in Camoufox source guide
├── searxng/settings.yml
└── docs/
    ├── frontend-eval.md                # archived — comparison that led to opencode+omo
    ├── omo-perfect-config.md
    └── omo-pool-tuning.md
```

## How it stays healthy

**Tier 1 — automatic** (no human/skill):
- Bifrost's built-in cascade auto-fails-over on provider errors
- Omo handles agent orchestration internally
- Docker `restart: unless-stopped` keeps containers up

**Tier 2 — skill-on-demand** (user/agent invokes when needed):
- `/coire-monitor` to see drift
- `/coire-diagnose` for stuck sessions
- `/coire-health` for stack-wide check

**Tier 3 — scheduled** (opt-in via opencode's `/loop` or `/schedule`):
- `/loop 4h /coire-health` for periodic check-ins
- `/schedule /coire-monitor` for daily summary

No long-running background daemons. No systemd timers. The system either
self-recovers via bifrost cascade, or surfaces issues for human action via
skills. Simpler to reason about, easier to debug.

## Privacy

- All provider keys live in `.env` — never logged, never sent anywhere
  except the relevant provider
- Bifrost stores routing logs locally only
- Strip-shim doesn't persist request/response bodies
- Dashboard reads everything in-memory, no external telemetry

## Disclaimer

This is research-grade infrastructure. Free-tier providers can change rate
limits, deprecate models, or disappear at any time. The cascade exists
precisely because individual providers are unreliable — but the system
itself is best-effort.

## License

MIT
