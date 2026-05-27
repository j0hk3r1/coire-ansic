# Coire-ansic Session Handoff — 2026-05-21

## Goal
Stress-test + optimize free-tier LLM stack for omo (oh-my-openagent) running on .93. User runs omo locally (TUI), omo → coire/* model alias → strip-shim (:4002) → bifrost (:4001) → free provider.

## Current State

### Stack
- **Containers** (.93): coire-strip-shim, coire-bifrost, coire-dashboard, coire-searxng, coire-camofox, coire-openwebui, firecrawl-*. All healthy.
- **Hermes**: paused (4 cron jobs commented out as "PAUSED 2026-05-21 (omo-only mode)"). No active processes.
- **Pools**: omo-only — best/code/mid/fast/compress/vision/ops bifrost routing rules DELETED. yaml at `scripts/runtime/pool_weights.yaml` has only 4 omo-* pools.
- **Shim**: latest build with:
  - param-rejection retry (reasoning_effort/thinking strip)
  - stream_options strip when stream≠true
  - reasoning-only-no-action retry (max_tokens bump + nudge)
  - **NEW**: Kimi normalizer handles BOTH old format (`functions.NAME:IDX`) and NEW format (hex-ID + JSON-key inference vs request tools[])
  - unhashable-type 500 retry (NVIDIA Kimi K2.6 bug workaround)

### omo adapter (`~/.config/opencode/oh-my-openagent.json` on .93)
- `runtime_fallback: false` — disabled omo's plugin-level retry hook
- All agents + categories: `fallback_models: []` (empty) — bifrost cascade is sole authority
- Map (all agents → 1 pool):
  - sisyphus / sisyphus-junior / atlas / metis → `coire/omo-kimi`
  - prometheus / hephaestus / oracle / momus → `coire/omo-gpt-5-5`
  - multimodal-looker → `coire/omo-gemini`
  - librarian / explore → `coire/omo-utility`

### Current omo-kimi cascade (sequential, 1 primary + 6 fallbacks)
```
PRIMARY: cloudflare/@cf/moonshotai/kimi-k2.6   (DAILY 429 today)
fb1:     nvidia-nim/moonshotai/kimi-k2.6        (5-7% "unhashable" bug)
fb2:     cerebras/zai-glm-4.7                   (FAST 1-4s, RPM-saturated under burst)
fb3:     openrouter/z-ai/glm-5.1                (402 today — credits gated)
fb4:     cerebras/qwen-3-235b-a22b-instruct-2507 (FAST, shim handles RE-rejection)
fb5:     cloudflare/@cf/moonshotai/kimi-k2.5    (DAILY 429 today)
fb6:     nvidia-nim/z-ai/glm-5.1                (SLOW — median 62s, p95 226s)
```

### Bifrost provider timeouts
- cerebras: 60s · openrouter: 90s · cloudflare: 120s · gemini: 180s · mistral: 120s · cohere: 30s · groq: 30s · nvidia-nim: 300s · sambanova: 60s · github-models: 120s · opencode-zen: 90s

### Models registered (70 total across 11 providers)
- cerebras (4), cloudflare (6), cohere (6), gemini (8 — incl. gemini-3.5-flash, gemini-3.1-pro-preview NEW), github-models (4), groq (5), mistral (8 — incl. mistral-large-2512 NEW), nvidia-nim (11), opencode-zen (4), openrouter (11), sambanova (3).

## Open Issue: 5-Minute Idle Gaps Between Cascade Attempts

Observed pattern (test-13 run):
```
21:21:55-21:22:08  cascade exhausts (all 7 fail = 429+402+timeout) [13s]
21:22:08-21:27:01  ⏱ 5 MIN SILENCE
21:27:01-21:27:17  cascade exhausts again [16s]
21:27:17-21:28:59  ⏱ 1.7 MIN SILENCE
21:28:59           groq/gpt-oss-120b SUCCESS [eventually works]
```

40-min wall time → 34 min spent in gaps >60s. 12.6/8.2/7.6/4.5 min gaps observed in single session.

**Hypotheses for the 5-min wait** (need to verify):
1. `@ai-sdk/openai-compatible` retries with exponential backoff (default 2 retries / 2s+4s — but observed 5min, so longer than docs)
2. omo's `anthropic-context-window-limit-recovery` hook (has backoffFactor=2, retryTimerBySession map)
3. omo's `compaction-context-injector` (RECOVERY_COOLDOWN_MS=60000)
4. opencode's own session-status recovery (untraced)

**Already eliminated**:
- `runtime_fallback` — disabled, still gaps
- fallback_models in adapter — zero
- bifrost cascade itself — completes in 10-20s

## Latency Per Successful Target (test-13 sample)
| target | calls | median | total |
|---|---|---|---|
| nvidia-nim/z-ai/glm-5.1 | 12 | **62s** | 916s |
| cerebras/zai-glm-4.7 | 4 | 1.9s | 8s |
| cerebras/qwen-3-235b | 3 | 1.2s | 5s |
| cerebras/gpt-oss-120b | 1 | 3.5s | 4s |
| groq/openai/gpt-oss-120b | 3 | ~250ms | ~800ms |

**nvidia-nim/z-ai/glm-5.1 = THE LLM bottleneck** (62s median). Almost all LLM time goes here.

## Today's Quota State (free providers)
- **cloudflare**: 10k neurons/day POOLED — exhausted, recovers midnight UTC
- **openrouter**: 50 RPD POOLED (or 402 needs $10 deposit) — exhausted today
- **cerebras**: 30 RPM, 14400 RPD per model — alive, RPM-saturates during bursts
- **gemini**: 250 RPD per model — alive, some intermittent 503s
- **nvidia-nim**: ~1000 lifetime credits — alive, slow (cold-start + glm-5.1 slow-serving)
- **mistral**: alive
- **groq**: alive (6k TPM hard cap)
- **cohere**: works but rejects omo schemas (regex-pattern fail in tools[30])
- **sambanova**: 20 RPD per model — limited
- **github-models**: 20k RPM but 8k ctx cap (too small for omo handoffs)
- **opencode-zen**: ~5-10 calls/day pooled — demo-tier only

## Memory Notes (saved this session, in ~/.claude/projects/-home-jkr-Repos-coire-ansic/memory/)
- `feedback_double_check_first.md` — Bernardo flagged my surface-level grepping; must exhaustively check schemas + plugin hooks + constants on first pass
- `project_shim_param_rejection_retry.md` — shim pre-strip + post-retry for known-rejecting models
- `project_shim_kimi_normalizer.md` — Kimi control-token parser (now handles 2 formats)
- `project_kimi_reasoning_only_freeze.md` — Kimi K2.5/K2.6 on free hosts emit reasoning-only responses
- `project_cloudflare_kimi_tool_call_issue.md` — CF/kimi-k2.6 emits text instead of structured tool_calls
- `project_omo_pool_naming_load_bearing.md` — pool name substring drives omo's prompt variant selection
- `project_omo_agent_roles.md` — Momus is plan-reviewer ONLY (not code), Oracle for architecture

## Where to Pick Up

**Immediate next step**: research source of 5-min idle gaps. Specifically:
1. Add `"maxRetries": 0` to `coire.options` in `~/.config/opencode/opencode.json` — see if AI SDK was the source
2. If gaps persist, instrument omo plugin (`anthropic-context-window-limit-recovery` + others) by checking docker logs or opencode TUI debug
3. Verify which hook owns the wait timer

**Test prompt (used in test-13)**:
```
Build a Python autocomplete library backed by a trie:
- Trie class with insert(word, weight=1), search(word), delete(word),
  prefix_search(prefix, limit=10) returning (word, weight) pairs sorted
  by weight descending then alphabetically
- Case-insensitive by default, configurable via constructor
- Persistence: save(path) / load(path) using JSON (no pickle)
- Stats: word_count, node_count, longest_prefix_depth
- pytest tests + edge cases + corruption recovery on load
- README.md with usage + complexity analysis
Standard library only.

EXECUTION ORDER (no pauses):
1. Prometheus → .omo/plans/trie-plan.md
2. Hephaestus → trie.py + test_trie.py
3. Run pytest, fix until 100% green
4. README.md
5. Oracle review
```

Run in `~/scratch/omo-test-14` (next).

## Quota Reset
Cloudflare + OpenRouter daily pools reset midnight UTC. After that, primary cf/kimi-k2.6 should serve correctly + cascade depth drops dramatically. Expected: same workload completes in 15-20 min instead of 80+ min.

---

## 2026-05-22 Update — Pool Redesign Analysis (autonomous work block)

### Last session (20:56–00:06) recomputed

86 omo requests, 191.8 min wall, 160.5 min LLM time. 4 cascade-fail 429s (each 5min) + 2 idle gaps of 22+ min.

**Per fallback_index timing** (last session):
| fb | n | err | avg | max | sum |
|---|---|---|---|---|---|
| 0 | 118 | 80 | 1.0s | 64.6s | 119s |
| 1 | 72 | 69 | 0.13s | 3.5s | 9.7s |
| 2 | 69 | 63 | 0.18s | 3.5s | 12.6s |
| 3 | 63 | 63 | 0ms | 0ms | 0s |
| 4 | 63 | 58 | 0.22s | 5.7s | 14s |
| 5 | 58 | 58 | 0ms | 0ms | 0s |
| **6** | **57** | **6** | **66.3s** | **285s** | **3781s (63 min)** |

**Root cause confirmed**: cascade walk fb0-fb5 = instant errors (<5ms each). The "5min wait" = **nvidia/glm-5.1 itself at fb=6**, avg 66s, max 285s, occasionally hits 300s bifrost timeout → cascade-fail 429 to opencode.

### Pool utilization
| pool | parent reqs | % traffic | LLM min |
|---|---|---|---|
| omo-kimi | 69 | **96%** | 63.5 |
| omo-gpt-5-5 | 3 | 4% | 1.9 |
| omo-gemini | 0 | 0% | 0 |
| omo-utility | 0 | 0% | 0 |

3 of 4 pools idle. Sisyphus = 69 calls all routed to omo-kimi → all served by fb=6 nvidia/glm-5.1.

### Omo prompt-variant matcher (load-bearing finding)

`extractModelName("coire/omo-kimi")` → `"omo-kimi"` → `.includes("kimi")` → loads `buildKimiK26SisyphusPrompt`.

Branches: kimi / gpt-5-5 / claude-opus / **else → `buildDynamicSisyphusPrompt`** (default).

Default = NOT a stub. Dynamic prompt:
- If model has "gemini" in extracted name → injects gemini intent-gate + tool-mandate
- If "gpt" in name → reasoningEffort: medium
- Else → thinking enabled, 32k budget

= Default variant is the SAFER choice for mixed-family pool.

### Live verification (probes 2026-05-22 ~00:10 UTC)

**Tool-calling probe** (`get_weather` test):
| model | tool calls | latency | verdict |
|---|---|---|---|
| sambanova/DeepSeek-V3.2 | ✅ TOOLCALL | 2.03s | KEEP |
| sambanova/DeepSeek-V3.1 | ❌ TEXT-ONLY | 1.79s | **DROP — no tools** |
| sambanova/Llama-4-Maverick | ✅ TOOLCALL | 1.15s | OK but ~1370 arena |
| mistral/mistral-large-2512 | ✅ TOOLCALL | **0.60s** | KEEP |
| mistral/mistral-large-latest | ✅ TOOLCALL | 0.55s | dupe of 2512 |
| mistral/mistral-medium-3.5 | ✅ TOOLCALL | **0.33s** | KEEP — blazing |
| mistral/codestral-latest | ✅ TOOLCALL | 0.28s | code-spec only |
| groq/llama-3.3-70b-versatile | ✅ TOOLCALL | **0.24s** | 12k TPM too small |
| cerebras/llama3.1-8b | ❌ TEXT-ONLY | 0.59s | DROP |
| **nvidia/nemotron-49b** | **❌ TEXT-ONLY** | **4.83s** | **DROP — broken tools** |
| opencode-zen/nemotron-3-super | ✅ TOOLCALL | 3.36s | OK, 5-10/day pooled |
| opencode-zen/qwen3.6-plus | ❌ TEXT-ONLY | 0.49s | **broken today** |
| opencode-zen/deepseek-v4-flash | ✅ TOOLCALL | 1.76s | OK, 5-10/day pooled |

### LMArena Elo verified (arena.ai/leaderboard/text)

**Top tier (1450+)**: gemini-3.1-pro-preview 1488, gemini-3.5-flash 1480, **gemini-3-flash-preview 1473**, glm-5.1 1472, kimi-k2.6 1462, deepseek-v4-pro 1459

**High tier (1400-1450)**: kimi-k2.5-thinking 1449, qwen3.6-plus 1444, glm-4.7 1443, gemini-3.1-flash-lite 1436, deepseek-v4-flash 1433, deepseek-v3.2 1424, mistral-large-3/2512 1415, qwen3-next-80b-instruct 1402

**Mid (1370-1400)**: minimax-m2.7 1409, glm-4.5-air 1373, llama-4-maverick (public) ~1370, nemotron-3-super-120b 1361, gpt-oss-120b 1353, nemotron-49b 1343

### Live quota headers (2026-05-22 00:15 UTC)
| provider | model | rate limit |
|---|---|---|
| cerebras | zai-glm-4.7 | 5 RPM, 150 RPH, 2400 RPD, 30k TPM |
| mistral | large-2512 | **4 RPM (TIGHT)**, 250k TPM |
| mistral | medium-3.5 | **50 RPM (GENEROUS)**, 50k TPM |
| sambanova | DeepSeek-V3.2 | **20 RPD/model (TIGHT)** |
| groq | llama-3.3-70b | 1000 req/?, **12k TPM (too small)** |
| gemini | flash variants | 250 RPD per-model |
| openrouter | :free pooled | 50 RPD account-wide |
| cloudflare | account | 10k neurons/day (STILL EXHAUSTED) |

### PROPOSED redesign (drafted, NOT applied)

**Files written** (review then apply):
- `scripts/runtime/pool_weights.yaml.proposed` — merged omo-main pool, 12 targets, 7 distinct providers
- `adapters/omo/oh-my-openagent.json.proposed` — 8 work agents → omo-main, librarian/explore stay omo-utility, multimodal-looker stays omo-gemini

**Key changes**:
1. `omo-kimi` + `omo-gpt-5-5` merged → `omo-main` (name has no family substring → loads default dynamic variant, NOT misapplied kimi-tuned prompt)
2. **Primary**: `cerebras/zai-glm-4.7` (1443 arena, 2s, alive NOW — replaces dead CF as fb=0)
3. CF demoted to fb=2 (still useful when neurons reset)
4. nvidia/glm-5.1 moved to **fb=11 LAST RESORT** (the 66s bottleneck)
5. gpt-oss-120b demoted to fb=10 (~1353 arena, weak)
6. **4 NEW providers added**: mistral (2 models), sambanova/DeepSeek-V3.2, gemini-3-flash, openrouter/qwen3-next-80b
7. nvidia/nemotron-49b REMOVED (broken tool-calling)

### Apply commands (when Bernardo approves)

```bash
# 1. Diff review
diff scripts/runtime/pool_weights.yaml scripts/runtime/pool_weights.yaml.proposed
diff adapters/omo/oh-my-openagent.json adapters/omo/oh-my-openagent.json.proposed

# 2. Apply if good
mv scripts/runtime/pool_weights.yaml.proposed scripts/runtime/pool_weights.yaml
mv adapters/omo/oh-my-openagent.json.proposed adapters/omo/oh-my-openagent.json

# 3. Push to bifrost
python3 scripts/runtime/apply_pool_weights.py --plan scripts/runtime/pool_weights.yaml --apply

# 4. Push omo adapter to .93
scp adapters/omo/oh-my-openagent.json jkr@192.168.1.93:/home/jkr/.config/opencode/oh-my-openagent.json

# 5. Restart opencode session on .93 (kill TUI, relaunch — adapter loaded at startup)
```

### Rollback

```bash
git checkout scripts/runtime/pool_weights.yaml adapters/omo/oh-my-openagent.json
python3 scripts/runtime/apply_pool_weights.py --plan scripts/runtime/pool_weights.yaml --apply
scp adapters/omo/oh-my-openagent.json jkr@192.168.1.93:/home/jkr/.config/opencode/oh-my-openagent.json
```

### Open questions (decide on review)
- **Keep groq/gpt-oss-120b at fb=10?** 12k TPM cap means it will 429 on omo handoffs >12k tokens. Could drop entirely.
- **Add opencode-zen/deepseek-v4-flash at fb=12?** Working today, but pooled 5-10/day. Wastes shim's last-resort if exhausted.
- **Promote gemini-3-flash to fb=0?** Highest arena (1473) of healthy providers. Currently fb=1. Counter-argument: CF when alive serves at 1-3s and frees 250 RPD gemini for visual work.

