# Connect hermes

hermes is an OpenAI-compatible agent client, so it connects exactly like any other:
point its model endpoint at the router's `/v1` and use a `coire-*` model name.

## Configure

Set hermes' OpenAI-compatible endpoint:

| setting | value |
|---|---|
| base URL / `OPENAI_BASE_URL` | `http://localhost:4001/v1` (or the router's LAN IP) |
| API key | any placeholder — inference is unauthenticated on a trusted LAN |
| model | `coire-main` · `coire-fast` · `coire-vision` (or a direct `provider/model`) |

If hermes reads an env file:

```bash
OPENAI_BASE_URL=http://192.168.1.93:4001/v1
OPENAI_API_KEY=coire-local
HERMES_MODEL=coire-main
```

## Verify

```bash
curl http://localhost:4001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"coire-main","messages":[{"role":"user","content":"say OK"}],"max_tokens":256}'
```

## Notes

- Point hermes' base URL at `:4001/v1` — the strip-shim front door (always on). It normalizes
  provider tool-call quirks and reasoning-only/param-rejection cases, then forwards to bifrost
  (which itself sits behind it on `:4011`). Older configs that used `:4002` should move to `:4001`.
- For heavy concurrent / orchestrated runs, give requests adequate `max_tokens` so
  reasoning-model primaries return content rather than spending the budget on thinking.
