---
name: coire-probe
description: Test a model/provider for tool-calling support, latency, and rate-limit headers. Use when the user wants to verify a new model works, check if a provider is alive, validate a model before adding to omo pool, test tool-calling on a model, check rate-limit headers, or diagnose why a model is failing. Trigger on phrases like "probe X", "test this model", "is X alive", "does X support tools", "check if cerebras/qwen works", "/coire-probe X", "try X with tools". Use this skill any time a specific provider/model is being evaluated — don't just curl manually.
---

# coire-probe

Verifies a single provider/model serves OpenAI-compatible chat completions correctly. Tests three things:
1. **Simple response** — does it return at all + how fast
2. **Tool-calling** — does it emit structured `tool_calls` (omo requirement) or just text
3. **(Optional) Big context** — does it handle 30k-token prompts (omo handoff size)

Plus parses rate-limit headers from the response so you can see RPM/quota structure.

## When to use

- New model added to a pool — verify it actually works before promoting
- User asks "is X alive?" / "does X work?"
- After provider recovery — confirm cascade can rely on it again
- Investigating why a model fails — narrows code-vs-config-vs-provider
- Pre-flight when scouting new providers

Don't use for: bulk monitoring (use `coire-monitor`), debugging session-level issues (different skill), or non-OpenAI-compat APIs.

## How to run

The script lives at `scripts/probe.py` next to this SKILL.md. It hits `localhost:4002` (shim) or `localhost:4001` (bifrost) — run wherever those are reachable. In this stack, both run on `.93` alongside opencode, so the script runs locally from opencode's perspective.

If running from a remote claude-code session where the shim is NOT on localhost, wrap with ssh: `ssh jkr@192.168.1.93 'python3 ~/.config/opencode/skills/coire-probe/scripts/probe.py <model>'`.

### Default call

```bash
python3 ~/.config/opencode/skills/coire-probe/scripts/probe.py cerebras/qwen-3-235b-a22b-instruct-2507
```

### Args

- `model` (positional, required) — `provider/model` form. Examples: `cerebras/zai-glm-4.7`, `zai/glm-4.7-flash`, `nvidia-nim/moonshotai/kimi-k2.6`, `openrouter/deepseek/deepseek-v4-flash:free`
- `--via shim|bifrost` — which entry point. Default `shim` (full omo path including shim normalizations). Use `bifrost` to bypass shim for raw provider behavior.
- `--no-tools` — skip the tool-calling test (useful for known-no-tools models)
- `--big` — also fire ~30k-token prompt to test context handling
- `--ctx 30000` — token count for `--big` test (default 30k matches omo handoff size)

### Examples

| user says | command |
|---|---|
| "probe qwen-3-235b" | `... cerebras/qwen-3-235b-a22b-instruct-2507` |
| "is z.ai alive?" | `... zai/glm-4.7-flash` |
| "test mistral large with big context" | `... mistral/mistral-large-2512 --big` |
| "check nvidia kimi raw (no shim)" | `... nvidia-nim/moonshotai/kimi-k2.6 --via bifrost` |
| "verify openrouter deepseek flash" | `... openrouter/deepseek/deepseek-v4-flash:free` |

## Output anatomy

Versioned header for confirmation:
```
## coire-probe v0.1 — model=<spec> via=<shim|bifrost>
```

Then sections:
1. **Simple probe** — `HTTP <code>, <time>s` + `TOOLCALL|TEXT-ONLY|ERROR|BADRESP: <detail>`
2. **Tool-calling probe** — same format, with get_weather payload
3. **Big-context probe** (if `--big`) — same format with ~30k tokens
4. **Rate-limit headers** (if present) — raw header dump
5. **Verdict** — bulleted callouts:
   - ✅ tool-calling works → usable in pool
   - ⚠️ TOOL-CALLING BROKEN → NOT for omo pools (model just emits text)
   - ⚠️ slow (>10s) → last-resort only
   - ❌ errors → reason

## How to use the verdict

| verdict | what to do |
|---|---|
| ✅ tool-calling works + fast | add to omo-main TIER A/B fallback |
| ✅ tool-calling works + slow | add to TIER C/D only |
| ⚠️ tool-calling broken | exclude from omo-main entirely. Maybe usable in omo-utility for small no-tools queries. |
| ❌ error | check the error message — auth (401), rate limit (429), endpoint mismatch (404), provider down (504) |

Read rate-limit headers to understand the provider's quota structure:
- `x-ratelimit-remaining-requests-minute: 5` → tight RPM, will saturate fast
- `x-ratelimit-limit-tokens-minute: 12000` → tight TPM, can't handle big handoffs
- `x-trial-endpoint-call-limit: 20` → daily cap, careful with placement

## Common errors and meaning

- `HTTP 401 invalid_iam_token` — auth setup wrong (Baidu Qianfan needs IAM, not bearer)
- `HTTP 402 Insufficient balance` — paid-only model (e.g., gemini-3.1-pro on free tier returns "limit: 0")
- `HTTP 429 Rate limit reached` — hit per-minute or per-day quota
- `HTTP 404 Not Found` — endpoint path mismatch (often Bifrost+Z.ai-style `/v1/` suffix issue)
- `HTTP 500 unhashable type: 'dict'` — known NVIDIA NIM bug on parallel_tool_calls + large tools array
- `HTTP 504 request timed out` — provider hung; check if model is cold/down today

## Reporting back

Keep terse. Most probes are 1-2 lines:
> "✅ cerebras/qwen-3-235b: tool-calling works, 0.34s, 5 RPM headroom"
> "❌ nvidia/gemma-4-31b: 504 timeout 60s (NVIDIA cold today)"

Only expand when the user explicitly asks for full output or the verdict has nuance (e.g., works-with-caveat).

## Limitations

- Doesn't test streaming (omo uses non-streaming in cascade). If model behaves differently on stream, this won't catch it.
- Doesn't test very long context (>30k unless `--ctx` bumped). Omo handoffs can hit 90k.
- Doesn't test concurrent throughput. Single-request only.
- Doesn't test multi-turn conversation. Single turn.

For deeper testing, run multiple times or modify the script's payloads.
