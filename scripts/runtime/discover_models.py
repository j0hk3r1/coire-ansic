#!/usr/bin/env python3
"""Discover provider models not yet in pools. Writes findings to
~/.hermes/operator/discoveries/<date>.md for human/pi review.

For each verified-working provider, fetches /v1/models and diffs against
current pool_weights.yaml memberships + fallback chains. Outputs a markdown
file listing new models — does NOT auto-add. Pi-op or human reviews + adds.

Runs as systemd timer (op-discover.timer, weekly).
"""
from __future__ import annotations
import datetime as dt, json, os, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "scripts" / "runtime" / "pool_weights.yaml"
DISCOVERIES = Path.home() / ".hermes" / "operator" / "discoveries"

# Provider → (endpoint, env_var_for_auth, header_prefix)
# Only providers with a public /v1/models endpoint we can hit directly.
PROVIDERS = {
    "groq":          ("https://api.groq.com/openai/v1/models", "GROQ_API_KEY", "Bearer"),
    "cerebras":      ("https://api.cerebras.ai/v1/models", "CEREBRAS_API_KEY", "Bearer"),
    "mistral":       ("https://api.mistral.ai/v1/models", "MISTRAL_API_KEY", "Bearer"),
    "sambanova":     ("https://api.sambanova.ai/v1/models", "SAMBANOVA_API_KEY", "Bearer"),
    "cohere":        ("https://api.cohere.com/v1/models", "COHERE_API_KEY", "Bearer"),
    "openrouter":    ("https://openrouter.ai/api/v1/models", None, None),  # public catalog
}


def fetch_models(url, key, prefix):
    req = urllib.request.Request(url)
    # Cloudflare-protected endpoints (groq, cerebras) return 403 error 1010
    # for the default Python-urllib UA — set a real-looking one.
    req.add_header("User-Agent", "hermes-free-cloud/discover (curl/8)")
    if key:
        req.add_header("Authorization", f"{prefix} {key}")
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except Exception as e:
        return [], f"fetch failed: {e}"
    items = d.get("data", []) or d.get("models", []) or []
    ids = []
    for m in items:
        mid = m.get("id") or m.get("name") or m.get("model")
        if not mid:
            continue
        # cohere returns nested model field sometimes
        if isinstance(mid, dict):
            mid = mid.get("id") or mid.get("name")
        if mid:
            ids.append(mid)
    return ids, None


def load_pool_memberships():
    """Return dict: provider → set of model strings present in any pool target+fallback."""
    try:
        import yaml
    except ImportError:
        sys.exit("FAIL: PyYAML required")
    plan = yaml.safe_load(PLAN_PATH.read_text())
    memberships = {}
    for pool in plan["pools"].values():
        for t in pool.get("targets", []):
            memberships.setdefault(t["provider"], set()).add(t["model"])
        for fb in pool.get("fallbacks", []):
            if "/" in fb:
                p, m = fb.split("/", 1)
                memberships.setdefault(p, set()).add(m)
    return memberships


def main():
    env = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")

    memberships = load_pool_memberships()
    findings = {}
    for prov, (url, env_var, prefix) in PROVIDERS.items():
        key = env.get(env_var) if env_var else None
        if env_var and not key:
            print(f"  {prov}: skip (no {env_var})")
            continue
        models, err = fetch_models(url, key, prefix)
        if err:
            print(f"  {prov}: {err}")
            continue
        in_pool = memberships.get(prov, set())
        # Chat-capable + non-deprecated filter (heuristic by name)
        candidates = [m for m in models
                      if not any(x in m.lower() for x in (
                          "embed", "whisper", "orpheus", "tts", "guard",
                          "translate", "stt", "rerank", "voxtral",
                      ))]
        new = sorted(set(candidates) - in_pool)
        if new:
            findings[prov] = new
        print(f"  {prov}: {len(new)} new (of {len(candidates)} chat-capable, {len(in_pool)} in pool)")

    DISCOVERIES.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    out_path = DISCOVERIES / f"{today}.md"
    lines = [
        f"# New model candidates — {today}",
        "",
        f"Run: `scripts/runtime/discover_models.py`",
        f"Pool plan: `scripts/runtime/pool_weights.yaml`",
        "",
        "Models below are NOT in any pool (target or fallback). Review, probe,",
        "and add to pool_weights.yaml if desired. Each provider's free-tier",
        "quota is documented in `dashboard/app.py:PROVIDER_QUOTAS`.",
        "",
    ]
    for prov, new in sorted(findings.items()):
        lines.append(f"## {prov} ({len(new)} new)")
        lines.append("")
        for m in new:
            lines.append(f"- `{prov}/{m}`")
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"\nWrote {out_path} ({sum(len(v) for v in findings.values())} candidates)")


if __name__ == "__main__":
    main()
