# Morning hand-off — 2026-05-27

Built ops layer + 6 skills + 7 CLI tools. All deployed to .93. Tested live. Ready for you to test via opencode.

## TL;DR

Open opencode (TUI or web). Type `/coire` — should autocomplete 6 slash commands:
- `/coire-monitor` — bifrost activity snapshot
- `/coire-probe MODEL` — model probe
- `/coire-health` — stack health
- `/coire-diagnose` — stuck-session forensic
- `/coire-cascade-show` — live cascade dump
- `/coire-check-quotas` — quota probe (burns reqs)

Plus 2 CLI-only (destructive ops, no slash):
- `~/coire-tools/coire-restart [svc]`
- `~/coire-tools/coire-kill-opencode [--tui|--web|--all]`

## What was built last night

### 6 skills (in opencode + slash commands)

| skill | what | tested? |
|---|---|---|
| coire-monitor | aggregates bifrost logs, categorizes errors, flags | ✅ via slash, returned versioned output |
| coire-probe | tests tool-calling + latency + rate-limit headers | ✅ via slash + CLI |
| coire-health | stack-wide observability (no mutations) | ✅ CLI tested, found false-positive STUCK detection, fixed |
| coire-diagnose | reads opencode log for orphan streams, recommends fix | ✅ initially false-positive on slash-command session, fixed (now detects `exiting loop` + `step=N`) |
| coire-cascade-show | annotates live routing rules with arena Elo + quota class | ✅ shows omo-main + omo-utility + omo-gemini cleanly |
| coire-check-quotas | pings each provider, parses rate-limit headers | ✅ caught cerebras 4 RPM left, mistral 3 RPM, cohere 19/20 trial |

### 2 CLI-only ops tools (destructive)

| tool | what |
|---|---|
| coire-restart | `docker compose restart` for bifrost/shim/dashboard with deps |
| coire-kill-opencode | kills hung opencode TUI (defaults to TUI only — preserves web) |

### Layered design (key insight from yesterday)

**Skills break when system breaks.** Need standalone CLI fallback.

- All skills have CLI equivalents under `~/coire-tools/`
- CLI works even when opencode/Sisyphus is wedged
- If `/coire-monitor` fails because opencode is hung → ssh + `~/coire-tools/coire-monitor`

## Triage flow

```
suspect something off
  ↓
/coire-health  (or ssh + ~/coire-tools/coire-health)
  ├─ ✗ bifrost → coire-restart bifrost
  ├─ ✗ shim → coire-restart strip-shim
  └─ ✓ → /coire-diagnose
         ├─ 🟢 active/idle → no action
         ├─ 🟡 partial hang → wait 1 min
         └─ 🔴 hung → coire-kill-opencode --tui
```

## Files added

**Repo paths**:
```
coire-ansic/.opencode/
├── README.md
├── command/
│   ├── coire-monitor.md
│   ├── coire-probe.md
│   ├── coire-health.md
│   ├── coire-diagnose.md
│   ├── coire-cascade-show.md
│   └── coire-check-quotas.md
└── skills/
    ├── coire-monitor/SKILL.md + scripts/monitor.py
    ├── coire-probe/SKILL.md + scripts/probe.py
    ├── coire-health/SKILL.md
    ├── coire-diagnose/SKILL.md
    ├── coire-cascade-show/SKILL.md
    └── coire-check-quotas/SKILL.md

coire-ansic/scripts/ops/
├── coire-health
├── coire-kill-opencode
├── coire-restart
├── coire-cascade-show
├── coire-check-quotas
├── coire-diagnose
└── deploy.sh
```

**Deployed on .93**:
```
~/coire-tools/                       # CLI tools (canonical execution path)
├── coire-health
├── coire-kill-opencode
├── coire-restart
├── coire-cascade-show
├── coire-check-quotas
├── coire-diagnose
├── coire-monitor  (symlink → skill script)
└── coire-probe    (symlink → skill script)

~/.config/opencode/skills/           # opencode skill metadata
├── coire-monitor/{SKILL.md, scripts/monitor.py}
├── coire-probe/{SKILL.md, scripts/probe.py}
├── coire-health/SKILL.md
├── coire-diagnose/SKILL.md
├── coire-cascade-show/SKILL.md
└── coire-check-quotas/SKILL.md

~/.config/opencode/command/          # opencode slash commands
├── coire-monitor.md
├── coire-probe.md
├── coire-health.md
├── coire-diagnose.md
├── coire-cascade-show.md
└── coire-check-quotas.md
```

## Confirmed working

Last `coire-health` snapshot at 23:26:
```
✓ all 5 containers up
✓ bifrost API responding, 12 providers, 5 routing rules
✓ shim healthy
✓ opencode web up (PID 1112227)
✓ bifrost: 7 POSTs in last 1h
✓ disk 68% used, mem 42%
→ ✅ everything looks healthy
```

`coire-cascade-show --pool omo-main` displays 13-target cascade with MT arena scores annotated correctly (primary cerebras 1460, fb=3 gemini-3.5-flash 1487 highest, last-resort fb=12 nvidia/glm-5.1 1478).

`coire-check-quotas` shows live rate limits — cerebras has 4 RPM headroom right now, mistral-large 3 RPM, mistral-medium 49 RPM, groq 999 RPD/11961 TPM, cohere 19/20 trial left.

## What to do this morning

1. Open opencode (TUI ssh or web at `:4040`)
2. Type `/coire` — verify 6 slash commands autocomplete
3. Run each once to confirm output looks right
4. If anything weird → `~/coire-tools/coire-diagnose` and `~/coire-tools/coire-health` will tell you
5. Fire your next test prompt when ready

## Open items for future iteration

- `coire-restart` and `coire-kill-opencode` are CLI-only — consider whether they need confirmation-with-context skills (could be problematic if opencode is the thing being killed)
- `coire-check-quotas` could be extended to show per-MODEL not per-provider for Z.ai (since it has tight per-model RPM)
- arena scores in `coire-cascade-show` are a local snapshot — need periodic refresh from lmarena.ai
- `coire-monitor` doesn't yet differentiate "fb=10+ slow last-resort" served well vs poorly — could add tier annotation

Sleep tight. Everything's deployed + tested. Stack is healthy.
