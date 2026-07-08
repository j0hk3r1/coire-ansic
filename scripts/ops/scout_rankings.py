# /// script
# requires-python = ">=3.10"
# dependencies = ["pandas", "pyarrow"]
# ///
"""scout_rankings — sanity-check pool cascade order against LMArena.

Pulls the latest LMArena leaderboard parquets (lmarena-ai/leaderboard-dataset
on HuggingFace — published data, no scraping) and cross-references every pool
member in bifrost/config.json:

  uv run scripts/ops/scout_rankings.py

For each pool it prints the cascade in order with each member's arena rank +
score in the most relevant category (coire-main→agent+text, coire-fast/chat→
text, coire-vision→vision), flagging members that rank far above/below their
cascade position. Ends with arena-top open-weight models that appear in NO
pool — candidates for the next re-tune (probe tool-calling before pooling!).

Ordering-only signal: arena measures preference, not tool-reliability, quota
or latency — never auto-apply, just inform the human/agent doing the re-tune.
"""
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "bifrost" / "config.json"
BASE = ("https://huggingface.co/datasets/lmarena-ai/leaderboard-dataset/"
        "resolve/main/{cat}/latest-00000-of-00001.parquet")
# (parquet category, sub-leaderboard) pairs per pool. agent uses a win-prob
# `score`; text/vision/webdev use ELO `rating` — normalized to `metric` below.
POOL_CATEGORIES = {
    "coire-main": [("agent", "overall"), ("text", "coding")],
    "coire-fast": [("text", "overall")],
    "coire-chat": [("text", "overall")],
    "coire-vision": [("vision", "overall")],
}

_df_cache: dict = {}


def fetch(cat: str, sub: str = "overall") -> pd.DataFrame:
    if cat not in _df_cache:
        with urllib.request.urlopen(BASE.format(cat=cat), timeout=60) as r:
            _df_cache[cat] = pd.read_parquet(io.BytesIO(r.read()))
    df = _df_cache[cat]
    if "category" in df.columns and (df["category"] == sub).any():
        df = df[df["category"] == sub]
    df = df.copy()
    df["metric"] = df["rating"] if "rating" in df.columns else df["score"]
    # rank is per-subcategory; recompute to be safe after filtering
    df = df.sort_values("metric", ascending=False).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df


def canon(name: str) -> str:
    """Canonicalize a model name for cross-source matching."""
    s = name.lower()
    s = s.split("/")[-1]                      # drop provider/org prefixes
    s = re.sub(r":free$", "", s)
    s = re.sub(r"\((thinking|high|xhigh|low|medium|minimal)\)", "", s)
    s = re.sub(r"(instruct|preview|latest|it)\b", "", s)
    s = re.sub(r"[^a-z0-9.]", "", s)          # keep digits+dots (versions matter)
    return s


def arena_lookup(df: pd.DataFrame):
    table = {}
    for _, row in df.iterrows():
        table.setdefault(canon(row["model_name"]), (int(row["rank"]), float(row["metric"]), row["model_name"]))
    return table


def find(table: dict, member: str):
    key = canon(member)
    if key in table:
        return table[key]
    # substring fallback, prefer the longest (most specific) arena key
    hits = [(k, v) for k, v in table.items() if k and (k in key or key in k)]
    if hits:
        return max(hits, key=lambda kv: len(kv[0]))[1]
    return None


def main():
    config = json.loads(CONFIG.read_text())
    rules = config["governance"]["routing_rules"]
    cats = sorted({c for cs in POOL_CATEGORIES.values() for c in cs})
    print("fetching LMArena categories:", ", ".join(f"{c}/{s}" for c, s in cats), file=sys.stderr)
    arena = {(c, s): arena_lookup(fetch(c, s)) for c, s in cats}

    pooled_keys = set()
    for rule in rules:
        pool = rule["name"]
        members = [f"{t['provider']}/{t['model']}" for t in rule.get("targets", [])]
        members += rule.get("fallbacks", [])
        pooled_keys.update(canon(m) for m in members)
        cats_for = POOL_CATEGORIES.get(pool, [("text", "overall")])
        print(f"\n## {pool}  (arena: {'+'.join(f'{c}/{s}' for c, s in cats_for)})")
        for pos, m in enumerate(members, 1):
            marks = []
            for c, s in cats_for:
                hit = find(arena[(c, s)], m)
                marks.append(f"{c}#{hit[0]} ({hit[1]:.0f})" if hit else f"{c}:–")
            print(f"  {pos:2d}. {m:55s} {'  '.join(marks)}")

    # arena-top open models not pooled anywhere
    print("\n## arena-top OPEN models not in any pool (candidates — probe first)")
    shown = 0
    for _, row in fetch("text").sort_values("rank").iterrows():
        if str(row.get("license", "")).lower() == "proprietary":
            continue
        if canon(row["model_name"]) in pooled_keys or any(canon(row["model_name"]) in k or k in canon(row["model_name"]) for k in pooled_keys):
            continue
        print(f"  text#{int(row['rank']):3d} {row['model_name']}  [{row['organization']}]")
        shown += 1
        if shown >= 12:
            break


if __name__ == "__main__":
    main()
