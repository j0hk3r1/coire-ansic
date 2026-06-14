<p align="center">
  <img src="assets/logo.png" alt="CoireAnsic — the cauldron that never empties" width="300">
</p>

<h1 align="center">CoireAnsic</h1>

> *Coire Ansic* — Irish: "the un-dry cauldron." The Dagda's magic cauldron from the Tuatha
> Dé Danann that never emptied; nobody left hungry, no matter how many came to feast.

A self-hosted **free-tier LLM router**. Aggregates ~12 free-tier providers behind one
OpenAI-compatible endpoint with automatic cascade failover — so your coding agent always has
a working model even when individual providers saturate.

**Bring your own harness.** CoireAnsic ships *no* agent — you install your own
(opencode, pi, hermes, Claude Code, …) and point it at the router.

```
You install the router        →  ./install.sh   (shim :4001 → bifrost, from your .env)
You install your harness       →  opencode / pi / hermes / Claude Code
You connect it                 →  copy-paste from docs/connect/
You use free-tier models free  →  your harness now runs on the cascade
```

## Why

Free-tier inference is great but fragile: every provider has different RPM/RPD/TPM caps,
caps shift monthly, and a single hammered provider stalls your agent. CoireAnsic routes
across all of them via [Bifrost](https://github.com/maximhq/bifrost) and fails over
automatically when one saturates.

## Architecture

```
your harness  (opencode / pi / hermes / Claude Code — you bring it)
     │
     ▼
strip-shim :4001         — always-on front door. Normalizes every request
     │                     (tool-call formats, param-rejection + reasoning-only
     │                     retries) on /v1; passes /anthropic + /api through.
     ▼
bifrost (:4011)          — gateway behind the shim. OpenAI-compat /v1 + Anthropic
     │                     /anthropic. Cascade routing over ~12 providers. Driven
     ▼                     entirely by bifrost/config.json (providers + env-keys + pools).
free-tier providers      — cerebras, cloudflare, gemini, mistral, nvidia-nim, openrouter,
                           sambanova, groq, cohere, github-models, opencode-zen, z.ai
```

**The whole router is one declarative file: [`bifrost/config.json`](bifrost/config.json).**
Providers, keys (referenced from `.env`, never stored in the file), and the routing pools all
live there. Edit it, re-run `./install.sh`, done.

The strip-shim normalizer is part of the core now — it's the always-on `:4001` front door that
fixes provider tool-call quirks before forwarding to bifrost. This stack is just the router —
auxiliary services (SearXNG, Camofox, Firecrawl) live in their own deploys, not here.

## Install

```bash
git clone https://github.com/<owner>/coire-ansic && cd coire-ansic
cp .env.example .env
$EDITOR .env            # paste at least one free provider key
./install.sh            # brings up bifrost, smoke-tests the pools
```

Idempotent — re-run any time. The installer validates `.env`, renders config, starts
bifrost, and confirms every pool routes.

## Pools (models)

The router exposes four capability tiers as model names:

| model | for |
|---|---|
| `coire-main` | top reasoning + tool-calling (the workhorse) |
| `coire-fast` | small / high-RPM utility (search, quick calls) |
| `coire-vision` | multimodal / vision |
| `coire-chat` | fast conversational (sub-second cascade) |

`model` can also be a direct `provider/model` (e.g. `cerebras/zai-glm-4.7`) to pin one
target. Each pool is one Bifrost routing rule: a weighted primary set + an ordered fallback
cascade. Tune them in `bifrost/config.json`.

## Connect a harness

See **[`docs/connect/`](docs/connect/)** — one copy-paste guide each:

| harness | how |
|---|---|
| [opencode](docs/connect/opencode.md) | OpenAI-compat provider → `http://<host>:4001/v1` |
| [pi](docs/connect/pi.md) | `~/.pi/agent/models.json` → `:4001/v1` |
| [hermes](docs/connect/hermes.md) | OpenAI base URL → `:4001/v1` |
| [Claude Code](docs/connect/claude-code.md) | `ANTHROPIC_BASE_URL` → `:4001/anthropic` |

*Coming later:* Codex (needs a Responses-API bridge) · omo (opencode plugin, needs extra
pools).

Quick check the router works before wiring a harness:

```bash
curl http://localhost:4001/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"coire-main","messages":[{"role":"user","content":"say OK"}],"max_tokens":256}'
```

## Required keys

At least one free-tier provider key in `.env`. Sign-ups (all have a free tier; limits vary
and change often):

| Provider | Sign-up |
|---|---|
| Cerebras | https://cloud.cerebras.ai |
| Cloudflare Workers AI | https://dash.cloudflare.com → AI |
| Gemini (Google AI Studio) | https://aistudio.google.com |
| Mistral | https://console.mistral.ai |
| NVIDIA NIM | https://build.nvidia.com |
| OpenRouter | https://openrouter.ai |
| Groq | https://console.groq.com |
| SambaNova | https://cloud.sambanova.ai |
| GitHub Models | https://github.com/marketplace/models (PAT, `models` scope) |
| Cohere | https://dashboard.cohere.com |
| OpenCode Zen | https://opencode.ai |
| Z.ai (Zhipu) | https://z.ai/manage-apikey/apikey-list |

Cloudflare also needs `CLOUDFLARE_ACCOUNT_ID`. See `.env.example` for the full list.

## Add / change a provider or pool

1. Edit [`bifrost/config.json`](bifrost/config.json):
   - **provider** → add under `providers.<name>` with `keys:[{value:"env.YOUR_KEY", models:["*"]}]`
     (custom OpenAI-compat providers also need `network_config.base_url` +
     `custom_provider_config.request_path_overrides`).
   - **pool / cascade** → edit a rule under `governance.routing_rules` (weighted `targets` +
     ordered `fallbacks`).
2. Add the key to `.env`.
3. `./install.sh` to re-render + reload.

## Auth & exposure

Inference is **unauthenticated by default** — intended for a trusted LAN. `BIFROST_PASS`
guards the admin/management API. To expose the router beyond a trusted network, set a Bifrost
virtual key (`sk-bf-*`) and enable `client.enforce_auth_on_inference` in `config.json`.

## Tear down

```bash
./uninstall.sh           # stop services, keep .env + data
./uninstall.sh --purge   # also wipe bifrost/data + camofox
```

## Privacy

Keys live only in `.env`, referenced into config as `env.` placeholders — never written into
`bifrost/config.json`, never logged, never sent anywhere except the relevant provider.
Bifrost stores routing logs locally only. No external telemetry.

## Disclaimer

Research-grade infrastructure. Free-tier providers can change limits, deprecate models, or
vanish at any time — the cascade exists precisely because individual providers are
unreliable, but the system is best-effort.

## License

MIT
