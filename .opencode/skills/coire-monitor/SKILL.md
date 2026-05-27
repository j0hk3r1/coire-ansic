---
name: coire-monitor
description: Snapshot recent coire-ansic bifrost activity + categorize errors + flag issues. Use when the user wants to check an omo run, monitor cascade health, see what models are serving, identify hangs, or diagnose drift. Trigger on phrases like "check the run", "monitor omo", "snapshot bifrost", "how's it going", "what's happening with the cascade", "/coire-monitor", "test going ok", or any request that implies inspecting live bifrost telemetry. Use even if the user doesn't explicitly say "monitor" — any check-in on the running test or stack health should fire this.
---

# coire-monitor

Quick health snapshot of the bifrost LLM gateway running on `.93`. Aggregates recent routing logs into per-fb, per-target, and per-error tables. Flags issues that need attention.

## When to use

The user wants a status read on coire-ansic:
- "check the run" / "how's it going" mid-test
- "monitor omo" / "snapshot bifrost activity"
- After making cascade changes ("did my fix work?")
- After being away for a while ("anything happening?")
- Whenever judgment about cascade health is needed

Don't use for: standalone provider probes (use `coire-probe` skill if it exists; otherwise direct curl), debugging stuck sessions (different skill), or making configuration changes.

## How to run

The helper lives at `scripts/monitor.py` next to this SKILL.md. It hits bifrost's `/api/logs` at `http://localhost:4001` directly.

Run the script **wherever bifrost is reachable on `localhost:4001`**. In this stack, bifrost runs in a Docker container on the same host as opencode (`.93`), so the script runs locally from opencode's perspective — no SSH wrapping needed.

If running this skill from a remote claude-code session (e.g., on `.68`) where bifrost is NOT on localhost, wrap with ssh: `ssh jkr@192.168.1.93 'python3 ~/.config/opencode/skills/coire-monitor/scripts/monitor.py --since 1h'`. Otherwise call directly.

### Default call

```bash
python3 ~/.config/opencode/skills/coire-monitor/scripts/monitor.py --since 1h
```

### Args

- `--since 1h|30m|24h` — window (default 1h). Unit char: `m`=minutes, `h`=hours, `d`=days.
- `--limit 500` — max log entries scanned (default 500; bump for long windows).
- `--errors` — include sample error messages (not just categories).

### Examples

| user says | command |
|---|---|
| "check the run" | `python3 ~/.config/opencode/skills/coire-monitor/scripts/monitor.py --since 1h` |
| "snapshot full test" | `... --since 24h --limit 2000` |
| "anything failing?" | `... --since 1h --errors` |
| "check just last 5 min" | `... --since 5m` |

## Output anatomy

The script emits a versioned header so the user can confirm the skill (not ad-hoc work) produced the output:

```
## coire-monitor v0.1 — window=1h since 14:30:00 UTC (15:30:00 now)
```

Then 4 sections:

1. **Activity line**: total log entries + which routing rules fired
2. **Per fb_index**: which cascade slots were hit, error rate, average latency per slot
3. **Per target (top 12)**: provider/model breakdown by total LLM time consumed
4. **Errors by category**: rate-limit / timeout / ctx-overflow / etc. with severity tags:
   - `expected` — normal cascade behavior (RPM saturation, 429s, expected 402s)
   - `expected-known` — documented provider bug (e.g., NVIDIA unhashable)
   - `actionable` — something the user can fix (max_tokens too high, ctx-overflow, schema rejection)
   - `concern` — unusual, worth investigating
   - `unknown` — uncategorized — script may need a new pattern
5. **Flags**: 1-2 line callouts for cascade hangs, dead targets, long calls, actionable errors

## Interpreting the output

The user usually wants:
- **Is anything actionable?** → look at "Flags" first. If `✅ nothing notable`, summary that.
- **Cascade healthy?** → per-fb shows where traffic served. If fb=0..3 doing most work, healthy. If fb=10+ has high `tot_min`, last-resort slow models are catching too much.
- **Which models doing real work?** → per-target sorted by `llm_min`. Top 2-3 are the workhorses.
- **Quotas burning?** → high `n` with high `err` ratio at top of per-target = RPM saturation. Usually fine if cascade walks past fast.

## Reporting back to the user

Keep responses tight. The script already emits formatted tables — don't re-format them. Either:
- Paste the script output verbatim if user wants raw data
- Summarize in 2-3 sentences if user wants gist: top workhorse, any flags, overall verdict

If `Flags` show actionable issues, propose specific fixes (e.g., "drop X from cascade", "shim cap for Y"). Don't apply fixes inside this skill — leave to user direction.

## Limitations

- Only sees the **last 500 log entries** by default. For >2h windows of heavy activity, bump `--limit`.
- Doesn't tell you about opencode-side errors (those live in `~/.local/share/opencode/log/`). For "session stuck" diagnosis, also check opencode log mtime + look for `AI_APICallError`. A future `coire-diagnose-stuck` skill could combine both.
- Doesn't probe live model health — just historical log analysis. For "is X currently alive?", use direct curl or a dedicated probe skill.

## Versioning

Bump version (`v0.1` → `v0.2`) when changing output format or adding categories. Helps users see which skill version they're invoking.
