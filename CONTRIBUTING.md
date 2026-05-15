# Contributing

Bug reports + PRs welcome.

## Filing a bug

Include:
1. Output of `docker compose ps` + `systemctl --user list-timers`
2. Last 50 lines of: `journalctl --user -u circuit-breaker -n 50 --no-pager`
3. Output of: `curl -s http://localhost:9118/api/circuit_breaker | jq` (if dashboard up)
4. Which provider keys you have set in `.env` (just the names — never paste the values)
5. What you tried that didn't work + what you expected

## Adding a provider

1. **Probe upstream first** to confirm the free tier exists:
   ```bash
   curl -sS https://<provider>/v1/chat/completions \
     -H "Authorization: Bearer $KEY" \
     -H "Content-Type: application/json" \
     -d '{"model":"<m>","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
     -D /tmp/h.txt
   grep -i "x-ratelimit\|x-trial\|retry-after" /tmp/h.txt
   ```
   The response headers tell you the actual caps (often more useful than docs).

2. **Add env var to `.env.example`** with comment listing free-tier caps.

3. **Add to `bifrost/seed.sh`** — provider POST + first routing-rule entry.

4. **Add to `scripts/runtime/pool_weights.yaml`** — pick a pool, set initial
   weight conservatively (0.03-0.05 if you're unsure).

5. **Add to `scripts/runtime/auto_rebalance_weights.py:PROVIDER_TO_ENV`** so
   the rebalancer can sanitise snapshots + classify saturation.

6. **Add to `dashboard/app.py:PROVIDER_QUOTAS`** with header-verified caps.

7. **Test:** `./install.sh` should pass `[core] .env validated` and the
   dashboard should show the new provider in usage estimates.

## Adding a pool

1. Add the pool block to `scripts/runtime/pool_weights.yaml` (sum=1.0).
2. Run `python3 scripts/runtime/apply_pool_weights.py` — it auto-creates the
   bifrost routing rule.
3. Add a pi-models.json entry under `operator/pi-models.json` if pi-op
   should be able to use it.

## Adding an adapter

1. Create `adapters/<name>/` with the install fragment + any helper scripts.
2. Add `--with-<name>` flag handling to `install.sh`.
3. Add a line to the README's "Optional adapters" table.

## Code style

- Python 3.11+. Type hints where they help, not where they don't.
- Bash with `set -euo pipefail`. Use `step`/`ok`/`warn`/`die` helpers from
  install.sh for output.
- Idempotent scripts wherever possible — assume the user re-runs.
- Comments explain WHY (rate-limit quirk, provider bug, non-obvious
  ordering), not WHAT (read the code for that).

## Security

- **Never commit `.env`** — it's gitignored.
- **Never commit `bifrost/snapshot/providers.json` raw from a capture**
  without running `bifrost/snapshot.py` (which redacts bearer tokens +
  account IDs to `${ENV_VAR}` placeholders).
- If you find a leaked secret in git history, file a security issue
  privately rather than opening a public PR.

## What's out of scope

- Paid-tier inference (use [LiteLLM] or [OpenRouter] directly)
- Per-user auth (Bifrost has a global admin key; bring your own auth layer
  if you need multi-user)
- Streaming-only optimisations (bifrost already does this; we don't
  re-implement)

[LiteLLM]: https://github.com/BerriAI/litellm
[OpenRouter]: https://openrouter.ai
