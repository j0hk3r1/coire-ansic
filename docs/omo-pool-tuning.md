# Pool tuning aligned to omo's per-agent constraints

Source: `https://github.com/code-yeongyu/oh-my-openagent/blob/dev/docs/guide/agent-model-matching.md`

omo distinguishes three "cognitive styles" — Claude-family (mechanics-driven
orchestrators), GPT-family (principle-driven deep workers), Gemini-family
(visual reasoning) — and warns substitution across families degrades quality.
The pools below restructure our existing 7 to match omo's recommendations
using ONLY free-tier providers we already have.

## Pool tuning per omo agent type

### `best` (Communicator pool — Sisyphus / Atlas / Prometheus / Metis / Junior)

omo's Claude-family models: Opus 4.7, Sonnet 4.6, Kimi K2.5/K2.6, GLM-5.
Our free equivalents (Kimi + GLM + Qwen-235b + Cohere Command-a) ARE the
Claude-substitutes per omo's doc.

```
0.22  cf-openai/@cf/moonshotai/kimi-k2.6        (AA=43, primary Claude-substitute)
0.20  cerebras/qwen-3-235b-a22b-instruct-2507  (Claude-like reasoning, fast)
0.15  cerebras/zai-glm-4.7                     (GLM-5 substitute)
0.13  cohere/command-a-03-2025                 (Cohere — communicator-style)
0.10  gemini/gemini-3-flash-preview            (general capable, big ctx)
0.08  mistral/mistral-large-latest             (Mistral large)
0.07  mistral/mistral-medium-3.5               (Mistral medium)
0.05  sambanova/DeepSeek-V3.1                  (variety, 20 RPD cap)
```
Removed cerebras/gpt-oss-120b from best primary — its principle-driven style
is wrong for communicator/orchestrator agents per omo. Moves to `code`.

### `code` (Deep Specialist pool — Hephaestus / Oracle / Momus / deep + ultrabrain categories)

omo's GPT-family: GPT-5.5 / GPT-5.4 / GPT-5.3-codex / DeepSeek v3.2.
Hephaestus specifically REQUIRES GPT-family — no Claude substitute viable.
Our gpt-oss + deepseek + qwen-coder fit.

```
0.25  cerebras/gpt-oss-120b                              (GPT-OSS, 60k TPM)
0.18  openrouter/openai/gpt-oss-120b:free               (alt GPT-OSS path)
0.15  openrouter/deepseek/deepseek-v4-flash:free        (DeepSeek — GPT-equivalent OSS)
0.12  mistral/codestral-latest                          (code-specialized)
0.10  cf-openai/@cf/qwen/qwen2.5-coder-32b-instruct     (code-specialized)
0.10  cerebras/qwen-3-235b-a22b-instruct-2507           (general capable)
0.07  cf-openai/@cf/moonshotai/kimi-k2.6                (last-resort Claude fallback)
0.03  mistral/mistral-medium-3.5                        (small slot)
```
Removed groq primaries — 6k TPM cap kills multi-file code reasoning.

### `vision` (Visual pool — Multimodal Looker / visual-engineering / artistry categories)

omo: Gemini 3.1-Pro primary; Qwen as Gemini alternative; explicitly NO
Claude/Kimi (wrong style for visual).

```
0.30  gemini/gemini-3-flash-preview            (Gemini primary, vision)
0.20  gemini/gemini-2.5-flash                  (Gemini alt)
0.15  gemini/gemini-3.1-flash-lite-preview     (lighter Gemini)
0.13  mistral/pixtral-large-latest             (vision capable)
0.10  nvidia-nim/meta/llama-3.2-90b-vision-instruct  (alt vision path)
0.07  github-models/openai/gpt-4o-mini         (vision GPT-mini)
0.05  cohere/command-a-vision-07-2025          (vision Cohere, low weight)
```
Removed pixtral-12b (smaller than large). Removed cohere/c4ai-aya-vision-32b
(per omo, Cohere is communicator-style, not Gemini-substitute for visual).

### `compress` (Writing pool — writing category, Kimi K2.5 default per omo)

```
0.25  cf-openai/@cf/moonshotai/kimi-k2.5       (Kimi K2.5 — exact omo match)
0.20  cf-openai/@cf/moonshotai/kimi-k2.6       (Kimi K2.6 — newer)
0.18  gemini/gemini-3-flash-preview            (1M ctx, fast)
0.15  mistral/mistral-medium-3.5               (capable)
0.10  cerebras/qwen-3-235b-a22b-instruct-2507  (Qwen alt)
0.07  mistral/mistral-medium-latest            (Mistral medium)
0.05  cf-openai/@cf/google/gemma-4-26b-a4b-it  (small slot)
```

### `fast` (Utility pool — Explore / Librarian / quick category)

omo: GPT-5.4-mini-fast → Qwen 3.5+ → MiniMax → Haiku → nano. Speed over quality.
"Never upgrade these to Opus — wasteful overprovision."

```
0.25  groq/llama-3.1-8b-instant                (inst LPU)
0.20  cerebras/llama3.1-8b                     (inst)
0.15  groq/openai/gpt-oss-20b                  (inst small GPT-OSS)
0.10  groq/qwen/qwen3-32b                      (inst Qwen)
0.10  github-models/openai/gpt-4.1-mini        (fast mini)
0.08  github-models/openai/gpt-4o-mini         (vision-capable mini)
0.07  gemini/gemini-3.1-flash-lite-preview     (lite Gemini)
0.05  gemini/gemini-flash-latest               (Gemini fallback)
```
All inst/fast tier. Zero frontier models. omo wins here vs current state
(where cerebras qwen-235b leaked into fast).

### `mid` (Sonnet-equivalent pool — Atlas / Sisyphus-Junior)

Balanced quality + latency mid-tier.

```
0.20  cf-openai/@cf/moonshotai/kimi-k2.6        (Sonnet-substitute)
0.18  cerebras/qwen-3-235b-a22b-instruct-2507  (general capable)
0.15  gemini/gemini-3-flash-preview            (fast capable)
0.12  mistral/mistral-medium-3.5               (Mistral medium)
0.10  github-models/openai/gpt-4.1-mini        (fast GPT-mini)
0.10  groq/qwen/qwen3-32b                      (inst Qwen)
0.08  mistral/mistral-medium-latest            (Mistral medium)
0.07  cf-openai/@cf/qwen/qwen2.5-coder-32b-instruct  (code-tilt)
```

### `ops` (Operator agents — pi-op-* / sub-agent fan-out)

High-RPD inst/fast tier only. Quality not needed.

```
0.30  cerebras/llama3.1-8b                     (inst, 14400 RPD)
0.25  groq/llama-3.1-8b-instant                (inst, 14400 RPD)
0.18  groq/openai/gpt-oss-20b                  (inst, 14400 RPD)
0.10  cerebras/zai-glm-4.7                     (fast, 14400)
0.10  cerebras/gpt-oss-120b                    (fast, 14400)
0.07  github-models/openai/gpt-4.1-mini        (fast mini)
```

## Refined adapters/omo/oh-my-openagent.json mapping (small changes)

```json
{
  "agents": {
    "sisyphus":          {"model": "coire/best"},
    "atlas":             {"model": "coire/mid"},      // ↑ was best; omo says Sonnet-tier
    "prometheus":        {"model": "coire/best"},
    "metis":             {"model": "coire/best"},
    "hephaestus":        {"model": "coire/code"},
    "oracle":            {"model": "coire/code"},     // ↑ was best; omo says GPT-family
    "momus":             {"model": "coire/code"},
    "librarian":         {"model": "coire/fast"},
    "explore":           {"model": "coire/fast"},
    "multimodal-looker": {"model": "coire/vision"},
    "sisyphus-junior":   {"model": "coire/mid"}
  },
  "categories": {
    "visual-engineering": {"model": "coire/vision"},
    "artistry":           {"model": "coire/vision"},  // ↑ was best; omo says Gemini-family
    "ultrabrain":         {"model": "coire/code"},    // ↑ was best; omo says GPT-5.5 xhigh
    "deep":               {"model": "coire/code"},    // ↑ was best; omo says GPT-5.5 medium
    "quick":              {"model": "coire/fast"},
    "writing":            {"model": "coire/compress"},
    "unspecified-high":   {"model": "coire/best"},
    "unspecified-low":    {"model": "coire/mid"}
  }
}
```

## Per-omo critical constraints honored

1. **Hephaestus has no Claude alternative** — code pool leads with cerebras/gpt-oss-120b + openrouter/gpt-oss-120b + deepseek-v4-flash. No Claude-family models in code pool primaries.
2. **Visual work avoids Claude/Kimi** — vision pool led by Gemini variants; only Cohere-vision (lowest weight) sneaks in.
3. **Utility agents don't get Opus** — fast pool has zero frontier models; only inst/fast-tier small models.
4. **Communicator/Sonnet-tier agents get Claude-family substitutes** — best + mid lead with Kimi K2.6, GLM-4.7, Qwen-235b, Cohere.
5. **Writing category gets Kimi K2.5 exact match** — compress pool leads with cf-openai/@cf/moonshotai/kimi-k2.5.
6. **No MiniMax for deep agents** — we don't have MiniMax, no issue.

## What this re-tuning does NOT solve

- Hephaestus's "GPT-5.5 ONLY, cannot degrade" requirement can't fully be honored
  with free OSS — gpt-oss is the closest substitute but quality_score=33 vs GPT-5.5's
  ~60. Real Hephaestus quality on coire is degraded.
- visual-engineering / artistry depend on Gemini Pro per omo — we have Flash variants
  only (free-tier zero for pro). UX will be acceptable, not optimal.

## Model-variant prompt routing — a deeper constraint

Cloned omo and read `src/agents/*/AGENTS.md` + `src/shared/model-requirements.ts`.
Every Sisyphus/Atlas/Prometheus/Junior agent has MULTIPLE prompt variants
(default/Claude ~1100 LOC, gpt.ts ~120 LOC, gemini.ts, kimi.ts) that auto-route
based on the model identifier string.

When we pin `sisyphus -> coire/best`, omo sees model name "best" (no claude/
gpt/gemini/kimi pattern match) and falls back to the **default (Claude)
prompt variant**. That prompt expects mechanics-driven instruction following.

Implication: code pool's primary cerebras/gpt-oss-120b will get the Claude
prompt, not the GPT-tuned one. Should still work — gpt-oss is instruct-tuned
and handles Claude-style prompts decently — but loses the prompt-tuning
benefit omo's developers put in.

Two possible upgrades (future work, not blocking):

**(a) Hint the family in the pool alias name.** Expose dual aliases:
  `coire/best-claude` -> same backend rule as best (Claude-style primaries)
  `coire/gpt-code`    -> same backend as code (GPT-style primaries)
  `coire/gemini-vision` -> same backend as vision

omo's variant detector matches substring `claude`/`gpt`/`gemini`/`kimi` in
the model id; the alias name carries the hint. bifrost can host multiple
routing rules with the same target list — cheap to add.

**(b) Per-agent direct-target pinning.** Skip pool aliases for agents whose
prompt-variant matters (Sisyphus, Hephaestus). Pin `sisyphus ->
coire-bifrost/kimi-k2.6` directly so omo sees "kimi" and uses the Kimi
variant. Loses pool routing benefits (no cascade) for those agents.

Recommendation: ship pool tuning first (this doc), then add (a) as v2 if
prompt-variant mismatch shows up in real omo runs.

## Apply path

When approved:
1. Replace pool_weights.yaml `best`, `code`, `mid`, `fast`, `compress`, `vision`, `ops` sections with the blocks above.
2. Update `adapters/omo/oh-my-openagent.json` with the refined agent/category mapping.
3. `rsync` yaml to .93; run `apply_pool_weights.py` (auto-runs sync_key_models + build_models_list).
4. Restart opencode session on .93 to pick up new omo config.

Not applying autonomously — exceeds audit auto-bounds (>2 weight changes per
tick, multiple pools touched). Awaiting greenlight.
