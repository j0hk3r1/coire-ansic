#!/usr/bin/env python3
"""Probe every pool target to classify free-tier status.

For each (provider, model) in pool_weights.yaml, send three sizing probes:
  * small  (~50 token prompt)   — catches "model not available" / 404
  * medium (~4k token prompt)   — catches mid-band context caps
  * large  (~12k token prompt)  — catches free-tier 8k caps (github-models et al.)

Classify the result per probe:
  * "ok"             — 200, response generated
  * "free_zero"      — error mentions "limit: 0" (gemini-3-pro on free tier)
  * "needs_balance"  — 402 / "Insufficient Balance" / "insufficient_quota"
  * "ctx_cap_<N>"    — 413 "Max size: N tokens" or "Request too large"
  * "rate_limit"     — 429 (transient, retry — not a permanent classification)
  * "not_available"  — 404 / "model_not_found"
  * "other_<code>"   — anything else

Writes the digest to ~/.coire/curator-pool/free_tier_probe.json so:
  * CB can consult it to skip retry on guaranteed-fail targets
  * apply_pool_weights can warn when a primary is classified bad
  * op-rebalance can avoid boosting weight on dead targets

Run weekly via systemd (probe-free-tier.timer) or one-shot for ad-hoc checks.

Usage:
  python3 probe_free_tier.py            # probe every target in pool_weights
  python3 probe_free_tier.py --provider gemini  # probe only one provider
  python3 probe_free_tier.py --dry-run  # show plan, no calls
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.exit("FAIL: PyYAML required (pip install pyyaml)")


ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "scripts" / "runtime" / "pool_weights.yaml"
OUT_PATH = Path.home() / ".coire" / "curator-pool" / "free_tier_probe.json"
BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
USER = os.environ.get("BIFROST_USER", "admin")
PASS = os.environ.get("BIFROST_PASS", "")
if not PASS:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("BIFROST_PASS="):
                PASS = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not PASS:
    sys.exit("FAIL: BIFROST_PASS not set in env or .env")

AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()


SMALL_PROMPT = "reply ok"
# ~4k token medium prompt — repeat a short phrase
MEDIUM_PROMPT = ("The quick brown fox jumps over the lazy dog. " * 350)[:4000] + " — reply ok"
# ~12k token large prompt — catches free-tier 8k caps
LARGE_PROMPT = ("The quick brown fox jumps over the lazy dog. " * 1100)[:12000] + " — reply ok"


def _request_chat(provider: str, model: str, prompt: str, timeout: int = 30) -> dict[str, Any]:
    """POST to bifrost /v1/chat/completions with a concrete provider/model target.

    Returns dict with: ok (bool), status_code (int|None), message (str), latency_ms (int).
    """
    body = json.dumps({
        "model": f"{provider}/{model}",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 12,
    }).encode()
    req = urllib.request.Request(
        f"{BIFROST_URL}/v1/chat/completions",
        data=body,
        headers={"Authorization": AUTH, "Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        latency_ms = int((time.time() - t0) * 1000)
        try:
            d = json.loads(r.read())
        except json.JSONDecodeError:
            return {"ok": False, "status_code": r.status, "message": "non-json response", "latency_ms": latency_ms}
        return {"ok": True, "status_code": 200, "message": "", "latency_ms": latency_ms,
                "model_resolved": d.get("model")}
    except urllib.error.HTTPError as e:
        latency_ms = int((time.time() - t0) * 1000)
        try:
            err_body = json.loads(e.read())
        except (json.JSONDecodeError, ValueError):
            err_body = {}
        eo = err_body.get("error") or {}
        msg = eo.get("message", "") if isinstance(eo, dict) else str(eo)
        return {"ok": False, "status_code": e.code, "message": msg or str(err_body)[:300], "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = int((time.time() - t0) * 1000)
        return {"ok": False, "status_code": None, "message": str(e)[:300], "latency_ms": latency_ms}


def classify(probe_result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map probe result → (classification, extra-info dict).

    Classifications:
      ok, free_zero, needs_balance, ctx_cap_<N>, rate_limit, not_available, other_<code>
    """
    if probe_result["ok"]:
        return "ok", {"resolved": probe_result.get("model_resolved")}
    sc = probe_result["status_code"]
    msg = (probe_result["message"] or "").lower()

    if "limit: 0" in msg or "limit:0" in msg:
        return "free_zero", {"msg": probe_result["message"][:200]}
    if sc == 402 or "insufficient balance" in msg or "insufficient_quota" in msg:
        return "needs_balance", {"msg": probe_result["message"][:200]}
    if sc == 413 or "request too large" in msg or "request body too large" in msg or "max size" in msg:
        # Extract the cap if mentioned
        m = re.search(r"max size:?\s*(\d+)\s*tokens?", msg, re.IGNORECASE)
        cap = int(m.group(1)) if m else 0
        if cap == 0:
            m = re.search(r"limit\s+(\d+)", msg, re.IGNORECASE)
            if m: cap = int(m.group(1))
        return f"ctx_cap_{cap or 'unknown'}", {"msg": probe_result["message"][:200], "cap_tokens": cap}
    if sc == 429:
        return "rate_limit", {"msg": probe_result["message"][:200]}
    if sc == 404 or "model_not_found" in msg or "no keys found that support model" in msg:
        return "not_available", {"msg": probe_result["message"][:200]}
    return f"other_{sc}", {"msg": probe_result["message"][:200]}


def collect_targets(plan: dict, filter_provider: str | None = None) -> set[tuple[str, str]]:
    """Walk pool_weights, return unique (provider, model) pairs from primaries + fallbacks."""
    out: set[tuple[str, str]] = set()
    for pool in (plan.get("pools") or {}).values():
        for t in pool.get("targets") or []:
            p, m = t.get("provider"), t.get("model")
            if p and m and (not filter_provider or p == filter_provider):
                out.add((p, m))
        for fb in pool.get("fallbacks") or []:
            if isinstance(fb, str) and "/" in fb:
                p, _, m = fb.partition("/")
                if not filter_provider or p == filter_provider:
                    out.add((p, m))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--provider", help="probe only this provider")
    ap.add_argument("--dry-run", action="store_true", help="show targets, don't probe")
    ap.add_argument("--small-only", action="store_true",
                    help="skip medium + large probes (faster, catches availability only)")
    args = ap.parse_args()

    plan = yaml.safe_load(PLAN_PATH.read_text())
    targets = sorted(collect_targets(plan, args.provider))
    print(f"probing {len(targets)} target(s){' (filtered by '+args.provider+')' if args.provider else ''}")
    if args.dry_run:
        for p, m in targets: print(f"  {p}/{m}")
        return

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    probes = [("small", SMALL_PROMPT, 30)] + (
        [] if args.small_only else [("medium", MEDIUM_PROMPT, 30), ("large", LARGE_PROMPT, 45)]
    )

    for provider, model in targets:
        key = f"{provider}/{model}"
        entry: dict[str, Any] = {"provider": provider, "model": model, "probes": {}}
        worst_class = "ok"
        for label, prompt, timeout in probes:
            res = _request_chat(provider, model, prompt, timeout=timeout)
            cls, extra = classify(res)
            entry["probes"][label] = {
                "class": cls, "status_code": res["status_code"],
                "latency_ms": res["latency_ms"], **extra,
            }
            if cls not in ("ok", "rate_limit"):
                worst_class = cls
            print(f"  {key:<60s} {label:<6s} -> {cls}")
            # Don't hammer providers
            time.sleep(0.5)
        entry["summary"] = worst_class
        results[key] = entry

    out_doc = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total_targets": len(results),
        "results": results,
        "summary_counts": {},
    }
    counts: dict[str, int] = {}
    for v in results.values():
        s = v["summary"]
        counts[s] = counts.get(s, 0) + 1
    out_doc["summary_counts"] = counts

    OUT_PATH.write_text(json.dumps(out_doc, indent=2))
    print()
    print(f"wrote {OUT_PATH} ({len(results)} targets)")
    print(f"  summary: {counts}")
    # Refresh ~/.coire/models.json so strip-shim /v1/models picks up the
    # new probe-classification tags (e.g. probe:needs_balance for deepseek).
    import subprocess
    builder = Path(__file__).resolve().parent / "build_models_list.py"
    if builder.exists():
        print("→ auto-running build_models_list")
        try:
            subprocess.run([sys.executable, str(builder)], check=False, timeout=30)
        except Exception as e:
            print(f"  build_models_list failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
