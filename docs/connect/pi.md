# Connect pi

[pi](https://github.com/earendil-works/pi) (Mario Zechner's coding agent, formerly
`pi-mono`) supports custom OpenAI-compatible providers via a user config file.

## 1. Install pi

```bash
curl -fsSL https://pi.dev/install.sh | sh
# or: npm install -g @earendil-works/pi-coding-agent
```

## 2. Add the coire provider

Edit `~/.pi/agent/models.json` — `providers` is an **object keyed by provider id**:

```json
{
  "providers": {
    "coire": {
      "baseUrl": "http://localhost:4001/v1",
      "api": "openai-completions",
      "apiKey": "coire-local",
      "models": [
        { "id": "coire-main",   "name": "CoireAnsic main",   "contextWindow": 128000, "maxTokens": 8192 },
        { "id": "coire-fast",   "name": "CoireAnsic fast",   "contextWindow": 128000, "maxTokens": 8192 },
        { "id": "coire-vision", "name": "CoireAnsic vision", "contextWindow": 128000, "maxTokens": 8192 }
      ]
    }
  }
}
```

- `baseUrl` ends in `/v1` (pi appends `/chat/completions`). Use the LAN IP if remote.
- `api` must be `"openai-completions"` (the router is Chat Completions, not Responses).
- `apiKey` is required by pi's schema but the router ignores it on a trusted LAN — any
  placeholder (`"coire-local"`) works. It can also be `"$ENV_VAR"` or `"!command"`.

## 3. Use it

```bash
pi --model coire/coire-main
# thinking level: pi -m "coire/coire-main:medium"
```

## Notes

- pi sends the OpenAI `developer` role; the router handles it (coerced to `system` upstream
  where a provider rejects it).
- `id` is sent verbatim as the wire model name — `coire-main` or a direct `provider/model`.
