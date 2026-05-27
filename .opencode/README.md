# coire-ansic ops layer

Skills + CLI tools for ongoing coire-ansic maintenance. Split into two layers:

## Layer 1: opencode skills + slash commands

Used WHEN opencode session is responsive. Auto-trigger via natural language or `/<name>`.

| slash | use for |
|---|---|
| `/coire-monitor [window]` | snapshot bifrost activity + categorize errors |
| `/coire-probe MODEL [--big] [--via shim\|bifrost]` | tool-call + latency + rate-limit test on a model |
| `/coire-health` | stack health (containers, APIs, recent activity, resources) |
| `/coire-diagnose` | deep stuck-session detection (orphan streams, error loops) |
| `/coire-cascade-show [--pool X]` | live cascade with arena scores + quota classes |
| `/coire-check-quotas` | per-provider live rate-limit headers (burns ~11 reqs) |

Defined in:
- `.opencode/skills/<name>/SKILL.md` (natural-language trigger + how-to)
- `.opencode/command/<name>.md` (slash-command invocation)

Deployed globally on `.93` at:
- `~/.config/opencode/skills/<name>/`
- `~/.config/opencode/command/<name>.md`

## Layer 2: CLI tools (works when opencode is dead)

Direct shell invocation on `.93` — no opencode needed. Use when LLM stack is broken AND skills can't fire.

| CLI tool | use for |
|---|---|
| `~/coire-tools/coire-health` | observability — what's up, what's down |
| `~/coire-tools/coire-diagnose` | stuck-session forensic check |
| `~/coire-tools/coire-monitor [--since 1h]` | bifrost log snapshot (CLI mirror of skill) |
| `~/coire-tools/coire-probe MODEL` | model probe (CLI mirror of skill) |
| `~/coire-tools/coire-cascade-show` | cascade dump |
| `~/coire-tools/coire-check-quotas` | quota probe |
| `~/coire-tools/coire-restart [svc]` | restart docker container (destructive) |
| `~/coire-tools/coire-kill-opencode [--tui\|--web\|--all]` | kill hung opencode (destructive, defaults to TUI only) |

Note: `coire-restart` and `coire-kill-opencode` have NO slash command. Destructive ops are CLI-only by design.

Canonical source: `coire-ansic/scripts/ops/*` in this repo. Deploy via `./scripts/ops/deploy.sh`.

## Triage flow

```
something looks off
   │
   ▼
1. /coire-health          (or ~/coire-tools/coire-health if opencode is dead)
   ├─ ✓ healthy           → 2
   ├─ ✗ bifrost           → ~/coire-tools/coire-restart bifrost
   ├─ ✗ shim              → ~/coire-tools/coire-restart strip-shim
   └─ disk/mem critical   → manual cleanup
   │
   ▼
2. /coire-diagnose        (if session feels stuck)
   ├─ 🟢 active           → no action, just wait
   ├─ 🟢 idle/ended       → no action, fire new prompt if desired
   ├─ 🟡 partial hang     → wait 1 min, re-diagnose
   └─ 🔴 hung / error loop → ~/coire-tools/coire-kill-opencode --tui
   │
   ▼
3. /coire-monitor 1h      (during/after a test, to understand cascade behavior)
4. /coire-cascade-show    (verify config after edits)
5. /coire-check-quotas    (end-of-day planning)
```

## Adding a new skill

1. Drop canonical script in `coire-ansic/scripts/ops/<name>` (executable)
2. Write `coire-ansic/.opencode/skills/<name>/SKILL.md` with frontmatter
3. Write `coire-ansic/.opencode/command/<name>.md` (slash dispatcher)
4. Add to `scripts/ops/deploy.sh` for-loop OR as symlink
5. Run `./scripts/ops/deploy.sh`
6. Reload opencode TUI (or restart web) to pick up new slash command

## Versioning

Scripts emit `## <name> vX.Y` headers. Bump when output format or behavior changes — helps you confirm which version produced output you're looking at.

## Memory of this stack

See `~/.claude/projects/-home-jkr-Repos-coire-ansic/memory/` for project memory entries (provider quirks, tool-call matrix, omo internals, etc).
