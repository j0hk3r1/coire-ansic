# CoireAnsic — strip to an agnostic free-LLM router + harness adapters

**Date:** 2026-05-29
**Status:** design approved (pending spec review)
**Author:** brainstormed with Crux

## 1. Problem & vision

CoireAnsic today is framed and wired as an **opencode+omo appliance**: the README,
installer, pool names, ops layer, and even the strip-shim's hardcoded constants all
assume opencode + oh-my-openagent is *the* harness. A multi-agent review (2026-05-29,
23 agents) confirmed this coupling plus a pile of dead references left by the recent
curator / circuit-breaker / model_capabilities removals.

**The vision** (user's words): make it so *anyone can connect whatever harness and use
free-tier LLMs for free*.

> Jimmy comes over, installs coire-ansic, installs Claude Code, runs the connect
> command, and starts using it.
> Jon installs coire, installs hermes + Claude Code, and starts using it.

So the product is: **a self-hosted, OpenAI-compatible free-tier LLM router you stand up
once, then point your own harness at.** The repo ships the router + a normalizer shim +
copy-paste connect guides — and **bundles no harness**. Onboarding (install → connect →
use) is the headline experience, not an afterthought.

## 2. Goals / non-goals

**Goals**
- Core = `bifrost` (gateway) + `strip-shim` (OpenAI-compat normalizer) + a
  **harness-neutral maintenance CLI**. Reusable, no omo/opencode branding anywhere in core.
- Neutral, capability-based pool tiers (`coire-main/fast/vision`).
- One copy-paste **connect guide per harness** in `docs/connect/`. **Guides only —
  the installer deploys ZERO harness config and installs no harness alongside core.**
- A clean fresh-install: `cp .env.example .env && ./install.sh` works from a clean
  clone with only provider keys, producing a running router + maintenance CLI.
- Maintenance is **standalone scripts**, run directly or via system cron — never via an
  AI harness. Maintaining the router must not require any harness installed.

**Non-goals (for now)**
- Any harness (or harness glue) installed alongside core. No `~/.config/opencode/` writes,
  no `oh-my-openagent.json`, no plugin. Harness support is guides only.
- **Codex** — deferred to a later phase (needs a Responses↔chat bridge; not plug-and-play).
- **omo** — deferred to a later phase (needs its own additional pools + tuning; revisit
  once the four base harnesses connect cleanly).
- **openclaw** — deferred (future stub).
- In-repo translation bridges (Codex Responses, Claude Anthropic-through-shim).
- Per-harness pools / load-spread overrides (YAGNI until concurrency is real).
- Two-repo split (single repo chosen).

## 3. Supported harnesses

**NOW (this round):** claude, opencode (vanilla — no omo), pi, hermes.
**LATER (own specs):** codex → omo → openclaw.

Verified against current official docs during the review (sources in §8):

| harness | phase | wire format | connects how | effort |
|---|---|---|---|---|
| **opencode** (vanilla) | NOW | OpenAI chat/completions | `@ai-sdk/openai-compatible`, `baseURL=…:4002/v1`, name models `coire-*` | ✅ snippet |
| **pi** (`earendil-works/pi`, ex `pi-mono`) | NOW | OpenAI chat/completions | `~/.pi/agent/models.json`, `api:"openai-completions"`, `baseUrl=…:4002/v1` | ✅ snippet |
| **hermes** (user's agent framework) | NOW | OpenAI chat/completions | point its base URL at `…:4002/v1` | ✅ snippet |
| **Claude Code** | NOW | Anthropic Messages | `ANTHROPIC_BASE_URL=…:4001/anthropic` + `ANTHROPIC_DEFAULT_*_MODEL="coire-*"`; bypasses shim | ⚠ env vars + tradeoff note |
| **Codex CLI** | LATER | OpenAI **Responses** only | needs a Responses↔chat bridge (LiteLLM / community) | ❌ later |
| **omo** (opencode plugin) | LATER | via opencode | needs extra pools + load-bearing `omo-*` names (aliases) | ⏳ later |
| **openclaw** | LATER | OpenAI chat/completions | (stub) | ⏳ later |

"pi" was confirmed = Mario Zechner's Pi coding agent (`earendil-works/pi`, formerly
`badlogic/pi-mono`) — corroborated by the shim's existing `Pi-mono / hermes-agent`
comments, its `developer→system` role coercion matching Pi's `supportsDeveloperRole:false`,
the `NOTICE` credit, and Pi being the stack behind the user's old OpenClaw.

## 4. Architecture — core + guides (single repo)

**The pivot (validated against Bifrost docs — see Appendix C):** Bifrost natively loads a
declarative **`config.json`** from its app-dir (`/app/data`) that holds providers + keys +
governance routing rules, with per-entity content-hash reconciliation on every restart
(true GitOps source-of-truth). This **replaces our four imperative scripts** (`seed.sh`,
`sync_key_models.py`, `apply_snapshot.py`, `snapshot.py`). The minimal core is:

```
MINIMAL CORE (= "bifrost + pools + env", start here):
  bifrost/config.json   declarative: providers (keys via env.), governance.routing_rules
                        ($schema-validated, git-tracked, NO secrets in the file)
  docker-compose.yml    bifrost service only (mounts ./bifrost/data; env_file .env)
  .env / .env.example   provider keys + BIFROST_PASS (admin); secrets live ONLY here
  scripts/ops/          harness-neutral maintenance CLI (run directly / via cron)
  install.sh            renders/validates config.json, brings up bifrost, smoke-tests

LAYER 2 (optional, added after core is solid):
  strip-shim/           OpenAI-compat normalizer — only for providers that need it
                        (Mistral tool-id, Kimi/Qwen tokens, param-rejection retries)
  dashboard/            --profile dashboard (observability; harness-neutral)
  scripts/runtime/      pool_weights.yaml + a GENERATOR that emits config.json's
                        governance.routing_rules (optional convenience over hand-editing)

GUIDES (copy-paste only, nothing deployed):
  docs/connect/{opencode,pi,hermes,claude-code}.md  + README.md (install→connect→use)
  (later: codex.md, omo.md, openclaw.md)
```

### `bifrost/config.json` shape (validated, Appendix C)
- `"$schema": "https://www.getbifrost.ai/schema"`, `"config_store": {"enabled": true}`
  (keeps the UI live + enables hash-reconciliation; DB still lives in the mounted volume).
- `providers.<name>.keys[]` = `{name, value: "env.GROQ_API_KEY", models: ["*"], weight}`.
  - **`value: "env.X"`** → secrets stay in `.env`, never in the committed file (today we
    POST raw key strings into bifrost's DB).
  - **`models: ["*"]`** → the empty `[]` we currently send is *deny-all*; that is the only
    reason `sync_key_models.py` exists. `["*"]` kills that whole script.
  - custom/OpenAI-compat providers (nvidia-nim, cloudflare, github-models, sambanova,
    opencode-zen, deepseek, zai) keep their `network_config` + `custom_provider_config`
    blocks inline (same fields `seed.sh` builds today).
  - add **`network_config.max_retries: 1–2`** + backoff per provider (currently `0` = off;
    this is the biggest free resilience win + unlocks native 429 key-rotation).
- `governance.routing_rules[]` = the pools (see §5), identical shape to today's
  `routing-rules.json`.

### Pools are Bifrost "dynamic aliases" (validated)
Bifrost has no native pool object; a pool = one governance routing rule whose
`cel_expression` matches the virtual name and whose weighted `targets[]` + ordered
`fallbacks[]` define the cascade. This is exactly idiomatic. `model in ["a","b"]` CEL
membership is supported (the omo-phase alias path). Drop the undocumented `chain_rule`
field from rule bodies.

### Maintenance CLI (`scripts/ops/`, deployed to `~/coire-tools/`)
Promoted from omo-skill wrappers to the canonical harness-neutral interface:
- keep: `coire-health`, `coire-monitor`, `coire-restart`, `coire-check-quotas`,
  `coire-cascade-show`
- generalize `coire-diagnose` → stuck/error patterns from **bifrost logs only**
- rename `coire-kill-opencode` → `coire-kill-harness` (configurable process pattern)
- **drop from core:** `.opencode/`, `oh-my-openagent.json`, `opencode.json.template`,
  `scripts/ops/deploy.sh`. omo material parked (git history) for the LATER omo spec.

### Unit boundaries
- **config.json** — the declarative source of truth; the only thing bifrost needs to come
  up correctly. Depends on `.env` (for `env.` key refs). Reviewable, diffable, no secrets.
- **bifrost** — the gateway. Consumes config.json + `.env`. Exposes `/v1` (OpenAI-compat)
  + `/anthropic` (Claude Code) directly to clients.
- **strip-shim (Layer 2)** — pure OpenAI-compat normalizer; optional. Knows no harness.
- **maintenance CLI** — bifrost API + docker only. No harness dependency.
- **guides** — pure docs; consume `/v1` or `/anthropic`. Not a code dependency of anything.

## 5. Pool model — capability tiers (NOW cut needs no aliases)

Three neutral tiers, 1:1 with today's cascades (renamed):

| tier | = today's pool | role |
|---|---|---|
| `coire-main` | omo-main | top reasoning + tool-calling workhorses |
| `coire-fast` | omo-utility | small / high-RPM utility (search, explore) |
| `coire-vision` | omo-gemini | multimodal / vision |

**NOW cut: no aliases needed.** All four supported harnesses can name `coire-*` directly
— including Claude Code via `ANTHROPIC_DEFAULT_OPUS_MODEL="coire-main"` /
`..._HAIKU_MODEL="coire-fast"`. So the initial routing-rules ship exactly three rules with
plain `cel_expression: model == "coire-X"`. Clean.

**Alias mechanism — reserved for the omo phase (LATER).** omo's variant matcher keys on
load-bearing fixed names (`omo-main`/`omo-gemini`/…). When omo lands, each tier rule's
`cel_expression` becomes `model in ["coire-main", "omo-main", …]` — one rule serving every
alias, no cascade duplication. `pool_weights.yaml` will grow an `aliases:` list then;
`apply_pool_weights.py` emits the `in [...]`. Not built now (YAGNI).

**Why not per-harness pools:** a pool is a *named cascade*, not a quota partition — every
pool draws from the same provider RPM/TPM. Per-harness pools give zero isolation by
themselves. Tiers stay shared; only names differ.

**Load-spread is NATIVE (validated, Appendix C).** Bifrost does weighted load-balancing
across a rule's `targets[]`. Today every pool has ONE target @ weight 1.0 and spreads
purely via sequential `fallbacks` (deterministic best-first). To conserve free-tier quota,
promote the top 2–3 healthy providers into weighted `targets` (e.g. 0.4/0.3/0.3) so bifrost
spreads load probabilistically. No per-harness pools, no custom load-balancer needed.

**Tool-pool inclusion rules (validated matrix, Appendix D).** `coire-main` (the tool-using
pool) must contain only native-OR-shim-rescued tool-callers:
- **hard-exclude** (text-only / broken tools): `sambanova/DeepSeek-V3.1`,
  `nvidia-nim/llama-3.3-nemotron-super-49b`, `opencode-zen/qwen3.6-plus`,
  `cerebras/llama3.1-8b`.
- **fallback-only** (rescued but flaky — reasoning-only freeze risk): `cloudflare/kimi-k2.6`
  and `nvidia-nim/.../kimi-k2.6` (prefer NIM for tool turns; shim lifts its hex-id format),
  `nvidia-nim/z-ai/glm-5.1` (slow ~60s, last resort).
- **same model ≠ interchangeable:** only kimi-k2.6, glm-5.1, deepseek-v4-flash are truly
  the same weights across providers; `cerebras/zai-glm-4.7` (355B) ≠ `zai/glm-4.7-flash`
  (30B), `cerebras/qwen-3-235b` ≠ `openrouter/qwen3-next-80b` (different architectures).

**Highest-value shim fix (Layer 2, Appendix D):** make `reasoning_effort` handling
**value-aware** — keep `high`/`none`, drop only `low`/`medium`/`minimal` — and add
`cerebras/zai-glm-4.7` (the current `coire-main` PRIMARY) to the rejecter set: it accepts
`reasoning_effort` only as `none` and today has zero recovery path. Also translate
`reasoning_effort → thinking:{type}` for native z.ai targets.

**Ranking data = LMArena, not AA (validated, Appendix E).** Order pool targets by LMArena
Elo pulled from the HF dataset `lmarena-ai/leaderboard-dataset` (live, no auth; `text`
config, `latest` split, `overall`+`multi_turn` categories). A `scripts/runtime/scout_lmarena.py`
fetches the parquet, maps arena names → our `provider/model` IDs (basename auto-match +
small hand alias table), and writes scores for pool **ordering only** — Elo never overrides
the verified broken-tools/quota/latency verdicts (it is model-level, blind to per-provider
quirks). Found bug to fix: `zai/glm-4.7-flash` is tagged ~1430 MT in `pool_weights.yaml`;
arena actual ~1345.

## 6. Bug-fix sweep (Phase 1 — after the Phase 0 core lands)

All confirmed by the review (file:line). Scope = fresh-install blockers + dead-ref
removal + the broken test. Full doc *rewrites* happen later in their own phases.

> **Subsumed by Phase 0:** the config.json migration deletes `seed.sh`,
> `sync_key_models.py`, `apply_snapshot.py`, `snapshot.py` — so the `seed.sh:2` header and
> `snapshot.py` `PROVIDER_TO_ENV` items below are moot once Phase 0 lands (env-var labelling
> becomes `value:"env.OPENCODE_ZEN_API_KEY"` in config.json directly). The DeepSeek doc-gap
> + dashboard/compose/ops items still stand.

- **docker-compose.yml** — remove dead dashboard mounts (`excluded_models.json`,
  `candidate_providers.json`, `scripts/runtime/model_capabilities.yaml`,
  `~/.coire/curator-pool` rw) at lines 127–134; rewrite the stale header (lines 1–14:
  Hermes-on-host, circuit-breaker.service, systemd); guard/​drop the `jkr`-specific
  crontab mount (line 130).
- **install.sh** — `mkdir -p ~/.coire` as the invoking user *before* `docker compose up`
  (fixes root-owned `~/.coire` → `models.json` EACCES, lines 124/143); add
  `OPENCODE_ZEN_API_KEY` to the provider-count loop (lines 73–79).
- **scripts/ops/coire-restart:42** — target compose **service** names, not container name
  `coire-dashboard`.
- **dashboard/tests/test_stream_state.py:8-9,19** — remove/rewrite monkeypatches of
  deleted functions (suite currently `AttributeError`s on run).
- **dashboard/static/dashboard.js:46,459** — drop dead curator state read/write.
- **dashboard/templates/dashboard.html:208-272** — remove the `x-show="false"` CB block;
  fix stale "circuit breaker"/"curator" branding (lines 29, 263, 656).
- **dashboard/app.py:911** — drop the comment citing removed `model_capabilities.yaml`.
- **bifrost/seed.sh:2** — header now "~13 providers, 0 rules (rules applied separately)".
- **bifrost/snapshot.py:58-79** — add `opencode-zen → OPENCODE_ZEN_API_KEY` to
  `PROVIDER_TO_ENV`; re-run snapshot.py; recommit `providers.json`.
- **DeepSeek decision** — `install.sh`/`seed.sh`/snapshot wire deepseek but README +
  `.env.example` omit it. Resolve by documenting it in both (keep the provider).

## 7. Sequencing (start slow — each step = one local commit; no push until the reconcile)

**Phase 0 — minimal core = bifrost + pools + env (DO THIS FIRST, prove it, stop).**
0a. Author `bifrost/config.json` from current live state: 13 providers with
    `value:"env.X"` + `models:["*"]` + custom blocks; `network_config.max_retries:1–2`;
    `governance.routing_rules` = the 3 pools renamed `coire-main/fast/vision` (plain
    `model=="coire-X"`, weighted targets summing to 1, ordered fallbacks). Validate against
    `$schema`.
0b. `docker-compose.yml`: bifrost service only for the core; mount config.json into the
    app-dir; `env_file: .env`; bind **loopback** (`127.0.0.1:4001:8080`) OR enable
    `client.enforce_auth_on_inference` — fix the LAN-open-unauthenticated exposure.
0c. Resolve `BIFROST_API_KEY`: either drop it (loopback, inference open, `BIFROST_PASS`
    guards only the admin API) or mint a real virtual key (`sk-bf-*`) + enforce. Update
    `.env.example` + `install.sh` gate to match reality.
0d. `install.sh` (core path): validate `.env` → `docker compose up bifrost` → smoke-test
    `/v1/chat/completions` against each pool. Delete `seed.sh`, `sync_key_models.py`,
    `apply_snapshot.py`, `snapshot.py` once config.json reproduces them.
0e. Fresh-install test on `.93` (wipe + reinstall from `.env`): bifrost comes up from
    config.json alone, all 3 pools route. **Checkpoint — stop, review, decide to continue.**

**Phase 1 — bug-fix sweep** (§6): the dead-ref + fresh-install fixes that aren't already
subsumed by Phase 0 (dashboard test, dead curator JS, docs drift, etc.).

**Phase 2 — Layer 2 (optional services).** strip-shim as an opt-in profile (value-aware
`reasoning_effort`, the de-omo cleanup, pinned Dockerfile, honor `PORT`); dashboard profile
fixes; `scripts/runtime` generator + `scout_lmarena.py`.

**Phase 3 — connect guides ×4** — `docs/connect/{opencode,pi,hermes,claude-code}.md` (+
index): verified copy-paste config + the "install router → install your harness → connect →
use free" quickstart. opencode guide = **vanilla opencode**, no omo.

**Phase 4 — README + docs rewrite** — core-first, Jimmy/Jon onboarding; mark
codex/omo/openclaw "coming"; rewrite/retire CONTRIBUTING/CHANGELOG/NOTICE/`docs/omo-*`.

**Phase 5 — reconcile `.93` + push** — full wipe+reinstall; validate all four NOW harnesses
connect; **then** user reviews and pushes.

**Later phases (separate spec each):** Codex (Responses bridge) → omo (extra pools + `omo-*`
aliases via §5 + parked ops/skills) → openclaw.

**Later phases (separate spec each):** Codex (Responses bridge) → omo (extra pools +
`omo-*` aliases via the §5 mechanism + the parked ops/skills) → openclaw.

## 8. Sources (harness connect facts)

- opencode: https://opencode.ai/docs/providers/ , https://ai-sdk.dev/providers/openai-compatible-providers
- pi: https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md , https://pi.dev/docs/latest/custom-provider
- Claude Code: https://code.claude.com/docs/en/llm-gateway , https://code.claude.com/docs/en/model-config , https://docs.getbifrost.ai/cli-agents/claude-code
- Codex (Responses-only): https://github.com/openai/codex/blob/main/codex-rs/model-provider-info/src/lib.rs , https://github.com/openai/codex/discussions/7782
- bifrost drop-in routes (`/openai`, `/anthropic`, `/genai`): https://github.com/maximhq/bifrost

## 9. Risks / open items

- **Claude Code bypasses the shim** (goes to bifrost `/anthropic`). Acceptable: most shim
  fixes are OpenAI-format quirks. If Anthropic-path provider bugs appear, revisit
  proxying `/anthropic` through the shim. Must verify bifrost `/anthropic` actually serves
  the free providers in the NOW reconcile test (step 7).
- **Codex deferred, not solved** — Responses-only. The "just connect and use" vision is
  honest only for the 3 OpenAI-compat harnesses + Claude; Codex gets a `/v1/responses`
  bridge in its own later phase.
- **omo deferred** — its load-bearing `omo-*` names + extra pools come back in the omo
  phase via the §5 alias mechanism. Parked omo material is recoverable from git history.
- **.93 is divergent** (old base + uncommitted hand-edits). Reconcile only via fresh
  wipe+reinstall (step 7), never `git pull` over that tree.
- **Maintenance CLI generalization** — `coire-diagnose` currently reads opencode logs;
  rewriting it to bifrost-logs-only may lose some signal. Verify it still catches the
  failure modes it was built for (orphan streams, error loops) against bifrost data alone.
- **No native circuit-breaking (validated, Appendix C).** Bifrost OSS does NOT health-demote
  dead providers — that is Enterprise "Adaptive LB". A dead primary is re-probed every
  request (stateless cascade). The deleted CB daemon filled a real gap; the dashboard
  comment claiming "bifrost's built-in cascade handles it" is wrong and must be corrected.
  Acceptable for free-tier IF per-provider timeouts are tight (fast-fail through the
  cascade). Whether to re-add lightweight demotion is a later call.
- **Inference is open by default (validated).** `BIFROST_API_KEY` is not actually enforced
  unless `client.enforce_auth_on_inference=true` + a virtual key. `.93` currently binds
  `0.0.0.0:4001` with open inference = LAN-open unauthenticated gateway. Phase 0 fixes this
  (loopback or enforce-auth) — must not regress.
- **No automated broken-tools regression guard (Appendix D).** Text-only models are caught
  only by manual `probe.py`. Provider model sets drift; a benched model may recover or a
  good one may break silently. A periodic probe-sweep is worth considering (later).
- **Several quota numbers are stale/conflicting (Appendix D gaps).** repo vs public-doc RPM/
  RPD/TPM disagree for mistral, github-models, cerebras. Re-read live `x-ratelimit-*` headers
  during Phase 0/5 rather than trusting comments.
- **LMArena name-matching is fuzzy.** 13/21 pool models auto-match arena names; the rest need
  a hand alias table (date↔version, `:free`, vendor-token, vision-vs-text). Scout must not
  silently mis-map; unmatched → flagged, not guessed.

## Appendix A — full review backlog (every finding, all subsystems)

Captured so nothing is lost. Severity as adversarially re-rated where the verifier adjusted it. Items already in the §6 sweep are marked [SWEEP]; the rest are tracked for their relevant strip step or as backlog.


### bifrost/
_CORE — the seeding/snapshot scripts are the reusable provider-config layer for bifrost; routing-rules.json must be genericized (omo-* pool names → neutral names) and providers.json kept as version-control artifact._

- **[INFO/security] No committed secrets in bifrost/ tracked files — redaction works**  
  `bifrost/snapshot/providers.json, bifrost/snapshot/routing-rules.json, bifrost/seed.sh` providers.json:1-739  
  Broad scans for sk-/AIza/gsk_/csk-/ghp_/Bearer <token>/32-hex patterns and
  api_key/token/password literals across all git-tracked bifrost files returned zero hits. All
  auth in providers.json is ${ENV_VAR} placeholders; account IDs are ${CLOUDFLARE_ACCOUNT_ID};
  raw key values come only from .env at runtime. snapshot.py's _sanitize_provider drops
  keys/config_hash/status, rewrites Authorization headers to Bearer ${ENV}, and sanitizes
  Cloudflare 32-hex account IDs in URLs. This is the security-critical check for this subsystem
  and it passes.
  → _fix:_ No action. Keep CONTRIBUTING.md's rule to always run snapshot.py before committing providers.json.
- **[HIGH/coupling] Committed routing-rules.json is fully omo-coupled (pool names + cel_expressions)**  
  `bifrost/snapshot/routing-rules.json` 4-82  
  All three seeded rules are named omo-gemini / omo-utility / omo-main with cel_expression
  'model == "omo-X"' and descriptions 'Pool ... from pool_weights.yaml'. These names are load-
  bearing for the omo plugin's variant matcher (per MEMORY: omo pool names drive prompt-variant
  selection), so the committed router config only routes for omo. A harness-agnostic core that
  ships this file forces every downstream user onto omo-specific virtual model names.
  install.sh applies this file unconditionally (install.sh:139-140), so a fresh non-omo install
  gets a router whose only routable models are omo-* aliases.
  → _fix:_ For the agnostic core, ship a generic routing-rules.json with neutral pool names (e.g. coire-main / coire-fast / coire-vision) and a generic cel_expression. Keep the omo-* version as an opt-in adapter overlay (adapters/omo/routing-rules.json) layered only when the omo profile is selected. Document that pool names are user-chooseable virtual models.
- **[MEDIUM/bug] [SWEEP] snapshot.py PROVIDER_TO_ENV missing opencode-zen and zai → wrong/placeholder redaction labels**  
  `bifrost/snapshot.py` 58-70, 79  
  PROVIDER_TO_ENV maps 11 providers but omits opencode-zen and zai, even though seed.sh creates
  both (seed.sh:195-234). The fallback env_name = 'PROVIDER_API_KEY' (snapshot.py:79) is why
  the committed snapshot/providers.json:465 shows opencode-zen's Authorization as 'Bearer
  ${PROVIDER_API_KEY}' instead of '${OPENCODE_ZEN_API_KEY}'. This is not a secret leak (it's
  still a placeholder) but it produces a misleading/non-restorable snapshot: anyone reading
  providers.json to learn the wiring sees the wrong env var, and any future apply tooling keyed
  off these placeholders would inject the wrong variable. zai is keyless in bifrost (auth
  injected by the shim) so its omission is harmless, but opencode-zen's is a real mislabel.
  → _fix:_ Add 'opencode-zen': 'OPENCODE_ZEN_API_KEY' (and optionally 'zai' even though it has no header) to PROVIDER_TO_ENV, then re-run snapshot.py and recommit providers.json so opencode-zen shows ${OPENCODE_ZEN_API_KEY}.
- **[HIGH/fresh-install] docker-compose dashboard mounts 3 deleted bifrost/scripts files (fresh-install break)**  
  `docker-compose.yml` 131-134  
  Confirmed scope within this subsystem: the dashboard service bind-mounts
  ./bifrost/excluded_models.json (line 131), ./bifrost/candidate_providers.json (line 132), and
  ./scripts/runtime/model_capabilities.yaml (line 134) — all three are NOT git-tracked (deleted
  in the recent curator/model_capabilities cleanup). Docker bind-mounting a non-existent host
  path creates a root-owned directory in their place (or errors), so a fresh 'docker compose up
  dashboard' from a clean clone breaks or silently creates junk dirs inside the repo. The
  ~/.coire/curator-pool rw mount (line 129) is the same dead-curator class. README.md:221 also
  still lists candidate_providers.json in the tree.
  → _fix:_ Drop the three dead mounts (lines 131-132, 134) and the curator-pool rw mount (line 129) from the dashboard service; fix the model_capabilities.yaml reference (caps now inline per recent commit 28fc7c4). Remove candidate_providers.json from README.md:221 tree.
- **[LOW/doc-drift] [SWEEP] seed.sh header comment is stale (claims 7 providers + 4 rules; reality 13 providers, 0 rules)**  
  `bifrost/seed.sh` 2  
  Header says 'Seed Bifrost with: 7 providers + 4 routing rules.' but the script now POSTs 13
  providers (groq, gemini, mistral, cerebras, openrouter, nvidia-nim, cloudflare, github-
  models, cohere, sambanova, opencode-zen, zai, deepseek) and creates ZERO routing rules —
  rules were moved out to apply_snapshot.py + apply_pool_weights.py (correctly noted at
  seed.sh:260-264). The header contradicts the footer. Doc drift only, but misleads anyone
  auditing the seed surface.
  → _fix:_ Update line 2 to '~13 free-tier providers (routing rules applied separately via apply_snapshot.py + apply_pool_weights.py).'
- **[INFO/strategy] snapshot/providers.json is write-only — no apply path consumes it**  
  `bifrost/snapshot/providers.json` 1-739  
  snapshot.py writes providers.json but no script reads it to apply: apply_snapshot.py only
  applies routing-rules.json and explicitly skips providers ('those carry secrets',
  apply_snapshot.py:3). On fresh install, providers come solely from seed.sh + .env. So
  providers.json is purely a redacted version-control/documentation artifact of deployed
  provider wiring — which is its stated purpose. This is correct by design, not a bug, but
  worth flagging: the file can silently drift from what seed.sh actually creates (it already
  has: it shows opencode-zen mislabeled and reflects whatever was live at capture time,
  including timeout/concurrency values seed.sh never sets). Reviewers should not treat
  providers.json as the source of truth for seeding — seed.sh is.
  → _fix:_ Keep as-is but add a one-line header note in providers.json's purpose (or README) that it is a redacted capture for audit only and is never replayed; seed.sh + .env are authoritative for provisioning.
- **[INFO/quality] seed.sh uses unauthenticated GETs for idempotency checks — correct given bifrost config, but fragile**  
  `bifrost/seed.sh` 21, 26, 31, 68  
  provider_exists, provider_has_key, the wait loop, and post_rule's existence check use 'curl
  -sf' WITHOUT -u admin:$BIFROST_PASS, while creates/key-POSTs use -u (lines 44, 56). This
  works today because bifrost's read endpoints are unauthenticated (the compose healthcheck
  wget's /api/providers with no auth, docker-compose.yml:30). If a future bifrost version or
  config requires auth on GET /api/providers, every provider_exists check would silently return
  'False', causing seed.sh to attempt re-creates each run (create is guarded by a 200 check so
  it degrades to noisy warnings rather than data corruption, but idempotency would break). Low
  risk now; note for portability of the agnostic core.
  → _fix:_ Add -u "admin:${BIFROST_PASS:-}" to the GET helpers (lines 26, 31, 68) for forward-compat, or document that bifrost read endpoints must remain unauthenticated for seeding.
- **[INFO/fresh-install] Fresh-install seeding chain is sound from .env alone (positive finding)**  
  `bifrost/seed.sh, bifrost/sync_key_models.py, bifrost/apply_snapshot.py` install.sh:131-144  
  Traced the install ordering: BIFROST_PASS auto-generated before seed (install.sh:62-70);
  seed.sh sources ../.env and only POSTs providers whose key var is non-empty (skips absent
  providers cleanly); apply_snapshot.py.filter_rule_targets drops targets/fallbacks for
  unconfigured providers and re-normalizes weights to sum 1.0, so a partial-key install (e.g.
  only GROQ) doesn't 400 the whole rule apply; sync_key_models.py pulls LIVE rules (with
  snapshot fallback when live has 0 rules — the fresh-install path) and backfills each key's
  models list, which fixes bifrost's 'no keys found that support model' rejection. The chain
  genuinely produces a working router from cp .env.example .env + edit keys + ./install.sh. The
  only fresh-install breakage in scope is the docker-compose dead-mount finding above
  (dashboard only — core bifrost/shim unaffected).
  → _fix:_ No action on the seeding logic. Just fix the dashboard mounts so the full 'docker compose up' doesn't break alongside the working core.
- **[LOW/strategy] routing-rules.json fallbacks reference models not guaranteed at fresh install (informational)**  
  `bifrost/snapshot/routing-rules.json` 15-21, 39-46, 64-77  
  Fallback chains list specific models (gemini-3-flash-preview, cerebras/zai-glm-4.7,
  cloudflare/@cf/moonshotai/kimi-k2.6, openrouter/...:free, etc.) that are a frozen capture of
  the user's tuned omo deployment as of 2026-05. sync_key_models.py backfills whatever models
  the live/snapshot rules reference into each provider key, so unknown/renamed free-tier models
  will be allowed but fail at request time if the provider no longer serves them.
  apply_snapshot.py drops fallbacks for unconfigured providers but does NOT validate model
  existence. This is expected for a free-tier router (models churn), but combined with the
  omo-* naming it reinforces that the committed file is a personal snapshot, not a portable
  default.
  → _fix:_ When genericizing for the agnostic core, replace the model-specific frozen fallbacks with a small, conservative, current-as-of-ship set under neutral pool names, and document that users re-tune via snapshot.py after their own runs.

### strip-shim (OpenAI-compat normalizer proxy)
_CORE — this is the reusable custom normalizer the strategy is built around; keep it, but de-omo the hardcoded pool names into env/config so it ships harness-agnostic._

- **[MEDIUM/coupling] Hardcoded omo pool names break harness-agnostic goal — should be config-driven**  
  `strip-shim/app.py` 65, 145-153  
  _FALLBACK_POOLS = ["omo-main", "omo-utility", "omo-gemini"] (L65) and _POOL_OUTPUT_CAP keyed
  on the same three omo pool names (L145-153) bake the user's specific omo harness pool
  taxonomy into the supposedly-reusable core. For the stated strategy (ship bifrost+shim
  harness-agnostic so anyone connects Claude Code / Codex / opencode / pi), a fresh adopter
  using different pool names (e.g. 'best'/'fast'/'code') gets: (a) a /v1/models fallback list
  advertising pools that don't exist in THEIR bifrost config, and (b) no per-pool output caps
  so everything silently falls to DEFAULT_OUTPUT_CAP=16384, strangling pools whose providers
  support more. The memory note 'omo pool names load-bearing' confirms these substrings drive
  behavior elsewhere too.
  → _fix:_ Drive both from env/JSON: read fallback pool list from an env var (e.g. STRIP_SHIM_FALLBACK_POOLS, comma-separated) and load per-pool output caps from a small JSON/YAML config file (or env-encoded map) mounted alongside models.json. Default to an EMPTY pool-cap map (so DEFAULT_OUTPUT_CAP applies uniformly) and a generic single-entry fallback when unset. Move the omo-specific values into the user's own config, not the shipped image. This is the single biggest blocker to genericizing the core.
- **[LOW/dead-code] _POOL_OUTPUT_CAP is effectively dead/redundant — all three pools share one value**  
  `strip-shim/app.py` 145-176  
  All three entries in _POOL_OUTPUT_CAP map to the identical 65536 cap, so the dict provides no
  per-pool differentiation — it's equivalent to a single constant 'if model is one of these 3
  omo pools, cap 65536, else 16384'. The elaborate per-pool comment block (L140-153) describing
  Sisyphus/Junior 64k vs Hephaestus 32k differentiation describes behavior the code does not
  actually implement (Hephaestus 32k is never enforced). clamp_max_tokens at 65536 is also a
  near-no-op since no free provider here exceeds that. Dead-ish complexity that misleads a
  reader into thinking pools are differentiated.
  → _fix:_ Either collapse to a single STRIP_SHIM_POOL_MAX_OUTPUT_CAP env constant, or actually populate distinct per-pool values if differentiation is intended. Trim the comment to match reality. Folds naturally into the config-driven fix above.
- **[LOW/dead-code] sanitize_reasoning_effort() is a permanent no-op — _POOL_DROPS_RE is empty**  
  `strip-shim/app.py` 158-195  
  _POOL_DROPS_RE is initialized to an empty set() (L162) with a comment stating 'No pool-level
  drops currently needed' (L161). sanitize_reasoning_effort (L179-195) gates its only action on
  `pool_or_alias in _POOL_DROPS_RE`, which can never be true, so the entire function — and its
  call at L770 — does nothing. The actual reasoning_effort handling lives in
  pre_strip_unsupported_params + _detect_param_rejection. This is dead code carrying ~18 lines
  plus a call site that runs on every request.
  → _fix:_ Remove sanitize_reasoning_effort + _POOL_DROPS_RE + the L770 call, OR if pool-level drops may return, populate it from config and document that it's currently disabled. As-is it's pure noise that a fresh reader must reverse-engineer to discover is inert.
- **[MEDIUM/quality] Dockerfile pins no dependency versions — non-reproducible builds**  
  `strip-shim/Dockerfile` 3  
  `pip install --no-cache-dir fastapi uvicorn[standard] httpx` installs latest at build time. A
  fresh-install-from-scratch on .93 (or any future rebuild) silently pulls whatever the newest
  fastapi/uvicorn/httpx are, which can introduce breaking changes (e.g. httpx has changed
  Timeout/stream semantics across minors; fastapi/starlette response signatures shift). For a
  project whose explicit test plan is 'full reinstall from scratch to surface installer gaps',
  an unpinned image is a latent reproducibility gap that won't show up until a dependency
  releases a breaking change.
  → _fix:_ Pin versions, e.g. `pip install --no-cache-dir 'fastapi==0.115.*' 'uvicorn[standard]==0.34.*' 'httpx==0.28.*'` or add a requirements.txt with hashes and COPY it. Pin python:3.13-slim to a digest or at least a patch tag while here.
- **[LOW/quality] Dockerfile hardcodes port 4002, ignoring the PORT env var the app reads**  
  `strip-shim/Dockerfile` 5  
  app.py reads PORT from env (L24) but never uses it — uvicorn is launched in the Dockerfile
  CMD with a hardcoded `--port 4002` (L5). So the PORT variable is dead, and anyone trying to
  relocate the port via env (a reasonable expectation for a reusable core) silently gets 4002
  regardless. Minor, but it's a latent confusion: the code advertises configurability it
  doesn't honor.
  → _fix:_ Either drop the unused PORT var from app.py, or change CMD to a shell form honoring it: `CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-4002}`. Pick one so code and container agree.
- **[LOW/bug] Z.ai streaming proxy has no upstream error handling — auth/5xx surface as broken SSE**  
  `strip-shim/app.py` 737-745  
  In zai_proxy the streaming branch opens client.stream() and yields raw chunks inside a
  StreamingResponse with media_type text/event-stream and an implicit 200, regardless of the
  upstream status. If Z.ai returns 401/429/5xx on a streaming request, the client receives HTTP
  200 + a body that is the error JSON (not valid SSE), which most OpenAI clients mis-parse
  rather than treating as an error. The non-streaming branch (L746-751) correctly propagates
  status_code; the streaming branch does not. Also any httpx exception inside gen() propagates
  after headers are sent, truncating the stream with no diagnostic.
  → _fix:_ Before streaming, peek upstream status (httpx stream context exposes r.status_code before iterating) and if not 200, buffer + return a proper JSONResponse with the real status. Wrap the aiter loop in try/except logging the exception. Mirror the non-streaming branch's status fidelity.
- **[INFO/strategy] Reasoning-only retry forces ALL tool-bearing streaming requests through buffered non-streaming, adding latency**  
  `strip-shim/app.py` 826-994  
  retry_eligible is true for EVERY chat/completions request that carries tools[] (L826-831)
  when STRIP_SHIM_RETRY_REASONING_ONLY=1 (default on). That means every streaming agent request
  with tools is silently converted to non-streaming upstream, fully buffered, then re-emitted
  as a single-shot SSE (_payload_to_sse_chunks). This defeats token-by-token streaming for the
  entire agent workload — the user loses incremental output and time-to-first-token on all tool
  calls, not just Kimi. For an opencode/omo workload that's an accepted tradeoff (and
  documented in the kimi-freeze memory), but for a harness-agnostic core where most adopters
  won't hit the Kimi freeze, this is a heavy default. Note _payload_to_sse_chunks also drops
  fields like 'usage' and any choices beyond index 0.
  → _fix:_ For the agnostic ship, consider gating this to only pools/models known to freeze (config-driven list) rather than all-tools-on. At minimum document the latency/streaming-fidelity tradeoff prominently so adopters can set STRIP_SHIM_RETRY_REASONING_ONLY=0. Optionally preserve usage in the SSE conversion.
- **[LOW/quality] Retry paths swallow JSON-decode failures, can leave stale payload/status mismatch**  
  `strip-shim/app.py` 900-944, 976-982  
  In the param-rejection and nudge retries, when the retry succeeds (200) but
  json.loads/normalize_response raises, the except branch `pass`es leaving `payload` set to the
  EARLIER (pre-retry) parsed body while `r` may or may not be advanced — the final Response
  then serializes the old payload (out_content from payload at L984-985) even though r was
  potentially reassigned. The combinations are subtle: e.g. nudge retry 200-but-unparseable
  leaves r=original-200 + payload=original (fine), but param-rejection retry has an else branch
  (L942-944) reassigning r=r3 on non-200 while payload stays original, so a 200 out_content
  body could ship with a non-200 status from a different response. Low likelihood (requires a
  200 with unparseable JSON) but the payload/r coupling is fragile.
  → _fix:_ On any retry, treat (status, body, payload) as one atomic unit: only adopt the retry result if it both is 200 AND parses; otherwise keep the original r AND original out_content together. Recompute out_content from r.content when payload is None rather than relying on the earlier payload. Add a debug log in the except branches instead of bare pass.
- **[INFO/security] No secrets in tracked shim files; ZAI_API_KEY handled cleanly**  
  `strip-shim/app.py` 31, 715-720  
  Confirmed clean: app.py and Dockerfile contain no hardcoded keys/tokens. ZAI_API_KEY is read
  from env (L31), defaults to empty string, and a missing key produces a clean 500
  configuration_error rather than crashing or forwarding unauthenticated (L715-720). docker-
  compose passes it via ${ZAI_API_KEY:-} and .env.example has `ZAI_API_KEY=` as an empty
  placeholder. Auth header is correctly stripped from inbound and re-injected outbound in
  zai_proxy. No leakage.
  → _fix:_ No action. Noting as positive confirmation for the security sweep.
- **[INFO/fresh-install] Fresh-install viability of the shim is good (graceful fallbacks)**  
  `strip-shim/app.py` 37, 68-102  
  Positive: MODELS_JSON_PATH missing/unreadable is handled (_load_models_doc returns None on
  FileNotFoundError/JSONDecodeError/OSError, /v1/models falls back to static pools). The
  ~/.coire:ro mount tolerates a non-existent models.json. So a clean `docker compose up strip-
  shim` works without the host generator having run. The only fresh-install caveat is the omo-
  named fallback pools (see coupling finding) which would advertise wrong names but still
  RESPOND. Unlike the dashboard service, the shim does NOT bind-mount any of the deleted files
  (excluded_models.json, candidate_providers.json, model_capabilities.yaml, curator-pool), so
  the confirmed dashboard mount bug does not extend into this subsystem.
  → _fix:_ No fix needed for install viability. When genericizing fallback pools, keep the graceful-degradation property.
- **[LOW/quality] Kimi NEW-format span dedup compares spans across two different regexes — can double-emit or miss**  
  `strip-shim/app.py` 371-410  
  normalize_kimi_tool_calls records OLD-format match spans in seen_spans (L380) then in pass 2
  skips NEW matches whose span exactly equals an OLD span (L388). But _KIMI_TC_OLD and
  _KIMI_TC_NEW have different group boundaries (OLD anchors on 'functions.NAME:IDX', NEW on a
  bare id token), so for the same physical tool_call the two regexes produce DIFFERENT spans —
  the exact-equality check at L388 will essentially never match, and the real guard against
  double-emission is instead the `if token_id.startswith("functions")` heuristic at L391-392.
  That heuristic works for the documented format but is fragile (any non-'functions'-prefixed
  token already captured by OLD via a different surrounding context could re-emit). In practice
  OLD and NEW are mutually exclusive per-host so collisions are unlikely, but the seen_spans
  mechanism gives a false sense of safety.
  → _fix:_ Replace span-equality dedup with character-offset interval overlap (track (start,end) ranges and skip NEW matches whose range intersects any consumed OLD range), or remove seen_spans and rely solely on the startswith('functions') exclusion with a comment that OLD/NEW are host-exclusive. Add a unit test with a mixed-format blob to lock the behavior.

### dashboard/
_DASHBOARD/OPTIONAL — genuinely optional (behind compose `--profile dashboard`), harness-agnostic, reusable for any Bifrost deploy; keep as the optional observability layer, but it needs the dead-mount/dead-ref cleanup before shipping in a minimal core._

- **[CRITICAL/fresh-install] [SWEEP] docker-compose dashboard service bind-mounts deleted files — fresh `compose up dashboard` is broken**  
  `docker-compose.yml` 131-134, 125-129  
  The dashboard service mounts four sources that no longer exist in the repo:
  `./bifrost/excluded_models.json` (131), `./bifrost/candidate_providers.json` (132), and
  `./scripts/runtime/model_capabilities.yaml` (134) were all deleted in commits
  101245d/de89ce6/28fc7c4. I verified with `git ls-files` and filesystem checks: only
  `scripts/runtime/pool_weights.yaml` (133) still exists. On a fresh clone, `docker compose
  --profile dashboard up` makes Docker auto-create EMPTY DIRECTORIES at each missing host path
  and bind-mount a directory over the expected file location inside the container. Worse, none
  of these four are even READ by app.py anymore: model_capabilities.yaml caps are now inlined
  in `_load_model_capabilities()` (app.py:796-833, mounted to /app/model_capabilities.yaml
  which nothing opens), and excluded/candidate loaders were deleted with the curator tab. The
  `~/.coire/curator-pool:rw` mount (129) and its comment about prune/restore writing
  `circuit_state.json` is also dead: app.py has zero references to `.coire/curator-pool` or
  `circuit_state.json` (only `/root/.coire/pool_weights.yaml` at app.py:195 is a real fallback
  path). Net effect: the dashboard does not start cleanly from a clean clone and litters the
  host with empty dirs.
  → _fix:_ Delete mount lines 131, 132, and 134 entirely. Keep only line 133 (`./scripts/runtime/pool_weights.yaml`). Drop the `~/.coire/curator-pool:rw` mount (129) and its comment (126-128); the parent `~/.coire:ro` mount (125) suffices for the pool_weights.yaml fallback. Also fix stale comments at lines 114-115 ('circuit_breaker daemon cooldown_status.json mounted via ~/.coire') and 6/13 ('circuit breaker UI', 'circuit-breaker.service — see systemd/circuit-breaker.service') since systemd/ was deleted in afe61b8.
- **[HIGH/bug] [SWEEP] test_stream_state.py monkeypatches functions that were removed from app.py — will AttributeError on run**  
  `dashboard/tests/test_stream_state.py` 8-9, 19  
  The test calls `monkeypatch.setattr(app, 'load_curator_recommendations', ...)` and
  `monkeypatch.setattr(app, 'load_curator_history', ...)`. Both functions were removed in the
  curator cleanup (app.py:249-251 only has a comment noting their removal; grep confirms zero
  definitions). pytest's monkeypatch.setattr defaults to raising=True, so it raises
  `AttributeError: <module 'app'> has no attribute 'load_curator_recommendations'` and the test
  errors out. It also asserts (line 19) that `curator_recommendations` and `curator_history`
  are keys in the /api/stream_state response, but load_stream_state (app.py:536-550) no longer
  emits them. This is the only test that exercises the HTTP route + cleanup, and it is
  guaranteed-broken. I could not run pytest in this sandbox (no network to install
  httpx/freezegun) but the breakage is provable statically: the two attributes do not exist on
  the module. The cleanup commit shipped with zero passing coverage of the thing it changed.
  → _fix:_ Remove lines 8-9 (the two curator monkeypatches) and remove 'curator_recommendations', 'curator_history' from the asserted key list on line 19. The remaining stubs (bifrost_get, load_circuit_breaker, get_cron_status) plus the real key set from load_stream_state are correct. httpx is already in requirements-dev.txt, implying tests aren't run in CI; wire `cd dashboard && pytest` into the install/CI path.
- **[MEDIUM/dead-code] [SWEEP] dashboard.js still reads/writes removed curator state (tab routing + ticker)**  
  `dashboard/static/dashboard.js` 46, 459  
  Line 46 includes `'curator'` in the list of valid tabs honored from the URL hash
  (`['overview','stream','pools','providers','curator','latency']`), but the curator tab was
  removed from the TAB NAV (dashboard.html:103 lists only
  overview/stream/pools/providers/latency) and no `x-show="activeTab === 'curator'"` section
  exists. Navigating to `#curator` sets activeTab to a tab that renders nothing — a blank
  dashboard. Line 459 in tickerItems() reads `s.curator_recommendations?.total_models` for the
  'aa models' ticker chip; stream_state never sends curator_recommendations now, so this chip
  is permanently 0/blank.
  → _fix:_ Remove 'curator' from the valid-hash list at line 46. Remove the `['aa models', s.curator_recommendations?.total_models || 0]` ticker item at line 459 (or repurpose it to a live value). cbBadge() (101-106) and restore/prune handlers (143-166) are also dead now that the CB section is x-show=false.
- **[LOW/dead-code] [SWEEP] Circuit-breaker UI block + handlers retained behind x-show=false (dead but shipping)**  
  `dashboard/templates/dashboard.html` 208-272  
  The entire Circuit Breaker section is wrapped in `x-show="false"` (211) with a comment that
  CB was removed. It still contains Jinja reading
  `circuit_breaker.error/.demoted_count/.demoted/.updated_at` (215-253) and Alpine handlers
  `restoreTarget`/`pruneTarget` (263-264) that POST to /api/circuit_breaker/restore|prune —
  which now return 410 Gone (app.py:959-964). load_circuit_breaker() (app.py:172-175) is a stub
  returning `{demoted:[],demoted_count:0,removed:True}`, so the block renders nothing, but ~65
  lines of dead template + cbBadge/restoreTarget/pruneTarget JS (dashboard.js:101-106,143-166)
  and the 'On Cooldown' top-stat card (dashboard.html:84-90) all persist. api_health_status
  (app.py:967-991) still computes demoted_count-based red/amber escalation that can never
  trigger. Pure dead weight that contradicts the 'strip to minimal core' goal.
  → _fix:_ For the minimal core: delete the CB section (208-272), the 'On Cooldown' stat card (84-90), cbBadge/restoreTarget/pruneTarget in dashboard.js, the /api/circuit_breaker* routes + load_circuit_breaker stub + the demoted_count branch in api_health_status. At minimum drop the stat card and ticker entry so the UI doesn't advertise a feature that's gone.
- **[LOW/doc-drift] [SWEEP] Stale 'circuit breaker' / 'curator' branding in header, boot animation, and tooltips**  
  `dashboard/templates/dashboard.html` 29, 656, 263  
  User-visible copy still advertises removed features: header subtitle reads 'Pool Monitor ·
  Circuit Breaker · Curator' (dashboard.html:29); the latency empty-state says 'wait for next
  CB tick (30s)' (656) though there is no CB tick — latency is computed from logs on a 30s JS
  poll; the hidden restore button tooltip says 'schedule restore on next CB tick' (263).
  boot.js:16-19 types 'BIFROST v2.0 / curator online [OK] / circuit breaker armed [OK]' on
  every fresh session. Cosmetic but directly misleads anyone evaluating the repo as a clean
  reusable core.
  → _fix:_ Header subtitle -> 'Pool Monitor · Free-Tier Router'. Latency empty-state -> 'no latency data yet — waiting for traffic'. Update boot.js lines 16-19 to drop curator/circuit-breaker. Remove the restore tooltip with the CB block.
- **[INFO/doc-drift] [SWEEP] Per-model caps note still cites removed model_capabilities.yaml**  
  `dashboard/app.py` 911, 796-801  
  api_usage_estimates_by_model returns a note string 'per-model caps from
  model_capabilities.yaml' (911) and the _load_model_capabilities docstring frames itself as
  overrides, but the YAML file was deleted (28fc7c4) and caps are now hardcoded in the function
  dict (802-833). The docker-compose mount of that YAML (line 134) is the dead mount called out
  in the critical finding. The data path works (inline dict); only the provenance note is
  wrong.
  → _fix:_ Change the note at line 911 to 'per-model caps inlined (header-verified)'. Optionally update the _load_model_capabilities docstring to stop implying an external YAML source.
- **[INFO/quality] No test coverage asserting curator/CB endpoints are gone or 410**  
  `dashboard/tests` n/a  
  After the cleanup there is no test verifying the new contract: that load_stream_state omits
  curator keys, that /api/circuit_breaker/restore|prune return 410, or that
  load_circuit_breaker is the empty stub. The only route-level test (test_stream_state.py) is
  itself broken (separate finding). The other tests (test_rpm, test_heatmap, test_logs_cache,
  test_pool_health_hourly) are pure loader unit tests untouched by the cleanup and look
  correct.
  → _fix:_ After fixing test_stream_state.py, add a small test asserting the 410 on the CB endpoints (or their removal) and that stream_state keys match the current set, so the next refactor can't silently regress.
- **[INFO/fresh-install] [SWEEP] crontab mount + get_cron_status assume host /var/spool/cron layout and a 'jkr' user — .93-specific glue in core compose**  
  `docker-compose.yml` 130  
  The dashboard mounts `/var/spool/cron/crontabs/${USER:-jkr}:/host_crontab:ro`. This path is
  Debian/Ubuntu-specific (RHEL uses /var/spool/cron/<user>), defaults the user to 'jkr' (the
  maintainer's box), and if the user has no crontab the source doesn't exist -> Docker creates
  an empty dir, get_cron_status (app.py:510-530) sees a directory at /host_crontab and open()
  errors (caught, returns empty jobs). Not breakage (the Scheduled Tasks card hides when jobs
  is empty, dashboard.html:945), but it's .93-specific glue baked into the 'core' compose and
  another empty-dir-on-host side effect.
  → _fix:_ For the harness-agnostic core, drop the crontab mount + Scheduled Tasks card (the systemd timers/cron ops it monitored were all deleted) or document it as optional and don't hardcode 'jkr'. The cron card is arguably STRIP-tier.

### install/orchestration layer (install.sh, uninstall.sh, docker-compose.yml, .env.example, .gitignore, scripts/install/install_firecrawl.sh)
_CORE (bifrost+shim+compose are the reusable core) — but the docker-compose dashboard service and install.sh steps 5-6 must be split: dashboard=DASHBOARD/OPTIONAL, and the opencode/omo/ops deploy is STRIP/ADAPTER glue that should not live in the always-run CORE install path._

- **[CRITICAL/bug] [SWEEP] docker-compose dashboard mounts 3 deleted files — breaks fresh `docker compose up`**  
  `docker-compose.yml` 131-134  
  The dashboard service bind-mounts ./bifrost/excluded_models.json (line 131),
  ./bifrost/candidate_providers.json (line 132), and ./scripts/runtime/model_capabilities.yaml
  (line 134) — all three were deleted from the repo (commits 28fc7c4 / curator-removal). On a
  clean clone these source paths don't exist, so Docker auto-creates each as an empty root-
  owned DIRECTORY and mounts it over the container path. The dashboard no longer even reads
  them: model_capabilities was inlined into dashboard/app.py:796-833 (_load_model_capabilities
  returns a hardcoded dict), and excluded_models/candidate_providers are referenced nowhere in
  app.py. So the mounts are pure dead weight that corrupt the working tree (leaving stray empty
  dirs / files) and make a standalone `docker compose up dashboard` confusing or broken.
  → _fix:_ Delete lines 131, 132, and 134 entirely. Keep only line 133 (pool_weights.yaml, which app.py:194 actually reads). model_capabilities is already inlined in code so no mount is needed.
- **[HIGH/fresh-install] [SWEEP] ~/.coire created as root by compose before user-level writer runs → models.json write fails (EACCES)**  
  `install.sh` 124,143  
  install.sh runs `docker compose up -d` at line 124 BEFORE creating ~/.coire. Both strip-shim
  (docker-compose.yml:53, `~/.coire:/root/.coire:ro`) and dashboard (line 125) bind-mount
  ~/.coire. On a fresh machine ~/.coire does not exist (confirmed missing here), so Docker
  creates it owned by root:root. Then at line 143 install.sh runs apply_pool_weights.py as user
  jkr, which auto-runs build_models_list.py (scripts/runtime/apply_pool_weights.py:211-220),
  and build_models_list.py writes to Path.home()/'.coire'/'models.json'
  (build_models_list.py:30) — into the now root-owned dir → PermissionError. The shim then
  permanently falls back to the static model list instead of the live one. install.sh never
  `mkdir -p`s ~/.coire (only bifrost/data, camofox/data, ~/.config/opencode, ~/coire-tools).
  → _fix:_ Add `mkdir -p "$HOME/.coire"` (as the user) near the top of the core section, BEFORE `docker compose up` at line 124. Same for the dashboard's rw subdir if kept.
- **[HIGH/dead-code] [SWEEP] dashboard mounts ~/.coire/curator-pool (rw) — removed curator daemon, root-owned dir on fresh clone**  
  `docker-compose.yml` 127-129  
  The dashboard mounts `~/.coire/curator-pool:/root/.coire/curator-pool:rw` with a comment
  about force-restore/prune action endpoints writing circuit_state.json. But the circuit-
  breaker daemon and curator tab were removed (commit 101245d; dashboard/app.py:249-251
  explicitly says 'Curator/candidate/recommendations functions removed 2026-05-27 ... Dashboard
  no longer has a curator tab'). ~/.coire/curator-pool does not exist on a fresh machine, so
  Docker creates it root-owned. The mount is dead (no code writes circuit_state.json anymore)
  and just litters the host.
  → _fix:_ Remove lines 127-129 (the curator-pool rw mount and its comment). The plain `~/.coire:ro` mount on line 125 is sufficient for whatever the dashboard still reads.
- **[MEDIUM/fresh-install] [SWEEP] dashboard crontab mount targets a path that doesn't exist on fresh machines**  
  `docker-compose.yml` 130  
  `/var/spool/cron/crontabs/${USER:-jkr}:/host_crontab:ro` assumes the user has an existing
  crontab file. On a fresh clone with no crontab installed, that source path is missing, so
  Docker creates a root-owned DIRECTORY inside /var/spool/cron/crontabs (a dir cron/crontab
  owns, mode drwx-wx--T). Best case the dashboard's get_cron_status (app.py:510-528) opens a
  directory and the try/except returns an error; worst case it pollutes cron's spool. Also the
  `${USER}` fallback hardcodes `jkr` — a .93/Bernardo-specific default leaking into a
  supposedly portable compose file.
  → _fix:_ Either drop the crontab mount (the cron-status panel is optional monitoring), or guard it so it only mounts when the file exists, and remove the `jkr` literal fallback. If kept, document that the user must `crontab -l` first.
- **[MEDIUM/doc-drift] [SWEEP] Stale docker-compose header: Hermes-on-host, circuit-breaker UI, systemd service that no longer exist**  
  `docker-compose.yml` 1-14,113-115  
  Header comment (lines 1-14) describes a '5 services / Hermes Free Cloud' topology where
  'Hermes itself runs on the host' and a 'Companion: circuit-breaker.service (systemd user) —
  see systemd/circuit-breaker.service'. There is no systemd/ directory in the repo (confirmed
  missing) and the circuit-breaker daemon was removed. Line 6 and 113-115 call dashboard a
  'circuit breaker UI' reading 'cooldown_status.json' — both gone. This actively misleads
  anyone doing the fresh-install audit, and references a Hermes harness the agnostic strategy
  is trying to shed.
  → _fix:_ Rewrite the header to describe the actual current stack (bifrost, strip-shim, optional dashboard/searxng/camofox). Delete the systemd/circuit-breaker.service reference and the 'circuit breaker UI / cooldown_status.json' language on lines 6 and 113-115.
- **[MEDIUM/strategy] install.sh self-describes as opencode+omo-coupled and always deploys harness glue in the CORE path**  
  `install.sh` 4-8,146-199  
  The installer header (lines 4-8) hard-frames the project as 'targeting opencode+omo as the
  harness' and bakes omo orchestration + ops-layer into core. Steps 'core 5' (146-181) and
  'core 6' (183-199) unconditionally write ~/.config/opencode/opencode.json, oh-my-
  openagent.json, copy .opencode/skills + command into the user's opencode config, and deploy
  ~/coire-tools. This is exactly the harness-specific coupling the agnostic strategy wants
  stripped: a fresh user who just wants the bifrost+shim endpoint is forced to have
  opencode+omo config written. None of this is needed for the reusable router core.
  → _fix:_ For the agnostic version: move steps 5-6 behind an opt-in flag (e.g. --with-opencode) or into a separate adapters/opencode/install-adapter.sh. Keep CORE = .env validate + compose up + seed + pool config only. Reword the header to drop 'targeting opencode+omo as the harness'.
- **[LOW/quality] [SWEEP] OPENCODE_ZEN_API_KEY in .env.example but not recognized by install.sh provider-count loop**  
  `install.sh` 73-79  
  .env.example:33 advertises OPENCODE_ZEN_API_KEY as a provider key (and
  dashboard/app.py:828-830 has opencode-zen caps), but install.sh's PROVIDER_COUNT validation
  loop (lines 73-75) does not include it. A user who sets ONLY OPENCODE_ZEN_API_KEY would hit
  the `die "no provider keys set"` guard at line 79 even though they configured a valid
  provider. Minor inconsistency between the documented provider set and the installer's
  accepted set.
  → _fix:_ Add OPENCODE_ZEN_API_KEY to the provider-count loop at lines 73-75 (and confirm seed.sh seeds it), or drop opencode-zen from .env.example if it's not a supported free-tier provider.
- **[LOW/doc-drift] install.sh banner claims dashboard is always-on core, but it lives behind a profile**  
  `install.sh` 48,221-224  
  Line 48 prints 'core: always — bifrost + strip-shim + dashboard' and the final banner
  (221-224) lists Dashboard at :9118 unconditionally. The dashboard is actually a profiled
  service (docker-compose.yml:117 `profiles: [dashboard]`) only enabled because install.sh
  hardcodes PROFILES="dashboard" at line 87. That's fine, but it conflicts with the strategy of
  treating dashboard as OPTIONAL — and if a user later runs `docker compose up -d` without
  COMPOSE_PROFILES, the dashboard silently won't come up, contradicting the banner.
  → _fix:_ Either gate the dashboard behind a flag (consistent with searxng/camofox), or document that dashboard requires COMPOSE_PROFILES=dashboard so manual `docker compose up` matches the banner. Clarify in the banner that dashboard is profile-gated.
- **[LOW/doc-drift] install_firecrawl.sh + searxng/settings.yml still reference 'Hermes' harness**  
  `scripts/install/install_firecrawl.sh` 4,108  
  install_firecrawl.sh:4 says 'Hermes-agent ships a firecrawl backend' and the closing message
  (line 108) says 'Point omo's librarian web_extract at ...'. searxng/settings.yml:1,14 say
  config is 'for Hermes' and 'required for Hermes/MCP queries'. These are stale harness-era
  references. Harmless functionally but reinforce harness coupling the agnostic strategy is
  removing, and confuse a fresh reader about what consumes these services.
  → _fix:_ Genericize the comments: describe firecrawl/searxng as optional web-extract/search backends any OpenAI-compat client can use, not 'Hermes' or 'omo librarian' specifically.
- **[LOW/fresh-install] camofox chown to UID 1000 and OS-specific cron path assume Linux/Debian + sudo**  
  `install.sh` 113-116,133  
  install.sh chowns camofox/data to 1000:1000 via sudo (lines 114-116) assuming the container's
  node user is UID 1000 and that the host user can sudo non-interactively. Line 133 installs jq
  via `sudo apt-get` — Debian/Ubuntu-only, breaks on Fedora/Arch/macOS. install_firecrawl.sh
  similarly sudo-chowns rabbit_data to 999. For a portable agnostic release these silently
  assume a Debian host with passwordless sudo. Not a .93-only path but an unstated platform
  assumption.
  → _fix:_ Document the Linux+Debian+sudo prerequisite in README, or detect the package manager / skip jq auto-install with a clear 'please install jq' message. Make the camofox UID configurable rather than hardcoded 1000.
- **[INFO/security] No secrets leaked in tracked install/orchestration files (positive confirmation)**  
  `.env.example` 5-49  
  Scanned all tracked json/yaml/yml/sh/env files in the subsystem for real keys (sk-, AIza,
  gsk_, csk-, ghp_, nvapi-, 40+ hex). Clean. .env.example contains only placeholders
  (BIFROST_API_KEY=sk-CHANGE-ME-32-random-bytes, empty provider slots), searxng/settings.yml:27
  uses 'REPLACE_ME_IN_ENV', and .env is correctly gitignored (.gitignore:1). BIFROST_PASS and
  CAMOFOX_API_KEY are auto-generated into .env at install time, never committed.
  → _fix:_ No action. Maintain the placeholder discipline; keep auto-gen of BIFROST_PASS/CAMOFOX_API_KEY writing only to the gitignored .env.
- **[INFO/fresh-install] build_models_list.py is never invoked directly by install.sh (only transitively)**  
  `install.sh` 137-144  
  install.sh never calls scripts/runtime/build_models_list.py directly; ~/.coire/models.json
  only gets generated as a side-effect of apply_pool_weights.py auto-running it
  (apply_pool_weights.py:214-220). So if apply_pool_weights fails (the `|| warn` on line 143
  makes it non-fatal), models.json is never created and the shim silently serves the static
  fallback list. Combined with the root-ownership bug above, the live model list is fragile on
  fresh installs.
  → _fix:_ Consider an explicit `python3 scripts/runtime/build_models_list.py` step after pool config, after ensuring ~/.coire is user-owned, so models.json generation is deterministic and not buried inside another script's success path.

### harness-coupling glue (adapters/, .opencode/, scripts/ops/, scripts/runtime/)
_MIXED — scripts/runtime engine = CORE (genericize the YAML data); ops scripts = ADAPTER (split bifrost/shim-generic from opencode-specific); .opencode/ + adapters/omo + adapters/opencode = STRIP/ADAPTER (move out of always-installed core into opt-in harness adapters)._

- **[HIGH/bug] [SWEEP] coire-restart default targets use container name 'coire-dashboard' instead of compose service name 'dashboard'**  
  `scripts/ops/coire-restart` 42  
  The no-arg default sets TARGETS=(bifrost strip-shim coire-dashboard). `docker compose
  restart` resolves arguments by SERVICE name. In docker-compose.yml the service is named
  `dashboard` (line 116) with container_name: coire-dashboard (line 121). bifrost and strip-
  shim work because their service names match, but `docker compose restart coire-dashboard`
  errors with 'no such service: coire-dashboard'. So the most common invocation (`coire-
  restart` with no args) fails to restart the dashboard and prints '✗ failed' for it. (The
  post-restart health loop on line 69 uses `docker ps --filter name=$svc`, which DOES
  substring-match the container name — so the failure is masked as a status read but the actual
  restart never happened.)
  → _fix:_ Change line 42 to TARGETS=(bifrost strip-shim dashboard). Optionally normalize the health-ping section to map service->container or just filter on the coire- prefix.
- **[HIGH/strategy] install.sh deploys opencode/omo harness glue as [core] 'always', defeating harness-agnostic goal**  
  `install.sh` 146-199  
  Steps 5 and 6 (`# ─── core 5. opencode + omo config` and `# ─── core 6. ops tools`) are
  inside the CORE block, not the OPTIONAL ADAPTERS block (which only gates
  camofox/searxng/firecrawl). A fresh `cp .env.example .env && ./install.sh` therefore
  unconditionally writes ~/.config/opencode/opencode.json, ~/.config/opencode/oh-my-
  openagent.json, copies all .opencode/skills + commands, and pins oh-my-openagent@latest as an
  opencode plugin — even for a user who wants only the bifrost+shim router for Claude Code or
  Codex CLI. This is exactly the bundling the strategy wants to eliminate. The header comment
  (line 4) also frames the whole project as 'targeting opencode+omo as the harness'.
  → _fix:_ For the agnostic version: move steps 5+6 behind a `--with-opencode` / `--with-omo` flag (mirroring the existing --with-searxng pattern), so core install stops at bifrost+shim(+dashboard). Relabel them [adapter]. Keep adapters/opencode + adapters/omo + .opencode/ in the tree as documented opt-in adapters, not auto-deployed.
- **[MEDIUM/coupling] pool_weights.yaml hardcodes omo-specific, load-bearing pool names as the routing-rule identities**  
  `scripts/runtime/pool_weights.yaml` 28-94  
  The three pools are named omo-main / omo-gemini / omo-utility, and these names are not
  cosmetic: per project memory the omo prompt-variant matcher does substring matching on
  omo-<family> names, and the YAML comments (lines 29-32, 66-67) explicitly document that the
  name must NOT contain kimi/gpt-5-5 so omo loads buildDynamicSisyphusPrompt.
  apply_pool_weights.py turns each pool name into a bifrost CEL rule `model == "<pool_name>"`
  (apply_pool_weights.py:131), and build_models_list.py + the opencode.json.template model list
  + oh-my-openagent.json agent map all reference these exact strings. So the routing DATA is
  tightly coupled to omo's internal prompt logic, even though the apply/build ENGINES are
  generic. A Claude Code / Codex user gets pools literally named after a harness they don't
  use.
  → _fix:_ For the agnostic core: ship a neutral example plan (e.g. pools named `main`/`fast`/`vision` or `default`/`utility`) as scripts/runtime/pool_weights.example.yaml, and move the omo-named plan into adapters/omo/ as harness-specific data. Document that pool names are arbitrary aliases the client selects via the `model` field. This decouples the generic router from omo's substring-matching convention.
- **[MEDIUM/coupling] Several ops scripts hard-assume opencode is the harness (log path + process names)**  
  `scripts/ops/coire-diagnose` 20,62-77,99-108  
  coire-diagnose is built entirely around opencode internals:
  LOG_DIR=~/.local/share/opencode/log, and it parses opencode-specific log tokens (`service=llm
  ... stream`, `small=false`, `step=N loop`, `exiting loop`, `step-finish`) to detect orphan
  streams. coire-kill-opencode (whole script) and coire-health (lines 62-71, 82-104 read
  opencode PIDs + ~/.local/share/opencode/log) are likewise opencode-bound. coire-cascade-show
  filters strictly to rules where name.startswith('omo-') (coire-cascade-show:91), so it shows
  nothing for a non-omo pool naming scheme. In a harness-agnostic build these are dead weight
  for a Claude Code / Codex user — none of those harnesses write that log format or run an
  `opencode` process.
  → _fix:_ Split the ops layer: keep the harness-neutral tools (coire-health containers/API/shim sections, coire-monitor, coire-probe, coire-check-quotas, coire-snapshot-sync, coire-restart) as the reusable CORE ops set; move coire-diagnose + coire-kill-opencode + the opencode-log/PID sections of coire-health into adapters/opencode/ops/ as opencode-specific. Make coire-cascade-show's `omo-` filter configurable (e.g. --pool-prefix, default show-all).
- **[MEDIUM/strategy] .opencode/ skills+commands are 100% opencode-harness glue, not core**  
  `.opencode/README.md` 1-139  
  The entire .opencode/ tree (7 SKILL.md, 7 command/*.md, 2 skill scripts) is opencode-
  specific: skills use opencode's frontmatter + slash-command system, the README documents
  deployment to ~/.config/opencode/{skills,command}, and the 'Driving opencode + omo
  autonomously via HTTP' section (README:87-139) documents the opencode web REST API + omo
  agent names. The two scripts (monitor.py, probe.py) are themselves harness-neutral (pure
  bifrost/shim HTTP), but they are wrapped/owned by opencode skills here and only symlinked
  into ~/coire-tools by install.sh. For an agnostic ship this whole directory is STRIP-or-
  relocate.
  → _fix:_ Relocate .opencode/ wholesale to adapters/opencode/.opencode/ (or a clearly opt-in path) and gate its deployment behind --with-opencode. Move the two reusable scripts (monitor.py, probe.py) to scripts/ops/ as the canonical copies and have the opencode skill scripts symlink to them (reverse the current dependency, which currently has install.sh symlinking FROM the skill INTO coire-tools).
- **[LOW/doc-drift] adapters/omo/oh-my-openagent.json is the only correctly-shaped adapter, but its $schema points to a dev branch**  
  `adapters/omo/oh-my-openagent.json` 2  
  This file is exactly the kind of thin, documented, harness-specific adapter the strategy
  wants — it maps omo agent/category names to coire/omo-* models and carries a clear _doc
  rationale. Two notes: (1) the $schema URL pins the `dev` branch of code-yeongyu/oh-my-
  openagent (.../dev/assets/oh-my-opencode.schema.json), which can drift/404 as that branch
  moves; (2) every model reference (coire/omo-main etc.) is hard-tied to the omo pool names
  from pool_weights.yaml, reinforcing the coupling noted separately. It correctly contains no
  secrets.
  → _fix:_ Keep as the canonical omo ADAPTER. Pin the $schema to a tagged release or vendor the schema locally to avoid dev-branch drift. Keep it co-located with the omo-named pool plan so the harness-specific data travels together.
- **[LOW/coupling] adapters/opencode/opencode.json.template bundles camofox/searxng/firecrawl MCPs that core install does not enable**  
  `adapters/opencode/opencode.json.template` 23-50  
  The template unconditionally registers searxng (:8891), firecrawl (:3002), and camofox
  (:9378) MCP servers with enabled:true, plus references ${CAMOFOX_API_KEY}. But these backends
  are OPTIONAL adapters in install.sh (--with-searxng/--with-firecrawl/--with-camofox, default
  off). So a default install that merges/copies this template gives opencode three MCP servers
  pointing at ports where nothing is listening, and camofox requires CAMOFOX_API_KEY which a
  core-only user never set. Functional only if those optional adapters were installed. No
  secret leak (CAMOFOX_API_KEY is a ${VAR} placeholder, correct).
  → _fix:_ For the agnostic adapter: ship a minimal opencode.json.template with only the coire provider, and document the searxng/firecrawl/camofox MCP blocks as opt-in snippets to paste when those adapters are installed. Or have install.sh conditionally inject each MCP block based on the corresponding --with-* flag.
- **[LOW/strategy] deploy.sh is .68->.93-specific and references opencode skill paths; redundant with install.sh**  
  `scripts/ops/deploy.sh` 8,15-26  
  deploy.sh hardcodes TARGET=jkr@192.168.1.93 and scp's the ops scripts to ~/coire-tools, then
  symlinks monitor/probe from ~/.config/opencode/skills/... (opencode-specific path) on the
  remote. This is a personal two-host workflow tool (.68 dev -> .93 deploy) and overlaps
  heavily with what install.sh step 6 already does locally. For a generic repo it's
  environment-specific noise and assumes opencode skill layout exists on the target.
  → _fix:_ Exclude deploy.sh from the agnostic core (it's a personal ops convenience). If kept, parameterize fully via TARGET (already done) and drop the opencode-skill symlink step or guard it behind a flag. Document it as 'maintainer-only, two-host sync', not part of the shipped tooling.
- **[LOW/doc-drift] apply_pool_weights.py docstring references removed 'daily curator'**  
  `scripts/runtime/apply_pool_weights.py` 8-9  
  Docstring says 'This is reused by the daily curator and by ops one-shots...'. The curator was
  removed in the recent bare-minimum refactor (curator dashboard tab + curator/candidate JSON
  deleted, per recent commits and dashboard/app.py:249-251). No code path here actually depends
  on a curator — it's a stale doc reference only, so harmless at runtime, but it's exactly the
  kind of dead reference the review flagged to watch for. The engine itself is otherwise clean
  and harness-agnostic (pure bifrost routing-rule API driver).
  → _fix:_ Drop the 'daily curator' clause from the docstring. Reword to 'Used by install.sh and ops one-shots when capacity-aware re-weighting is needed.'
- **[INFO/strategy] apply_pool_weights.py auto-runs sync_key_models + build_models_list; keep these together in the CORE cut**  
  `scripts/runtime/apply_pool_weights.py` 189-220  
  After applying, the script auto-runs bifrost/sync_key_models.py and
  scripts/runtime/build_models_list.py via subprocess. Both targets exist in the repo today
  (verified present), so this works on a fresh clone. Flagging for the strip plan: these two
  helpers are part of the bifrost-core surface, not harness glue, so they must travel with the
  CORE (bifrost+shim) cut — apply_pool_weights.py is otherwise a clean, reusable engine and
  should be classified CORE alongside them. The subprocess calls are guarded by .exists() so a
  missing helper degrades gracefully rather than crashing.
  → _fix:_ When carving the agnostic core, keep apply_pool_weights.py + build_models_list.py + bifrost/sync_key_models.py + bifrost/snapshot.py together as the routing-runtime CORE. No code change needed; this is a classification note for the strip plan.
- **[INFO/doc-drift] build_models_list.py output path/comments name omo pool aliases as the example surface**  
  `scripts/runtime/build_models_list.py` 5-7,89-95  
  The engine is fully generic — it reads live bifrost routing-rules + providers and emits
  ~/.coire/models.json for the shim's /v1/models, with owned_by:'coire-ansic'. It is NOT
  hardcoded to omo (it enumerates whatever pools/targets bifrost has). Only the docstring
  example names the omo-* aliases (omo-main/omo-utility/omo-gemini) and lists 'opencode/omo
  etc.' as the consumer. So this is reusable CORE; just the documentation is omo-flavored.
  → _fix:_ Keep as CORE. Soften the docstring to say 'pool aliases (whatever your pool_weights.yaml defines)' rather than naming omo-* specifically, so the agnostic intent is clear.
- **[INFO/quality] coire-check-quotas probes a fixed provider/model list that has drifted from the live pool plan**  
  `scripts/ops/coire-check-quotas` 33-43  
  The probe list is hardcoded and includes nvidia-nim/deepseek-v4-pro and groq/llama-3.3-70b,
  both of which pool_weights.yaml explicitly dropped (groq 'DROPPED too small for omo' at
  pool_weights.yaml:19; nvidia deepseek-v4-pro 'Removed ... cold today' at
  pool_weights.yaml:39). It also omits some pool fallbacks (e.g. openrouter deepseek-v4-flash).
  Functionally fine — it's an end-of-day quota scout, and probing dropped/candidate providers
  is arguably intentional scouting — but it's manual-maintenance drift and it reads keys
  directly from .env (CEREBRAS_API_KEY, etc.), so the probe list must stay in sync with what
  .env.example documents.
  → _fix:_ Low priority: either drive the probe list from pool_weights.yaml / live bifrost providers, or add a comment that the list is intentionally a superset (includes scouting candidates). Ensure every *_API_KEY it sources is present in .env.example so a fresh user doesn't hit unbound-variable under `set -u`.
- **[INFO/quality] coire-monitor window parser raises KeyError on malformed --since unit**  
  `.opencode/skills/coire-monitor/scripts/monitor.py` 76-78  
  unit=args.since[-1]; n=int(args.since[:-1]); delta={'h':...,'m':...,'d':...}[unit]. A typo
  like --since 1w raises KeyError, and --since 1 raises ValueError on int(''). Minor UX
  papercut in a diagnostic tool; not a correctness/security issue. The script is otherwise
  harness-neutral (pure bifrost /api/logs reader) and is a good CORE-ops candidate.
  → _fix:_ Optional: validate the unit char and emit a friendly error ('use m/h/d, e.g. 30m'). Not blocking.
- **[INFO/security] No secrets leaked in any tracked file across the subsystem**  
  `scripts/ops/coire-check-quotas` 9-11,33-46  
  Verified: adapters/, .opencode/, scripts/ops/, scripts/runtime/ contain no real API keys,
  passwords, or tokens. coire-check-quotas sources keys from .env at runtime (set -a; source
  .env) and references them only as $CEREBRAS_API_KEY etc. apply_pool_weights.py +
  build_models_list.py read BIFROST_PASS from env or parse it out of .env (gitignored), never
  hardcoding it. The only credential-shaped string in tracked files is the ${CAMOFOX_API_KEY}
  placeholder in opencode.json.template, which is correct.
  → _fix:_ No action. Confirms the security posture for this subsystem is clean.

### Documentation / prose (README.md, CONTRIBUTING.md, CHANGELOG.md, HANDOFF.md, MORNING.md, NOTICE, LICENSE, docs/*.md, camofox/README.md, .opencode/README.md, adapters/*)
_DASHBOARD/OPTIONAL for docs as a subsystem — but the docs REQUIRE a CORE-vs-ADAPTER rewrite: README/CONTRIBUTING must be re-centered on bifrost+shim as CORE with opencode/omo demoted to ADAPTER docs; HANDOFF.md/MORNING.md/docs/omo-*.md are STRIP (dated session scratchpads + obsolete pool designs)._

- **[HIGH/doc-drift] CONTRIBUTING bug-report template tells users to query a removed circuit-breaker daemon + API**  
  `CONTRIBUTING.md` 15-16  
  The 'Filing a bug' checklist instructs users to paste `journalctl --user -u circuit-breaker
  -n 50` and `curl http://localhost:9118/api/circuit_breaker`. The circuit-breaker daemon was
  removed (commit 9d5a28c hid the dead CB panel; the daemon + systemd timers are gone). A
  fresh-clone user following this will get 'Unit circuit-breaker not found' and a 404,
  producing confusing/empty bug reports and signaling the project still ships a CB.
  → _fix:_ Replace lines 15-16 with current diagnostics: `docker compose ps`, last 50 lines of `docker compose logs bifrost strip-shim`, and the dashboard URL `http://localhost:9118` (no /api/circuit_breaker). Drop the systemd-timers reference.
- **[HIGH/doc-drift] CONTRIBUTING 'Adding a provider' step 5 points to non-existent auto_rebalance_weights.py**  
  `CONTRIBUTING.md` 40-41  
  Step 5 says 'Add to scripts/runtime/auto_rebalance_weights.py:PROVIDER_TO_ENV'. That file
  does not exist (the auto-rebalance/op-rebalance machinery was removed; scripts/runtime now
  holds only apply_pool_weights.py, build_models_list.py, pool_weights.yaml). A contributor
  will hit a dead path and the new provider won't get classified anywhere.
  → _fix:_ Remove step 5 entirely, or redirect it to the surviving mechanism. The env→provider mapping now effectively lives in bifrost/seed.sh + bifrost/sync_key_models.py; update the step to reference those.
- **[MEDIUM/doc-drift] CONTRIBUTING 'Adding a pool' step 3 references operator/pi-models.json + pi-op (removed pi-operator layer)**  
  `CONTRIBUTING.md` 53-54  
  Step 3 tells contributors to 'Add a pi-models.json entry under operator/pi-models.json if pi-
  op should be able to use it.' There is no operator/ directory in the repo and no pi-op layer;
  the entire pi-operator/curator subsystem was removed. This is a dead instruction.
  → _fix:_ Delete step 3. Adding a pool is now just: add block to pool_weights.yaml (sum=1.0) + run apply_pool_weights.py. The bifrost routing rule is auto-created; nothing else is needed.
- **[MEDIUM/doc-drift] [SWEEP] README Tree lists deleted files candidate_providers.json + excluded_models.json**  
  `README.md` 221-222  
  The Tree section under bifrost/ lists `candidate_providers.json` and `excluded_models.json`.
  Neither is tracked nor present on disk (confirmed: git ls-files empty, ls fails). These are
  the same files the known docker-compose dashboard-mount bug references (docker-
  compose.yml:131-132), so the README's Tree corroborates a broken layout. A fresh cloner
  comparing tree-to-disk will think their clone is incomplete.
  → _fix:_ Remove both lines from the Tree. While editing, the actual bifrost/ contents are: seed.sh, apply_snapshot.py, snapshot.py, sync_key_models.py, snapshot/{providers.json,routing-rules.json} — align the tree to that.
- **[LOW/doc-drift] README Extending step 2 references coire-add-provider skill 'when built' — never built**  
  `README.md` 184-186  
  Step 2 says 'see .opencode/skills/coire-add-provider when built'. That skill does not exist
  (the 7 shipped skills are cascade-show/check-quotas/diagnose/health/monitor/probe/snapshot-
  sync). The 'when built' hedge has been left in a doc framed as a public release; it reads as
  an unfinished promise.
  → _fix:_ Drop the parenthetical. Step 2 should just point to bifrost/seed.sh as the POST template (which exists and is the real path).
- **[HIGH/doc-drift] docs/omo-pool-tuning.md describes the obsolete 7-pool topology + auto-runs of removed scripts**  
  `docs/omo-pool-tuning.md` 1-216  
  The entire doc is built around pools best/code/vision/compress/fast/mid/ops and an `ops` pool
  for 'pi-op-* operator agents' (line 112). The live pool_weights.yaml has exactly 3 pools
  (omo-main, omo-gemini, omo-utility) — none of the 7 documented pools exist. Line 212 also
  claims apply_pool_weights.py 'auto-runs sync_key_models + build_models_list', and the doc
  references pi-op agents that were removed. This is the single most drifted doc: it will
  actively mislead anyone tuning pools.
  → _fix:_ For the public release, either delete this doc or rewrite it against the 3 real pools. At minimum move it to an archive/ folder with a header banner '⚠ describes pre-2026-05 7-pool design, superseded'. Verify the apply_pool_weights.py auto-run claim against the actual script before repeating it.
- **[MEDIUM/doc-drift] docs/omo-perfect-config.md references removed omo-kimi/omo-gpt-5-5 pools + deepseek/deepseek-chat targets**  
  `docs/omo-perfect-config.md` 59-118, 167-218  
  This design doc centers on pools omo-kimi and omo-gpt-5-5 (lines 59, 63, 73-118) and the per-
  pool cap/effort tables (167-199) list best/code/mid/fast/compress/vision/ops — all removed.
  It also lists deepseek/deepseek-chat + deepseek/deepseek-reasoner as fallback targets. Memory
  says omo pool names are load-bearing for omo's prompt-variant matcher, so a doc telling users
  to recreate omo-kimi is doubly wrong now that the design moved to omo-main (family-neutral,
  intentionally chosen to load the default variant per HANDOFF.md:166-177).
  → _fix:_ Archive or rewrite against current omo-main/omo-utility/omo-gemini. If kept as historical rationale, add a banner pointing to the current pool_weights.yaml as source of truth.
- **[MEDIUM/doc-drift] CHANGELOG 0.1.0 describes circuit-breaker daemon, operator timers, 7 pools, hermes/telegram adapters — all removed**  
  `CHANGELOG.md` 5-63  
  The 'initial public release' changelog claims: 10 providers + 7 weighted pools (line 11), a
  circuit-breaker daemon with fcntl locking (17-20), 7 operator systemd timers incl. cb-
  deadman/pi-op-*/op-rebalance/op-discover (21-30), an `ops` pool (31-33), and `--with-
  hermes`/`--with-telegram` adapters (52-53). None of this is in the current code: 3 pools, no
  CB, no timers, install.sh has no hermes/telegram flags (only camofox/searxng/firecrawl). It
  also references bifrost_tune_timeouts.py + op-log helper which aren't in the repo. Shipping
  this as the 0.1.0 public changelog misrepresents the product.
  → _fix:_ Rewrite CHANGELOG 0.1.0 to match what actually ships: bifrost + strip-shim core, 3 omo pools, dashboard, 3 opt-in adapters (searxng/firecrawl/camofox), 7 ops skills + CLI tools, no daemons/timers. The README's 'How it stays healthy' section (no daemons/timers) is the correct current story — make the changelog consistent with it.
- **[MEDIUM/doc-drift] NOTICE credits hermes-agent + pi-coding-agent + wrong camofox upstream — none match shipped code**  
  `NOTICE` 17-42  
  NOTICE lists hermes-agent (NousResearch) 'optional adapter via --with-hermes' (17-22) and pi-
  coding-agent @earendil-works (24-27) as runtime deps — neither has any install.sh flag or
  code path (grep for hermes/telegram in install.sh = empty). Worse, the Camoufox entry (37-41)
  cites `https://github.com/jo-inc/camofox-browser`, but the actual clone source everywhere
  else (install.sh:11, camofox/README.md) is `redf0x1/camofox-browser`. A NOTICE file is the
  legal-attribution doc; citing the wrong upstream repo and crediting deps that aren't used is
  both inaccurate and a compliance smell for a public MIT release.
  → _fix:_ Remove the hermes-agent and pi-coding-agent stanzas (no longer adapters). Fix the Camoufox URL to redf0x1/camofox-browser to match install.sh + camofox/README.md (and clarify the underlying engine is daijro/camoufox, MPL-2.0, as camofox/README.md:49 states). Keep only Bifrost/SearXNG/Camoufox/Firecrawl which are actually installed.
- **[HIGH/strategy] README is framed as an opencode+omo appliance, contradicting the harness-agnostic strategy**  
  `README.md` 7-11, 45-62, 158-192, 248-266  
  The strategy under evaluation is a harness-AGNOSTIC core (bifrost+shim) with thin adapters.
  But the README's first sentence defines the project as 'A free-tier LLM router for opencode +
  oh-my-openagent', the Core table calls omo pools part of Core (line 49: '3 omo pools'), the
  only Pools doc is in omo-agent terms (55-62), and Extending/health sections assume opencode
  skills (/coire-* slash commands). The pool names themselves (omo-main/omo-utility/omo-gemini)
  bake omo into the supposedly-generic core. install.sh confirms the coupling: a plain
  `./install.sh` (core only) unconditionally deploys oh-my-openagent.json + opencode skills
  into ~/.config/opencode (install.sh:147-200). So the docs AND installer treat omo as core,
  not adapter. This is the central blocker for the agnostic-release strategy.
  → _fix:_ Restructure: (1) README opening = 'OpenAI-compatible free-tier router (bifrost + strip-shim)'; point any client at :4002. (2) Move all omo/opencode content into an Adapters section that links adapters/omo/ + adapters/opencode/ + .opencode/. (3) Rename pools to harness-neutral (e.g. main/utility/vision) OR explicitly document that pool names are arbitrary aliases the caller chooses, noting the omo-prefix is only load-bearing IF you use omo's prompt-variant matcher. (4) Decouple install.sh so opencode/omo deploy moves behind a --with-opencode flag. The docs can't be made agnostic while the installer hard-wires the harness.
- **[MEDIUM/doc-drift] [SWEEP] DeepSeek provider in tracked snapshot but absent from README table + .env.example (fresh-install gap)**  
  `README.md` 40-43, 127-141  
  bifrost/snapshot/providers.json (tracked) contains a 13th provider `deepseek` requiring
  ${DEEPSEEK_API_KEY}. But: (a) README architecture diagram + provider sign-up table list only
  12 providers, no DeepSeek; (b) .env.example has no DEEPSEEK_API_KEY line; (c)
  README/CHANGELOG say '12 providers'/'10 providers' respectively — three different counts. On
  a fresh `cp .env.example .env && ./install.sh` followed by apply_snapshot, the deepseek
  provider resolves ${DEEPSEEK_API_KEY} to an unset var. Either DeepSeek is a real provider
  (then docs+env.example must include it) or it's stale snapshot cruft (then it should be
  pruned).
  → _fix:_ Decide DeepSeek's status. If keeping: add DEEPSEEK_API_KEY to .env.example, add the row to README's provider table, fix the '12 providers' counts to 13. If dropping: remove the deepseek block from bifrost/snapshot/providers.json + routing-rules. Either way reconcile the provider count across README diagram (40-42), README table (127-141), and CHANGELOG (line 5).
- **[LOW/doc-drift] HANDOFF.md + MORNING.md are dated session scratchpads describing dead state — strip for public release**  
  `HANDOFF.md` 1-265  
  HANDOFF.md (2026-05-21/22) documents the old omo-kimi/omo-gpt-5-5 pools, the deleted
  best/code/mid/fast/compress/vision/ops bifrost rules (line 11), the paused hermes adapter
  (line 10), .proposed files, and a private .93 deploy workflow with scp/ssh to 192.168.1.93.
  MORNING.md (2026-05-27) similarly hard-codes .93, opencode web on :4040, and a snapshot of '5
  containers up'. These are internal worklog artifacts, not user docs; they reference the very
  things that were removed and expose .93-specific paths/IPs. .opencode/README.md:88-139 also
  embeds the 192.168.1.93:4040 remote-control flow.
  → _fix:_ For a public-release repo, move HANDOFF.md + MORNING.md out of the repo root (delete or relocate to a private/ gitignored notes dir). If any content is worth keeping (e.g. the omo prompt-variant-matcher insight, HANDOFF:166-177), promote it into a clean architecture doc with no dated/.93-specific framing. Scrub the 192.168.1.93 references from .opencode/README.md or genericize to <host>.
- **[LOW/doc-drift] CONTRIBUTING 'Adding an adapter' + README adapters table omit camofox in --all, but install.sh includes it**  
  `README.md` 64-71  
  README's Optional-adapters table says `--all` = 'searxng + firecrawl (camofox stays opt-in)'
  (line 71) and the --with-camofox row says camofox is BYO/opt-in (line 70). But install.sh:34
  has `--all) WITH_SEARXNG=1; WITH_FIRECRAWL=1; WITH_CAMOFOX=1` and its header comment
  (install.sh:14) explicitly says --all now includes camofox (~150MB browser download). The doc
  says the opposite of the code — a user running --all expecting a light install will get the
  150MB Camoufox binary.
  → _fix:_ Update README line 71 to match install.sh: `--all` = searxng + firecrawl + camofox, and note the ~150MB browser download so the surprise is documented. Reconcile with camofox/README.md which correctly notes the download size.
- **[INFO/doc-drift] CHANGELOG provider-caps table conflicts with README/HANDOFF verified caps (quota drift)**  
  `CHANGELOG.md` 35-48  
  CHANGELOG lists caps (e.g. github-models '20000 RPM, 2M TPM', sambanova '20 RPD', cohere
  '1000 monthly') that conflict with HANDOFF.md's later header-probed values (github-models 8k
  ctx cap noted as too small, sambanova 20 RPD per-MODEL, cohere 20 RPM trial). Recent commit
  64fd4d8 also refreshed PROVIDER_QUOTAS in dashboard with 'verified 2026-05-27 limits'. The
  CHANGELOG caps table is the oldest snapshot and now lags the dashboard's authoritative
  PROVIDER_QUOTAS.
  → _fix:_ Don't maintain provider caps in the CHANGELOG at all — point readers to the single source (dashboard/app.py PROVIDER_QUOTAS, header-verified). Remove the caps table from CHANGELOG to avoid a third drifting copy.
- **[INFO/security] No secret leakage in tracked docs/config — verified clean**  
  `bifrost/snapshot/providers.json` n/a  
  Scanned all tracked json/yaml/sh/env.example in scope for live secrets:
  bifrost/snapshot/providers.json contains only ${ENV_VAR} placeholders
  (CLOUDFLARE_ACCOUNT_ID/API_KEY, DEEPSEEK/NVIDIA/SAMBANOVA/GITHUB_MODELS/PROVIDER_API_KEY) —
  snapshot.py redaction (bifrost/snapshot.py:74-88) confirmed working as CONTRIBUTING.md:75-76
  claims. .env.example has empty placeholders only. No sk-/AIza/gsk_/ghp_/Bearer real tokens
  found anywhere tracked. .env is gitignored (correct).
  → _fix:_ No action. This validates the CONTRIBUTING security claim about snapshot.py redaction; keep that guarantee documented if the snapshot workflow stays in the public release.

## Appendix B — verified harness connect recipes (for the §5 adapter docs)

_Raw research excerpts (some snippets truncated). Source material for the clean, tested
connect guides produced in §7 step 5 — not the final docs._

### Claude Code
- identified: Claude Code — Anthropic's terminal coding agent. It speaks the Anthropic Messages API natively (`POST /v1/messages`, `/v1/messages/count_tokens`), NOT the OpenAI Chat Completions API. There is no built-in setting to point it at a bare OpenAI-compatible endpoint.
- openai-compat: **partial** (confidence high)
- connect: PLAINLY: Claude Code CANNOT talk to a raw OpenAI-compatible endpoint directly. `ANTHROPIC_BASE_URL` only changes WHERE requests go, not the wire format — Claude Code always sends Anthropic Messages API bodies. Anthropic's official "LLM gateway" docs state the gateway MUST expose one of: Anthropic Messages (`/v1/messages`, `/v1/messages/count_tokens`), Bedrock InvokeModel, or Vertex rawPredict, and must forward the `anthropic-beta` and `anthropic-version` headers. So CoireAnsic's OpenAI-compatible surface needs a translation bridge. Three real options, in order of relevance to a Bifrost-based router: (A) BIFROST NATIVE ANTHROPIC ROUTE (best fit — CoireAnsic is built on Bifrost). Bifrost ships

```
# ===== OPTION A: Claude Code -> Bifrost native /anthropic route =====
# ~/.claude/settings.json  (env block is read once at process start)
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://192.168.1.93:4002/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "sk-coireansic-yourkey",          // sent as Authorization: Bearer
    "ANTHROPIC_DEFAULT_OPUS_MODEL":   "omo-main",             // your router model name
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "provider/model-sonnet",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL":  "provider/model-fast",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1"         // optional; needs /anthropic/v1/models on router
  }
}
# Then: claude            (or:  claude --model omo-main)

# Equivalent as shell exports:
export ANTHROPIC_BASE_URL="http://192.168.1.93:4002/anthropic"
export ANTHROPIC_AUTH_TOKEN="sk-coireansic-yourkey"
export ANTHROPIC_DEFAULT_SONNET_MODEL="provider/model-sonnet"

# ===== OPTION B: Claude Code -> LiteLLM -> CoireAnsic (OpenAI-compat) =====
# config.yaml for LiteLLM proxy
model_list:
  - model_name: omo-main
    litellm_params:
      model: openai/omo-main                 # openai/ prefix = OpenAI-compatible backend
      api_base: http://192.168.1.93
```
- gotchas: - WIRE FORMAT IS THE WHOLE STORY: Claude Code never emits OpenAI Chat Completions. `ANTHROPIC_BASE_URL` is a destination switch, not a translator (confirmed verbatim in model-config docs: "ANTHROPIC_BASE_URL changes where requests are sent, not which model answers them"). A bare OpenAI `/v1/chat/completions` endpoint will reject Claude Code's `/v1/messages` POSTs. A bridge (Bifrost /anthropic, LiteLLM, or claude-code-router) is mandatory. - AUTH HEADER: `ANTHROPIC_AUTH_TOKEN` => `Authorization: Bearer <token>`. `ANTHROPIC_API_KEY` => `x-api-key: <key>`. AUTH_TOKEN takes precedence over API_KEY

### OpenAI Codex CLI
- identified: OpenAI Codex CLI — the Rust-based open-source `codex` terminal coding agent from openai/codex. Configured via ~/.codex/config.toml. Not to be confused with the deprecated Node "@openai/codex" or the cloud "Codex" web agent.
- openai-compat: **partial** (confidence high)
- connect: Codex DOES support pointing at a custom base URL via a `[model_providers.<id>]` table in ~/.codex/config.toml (or pass --config / -c overrides on the CLI). You set `base_url`, `env_key` (name of the env var holding the bearer token), and select it with the top-level `model` + `model_provider` keys. BUT — and this is the dealbreaker for CoireAnsic as written — Codex now ONLY speaks the OpenAI **Responses** API, not Chat Completions. In the current source (codex-rs/model-provider-info/src/lib.rs, v0.135.0) the `WireApi` enum has a single variant `Responses`; deserializing `wire_api = "chat"` returns a hard error: `\"wire_api = \\\"chat\\\"\" is no longer supported. set wire_api = \"responses\"

```
# ~/.codex/config.toml

# pick the model the router exposes and which provider def to use
model = "omo-main"            # or "provider/model" — passed through verbatim as the Responses `model` field
model_provider = "coire"

[model_providers.coire]
name = "CoireAnsic router"
# Codex appends "/responses" -> POSTs to http://192.168.1.93:4002/v1/responses
base_url = "http://192.168.1.93:4002/v1"
# Codex reads this env var at runtime and sends it as: Authorization: Bearer <value>
env_key = "CODEX_COIRE_KEY"
# REQUIRED in current Codex — "chat" is a hard error now. Default is also "responses".
wire_api = "responses"
# optional: extra static headers
# http_headers = { "X-Coire-Client" = "codex" }
# optional: headers pulled from env when present
# env_http_headers = { "X-Trace-Id" = "CODEX_TRACE_ID" }
# optional tuning
request_max_retries = 4

# Then run:
#   export CODEX_COIRE_KEY=sk-your-router-token
#   codex
# or one-off without editing the file:
#   codex -c model_provider=coire -c model=omo-main \
#         -c 'model_providers.coire.base_url="http://192.168.1.93:4002/v1"' \
#         -c 'model_providers.coire.env_key="CODEX_COIRE_KEY"' \
#         -c 'model_providers.coire.wire_api=
```
- gotchas: - RESPONSES API ONLY. This is the single biggest gotcha. `wire_api = "chat"` is removed (hard deserialize error) as of ~v0.84/PR #10157, early Feb 2026; the only accepted value is "responses" (also the omit-default). A plain /v1/chat/completions proxy like CoireAnsic will NOT work directly — Codex POSTs to {base_url}/responses with a Responses-shaped body and expects Responses-shaped SSE back. You must either add a /responses endpoint to the router or front it with a responses->chat shim (LiteLLM proxy, or VibeAround API Bridge). Ship that shim as the documented thin adapter. - /v1 PATH HANDLI

### opencode
- identified: opencode (sst/opencode) — confirmed unambiguous. The CoireAnsic repo already targets it (.opencode/ dir + adapters/opencode/opencode.json.template).
- openai-compat: **yes** (confidence high)
- connect: opencode points at any OpenAI-compatible /v1/chat/completions endpoint via a custom provider declared in opencode.json, using the @ai-sdk/openai-compatible npm adapter with options.baseURL. Steps: 1. Pick a config location: - Project-scoped: opencode.json in the project/repo root. - Global: ~/.config/opencode/opencode.json. - Override: OPENCODE_CONFIG=/abs/path/opencode.json (or inline OPENCODE_CONFIG_CONTENT). Configs are merged, later overrides only conflicting keys. 2. Declare a provider object keyed by an id (CoireAnsic uses "coire"). Inside it set: - "npm": "@ai-sdk/openai-compatible" (this adapter hits /v1/chat/completions; use @ai-sdk/openai instead only for the /v1/responses API, whi

```
// opencode.json  (project root, or ~/.config/opencode/opencode.json)
// export COIRE_API_KEY=sk-... in your shell first
{
  "$schema": "https://opencode.ai/config.json",
  "model": "coire/omo-main",
  "provider": {
    "coire": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "CoireAnsic (free-tier router)",
      "options": {
        "baseURL": "http://192.168.1.93:4002/v1",
        "apiKey": "{env:COIRE_API_KEY}"
      },
      "models": {
        "omo-main": {
          "name": "omo-main",
          "limit": { "context": 200000, "output": 65536 }
        },
        "omo-utility": { "name": "omo-utility" },
        "omo-gemini":  { "name": "omo-gemini" }
      }
    }
  }
}

// If the router is keyless, drop the apiKey line.
// For a non-Bearer scheme, swap apiKey for:
//   "headers": { "x-api-key": "{env:COIRE_API_KEY}" }
//
// Minimal sanity check the endpoint expects (what opencode will POST):
//   POST http://192.168.1.93:4002/v1/chat/completions
//   Authorization: Bearer $COIRE_API_KEY
//   {"model":"omo-main","messages":[...],"stream":true,"tools":[...]}
```
- gotchas: - /v1 path handling: put EXACTLY the /v1 prefix in options.baseURL. The adapter appends /chat/completions. Adding /chat/completions yourself yields 404 NotFoundError. - Model-name passthrough: the provider.models KEY is sent verbatim as the "model" field on the wire. So "omo-main" or a "provider/model" string passes straight through to the router. The per-model "name" is cosmetic (picker label) only. - The provider-level "name" field is load-bearing. Without it there is an open/known bug (anomalyco/opencode #971 closed-with-workaround, #5674 still open at v1.0.164) where options ({baseURL, api

### pi
- identified: Ranked candidates for "pi": 1. (BEST MATCH, high confidence) "pi" = the Pi coding agent by Mario Zechner (badlogic) — repo now at github.com/earendil-works/pi (formerly github.com/badlogic/pi-mono), homepage pi.dev. npm package: @earendil-works/pi-coding-agent (older: @mariozechner/pi-coding-agent). It's a terminal coding-agent CLI with a Read/Write/Edit/Bash tool core and a unified multi-provider
- openai-compat: **yes** (confidence high)
- connect: Pi has first-class support for arbitrary OpenAI-compatible endpoints via a user-level provider config file. No code, no harness shipped by the router — the user just declares CoireAnsic as a custom provider. Steps: 1. Install Pi: curl -fsSL https://pi.dev/install.sh | sh (or: npm install -g --ignore-scripts @earendil-works/pi-coding-agent) 2. Create/edit ~/.pi/agent/models.json. `providers` is an OBJECT keyed by provider id. For each provider set: - baseUrl: the FULL OpenAI base URL INCLUDING the /v1 suffix (e.g. http://192.168.1.93:4002/v1). Pi appends /chat/completions itself; the path must end in /v1, not /v1/chat/completions. - api: "openai-completions" (this is the OpenAI Chat Completio

```
// ~/.pi/agent/models.json
// `providers` is an object keyed by provider id.
{
  "providers": {
    "coireansic": {
      "baseUrl": "http://192.168.1.93:4002/v1",
      "api": "openai-completions",
      "apiKey": "$COIREANSIC_API_KEY",
      "compat": {
        "supportsDeveloperRole": false
      },
      "models": [
        { "id": "omo-main",        "name": "CoireAnsic omo-main",  "contextWindow": 128000, "maxTokens": 8192 },
        { "id": "provider/model",  "name": "CoireAnsic passthrough" }
      ]
    }
  }
}

# Then in your shell:
#   export COIREANSIC_API_KEY="sk-your-router-token"
#   pi --model coireansic/omo-main
#   # or with a thinking level:
#   pi -m "coireansic/omo-main:medium"
#
# apiKey alternatives instead of "$COIREANSIC_API_KEY":
#   "apiKey": "!pass show coireansic/token"   # run a command, use stdout
#   "apiKey": "sk-literal-token"               # literal value
# A literal placeholder is fine if the router does not enforce auth
# (same convention as the Ollama example "apiKey": "ollama").
#
# --- oh-my-pi (omp) fork equivalent: ~/.omp/agent/models.yml ---
# providers:
#   coireansic:
#     baseUrl: http://192.168.1.93:4002/v1
#     api: openai-completion
```
- gotchas: - baseUrl must INCLUDE /v1 and STOP there. Pi appends /chat/completions. Do NOT put /v1/chat/completions (confirmed by the official Ollama example using http://localhost:11434/v1). This matches the router exposing http://host:4002/v1. - `providers` is an OBJECT keyed by id, NOT an array. (One WebFetch render hallucinated an array-with-"type":"openai" schema — that is wrong; the authoritative docs/models.md and the bundled Ollama example use the object-keyed `api`:"openai-completions" form.) - Model name PASSTHROUGH: the model `id` field is sent verbatim as the OpenAI `model` param, so "omo-mai
## Appendix C — Bifrost best-practice validation (cited, adversarially checked)

Two-workflow research, 2026-05-29. Verdicts adversarially re-checked (bifrost: 5 upheld / 3 direction-right-mechanism-refined / 0 reversed).


### CONFIG METHOD — declarative config.json vs imperative API-seeding
**verdict: suboptimal** (confidence high)  
Bifrost's canonical config mechanism is a declarative `config.json` placed in the app
directory, which in Docker is the volume mounted at `/app/data` (`docker run -p 8080:8080 -v
$(pwd)/data:/app/data maximhq/bifrost`; "the volume you mount will be used as the app-dir",
and "the container automatically discovers and loads this configuration on startup"). One file
holds EVERYTHING: top-level keys `providers`, `governance`, `config_store`, `client`, `mcp`,
`plugins`, `logs_store`, etc. (confirmed against the published JSON Schema at
https://www.getbifrost.ai/schema). Providers + keys: `providers.<name>.keys[]` where each key
is `{name, value, models[], weight, ...}`. Secrets use the `env.` prefix: `"value":
"env.OPENAI_API_KEY"` — "Never put secrets directly in config.json. Use the env. prefix to
reference any environment variable." `models: ["*"]` allows all; empty array is deny-by-
default. Custom / OpenAI-compatible providers (our NVIDIA NIM, Cloudflare, GitHub Models,
SambaNova, DeepSeek, Z.ai, opencode-zen): the schema's `$defs/custom_provider_config` ALLOWS
`base_provider_type` (enum incl. "openai"), `request_path_overrides` (request-type→path map),
`allowed_requests`, `is_key_less`; and `$defs/network_config` allows `base_url`,
`extra_headers` (string map), `default_request_timeout_in_seconds`, `max_retries`, etc. These
attach directly under `providers.<name>` in config.json — i.e. every field seed.sh POSTs
imperatively is expressible declaratively. Routing/governance: the `governance` block "lets
you seed all governance resources directly in config.json. On startup, Bifrost loads these
into the configuration store." It contains `routing_rules[]` (each `{name, enabled,
cel_expression, targets:[{provider,model,weight}], fallbacks[], scope, priority}` — identical
shape to our snapshot/routing-rules.json) and `virtual_keys[]`. Startup reconciliation (the
key GitOps property): with `config_store.enabled: true`, Bifrost does content-hash
reconciliation per entity — empty DB bootstraps from config.json; on an existing DB, entities
whose file-hash CHANGED overwrite the DB copy, unchanged entities keep DB state, and UI/API-
only additions are preserved. So config.json is a true declarative source of truth that re-
applies on restart without wiping out-of-band edits.

_minimal-core:_ For the 'bifrost + pools + env' minimal core, the cleanest correct shape is: ONE git-tracked `bifrost/config.json` + `docker-compose.yml` + `.env`, dropping seed.sh, sync_key_models.py, apply_snapshot.py, snapshot.py, and (optionally) apply_pool_weights.py. config.json structure: - `$schema: "https://www.getbifrost.ai/schema"` - `config_store: {enabled: true, type: sqlite, config:{path: "./config.db"}}` (keeps UI live + enables per-entity hash reconciliation on restart) - `providers: { groq:{keys:[{name:"groq-1", value:"env.GROQ_API_KEY", models:["*"], weight:1}]}, gemini:{...}, mistral:{...},

_corrections:_
- **[HIGH]** The entire seed.sh + sync_key_models.py + apply_snapshot.py 'dance' re-implements, imperatively over HTTP, exactly what Bifrost's declarative config.json does natively. seed.sh even documents fighting — _fix:_ Author one `bifrost/config.json` (committed to git, secrets via `env.` prefix). Mount it into the existing `./bifrost/data:/app/data` volume. Set `config_store.enabled: true` so the UI stays live and 
- **[HIGH]** Routing rules are split across TWO out-of-band sources (bifrost/snapshot/routing-rules.json applied by apply_snapshot.py, and scripts/runtime/pool_weights.yaml applied by apply_pool_weights.py), neith — _fix:_ Move the routing rules into `governance.routing_rules[]` in config.json — the shape is identical to our snapshot/routing-rules.json (name/enabled/cel_expression/targets/fallbacks/scope/priority). The 
- **[MEDIUM]** Per-key secrets are passed as literal raw key strings into the API (seed.sh simple_key() embeds `$GROQ_API_KEY` into `value:{value:$k,...}`), and custom-provider keys are even embedded into `extra_hea — _fix:_ In config.json use `value: "env.GROQ_API_KEY"` etc. The schema confirms `value` 'can use env. prefix'. For custom providers that need the key in a header, prefer the native key value over hand-rolled 
- **[MEDIUM]** sync_key_models.py's deny-by-default workaround sets each key's `models` to the exact union of models referenced by rules. config.json keys default `models` to deny-all when empty — but you can also j — _fix:_ Decide intent: if per-key model allow-listing is wanted, declare `models: [...]` inline per key in config.json (atomic, no sync pass). If not, use `models: ["*"]`. Either way sync_key_models.py is unn
- **[LOW]** config.json does NOT support a `blacklisted_models` field (schema uses `models` as an allow-list only), whereas seed.sh's simple_key() sets `blacklisted_models:[]`. Minor, but worth knowing when porti — _fix:_ When migrating, convert any blacklist intent into an explicit `models[]` allow-list. The empty `blacklisted_models:[]` we currently send is a no-op and simply drops in config.json.
- **[INFO]** Without config_store enabled the UI is disabled and config becomes read-only requiring restart-to-change. If you adopt file-config you must keep `config_store.enabled: true` to retain the dashboard/UI — _fix:_ Always set `config_store: {enabled: true, type: sqlite, config: {path: ./config.db}}` in config.json so UI + hash-reconciliation both work; the DB still lives in the mounted /app/data volume.

_sources:_ https://docs.getbifrost.ai/quickstart · https://docs.getbifrost.ai/quickstart/gateway/setting-up · https://docs.getbifrost.ai/deployment-guides/config-json · https://docs.getbifrost.ai/deployment-guides/config-json/governance · https://docs.getbifrost.ai/deployment-guides/config-json/providers · https://www.getbifrost.ai/schema

### PROVIDERS + KEYS schema/API (provider object, key object, per-key models allow-list + weight, multiple keys, /api/providers vs /api/providers/{name}/keys subresource, PUT-drops-keys finding)
**verdict: suboptimal** (confidence high)  
Bifrost models providers as a map keyed by provider name; each provider holds a `keys` array.
A key is the `schemas.Key` Go struct (core/schemas/account.go): `{ id, name, value(EnvVar),
models(WhiteList), blacklisted_models(BlackList), weight(float64), aliases, enabled,
use_for_batch_api, <provider-specific *KeyConfig> }`. The declarative/recommended way is a
single config.json at the app-dir root (the mounted Docker volume) using env references and a
wildcard models list: { "$schema": "https://www.getbifrost.ai/schema", "config_store": {
"enabled": true }, "providers": { "openai": { "keys": [ { "name":"openai-primary",
"value":"env.OPENAI_API_KEY", "models":["*"], "weight":1.0 } ] } } } On an empty DB, Bifrost
"Bootstraps database with config.json settings, then uses DB for runtime reads"; on subsequent
runs it reconciles per-entity by ConfigHash (UI/API edits survive). The runtime HTTP API
mirrors the struct: keys are a dedicated subresource — POST/GET/PUT/DELETE
/api/providers/{provider}/keys[/{id}] — and the API `value` field is the EnvVar object {value,
env_var, from_env}. The `models` field is a WhiteList with explicit semantics: '"*" alone =
all allowed; Empty list = NOTHING allowed; non-empty (no "*") = only those listed.'
IsAllowed(v) == IsUnrestricted() || Contains(v).

_minimal-core:_ Single declarative ./bifrost/data/config.json (app-dir = the volume we already mount) is the cleanest correct shape: { "$schema":"https://www.getbifrost.ai/schema", "config_store":{"enabled":true}, "providers":{ "groq":{"keys":[{"name":"groq-1","value":"env.GROQ_API_KEY","models":["*"],"weight":1}]}, "gemini":{"keys":[{"name":"gemini-1","value":"env.GEMINI_API_KEY","models":["*"],"weight":1}]}, ... (custom/openai-compat providers keep their network_config + custom_provider_config blocks exactly as seed.sh builds them today, just declared here), }, "governance":{"routing_rules":[ ...the 3 omo p

_corrections:_
- **[HIGH]** seed.sh creates every key with models:[] (empty WhiteList). Per core/schemas/account.go the empty WhiteList means 'nothing is allowed' (deny-all), so on a fresh seed ZERO models can route until a seco — _fix:_ In simple_key() set models:["*"] instead of models:[]. This is exactly what the official minimal config and the maximhq OpenAI/Bedrock example keys use. With ["*"] every model the provider can serve i (`/home/jkr/Repos/coire-ansic/bifrost/seed.sh (simple_key, line ~88)`)
- **[HIGH]** sync_key_models.py is a workaround for the models:[] bug, not a Bifrost best practice. Maintaining a per-key allow-list that mirrors the union of routing-rule targets/fallbacks is redundant book-keepi — _fix:_ Switch keys to models:["*"] and DELETE sync_key_models.py from the minimal core. Let routing rules be the single source of truth for which provider+model combos are reachable. Keep an explicit per-key (`/home/jkr/Repos/coire-ansic/bifrost/sync_key_models.py`)
- **[MEDIUM]** Entire provider+key bring-up is imperative (curl POST loops, idempotency probes, PUT-drops-keys gymnastics) when Bifrost natively supports declarative config.json bootstrap from the app-dir. The app-d — _fix:_ For the 'bifrost + pools + env' minimal core, replace seed.sh + sync_key_models.py + apply_snapshot.py with ONE declarative ./bifrost/data/config.json containing providers (keys: value:'env.GROQ_API_K (`/home/jkr/Repos/coire-ansic/docker-compose.yml + /home/jkr/Repos/coire-ansic/bifrost/seed.sh`)
- **[LOW]** Secrets handling: seed.sh injects the RAW key string into the API (value.value=<raw>, from_env:false). Functional, but config.json's recommended pattern is value:'env.VAR' / from_env:true so the gatew — _fix:_ If staying imperative, set value:{value:'', env_var:'GROQ_API_KEY', from_env:true}. If moving to config.json, use 'value':'env.GROQ_API_KEY'. Either keeps raw secrets out of the persisted store / snap (`/home/jkr/Repos/coire-ansic/bifrost/seed.sh (simple_key, line ~88)`)
- **[INFO]** The seed.sh comment 'PUT /api/providers ... IGNORES keys' is treated as a quirk to route around. It is actually correct/by-design: keys are a separate subresource (POST/PUT .../keys/{id}), and PUT /pr — _fix:_ No code change needed — keep using the /keys subresource. Just reframe the comment from 'silently drops (bug-like)' to 'by design: keys are a separate subresource'. Confirms our handling is correct. (`/home/jkr/Repos/coire-ansic/bifrost/seed.sh (post_provider comment, lines 36-41)`)

_sources:_ https://github.com/maximhq/bifrost/blob/main/core/schemas/account.go · https://docs.getbifrost.ai/deployment-guides/config-json · https://docs.getbifrost.ai/quickstart/gateway/setting-up · https://bifrost.mintlify.app/api-reference/providers/update-a-key-for-a-provider · https://github.com/maximhq/bifrost/blob/main/examples/configs/withvirtualkeys/config.json · https://docs.getbifrost.ai/quickstart

### Routing Rules + CEL + the "pool" concept (governance routing rules as virtual-model cascades)
**verdict: matches-best-practice** (confidence high)  
Bifrost has NO native "pool"/"cluster" object. The intended way to make a virtual model name
resolve to ordered, weighted, cross-provider targets with a fallback cascade is exactly what
we do: a governance routing rule whose cel_expression matches the virtual name and whose
targets[]/fallbacks[] define the cascade. Bifrost calls this a "dynamic alias." Per the
maintainer (Akshaydeo, Discussion #945): aliasing comes in two forms — "Static Aliases: Simple
one-to-one mappings (e.g. gpt-4-prod -> openai/gpt-4.1-mini)" and "Dynamic Aliases: Rule-based
routing using CEL expressions that evaluated against request context at runtime," and "Dynamic
aliases use Google's CEL library." The aliasing-models doc states verbatim: "Dynamic aliasing
uses Routing Rules to rewrite the model at request time based on a CEL expression." Released
in v1.5.0-prerelease2 (April 2026). Authoritative request schema (api-
reference/governance/create-routing-rule): required = name, cel_expression, scope (enum
global|team|customer|virtual_key), priority (lower=higher), targets (minItems 1; each weight>0
and "all target weights in a rule must sum to 1", provider/model nullable, key_id nullable);
optional = description, enabled, fallbacks ("array of strings, Fallback providers in format
provider/model"), scope_id (required if scope != global). So `model == "coire-main"` ->
weighted targets + provider/model fallbacks IS the correct, documented primitive.

_minimal-core:_ Keep the routing-rule-as-dynamic-alias approach unchanged — it is the canonical Bifrost primitive and matches the documented schema precisely. For the bifrost+pools+env minimal cut: (1) Treat pool_weights.yaml as the single source of truth (virtual-name -> targets[] + fallbacks[]); apply_pool_weights.py renders each pool into one POST /api/governance/routing-rules with cel_expression `model == "<name>"`, weighted targets summing to 1, and "provider/model" fallbacks. This is exactly right. (2) Drop chain_rule from the CREATE/PUT body (not in the documented schema; default behavior is what we wa

_corrections:_
- **[INFO]** Naming/mental model: code+files call these 'pools' but Bifrost has no native pool object. The construct we build IS Bifrost's 'dynamic alias' (a routing rule). Calling it a pool is fine internally but — _fix:_ In the minimal-core README/comments, state explicitly: 'a coire pool == one Bifrost governance routing rule acting as a dynamic alias (model==NAME -> weighted targets + provider/model fallbacks). Bifr
- **[LOW]** apply_pool_weights.py sends chain_rule implicitly via snapshot but the authoritative create-routing-rule request schema does NOT list chain_rule, and the example config omits it; it only exists in v1. — _fix:_ chain_rule:false is harmless (it is the default and we never chain). Keep it out of the CREATE body to match the documented schema, or pin the Bifrost image to a tag known to accept it. Not a bug toda
- **[INFO]** Single-target pools (each rule has exactly one weight-1 target; the cascade lives entirely in fallbacks[]). This is valid, but it means weighted load-balancing across primaries is unused — all balanci — _fix:_ This is a deliberate design choice (deterministic primary + ordered failover) and is fully supported. No change required. If you ever want to spread load across 2-3 equally-good primaries to conserve 
- **[INFO]** Aliasing multiple names to one rule: if the minimal core wants e.g. both 'coire-main' and a legacy name to hit the same cascade, today you would need two separate rules. — _fix:_ CEL list membership is supported: use cel_expression `model in ["coire-main","coire-default"]` in a single rule. Confirmed by the governance doc quoting `headers["x-tier"] in ["staging","testing"]` as
- **[INFO]** Static aliases were NOT considered as an alternative; a reviewer stripping to minimal core might wrongly think static aliases (the simpler 'aliases' map on a key) could replace these rules. — _fix:_ Document that static aliases are flat 1:1 string->string maps on a provider key and CANNOT carry weights or fallbacks, so they cannot express the cross-provider cascade. Routing rules (dynamic aliases
- **[INFO]** Free-tier intent vs unique-priority handling: apply_pool_weights assigns priority=max+1 for new rules to avoid 'priority already exists', but priority only matters for evaluation ORDER among rules who — _fix:_ Keep the max+1 collision-avoidance (it prevents POST 500s), but note in comments that inter-pool priority is cosmetic here because the model==NAME predicates are disjoint. Reduces confusion in the min

_sources:_ https://docs.getbifrost.ai/providers/routing-rules · https://docs.getbifrost.ai/providers/aliasing-models · https://docs.getbifrost.ai/api-reference/governance/create-routing-rule.md · https://docs.getbifrost.ai/features/governance · https://docs.getbifrost.ai/providers/provider-routing · https://github.com/maximhq/bifrost/discussions/945

### Cascade / Fallback / Load-Balancing / Resilience (native Bifrost capabilities vs our hand-rolled approach)
**verdict: suboptimal** (confidence high)  
Bifrost (OSS) gives you three stateless, per-request resilience primitives and reserves
stateful health-based circuit-breaking for Enterprise: 1. AUTOMATIC RETRIES (OSS, per-
provider, OFF by default). Configured in each provider's `network_config`: `max_retries`
(default `0` = no retries), `retry_backoff_initial` (default 500ms), `retry_backoff_max`
(default 5000ms). Triggers on transient errors: network failures, 5xx, and 429. Retries hit
the SAME provider with exponential backoff. (docs.getbifrost.ai/features/retries-and-
fallbacks) 2. AUTOMATIC FALLBACKS (OSS, sequential, deterministic). An ordered `fallbacks:
["provider/model", ...]` list. "If the primary fails (and the error is retryable at the
provider level)" after exhausting its retry budget, Bifrost tries each fallback in order; each
fallback gets its OWN full retry budget; each fallback runs the full plugin chain again; first
success wins. NOT weighted/probabilistic — strict priority order. Can be supplied per-request
OR baked into a governance routing rule's `fallbacks` field.
(docs.getbifrost.ai/features/retries-and-fallbacks, /providers/routing-rules) 3. WEIGHTED LOAD
BALANCING via governance routing-rule `targets` (OSS). "When multiple targets are defined, one
is selected probabilistically at request time." Weights must sum to 1; selection is
proportional. Also key-level: with `max_retries > 0` (v1.5.0+) Bifrost rotates to a fresh key
in the same provider on a 429. (docs.getbifrost.ai/providers/routing-rules,
/features/governance/routing) 4. ADAPTIVE LOAD BALANCING = the real circuit breaker, and it is
ENTERPRISE-ONLY. It tracks four route health states (Healthy <2% err, Degraded =2%, Failed >5%
err or throughput cap, Recovering), recalculates weights every 5s from error-penalty (50%,
time-decayed) + latency (20%) + utilization (5%) + momentum, and includes "Circuit Breaker
Integration" that "temporarily removes poorly performing keys from rotation" then auto-
restores. Docs: "Contact your Bifrost Enterprise representative to enable adaptive load
balancing." (docs.getbifrost.ai/enterprise/adaptive-load-balancing). The OSS version has NO
memory of past failures — every request re-walks the same retry->fallback chain from the top,
re-probing a dead primary each time.

_minimal-core:_ For the bifrost+pools+env minimal cut, the cleanest correct shape is: 1. Keep the native model: governance routing-rules where each pool = one rule with `cel_expression: model == "<pool>"`, weighted `targets` (sum=1), and an ordered `fallbacks` list. This IS Bifrost's documented cascade primitive — do not hand-roll failover. (Verdict: our fallback-list approach is correct and idiomatic.) 2. ADD `network_config.max_retries` (1-2) + backoff per provider in seed.sh. This is the biggest free win: absorbs transient 429/5xx in-provider and unlocks native multi-key rotation on 429 (v1.5.0+). Currentl

_corrections:_
- **[HIGH]** MISCHARACTERIZATION: dashboard/app.py (lines 4-5, 173-174) claims the deleted circuit-breaker daemon was redundant because 'bifrost's built-in cascade handles failover natively.' This conflates two di — _fix:_ Correct the comment to: 'CB removed 2026-05-27; we accept stateless per-request cascade. Bifrost OSS does NOT demote unhealthy providers (that is Enterprise Adaptive LB only) — a dead primary is re-pr
- **[HIGH]** RETRIES LIKELY OFF: Bifrost defaults `max_retries: 0` (no retries at all, and no multi-key rotation). I found no `network_config`/`max_retries` set in seed.sh or our config. With retries off, a single — _fix:_ Set `network_config.max_retries` (e.g. 1-2) with `retry_backoff_initial`/`retry_backoff_max` per provider during seed.sh, especially for multi-key providers. This absorbs transient 429/5xx in-provider
- **[MEDIUM]** WEIGHTED TARGETS UNUSED: every pool has exactly ONE target at weight 1.0; all spreading is done via the ordered `fallbacks` list. That means we get ZERO native load-balancing — 100% of traffic always  — _fix:_ If the intent is to spread load across several healthy free providers (not just failover), promote the top 2-3 fallbacks into weighted `targets` (e.g. 0.4/0.3/0.3). normalize() already enforces sum=1.
- **[LOW]** MINIMAL-CORE SCOPE: the task frames the minimal core as 'bifrost + pools + env from .env'. apply_pool_weights.py is reasonable glue, but it reimplements provider-existence filtering, priority allocati — _fix:_ For the minimal core, the routing rules (with weighted targets + fallbacks) are the durable artifact and could be applied directly from a single snapshot/JSON via apply_snapshot.py at boot, treating p
- **[INFO]** Fallback chain depth cost: omo-main has 12 fallbacks. Docs state each fallback 're-runs the full plugin chain' and gets 'its own full retry budget.' On a bad day a request can walk all 12 (x retry bud — _fix:_ Keep the deep list (free-tier resilience justifies it) but be aware tail latency is bounded by sum of (per-hop timeout x retries). Ensure per-provider timeouts are tight so the cascade fails fast thro

_sources:_ https://docs.getbifrost.ai/features/retries-and-fallbacks · https://docs.getbifrost.ai/features/fallbacks · https://docs.getbifrost.ai/providers/routing-rules · https://docs.getbifrost.ai/features/governance/routing · https://docs.getbifrost.ai/enterprise/adaptive-load-balancing · https://docs.getbifrost.ai/enterprise/intelligent-load-balancing

### AUTH + ENDPOINTS + how clients CONNECT (Bifrost gateway)
**verdict: suboptimal** (confidence high)  
Bifrost (default port 8080) exposes TWO families of inference routes, all served from the same
gateway and all subject to the same provider config + governance routing: (A) Unified OpenAI-
compatible route: POST /v1/chat/completions. The `model` field is "provider/model" (e.g.
"openai/gpt-4o-mini") OR a bare name ("gpt-4o-mini") that Bifrost resolves via the Model
Catalog (queries each provider's /v1/models + remote pricing map). Quote (setting-up): `curl
-X POST http://localhost:8080/v1/chat/completions ... "model": "openai/gpt-4o-mini"` and
"Bifrost will automatically resolve the provider via the Model Catalog."
[docs.getbifrost.ai/quickstart/gateway/setting-up] (B) Drop-in SDK routes (README): OpenAI ->
base_url `http://localhost:8080/openai` (path /openai/v1/chat/completions, also works without
/v1 as /openai/chat/completions); Anthropic -> base_url `http://localhost:8080/anthropic`
(path /anthropic/v1/messages, native Anthropic body, model e.g. "claude-3-opus-20240229");
Google GenAI -> api_endpoint `http://localhost:8080/genai`. [github.com/maximhq/bifrost
README; docs api-reference/openai-integration/create-chat-completion-openai-format.md;
.../anthropic-integration/create-message-anthropic-format.md] AUTH MODEL (two distinct
planes): - Admin/management plane: username+password Basic auth, login at POST
/api/session/login (returns 30-day session token); status via GET /api/session/is-auth-
enabled. Enabling auth protects the dashboard + admin API. [docs .../session/login.md,
.../session/is-auth-enabled.md; quickstart/gateway/setting-up-auth] - Inference plane: OPEN BY
DEFAULT. "By default governance is optional, meaning that if the virtual key header is not
present, the request will be allowed but without any governance checks/routing." Client auth
on inference is enabled only by `client.enforce_auth_on_inference: true` (config.json) or the
UI's inverse "Disable authentication on inference calls" toggle / governance.auth_config.
[docs features/governance/virtual-keys] - When inference auth IS enforced, the accepted
credentials are: a virtual key `sk-bf-*` via header `x-bf-vk`, `Authorization: Bearer`,
`x-api-key`, or `x-goog-api-key`; OR admin Basic auth (username:password) / Bearer =
base64("username:password"). There is NO concept of a single arbitrary "master gateway API
key" string. [docs features/governance/virtual-keys; quickstart/gateway/setting-up-auth]
Routing rules apply globally across ALL inference endpoints (scope "global", no virtual key
required); targets are weighted (sum to 1, picked probabilistically), fallbacks are
"provider/model" strings tried on failure. [docs api-reference/governance/create-routing-
rule.md]

_minimal-core:_ Cleanest correct shape for AUTH+ENDPOINTS in a bifrost+pools+env minimal cut: 1. Expose Bifrost on loopback: ports "127.0.0.1:4001:8080" (default container port 8080). Set BIFROST_PASS (admin Basic auth) to protect the dashboard + /api/* management plane only. 2. Leave inference auth at Bifrost's default (open) — do NOT invent BIFROST_API_KEY. Remove it from .env.example and install.sh's required gate. Clients on localhost reach the router with no credential. If LAN exposure is needed later, flip client.enforce_auth_on_inference=true and mint a real virtual key (sk-bf-*). 3. Clients connect DI

_corrections:_
- **[HIGH]** BIFROST_API_KEY is presented as the 'master key clients use to talk to Bifrost' (.env.example) and required by install.sh (install.sh:61), but Bifrost has NO such concept. Bifrost recognizes only (a)  — _fix:_ Either (A) for the minimal cut, drop BIFROST_API_KEY entirely, document that inference is unauthenticated on the loopback/LAN, and rely on admin auth (BIFROST_PASS) only for the management API; or (B)
- **[HIGH]** README documents BIFROST_API_KEY as a Bearer credential (README.md:150) implying client requests are authenticated. With Bifrost's default (governance optional / no enforce_auth_on_inference), :4001 i — _fix:_ For minimal core: bind Bifrost to 127.0.0.1:4001:8080 (loopback) like strip-shim/searxng already are, OR enable client.enforce_auth_on_inference=true + a virtual key. Correct the compose comment which
- **[MEDIUM]** For a 'minimal core = bifrost + pools + env', the strip-shim sits in the client path (clients point at :4002, README.md:149,167). The shim is NOT required to reach the free providers: OpenAI-compat cl — _fix:_ Minimal core: point clients straight at Bifrost :4001 (/v1 for OpenAI-compat, /anthropic for Claude Code). Keep strip-shim as an OPTIONAL profile for the providers that actually need it (Mistral tool_
- **[INFO]** Our virtual model routing uses GOVERNANCE routing-rules (cel_expression model=='omo-main'), which is valid and global, but Bifrost also ships a first-class 'Model Aliases' feature (static + dynamic, c — _fix:_ Keep governance routing-rules for the pools (they support weighted targets + fallbacks, which aliases do not). Document why: aliases can't express fallback chains. No change needed beyond noting it.
- **[LOW]** We rely on the runtime admin API (config_store / empty data dir) for ALL config, driven by seed.sh + apply_snapshot + apply_pool_weights. Bifrost's documented/recommended source-of-truth for a reprodu — _fix:_ For 'reproducible free-tier router from .env', consider generating a single config.json (providers with keys as env.VAR refs, client.enforce_auth_on_inference, governance routing rules) and mounting i

_sources:_ https://docs.getbifrost.ai/quickstart/gateway/setting-up · https://github.com/maximhq/bifrost · https://docs.getbifrost.ai/api-reference/openai-integration/create-chat-completion-openai-format.md · https://docs.getbifrost.ai/api-reference/anthropic-integration/create-message-anthropic-format.md · https://docs.getbifrost.ai/features/governance/virtual-keys · https://docs.getbifrost.ai/quickstart/gateway/setting-up-auth


## Appendix D — provider/model behavior matrix (cited)


### Tool-calling support matrix across all 13 free-tier providers (CoireAnsic / Bifrost + stri
Across the 13 providers, tool-calling falls into three buckets. (1) NATIVE OpenAI tool_calls
JSON — the large majority: Cerebras, Gemini (OpenAI-compat endpoint), Mistral, NVIDIA-NIM,
OpenRouter (it normalizes ALL underlying providers to OpenAI tool_calls), SambaNova, Groq,
Cohere (compatibility API), GitHub-Models, opencode-zen, Z.ai/Zhipu (paas v4), DeepSeek-
direct, and Cloudflare's "traditional" function-calling path. Native here means model behaves
AND the host's parser populates tool_calls. (2) NON-NATIVE / LEAKY formats that the shim must
rescue: Kimi K2.x control-tokens (OLD `functions.NAME:idx` from Cloudflare/Moonshot vs NEW
hex-id from NIM needing name-inference) → normalize_kimi_tool_calls; Qwen <tool_call> XML when
a host's vLLM tool-parser is misconfigured/off → normalize_qwen_tool_calls; bare-JSON
`{"name":..,"arguments":..}` from small Llama-class models → normalize_json_tool_call. (3)
TEXT-ONLY / BROKEN — model narrates instead of emitting any callable structure: nvidia-nim
nemotron-super-49b (NVIDIA's own NIM function-calling doc does NOT list nemotron among tool-
call-supporting models — corroborates the empirical TEXT-ONLY verdict),
sambanova/DeepSeek-V3.1, opencode-zen/qwen3.6-plus + minimax-m2.5, cerebras/llama3.1-8b, and
cloudflare/@cf/moonshotai/kimi-k2.6 (reasoning-text-only freeze). The dominant nuance — and
the whole reason pool composition matters — is that the SAME model differs by host: Kimi K2.6
works (after shim normalization) on NIM but is reasoning-only-broken on Cloudflare; DeepSeek
tool-calling depends on the host's V-version (V3.2 ok on SambaNova, V3.1 broken on SambaNova
even though DeepSeek-direct V3.2/V4-flash are fine); Qwen XML-leak depends on whether the host
enabled the qwen3_xml/coder parser. The shim already covers the three non-native formats plus
a reasoning-only-no-action retry and the NIM 'unhashable type' parallel_tool_calls retry; the
only systemic gap is detection — there is no automated TEXT-ONLY regression guard, so broken
targets are caught only by the manual probe.py and benched by hand.

| provider | model | fact | shim | conf | source |
|---|---|---|---|---|---|
| cerebras | zai-glm-4.7 | Native OpenAI tool_calls; Cerebras supports tool-use across GLM/Qwen/Llama/gpt-oss with strict-mode schema. Empirically TOOLCALL-ok; in omo-main primary slot. | n/a | high | https://inference-docs.cerebras.ai/capabilities/tool-use ; our-file:sc |
| cerebras | qwen-3-235b-a22b-instruct-2507 | Native tool_calls work; but model REJECTS reasoning_effort (bifrost wraps as generic 400). Shim pre-strips/post-retries the param. Tool-calling itself is fine. | yes | high | our-file:strip-shim/app.py:543 (_RE_REJECTERS) ; pool_weights.yaml:54 |
| cerebras | llama3.1-8b | TEXT-ONLY / broken tools (small Llama on Cerebras narrates, no tool_calls). Excluded from pools. | no | high | our-memory:project_provider_tool_calling_matrix (probe 2026-05-22) |
| cloudflare | @cf/moonshotai/kimi-k2.6 | BROKEN for agentic use: emits reasoning-text ('I should call Oracle now') with finish_reason=stop and NO tool_call structure — reasoning-only freeze. Fallback-o | partial | high | our-memory:project_cloudflare_kimi_tool_call_issue ; project_kimi_reas |
| cloudflare | @cf/moonshotai/kimi-k2.x (when i | When Kimi does emit, Cloudflare leaks OLD control-token format </tool_call_begin/>functions.NAME:idx</tool_call_argument_begin/>{json}</tool_call_end/> into con | yes | high | our-file:strip-shim/app.py:304 (_KIMI_TC_OLD), :356 |
| cloudflare | @hf/nousresearch/hermes-2-pro-mi | Cloudflare 'traditional' function calling returns a tool_calls array (name+arguments) — native-shaped. Hermes-2-pro is the doc's canonical FC model. | n/a | high | https://developers.cloudflare.com/workers-ai/features/function-calling |
| gemini | gemini-3-flash-preview / gemini- | Native OpenAI tool_calls via OpenAI-compat endpoint (generativelanguage.../v1beta/openai). Empirically TOOLCALL-ok. Caveat: endpoint rejects `store` and `stream | yes | high | https://ai.google.dev/gemini-api/docs/openai ; our-file:strip-shim/app |
| mistral | mistral-large-2512 / mistral-med | Native tool_calls; supports parallel tool calls. Empirically TOOLCALL-ok (large 0.6s, medium 0.33s). Requires 9-char alphanumeric tool_call_id (^[a-zA-Z0-9]{9}$ | yes | high | https://docs.mistral.ai/capabilities/function_calling ; our-file:strip |
| nvidia-nim | moonshotai/kimi-k2.6 | NIM serves Kimi; emits NEW control-token format </tool_call_begin/>HEX_ID</...> with NO function name — shim infers name from JSON keys vs request tools[]. Also | yes | high | our-file:strip-shim/app.py:309 (_KIMI_TC_NEW), :320 (_infer_function_n |
| nvidia-nim | llama-3.3-nemotron-super-49b | TEXT-ONLY / broken tools (4.8s, narrates). CORROBORATED: NVIDIA's NIM function-calling doc lists Llama/Mistral as FC-supported but does NOT list Nemotron. Exclu | no | high | https://docs.nvidia.com/nim/large-language-models/1.7.0/function-calli |
| nvidia-nim | z-ai/glm-5.1, deepseek-v4-pro | Native tool_calls (GLM/DeepSeek on NIM are FC-capable per NIM coverage). glm-5.1 on NIM is SLOW (~66s) — last-resort only. deepseek-v4-pro needs enable_thinking | yes | medium | our-file:strip-shim/app.py:798 ; pool_weights.yaml:64 ; https://docs.n |
| openrouter | * (deepseek-v4-flash:free, z-ai/ | OpenRouter NORMALIZES tool calling to OpenAI tool_calls across ALL underlying providers — response shape is uniform regardless of upstream. Per-model support di | n/a | high | https://openrouter.ai/docs/guides/features/tool-calling ; pool_weights |
| sambanova | DeepSeek-V3.2 | Native tool_calls, TOOLCALL-ok (2.0s). 20 RPD hard cap, 32k ctx. KEEP. | n/a | high | our-memory:project_provider_tool_calling_matrix ; https://docs.sambano |
| sambanova | DeepSeek-V3.1 | TEXT-ONLY / broken tools on SambaNova specifically (1.8s, no tool_calls) even though SambaNova docs list V3.1 for JSON-mode. EXCLUDE — same DeepSeek family diff | no | high | our-memory:project_provider_tool_calling_matrix ; our-file:dashboard/a |
| sambanova | Llama-4-Maverick-17B-128E-Instru | Native tool_calls, TOOLCALL-ok (1.15s) but ~1370 arena (low IQ for omo). Usable text/fallback. | n/a | high | our-memory:project_provider_tool_calling_matrix |
| groq | llama-3.3-70b-versatile | Native tool_calls (Groq returns tool_calls array; Llama-3-Groq-Tool-Use topped BFCL). TOOLCALL-ok 0.24s BUT 12k TPM cap = too small for omo handoffs → DROPPED f | n/a | high | https://console.groq.com/docs/tool-use/overview ; pool_weights.yaml:19 |
| cohere | command-a-03-2025 | Native tool_calls via Cohere compatibility API (OpenAI SDK at /compatibility/v1). Command-A is strong at tool use. Quirks: 422 on developer role (shim coerces d | yes | high | https://docs.cohere.com/docs/compatibility-api ; our-file:strip-shim/a |
| cohere | command-r / command-a-vision | Tool use supported; same compatibility-API native tool_calls. command-r and command-a-vision in _THINKING_REJECTERS (reject thinking param) — shim handles. | yes | medium | https://docs.cohere.com/docs/command-r ; our-file:strip-shim/app.py:54 |
| github-models | openai/gpt-4.1, deepseek, llama  | Native OpenAI tool_calls (Azure inference, OpenAI-compat). MAJOR CAVEAT for tool pools: free tier caps 8000 tokens INPUT / 4000 OUTPUT per request — too small f | n/a | high | https://docs.github.com/en/rest/models/inference ; https://github.com/ |
| opencode-zen | deepseek-v4-flash-free / nemotro | Native tool_calls, TOOLCALL-ok (deepseek 1.76s, nemotron-3-super 3.36s). Pooled demo ~5-10/day. Note nemotron-3-super-free WORKS (distinct from nvidia-nim nemot | n/a | high | our-memory:project_provider_tool_calling_matrix ; https://opencode.ai/ |
| opencode-zen | qwen3.6-plus-free | BROKEN today: 500 system error AND no tool_calls (TEXT-ONLY). EXCLUDE from tool pools. | no | high | our-memory:project_provider_tool_calling_matrix |
| opencode-zen | minimax-m2.5-free | TEXT-ONLY / broken tools (0.36s). EXCLUDE. (Also collects data during free period.) | no | high | our-memory:project_provider_tool_calling_matrix ; https://opencode.ai/ |
| zai | glm-4.7-flash / glm-4.5-flash | Native tool_calls; GLM-4.7 OpenAI-compat tool format, τ²-Bench 87.4 (top open tool-use). Reached via shim Z.ai path-rewrite proxy (/zai-proxy/v4). Free tier ~2  | yes | high | https://docs.z.ai/guides/llm/glm-4.7 ; our-file:bifrost/seed.sh:216 ;  |
| deepseek | deepseek-chat (V4-flash non-thin | Native tool_calls; supports strict-mode schema; V3.2+ supports tool use IN thinking mode. NOTE deepseek-chat/reasoner deprecate 2026-07-24 → map to deepseek-v4- | n/a | high | https://api-docs.deepseek.com/guides/tool_calls ; our-file:bifrost/see |
| (any host) | Qwen family (qwen3-next-80b, qwe | Qwen uses Hermes-style <tool_call> XML internally. If the host's vLLM tool-parser (qwen3_xml/qwen3_coder) is OFF/misconfigured, raw XML LEAKS into content inste | yes | high | https://docs.vllm.ai/en/latest/features/tool_calling/ ; https://github |
| (small Llama-class hosts) | various | Some small models post a bare JSON object {"name":..,"arguments"/"parameters":..} in content instead of tool_calls. Shim normalize_json_tool_call lifts it. Defe | yes | medium | our-file:strip-shim/app.py:415 |

_rec:_ TOOL pool (coire-main / omo-main) membership rule: include ONLY native-OR-shim-rescued + adequate context. Confidently keep: cerebras/zai-glm-4.7, cerebras/qwen-3-235b, zai/glm-4.7-flash, gemini-3/3.5-flash, mistral-large-2512 + mistral-medium-3.5, sambanova/DeepSeek-V3.2, openrouter/deepseek-v4-flash:free + z-ai/glm-5.1, deepseek-direct, opencode-zen/deepseek-v4-flash + nemotron-3-super. Keep as FALLBACK-ONLY (rescued but flaky): cloudflare/kimi-k2.6 and nvidia-nim/moonshotai/kimi-k2.6 (shim normalizes + reasoning-only retry, but freeze risk), nvidia-nim/z-ai/glm-5.1 (slow 66s last-resort). HARD-EXCLUDE from any tool pool: sambanova/DeepSeek-V3.1, nvidia-nim/llama-3.3-nemotron-super-49b, op

_gaps/unknowns:_
- No automated TEXT-ONLY regression guard: broken targets (DeepSeek-V3.1@SambaNova, nemotron-49b@NIM, qwen3.6-plus/minimax@zen) are only caught by manual probe.py + hand-benching. A model can silently r
- Kimi-on-host divergence is only partially mapped: confirmed broken on Cloudflare (reasoning-text) and quirky-but-rescuable on NIM (NEW hex format + unhashable retry). Direct Moonshot API and Kimi-K2-T
- Cloudflare catalog now advertises Kimi K2.5 with 'multi-turn tool calling' + structured outputs — conflicts with our K2.6 freeze observation. Unverified whether K2.5 (vs K2.6) on Cloudflare actually e
- DeepSeek strict-mode + thinking-mode tool calling (V3.2/V4) changed semantics vs V3.1; whether enabling thinking changes tool emission on NIM-hosted vs deepseek-direct is not separately probed (only d
- GitHub-Models tool format is native but the 8k-in/4k-out free cap is a HARD blocker for omo-main context sizes — never verified whether a paid/org PAT lifts it for this stack.
- normalize_json_tool_call is greedy: any assistant message whose ENTIRE content is a JSON object with a 'name' key gets converted to a tool_call, which could mis-fire if a model legitimately returns JS
- Cohere command-r 'thinking' rejection + tool support is inferred from family membership in _THINKING_REJECTERS, not freshly probed for tool_calls emission via the compatibility API.
- OpenRouter normalizes format but the underlying free model can still be a TEXT-ONLY one for that hour; relying on OR's supported_parameters=tools filter at request time (rather than our static pool li

### PARAMETER + REQUEST quirks per provider (reasoning_effort, thinking, max_tokens ceilings, 
The shim correctly handles the big-ticket, observed rejections: (1) stream_options stripped
universally when stream!=true (fixes nvidia-nim 400); (2) reasoning_effort pre-strip+retry for
cerebras/qwen-3-235b and mistral-* (via _RE_REJECTERS); (3) thinking pre-strip+retry for
cohere command-* (via _THINKING_REJECTERS); (4) developer->system normalization; (5)
deepseek-v4-pro enable_thinking injection (NIM); (6) nvidia 'unhashable type' 500 retry strips
parallel_tool_calls; (7) strip_reasoning removes reasoning_content from request history
(critical for deepseek-reasoner which 400s on it). BUT verification surfaced real gaps. The
single most important correction: the shim treats mistral and (implicitly) cerebras reasoning
targets as if they reject reasoning_effort outright, when in fact the field IS accepted but
only with a restricted value set — mistral accepts only high|none, cerebras/zai-glm-4.7
accepts only none, cerebras/gpt-oss accepts low|medium|high. So low/medium/minimal get 422'd
on mistral and cerebras/zai-glm-4.7 while high would work. The current strip-everything
behavior is functionally safe (request succeeds) but loses reasoning depth unnecessarily where
high was requested. Bigger functional gaps: (a) z.ai native API (proxied as openai-compat)
does NOT use reasoning_effort at all — it needs thinking:{type:enabled|disabled} and rejects
parallel_tool_calls/reasoning_effort; nothing in the shim translates this for the zai targets
in omo-main; (b) cohere rejects parallel_tool_calls (shim never strips it; only nvidia
'unhashable' path removes it reactively); (c) github-models free tier hard-caps 8k input / 4k
output — DEFAULT_OUTPUT_CAP=16384 would exceed the 4k output cap (github-models not currently
pooled, so latent); (d) sambanova rejects frequency_penalty/presence_penalty/seed and
cerebras/zai-glm-4.7 (the omo-main PRIMARY) is NOT in _RE_REJECTERS so its 'none-only'
reasoning_effort constraint has no recovery path. Gemini openai-compat silently ignores
unknown params and maps reasoning_effort->thinking budget, so it is the safest target. NVIDIA
NIM is the most quirk-dense: top-level reasoning_effort (high|max) plus per-model
chat_template_kwargs (enable_thinking for gemma, thinking for kimi-k2.5), and the unhashable-
type 500 bug.

| provider | model | fact | shim | conf | source |
|---|---|---|---|---|---|
| cerebras | qwen-3-235b-a22b-instruct-2507 | reasoning_effort is NOT supported on this model — only gpt-oss-120b and zai-glm-4.7 accept it on Cerebras. Returns 400/param-rejection. Uses max_completion_toke | yes | high | https://inference-docs.cerebras.ai/api-reference/chat-completions ; st |
| cerebras | zai-glm-4.7 | reasoning_effort IS accepted but ONLY value 'none' (to disable reasoning); low/medium/high/minimal are rejected. zai-glm-4.7 is NOT in _RE_REJECTERS, so a clien | no | high | https://inference-docs.cerebras.ai/api-reference/chat-completions ; st |
| cerebras | gpt-oss-120b | reasoning_effort accepts low/medium/high (default medium). max_completion_tokens includes reasoning tokens. Not currently pooled (dropped per pool_weights.yaml  | n/a | high | https://inference-docs.cerebras.ai/api-reference/chat-completions ; sc |
| cerebras | * | Cerebras references max_completion_tokens, not max_tokens. Per-model 30k TPM / 5 RPM / 2400 RPD. Bifrost obscures Cerebras 400 detail as 'provider API error', s | partial | high | https://inference-docs.cerebras.ai/api-reference/chat-completions ; st |
| mistral | mistral-large-2512 | reasoning_effort accepts ONLY 'high'/'none' — low/medium/minimal return HTTP 422. The shim treats mistral-large as a full reasoning_effort REJECTER (pre-strips/ | partial | high | https://docs.mistral.ai/api/ ; https://docs.mistral.ai/studio-api/conv |
| mistral | mistral-medium-3.5 | Same reasoning_effort high/none constraint. temperature recommended 0.0..0.7 (>0.7 unstable). parallel_tool_calls supported (default true). 50 RPM / 50k TPM. Sh | partial | high | https://docs.mistral.ai/api/ ; strip-shim/app.py:545 ; dashboard/app.p |
| mistral | magistral-* | magistral-small/medium reason NATIVELY and REJECT reasoning_effort with 422 (param not needed). NOT in shim's _RE_REJECTERS (only mistral-large/medium/small/cod | no | medium | https://github.com/pydantic/pydantic-ai/issues/5285 ; https://docs.mis |
| mistral | * | Mistral 422s on the OpenAI 'developer' role (shim normalize_roles maps developer->system — correct). max_tokens accepted (integer/null). temperature safe range  | yes | high | https://docs.mistral.ai/api/ ; strip-shim/app.py:197-216 |
| gemini | * | OpenAI-compat layer SILENTLY IGNORES any unknown/unsupported param (parallel_tool_calls, presence/frequency_penalty, developer role). reasoning_effort IS suppor | n/a | high | https://ai.google.dev/gemini-api/docs/openai |
| gemini | gemini-3-flash-preview | Up to 65536 output tokens (shim _POOL_OUTPUT_CAP comment cites gemini-3-flash-preview as the 65536 ceiling). 20 RPM / 250 RPD per flash variant / 250k input TPM | yes | high | strip-shim/app.py:148-153 ; dashboard/app.py:715,814 |
| gemini | gemini-3.1-pro-preview | PAID-ONLY on free tier (free_tier_requests limit:0). Must NOT be pooled. Already removed from omo-gemini. | n/a | high | scripts/runtime/pool_weights.yaml:73-74 ; dashboard/app.py:820 |
| nvidia-nim | * | Uses TOP-LEVEL reasoning_effort (incl high/max) as reasoning activator. Per-model thinking toggle goes in chat_template_kwargs (gemma: enable_thinking:true; kim | partial | high | https://docs.nvidia.com/nim/large-language-models/1.10.0/reasoning-mod |
| nvidia-nim | deepseek-v4-pro | Hangs/empty without chat_template_kwargs.enable_thinking=true. Shim injects enable_thinking+thinking into chat_template_kwargs ONLY for model substring 'deepsee | yes | high | strip-shim/app.py:795-801 |
| nvidia-nim | moonshotai/kimi-k2.6 | Known 'unhashable type: dict' HTTP 500 on parallel_tool_calls + large tools array (non-deterministic). Shim detects 'unhashable type' in 500/502/503 body, strip | yes | high | strip-shim/app.py:871-907,356-412 ; pool_weights.yaml:61 |
| cohere | command-a-03-2025 / command-r /  | reasoning_effort accepts ONLY 'none'/'high' (maps to Cohere thinking). parallel_tool_calls NOT supported (compat docs list as unsupported) — shim does NOT proac | partial | high | https://docs.cohere.com/docs/compatibility-api ; strip-shim/app.py:548 |
| cohere | * | Cohere's CURRENT OpenAI-compat layer DOES accept the 'developer' role (shown in official examples for system instructions). The shim comment claiming cohere 422 | yes | medium | https://docs.cohere.com/docs/compatibility-api ; github.com/cohere-ai/ |
| deepseek | deepseek-reasoner | Reasoning enabled by MODEL NAME, not by reasoning_effort/thinking/enable_thinking. temperature/top_p/presence_penalty/frequency_penalty accepted but have NO EFF | partial | high | https://api-docs.deepseek.com/guides/reasoning_model |
| deepseek | deepseek-reasoner | CRITICAL: returns HTTP 400 if reasoning_content from a previous assistant turn is left in messages history. Shim's strip_reasoning() removes reasoning_content/r | yes | high | https://api-docs.deepseek.com/guides/reasoning_model ; strip-shim/app. |
| zai | glm-4.7-flash / glm-4.5-flash | Native Z.ai API (proxied via shim zai-proxy as openai-compat) uses thinking:{type:'enabled'/'disabled'} — NOT reasoning_effort and NOT enable_thinking. Does NOT | no | high | https://docs.z.ai/api-reference/llm/chat-completion ; strip-shim/app.p |
| zai | glm-5.1 | glm-5/5.1 are PAID-only on Z.ai free tier (free tier only glm-4.7-flash/glm-4.5-flash). glm-5.1 reachable on free tiers only via openrouter (z-ai/glm-5.1, 402 s | n/a | high | dashboard/app.py:723 ; pool_weights.yaml:60,64 |
| github-models | * | Free tier HARD-CAPS 8k input / 4k output tokens per request (cumulative-per-minute). DEFAULT_OUTPUT_CAP=16384 in shim EXCEEDS the 4k output cap — a direct githu | no | high | https://docs.github.com/en/rest/models/inference ; https://github.com/ |
| groq | openai/gpt-oss-120b | reasoning_effort accepts low/medium/high (default medium). reasoning_format NOT supported on gpt-oss (reasoning lands in 'reasoning' field). max_tokens DEPRECAT | n/a | high | https://console.groq.com/docs/reasoning ; https://console.groq.com/doc |
| groq | qwen/qwen3-32b | reasoning_effort on qwen3 accepts ONLY 'none'/'default' (NOT low/medium/high). Mismatched value would error. Not currently pooled but a latent quirk if added. | no | medium | https://console.groq.com/docs/reasoning ; https://console.groq.com/doc |
| cloudflare | * | Workers AI OpenAI-compat accepts standard temperature/max_tokens/top_p/frequency_penalty/presence_penalty. No top-level reasoning_effort. 10k neurons/day pooled | partial | high | https://developers.cloudflare.com/workers-ai/configuration/open-ai-com |
| cloudflare | @cf/moonshotai/kimi-k2.6 | OLD Kimi control-token tool-call format (</tool_call_begin/>functions.NAME:IDX...). Shim normalize_kimi_tool_calls OLD-format regex lifts these to structured to | yes | high | strip-shim/app.py:304-308,630-656 ; pool_weights.yaml:51 ; memory proj |
| sambanova | * | Rejects/ignores frequency_penalty, presence_penalty, seed. Supports top_k (non-OpenAI). 32k ctx limit, 20 RPD per-model hard cap. Shim does NOT strip frequency_ | no | medium | https://docs.sambanova.ai/docs/en/features/openai-compatibility ; dash |
| sambanova | DeepSeek-V3.1 | Only DeepSeek-V3.1 NON-thinking supports function calling; the thinking variant does not. Probe confirmed V3.1 = TEXT-ONLY broken tools on the served variant —  | n/a | high | https://sambanova-systems.mintlify.dev/docs/en/features/function-calli |
| openrouter | *:free | OpenAI-compat passthrough; reasoning controls vary by underlying model/provider. 50 RPD pooled account-wide on $0 credit. deepseek-v4-flash:free and qwen3-next- | partial | medium | dashboard/app.py:720 ; pool_weights.yaml:57,62 |
| opencode-zen | *-free | OpenAI-compat demo tier, ~5-10 calls/day pooled. qwen3.6-plus-free returns 500 + no tools (broken); minimax-m2.5-free broken tools; deepseek-v4-flash-free + nem | n/a | medium | dashboard/app.py:721 ; memory project_provider_tool_calling_matrix |
| ALL | * | stream_options is rejected by nvidia-nim (and others) with 'Stream options can only be defined when stream=True'. Shim UNIVERSALLY pops stream_options whenever  | yes | high | strip-shim/app.py:802-807,851-852 |
| ALL | * | max_tokens clamped per-pool: omo-main/omo-gemini/omo-utility cap 65536; direct provider/model calls cap DEFAULT_OUTPUT_CAP=16384. No per-provider lower clamp (e | partial | high | strip-shim/app.py:145-176 |

_rec:_ Pool/shim implications: (1) The shim's observed-rejection handling (stream_options strip, developer->system, deepseek reasoning_content stripping, nvidia unhashable retry, NIM deepseek-v4-pro enable_thinking) is correct and load-bearing — keep. (2) HIGHEST-VALUE shim fix: make reasoning_effort handling VALUE-AWARE — keep 'high'/'none', drop only 'low'/'medium'/'minimal' — and add cerebras zai-glm-4.7/glm-4.7 to the rejecter set; it is the omo-main PRIMARY and currently has zero recovery path for a low/medium reasoning_effort. (3) Make zai_proxy translate reasoning_effort->thinking:{type:enabled|disabled} and strip parallel_tool_calls, since zai/glm-4.7-flash is a live omo-main fallback serve

_gaps/unknowns:_
- cerebras/zai-glm-4.7 (the omo-main PRIMARY) accepts reasoning_effort ONLY as 'none'; low/medium/high/minimal -> 400. zai-glm-4.7 is NOT in _RE_REJECTERS, so neither pre-strip (direct call) nor post-re
- z.ai native targets (zai/glm-4.7-flash in omo-main, zai/glm-4.5-flash) need thinking:{type:'enabled'|'disabled'} and do NOT understand reasoning_effort or parallel_tool_calls. The zai-proxy is a dumb 
- cohere rejects parallel_tool_calls but the shim only strips it reactively inside the nvidia 'unhashable type' 500 path (which cohere never triggers). If omo sends parallel_tool_calls to a cohere targe
- mistral and cerebras/zai-glm-4.7 are treated as full reasoning_effort rejecters, but they ACCEPT 'high'/'none' (mistral) / 'none' (cerebras-glm). Current behavior strips the field entirely, silently d
- mistral magistral-* models reject reasoning_effort with 422 (native reasoning) and are NOT in _RE_REJECTERS — latent if ever pooled.
- github-models free tier caps OUTPUT at 4k tokens but DEFAULT_OUTPUT_CAP=16384; a direct github-models call asking >4k output would error/truncate. Latent (not pooled) but would bite if github-models i
- groq qwen3 reasoning_effort accepts only none|default (not low/medium/high) — opposite-direction quirk vs gpt-oss; unhandled, latent if a groq qwen reasoning model is pooled.
- sambanova rejects frequency_penalty/presence_penalty/seed; deepseek-reasoner errors on logprobs/top_logprobs. Neither is stripped by the shim. Low practical impact (omo doesn't send these) but worth a
- _detect_param_rejection path-2 depends on body_json.error_details/extra_fields.model_requested being present in bifrost's 400 body — verify this field name still matches current bifrost output (the ce
- DEFAULT_OUTPUT_CAP=16384 vs known per-provider output ceilings (github-models 4k, deepseek 64k OK, cohere/mistral historically 8192) is not provider-aware for direct calls; relies on cascade walk. Cou

### SAME MODEL, DIFFERENT PROVIDER divergence — cross-provider behavior of shared models in th
Across the omo-main cascade, only THREE models are genuinely the same weights served by >1
provider: kimi-k2.6 (cloudflare + nvidia-nim), glm-5.1 (openrouter + nvidia-nim), and
deepseek-v4-flash (openrouter + opencode-zen, plus an unrouted deepseek-direct provider).
Several pairs that LOOK shared are actually DIFFERENT models and must not be treated as
interchangeable: cerebras/zai-glm-4.7 (355B full GLM-4.7) vs zai/glm-4.7-flash (30B dense
distilled, no multimodal, 16k output cap); and cerebras/qwen-3-235b-a22b-instruct-2507 vs
openrouter/qwen3-next-80b-a3b-instruct (entirely different Qwen architectures). KEY VERIFIED
DIVERGENCES: (1) kimi-k2.6 — Cloudflare's host emits reasoning-text-instead-of-tool_calls and
42s latency outliers (our memory) and the shim's Kimi normalizer handles the OLD control-token
format (functions.NAME:IDX); NVIDIA NIM uses the NEW hex-id control-token format (no function
name → shim infers from JSON keys vs tools[]) AND has a confirmed, documented HTTP-500
'unhashable type: dict' bug triggered by large tools arrays + parallel_tool_calls + reasoning
streaming (NVIDIA forums 369730/369903). Shim handles both: control-token parsing + the
unhashable-500 retry stripping parallel_tool_calls. K2.6 also has a documented quirk: when
thinking is enabled, tool_choice must be auto/none. (2) glm-5.1 — openrouter/z-ai/glm-5.1 is a
PAID model (NO :free variant exists; $0.98/$3.08 per Mtok), so on a $0-credit OpenRouter
account it 402s ('credits gated' per HANDOFF); nvidia-nim/z-ai/glm-5.1 is free-credit but is
THE cascade bottleneck (62s median, 226s p95, ~300s timeouts). Same model, radically different
cost class + latency. (3) deepseek-v4-flash — openrouter :free (50 RPD pooled account-wide, 20
RPM) tool-calls OK; opencode-zen -free (5-10/day pooled demo) tool-calls OK; the deepseek-
direct provider is wired but UNROUTED, and its seed.sh comment is WRONG (says deepseek-
chat/reasoner = V4-Pro; current docs say they map to V4-FLASH and are deprecated 2026-07-24 —
should pin deepseek-v4-flash/deepseek-v4-pro directly). The shim already neutralizes most
format divergence (tool-id rewrite, Kimi/Qwen/JSON tool-call lift, think-strip, role coerce,
param pre-strip+retry, nvidia unhashable retry, reasoning-only nudge, z.ai path rewrite).
Remaining gaps are quota/cost-class aware ordering, not normalization.

| provider | model | fact | shim | conf | source |
|---|---|---|---|---|---|
| cloudflare | @cf/moonshotai/kimi-k2.6 | Emits reasoning-text instead of structured tool_calls (finish_reason=stop, content narrates 'I should call X', tool_calls=[]) → freezes omo Sisyphus. Also 42s l | partial | high | project_cloudflare_kimi_tool_call_issue.md; project_kimi_reasoning_onl |
| cloudflare | @cf/moonshotai/kimi-k2.6 | When Kimi DOES emit control tokens, Cloudflare/Moonshot uses the OLD format </tool_call_begin/>functions.NAME:IDX</tool_call_argument_begin/>{json}</tool_call_e | yes | high | strip-shim/app.py:300-308,372-385 |
| cloudflare | * | OpenAI-compat endpoint is account-scoped: api.cloudflare.com/client/v4/accounts/<ACCT>/ai/v1/chat/completions (not a flat /v1). Wired via request_path_overrides | n/a | high | bifrost/seed.sh:120-137; developers.cloudflare.com/workers-ai/configur |
| nvidia-nim | moonshotai/kimi-k2.6 | Uses NEW control-token format </tool_call_begin/>HEX_ID</tool_call_argument_begin/>{json}</tool_call_end/> with NO function name in token. Shim _KIMI_TC_NEW reg | yes | high | strip-shim/app.py:309-313,386-410; HANDOFF.md:16 |
| nvidia-nim | moonshotai/kimi-k2.6 | Confirmed documented bug: HTTP 500 'unhashable type: dict' on large tools arrays (~200 tools) + parallel_tool_calls=true + reasoning streaming. Non-deterministi | yes | high | strip-shim/app.py:871-907; NVIDIA forums t/369730 + t/369903; github K |
| nvidia-nim | moonshotai/kimi-k2.6 | Tier-C fallback in omo-main (fb=10): NIM hex-id + unhashable bug + 429s on ~1000-credit dev preview. 40 RPM documented limit, no rate-limit headers, monthly cre | partial | high | pool_weights.yaml:61; PROVIDER_QUOTAS nvidia-nim note (dashboard/app.p |
| * | kimi-k2.6 | Documented model-level quirk: when thinking/reasoning enabled, tool_choice may only be 'auto' or 'none' (not a forced specific tool) to avoid reasoning/tool_cho | no | medium | help.apiyi.com/en/kimi-k2-6-api-integration-guide-en.html; platform.ki |
| openrouter | z-ai/glm-5.1 | PAID model — there is NO z-ai/glm-5.1:free variant. Priced $0.98/$3.08 per Mtok. On a $0-credit OpenRouter account it returns 402 ('credits gated'). The pool li | no | high | openrouter.ai/z-ai/glm-5.1 (paid only); routing-rules.json:73; HANDOFF |
| nvidia-nim | z-ai/glm-5.1 | Free-credit copy of SAME glm-5.1, but is THE cascade latency bottleneck: 62s median, 226s p95, occasionally hits 300s bifrost timeout → cascade-fail. Kept as ab | n/a | high | pool_weights.yaml:64; HANDOFF.md:72,78,154; routing-rules.json:76 |
| openrouter | deepseek/deepseek-v4-flash:free | Genuine $0 :free model, tool-calls OK (~3s probe). Subject to OpenRouter free-tier 50 RPD pooled ACCOUNT-WIDE across all :free models + 20 RPM; failed attempts  | n/a | high | openrouter.ai/deepseek/deepseek-v4-flash:free ($0); openrouter.ai/docs |
| opencode-zen | deepseek-v4-flash-free | SAME deepseek-v4-flash weights via opencode's demo tier: tool-calls OK (1.76s probe) but ~5-10 calls/day POOLED across all zen-free models. Deep last-resort cus | n/a | high | project_provider_tool_calling_matrix.md; dashboard/app.py:829; bifrost |
| deepseek | deepseek-v4-flash / deepseek-v4- | Direct DeepSeek API provider is WIRED but NOT in any pool (unrouted). seed.sh comment is STALE/WRONG: claims deepseek-chat=V4-Pro/deepseek-reasoner=V4-Pro-reaso | n/a | high | bifrost/seed.sh:236-258; api-docs.deepseek.com (deprecation 2026-07-24 |
| cerebras | zai-glm-4.7 | NOT the same model as zai/glm-4.7-flash: this is the FULL GLM-4.7 (355B MoE, 128k ctx, multimodal, 128k output). Primary of omo-main. Tool-calls OK, fast 1.9s m | n/a | high | inference-docs.cerebras.ai/models/zai-glm-47 (5 RPM/30k TPM/1M daily t |
| zai | glm-4.7-flash | DIFFERENT model from cerebras/zai-glm-4.7: 30B DENSE distilled, 128k ctx but only 16,384 output, NO multimodal, weaker on all benchmarks. Native Z.ai free tier  | n/a | high | llm-stats.com GLM-4.7 vs GLM-4.7-Flash; blogs.novita.ai/glm-4-7-vs-glm |
| zai | * | Native Z.ai requires path /api/paas/v4/chat/completions (no /v1). Bifrost openai-compat hardcodes /v1 suffix, so shim runs a zai-proxy that strips the leading v | yes | high | strip-shim/app.py:25-32,710-751; docs.z.ai/api-reference/llm/chat-comp |
| cerebras | qwen-3-235b-a22b-instruct-2507 | NOT the same as openrouter/qwen3-next-80b: this is Qwen3-235B-A22B-Instruct, a NON-thinking model (no <think> blocks). It REJECTS reasoning_effort (400). Confir | yes | high | strip-shim/app.py:543-547,605; inference-docs.cerebras.ai/models/qwen- |
| openrouter | qwen/qwen3-next-80b-a3b-instruct | DIFFERENT model from cerebras qwen-3-235b: 80B-A3B Qwen3-Next, instruct (no thinking traces), genuine $0 :free. Venice-hosted, fast-fails on 429 → useful cheap  | n/a | high | openrouter.ai/qwen/qwen3-next-80b-a3b-instruct:free ($0); pool_weights |
| sambanova | DeepSeek-V3.1 vs DeepSeek-V3.2 | Same provider, near-same model family, DIVERGENT tool behavior: V3.2 tool-calls OK (2.0s, KEEP); V3.1 is TEXT-ONLY broken tools (EXCLUDED). Plausible per docs:  | no | high | project_provider_tool_calling_matrix.md; pool_weights.yaml:15-16; dash |
| mistral | mistral-large-2512 vs mistral-me | Both tool-call OK. Large = 4 RPM / 250k TPM (tight), medium = 50 RPM / 50k TPM (huge headroom). Both REJECT reasoning_effort low/medium/minimal (silently, as ge | yes | high | strip-shim/app.py:1-9,543-547,492-509; dashboard/app.py:804-808; pool_ |

_rec:_ PER-SHARED-MODEL PREFERRED PROVIDER + cascade rationale: (1) kimi-k2.6 — PREFER nvidia-nim over cloudflare for tool-using turns. Rationale: NIM's hex-id format is fully lifted by the shim AND its unhashable-500 bug is retry-handled, whereas Cloudflare's reasoning-text-instead-of-tool_calls is only PARTIALLY recoverable (the reasoning-only nudge-retry helps but freezes are still observed). BUT both share the K2.x reasoning-only-freeze risk, so neither should be a tool-pool PRIMARY. Current pool has cloudflare at fb=2 (29% serve) ahead of nvidia at fb=10 — consider keeping cloudflare for its daily-free pool (no credit burn) but accept the shim nudge-retry as the safety net. Keep both in fallba

_gaps/unknowns:_
- openrouter/z-ai/glm-5.1 cost-class: confirm whether the live OR account has >=10 credits (which would lift it from 402-gated to 1000 RPD billed) — if it's still $0-credit it should be DROPPED from omo
- deepseek-direct provider is unrouted: should it be added to the deepseek-v4-flash cascade tier as a primary (it's faster + cheaper than NIM-hosted and not pooled like zen)? Needs a live tool-call prob
- Whether NVIDIA NIM kimi-k2.6 still emits hex-id (NEW) format or has switched to returning native OpenAI tool_calls in a recent NIM release — re-probe; if NIM now returns proper tool_calls the shim inf
- Cloudflare kimi-k2.6 reasoning-text-instead-of-tool_calls: is it intermittent (some turns OK) or 100% broken? Memory cites 2/2 stuck turns + 29% serve rate in pool — needs a fresh 10-shot tool probe t
- Kimi thinking+tool_choice=auto/none constraint: does omo ever send tool_choice as a forced specific function? If so, on a thinking-enabled Kimi turn that would conflict — shim does not currently coerc
- GLM-5.1 latency on NIM (62s median): confirm if NIM has improved serving since the 2026-05-22 probe; if still ~60s it remains the bottleneck justifying last-resort placement.
- SambaNova V3.2 vs V3.1 tool divergence: re-verify V3.1 is still text-only (the V3.1-Terminus non-reasoning-mode function-calling doc hint suggests it MIGHT work if reasoning is disabled — could be a r
- zai/glm-4.7-flash 2 RPM is extremely tight — confirm it's worth a Tier-B slot (fb=5) vs demoting; at 2 RPM it saturates almost immediately under any burst.

### How to enumerate each provider's current FREE models + correct model IDs (per-provider aut
Verified the authoritative model-enumeration method, ID naming convention, and free-vs-paid
reality for all 13 providers against current (May 2026) official docs. Three classes emerged:
(1) PROVIDERS WITH A TRUSTWORTHY /models LIST THAT ENCODES FREE-NESS — OpenRouter (GET
/api/v1/models; free = ":free" suffix AND pricing.prompt=="0"/completion=="0", both
programmatically filterable) and OpenCode Zen (GET /zen/v1/models, free models tagged in
metadata e.g. "DeepSeek V4 Flash Free"). (2) PROVIDERS WITH A /models LIST BUT FREE-NESS LIVES
OUTSIDE IT — Cerebras (GET /v1/models, but docs catalog page lists only 2 IDs while live
endpoint + free rotation has qwen3-235b/llama; free tier is 1M tok/day, no per-model free
flag), Gemini (GET /v1beta/openai/models OR native /v1beta/models?key=; free-ness per-model is
on the pricing page, e.g. pro-preview returns "free_tier_requests limit: 0"), Mistral (GET
/v1/models; Experiment tier free across ALL models), Groq (GET /openai/v1/models; everything
is "paid" priced but dev free tier = generous RPM/RPD), Cohere (GET /v1/models native or
/compatibility/v1 OpenAI-compat; trial key = free, 20 RPM chat / 1000 calls/mo), GitHub Models
(GET https://models.github.ai/catalog/models; free during preview, tiered by Copilot plan),
z.ai (NO list endpoint; -flash models free, glm-5/5.1 paid). (3) PROVIDERS WHERE /models IS
USELESS FOR FREE-NESS — NVIDIA NIM (/v1/models returns ~189 catalog entries incl
retired/embeddings with NO hosted/free flag; ONLY reliable method is probe each with a tiny
chat-completion and classify by HTTP 200/429 vs 404/403), Cloudflare (GET
/accounts/{id}/ai/models/search?task=text-generation; free = 10k Neurons/day account-wide
compute budget, not per-model), DeepSeek (deepseek-v4-flash/deepseek-v4-pro; NO permanent free
tier — ~5M signup tokens then pay-as-you-go), SambaNova (GET /v1/models; free = 20 RPD/model
hard cap). Several ID/quota drifts vs the repo were found and flagged.

| provider | model | fact | shim | conf | source |
|---|---|---|---|---|---|
| cerebras | * | Authoritative enumeration = GET https://api.cerebras.ai/v1/models (Bearer auth). Returns {id, owned_by} OpenAI-list format. CRITICAL: the docs catalog PAGE (inf | n/a | high | https://inference-docs.cerebras.ai/api-reference/models ; https://infe |
| cerebras | * | Free tier: 1M tokens/day, no credit card. Web docs cite 30 RPM / 60-100k TPM / 8192-token ctx cap across free models. NOTE conflict: repo dashboard says '5 RPM, | n/a | medium | https://www.getaiperks.com/en/ai/cerebras-free-tier-guide ; our-file:d |
| cerebras | zai-glm-4.7 | Confirmed valid free ID (lowercase-hyphenated convention). qwen-3-235b-a22b-instruct-2507 is referenced in repo pools and is in free rotation but NOT shown on t | n/a | high | https://inference-docs.cerebras.ai/models/overview ; our-file:scripts/ |
| cloudflare | * | Authoritative enumeration = GET https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/models/search?task=text-generation (Bearer token). Supports search | n/a | high | https://developers.cloudflare.com/api/resources/ai/subresources/models |
| cloudflare | @cf/moonshotai/kimi-k2.6 | ID string confirmed exact; Cloudflare docs CLAIM function-calling=Yes + multi-turn tool calling. BUT project memory (verified empirically) says cf/kimi-k2.6 emi | partial | high | https://developers.cloudflare.com/workers-ai/models/kimi-k2.6/ ; proje |
| gemini | * | Two enumeration paths: OpenAI-compat GET https://generativelanguage.googleapis.com/v1beta/openai/models (Bearer GEMINI_API_KEY) — this is what the repo's openai | n/a | high | https://ai.google.dev/gemini-api/docs/openai ; https://ai.google.dev/g |
| gemini | gemini-3.1-pro-preview | The /models list does NOT tell you free-vs-paid. Free-ness is per-model and only knowable from the pricing page or by probing: pro-preview returns 'free_tier_re | n/a | high | our-file:scripts/runtime/pool_weights.yaml (omo-gemini comment) ; http |
| mistral | * | Authoritative enumeration = GET https://api.mistral.ai/v1/models (Bearer). Convention: name-tier-version e.g. mistral-large-2512, mistral-medium-3.5 (=mistral-m | n/a | high | https://docs.mistral.ai/api/ ; https://pricepertoken.com/endpoints/mis |
| mistral | * | QUOTA CONFLICT to resolve: public 2026 sources say free Experiment tier = 2 RPM / 500K TPM / 1B tokens/month, applied account-wide. Repo says per-model 'large=4 | n/a | medium | https://docs.mistral.ai/admin/user-management-finops/tier ; our-file:d |
| mistral | mistral-large-2512 | reasoning_effort/thinking params rejected by mistral-* — shim pre-strips and retries (param-rejection retry). ID convention valid. | yes | high | project-memory:shim_param_rejection_retry ; our-file:strip-shim/app.py |
| nvidia-nim | * | Enumeration trap: GET https://integrate.api.nvidia.com/v1/models returns the ENTIRE catalog (~189 entries on 2026-04-06) including embeddings, vision, reward, r | n/a | high | https://stevescargall.com/blog/2026/04/using-the-api-to-find-free-host |
| nvidia-nim | * | Free tier = 1000 inference credits on signup (requestable up to 5000) via build.nvidia.com, key prefix nvapi-. Some partner models (e.g. Zhipu GLM family) are f | yes | high | https://medium.com/coding-nexus/nvidia-is-offering-80-ai-models-for-fr |
| openrouter | * | BEST-IN-CLASS enumeration: GET https://openrouter.ai/api/v1/models (no auth needed for listing). Free models = id ends in ':free' AND pricing.prompt=='0' && pri | n/a | high | https://openrouter.ai/docs/api/api-reference/models/get-models ; live  |
| openrouter | * | Free-tier rate limit is credit-gated: <$10 lifetime credits purchased => 50 :free requests/day; >=$10 purchased => 1000 :free requests/day; both capped at 20 RP | n/a | high | https://openrouter.ai/docs/api/reference/limits ; https://openrouter.z |
| sambanova | * | Enumeration = GET https://api.sambanova.ai/v1/models (Bearer). Convention: CamelCase-with-dots/dashes e.g. DeepSeek-V3.2, DeepSeek-V3.1, Meta-Llama-3.3-70B-Inst | n/a | high | https://docs.sambanova.ai/cloud/docs/get-started/supported-models ; ou |
| sambanova | DeepSeek-V3.1 | Empirically TEXT-ONLY (broken tool-calls) — repo excluded it; DeepSeek-V3.2 tool-calls OK. /models won't tell you this; it's a probe-only fact. Keep V3.1 out of | no | high | our-file:scripts/runtime/pool_weights.yaml |
| groq | * | Enumeration = GET https://api.groq.com/openai/v1/models (Bearer). Convention: bare (llama-3.3-70b-versatile, llama-3.1-8b-instant) OR {publisher}/{model} (opena | n/a | high | https://console.groq.com/docs/models ; our-file:dashboard/app.py:717 ; |
| cohere | * | Enumeration = GET https://api.cohere.com/v1/models (native; supports ?endpoint= filter + page_size). OpenAI-compat layer at https://api.cohere.ai/compatibility/ | n/a | high | https://docs.cohere.com/reference/list-models ; https://docs.cohere.co |
| cohere | command-a-plus-05-2026 | NEW flagship (May 2026, first MoE, vision+agentic+reasoning) — repo pools only reference the OLDER command-a-03-2025. Worth probing for tool-call quality and ad | n/a | high | https://docs.cohere.com/docs/models ; https://codenote.net/en/posts/co |
| cohere | command-a-03-2025 | reasoning_effort/thinking rejected by cohere command-* — shim pre-strips+retries. | yes | high | project-memory:shim_param_rejection_retry ; our-file:strip-shim/app.py |
| github-models | * | Enumeration = GET https://models.github.ai/catalog/models (Bearer PAT with 'models' scope). Response objects carry separate publisher, name, AND id fields. Infe | n/a | high | https://docs.github.com/en/rest/models/catalog ; https://docs.github.c |
| github-models | * | Free during public preview; rate limits TIERED by Copilot plan (Free/Pro/Business/Enterprise) and model class (Low/High/Embedding/specialized). Free user 'Low'  | n/a | medium | https://docs.github.com/en/github-models/use-github-models/prototyping |
| opencode-zen | * | Enumeration = GET https://opencode.ai/zen/v1/models (returns full list + metadata). FREE models currently: 'DeepSeek V4 Flash Free', 'MiMo-V2.5 Free', 'Nemotron | n/a | high | https://opencode.ai/docs/zen/ ; our-file:bifrost/seed.sh |
| zai | * | NO list-models endpoint documented. Base = https://api.z.ai/api/paas/v4 (native path has NO /v1/ — repo's shim path-rewrite proxy /zai-proxy/v4 handles this). C | yes | high | https://docs.z.ai/api-reference/llm/chat-completion ; our-file:bifrost |
| deepseek | * | OpenAI-compat base = https://api.deepseek.com (or /v1). Current explicit IDs: deepseek-v4-flash, deepseek-v4-pro. CRITICAL CORRECTION: legacy aliases deepseek-c | n/a | high | https://api-docs.deepseek.com/quick_start/pricing ; https://deepseeksr |
| deepseek | deepseek-v4-pro | Shim injects enable_thinking for deepseek-v4-pro. v4-pro on a 75%-off promo until 2026-05-31 15:59 UTC, then prices rise to 1/4 of original — cost profile chang | yes | high | our-file:strip-shim/app.py ; https://api-docs.deepseek.com/quick_start |

_rec:_ Build ONE generic enumeration shim/script, not 13 bespoke ones, because providers cluster into three tiers by how trustworthy their /models list is for free-ness. TIER 1 (trust the list, filter programmatically): OpenRouter (id endswith ':free' AND pricing.prompt=='0' && completion=='0') and OpenCode Zen (free flag in metadata) — these can auto-populate pools safely. TIER 2 (list gives valid IDs but free-ness is external): cerebras /v1/models, gemini /v1beta/openai/models, mistral /v1/models, groq /openai/v1/models, cohere /v1/models, github-models /catalog/models, sambanova /v1/models — use the list to catch RENAMED/DEAD IDs (prevents pools pointing at 404s), but free-ness + quota must come

_gaps/unknowns:_
- Mistral free 'Experiment' tier real numbers: public sources say 2 RPM/500K TPM/1B tok-month ACCOUNT-WIDE; repo says per-model 4 RPM (large)/50 RPM (medium). Official numeric tier table is gated behind
- GitHub Models RPM/RPD: repo claims 20k RPM/2M TPM but free Copilot-tier docs imply ~15 RPM/150 RPD for 'Low' models. ACTION: verify the account's Copilot plan tier and the per-model class (Low/High) f
- Cerebras free-tier numbers: web says 30 RPM/1M tok-day/8k ctx; repo header-verified 5 RPM/2400 RPD/30k TPM. Likely different key tiers. ACTION: re-read x-ratelimit-* headers from a live cerebras call 
- NVIDIA NIM free-model SET is volatile and has no API flag — needs a periodic probe-sweep script (catalog /v1/models -> minimal chat-completion -> classify by HTTP 200/429). No such sweep script exists
- z.ai has no list-models endpoint at all — the model inventory is maintained by hand/doc-scrape. New free -flash variants (e.g. glm-5-flash if/when released) won't be auto-discovered.
- Cohere command-a-plus-05-2026 (new flagship) tool-call behavior + trial-key availability unverified — needs a live probe before pooling.
- deepseek-chat/deepseek-reasoner alias remap to v4-flash (not v4-pro) contradicts seed.sh comment — confirm by GET model metadata or a probe, and fix the stale comment.
- Whether OpenRouter free IDs ever get a non-:free-suffixed free variant, or vice-versa — rely on the dual check (suffix AND pricing==0) rather than suffix alone to be safe.


## Appendix E — LMArena (arena.ai) ranking data — scout spec (cited)

The canonical, no-scrape way to get LMArena Elo is the HuggingFace dataset `lmarena-
ai/leaderboard-dataset` — VERIFIED LIVE: lastModified 2026-05-28, 50,139 downloads, ungated,
CC-BY-4.0, leaderboard_publish_date=2026-05-27. It works without an HF token. The pinned
memory (project_lmarena_integration.md) is correct on repo id/schema but STALE on structure:
categories are NOT separate parquet configs — they are values in a `category` column inside
each top-level config. The relevant config is `text` (raw, no style-control) with two splits:
`latest` (8,902 rows = current snapshot, what the scout wants) and `full` (839,796 rows = all
historical snapshots). Schema confirmed exactly: model_name, organization, license, rating,
rating_lower, rating_upper, variance, vote_count, rank, category, leaderboard_publish_date
(rating/CI/votes/rank as float64 in text config). 27 category values exist including overall,
multi_turn, hard_prompts, coding, math, instruction_following, creative_writing, longer_query,
expert, plus per-language and per-industry buckets. Fetch via plain HTTP GET of the parquet
(curl/urllib) then read with pyarrow — confirmed two working no-auth URLs. NO official free
LMArena REST API exists: arena.ai/blog/policy only says they 'periodically share portions of
our data' and open-source the ranking pipeline; the 'enterprise API / GitHub JSON feed' claim
came from a low-quality SEO blog, not official docs (low confidence, do not rely on it). Name-
matching: of 21 distinct pool models, automated matching (exact-basename after stripping
provider/sub-namespace prefix + ':free' suffix + normalization + leading-vendor strip)
resolves 13; 8 need handling (3 date-version aliases, 5 genuinely absent from arena). Concrete
pool-ordering bug found: pool_weights.yaml guesses zai/glm-4.7-flash at '~1430 est MT' but
arena has glm-4.7-flash at #151 = 1353 overall / 1345 MT (~85 pts too high).

_verified facts:_
- Dataset is LIVE and ungated: lastModified 2026-05-28T17:20Z, 50,139 downloads, license CC-BY-4.0, gated=false. Accessible with no HF token.  (https://huggingface.co/api/datasets/lmarena-ai/leaderboard-dataset (fe)
- This IS the correct/current repo id (pinned memory was right). 14 configs: text, text_style_control, vision, vision_style_control, search, search_style_control, document, document_style_control, webde  (https://datasets-server.huggingface.co/info?dataset=lmarena-ai/leaderb)
- Schema verified exactly: model_name(str), organization(str), license(str), rating(float64=Elo), rating_lower(float64), rating_upper(float64=95% CI), variance(float64), vote_count(float64), rank(float6  (/info endpoint + read of text/latest-00000-of-00001.parquet via pyarro)
- Splits: 'latest' = most recently published leaderboard snapshot (text config: 8,902 rows; publish_date 2026-05-27 as of fetch) — USE THIS for the scout. 'full' = all historical snapshots (839,796 rows  (dataset card README + parquet row read; /info row counts)
- 27 category values present in text/latest: overall, multi_turn, hard_prompts, hard_prompts_english, coding, math, instruction_following, creative_writing, longer_query, expert, exclude_ties, english/c  (pyarrow read of text/latest parquet (distinct category values) + /stat)
- BEST fetch method = plain HTTP GET of the parquet, no auth, then read with pyarrow. Two working no-auth URLs (both HTTP 200): https://huggingface.co/api/datasets/lmarena-ai/leaderboard-dataset/parquet  (curl -w HTTP %{http_code}: both returned 200 (2026-05-29))
- datasets-server.huggingface.co /rows works via curl for raw JSON paging (max length=100). But /search and /filter ERROR for this dataset (/search -> {error:Unexpected error}; /filter where= -> 422 inv  (curl tests of /rows (200 OK), /search (error), /filter (422) on 2026-0)
- hf CLI is NOT a reliable fetch path here: /home/jkr/.local/bin/hf exists but errors ModuleNotFoundError: No module named huggingface_hub. Use curl/urllib + pyarrow (run under `uv run --with pyarrow`,   (Bash: hf download attempt failed; `uv run --with pyarrow` succeeded)
- NO documented free public REST API for Elo. arena.ai/blog/policy only says 'We periodically share portions of our data' and that the eval/ranking pipeline is open-sourced (Arena-Rank). The HF dataset   (https://arena.ai/blog/policy/ (redirect from news.lmarena.ai/policy); )
- Exact basename match to arena 'gemini-3.5-flash'. overall=1482 (#4), multi_turn=1489 (#5). High-confidence top pool primary.  (pyarrow read text/latest overall+multi_turn)
- NO direct match — arena name is 'gemini-3-flash' (NO '-preview'). overall=1466 (#15), multi_turn=1472. Needs alias gemini-3-flash-preview->gemini-3-flash. NB arena also has a separate variant row 'gem  (pyarrow read; overall has both gemini-3-flash and 'gemini-3-flash (thi)
- Matches after normalization: arena 'qwen3-235b-a22b-instruct-2507' (arena uses 'qwen3' no hyphen, our id 'qwen-3'). overall=1419 (#74), multi_turn=1433. Strip non-alphanumerics + lowercase to match.  (pyarrow read + norm match in /tmp/match.py)
- Matches arena 'glm-4.7' after stripping leading vendor token 'zai-' (and provider prefix). overall=1436 (#47), multi_turn=1448. org='zai' on arena.  (pyarrow read + strip-vendor match)
- Exact match arena 'glm-4.7-flash' = #151, overall 1353 / multi_turn 1345. POOL BUG: pool_weights.yaml comment guesses '~1430 est MT' for this slot — actual is ~1345 MT, ~85 pts too high. Real win for   (pyarrow read; pool_weights.yaml line 'zai/glm-4.7-flash  # ~1430 est M)
- Both cloudflare/@cf/moonshotai/kimi-k2.6 and nvidia-nim/moonshotai/kimi-k2.6 match arena 'kimi-k2.6' (org moonshot) by basename. overall=1456 (#20), multi_turn=1448. Same Elo for both providers (Elo i  (pyarrow read; both pool ids reduce to kimi-k2.6)
- Both match arena 'glm-5.1' (#13, overall 1469 / multi_turn 1476) after stripping provider + 'z-ai/' sub-namespace.  (pyarrow read + basename match)
- Matches arena 'deepseek-v4-flash' (#55, overall 1428 / multi_turn 1444) after stripping ':free' and provider/vendor prefixes.  (pyarrow read + basename match)
- Matches arena 'qwen3-next-80b-a3b-instruct' (#75, overall 1419 / multi_turn 1418) after ':free' + prefix strip.  (pyarrow read + basename match)
- DATE<->VERSION aliases, no auto-match. arena 'mistral-large-3' = #52 overall 1430. arena 'mistral-medium-2508' = #58 overall 1426. Our pool comment already asserts large-2512==large-3 and medium-3.5==  (pyarrow read; pool_weights.yaml comments map these aliases)
- No arena entry — rolling '-latest' aliases and gemini-2.5-flash-lite are absent from text/latest. These live in omo-utility/omo-gemini pools where Elo is low-priority. Leave unranked or map gemini-2.5  (pyarrow read: no matching model_name in overall category)
- No arena text-leaderboard entry. arena has 'gemma-4-31b' (#36, 1442) WITHOUT '-it' (alias-fixable). No llama-3.2-90b-vision or pixtral on the TEXT board (vision models only ranked under 'vision' confi  (pyarrow read: gemma-4-31b present, no 90b-vision/pixtral in text)

_scout recommendation:_ Build scripts/runtime/scout_lmarena.py as a low-risk weekly job (NOT auto-weight-mutating). (1) FETCH: curl/urllib GET https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/resolve/main/text/latest-00000-of-00001.parquet (no token), read with pyarrow under `uv run --with pyarrow`. Optionally also pull text/full to track deltas. Do NOT use the `hf` CLI (broken here) or /search,/filter (broken for this dataset). (2) NORMALIZE pool->arena names with a two-stage matcher: first auto-match by basename (strip provider prefix, sub-namespace like z-ai//moonshotai//deepseek/, ':free' suffix, leading vendor token like 'zai-', then lowercase + strip non-alphanumerics) — this resolves 13/21 pool models; then apply a small HAND-MAINTAINED alias table in YAML for the date<->version and suffix cases: mistral-large-2512->mistral-large-3, mistral-medium-3.5->mistral-medium-2508, gemini-3-flash-preview->gemini-3-flash, gemma-4-31b-it->gemma-4-31b. Mark rolling '-latest'/vision-only ids (gemini-flash-latest, pixtral-large-latest, llama-3.2-90b-vision) as 'no-arena' and skip — for multimodal pools pull the 'vision' config not 'text'. (3) EMIT per (provider,model): pull BOTH category=overall and category=multi_turn rating (multi_turn is more relevant for omo orchestration, matching current pool_weights notes), plus rating_lower/upper (CI), vote_count, rank, leaderboard_publish_date -> write ~/.coire/curator-pool/lmarena.json. (4) Use scores ONLY for pool ORDERING / a capped weight bonus and a dashboard 'LM rank' column; do NOT let Elo override empirically-verified 'broken tools'/quota/latency verdicts (Elo is model-level and identical across providers, so it cannot capture per-provider quirks the shim handles). Immediate concrete win: correct the zai/glm-4.7-flash slot — pool_weights.yaml says ~1430 MT but arena says ~1345 MT. The shim handles none of this (n/a) — it is a ranking-metadata pipeline, orthogonal to strip-shim's request normalization.

_gaps:_
- Update cadence is not stated in the dataset card. Empirically lastModified 2026-05-28 with publish_date 2026-05-27 implies near-daily/weekly snapshots, but the exact cron is unverified — a weekly scou
- The 'enterprise API' and 'open GitHub JSON feed' claims surfaced only in a low-quality SEO blog, not in official LMArena docs. Not verified that any official REST API or GitHub JSON feed exists — shou
- Did not enumerate ALL pool models project-wide — only the 21 distinct entries in scripts/runtime/pool_weights.yaml. Other pools/configs (e.g. seed.sh provider lists, dashboard PROVIDER_QUOTAS) may ref
- Arena rows carry variant suffixes (-thinking, -high, -instant, '(thinking-minimal)', date stamps like -20251101) and multiple rows per base model. The scout must decide WHICH variant maps to our endpo
- datasets-server /search and /filter are broken for this dataset, so any approach relying on server-side query of categories won't work; confirmed only full-parquet-download works. If parquet grows or 
- pyarrow is not installed system-wide; scout must vendor its own read path (uv run --with pyarrow, or a tiny pure-python parquet read, or pin pyarrow in a venv). hf CLI is broken (missing huggingface_h