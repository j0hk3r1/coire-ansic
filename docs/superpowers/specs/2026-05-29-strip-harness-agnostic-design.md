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

```
CORE (always installed):
  bifrost/            seed + snapshot + apply        (neutral tier names)
  strip-shim/         OpenAI-compat normalizer       (de-omo'd; names config-driven)
  scripts/runtime/    apply_pool_weights + build_models_list + pool_weights.yaml
  scripts/ops/        MAINTENANCE CLI — harness-neutral, run directly / via cron
  docker-compose.yml  bifrost + strip-shim by default
  install.sh          installs core + maintenance CLI only — ZERO harness config
  .env.example

OPTIONAL:
  dashboard/          --profile dashboard (observability; harness-neutral)

GUIDES (copy-paste only, nothing deployed):
  docs/connect/opencode.md     vanilla opencode custom-provider config
  docs/connect/pi.md           ~/.pi/agent/models.json
  docs/connect/hermes.md       base-url config
  docs/connect/claude-code.md  ANTHROPIC_BASE_URL → bifrost /anthropic + model env
  docs/connect/README.md       index + the install→connect→use story
  (later: codex.md, omo.md, openclaw.md)
```

**Maintenance CLI (`scripts/ops/`, deployed to `~/coire-tools/`)** — promoted from
omo-skill wrappers to the canonical, harness-neutral interface:
- keep: `coire-health`, `coire-monitor`, `coire-restart`, `coire-check-quotas`,
  `coire-cascade-show`
- generalize `coire-diagnose` → detect stuck/error patterns from **bifrost logs only**
  (no harness log paths / process names)
- rename `coire-kill-opencode` → `coire-kill-harness` with a **configurable process
  pattern** (env/arg), so it works for any harness or none
- **drop from core:** `.opencode/` skills+commands, `oh-my-openagent.json`,
  `opencode.json.template`, `scripts/ops/deploy.sh` (.68→.93-specific). The omo material
  is parked (recoverable from git history) and returns in the LATER omo-phase spec.

### Unit boundaries
- **bifrost layer** — turns `.env` keys into providers + tier routing rules. Depends on:
  `.env`, the bifrost container, `pool_weights.yaml`. Output: a running router.
- **strip-shim** — pure OpenAI-compat proxy + normalizer. Depends on: bifrost URL,
  optional `models.json`, a tier-name config. Knows nothing about any harness.
- **maintenance CLI** — talks to the bifrost API + docker only. No harness dependency.
- **guides** — pure docs; consume the router's `/v1` (or bifrost's `/anthropic`). Not a
  code dependency of anything.

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
themselves. Tiers stay shared; only names differ. (Load-spread via per-harness primary
override is a far-future option, only if hermes-driven concurrency proves it out.)

## 6. Bug-fix sweep (step 1 — own commit, before any restructure)

All confirmed by the review (file:line). Scope = fresh-install blockers + dead-ref
removal + the broken test. Full doc *rewrites* happen later in their own steps.

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

## 7. Sequencing (each step = one local commit; no push until step 7)

1. **Bug-fix sweep** (§6) — known-good fresh-install baseline.
2. **Pool genericize** — rename pools to `coire-main/fast/vision`; re-snapshot
   routing-rules with three plain `model == "coire-X"` rules (no aliases this cut).
3. **Shim de-omo** — tier names from config (env/JSON), not hardcoded `_FALLBACK_POOLS`;
   delete the dead `_POOL_DROPS_RE` no-op + redundant `_POOL_OUTPUT_CAP`; strip omo/hermes
   comments; pin Dockerfile deps; honor `PORT`.
4. **Repo reshape + maintenance CLI** — `install.sh` = core + maintenance CLI only, ZERO
   harness config; generalize `coire-diagnose` (bifrost-logs only) + rename
   `coire-kill-opencode` → `coire-kill-harness` (configurable pattern); remove `.opencode/`,
   `oh-my-openagent.json`, `opencode.json.template`, `scripts/ops/deploy.sh` (omo material
   parked for the LATER omo spec); dashboard stays a profile.
5. **Connect guides ×4** — `docs/connect/{opencode,pi,hermes,claude-code}.md` (+ index),
   each verified copy-paste config + the "install router → install your harness → connect →
   use free" quickstart. opencode guide = **vanilla opencode**, no omo.
6. **README + docs rewrite** — core-first, harness-agnostic; lead with the Jimmy/Jon
   onboarding story; mark codex/omo/openclaw "coming"; rewrite/retire CONTRIBUTING,
   CHANGELOG, NOTICE, `docs/omo-*` (the omo docs get parked with the omo material).
7. **Reconcile .93 + push** — wipe + reinstall from `.env`; validate all four NOW harnesses
   connect; **then** user reviews and pushes.

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