---
name: coire-health
description: Quick stack health check — verify bifrost + shim + containers + opencode + recent activity + disk/memory. Use when user wants a baseline read on coire-ansic stack health, before starting a new test, after restart, after long absence, when something feels off. Trigger on phrases like "is it up", "health check", "stack ok", "everything running", "/coire-health", "verify stack", "system status". Always use this before complex diagnostics — it's the cheapest first check.
---

# coire-health

Pure observation, no mutations. Bypasses opencode/Sisyphus — runs as plain shell command directly on `.93`. Designed to work even when the LLM stack is partially or fully broken.

## When to use

- First check before running a new omo test ("is it up?")
- After server restart / docker compose changes
- When user reports anything off ("seems slow", "is something broken")
- Before invoking other coire-* skills (sanity prereq)
- Daily/weekly check-in

## How to run

Script is `~/coire-tools/coire-health` on `.93` (canonical location, deployed from `coire-ansic/scripts/ops/`).

```bash
~/coire-tools/coire-health
```

No args. Output has 7 sections + suggested-action footer.

## Output anatomy

```
# coire-health v0.1 — HH:MM:SSZ

## Containers           ← docker ps with healthy/unhealthy detection
## Bifrost API          ← /api/providers reachable + provider count + rule count
## Strip-shim           ← /health endpoint
## opencode processes   ← TUI/web PIDs running
## Recent activity      ← bifrost POSTs last 5min/1h + opencode log mtime
## Recent shim errors   ← ERROR/ReadTimeout/500 events
## Resources            ← disk % + memory MB
## Suggested next step  ← one-line action recommendation
```

## Interpreting

- All ✓ + "everything looks healthy" → safe to run new tests
- ✗ bifrost → run `~/coire-tools/coire-restart bifrost`
- ✗ shim → run `~/coire-tools/coire-restart strip-shim`
- ⚠ shim errors >5 → check `docker logs coire-strip-shim`
- Disk >90% → urgent, clean docker logs / images

## Limitations

- Doesn't diagnose hung sessions deeply — that's `coire-diagnose` (analyzes opencode log streams).
- Doesn't probe providers — that's `coire-probe`.
- Doesn't show cascade telemetry — that's `coire-monitor`.

Health is the **breadth** check. The other skills go deep.
