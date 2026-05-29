#!/usr/bin/env python3
"""coire-monitor — snapshot bifrost activity + categorize errors.

Runs ON .93 (where bifrost lives). When called from elsewhere, wrap with ssh.

Output: per-fb table, per-target table, error categorization, flags.
Designed for fast human scan + LLM consumption.
"""
from __future__ import annotations
import argparse
import json
import sys
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone

BIFROST_URL = "http://localhost:4001"

ERROR_CATEGORIES = [
    # (matcher, label, severity)
    ("Rate limit", "429 RPM saturation", "expected"),
    ("rate-limited", "429 RPM saturation", "expected"),
    ("provider API error (status 429)", "429 RPM saturation", "expected"),
    ("quota", "429 quota exhausted", "expected"),
    ("Insufficient balance", "402 paid-only", "concern"),
    ("requires more credits", "402 credit-gated", "expected"),
    ("max_tokens", "402 max_tokens too high", "actionable"),
    ("context length", "400 ctx overflow", "actionable"),
    ("unhashable type", "500 NVIDIA bug", "expected-known"),
    ("Internal server error", "500 provider error", "concern"),
    ("request timed out", "504 bifrost timeout", "actionable"),
    ("timeout", "timeout", "expected"),
    ("not supported", "400 schema rejected", "actionable"),
    ("invalid_iam_token", "401 auth", "concern"),
]


def categorize_error(code, msg: str) -> tuple[str, str]:
    if not msg:
        msg = f"http {code}"
    low = msg.lower()
    for keyword, label, sev in ERROR_CATEGORIES:
        if keyword.lower() in low:
            return label, sev
    return f"http {code}", "unknown"


def fetch_logs(since_iso: str, limit: int = 500) -> list[dict]:
    url = f"{BIFROST_URL}/api/logs?limit={limit}&order=desc&sort_by=timestamp&start_time={since_iso}"
    try:
        r = urllib.request.urlopen(url, timeout=15)
        return json.loads(r.read()).get("logs", [])
    except urllib.error.URLError as e:
        sys.exit(f"FAIL: bifrost unreachable @ {BIFROST_URL} — {e}")


def fmt_table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    out = []
    out.append(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    out.append("-+-".join("-" * w for w in widths))
    for r in rows:
        out.append(" | ".join(str(c).ljust(w) for c, w in zip(r, widths)))
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="1h", help="Window: e.g. 1h, 30m, 24h (default 1h)")
    p.add_argument("--limit", type=int, default=500, help="Max log entries to scan")
    p.add_argument("--errors", action="store_true", help="Also show error sample messages")
    args = p.parse_args()

    # Parse window
    unit = args.since[-1]
    n = int(args.since[:-1])
    delta = {"h": timedelta(hours=n), "m": timedelta(minutes=n), "d": timedelta(days=n)}[unit]
    since_dt = datetime.now(timezone.utc) - delta
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    logs = fetch_logs(since_iso, args.limit)

    # Aggregate
    by_fb = defaultdict(lambda: {"n": 0, "err": 0, "sum_ms": 0})
    by_target = defaultdict(lambda: {"n": 0, "err": 0, "sum_ms": 0, "max_ms": 0, "fb": set()})
    by_rule = defaultdict(int)
    error_buckets = defaultdict(int)
    error_severity = defaultdict(set)  # label → set of severities (should be 1)
    long_calls = []  # (dur_s, status, target)

    for L in logs:
        if not L.get("routing_rule_name"):
            continue
        rr = L["routing_rule_name"]
        by_rule[rr] += 1
        fb = L.get("fallback_index", -1)
        lat = L.get("latency") or 0
        s = L.get("status")
        prov = L.get("provider", "?")
        model = L.get("model", "?")
        key = f"{prov}/{model}"

        by_fb[fb]["n"] += 1
        by_fb[fb]["sum_ms"] += lat
        by_target[key]["n"] += 1
        by_target[key]["sum_ms"] += lat
        by_target[key]["max_ms"] = max(by_target[key]["max_ms"], lat)
        by_target[key]["fb"].add(fb)

        if s == "error":
            by_fb[fb]["err"] += 1
            by_target[key]["err"] += 1
            ed = L.get("error_details") or {}
            code = ed.get("status_code")
            inner = ed.get("error") or {}
            msg = inner.get("message") if isinstance(inner, dict) else str(inner)
            label, sev = categorize_error(code, msg or "")
            error_buckets[label] += 1
            error_severity[label].add(sev)

        if lat and lat > 60000:  # >60s
            long_calls.append((lat / 1000, s, key))

    # Header
    now = datetime.now(timezone.utc)
    print(f"## coire-monitor v0.1 — window={args.since} since {since_dt.strftime('%H:%M:%S')} UTC ({now.strftime('%H:%M:%S')} now)")
    print()

    # Totals
    total = sum(b["n"] for b in by_fb.values())
    if not total:
        print("(no routing activity in window — omo TUI quiet OR bifrost dead)")
        return

    routes_str = ", ".join(f"{k}:{v}" for k, v in sorted(by_rule.items(), key=lambda x: -x[1]))
    print(f"### Activity: {total} log entries · rules: {routes_str}")
    print()

    # Per-fb
    print("### Per fallback_index")
    rows = []
    for fb in sorted(by_fb):
        v = by_fb[fb]
        avg = v["sum_ms"] / v["n"] / 1000 if v["n"] else 0
        rows.append([str(fb), str(v["n"]), str(v["err"]), f"{avg:.2f}s"])
    print(fmt_table(rows, ["fb", "n", "err", "avg"]))
    print()

    # Per-target — top by total LLM time
    print("### Per target (top 12 by total LLM time)")
    sorted_targets = sorted(by_target.items(), key=lambda x: -x[1]["sum_ms"])[:12]
    rows = []
    for key, v in sorted_targets:
        avg = v["sum_ms"] / v["n"] / 1000 if v["n"] else 0
        max_s = v["max_ms"] / 1000
        tot_min = v["sum_ms"] / 60000
        fb_str = ",".join(str(x) for x in sorted(v["fb"]))
        served = v["n"] - v["err"]
        rows.append([key[:50], str(v["n"]), str(served), f"{avg:.1f}s", f"{max_s:.0f}s", f"{tot_min:.1f}m", fb_str])
    print(fmt_table(rows, ["target", "tot", "ok", "avg", "max", "llm_min", "fb"]))
    print()

    # Errors
    if error_buckets:
        print("### Errors (by category)")
        rows = []
        for label, n in sorted(error_buckets.items(), key=lambda x: -x[1]):
            sev = " | ".join(sorted(error_severity[label]))
            rows.append([label, str(n), sev])
        print(fmt_table(rows, ["category", "n", "severity"]))
        print()

    # Flags
    flags = []
    cascade_exhausts = sum(1 for k, v in by_target.items() if v["max_ms"] > 200000 and v["err"] > 0)
    if cascade_exhausts:
        flags.append(f"⚠️ {cascade_exhausts} target(s) had >200s requests with errors (cascade hang risk)")
    dead_targets = [k for k, v in by_target.items() if v["n"] >= 5 and v["err"] == v["n"]]
    if dead_targets:
        flags.append(f"💀 fully-dead targets (100% fail, ≥5 attempts): {', '.join(dead_targets[:5])}")
    if long_calls:
        long_calls.sort(reverse=True)
        flags.append(f"🐌 {len(long_calls)} call(s) >60s — longest {long_calls[0][0]:.0f}s on {long_calls[0][2]}")
    actionable_errs = sum(n for label, n in error_buckets.items()
                          if "actionable" in error_severity[label])
    if actionable_errs:
        flags.append(f"🔧 {actionable_errs} actionable error(s) — see categories marked 'actionable'")

    print("### Flags")
    if flags:
        for f in flags:
            print(f"- {f}")
    else:
        print("- ✅ nothing notable")

if __name__ == "__main__":
    main()
