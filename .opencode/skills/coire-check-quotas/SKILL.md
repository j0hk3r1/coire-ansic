---
name: coire-check-quotas
description: Ping each provider with tiny request, parse rate-limit headers, summarize daily/monthly remaining quota. Use end-of-day to plan next session, before kicking off heavy tests, when wondering if a provider is burned. Trigger on phrases like "check quotas", "how much budget left", "what's burned today", "/coire-check-quotas", "any quota left", "OR credits", "CF neurons". Use whenever user wants to know if free quotas will sustain another test run.
---

# coire-check-quotas

Hits each registered provider with a tiny "hi" request to read live rate-limit headers. Reports remaining quota per provider. Costs 1 request per provider (~11 requests total).

## When to use

- End-of-day check before tomorrow's session
- Before kicking off a long test
- When suspecting a provider is burned (cascade reaching deep fbs)
- Debugging "why is X always 429ing"

## How to run

```bash
~/coire-tools/coire-check-quotas
```

Reads keys from `~/coire-ansic/.env`. No args. Outputs per-provider blocks with headers.

## Output anatomy

Per provider:
```
### cerebras/zai-glm-4.7 · HTTP 200
  x-ratelimit-limit-requests-day: 2400
  x-ratelimit-remaining-requests-day: 2398
  x-ratelimit-limit-tokens-minute: 30000
  ...
```

Plus at the bottom: OpenRouter account-wide usage from `/api/v1/auth/key`.

## Interpreting

- `remaining-requests-day` near 0 → that provider is burned for the day
- `remaining-tokens-minute` low → about to hit TPM cap on next call
- `retry-after: N` → currently rate-limited, wait N seconds
- `HTTP 429` with `limit: 0` → paid-only on free tier (gemini-3.1-pro pattern)

## Watch out

- This skill BURNS quota (1 req per provider). Don't run it every minute.
- Cohere has 20/day trial cap — running this counts toward it.
- Some providers (NVIDIA-NIM) don't expose rate-limit headers — you'll see no quota info for them.

## Limitations

- Only probes the model registered as "primary" per provider. Doesn't enumerate all models.
- Sambanova / NVIDIA-NIM headers are sparse → less actionable info.
- Z.ai per-model quota means probing glm-4.7-flash doesn't tell you about glm-4.5-flash's bucket.
