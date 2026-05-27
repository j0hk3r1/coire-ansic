"""coire-ansic Bifrost monitoring dashboard.

Reads live data from Bifrost gateway (logs + routing rules).
Circuit breaker was removed 2026-05-27 — bifrost's built-in cascade is the
failover mechanism now. CB-related endpoints/widgets are stubbed.
"""
from __future__ import annotations
import base64
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="coire-ansic Bifrost Dashboard")
app.mount("/static", StaticFiles(directory="./static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── config ──────────────────────────────────────────────────────────────
BIFROST_URL = os.environ.get("BIFROST_URL", "http://172.17.0.1:4001")
BIFROST_USER = os.environ.get("BIFROST_USER", "admin")
BIFROST_PASS = os.environ.get("BIFROST_PASS", "")

AUTH = "Basic " + base64.b64encode(f"{BIFROST_USER}:{BIFROST_PASS}".encode()).decode()

# ── helpers ─────────────────────────────────────────────────────────────
def now_utc():
    return datetime.now(timezone.utc)

def parse_ts(s: str):
    try:
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None

def bifrost_get(path: str, **params):
    qs = urllib.parse.urlencode(params) if params else ""
    url = f"{BIFROST_URL}{path}" + (f"?{qs}" if qs else "")
    r = urllib.request.Request(url, headers={"Authorization": AUTH})
    return json.loads(urllib.request.urlopen(r, timeout=15).read())


class _LogsCache:
    """Per-request memoization of /api/logs results to dedupe loaders."""

    def __init__(self):
        self._memo: dict[tuple, dict] = {}

    def fetch(self, *, window_hours: int, limit: int = 1000):
        key = (window_hours, limit)
        if key in self._memo:
            return self._memo[key]
        cutoff = now_utc() - timedelta(hours=window_hours)
        try:
            data = bifrost_get(
                "/api/logs",
                limit=limit, order="desc", sort_by="timestamp",
                start_time=cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            )
        except Exception as e:
            data = {"_error": str(e), "logs": []}
        self._memo[key] = data
        return data

# ── data loaders ────────────────────────────────────────────────────────
def load_pool_health(window_hours: int = 24, cache: _LogsCache | None = None):
    """Per-pool stats from live Bifrost logs."""
    if cache is not None:
        data = cache.fetch(window_hours=window_hours, limit=1000)
        if "_error" in data:
            return {"error": f"bifrost log fetch failed: {data['_error']}", "total": 0, "pools": []}
        rows_raw = data.get("logs", [])
    else:
        cutoff = now_utc() - timedelta(hours=window_hours)
        try:
            data = bifrost_get("/api/logs",
                               limit=1000, order="desc", sort_by="timestamp",
                               start_time=cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            rows_raw = data.get("logs", [])
        except Exception as e:
            return {"error": f"bifrost log fetch failed: {e}", "total": 0, "pools": []}

    rows = [l for l in rows_raw if l.get("routing_rule_name")]
    if not rows:
        return {"error": f"no pool-routed events in last {window_hours}h", "total": 0, "pools": []}

    by_pool = defaultdict(list)
    for r in rows:
        by_pool[r["routing_rule_name"]].append(r)

    now = now_utc()
    hour0 = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=window_hours - 1)
    hourly = defaultdict(lambda: defaultdict(list))
    for r in rows:
        ts = parse_ts(r.get("timestamp", ""))
        if not ts:
            continue
        hi = int((ts - hour0).total_seconds() // 3600)
        if 0 <= hi < window_hours:
            hourly[r["routing_rule_name"]][hi].append(r)

    all_p50s = []
    for pool, hdict in hourly.items():
        for hi, entries in hdict.items():
            lats = sorted((e.get("latency") or 0) / 1000 for e in entries if e.get("status") == "success")
            if lats:
                all_p50s.append(lats[len(lats) // 2])
    max_p50 = max(all_p50s) if all_p50s else 1.0
    levels = "▁▂▃▄▅▆▇█"

    pools = []
    for pool, entries in sorted(by_pool.items()):
        ok = sum(1 for e in entries if e.get("status") == "success")
        total = len(entries)
        lats = sorted((e.get("latency") or 0) / 1000 for e in entries if e.get("status") == "success")
        p50 = lats[len(lats) // 2] if lats else 0
        p95 = lats[int(len(lats) * 0.95)] if lats else 0
        top_models = Counter(f"{e['provider']}/{e['model']}" for e in entries if e.get("status") == "success").most_common(1)
        top = top_models[0][0] if top_models else "—"
        fb_used = sum(1 for e in entries if e.get("status") == "success" and (e.get("fallback_index") or 0) > 0)
        spark = []
        hourly_p50 = []
        for hi in range(window_hours):
            hentries = hourly.get(pool, {}).get(hi, [])
            hlats = sorted((e.get("latency") or 0) / 1000 for e in hentries if e.get("status") == "success")
            if hlats:
                hp50 = hlats[len(hlats) // 2]
                idx = min(int((hp50 / max_p50) * 8), 7)
                spark.append(levels[idx])
                hourly_p50.append(round(hp50, 2))
            else:
                spark.append("·")
                hourly_p50.append(None)
        pools.append({
            "pool": pool,
            "ok": ok,
            "total": total,
            "rate_pct": round(100 * ok / total, 1) if total else 0,
            "p50": round(p50, 2),
            "p95": round(p95, 2),
            "sparkline_24h": "".join(spark),
            "hourly_p50": hourly_p50,
            "top_model": top,
            "fallback_rescues": fb_used,
        })

    last_ts = max((parse_ts(r["timestamp"]) for r in rows if parse_ts(r.get("timestamp", ""))), default=None)
    return {
        "pools": pools,
        "total": len(rows),
        "last_run": last_ts.strftime("%Y-%m-%d %H:%M UTC") if last_ts else "?",
        "window_hours": window_hours,
    }


def load_circuit_breaker():
    """Stub — circuit breaker removed 2026-05-27. Bifrost's built-in cascade
    handles failover natively. Returns empty so existing template still renders."""
    return {"demoted": [], "demoted_count": 0, "updated_at": "", "removed": True}


def load_pool_targets():
    """Live Bifrost routing rules."""
    try:
        d = bifrost_get("/api/governance/routing-rules")
        rules = d.get("rules") or d.get("routing_rules") or []
        return {"rules": rules}
    except Exception as e:
        return {"error": str(e), "rules": []}


def load_pool_weights_plan():
    """Read scripts/runtime/pool_weights.yaml (the declarative weight plan).
    Mounted into container at /app/scripts/runtime/pool_weights.yaml. Falls
    back to host path via bind if container lookup fails.
    """
    paths = [
        "/app/scripts/runtime/pool_weights.yaml",
        "/root/.coire/pool_weights.yaml",
    ]
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            import yaml as _yaml
            with open(p) as f:
                return {"plan": _yaml.safe_load(f), "source": p}
        except ImportError:
            return {"error": "pyyaml not installed in dashboard container", "plan": None, "source": p}
        except Exception as e:
            return {"error": str(e), "plan": None, "source": p}
    return {"plan": None, "source": None, "error": "pool_weights.yaml not found"}


def load_weight_drift():
    """Compute drift between plan (pool_weights.yaml) and live bifrost weights.
    Returns per-pool list of {provider, model, planned, live, delta, status}.
    """
    plan_data = load_pool_weights_plan()
    plan = plan_data.get("plan") or {}
    live_rules = {r["name"]: r for r in load_pool_targets().get("rules", [])}
    pools = []
    for pool_name, pool_plan in (plan.get("pools") or {}).items():
        rule = live_rules.get(pool_name)
        live = {f"{t['provider']}/{t['model']}": float(t["weight"]) for t in (rule or {}).get("targets", [])}
        planned = {f"{t['provider']}/{t['model']}": float(t["weight"]) for t in pool_plan.get("targets", [])}
        keys = sorted(set(live) | set(planned))
        rows = []
        for k in keys:
            lp = planned.get(k, 0.0)
            lv = live.get(k, 0.0)
            delta = round(lv - lp, 4)
            if k not in live:
                status = "MISSING (cb-demoted or removed)"
            elif k not in planned:
                status = "extra (not in plan)"
            elif abs(delta) < 0.005:
                status = "ok"
            elif lv < lp:
                status = "under (cb-restored at floor?)"
            else:
                status = "over"
            prov, _, mdl = k.partition("/")
            rows.append({
                "provider": prov, "model": mdl, "key": k,
                "planned": round(lp, 4), "live": round(lv, 4),
                "delta": delta, "status": status,
            })
        pools.append({"pool": pool_name, "targets": rows})
    return {"pools": pools, "source": plan_data.get("source"), "error": plan_data.get("error")}


def load_candidates():
    """Load discovered free-inference provider candidates. Written by the
    hermes adapter's scout cron (adapters/hermes/cron/scout_free_providers.py)
    when --with-hermes was installed. Runtime store at
    ~/.coire/curator-pool/candidate_providers.json wins over repo default."""
    paths = [
        "/root/.coire/curator-pool/candidate_providers.json",
        "/app/bifrost/candidate_providers.json",
    ]
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                d = json.load(f)
            cands = d.get("candidates", [])
            return {
                "candidates": cands,
                "count": len(cands),
                "source": p,
                "updated_at": d.get("updated_at") or os.path.getmtime(p),
            }
        except Exception as e:
            return {"error": str(e), "candidates": [], "count": 0, "source": p}
    return {"candidates": [], "count": 0, "source": None,
            "note": "no candidate_providers.json — install --with-hermes to enable the weekly scout cron, "
                    "or manually run adapters/hermes/cron/scout_free_providers.py"}


def load_excluded_models():
    """Load curator excluded-model list (permanent excludes — distinct from
    transient circuit-breaker cooldowns). Runtime override at
    ~/.coire/curator-pool/excluded_models.json wins; falls back to repo
    default mounted in the image."""
    paths = [
        "/root/.coire/curator-pool/excluded_models.json",
        "/app/bifrost/excluded_models.json",
    ]
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                d = json.load(f)
            return {"excluded": d.get("excluded", []), "source": p,
                    "updated_at": os.path.getmtime(p)}
        except Exception as e:
            return {"error": str(e), "excluded": [], "source": p}
    return {"excluded": [], "source": None,
            "error": "excluded_models.json not found in any known path"}


def load_curator_recommendations():
    """Load curator recommendations snapshot if present (optional).

    Recommendations file is now generated by op-discover.timer (writes a
    markdown report to ~/.coire/operator/discoveries/<date>.md, not JSON).
    This endpoint returns empty if the legacy recommendations.json is absent
    — UI handles gracefully."""
    path = "/root/.coire/curator-pool/recommendations.json"
    try:
        with open(path) as f:
            d = json.load(f)
        # Trim to top 10 per pool for dashboard
        return {
            "best": d.get("best", [])[:10],
            "code": d.get("code", [])[:10],
            "mid": d.get("mid", [])[:10],
            "fast": d.get("fast", [])[:10],
            "total_models": len(d.get("models", [])),
            "updated_at": os.path.getmtime(path) if os.path.exists(path) else None,
        }
    except FileNotFoundError:
        return {"best": [], "code": [], "mid": [], "fast": [], "note": "no recommendations.json (op-discover writes markdown to ~/.coire/operator/discoveries/ instead)"}
    except Exception as e:
        return {"error": str(e), "best": [], "code": [], "mid": [], "fast": []}


def load_curator_history(n: int = 30):
    """Read circuit_history.jsonl (CB) + rebalance_history.jsonl (op-rebalance)."""
    events = []
    paths = [
        ("/root/.coire/curator-pool/circuit_history.jsonl", "breaker"),
        ("/root/.coire/curator-pool/rebalance_history.jsonl", "rebalance"),
    ]
    extra_paths: list[str] = []
    for p, src in paths + [(p, p.split("/")[-1].split(".")[0]) for p in extra_paths]:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                for line in f.readlines()[-n*2:]:
                    try:
                        e = json.loads(line)
                        e["_source"] = src
                        events.append(e)
                    except Exception:
                        pass
        except Exception:
            pass
    # Sort by ts desc
    def _ts(e):
        return e.get("ts") or e.get("timestamp") or ""
    events.sort(key=_ts, reverse=True)
    return {"events": events[:n]}


def load_recent_errors(window_hours: int = 24, limit: int = 500, cache: _LogsCache | None = None):
    """All non-success events in window: errors + cancelled."""
    if cache is not None:
        d = cache.fetch(window_hours=window_hours, limit=limit)
        if "_error" in d:
            return {"error": d["_error"], "errors": []}
    else:
        cutoff = now_utc() - timedelta(hours=window_hours)
        try:
            d = bifrost_get("/api/logs", limit=limit, order="desc", sort_by="timestamp",
                           start_time=cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        except Exception as e:
            return {"error": str(e), "errors": []}
    try:
        out = []
        for l in d.get("logs", []):
            if l.get("status") == "success":
                continue
            ed = l.get("error_details") or {}
            sc = ed.get("status_code") if isinstance(ed, dict) else None
            err_msg = ""
            err_type = ""
            if isinstance(ed, dict):
                err_msg = (ed.get("error") or {}).get("message", "")[:160]
                err_type = ed.get("type") or (ed.get("error") or {}).get("type") or ""
            is_cancelled = err_type == "request_cancelled" or "cancelled" in err_msg.lower() or "client disconnected" in err_msg.lower()
            out.append({
                "ts": (l.get("timestamp") or "")[:19].replace("T", " "),
                "pool": l.get("routing_rule_name") or "—",
                "provider": l.get("provider", ""),
                "model": l.get("model", ""),
                "status_code": sc,
                "latency_s": round((l.get("latency") or 0) / 1000, 1),
                "fb": l.get("fallback_index") or 0,
                "err": err_msg,
                "err_type": err_type,
                "cancelled": is_cancelled,
            })
        return {"errors": out, "window_hours": window_hours}
    except Exception as e:
        return {"error": str(e), "errors": []}


def load_recent_successes(window_hours: int = 24, limit: int = 500, cache: _LogsCache | None = None):
    """All successful events in window. Excludes errors and cancelled."""
    if cache is not None:
        d = cache.fetch(window_hours=window_hours, limit=limit)
        if "_error" in d:
            return {"error": d["_error"], "successes": []}
    else:
        cutoff = now_utc() - timedelta(hours=window_hours)
        try:
            d = bifrost_get("/api/logs", limit=limit, order="desc", sort_by="timestamp",
                           start_time=cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        except Exception as e:
            return {"error": str(e), "successes": []}
    try:
        out = []
        for l in d.get("logs", []):
            if l.get("status") != "success":
                continue
            if not l.get("routing_rule_name"):
                continue  # skip non-pool calls (model_listing etc)
            out.append({
                "ts": (l.get("timestamp") or "")[:19].replace("T", " "),
                "pool": l.get("routing_rule_name") or "—",
                "provider": l.get("provider", ""),
                "model": l.get("model", ""),
                "latency_s": round((l.get("latency") or 0) / 1000, 1),
                "fb": l.get("fallback_index") or 0,
                "stream": bool(l.get("stream")),
            })
        return {"successes": out, "window_hours": window_hours}
    except Exception as e:
        return {"error": str(e), "successes": []}


def requests_per_minute_60min(cache: "_LogsCache | None" = None):
    """Last 60 minutes bucketed by minute. Returns list[int] length 60."""
    now = now_utc()
    floor = now.replace(second=0, microsecond=0) - timedelta(minutes=60)
    if cache is None:
        cache = _LogsCache()
    data = cache.fetch(window_hours=1, limit=1000)
    buckets = [0] * 60
    for l in data.get("logs", []):
        ts = parse_ts(l.get("timestamp", ""))
        if not ts:
            continue
        idx = int((ts - floor).total_seconds() // 60)
        if 0 <= idx < 60:
            buckets[idx] += 1
    return buckets


def load_activity_heatmap(days: int = 7, cache: "_LogsCache | None" = None):
    """24×days grid per pool. pools[name][day_offset][hour] = count.
    day_offset 0 = today, increasing into past."""
    if cache is None:
        cache = _LogsCache()
    data = cache.fetch(window_hours=days * 24, limit=1000)
    if "_error" in data:
        return {"error": data["_error"], "pools": {}}
    now = now_utc()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    pools: dict[str, list[list[int]]] = {}
    truncated = len(data.get("logs", [])) >= 1000
    for l in data.get("logs", []):
        pool = l.get("routing_rule_name")
        if not pool:
            continue
        ts = parse_ts(l.get("timestamp", ""))
        if not ts:
            continue
        day_offset = (today_start - ts.replace(hour=0, minute=0, second=0, microsecond=0)).days
        if not 0 <= day_offset < days:
            continue
        if pool not in pools:
            pools[pool] = [[0] * 24 for _ in range(days)]
        pools[pool][day_offset][ts.hour] += 1
    return {"pools": pools, "days": days, "truncated": truncated}


def load_provider_errors(window_hours: int = 24, cache: _LogsCache | None = None):
    """Per-provider error breakdown by HTTP status code."""
    if cache is not None:
        d = cache.fetch(window_hours=window_hours, limit=1000)
        if "_error" in d:
            return {"error": d["_error"], "providers": []}
        logs = d.get("logs", [])
    else:
        cutoff = now_utc() - timedelta(hours=window_hours)
        try:
            d = bifrost_get("/api/logs", limit=1000, order="desc", sort_by="timestamp",
                           start_time=cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            logs = d.get("logs", [])
        except Exception as e:
            return {"error": str(e), "providers": []}
    by_prov = defaultdict(lambda: {"ok": 0, "429": 0, "timeout": 0, "400": 0, "other": 0, "total": 0})
    for l in logs:
        p = l.get("provider")
        if not p:
            continue
        by_prov[p]["total"] += 1
        if l.get("status") == "success":
            by_prov[p]["ok"] += 1
        else:
            ed = l.get("error_details") or {}
            sc = ed.get("status_code", 0) if isinstance(ed, dict) else 0
            try:
                sc = int(sc)
            except (TypeError, ValueError):
                sc = 0
            msg = (ed.get("error") or {}).get("message", "").lower() if isinstance(ed, dict) else ""
            if sc == 429:
                by_prov[p]["429"] += 1
            elif sc == 504 or "timed out" in msg:
                by_prov[p]["timeout"] += 1
            elif sc == 400:
                by_prov[p]["400"] += 1
            else:
                by_prov[p]["other"] += 1
    out = []
    for p, c in sorted(by_prov.items(), key=lambda x: -x[1]["total"]):
        rate = round(100 * c["ok"] / c["total"], 1) if c["total"] else 0
        out.append({"provider": p, **c, "ok_rate": rate})
    return {"providers": out, "window_hours": window_hours}


def load_bifrost_metrics():
    """Parse Bifrost Prometheus /metrics — cumulative since process start.
    Returns aggregated cost + tokens + counts per pool/provider."""
    import re
    try:
        url = f"{BIFROST_URL}/metrics"
        r = urllib.request.Request(url, headers={"Authorization": AUTH})
        text = urllib.request.urlopen(r, timeout=10).read().decode()
    except Exception as e:
        return {"error": str(e), "by_pool": [], "by_provider": []}

    label_re = re.compile(r'(\w+)="([^"]*)"')
    by_pool = defaultdict(lambda: {"cost": 0.0, "input": 0, "output": 0, "success": 0, "errors": 0})
    by_provider = defaultdict(lambda: {"cost": 0.0, "input": 0, "output": 0, "success": 0, "errors": 0})

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-z_]+)\{([^}]*)\}\s+([\d.eE+-]+)$", line)
        if not m:
            continue
        name, labels_str, value = m.groups()
        try:
            value = float(value)
        except Exception:
            continue
        labels = dict(label_re.findall(labels_str))
        pool = labels.get("routing_rule_name") or "—"
        provider = labels.get("provider") or "—"
        if name == "bifrost_cost_total":
            by_pool[pool]["cost"] += value
            by_provider[provider]["cost"] += value
        elif name == "bifrost_input_tokens_total":
            by_pool[pool]["input"] += int(value)
            by_provider[provider]["input"] += int(value)
        elif name == "bifrost_output_tokens_total":
            by_pool[pool]["output"] += int(value)
            by_provider[provider]["output"] += int(value)
        elif name == "bifrost_success_requests_total":
            by_pool[pool]["success"] += int(value)
            by_provider[provider]["success"] += int(value)
        elif name == "bifrost_error_requests_total":
            by_pool[pool]["errors"] += int(value)
            by_provider[provider]["errors"] += int(value)

    pool_rows = sorted(
        ({"pool": p, **v} for p, v in by_pool.items() if p != "—"),
        key=lambda r: -(r["input"] + r["output"]),
    )
    prov_rows = sorted(
        ({"provider": p, **v} for p, v in by_provider.items() if p != "—"),
        key=lambda r: -(r["cost"] or 0),
    )
    return {"by_pool": pool_rows, "by_provider": prov_rows}


def load_provider_status():
    """Per-provider config snapshot for the Providers card."""
    try:
        d = bifrost_get("/api/providers")
        out = []
        for p in d.get("providers", []):
            nc = p.get("network_config", {}) or {}
            out.append({
                "name": p["name"],
                "timeout_s": nc.get("default_request_timeout_in_seconds", "?"),
                "max_retries": nc.get("max_retries", "?"),
                "keys": len(p.get("keys", [])),
                "status": p.get("provider_status", "?"),
            })
        return {"providers": sorted(out, key=lambda x: x["name"])}
    except Exception as e:
        return {"error": str(e), "providers": []}


def parse_cron_schedule(cron_string):
    parts = cron_string.split()
    if len(parts) < 5:
        return cron_string
    return cron_string


def get_cron_status():
    try:
        path = "/host_crontab"
        if not os.path.exists(path):
            return {"error": "crontab not mounted", "jobs": {}}
        jobs = {}
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # cron format: m h dom mon dow command
                parts = line.split(None, 5)
                if len(parts) < 6:
                    continue
                schedule = " ".join(parts[:5])
                cmd = parts[5][:60]
                jobs[cmd] = {"schedule": schedule, "next_run": "—"}
        return {"jobs": jobs}
    except Exception as e:
        return {"error": str(e), "jobs": {}}


def load_stream_state(window_hours: int = 24):
    """Single-call aggregate of all loader payloads. Shares /api/logs cache."""
    cache = _LogsCache()
    return {
        "pool_health": load_pool_health(window_hours, cache=cache),
        "circuit_breaker": load_circuit_breaker(),
        "pool_targets": load_pool_targets(),
        "provider_status": load_provider_status(),
        "bifrost_metrics": load_bifrost_metrics(),
        "provider_errors": load_provider_errors(window_hours, cache=cache),
        "recent_errors": load_recent_errors(window_hours, 500, cache=cache),
        "recent_successes": load_recent_successes(window_hours, 500, cache=cache),
        "curator_recommendations": load_curator_recommendations(),
        "curator_history": load_curator_history(30),
        "excluded_models": load_excluded_models(),
        "candidates": load_candidates(),
        "activity_heatmap": load_activity_heatmap(days=7, cache=_LogsCache()),
        "requests_per_minute": requests_per_minute_60min(cache=_LogsCache()),
        "cron_status": get_cron_status(),
        "now": now_utc().strftime("%Y-%m-%d %H:%M UTC"),
        "server_ts": now_utc().isoformat(),
    }


# ── routes ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, h: int = 24):
    state = load_stream_state(window_hours=h)
    ctx = {"request": request, **state}
    return templates.TemplateResponse(request, "dashboard.html", ctx)


@app.get("/api/stream_state")
async def api_stream_state(h: int = 24):
    return load_stream_state(window_hours=h)


@app.get("/api/bifrost_metrics")
async def api_bifrost_metrics():
    return load_bifrost_metrics()


@app.get("/api/recent_errors")
async def api_recent_errors(hours: int = 24, limit: int = 500):
    return load_recent_errors(hours, limit)


@app.get("/api/recent_successes")
async def api_recent_successes(hours: int = 24, limit: int = 500):
    return load_recent_successes(hours, limit)


@app.get("/api/curator_recommendations")
async def api_curator_recommendations():
    return load_curator_recommendations()


@app.get("/api/excluded_models")
async def api_excluded_models():
    return load_excluded_models()


@app.get("/api/candidates")
async def api_candidates():
    return load_candidates()


@app.get("/api/curator_history")
async def api_curator_history(n: int = 30):
    return load_curator_history(n)


@app.get("/api/provider_errors")
async def api_provider_errors(hours: int = 24):
    return load_provider_errors(hours)


@app.get("/api/circuit_breaker")
async def api_circuit_breaker():
    return load_circuit_breaker()


@app.get("/api/latency")
async def api_latency():
    """Per-target P50/P95 latency, grouped by pool membership. Pulls planned
    targets from pool_weights.yaml so the table covers every target — even
    ones that haven't received traffic in the rolling window get a row with
    '—' values, making missing data visible (e.g. recently-restored target
    that hasn't been hit yet).
    """
    cb = load_circuit_breaker()
    lat = cb.get("latency", {}) if isinstance(cb, dict) else {}
    plan = (load_pool_weights_plan().get("plan") or {}).get("pools") or {}

    pools = []
    seen = set()
    for pool_name, pool_plan in plan.items():
        rows = []
        for t in pool_plan.get("targets", []):
            key = f"{t['provider']}/{t['model']}"
            seen.add(key)
            s = lat.get(key) or {}
            rows.append({
                "target": key,
                "provider": t["provider"],
                "model": t["model"],
                "weight": float(t.get("weight", 0)),
                "samples": s.get("samples"),
                "p50_ms": s.get("p50_ms"),
                "p95_ms": s.get("p95_ms"),
                "max_ms": s.get("max_ms"),
            })
        rows.sort(key=lambda r: -(r["weight"] or 0))
        pools.append({"pool": pool_name, "targets": rows})

    # Orphan latency entries: traffic seen on targets not in any plan
    # (e.g. fallback-only models, or stragglers from prior config).
    orphans = []
    for key, s in lat.items():
        if key in seen:
            continue
        prov, _, mdl = key.partition("/")
        orphans.append({
            "target": key, "provider": prov, "model": mdl, "weight": None,
            "samples": s.get("samples"),
            "p50_ms": s.get("p50_ms"),
            "p95_ms": s.get("p95_ms"),
            "max_ms": s.get("max_ms"),
        })
    orphans.sort(key=lambda r: -(r["p95_ms"] or 0))
    if orphans:
        pools.append({"pool": "orphans (fallback-only or unplanned)", "targets": orphans})

    # Window for header display — read from any sample
    window_sec = 0
    for s in lat.values():
        window_sec = s.get("window_sec", 600)
        break

    return {
        "pools": pools,
        "updated_at": cb.get("updated_at"),
        "window_sec": window_sec,
        "total_targets": sum(len(p["targets"]) for p in pools),
        "with_data": sum(1 for p in pools for t in p["targets"] if t.get("samples")),
    }


@app.get("/api/pool_targets")
async def api_pool_targets():
    return load_pool_targets()


@app.get("/api/weight_drift")
async def api_weight_drift():
    """Plan (pool_weights.yaml) vs live bifrost weights, per pool target."""
    return load_weight_drift()


@app.get("/api/pool_health")
async def api_pool_health(hours: int = 24):
    return load_pool_health(hours)


@app.get("/api/providers")
async def api_providers():
    return load_provider_status()


# ── Action endpoints (write-side) ─────────────────────────────────────────

# Provider quotas — verified via live-probe of each provider's API on
# 2026-05-10. Numbers below are real, not estimates. Sources:
#   groq:       /openai/v1/chat/completions response headers
#   cerebras:   /v1/chat/completions response headers
#   mistral:    /v1/chat/completions response headers
#   gemini:     dev docs + per-model 429 testing
#   openrouter: /api/v1/auth/key + /api/v1/credits (account-specific)
#   nvidia-nim: /v1/chat/completions (no headers; dev preview no per-day cap)
#   cloudflare:  4006 error message on quota exhaustion
PROVIDER_QUOTAS = {
    # All limits header-verified 2026-05-12 via x-ratelimit-*/x-trial-* probes.
    # See bifrost/candidate_providers.json for raw response captures.
    "groq":       {"rpd": 14400, "rpm": None, "tpm": 6000,   "note": "x-ratelimit-limit-requests=14400/model, TPM=6000 (header-verified 2026-05-12)"},
    "cerebras":   {"rpd": 14400, "rpm": 30,   "tpm": 60000,  "note": "RPM=30 / RPH=900 / RPD=14400 / TPM=60k / TPD=1M per-model (header-verified)"},
    "mistral":    {"rpd": None,  "rpm": 50,   "tpm": 50000,  "note": "small=50RPM/50kTPM, magistral-medium=5RPM/75kTPM, large=4RPM/250kTPM (header-verified per-model)"},
    "gemini":     {"rpd": 250,   "rpm": 30,   "tpm": None,   "note": "no rate-limit headers — 429 on exhaust; flash=250 RPD, pro=25-50 RPD"},
    "openrouter": {"rpd": 50,    "rpm": None, "tpm": None,   "note": "$0-credit account = 50 RPD pooled across all :free; retry-after on burst (header-verified)"},
    "nvidia-nim": {"rpd": None,  "rpm": 40,   "tpm": None,   "note": "no rate-limit headers; 40 RPM/model documented (forum-verified), 10k credits/month dev preview; deepseek-v4-pro+flash intermittent <50% reliability per forum, gpt-oss-120b cold-start slow, kimi-k2.6 30-50s cold-start"},
    "cloudflare":  {"rpd": 10000, "rpm": None, "tpm": None,   "note": "no rate-limit headers; 10k neurons/day pooled across CF Workers AI models"},
    "sambanova":  {"rpd": 20,    "rpm": None, "tpm": None,   "note": "x-ratelimit-limit-requests-day=20 (header-verified — free tier hard floor)"},
    "github-models": {"rpd": None, "rpm": 20000, "tpm": 2000000, "note": "x-ratelimit-limit-requests=20000 per 60s, TPM=2M, per-model bucket (header-verified)"},
    "cohere":     {"rpd": None, "rpm": 20,   "tpm": None,   "note": "x-trial-endpoint-call-limit=20/min, x-endpoint-monthly-call-limit=1000 (header-verified trial tier)"},
}


@app.get("/api/usage_estimates")
async def api_usage_estimates():
    """Per-provider usage vs known caps. Computes 3 measurements:

      - RPD (last 24h request count)  vs PROVIDER_QUOTAS.rpd
      - RPM (last 60s request count)  vs PROVIDER_QUOTAS.rpm
      - TPM (last 60s token total)    vs PROVIDER_QUOTAS.tpm

    Returns percentages for each so the dashboard can render 3 bars per
    provider. Close-to-limit shows orange/red. Caps are header-verified
    (see PROVIDER_QUOTAS docstring above).
    """
    import datetime as _dt
    counts_24h: Counter = Counter()
    counts_60s: Counter = Counter()
    tokens_60s: Counter = Counter()
    try:
        cache = _LogsCache()
        succ = load_recent_successes(24, 500, cache=cache).get("successes", [])
        errs = load_recent_errors(24, 500, cache=cache).get("errors", [])
        cutoff_60s = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=60)
        for e in succ + errs:
            p = (e.get("provider") or "").lower()
            if not p:
                continue
            counts_24h[p] += 1
            ts = e.get("timestamp") or e.get("ts") or ""
            try:
                ldt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if ldt >= cutoff_60s:
                counts_60s[p] += 1
                tu = e.get("token_usage") or {}
                tot = (tu.get("prompt_tokens") or 0) + (tu.get("completion_tokens") or 0)
                tokens_60s[p] += tot
    except Exception:
        pass
    out = []
    for p, q in PROVIDER_QUOTAS.items():
        used = counts_24h.get(p, 0)
        rpm_used = counts_60s.get(p, 0)
        tpm_used = tokens_60s.get(p, 0)
        rpd = q.get("rpd")
        rpm = q.get("rpm")
        tpm = q.get("tpm")
        pct_rpd = round(100 * used / rpd, 1) if rpd else 0
        pct_rpm = round(100 * rpm_used / rpm, 1) if rpm else 0
        pct_tpm = round(100 * tpm_used / tpm, 1) if tpm else 0
        out.append({
            "provider": p,
            "used_24h": used,
            "rpm_used_60s": rpm_used,
            "tpm_used_60s": tpm_used,
            "rpd_cap": rpd,
            "rpm_cap": rpm,
            "tpm_cap": tpm,
            "pct_of_rpd": pct_rpd,
            "pct_of_rpm": pct_rpm,
            "pct_of_tpm": pct_tpm,
            "note": q.get("note", ""),
            # legacy fields
            "cap_estimated": rpd or 0,
            "pct": pct_rpd,
        })
    out.sort(key=lambda x: -max(x["pct_of_rpd"], x["pct_of_rpm"], x["pct_of_tpm"]))
    return {"estimates": out, "note": "RPD=last 24h, RPM/TPM=last 60s; caps header-verified"}


def _load_model_capabilities() -> dict:
    """Read scripts/runtime/model_capabilities.yaml. Per-model caps may
    differ from per-provider defaults (e.g. mistral-large=4 RPM vs
    mistral-medium=50 RPM, cerebras qwen vs cerebras llama)."""
    import yaml
    caps_path = Path("/app/model_capabilities.yaml")
    if not caps_path.exists():
        # local dev fallback — when running outside container
        caps_path = Path(__file__).resolve().parent.parent / "scripts" / "runtime" / "model_capabilities.yaml"
    if not caps_path.exists():
        return {}
    try:
        return (yaml.safe_load(caps_path.read_text()) or {}).get("models") or {}
    except Exception:
        return {}


@app.get("/api/usage_estimates_by_model")
async def api_usage_estimates_by_model():
    """Per-model usage vs caps. Each (provider/model) gets its own bars
    because per-model caps differ within a provider:
        cerebras: 14400 RPD/MODEL, so qwen-3-235b and llama3.1-8b each
                  have their own bucket.
        mistral:  4 RPM for large, 50 for medium, 5 for magistral.
        gemini:   250 RPD flash, 25-50 for pro variants.
    """
    import datetime as _dt
    caps_db = _load_model_capabilities()
    counts_24h: Counter = Counter()
    counts_60s: Counter = Counter()
    tokens_60s: Counter = Counter()
    try:
        cache = _LogsCache()
        succ = load_recent_successes(24, 500, cache=cache).get("successes", [])
        errs = load_recent_errors(24, 500, cache=cache).get("errors", [])
        cutoff_60s = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=60)
        for e in succ + errs:
            p = (e.get("provider") or "").lower()
            m = e.get("model") or ""
            if not (p and m):
                continue
            key = f"{p}/{m}"
            counts_24h[key] += 1
            ts = e.get("timestamp") or e.get("ts") or ""
            try:
                ldt = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if ldt >= cutoff_60s:
                counts_60s[key] += 1
                tu = e.get("token_usage") or {}
                tot = (tu.get("prompt_tokens") or 0) + (tu.get("completion_tokens") or 0)
                tokens_60s[key] += tot
    except Exception:
        pass
    # All unique seen + all known from capabilities
    all_keys = set(counts_24h) | set(caps_db.keys())
    out = []
    for key in all_keys:
        caps = caps_db.get(key, {})
        rpd = caps.get("rpd_cap")
        rpm = caps.get("rpm_cap")
        tpm = caps.get("tpm_cap")
        used = counts_24h.get(key, 0)
        rpm_used = counts_60s.get(key, 0)
        tpm_used = tokens_60s.get(key, 0)
        pct_rpd = round(100 * used / rpd, 1) if rpd else 0
        pct_rpm = round(100 * rpm_used / rpm, 1) if rpm else 0
        pct_tpm = round(100 * tpm_used / tpm, 1) if tpm else 0
        if used == 0 and rpm_used == 0 and not (rpd or rpm or tpm):
            # No data, no caps -> uninteresting; skip
            continue
        out.append({
            "key": key,
            "provider": caps.get("provider") if "provider" in caps else key.split("/",1)[0],
            "model": "/".join(key.split("/")[1:]),
            "used_24h": used,
            "rpm_used_60s": rpm_used,
            "tpm_used_60s": tpm_used,
            "rpd_cap": rpd,
            "rpm_cap": rpm,
            "tpm_cap": tpm,
            "pct_of_rpd": pct_rpd,
            "pct_of_rpm": pct_rpm,
            "pct_of_tpm": pct_tpm,
            "latency_tier": caps.get("latency_tier"),
            "quality_score": caps.get("quality_score"),
            "tools": caps.get("tools"),
            "notes": caps.get("notes", ""),
        })
    # Sort by max-pct desc; models with any traffic float to top
    out.sort(key=lambda x: -max(x["pct_of_rpd"], x["pct_of_rpm"], x["pct_of_tpm"], x["used_24h"]/100))
    return {"models": out, "note": "Per-model RPD=last 24h, RPM/TPM=last 60s; per-model caps from model_capabilities.yaml"}


def _bifrost_put_rule(rule_id: str, body: dict):
    """Helper: PUT a routing rule to bifrost via httpx-like sync call."""
    import urllib.request
    auth = "Basic " + base64.b64encode(
        f"{os.environ.get('BIFROST_USER','admin')}:{os.environ.get('BIFROST_PASS','')}".encode()
    ).decode()
    req = urllib.request.Request(
        f"http://172.17.0.1:4001/api/governance/routing-rules/{rule_id}",
        data=json.dumps(body).encode(),
        method="PUT",
        headers={"Authorization": auth, "Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


async def _parse_target_payload(request: Request) -> tuple[str | None, str | None, str | None]:
    """Parse {provider, model} OR {target: 'p/m'} from request body.

    Tolerates empty body, plain text body, and missing Content-Type
    (agents calling via plain `curl -d '...'` without -H sometimes drop
    the Content-Type). Returns (provider, model, error_message).
    """
    try:
        raw = await request.body()
    except Exception as e:
        return None, None, f"failed to read body: {e}"
    if not raw:
        return None, None, "empty body — expected JSON {provider, model} or {target: 'p/m'}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, None, f"invalid JSON: {e.msg} — expected {{provider, model}} or {{target: 'p/m'}}"
    if not isinstance(payload, dict):
        return None, None, f"expected JSON object, got {type(payload).__name__}"
    provider = payload.get("provider")
    model = payload.get("model")
    if not (provider and model) and payload.get("target"):
        t = payload["target"]
        if "/" in t:
            provider, model = t.split("/", 1)
    if not (provider and model):
        return None, None, "missing provider+model (or target='provider/model')"
    return provider, model, None


@app.post("/api/circuit_breaker/restore")
@app.post("/api/circuit_breaker/prune")
async def api_cb_removed(request: Request):
    """CB was removed 2026-05-27. Endpoint kept as 410 Gone so existing UI
    handlers degrade gracefully instead of throwing."""
    return JSONResponse({"ok": False, "error": "circuit-breaker removed; bifrost cascade is the failover mechanism"}, status_code=410)


@app.get("/api/health_status")
async def api_health_status():
    """Roll-up status: green/amber/red + reasons. Used by header banner."""
    cb = load_circuit_breaker()
    ph = load_pool_health(24)
    cron = get_cron_status()
    reasons = []
    level = "green"
    demoted_n = cb.get("demoted_count", 0)
    if demoted_n >= 5:
        level = "red"; reasons.append(f"{demoted_n} cooldowns")
    elif demoted_n > 0:
        level = "amber"; reasons.append(f"{demoted_n} cooldowns")
    err_24h = ph.get("errors", 0)
    if err_24h > 100:
        level = "red"; reasons.append(f"{err_24h} errors/24h")
    elif err_24h > 20:
        if level == "green": level = "amber"
        reasons.append(f"{err_24h} errors/24h")
    if isinstance(cron, dict) and cron.get("failed"):
        level = "red"; reasons.append("cron failed")
    if not reasons:
        reasons.append("all systems nominal")
    return {"level": level, "reasons": reasons,
            "demoted_count": demoted_n, "errors_24h": err_24h}
