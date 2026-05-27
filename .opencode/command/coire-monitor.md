---
description: Snapshot bifrost activity + categorize errors + flag issues. Default 1h window. Pass window as arg (e.g. /coire-monitor 30m).
---

Run the coire-monitor skill to snapshot bifrost activity.

Window arg (optional, default 1h): $ARGUMENTS

Execute this bash command and show output verbatim:

```bash
python3 ~/.config/opencode/skills/coire-monitor/scripts/monitor.py --since ${ARGUMENTS:-1h}
```

The script outputs a versioned header (`## coire-monitor v0.1 — window=…`), 4 sections (activity / per-fb / per-target / errors), and a flags section. After showing the output, add 1-2 sentence summary of any actionable flags.
