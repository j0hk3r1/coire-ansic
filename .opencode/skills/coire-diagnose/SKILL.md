---
name: coire-diagnose
description: Deep stuck-session diagnosis — analyzes opencode log for stream-start-without-completion patterns, cross-references with live bifrost activity, gives actionable recommendation. Use when user thinks a session is hung, when activity dropped unexpectedly, when investigating why a test isn't progressing. Trigger on phrases like "stuck", "hung", "frozen", "is it dead", "session not progressing", "/coire-diagnose", "why is it stuck", "nothing happening". Use even if user is unsure — this skill differentiates "between turns" from "actually hung".
---

# coire-diagnose

Smarter than `coire-health` for stuck-detection. Reads opencode log structure to detect **orphan streams** (LLM call started, never finished), correlates with bifrost activity, recommends action.

## When to use

- "session looks stuck" / "nothing happening" / "is it frozen"
- After unexpected long silence during a test
- Before invoking `coire-kill-opencode` (confirm it's actually stuck first)

## How to run

Runs ON `.93` (where opencode log lives). No args.

```bash
~/coire-tools/coire-diagnose
```

If running from claude-code on remote host: `ssh jkr@192.168.1.93 '~/coire-tools/coire-diagnose'`.

## Output anatomy

```
## coire-diagnose v0.1 — HH:MM:SSZ

### Bifrost activity (last 2 min): N cascade attempts

### opencode log: `2026-MM-DDTHHMMSS.log`
  mtime: X.X min ago
  ⚠ N stream(s) started without completion:
    session=ses_... open for Ns
  ⚠ N recent ERROR event(s)

### Verdict
  🔴 HUNG SESSION         → kill TUI
  🟡 PARTIAL HANG         → wait, recheck
  🟡 ERROR LOOP           → kill TUI
  🟢 ACTIVELY WORKING     → no action
  🟢 IDLE between turns   → no action
  🟢 SESSION LIKELY ENDED → no action
```

Plus a recommended-command line.

## Decision rules embedded

| state | bifrost recent | orphan streams | errors | verdict |
|---|---|---|---|---|
| HUNG | 0 | >0 | any | 🔴 kill TUI |
| PARTIAL | >0 | >0 | any | 🟡 wait |
| ERROR LOOP | 0 | 0 | >0 | 🟡 kill TUI |
| ACTIVE | >0 | 0 | 0 | 🟢 working |
| IDLE | 0 | 0 | 0 (log <5min) | 🟢 between turns |
| ENDED | 0 | 0 | 0 (log >5min) | 🟢 over |

## What this catches that coire-health doesn't

- **Stream-start-without-end**: opencode wrote `service=llm ... stream` but no matching `step-finish` for that session within window. Strong hang signal.
- **AI_APICallError pattern**: when shim/cascade returns error to opencode, opencode retries with backoff. Multiple recent errors + 0 cascade activity = stuck in retry loop.

## Limitations

- Reads last 5000 log lines only — very long sessions may miss earlier context.
- Doesn't auto-fix — just diagnoses + recommends. Confirmation on destructive ops always required.
- If multiple opencode TUIs are running, all share the same log file — analysis treats them collectively.
