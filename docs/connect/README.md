# Connect your harness

CoireAnsic ships **no harness** — you bring your own and point it at the router.
The flow is always the same:

```
1. Install the router      ./install.sh        (brings up the shim :4001 + bifrost from .env)
2. Install your harness     (opencode / pi / hermes / Claude Code — your choice)
3. Connect it               copy-paste from the guide below
4. Use it free              your harness now runs on the free-tier cascade
```

## The one thing every harness needs

The router is an **OpenAI-compatible** endpoint:

| | |
|---|---|
| **Base URL** | `http://<ROUTER_HOST>:4001/v1` (e.g. `http://localhost:4001/v1`, or the LAN IP like `http://192.168.1.93:4001/v1`) |
| **Models** | `coire-main` (reasoning + tools), `coire-fast` (small/quick), `coire-vision` (multimodal) |
| **Auth** | none on a trusted LAN — inference is unauthenticated by default. Use any placeholder token if your client demands one. |

`model` may also be a direct `provider/model` (e.g. `cerebras/zai-glm-4.7`) to pin one target.

Claude Code is the exception — it speaks the **Anthropic** API, so it uses
`http://<ROUTER_HOST>:4001/anthropic` instead. See its guide.

## Guides

| harness | wire | guide |
|---|---|---|
| **opencode** | OpenAI `/v1` | [opencode.md](opencode.md) |
| **pi** | OpenAI `/v1` | [pi.md](pi.md) |
| **hermes** | OpenAI `/v1` | [hermes.md](hermes.md) |
| **Claude Code** | Anthropic `/anthropic` | [claude-code.md](claude-code.md) |

Coming later: Codex (needs a Responses-API bridge), omo (opencode plugin, needs extra pools).

## Verify the router first

```bash
curl http://localhost:4001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"coire-fast","messages":[{"role":"user","content":"say OK"}],"max_tokens":256}'
```

A JSON completion back = the router is good; now pick your harness above.
