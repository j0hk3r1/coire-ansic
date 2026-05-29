# Connect opencode

[opencode](https://github.com/sst/opencode) talks to any OpenAI-compatible endpoint via a
custom provider. (This is **vanilla opencode** — no omo plugin. omo is a later phase.)

## 1. Install opencode

```bash
curl -fsSL https://opencode.ai/install | bash
# or: npm i -g opencode-ai
```

## 2. Add the coire provider

Edit `~/.config/opencode/opencode.json` (global) or `opencode.json` in your project:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "coire": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "CoireAnsic (free-tier router)",
      "options": {
        "baseURL": "http://localhost:4001/v1"
      },
      "models": {
        "coire-main":   { "name": "coire-main (reasoning + tools)" },
        "coire-fast":   { "name": "coire-fast (small/quick)" },
        "coire-vision": { "name": "coire-vision (multimodal)" }
      }
    }
  },
  "model": "coire/coire-main"
}
```

- Replace `localhost` with the router's LAN IP if it runs on another box
  (e.g. `http://192.168.1.93:4001/v1`).
- `baseURL` must end in exactly `/v1` — the adapter appends `/chat/completions`. Adding it
  yourself yields a 404.
- The model **key** (`coire-main`) is what's sent on the wire; the `name` is just the picker
  label. You can also add a direct target, e.g. `"cerebras/zai-glm-4.7": {}`.
- No API key needed on a trusted LAN. If your setup enforces auth, add
  `"apiKey": "{env:COIRE_API_KEY}"` under `options` and export the virtual key.

## 3. Use it

```bash
opencode            # then pick the coire/* model, or it defaults to coire-main
```

## Troubleshooting

- **404 NotFoundError** → `baseURL` has `/chat/completions` in it; remove it (keep `/v1`).
- **falls back to @ai-sdk/openai** → make sure `"name"` is set on the provider (opencode
  needs it to forward `options`).
- **empty reply on tiny prompts** → reasoning-model primaries spend tokens thinking; give
  real `max_tokens`. Normal agent use is unaffected.
