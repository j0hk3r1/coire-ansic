You are the bifrost-ops operator agent. Run a health check.

Task:
1. GET http://localhost:9118/api/health_status — get level + reasons
2. GET http://localhost:9118/api/circuit_breaker — list demoted targets w/ flags
3. GET http://localhost:9118/api/usage_estimates — per-provider 24h usage vs cap
4. GET http://localhost:9118/api/latency — pool-grouped P50/P95 (look for any P95 > 10s)
5. Read recent CB history: tail last 20 events from `~/.hermes/curator-pool/circuit_history.jsonl`

Write a concise health report to stdout in this format:

```
HEALTH: <green|yellow|red>  (reasons: <comma-separated>)
DEMOTED: <N total> — <K quota-deferred>, <L slow-retry>, <M pruned>
TOP USAGE: <provider>=<used>/<cap>(<pct>%) [highest 3]
SLOW TARGETS: <provider/model> P95=<X>s [if any > 5s]
RECENT ACTIONS: <N demotes, M restores in last 24h>

NOTES:
- <any specific concerns>
- <or "all systems nominal" if green and nothing notable>

RECOMMENDED ACTIONS: <none | list specific actions if needed>
```

Then append a one-line JSON summary using the op-log helper (NOT raw `echo
>>` — that has produced concatenated objects without newlines):
```bash
echo '{"job":"health","level":"<green|yellow|red>","demoted":<N>,"errs_24h":<N>,"notes":"<summary>"}' \
  | ~/hermes-free-cloud/operator/bin/op-log -
```
The helper injects `ts` automatically, validates JSON, appends with a
trailing newline, and prints the canonical log file path on stdout.

Do NOT take any actions — health check is read-only. If you detect something requiring intervention, list it under RECOMMENDED ACTIONS and end.

Use the `bifrost-ops` skill for API endpoints + state file paths.
