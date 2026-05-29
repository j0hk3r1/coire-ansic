> **ARCHIVED (pre-strip omo era).** References removed omo-* pools + deleted tooling. Returns/updates in the omo phase. See `docs/superpowers/specs/2026-05-29-strip-harness-agnostic-design.md`.

# Coire pools tailored for omo — comprehensive design

Source: cloned `code-yeongyu/oh-my-openagent@dev`, read all 11 agent
factories + 8 categories + skill subagents + variant matchers.

## Per-agent settings (verbatim from omo source)

| Agent | maxTokens | reasoningEffort | textVerbosity | thinking | temperature | Notes |
|---|---|---|---|---|---|---|
| Sisyphus (Kimi/GPT/Gemini variants) | 64000 | medium | — | — | — | orchestrator |
| Sisyphus (Claude Opus 4-7 variant) | 64000 | — | — | enabled, 32k | — | |
| Sisyphus-Junior (GPT variants) | 64000 | medium | — | — | 0.1 | category executor |
| Sisyphus-Junior (Claude/Kimi/Gemini) | 64000 | — | — | enabled, 32k | 0.1 | |
| Atlas | — | medium OR thinking | — | per variant | 0.1 | todo orchestrator |
| Prometheus | — | dual prompt path | — | — | — | strategic planner |
| Metis | — | — | — | enabled, 32k | 0.3 | plan analyzer |
| Hephaestus | 32000 | medium | — | — | — | deep worker |
| Oracle (GPT) | — | medium | high | — | 0.1 | architect consult |
| Oracle (Claude) | — | — | — | enabled, 32k | 0.1 | |
| Momus (GPT) | — | **xhigh** OR medium | high | — | 0.1 | reviewer (extra depth) |
| Momus (Claude) | — | — | — | enabled, 32k | 0.1 | |
| Librarian | — | — | — | — | 0.1 | docs/code search |
| Explore | — | — | — | — | 0.1 | grep specialist |
| Multimodal-Looker | — | — | — | — | 0.1 | image/PDF |

## Category settings

| Category | model variant | fallback hints |
|---|---|---|
| visual-engineering | gpt-5.5/high → gemini-3.1-pro → glm-5 → claude-opus | Gemini primary, no Claude/Kimi |
| ultrabrain | gpt-5.5/xhigh → gemini-3.1-pro → opus → glm-5.1 | max reasoning |
| deep | gpt-5.5/medium → opus → gemini-3.1-pro → kimi → glm | autonomous coding |
| artistry | gpt-5.5/xhigh → gemini-3.1-pro → opus → gpt-5.5 → kimi → glm | creative |
| quick | gpt-5.4-mini → haiku → gemini-3-flash → minimax → nano | speed |
| writing | gpt-5.5 → gemini-3-flash → kimi → sonnet → minimax | text gen |
| unspecified-low | gpt-5.3-codex → sonnet → kimi → gemini-3-flash → minimax | std work |
| unspecified-high | gpt-5.3-codex/medium → opus → gpt-5.5/high → glm-5 → kimi | complex |

## Required output budgets vs our pool caps

| Agent | omo expects | Our cap | Status |
|---|---|---|---|
| Sisyphus | 64000 | 8192 (omo-kimi) | **CLAMP TOO LOW** — plan truncation |
| Sisyphus-Junior | 64000 | 8192 (omo-kimi) | **CLAMP TOO LOW** |
| Hephaestus | 32000 | 16384 (omo-gpt-5-5) | **CLAMP** — half their budget |
| Oracle | (no explicit) | 16384 | OK |
| Momus | (no explicit) | 16384 | OK |

Fix: bump per-pool caps where the underlying primaries actually support it.

## Capability matching by cognitive style

omo categorizes models into 3 cognitive families:

### Claude family (mechanics-driven, instruction-following)
- Premium: Claude Opus 4.7, Sonnet 4.6, Haiku 4.5
- Free substitutes: Kimi K2.5/K2.6, GLM 4.7/5/5.1, qwen-3-235b, Cohere Command-a
- Our pool: `omo-kimi`

### GPT family (principle-driven, autonomous exploration)
- Premium: GPT-5.5/5.4/5.3-codex
- Free substitutes: gpt-oss-120b (Cerebras + OpenRouter), DeepSeek v3.2/v4-flash, qwen-coder
- Our pool: `omo-gpt-5-5`

### Gemini family (visual reasoning)
- Premium: Gemini 3.1-Pro, Gemini 3-Flash
- Free substitutes: gemini-3-flash-preview, gemini-2.5-flash, gemini-3.1-flash-lite, Qwen vision
- Our pool: `omo-gemini`

## Final pool composition design

### omo-kimi — Communicator pool

omo agents using this pool send maxTokens=64000 + reasoningEffort=medium.
Primaries MUST handle 16k+ ctx_output AND tolerate Claude-style instruction
prompts (cohere/mistral DROPPED from primary — they cap at 8k + reject medium).

```
weight  provider/model                          ctx_out  notes
0.28    cloudflare/@cf/moonshotai/kimi-k2.6      16384    Kimi K2.6 = omo's exact target substitute
0.22    cerebras/qwen-3-235b                    16384    Claude-substitute per omo doc
0.18    cloudflare/@cf/moonshotai/kimi-k2.5      16384    Kimi K2.5 alt
0.15    cerebras/zai-glm-4.7                    16384    GLM-substitute
0.10    gemini/gemini-3-flash-preview           65536    safety net (1M ctx, big output)
0.07    cerebras/gpt-oss-120b                   16384    GPT fallback within Kimi pool
fallbacks:
  nvidia-nim/moonshotai/kimi-k2.6  (slow cold-start, last resort)
  openrouter/openai/gpt-oss-120b:free
  openrouter/z-ai/glm-4.5-air:free
  cohere/command-a-03-2025      (8k cap — last resort only)
  mistral/mistral-medium-3.5    (8k cap)
  mistral/mistral-large-latest  (8k cap, 4 RPM)
  sambanova/DeepSeek-V3.1
  deepseek/deepseek-chat
```

### omo-gpt-5-5 — Deep Specialist pool

Hephaestus needs maxTokens=32000. Cerebras/openrouter/deepseek provide 16k+
ctx_output. Lead with the highest-fidelity GPT substitutes.

```
weight  provider/model                              ctx_out  notes
0.30    cerebras/gpt-oss-120b                       16384    OpenAI's GPT-OSS, Cerebras-hosted (fast)
0.20    openrouter/openai/gpt-oss-120b:free         16384    Same model via OR
0.15    openrouter/deepseek/deepseek-v4-flash:free  16384    DeepSeek (GPT-equivalent per omo)
0.12    mistral/codestral-latest                    16384    Code-specialized
0.10    cloudflare/@cf/qwen/qwen2.5-coder-32b        4096     CAUTION: 4k ctx_output — low weight
0.08    cerebras/qwen-3-235b                        16384    general capable
0.05    cloudflare/@cf/moonshotai/kimi-k2.6          16384    last-resort Claude fallback
fallbacks:
  sambanova/DeepSeek-V3.2
  sambanova/DeepSeek-V3.1
  mistral/mistral-medium-3.5  (8k cap)
  cerebras/zai-glm-4.7
  nvidia-nim/moonshotai/kimi-k2.6
  deepseek/deepseek-reasoner  (needs balance)
```

### omo-gemini — Visual pool

omo: NO Claude/Kimi here. Multimodal-Looker temperature 0.1.

```
weight  provider/model                                  ctx_out  notes
0.30    gemini/gemini-3-flash-preview                   65536    primary Gemini
0.22    gemini/gemini-2.5-flash                         8192     vision-capable
0.15    gemini/gemini-3.1-flash-lite-preview            8192     lite Gemini
0.13    mistral/pixtral-large-latest                    8192     vision (Mistral)
0.10    gemini/gemini-flash-latest                      8192     fallback Gemini
0.05    github-models/openai/gpt-4o-mini                4096     vision GPT-mini
0.05    cohere/command-a-vision-07-2025                 16384    cohere vision (low — omo says no Cohere)
fallbacks:
  nvidia-nim/meta/llama-3.2-90b-vision-instruct
  nvidia-nim/meta/llama-3.2-11b-vision-instruct
  mistral/pixtral-12b-latest
  openrouter/nvidia/nemotron-nano-12b-v2-vl:free
  cohere/c4ai-aya-vision-32b
```

### omo-utility — Speed pool

Explore/Librarian get FULL Sisyphus context (~25k tokens) on handoff.
Original was sized for "small fast" which 413'd on the big handoff.
Gemini Flash variants (1M ctx, fast, free) lead now.

```
weight  provider/model                              ctx_in    ctx_out  notes
0.28    gemini/gemini-3.1-flash-lite-preview        1048576   8192     1M ctx, fast
0.22    gemini/gemini-flash-latest                  1048576   8192     same family
0.15    gemini/gemini-2.5-flash-lite                1048576   8192     lite
0.10    gemini/gemini-flash-lite-latest             1048576   8192     fallback
0.10    cerebras/llama3.1-8b                        8192      4096     small ctx, but inst-speed
0.08    groq/llama-3.1-8b-instant                   131072    4096     inst, but 6k TPM cap
0.05    groq/openai/gpt-oss-20b                     131072    4096     inst small GPT-OSS
0.02    nvidia-nim/meta/llama-3.3-70b-instruct      131072    4096     mid-tier
fallbacks (all small-ctx — only useful for short handoffs):
  github-models/openai/gpt-4.1-mini
  github-models/openai/gpt-4o-mini
  github-models/openai/gpt-4.1-nano
  cloudflare/@cf/meta/llama-3.1-8b-instruct-fp8
  mistral/mistral-small-latest
  groq/qwen/qwen3-32b
  groq/llama-3.3-70b-versatile
```

## Per-pool max_tokens caps (strip-shim aware)

```
POOL              CAP    REASON
omo-kimi          16384  primaries all support 16k+; was 8192 → bumped
omo-gpt-5-5       32768  Hephaestus expects 32k; gpt-oss + cerebras support
omo-gemini        32768  Gemini supports 65k, plenty of headroom
omo-utility       8192   gemini-flash variants cap at 8k output
best              16384  cerebras + cohere balance
code              16384  
mid               16384
fast              8192   small models
compress          8192   long input but short summary output
vision            8192   
ops               4096   tiny operator outputs
```

## Per-pool reasoning_effort policy

```
POOL              DROPS medium/low/minimal?  REASON
omo-kimi          no   (was yes, FIX: kimi/qwen accept it)
omo-gpt-5-5       no   (gpt-oss uses it for thinking depth)
omo-gemini        no   (gemini ignores)
omo-utility       no   (small models ignore)
best              partial — drop only on cohere/mistral fallback hits
code              no
mid               yes   (has cohere as primary — drops)
compress          yes
vision            no
fast              no
ops               no
```

## Cross-pool fallback chains in adapters/omo/oh-my-openagent.json

Each agent gets PRIMARY pool + ordered fallback chain of OTHER pools
that match its cognitive style:

```
sisyphus:          omo-kimi → best → omo-gpt-5-5 (last resort)
atlas:             omo-kimi → mid → best
metis:             omo-kimi → best
sisyphus-junior:   omo-kimi → mid
prometheus:        omo-gpt-5-5 → omo-kimi (dual-prompt support)
hephaestus:        omo-gpt-5-5 → code (Strict GPT — no Claude fallback)
oracle:            omo-gpt-5-5 → omo-kimi → omo-gemini (matches omo's order)
momus:             omo-gpt-5-5 → omo-kimi
librarian:         omo-utility → fast
explore:           omo-utility → fast
multimodal-looker: omo-gemini → vision
```

Categories follow same pattern.

## Variant (reasoning_effort) values per agent

```
sisyphus:        medium
sisyphus-junior: medium
atlas:           medium
metis:           high
prometheus:      high
hephaestus:      medium
oracle:          medium
momus:           xhigh    (omo's signature — "ruthless reviewer")
explore:         (none — temperature 0.1 only)
librarian:       (none)
multimodal-looker: (none)
```

Categories:
```
visual-engineering: high
ultrabrain:         xhigh
deep:               medium
artistry:           xhigh
quick:              (none)
writing:            (none)
unspecified-low:    medium
unspecified-high:   medium
```
