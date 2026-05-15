---
name: bifrost-ops
description: Ops knowledge for the coire-ansic stack — bifrost (4001), strip-shim (4002), dashboard (9118), circuit-breaker daemon, hermes-agent (gateway/dashboard). Use this skill when checking system health, reacting to dashboard alerts, integrating new providers, or applying patches to hermes after upstream updates.
---

# Bifrost-Ops — Operator Knowledge Base

You operate the local coire-ansic stack on this host. Read-only by default; mutate only when explicitly instructed.

## Stack topology

| Service | Port | Role |
|---|---|---|
| bifrost | 4001 | LLM gateway w/ 10 providers + 6 routing pools |
| strip-shim | 4002 | OpenAI-style proxy in front of bifrost (reasoning/tool-call normalization) |
| searxng | 8891 | Self-hosted meta-search |
| camofox | 9378 | Anti-detect Firefox browser |
| dashboard | 9118 | Pool monitor + circuit breaker UI |
| hermes-gateway | systemd user | Messaging integration |
| coire-dashboard | 9120 | Hermes web UI |
| circuit-breaker | systemd user | CB daemon |

## Authoritative state files

| Path | Purpose |
|---|---|
| `~/coire-ansic/scripts/runtime/pool_weights.yaml` | Plan: weight per pool target |
| `~/coire-ansic/bifrost/excluded_models.json` | Permanent excludes (Qwen XML, $0-balance, tier-gated) |
| `~/coire-ansic/bifrost/candidate_providers.json` | Free-tier candidates w/ verified-2026-05-11 status |
| `~/.coire/curator-pool/circuit_state.json` | Live CB state (demoted, daily_quota, pruned) |
| `~/.coire/curator-pool/cooldown_status.json` | Dashboard-facing CB snapshot (read this, not state.json) |
| `~/.coire/curator-pool/live_rules.json` | Live bifrost routing rules snapshot |
| `~/coire-ansic/.env` | All provider API keys (use `set -a; source ...` to expose) |

## Dashboard API (read-only)

Base: `http://localhost:9118`

| Endpoint | Returns |
|---|---|
| `/api/health_status` | `{level: green|yellow|red, reasons: [], demoted_count, errors_24h}` |
| `/api/circuit_breaker` | Full demoted list w/ pools, restore_at, daily_quota/pruned flags |
| `/api/usage_estimates` | Per-provider 24h request count + real RPD/RPM/TPM cap |
| `/api/latency` | Per-target P50/P95 grouped by pool |
| `/api/weight_drift` | pool_weights.yaml plan vs live bifrost weights |
| `/api/recent_errors?h=N` | Last N hours of errors |
| `/api/recent_successes?h=N` | Last N hours of successes |
| `/api/pool_targets` | Live bifrost routing rules |
| `/api/excluded_models` | Permanent exclude list |
| `/api/circuit_breaker/restore` | POST `{provider, model}` to force-restore |
| `/api/circuit_breaker/prune` | POST `{provider, model}` to permanently prune |

## Bifrost admin API

Base: `http://localhost:4001/api` — auth: basic, `admin` + `$BIFROST_PASS` from `.env`.

```bash
set -a; source ~/coire-ansic/.env; set +a
curl -s -u admin:$BIFROST_PASS http://127.0.0.1:4001/api/providers          # list providers
curl -s -u admin:$BIFROST_PASS http://127.0.0.1:4001/api/providers/<name>   # provider detail
curl -s -u admin:$BIFROST_PASS http://127.0.0.1:4001/api/governance/routing-rules  # pools
curl -s -u admin:$BIFROST_PASS "http://127.0.0.1:4001/api/logs?limit=100&order=desc"  # recent calls
```

## Reaction playbook

### When dashboard shows RED
1. Read `/api/circuit_breaker` — see what's demoted
2. Classify each entry:
   - `daily_quota: true` → wait for UTC midnight; cron `5 1 * * *` will auto-restore
   - `pruned: true` w/ `fail_count >= 10` → run `circuit_breaker.py --restore-all` (check upstream is actually back first)
   - Plain cooldown (no flags) → leave alone, CB daemon will tick
3. If a quota-deferred model already past `restore_at` but not restored → daemon stuck, run `--restore-quota`

### When new key arrives in `~/.coire/operator/incoming_keys/`
Format: filename = `provider-name.txt`, contents = `KEY=<value>` plus optional `BASE_URL=<url>` and `MODELS=<comma list>`.

Workflow:
1. Read the file
2. Probe key directly against upstream (use `curl` w/ Authorization header)
3. Capture rate-limit headers (`x-ratelimit-*`, `x-trial-*`) — these define real free tier
4. List available models via `/v1/models` if reachable
5. Decide: standard bifrost provider (groq/gemini/mistral/cohere/etc — built-in) or custom (cf-openai-style request_path_overrides)
6. Register via `POST /api/providers` then `PUT /api/providers/<name>` w/ keys (see existing seed.sh + integration commits for patterns)
7. Update `.env` to persist
8. Update `~/coire-ansic/dashboard/app.py` PROVIDER_QUOTAS w/ real caps
9. Append low-weight target to `pool_weights.yaml` (start at 0.03-0.05 floor, 20 RPD providers always conc=1)
10. Run `apply_pool_weights.py`
11. Live-test via 5 calls to relevant pool
12. Move incoming file to `~/.coire/operator/done/`

### When hermes-agent has upstream updates
Check via `cd ~/hermes-agent && git fetch && git log HEAD..origin/main --oneline`.

If updates present:
1. `git stash` (saves our patches)
2. `git pull --ff-only`
3. Run `bash ~/coire-ansic/scripts/install/patch_hermes_tui_model.sh`
4. Run `bash ~/coire-ansic/scripts/install/patch_hermes_jina_extract.sh`
5. Verify `extract_backend` in `~/.hermes/config.yaml` (we want `firecrawl`, NOT `jina` — patch overwrites)
6. `systemctl --user restart hermes-gateway coire-dashboard`
7. Smoke-test gateway via `curl -m 5 http://127.0.0.1:9120/`

## Hard rules (do NOT do)

- Do NOT touch `~/.hermes/sessions/` or `~/.hermes/kanban.db` (hermes-agent owns)
- Do NOT edit `~/.hermes/config.yaml` model fields (hermes self-manages)
- Do NOT remove a provider from bifrost — only disable keys
- Do NOT raise concurrency above tested caps (mistral=2, openrouter=1, sambanova=1)
- Do NOT add a pool target without an IQ band justification in the commit msg
- Do NOT auto-merge an `incoming_key` w/ unknown provider — log + skip if vendor isn't in candidate_providers.json

## Logging contract

Each ops run writes a one-line JSON to `~/.coire/operator/logs/YYYY-MM-DD.jsonl`:
```json
{"ts":"2026-05-12T01:00:00Z","job":"health","level":"green","actions":[],"notes":"..."}
```

If action taken, include `"actions":[{"kind":"force_restore","target":"openrouter/llama-3.3-70b-instruct:free"}]`.
