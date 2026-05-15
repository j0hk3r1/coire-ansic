---
name: pi-subagent
description: Delegate large coding/ops tasks to the pi-mono operator agent running on this host. Pi has its own tool harness (read/bash/edit/write/git), uses bifrost pools for inference, and maintains the hermes-free-cloud stack. Use this skill whenever a task involves >50 lines of code changes, multi-file refactors, test-and-iterate loops, repetitive shell work, or routine ops (provider onboarding, hermes patch reconciliation, weight rebalance). Reserve your own context for high-level orchestration.
---

# Pi-Subagent — Delegation Pattern

The operator agent `pi` runs on this host. It is built on `@earendil-works/pi-coding-agent`, configured at `~/.pi/agent/`, and uses our own bifrost pools for inference (`hermes-bifrost` provider, defaults to `code` pool which routes deepseek-v4-pro / kimi-k2.6 / gpt-4.1).

Pi handles:
- Self-maintenance of this stack (CB health, patch reconcile, key onboarding)
- Long-running coding tasks you'd rather not occupy your context with
- Anything that benefits from its own session/branching/compaction

## When to delegate

| Task type | Delegate to pi? |
|---|---|
| Single-file edit < 50 LoC | No — do it inline |
| Multi-file refactor | **Yes** |
| Test-and-iterate cycles (build, run, fix, repeat) | **Yes** |
| New provider onboarding | **Yes** — drop key in `~/.hermes/operator/incoming_keys/` |
| Hermes patch after upstream pull | **Yes** — auto-runs daily 02:00 |
| Routine bifrost health check | **Yes** — auto-runs hourly |
| Quick "what's the current state of X" | No — read state file directly |
| Generating a status report from logs | Either — your call |

## How to dispatch

### One-shot ad-hoc (most common)

For a single coding task — pi runs in `-p` (print, non-interactive) mode and exits:

```bash
pi -p \
  --provider hermes-bifrost \
  --model code \
  --append-system-prompt "<inline-system-instructions-or-path-to-md>" \
  --thinking high \
  --no-session \
  "<the actual task prompt>"
```

Output is plain text. Pi handles tool calls (file edit, bash, git) inside its own loop.

### Via ops template + bifrost-ops skill

For standard ops tasks (already documented), use the wrapper:

```bash
~/hermes-free-cloud/operator/op-run.sh <template-name> [extra args]
```

Templates available:
- `op-health` — read-only stack health report
- `op-react` — fix stuck CB demotes
- `op-integrate <key-file>` — onboard a new provider
- `op-patch-hermes` — reconcile hermes patches after upstream pull

### Via incoming queue (recommended for hermes → pi)

For provider onboarding, write a key file:

```bash
cat > ~/.hermes/operator/incoming_keys/<vendor>.txt <<EOF
KEY=<api-key>
BASE_URL=<optional>
MODELS=<optional>
NOTES=<context for pi>
EOF
```

The `pi-op-queue.timer` polls this dir every 5 min, picks up new files, dispatches `op-integrate` against each, moves to `done/` on completion. Fully async. You don't need to wait.

### Background long-running task

For multi-hour coding work — pi has interactive sessions w/ branching:

```bash
SESSION_ID=$(pi --session-dir /tmp/pi-sessions -p \
  "your-multi-step-task" --thinking high 2>&1 | tail -1)
```

Then check periodically or use `pi --resume`.

## What pi can/can't touch

Allowed:
- Anything in `~/hermes-free-cloud/` (repo)
- `~/.hermes/operator/` (its queue/logs/done)
- `~/.hermes/curator-pool/` (CB state — daemon must be stopped before writes)
- `~/.hermes/config.yaml` (extract_backend reconcile only)
- Restart docker services + systemd user units (bifrost, dashboard, hermes-gateway/dashboard, circuit-breaker)
- `~/hermes-agent/` git operations (stash/pull) + patch script execution

Forbidden (documented in `~/.pi/agent/skills/bifrost-ops/SKILL.md`):
- `~/.hermes/sessions/` (your sessions)
- `~/.hermes/kanban.db` (your tasks)
- `~/.hermes/skills/` (your skill registry — not pi's at `~/.pi/agent/skills/`)
- Removing providers from bifrost (only disabling)

## Log inspection

Pi writes to `~/.hermes/operator/logs/YYYY-MM-DD-<template>.log` (per run). Daily JSONL summary at `~/.hermes/operator/logs/YYYY-MM-DD.jsonl`.

When investigating an autonomous action:
```bash
tail -50 ~/.hermes/operator/logs/$(date +%Y-%m-%d)*.log
jq -c '.' ~/.hermes/operator/logs/$(date +%Y-%m-%d).jsonl | tail -10
```

## Mutual maintenance contract

You maintain pi:
- `npm install -g @earendil-works/pi-coding-agent` if pi is broken
- Edit `~/.pi/agent/settings.json` / `models.json` if config drifts
- Add new prompt templates to `~/hermes-free-cloud/operator/prompt-templates/` (committed)
- Update `bifrost-ops` skill at `~/hermes-free-cloud/operator/skills/bifrost-ops/SKILL.md`

Pi maintains you:
- Daily hermes patch reconcile (02:00 Lisbon)
- Hourly health monitor + alerts if stack red
- Reactive CB cleanup (every 15 min, conservative — never prunes)
- On-demand provider onboarding via queue

If pi makes a wrong call, the `op-react` template enforces:
- Max 3 actions per run
- Always stops CB daemon before writing state.json
- Never overrides existing keys without `OVERRIDE=true` in incoming file
- Logs everything to JSONL for audit

## Failure modes

| Failure | Recovery |
|---|---|
| pi binary missing or stale | `npm install -g @earendil-works/pi-coding-agent --force` |
| pi can't reach bifrost | Check `curl http://localhost:4002/v1/models` — if down, restart strip-shim |
| Timer running but log silent | Check `journalctl --user -u pi-op-<name> --no-pager -n 30` |
| Op took a wrong action | All actions are logged; revert via state.json edit or `circuit_breaker.py --restore-all` |
| Pi consumed too much budget | Check `dashboard /api/usage_estimates`; ops use `code` pool which routes 90% nvidia-nim (unlimited free) |
