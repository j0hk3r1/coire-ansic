# Contributing

Bug reports + PRs welcome.

> The whole router is one declarative file: **`bifrost/config.json`**. Adding a provider or
> pool = editing that file + re-running `./install.sh`. The sections below add the rigor
> reviewers expect for an upstream PR (header probes, sane cascade ordering).

## Filing a bug

Include:
1. `docker compose ps` (which services are up)
2. Last 50 lines of `docker logs coire-bifrost`
3. The pool/model you called + the request, and `curl`'d response if you have it
4. Which provider keys you have set in `.env` (just the **names** — never the values)
5. What you tried, what happened, what you expected

## Adding a provider

1. **Probe upstream first** to confirm a free tier + see the real caps:
   ```bash
   curl -sS https://<provider>/v1/chat/completions \
     -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
     -d '{"model":"<m>","messages":[{"role":"user","content":"hi"}],"max_tokens":8}' \
     -D /tmp/h.txt
   grep -i "x-ratelimit\|x-trial\|retry-after" /tmp/h.txt   # caps live in the headers
   ```
2. **Add the env var to `.env.example`** with a one-line free-tier note.
3. **Add the provider to `bifrost/config.json`** under `providers.<name>`:
   - key: `{"name":"<n>-1","value":"env.<YOUR_KEY>","models":["*"],"weight":1}`
     (always `["*"]`, never `[]` — empty is deny-all).
   - if it's a custom OpenAI-compat endpoint, add `network_config.base_url` +
     `custom_provider_config` with `base_provider_type:"openai"` and
     `request_path_overrides` for `chat_completion`/`chat_completion_stream` (bifrost
     otherwise appends `/v1/chat/completions`, which double-`/v1`s base URLs ending in `/v1`).
   - put the key in the **native key field via `env.`** — do NOT hand-roll an
     `Authorization` header (bifrost injects it from the key).
4. **Slot it into a pool** — add the `provider/model` to a routing rule's `targets`
   (weighted) or `fallbacks` (ordered) under `governance.routing_rules`. Place it by
   observed reliability; verify it actually emits tool calls before adding it to
   `coire-main` (some free models are text-only — see the spec's behavior matrix).
5. **`./install.sh`** — it renders config, brings bifrost up, and smoke-tests the pools.

## Adding a pool

Add a rule under `governance.routing_rules` in `config.json`:
`{"id":"coire-x","name":"coire-x","cel_expression":"model == \"coire-x\"","targets":[…],"fallbacks":[…],"scope":"global","priority":<n>}`.
Weighted `targets` load-balance; `fallbacks` are the ordered failover cascade.

## Adding a harness

Harnesses aren't bundled — they're documented. Add a copy-paste guide under
`docs/connect/<harness>.md` (verified against a live router) and link it from
`docs/connect/README.md` + the README table.

## Code style

- Python 3.11+, bash with `set -euo pipefail`. Use the `step`/`ok`/`warn`/`die` helpers in
  `install.sh` for output.
- Idempotent scripts — assume the user re-runs.
- Comments explain WHY (a rate-limit quirk, a provider bug), not WHAT.

## Security

- **Never commit `.env`** (gitignored).
- **No secrets in `bifrost/config.json`** — keys are `env.<NAME>` references; the one
  non-key semi-secret (Cloudflare account id) is a `${CLOUDFLARE_ACCOUNT_ID}` placeholder
  rendered at install. If you add a provider, keep its secret in `.env`, referenced via `env.`.
- Found a leaked secret in git history? File a private security issue, not a public PR.

## Out of scope

- Paid-tier inference (use [LiteLLM] or [OpenRouter] directly).
- Multi-user auth (bring your own layer; the router is single-tenant on a trusted LAN).

[LiteLLM]: https://github.com/BerriAI/litellm
[OpenRouter]: https://openrouter.ai
