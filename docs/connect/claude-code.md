# Connect Claude Code

Claude Code speaks the **Anthropic Messages API**, not OpenAI — so it does **not** use the
`/v1` endpoint. Instead it points at bifrost's built-in **`/anthropic`** drop-in route, which
translates Anthropic ↔ provider and runs the same `coire-*` pools.

> Verified: an Anthropic-format request to `…:4001/anthropic/v1/messages` with
> `model: coire-main` routes through the cascade and returns a normal Anthropic response.

## 1. Install Claude Code

```bash
npm i -g @anthropic-ai/claude-code
```

## 2. Point it at the router

Add an `env` block to `~/.claude/settings.json`:

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4001/anthropic",
    "ANTHROPIC_AUTH_TOKEN": "coire-local",
    "ANTHROPIC_DEFAULT_OPUS_MODEL":   "coire-main",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "coire-main",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL":  "coire-fast"
  }
}
```

Or as shell exports:

```bash
export ANTHROPIC_BASE_URL="http://192.168.1.93:4001/anthropic"   # LAN IP if remote
export ANTHROPIC_AUTH_TOKEN="coire-local"
export ANTHROPIC_DEFAULT_OPUS_MODEL="coire-main"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="coire-fast"
claude
```

- `ANTHROPIC_BASE_URL` ends in `/anthropic` (not `/v1`). Claude Code appends
  `/v1/messages`.
- `ANTHROPIC_AUTH_TOKEN` is required by Claude Code but ignored by the router on a trusted
  LAN — any placeholder works.
- The `*_MODEL` vars map Claude's opus/sonnet/haiku tiers onto router pools. Map them
  however you like (e.g. point haiku at `coire-fast` to save the big pool's quota).

## Tradeoff to know

The `/anthropic` route is served by **bifrost directly** — it bypasses the optional
strip-shim. That's fine: the shim's fixes are OpenAI-format quirks (tool-id rewriting,
control-token parsing) that don't apply to the Anthropic wire format. If you hit an
Anthropic-path provider quirk, open an issue.

## Verify

```bash
curl http://localhost:4001/anthropic/v1/messages \
  -H "Content-Type: application/json" -H "anthropic-version: 2023-06-01" \
  -d '{"model":"coire-main","max_tokens":256,"messages":[{"role":"user","content":"say OK"}]}'
```
