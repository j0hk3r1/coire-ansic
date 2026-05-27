---
name: coire-cascade-show
description: Show current bifrost routing rules (omo-main, omo-utility, omo-gemini) with arena MT scores and provider quota classes annotated per slot. Use whenever the user wants to inspect the cascade, confirm pool config, snapshot before/after rebalancing, see which models are currently primary vs fallback, understand pool depth. Trigger on phrases like "show cascade", "current pool", "what's in omo-main", "cascade config", "/coire-cascade-show", "show the routing", "current rules". Use even if user doesn't say "cascade" explicitly — any question about pool composition fires this.
---

# coire-cascade-show

Pretty-prints live bifrost routing rules with annotations:
- Multi-turn arena Elo score (from local lookup table)
- Provider quota class (RPM / RPD / TPM constraints)

Useful for:
- Snapshotting cascade before edits
- Verifying changes took effect after `apply_pool_weights.py`
- Quick "is X model in the cascade and at what fb position"

## When to use

- User asks about cascade composition
- Before/after pool rebalance
- Debugging why X model isn't being hit
- Explaining cascade structure to user

## How to run

```bash
~/coire-tools/coire-cascade-show              # all omo-* pools
~/coire-tools/coire-cascade-show --pool omo-main    # filter
```

## Output anatomy

```
### omo-main
(1 primary + 12 fallbacks)

 fb | provider       | model                  | arena | quota class
 0* | cerebras       | zai-glm-4.7            |  1460 | 5 RPM, 2400 RPD, ...
  1 | mistral        | mistral-large-2512     |  1421 | 4 RPM (large) ...
  ...
```

`0*` = primary target (weight=1.0). Numbered = fallbacks in cascade order.

## Updating arena scores

When MT leaderboard moves materially, update the `ARENA_MT` dict in `scripts/coire-cascade-show`. Bump `v0.1` → `v0.2` when format changes.

## Limitations

- Arena scores are a local snapshot — they decay. Verify against lmarena.ai/leaderboard/text/multi-turn periodically.
- Quota classes are rough — actual limits change per provider over time.
- Doesn't show live success rate per slot (use `coire-monitor` for that).
