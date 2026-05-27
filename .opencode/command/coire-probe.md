---
description: Probe a model/provider for tool-calling + latency + rate-limit headers. Pass provider/model as arg (e.g. /coire-probe cerebras/zai-glm-4.7). Add --big for context test.
---

Use the coire-probe skill to test a model/provider.

Args (model spec + optional flags): $ARGUMENTS

Execute this bash and show output verbatim:

```bash
python3 ~/.config/opencode/skills/coire-probe/scripts/probe.py $ARGUMENTS
```

Script outputs versioned header (`## coire-probe v0.1`), test results, rate-limit headers, and verdict. After showing output, add 1 sentence pass/fail summary.
