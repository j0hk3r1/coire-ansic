#!/usr/bin/env python3
"""Assemble pool_weights.yaml from model_capabilities.yaml + pool_intents.yaml.

Replaces hand-curation of pool_weights.yaml. Models tagged with capabilities
(ctx, rpm, tpm, tools, vision, reasoning, quality_score, free_tier) are
matched against per-pool intent specs (needs / exclude / prefer / max_per_provider).
For each pool the script picks min_primaries..max_primaries best-fit models,
weight-ranks by quality_score (within max_per_provider cap), normalizes to 1.0,
and emits a fallback chain from remaining matches.

Default: writes scripts/runtime/pool_weights.yaml.proposed and shows a diff
against the live yaml. Pass --apply to overwrite pool_weights.yaml (backup
to .yaml.bak). Pass --apply --no-confirm for full automation.

Idempotent: same inputs produce same output (sort keys deterministic).

Author-judgment knobs at top of file (WEIGHT_TIERS, CODE_NAME_HINTS,
COLD_START_NAMES). Edit there to retune weighting curve without
touching capabilities/intents data.
"""
from __future__ import annotations
import argparse
import sys
import re
from collections import defaultdict
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML required (pip install pyyaml)")


ROOT = Path(__file__).resolve().parents[2]
CAPS_PATH = ROOT / "scripts" / "runtime" / "model_capabilities.yaml"
INTENTS_PATH = ROOT / "scripts" / "runtime" / "pool_intents.yaml"
PLAN_PATH = ROOT / "scripts" / "runtime" / "pool_weights.yaml"

# Weight curve: rank-1 primary gets WEIGHT_TIERS[0], rank-2 gets WEIGHT_TIERS[1],
# etc. Last primaries share the tail. Tuned so a pool of ~8 primaries has a
# meaningful gradient without any single target dominating.
WEIGHT_TIERS = [0.28, 0.20, 0.15, 0.10, 0.08, 0.07, 0.06, 0.04, 0.03, 0.02]

# Heuristic: model id contains any of these → eligible bonus in 'code' pool
CODE_NAME_HINTS = ("codestral", "qwen2.5-coder", "qwen-coder", "gpt-oss",
                   "deepseek-v4-flash", "deepseek-reasoner")

# Heuristic: model id contains any of these → demoted to fallback tail
COLD_START_NAMES = ("nvidia-nim/moonshotai/kimi-k2.6",)


def load_yaml(p: Path) -> dict:
    if not p.exists():
        sys.exit(f"FAIL: missing {p}")
    return yaml.safe_load(p.read_text())


def model_matches_needs(caps: dict, needs: dict) -> bool:
    for k, want in (needs or {}).items():
        v = caps.get(k)
        if k in ("tools", "free_tier"):
            if v != want:
                return False
        elif k == "vision":
            if bool(v) is not bool(want):
                return False
        elif isinstance(want, (int, float)):
            if v is None or v < want:
                return False
        else:
            if v != want:
                return False
    return True


def model_excluded(caps: dict, exclude: dict) -> bool:
    for k, threshold in (exclude or {}).items():
        if k.endswith("_below"):
            field = k[:-len("_below")]
            v = caps.get(field)
            # `None` cap means unbounded — NOT below threshold (e.g. gemini has no TPM header)
            if v is not None and v < threshold:
                return True
        elif k.endswith("_above"):
            field = k[:-len("_above")]
            v = caps.get(field)
            if v is not None and v > threshold:
                return True
        else:
            if caps.get(k) == threshold:
                return True
    return False


def score_for_ranking(model_id: str, caps: dict, pool_name: str) -> float:
    """Composite ranking score within a pool. Higher = better primary."""
    base = float(caps.get("quality_score") or 0)
    # Code pool: name-heuristic bonus for coding-specialized models
    if pool_name == "code" and any(h in model_id for h in CODE_NAME_HINTS):
        base += 5
    # Ops pool: rpd_cap-heavy bonus (operator loops are RPD-hungry)
    if pool_name == "ops":
        rpd = caps.get("rpd_cap") or 0
        if rpd >= 10000:
            base += 8
    # Cold-start penalty (knocks nvidia-nim/kimi-k2.6 out of primaries)
    if model_id in COLD_START_NAMES:
        base -= 30
    # needs_balance penalty — only useful if user has credits
    if caps.get("free_tier") == "needs_balance":
        base -= 20
    return base


def assemble_pool(pool_name: str, pool_spec: dict, caps_db: dict) -> dict:
    needs = pool_spec.get("needs") or {}
    exclude = pool_spec.get("exclude") or {}
    max_per_provider = pool_spec.get("max_per_provider", 0.40)
    min_primaries = pool_spec.get("min_primaries", 3)
    max_primaries = pool_spec.get("max_primaries", 10)

    # Candidates pass needs + don't hit exclude
    candidates = []
    for model_id, caps in caps_db.items():
        if not model_matches_needs(caps, needs):
            continue
        if model_excluded(caps, exclude):
            continue
        candidates.append((model_id, caps))
    # Sort by ranking score desc
    candidates.sort(key=lambda mc: -score_for_ranking(mc[0], mc[1], pool_name))

    # Build primaries with per-provider cap
    provider_used: dict[str, float] = defaultdict(float)
    primaries: list[tuple[str, dict]] = []
    primaries_with_weight: list[tuple[str, dict, float]] = []
    tier_idx = 0
    leftover: list[tuple[str, dict]] = []
    for model_id, caps in candidates:
        if len(primaries) >= max_primaries:
            leftover.append((model_id, caps))
            continue
        # Cold-start models never go primary (negative score will sort them last
        # anyway; this is a belt-and-suspenders to be sure)
        if model_id in COLD_START_NAMES:
            leftover.append((model_id, caps))
            continue
        if caps.get("free_tier") == "needs_balance":
            leftover.append((model_id, caps))
            continue
        provider = model_id.split("/", 1)[0]
        weight_proposal = WEIGHT_TIERS[min(tier_idx, len(WEIGHT_TIERS) - 1)]
        if provider_used[provider] + weight_proposal > max_per_provider:
            # Provider full — skip to fallback
            leftover.append((model_id, caps))
            continue
        primaries.append((model_id, caps))
        primaries_with_weight.append((model_id, caps, weight_proposal))
        provider_used[provider] += weight_proposal
        tier_idx += 1

    if len(primaries) < min_primaries:
        return {
            "_error": f"pool '{pool_name}': only {len(primaries)} primaries match "
                      f"(need >= {min_primaries}); refusing to emit"
        }

    # Normalize weights to sum=1.0
    total_w = sum(w for _, _, w in primaries_with_weight)
    targets = []
    for model_id, _, w in primaries_with_weight:
        provider, model = model_id.split("/", 1)
        targets.append({"provider": provider, "model": model,
                        "weight": round(w / total_w, 4)})
    # Fix rounding drift on largest entry
    diff = round(1.0 - sum(t["weight"] for t in targets), 4)
    if abs(diff) > 0.0001 and targets:
        targets[0]["weight"] = round(targets[0]["weight"] + diff, 4)

    # Fallback chain — everything else that matched, in quality order
    leftover.sort(key=lambda mc: -score_for_ranking(mc[0], mc[1], pool_name))
    fallback_policy = (pool_spec.get("fallback_policy") or {})
    # Push needs_balance + cold_start to tail
    head, paid_tail, cold_tail = [], [], []
    for model_id, caps in leftover:
        if model_id in COLD_START_NAMES:
            cold_tail.append(model_id)
        elif caps.get("free_tier") == "needs_balance":
            paid_tail.append(model_id)
        else:
            head.append(model_id)
    fallbacks = head + paid_tail + cold_tail
    max_fb = fallback_policy.get("max_fallbacks_per_pool", 12)
    fallbacks = fallbacks[:max_fb]

    return {
        "description": pool_spec.get("description", ""),
        "targets": targets,
        "fallbacks": fallbacks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="overwrite pool_weights.yaml (backup to .yaml.bak)")
    ap.add_argument("--no-confirm", action="store_true",
                    help="don't show diff before --apply")
    ap.add_argument("--out", default=None,
                    help="output path (default: pool_weights.yaml.proposed)")
    args = ap.parse_args()

    caps_db = (load_yaml(CAPS_PATH) or {}).get("models") or {}
    intents = load_yaml(INTENTS_PATH) or {}
    pool_specs = intents.get("pools") or {}
    fb_policy = intents.get("fallback_policy") or {}

    if not caps_db or not pool_specs:
        sys.exit("FAIL: empty capabilities or intents data")

    # Inject fallback_policy into each pool spec so assemble_pool can read it
    for spec in pool_specs.values():
        spec.setdefault("fallback_policy", fb_policy)

    out_pools: dict[str, dict] = {}
    errors = []
    for pool_name, pool_spec in pool_specs.items():
        result = assemble_pool(pool_name, pool_spec, caps_db)
        if "_error" in result:
            errors.append(result["_error"])
            continue
        out_pools[pool_name] = result

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        # Don't fail entire run — emit what we can, escalate the missing pool(s)

    out_doc = {"pools": out_pools}
    out_yaml = yaml.safe_dump(out_doc, sort_keys=False, default_flow_style=False)

    # Summary print
    print("=== assembled pools ===")
    for pn, pdata in out_pools.items():
        s = sum(t["weight"] for t in pdata["targets"])
        print(f"  {pn:8s}  primaries={len(pdata['targets'])}  fallbacks={len(pdata['fallbacks'])}  sum={s:.4f}")
        for t in pdata["targets"]:
            print(f"    {t['weight']:.4f}  {t['provider']}/{t['model']}")

    out_path = Path(args.out) if args.out else PLAN_PATH.with_suffix(".yaml.proposed")
    out_path.write_text(out_yaml)
    print(f"\nwrote {out_path}")

    if args.apply:
        bak = PLAN_PATH.with_suffix(".yaml.bak")
        bak.write_text(PLAN_PATH.read_text())
        # Preserve header comments in the live yaml
        text = PLAN_PATH.read_text()
        head_end = text.find("\npools:")
        header = text[:head_end + 1] if head_end > 0 else ""
        PLAN_PATH.write_text((header + out_yaml) if header else out_yaml)
        print(f"  ✓ applied to {PLAN_PATH} (backup at {bak})")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
