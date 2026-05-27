---
name: coire-snapshot-sync
description: Capture live bifrost config (providers + routing rules) to repo snapshot files + optionally commit. Use after material changes to bifrost — provider added, pool edited, rules reordered, etc. Trigger on phrases like "snapshot bifrost", "sync the snapshot", "commit current bifrost state", "/coire-snapshot-sync", "save current config". Use this whenever cascade was edited and you want to persist the change in git for backup + restoration.
---

# coire-snapshot-sync

Captures current bifrost state to `bifrost/snapshot/{providers.json, routing-rules.json}` so it survives container restarts + is version-controlled in git.

## When to use

- After editing pool_weights.yaml + applying it (`apply_pool_weights.py`)
- After adding a new provider to bifrost
- After tuning routing rules via the dashboard
- Periodically (e.g. weekly) to capture drift

## How to run

```bash
~/coire-tools/coire-snapshot-sync           # capture + show diff
~/coire-tools/coire-snapshot-sync --commit  # also git add + commit (local)
```

Reads `REPO_DIR` env var (defaults to `~/coire-ansic`). Runs on .93 where bifrost lives.

## Output anatomy

```
# coire-snapshot-sync v0.1 — HH:MM:SSZ

[snapshot.py output — captured X providers, Y rules]

## diff vs HEAD
[file diff stat + first 50 lines of unified diff]

## next step
[either "commit now with --commit" OR "✓ already in sync"]
```

## Safety

- Pure git-local operation, never pushes
- Read-only against bifrost (just GETs current state)
- Stops if no diff (no empty commits)
- Commit message clearly labeled `auto(snapshot)` so it's easy to filter

## Limitations

- Doesn't capture provider keys (those live in .env, not bifrost API output)
- Doesn't capture shim config (no canonical persistence yet)
- Won't run if repo dir doesn't exist — set `REPO_DIR=/path/to/repo` if non-default location
