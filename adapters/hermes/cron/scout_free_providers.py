#!/usr/bin/env python3
"""Scout for new free-inference providers + new free-tier models on existing
providers. Writes ~/.hermes/curator-pool/candidate_providers.json which the
dashboard "Provider Candidates" section surfaces for user review.

Two discovery sources (cheap + offline-friendly):

  1. Artificial Analysis API — full model catalog grouped by host_provider.
     Filters out providers we already have configured in Bifrost. Whatever's
     left is a known-quality candidate with AA-verified scores.

  2. SearXNG queries — searches for community lists of free LLM APIs (reddit,
     blogs, github awesome-lists). Optional, soft-fails if SearXNG unreachable.
     Only adds providers that aren't already candidates from source #1.

Each candidate is enriched with:
  - signup_url (best-guess from AA host_provider_url or provider name lookup)
  - models[] with AA iq/code scores when known
  - free_tier hint if we recognize the provider
  - discovered_via tag

Run:
  python3 scripts/scout_free_providers.py            # scout + write
  python3 scripts/scout_free_providers.py --dry      # print, don't write

Cron: weekly Mondays 04:00 (low-traffic window). Hermes user crontab.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent.parent
ENV_FILE = ROOT / ".env"
OUT_FILE = Path.home() / ".hermes" / "curator-pool" / "candidate_providers.json"

BIFROST_URL = os.environ.get("BIFROST_URL", "http://localhost:4001")
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8891")

# Provider hints — names we recognize from the wider community. signup_url
# guesses + free-tier blurbs. Not exhaustive; scout adds anything else AA
# knows about as a generic candidate.
KNOWN_FREE_PROVIDERS = {
    "together":      ("https://www.together.ai/signup",      "$1 trial credit; some always-free models"),
    "fireworks":     ("https://fireworks.ai",                "Free tier with rate limits"),
    "deepinfra":     ("https://deepinfra.com",               "Free trial credits"),
    "perplexity":    ("https://www.perplexity.ai/settings/api","sonar-pro free tier (limited RPS)"),
    "hyperbolic":    ("https://hyperbolic.xyz",              "Free tier; needs CC"),
    "deepseek":      ("https://platform.deepseek.com",       "Free credit on signup"),
    "anthropic":     ("https://console.anthropic.com",       "Trial credit only — paid after"),
    "xai":           ("https://console.x.ai",                "$25/mo free credits if data sharing enabled"),
    "sambanova":     ("https://cloud.sambanova.ai",          "Free tier with rate limits"),
    "lambda-labs":   ("https://lambdalabs.com/inference",    "Free trial credit"),
    "novita":        ("https://novita.ai",                   "Free credit on signup"),
    "atoma":         ("https://atoma.network",               "Decentralised inference, free tier"),
    "chutes":        ("https://chutes.ai",                   "Bittensor-backed, generous free tier"),
    "huggingface":   ("https://huggingface.co/inference-endpoints","Free serverless inference, rate-limited"),
    "minimax":       ("https://www.minimaxi.com",            "Free trial credits"),
    "zhipu":         ("https://open.bigmodel.cn",            "Free tier (China-region); GLM models"),
    "alibaba":       ("https://dashscope.console.aliyun.com","Free tier; Qwen models"),
}


def load_env_var(name: str) -> str:
    v = os.environ.get(name, "")
    if v:
        return v
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def bifrost_auth() -> str:
    user = os.environ.get("BIFROST_USER", "admin")
    pw = load_env_var("BIFROST_PASS")
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def fetch_aa_models() -> list:
    key = load_env_var("AA_KEY") or load_env_var("AA_API_KEY")
    if not key:
        print("WARN: no AA key — skipping AA scout", file=sys.stderr)
        return []
    req = urllib.request.Request(
        "https://artificialanalysis.ai/api/v2/data/llms/models",
        headers={"x-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("data", [])


def fetch_configured_providers() -> set[str]:
    try:
        req = urllib.request.Request(
            f"{BIFROST_URL}/api/providers",
            headers={"Authorization": bifrost_auth()},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        return {p["name"].lower() for p in d.get("providers", [])}
    except Exception as e:
        print(f"WARN: bifrost providers fetch failed: {e}", file=sys.stderr)
        return set()


def normalize_provider_name(name: str) -> str:
    return (name or "").lower().replace("_", "-").replace(" ", "-").strip()


def candidates_from_aa(aa_models: list, configured: set[str]) -> list:
    """Group AA models by host_provider; emit candidate ONLY when the provider
    appears in KNOWN_FREE_PROVIDERS (validated free-tier hosters). Filter out
    model-creator-only entries (openai/anthropic/meta etc — they sell access,
    not free hosting) and 'trial credit only' entries (the opposite of free).
    """
    by_provider: dict[str, list] = {}
    for m in aa_models:
        host = (m.get("host_provider") or m.get("provider") or
                (m.get("model_creator", {}) or {}).get("name") or "").strip()
        if not host:
            continue
        slug = normalize_provider_name(host)
        slug = {
            "google": "gemini", "google-ai-studio": "gemini",
            "groqcloud": "groq",
            "nvidia": "nvidia-nim", "nvidia-nim": "nvidia-nim",
            "cloudflare": "cf-openai", "cloudflare-workers-ai": "cf-openai",
            "mistral-ai": "mistral",
        }.get(slug, slug)
        by_provider.setdefault(slug, []).append(m)

    candidates = []
    for slug, models in by_provider.items():
        if slug in configured:
            continue
        # GATE 1: must be in our known-free-tier table. Otherwise it's a
        # model creator (openai/anthropic/meta), an enterprise vendor, or
        # something we've never validated — skip entirely.
        if slug not in KNOWN_FREE_PROVIDERS:
            continue
        signup, free_tier = KNOWN_FREE_PROVIDERS[slug]
        # GATE 2: drop trial-credit providers (anthropic, minimax, etc).
        # Those give intro credit then go paid — NOT free inference.
        # Also drop xai-style "credit if data sharing" (technically still
        # paid-otherwise; user can investigate manually if interested).
        ft_lower = (free_tier or "").lower()
        if free_tier and any(bad in ft_lower for bad in
                             ("trial credit", "trial credits",
                              "intro credit", "paid after", "paid only",
                              "data sharing", "needs cc", "credit card required",
                              "$1 trial", "$5 trial", "starter credit")):
            continue
        scored = [m for m in models if m.get("evaluations", {}).get("artificial_analysis_intelligence_index") is not None]
        if not scored:
            continue
        scored.sort(key=lambda m: -(m.get("evaluations", {}).get("artificial_analysis_intelligence_index") or 0))
        candidates.append({
            "provider": slug,
            "signup_url": signup,
            "free_tier": free_tier,
            "models": [{
                "name": m.get("slug") or m.get("name") or "?",
                "iq": (m.get("evaluations", {}) or {}).get("artificial_analysis_intelligence_index"),
                "code": (m.get("evaluations", {}) or {}).get("artificial_analysis_coding_index"),
            } for m in scored[:6]],
            "discovered_via": "AA model catalog",
            "discovered_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "notes": f"Top intel: {scored[0].get('name')} ({(scored[0].get('evaluations') or {}).get('artificial_analysis_intelligence_index')})",
        })
    candidates.sort(key=lambda c: -(c["models"][0].get("iq") or 0) if c.get("models") else 0)
    return candidates


def searxng_search(query: str, n: int = 5) -> list[dict]:
    """Soft-search SearXNG. Returns [] if unreachable."""
    try:
        url = f"{SEARXNG_URL}/search?q={urllib.parse.quote(query)}&format=json"
        with urllib.request.urlopen(url, timeout=10) as r:
            d = json.loads(r.read())
        return (d.get("results") or [])[:n]
    except Exception as e:
        print(f"WARN: SearXNG '{query[:40]}': {e}", file=sys.stderr)
        return []


def candidates_from_search(existing_provider_slugs: set[str]) -> list:
    """Disabled — was emitting raw SearXNG titles that never got validated.
    The hermes-driven cron does the real web-discovery + free-tier
    confirmation; this static fallback was just adding noise.

    Kept as a stub so install/scout flow doesn't break.
    """
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry", action="store_true", help="print plan, don't write")
    ap.add_argument("--no-search", action="store_true", help="skip SearXNG fallback")
    args = ap.parse_args()

    print(f"# scout_free_providers — {dt.datetime.now(dt.timezone.utc).isoformat()}")
    configured = fetch_configured_providers()
    print(f"configured providers: {sorted(configured)}")

    aa = fetch_aa_models()
    print(f"AA models fetched: {len(aa)}")

    candidates = candidates_from_aa(aa, configured)
    print(f"AA candidates (providers not yet configured): {len(candidates)}")

    if not args.no_search:
        existing = {c["provider"] for c in candidates} | configured
        sx = candidates_from_search(existing)
        print(f"search candidates: {len(sx)}")
        candidates += sx

    payload = {
        "candidates": candidates,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if args.dry:
        print(json.dumps(payload, indent=2)[:4000])
        print(f"\n(dry-run; would write {OUT_FILE})")
        return

    # Merge with existing file: preserve any hermes-scout entries (those came
    # from the LLM-driven validation cron and should never be wiped by a
    # static AA refresh).
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text()).get("candidates", [])
        except Exception:
            existing = []
    preserved = [c for c in existing
                 if str(c.get("discovered_via", "")).startswith("hermes")]
    new_slugs = {c["provider"] for c in candidates}
    # Keep preserved entries unless static scout also has them (static wins
    # on freshness for those — usually has up-to-date model list).
    preserved = [c for c in preserved if c.get("provider") not in new_slugs]
    payload["candidates"] = candidates + preserved
    OUT_FILE.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {len(candidates)} static + {len(preserved)} preserved hermes entries → {OUT_FILE}")


if __name__ == "__main__":
    main()
