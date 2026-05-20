# Changelog

## 0.1.0 — initial public release

Self-managing free-tier LLM router. Aggregates 10 free-tier providers
behind one OpenAI-compatible endpoint with adaptive routing and an
autonomous operator layer.

### Core

- **Bifrost LLM gateway** (port 4001) — 10 providers, 7 weighted pools
  (best / code / compress / fast / mid / vision / ops) with fallback chains
- **strip-shim** (port 4002) — OpenAI-compat wrapper, normalises
  `reasoning_content` + Mistral tool-call IDs
- **Dashboard** (port 9118) — pool health, demoted state, CB cooldowns,
  P50/P95 latency, usage estimates vs caps, drift summary
- **Circuit breaker daemon** — real-time 429/timeout demote + restore.
  Burst-rate vs daily-cap distinction via `Retry-After` heuristics +
  provider-specific signature matching. State-locked via `fcntl.LOCK_EX`
  (no torn writes, no race)
- **Operator timers** (systemd user):
  - `cb-deadman` (2min) — restarts CB if it dies
  - `pi-op-react` (60min) — recovers stuck demotes via dashboard API
  - `pi-op-health` (hourly) — read-only status audit → JSONL
  - `pi-op-queue` (5min) — auto-onboards keys dropped in
    `~/.coire/operator/incoming_keys/`
  - `pi-op-patch` (daily) — reconciles upstream patches
  - `op-rebalance` (daily) — adaptive weights based on 24h util,
    saturation-aware via CB daily-quota state
  - `op-discover` (weekly) — `/v1/models` scan, surfaces new candidates
- **`ops` pool** — operator-isolated routing. Pi-op timers route here
  exclusively (cerebras + groq 8b + nvidia llama-70b, 14400+ RPD each)
  so maintenance can't eat user-facing pool budgets

### Verified providers (header-probed free-tier caps)

| Provider | Caps |
|---|---|
| groq | 14400 RPD/model, 6000 TPM |
| cerebras | 14400 RPD, 30 RPM, 60k TPM, 1M TPD/model |
| mistral | small=50RPM/50kTPM, magistral=5RPM, large=4RPM |
| cohere | 20 RPM trial, 1000 monthly call ceiling |
| github-models | 20000 RPM, 2M TPM, 60s renewal |
| sambanova | 20 RPD |
| openrouter | 50 RPD pooled across :free on $0-credit |
| gemini | 250 RPD flash, 25-50 RPD pro |
| nvidia-nim | 40 RPM/model, 10k credits/month preview |
| cloudflare | 10k neurons/day total |

### Adapters (opt-in via install.sh flags)

- `--with-hermes` — Nous hermes-agent CLI + gateway + free-provider scout cron
- `--with-telegram` — Telegram bot pairing
- `--with-firecrawl` — Local web_extract backend
- `--with-camofox` — Anti-detect Firefox
- `--with-searxng` — Self-hosted meta-search

### Tooling

- `pool_weights.yaml` — single source of truth for routing topology
- `apply_pool_weights.py` — push yaml → bifrost (creates missing rules)
- `op-log` helper — deterministic JSONL audit append (positional + stdin modes)
- `bifrost_tune_timeouts.py` — per-provider concurrency/timeout config
- `bifrost/snapshot.py` — captures current bifrost state with secret redaction
