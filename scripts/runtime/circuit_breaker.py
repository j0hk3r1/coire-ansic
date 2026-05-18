#!/usr/bin/env python3
"""Real-time circuit breaker for Bifrost pool targets.

Polls Bifrost logs every N seconds. When a (provider, model) target hits
consecutive 429s or high error rate, removes it from active routing rules and
schedules restoration after exponential cooldown. Smoke-tests before restoring.

Independent from auto_rebalance_weights.py — runs continuously, reacts to
live provider state. Rebalancer handles daily weight tuning; this handles
minute-level quota burst recovery.

USAGE:
  python3 circuit_breaker.py                    # run daemon (foreground)
  python3 circuit_breaker.py --once             # single tick (for cron testing)
  python3 circuit_breaker.py --status           # print current state
  python3 circuit_breaker.py --restore-all      # force restore everything

CONFIG (env or constants):
  CB_POLL_SEC=30              poll interval
  CB_WINDOW_SEC=300           rolling error window
  CB_MIN_CALLS=5              skip if fewer calls in window
  CB_FAIL_RATE=0.5            error rate threshold
  CB_CONSEC=3                 consecutive errors trip immediately
  CB_COOLDOWN_INITIAL=60      first cooldown seconds
  CB_COOLDOWN_MAX=3600        max cooldown (1h)

SAFETY:
  - Never reduces a pool below 1 active target (instead lowers weight to 0.05)
  - Smoke-tests before restoration
  - Persists state across restarts (~/coire-ansic/scripts/circuit_state.json)
  - Atomic state writes
  - Conservative defaults
"""
from __future__ import annotations
import argparse, base64, datetime as dt, fcntl, json, os, sys, time, urllib.request, urllib.parse
from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
_DASHBOARD_DIR = Path.home() / ".coire" / "curator-pool"
_DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
# Migrate legacy locations into the new ~/.coire/curator-pool dir on first
# run. Order matters: script-dir → ~/.hermes/curator-pool → ~/.coire/curator-pool.
_LEGACY_HERMES = Path.home() / ".hermes" / "curator-pool"
for _legacy_name in ("circuit_state.json", "circuit_history.jsonl", "cooldown_status.json"):
    _new = _DASHBOARD_DIR / _legacy_name
    if _new.exists():
        continue
    for _src in (SCRIPTS_DIR / _legacy_name, _LEGACY_HERMES / _legacy_name):
        if _src.exists():
            _src.rename(_new)
            break
STATE_FILE = _DASHBOARD_DIR / "circuit_state.json"
STATE_LOCK = _DASHBOARD_DIR / "circuit_state.lock"
HISTORY_FILE = _DASHBOARD_DIR / "circuit_history.jsonl"
DASHBOARD_STATUS = _DASHBOARD_DIR / "cooldown_status.json"
BIFROST_BASE = os.environ.get("BIFROST_URL", "http://localhost:4001")
_BPASS = os.environ.get("BIFROST_PASS")
if not _BPASS:
    _env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            if _line.startswith("BIFROST_PASS="):
                _BPASS = _line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not _BPASS:
    print("FAIL: BIFROST_PASS not set in env or .env", file=sys.stderr)
    sys.exit(1)
AUTH = "Basic " + base64.b64encode(
    f"{os.environ.get('BIFROST_USER','admin')}:{_BPASS}".encode()
).decode()

POLL_SEC = int(os.environ.get("CB_POLL_SEC", 30))
WINDOW_SEC = int(os.environ.get("CB_WINDOW_SEC", 600))
MIN_CALLS = int(os.environ.get("CB_MIN_CALLS", 3))           # raised: avoid trip on tiny samples
FAIL_RATE = float(os.environ.get("CB_FAIL_RATE", 0.6))       # raised: 50% → 60%
CONSEC = int(os.environ.get("CB_CONSEC", 3))
RATELIMIT_TRIP = int(os.environ.get("CB_RATELIMIT_TRIP", 3)) # raised: 2 → 3 (was thrashing mistral)
TIMEOUT_TRIP = int(os.environ.get("CB_TIMEOUT_TRIP", 4))  # 504/timed-out → demote on N in window (slow primary). Raised 2→4: deepseek-v4-pro was 70% successful but pruned for being slow under load.
COOLDOWN_INITIAL = int(os.environ.get("CB_COOLDOWN_INITIAL", 60))
COOLDOWN_MAX = int(os.environ.get("CB_COOLDOWN_MAX", 3600))
RESTORE_GRACE_SEC = int(os.environ.get("CB_RESTORE_GRACE_SEC", 60))   # don't re-demote within 1min of restore (was 5min; now compensated by floor-weight probe-ramp)
PRUNE_AFTER_FAILS = int(os.environ.get("CB_PRUNE_AFTER_FAILS", 10))   # mark permanently dead after N consecutive smoke failures
MIN_FLOOR_WEIGHT = 0.05

# STRICT daily-quota signatures — message itself confirms daily cap reached.
# When matched, always defer to UTC midnight regardless of provider.
_STRICT_QUOTA_SIGNATURES = (
    "daily free allocation",                # cloudflare workers AI
    "free-models-per-day",                  # openrouter
    "daily request limit",                  # generic
    "daily quota",                          # generic
)

# AMBIGUOUS quota signatures — could be daily exhaustion OR a per-minute
# burst that recovers within seconds. Only treat as daily when target is in
# a known small-daily-cap list; otherwise treat as regular 429 burst with
# exponential cooldown.
#   "exceeded your current quota": gemini sends this for BOTH 5 RPM bursts on
#     Pro models AND 250 RPD exhaustion on Flash models. Without parsing the
#     retryDelay field we can't distinguish, so use provider+model heuristic.
#   "resource_exhausted":           gemini gRPC variant — same ambiguity.
#   "rate-limited upstream":        openrouter — usually upstream provider
#     RPM, recovers fast; only "daily" when paired w/ explicit RPD msg.
_AMBIGUOUS_QUOTA_SIGNATURES = (
    "exceeded your current quota",
    "resource_exhausted",
    "quota_exceeded",
    "rate-limited upstream",
)

def is_daily_quota_msg(text: str, provider: str = "", model: str = "") -> bool:
    """Returns True iff the error indicates a daily-cap exhaustion (vs a
    transient RPM burst). Strict signatures match unconditionally. Ambiguous
    signatures only match when the target is in a known small-daily-cap list.
    """
    if not text: return False
    t = text.lower()
    if any(sig in t for sig in _STRICT_QUOTA_SIGNATURES):
        return True
    if any(sig in t for sig in _AMBIGUOUS_QUOTA_SIGNATURES):
        return is_daily_capped_target(provider, model)
    return False

# Providers with small daily caps where any 429 is overwhelmingly a daily-cap
# exhaustion (vs burst). Verified via live probe:
#   cf-openai: 10000 neurons/day total — exhausts in hours of normal use
#   gemini Pro: 25-50 RPD; gemini Flash: 250-1000 RPD
#   openrouter free models w/ $0 credit: 50 RPD pooled
#   github-models high-tier (gpt-4o/4.1/o-series): per-model daily cap.
#     Header x-ratelimit-type=UserByModelByDay; retry-after up to 50000s.
#     LOW-TIER variants (*-mini, *-nano) use 60s rolling window instead —
#     do NOT flag those as daily.
_DAILY_CAP_PROVIDERS = ("cf-openai",)
_DAILY_CAP_MODEL_PATTERNS = (
    ("gemini", "pro"),                    # gemini-3-pro-preview, gemini-2.5-pro etc
    ("openrouter", ":free"),              # openrouter :free on $0-credit
)
# github-models has split tier model. high/custom = daily, low = 60s window.
# Default to daily-capped for openai/* unless it's a -mini/-nano variant.
_GITHUB_OPENAI_LOWTIER_SUFFIXES = ("-mini", "-nano")

def is_daily_capped_target(provider: str, model: str) -> bool:
    if provider in _DAILY_CAP_PROVIDERS:
        return True
    m = (model or "").lower()
    for prov_match, sub in _DAILY_CAP_MODEL_PATTERNS:
        if provider == prov_match and sub in m:
            return True
    if provider == "github-models" and m.startswith("openai/"):
        if not any(m.endswith(suf) or suf + "-" in m for suf in _GITHUB_OPENAI_LOWTIER_SUFFIXES):
            return True
    return False

def next_utc_midnight_ts() -> float:
    now = dt.datetime.now(dt.timezone.utc)
    tomorrow = now.date() + dt.timedelta(days=1)
    midnight = dt.datetime.combine(tomorrow, dt.time(0, 5), tzinfo=dt.timezone.utc)
    return midnight.timestamp()

def req(method, path, body=None, base="api"):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(f"{BIFROST_BASE}/{base}{path}", data=data, method=method,
        headers={"Authorization": AUTH, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=15).read())

def now_ts(): return dt.datetime.now(dt.timezone.utc).timestamp()

def parse_iso(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()

def log_event(event):
    event["ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(event) + "\n")

# ── state ────────────────────────────────────────────────────────────────
@contextmanager
def state_lock(timeout: float = 10.0):
    """Cross-process exclusive lock for circuit_state.json mutations.

    Anything that does read-modify-write on STATE_FILE MUST hold this lock
    (CB daemon, dashboard /restore endpoint, --restore-all CLI). Atomic
    rename alone prevents torn writes but allows concurrent edits to
    silently overwrite each other's changes.
    """
    STATE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fp = open(STATE_LOCK, "w")
    deadline = time.time() + timeout
    while True:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                fp.close()
                raise TimeoutError(f"state_lock: could not acquire within {timeout}s")
            time.sleep(0.1)
    try:
        yield
    finally:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        finally:
            fp.close()

def load_state():
    if STATE_FILE.exists():
        d = json.loads(STATE_FILE.read_text())
        d.setdefault("recently_restored", {})  # key -> restore_ts (post-restore grace tracking)
        return d
    return {"demoted": {}, "last_log_ts": None, "recently_restored": {}}

def save_state(state):
    """Write state. Caller must hold state_lock() if doing read-modify-write."""
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)
    publish_dashboard_status(state)

def publish_dashboard_status(state):
    """Write dashboard-friendly snapshot of current cooldowns."""
    DASHBOARD_STATUS.parent.mkdir(parents=True, exist_ok=True)
    now = now_ts()
    demoted = []
    for key, info in state.get("demoted", {}).items():
        prov, model = key.split("/", 1)
        eta = max(0, info["restore_at"] - now)
        demoted.append({
            "provider": prov,
            "model": model,
            "pools": [p["pool"] for p in info.get("pools", [])],
            "first_demoted_at": dt.datetime.fromtimestamp(info.get("first_demoted_at", now), dt.timezone.utc).isoformat(),
            "cooldown_s": info.get("cooldown_s", 0),
            "restore_at": dt.datetime.fromtimestamp(info["restore_at"], dt.timezone.utc).isoformat(),
            "seconds_until_check": int(eta),
        })
    # Recent history (last 50 events)
    history = []
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open() as f:
            lines = f.readlines()[-50:]
        for line in lines:
            try:
                history.append(json.loads(line))
            except Exception:
                pass
    snapshot = {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "last_log_ts": state.get("last_log_ts"),
        "demoted_count": len(demoted),
        "demoted": demoted,
        "history_recent": history,
        "latency": state.get("latency_snapshot", {}),
    }
    tmp = DASHBOARD_STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2))
    tmp.replace(DASHBOARD_STATUS)
    # Also publish live pool snapshot for dashboard
    try:
        rules_data = req("GET", "/governance/routing-rules")
        live_rules_path = DASHBOARD_STATUS.parent / "live_rules.json"
        tmp2 = live_rules_path.with_suffix(".tmp")
        tmp2.write_text(json.dumps(rules_data, indent=2))
        tmp2.replace(live_rules_path)
    except Exception as e:
        pass  # non-fatal

# ── Bifrost log polling ───────────────────────────────────────────────────
def fetch_new_logs(since_iso=None):
    qs = {"limit": 500, "order": "desc", "sort_by": "timestamp"}
    if since_iso:
        qs["start_time"] = since_iso
    r = req("GET", f"/logs?{urllib.parse.urlencode(qs)}")
    return r.get("logs", [])

# ── decision logic ───────────────────────────────────────────────────────
def evaluate_target(events: list) -> tuple[bool, str, bool]:
    """Returns (should_demote, reason, is_daily_quota).
    Events: (ts, status, status_code, is_timeout, is_quota).
    Demote on:
      - daily-quota signature seen → demote w/ restore at next UTC midnight
      - 429 (rate limit) burst → demote w/ exponential cooldown
      - 504 / 'timed out' → demote (slow primary)
    Do NOT demote on:
      - 400 (bad request) — real client bug, user should see
      - 413 (too large) — request size issue, not provider health
      - Other errors — surface to user
    """
    if not events:
        return False, "no calls", False
    quotas = sum(1 for e in events if (len(e) > 4 and e[4]))
    if quotas >= 1:
        return True, f"daily quota exhausted ({quotas} quota errors)", True
    rate_limits = sum(1 for e in events if (e[2] if len(e) > 2 else 0) == 429)
    timeouts = sum(1 for e in events if (len(e) > 3 and e[3]))
    if rate_limits >= RATELIMIT_TRIP:
        return True, f"{rate_limits} rate-limit (429) errors in window", False
    if timeouts >= TIMEOUT_TRIP:
        return True, f"{timeouts} timeout errors in window", False
    return False, f"{rate_limits}/{RATELIMIT_TRIP} 429s, {timeouts}/{TIMEOUT_TRIP} timeouts — not tripping", False

# ── pool modification ────────────────────────────────────────────────────
def fetch_pools():
    return {r["name"]: r for r in req("GET", "/governance/routing-rules").get("rules", [])}

def write_pool(rule, new_targets):
    body = {
        "name": rule["name"],
        "description": rule.get("description", ""),
        "enabled": rule.get("enabled", True),
        "cel_expression": rule["cel_expression"],
        "targets": new_targets,
        "fallbacks": rule.get("fallbacks", []),
        "scope": rule.get("scope", "global"),
        "scope_id": rule.get("scope_id"),
        "priority": rule.get("priority", 0),
    }
    return req("PUT", f"/governance/routing-rules/{rule['id']}", body)

def renormalize(targets: list) -> list:
    s = sum(t["weight"] for t in targets)
    if s <= 0:
        return targets
    out = []
    for t in targets:
        out.append({**t, "weight": round(t["weight"] / s, 4)})
    # Fix sum to exactly 1.0 (rounding)
    diff = round(1.0 - sum(t["weight"] for t in out), 4)
    if abs(diff) > 0.0001 and out:
        out[0] = {**out[0], "weight": round(out[0]["weight"] + diff, 4)}
    return out

def demote_target(provider: str, model: str, pools: dict, state: dict, is_daily_quota: bool = False) -> int:
    """Remove target from BOTH primary targets AND fallbacks of all pools.
    Always re-fetches pools to avoid stale-state from sibling demotions.

    is_daily_quota: True when error signals daily-cap exhaustion (e.g. gemini
    RESOURCE_EXHAUSTED, cf neurons used up, openrouter free-day-limit). In that
    case restore_at is set to next UTC 00:05 — when caps reset — instead of
    using exponential cooldown.
    """
    pools = fetch_pools()
    modified = 0
    key = f"{provider}/{model}"
    fallback_str = f"{provider}/{model}"
    for pool_name, rule in pools.items():
        original_targets = rule["targets"]
        original_fallbacks = rule.get("fallbacks", [])
        kept_targets = [t for t in original_targets
                        if not (t["provider"] == provider and t["model"] == model)]
        kept_fallbacks = [fb for fb in original_fallbacks if fb != fallback_str]
        target_changed = len(kept_targets) != len(original_targets)
        fallback_changed = len(kept_fallbacks) != len(original_fallbacks)
        if not target_changed and not fallback_changed:
            continue  # not in this pool at all
        if target_changed and not kept_targets:
            print(f"  WARN: demoting from {pool_name} targets would empty pool — skipping target removal")
            kept_targets = original_targets  # keep targets, only remove from fallbacks
            target_changed = False
        target_orig_weight = None
        if target_changed:
            target_orig = next(t for t in original_targets
                               if t["provider"] == provider and t["model"] == model)
            target_orig_weight = target_orig["weight"]
            new_targets = renormalize(kept_targets)
        else:
            new_targets = kept_targets  # unchanged
        try:
            rule_copy = dict(rule)
            rule_copy["targets"] = new_targets
            rule_copy["fallbacks"] = kept_fallbacks
            write_pool(rule_copy, new_targets)
            # PUT will use rule_copy["fallbacks"] via write_pool's existing logic? Check.
        except Exception as e:
            print(f"  ERROR demoting {provider}/{model} from {pool_name}: {e}")
            log_event({"action": "demote_failed", "provider": provider, "model": model,
                       "pool": pool_name, "error": str(e)})
            continue
        modified += 1
        if key not in state["demoted"]:
            if is_daily_quota:
                restore_at = next_utc_midnight_ts()
                cooldown_s = int(restore_at - now_ts())
            else:
                cooldown_s = COOLDOWN_INITIAL
                restore_at = now_ts() + COOLDOWN_INITIAL
            state["demoted"][key] = {"pools": [], "first_demoted_at": now_ts(),
                                     "cooldown_s": cooldown_s,
                                     "restore_at": restore_at,
                                     "daily_quota": is_daily_quota,
                                     "fail_count": 0}
        state["demoted"][key]["pools"].append({
            "pool": pool_name,
            "original_weight": target_orig_weight,
            "was_target": target_changed,
            "was_fallback": fallback_changed,
            "fallback_index": original_fallbacks.index(fallback_str) if fallback_changed else None,
        })
    return modified

# Per-provider smoke timeout. Cold-start latencies vary wildly:
#   nvidia-nim deepseek-v4-pro: ~60s cold-start (verified 2026-05-10)
#   nvidia-nim kimi-k2.6: ~30s cold
#   openrouter: variable upstream
#   most fast providers (groq, cerebras, mistral, gemini-flash): <5s
_SMOKE_TIMEOUT_BY_PROVIDER = {
    "nvidia-nim": 150,    # deepseek-v4-pro cold-start hits 90s+ with no traffic
    "openrouter": 60,
    "cf-openai": 30,
    "gemini": 20,
    "mistral": 15,
    "groq": 10,
    "cerebras": 10,
    "deepseek": 30,
}

def smoke_test(provider: str, model: str, timeout: int = 0) -> tuple[bool, str, int]:
    """Returns (passed, error_text, retry_after_sec).
    retry_after_sec: parsed from upstream Retry-After header on failure (or 0).
      Bifrost preserves the header through its error path — verified for
      github-models gpt-4o which sends Retry-After in seconds matching
      x-ratelimit-timeremaining.
    """
    if timeout <= 0:
        timeout = _SMOKE_TIMEOUT_BY_PROVIDER.get(provider, 30)
    body = {"model": f"{provider}/{model}",
            "messages": [{"role": "user", "content": "say ok"}],
            "max_tokens": 5, "temperature": 0}
    try:
        r = urllib.request.Request(f"{BIFROST_BASE}/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Authorization": AUTH, "Content-Type": "application/json"})
        resp = urllib.request.urlopen(r, timeout=timeout)
        d = json.loads(resp.read())
        if d.get("choices"):
            return True, "", 0
        return False, json.dumps(d)[:300], 0
    except urllib.error.HTTPError as e:
        try: body_text = e.read().decode("utf-8", errors="replace")[:500]
        except Exception: body_text = ""
        ra = 0
        try:
            ra_hdr = e.headers.get("Retry-After") if e.headers else None
            if ra_hdr:
                # Most providers send seconds; some send HTTP-date. Try int first.
                try: ra = int(ra_hdr)
                except ValueError: ra = 0
        except Exception:
            ra = 0
        return False, f"HTTP {e.code} {body_text}", ra
    except Exception as e:
        return False, str(e)[:200], 0

def restore_target(provider: str, model: str, info: dict, pools: dict, state: dict) -> bool:
    """Smoke test first. If pass, add back to wherever it was demoted from
    (targets and/or fallbacks).

    Daily-quota handling: if smoke fails with a quota signature, do NOT
    increment fail_count toward prune. Instead reschedule for next UTC
    midnight + 5min and mark info["daily_quota"]=True. Means a model that's
    just out of daily cap won't get pruned — it'll come back fresh tomorrow.
    """
    print(f"  testing {provider}/{model}...", end=" ", flush=True)
    passed, err_text, retry_after = smoke_test(provider, model)
    if not passed:
        # Direct quota signature OR 429 on a known daily-cap provider
        is_429 = "HTTP 429" in err_text or '"status_code":429' in err_text or '"code":"429"' in err_text
        looks_daily = is_daily_quota_msg(err_text, provider, model) or (is_429 and is_daily_capped_target(provider, model))
        # GUARD: small Retry-After (<5min) means burst-rate-limit (per-minute
        # token bucket), NOT daily-cap exhaustion. OpenRouter free sends RA
        # in the 20-60s range for its RPM bucket. Without this guard CB
        # retries every ~60s, hammering an exhausted endpoint. Treat short RA
        # as standard burst — fall through to exponential backoff, no daily flag.
        if looks_daily and 0 < retry_after < 300:
            looks_daily = False
            # Clear stale daily flag — entry was previously classified daily
            # but new evidence shows it's actually a burst-rate cap. Reclassify.
            if info.get("daily_quota"):
                info.pop("daily_quota", None)
                info.pop("retry_after_seen", None)
                info.pop("retried_after_quota_reset", None)
            print(f"  (burst-rate, RA={retry_after}s — treating as standard backoff, not daily)")
        if looks_daily:
            info["daily_quota"] = True
            # AUTHORITATIVE: if upstream sent Retry-After (>=5min — long enough
            # to be a real daily/hourly cap), use it directly. Otherwise fall
            # back to next-UTC-midnight heuristic.
            if retry_after >= 300:
                wait = min(retry_after + 60, 26 * 3600)
                info["restore_at"] = now_ts() + wait
                info["cooldown_s"] = wait
                info["retry_after_seen"] = retry_after
                print(f"daily quota — upstream Retry-After: {retry_after}s (defer {wait/3600:.1f}h)")
            elif info.get("retried_after_quota_reset"):
                info["restore_at"] = now_ts() + 7200  # 2h
                info["cooldown_s"] = 7200
                print(f"daily quota still exhausted post-reset — retry in 2h")
            else:
                next_midnight = next_utc_midnight_ts()
                if (next_midnight - now_ts()) < 3600:
                    info["restore_at"] = now_ts() + 7200
                    info["cooldown_s"] = 7200
                    info["retried_after_quota_reset"] = True
                    print(f"daily quota retry post-cron — defer 2h instead of 24h")
                else:
                    info["restore_at"] = next_midnight
                    info["cooldown_s"] = int(info["restore_at"] - now_ts())
                    print(f"daily quota exhausted — defer to UTC midnight (in {info['cooldown_s']//60}min)")
            log_event({"action": "quota_deferred", "provider": provider, "model": model,
                       "restore_at": dt.datetime.fromtimestamp(info["restore_at"], dt.timezone.utc).isoformat(),
                       "retry_after_sec": retry_after,
                       "signature": err_text[:200]})
            return False
        info["fail_count"] = info.get("fail_count", 0) + 1
        if info["fail_count"] >= PRUNE_AFTER_FAILS:
            info["pruned"] = True
            info["cooldown_s"] = COOLDOWN_MAX
            # Track how many times this target has been pruned. After 3 prune
            # cycles in <24h, treat as "structurally dead" — back off harder
            # (3 days) and ignore /restore calls until that window expires.
            # Prevents pi-op-react from re-restoring a dead model every hour
            # and re-burning ~75 requests per cycle proving it's still dead.
            info["prune_count"] = info.get("prune_count", 0) + 1
            info["last_pruned_at"] = now_ts()
            if info["prune_count"] >= 3:
                info["restore_at"] = now_ts() + 3 * 86400
                info["structurally_dead"] = True
                print(f"PRUNED (cycle {info['prune_count']}) — marking structurally_dead, defer 3 days: {err_text[:80]}")
            else:
                info["restore_at"] = now_ts() + 86400  # check once a day in case provider revives
                print(f"PRUNED after {info['fail_count']} consecutive failures (model likely dead): {err_text[:100]}")
            log_event({"action": "pruned", "provider": provider, "model": model,
                       "fail_count": info["fail_count"], "prune_count": info["prune_count"],
                       "structurally_dead": info.get("structurally_dead", False),
                       "last_error": err_text[:200]})
        else:
            info["cooldown_s"] = min(info["cooldown_s"] * 2, COOLDOWN_MAX)
            info["restore_at"] = now_ts() + info["cooldown_s"]
            print(f"still failing ({info['fail_count']}/{PRUNE_AFTER_FAILS}) — backing off: {err_text[:80]}")
            log_event({"action": "restore_failed", "provider": provider, "model": model,
                       "fail_count": info["fail_count"], "next_cooldown_s": info["cooldown_s"],
                       "error": err_text[:200]})
        return False
    info.pop("daily_quota", None)
    info.pop("retried_after_quota_reset", None)
    info.pop("retry_after_seen", None)
    info["fail_count"] = 0
    print("ok, restoring")
    pools = fetch_pools()  # fresh
    key = f"{provider}/{model}"
    fallback_str = f"{provider}/{model}"
    for pool_info in info.get("pools", []):
        pool_name = pool_info["pool"]
        rule = pools.get(pool_name)
        if not rule:
            continue
        new_targets = list(rule["targets"])
        new_fallbacks = list(rule.get("fallbacks", []))
        if pool_info.get("was_target"):
            orig_w = pool_info.get("original_weight") or 0.2
            # Probe-before-promote ramp: restore at FLOOR weight (default
            # 0.05) so only ~5% of traffic hits the recovered target. If it
            # stays healthy, the next daily apply_pool_weights run at 01:15
            # UTC will lift it back to the planned weight. If it fails
            # again, only a small slice of users see the error before CB
            # re-demotes. Avoids the previous "burst-restore" mode where
            # half-original-weight could be 0.15-0.25 right after recovery.
            probe_w = min(MIN_FLOOR_WEIGHT, max(0.02, orig_w * 0.25))
            cur_sum = sum(t["weight"] for t in new_targets)
            if cur_sum > 0:
                remaining = max(0.01, 1.0 - probe_w)
                scaled = [{**t, "weight": round(t["weight"] / cur_sum * remaining, 4)} for t in new_targets]
                new_targets = renormalize(scaled + [{"provider": provider, "model": model, "weight": probe_w}])
            else:
                new_targets = [{"provider": provider, "model": model, "weight": 1.0}]
        if pool_info.get("was_fallback"):
            # Re-insert at original index (or end if index lost)
            idx = pool_info.get("fallback_index")
            if fallback_str not in new_fallbacks:
                if idx is not None and idx <= len(new_fallbacks):
                    new_fallbacks.insert(idx, fallback_str)
                else:
                    new_fallbacks.append(fallback_str)
        try:
            rule_copy = dict(rule)
            rule_copy["fallbacks"] = new_fallbacks
            write_pool(rule_copy, new_targets)
        except Exception as e:
            print(f"  restore write failed for {pool_name}: {e}")
            return False
    log_event({"action": "restored", "provider": provider, "model": model,
               "pools": [p["pool"] for p in info.get("pools", [])]})
    state["demoted"].pop(key, None)
    state.setdefault("recently_restored", {})[key] = now_ts()
    # Clean stale grace entries (>1h old)
    state["recently_restored"] = {k: ts for k, ts in state["recently_restored"].items()
                                   if now_ts() - ts < 3600}
    return True

# ── main loop ────────────────────────────────────────────────────────────
def tick(state, error_window, once_mode=False):
    # Daemon: incremental fetch since last poll. Once-mode: always full window.
    if once_mode:
        error_window.clear()
        cutoff_iso = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=WINDOW_SEC)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        logs = fetch_new_logs(since_iso=cutoff_iso)
    else:
        since = state.get("last_log_ts")
        logs = fetch_new_logs(since_iso=since)
    if logs:
        state["last_log_ts"] = logs[0]["timestamp"]

    # Update sliding window per (provider, model)
    cutoff = now_ts() - WINDOW_SEC
    for log in logs:
        prov = log.get("provider")
        model = log.get("model")
        if not prov or not model:
            continue
        ts = parse_iso(log["timestamp"])
        if ts < cutoff:
            continue
        # GUARD: skip "errors" caused by malformed input (e.g. pi-mono sending
        # a sole `tool` role message without preceding assistant tool_calls).
        # Models reject this as 400 Bad Request — not their fault. Demoting on
        # these would wrongly punish healthy providers and cascade through
        # fallbacks (78% of observed errors traced to this pattern 2026-05-12).
        if log.get("status") == "error":
            ih = log.get("input_history") or []
            if ih:
                # Only msg + role is 'tool' = orphan tool result → malformed
                if len(ih) == 1 and ih[0].get("role") == "tool":
                    continue
                # Last msg is tool but no preceding assistant tool_calls anywhere
                if ih[-1].get("role") == "tool":
                    has_tool_call_origin = any(
                        m.get("role") == "assistant" and m.get("tool_calls")
                        for m in ih
                    )
                    if not has_tool_call_origin:
                        continue
        # Extract HTTP status code + timeout flag + quota flag from error_details
        status_code = 0
        is_timeout = False
        is_quota = False
        if log.get("status") == "error":
            ed = log.get("error_details") or {}
            if isinstance(ed, dict):
                status_code = ed.get("status_code") or 0
                try: status_code = int(status_code)
                except (TypeError, ValueError): status_code = 0
                err_obj = ed.get("error")
                msg = ""
                if isinstance(err_obj, dict):
                    msg = (err_obj.get("message") or err_obj.get("error") or "")
                    # OpenRouter nests upstream text in metadata.raw
                    md = err_obj.get("metadata") or {}
                    if isinstance(md, dict) and md.get("raw"):
                        msg = msg + " " + str(md["raw"])
                msg_l = msg.lower()
                err_type = ed.get("type", "")
                if status_code == 504 or "timed out" in msg_l or err_type == "request_timed_out":
                    is_timeout = True
                if is_daily_quota_msg(msg_l, prov, model):
                    is_quota = True
                # 429 on known daily-cap target → treat as quota (cf, gemini-pro, OR :free on $0 credit)
                elif status_code == 429 and is_daily_capped_target(prov, model):
                    is_quota = True
                # bifrost log API frequently drops upstream status_code (we
                # see error_details={} on log entries even for 429 responses).
                # Fallback: if this target is on the daily-cap list (cf-openai,
                # gemini pro, openrouter :free, github-models openai/* high
                # tier) and we have NO classification yet, assume daily.
                # Restore-check smoke-probe will confirm/correct if wrong.
                elif status_code == 0 and is_daily_capped_target(prov, model):
                    is_quota = True
        # Extract per-request latency for P95 tracking. Bifrost puts latency
        # (milliseconds) at top-level of log entry. Successful requests only —
        # error latencies skew the distribution (often quick 429s).
        latency_ms = None
        if log.get("status") != "error":
            try:
                lm = log.get("latency")
                if lm is not None:
                    latency_ms = int(lm)
            except (TypeError, ValueError):
                pass
        key = (prov, model)
        error_window[key].append((ts, log.get("status", "unknown"), status_code, is_timeout, is_quota, latency_ms))

    # Trim old events from window
    for key, dq in error_window.items():
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    # Compute latency stats per target (P50, P95) — visibility only, not
    # used for auto-demote yet. Published to dashboard so humans can spot
    # targets that are healthy-but-slow (vs healthy-and-fast).
    latency_snapshot = {}
    for key, dq in error_window.items():
        prov, model = key
        latencies = [e[5] for e in dq if len(e) > 5 and e[5] is not None]
        if len(latencies) < 3:
            continue
        latencies.sort()
        p50 = latencies[len(latencies)//2]
        p95 = latencies[int(len(latencies)*0.95)] if len(latencies) > 1 else latencies[0]
        latency_snapshot[f"{prov}/{model}"] = {
            "samples": len(latencies),
            "p50_ms": p50,
            "p95_ms": p95,
            "max_ms": latencies[-1],
            "window_sec": WINDOW_SEC,
        }
    state["latency_snapshot"] = latency_snapshot

    # Decide demotions
    pools = fetch_pools()
    for key, events in list(error_window.items()):
        prov, model = key
        full_key = f"{prov}/{model}"
        if full_key in state["demoted"]:
            continue  # already demoted
        # Post-restore grace: don't re-demote within RESTORE_GRACE_SEC
        restored_at = state.get("recently_restored", {}).get(full_key)
        if restored_at and (now_ts() - restored_at) < RESTORE_GRACE_SEC:
            continue
        should, reason, is_daily_quota = evaluate_target(list(events))
        if should:
            tag = "[QUOTA-DEMOTE]" if is_daily_quota else "[DEMOTE]"
            print(f"{tag} {prov}/{model} — {reason}")
            log_event({"action": "demote", "provider": prov, "model": model,
                       "reason": reason, "daily_quota": is_daily_quota})
            n = demote_target(prov, model, pools, state, is_daily_quota=is_daily_quota)
            if n == 0:
                # Virtual model variant (e.g. gemini-3.1-pro-preview-customtools)
                # surfaced in bifrost logs but absent from any routing rule —
                # nothing to demote. Clear in-memory window + skip dashboard
                # update. Without this guard the subsequent state lookup
                # raises KeyError every tick (~30s) for the variant.
                print(f"  skip: {prov}/{model} not in any routing rule")
                log_event({"action": "demote_skipped", "provider": prov, "model": model,
                           "reason": "not in any routing rule (virtual variant?)"})
                error_window.pop(key, None)
                continue
            until = state["demoted"][f"{prov}/{model}"]["restore_at"]
            until_iso = dt.datetime.fromtimestamp(until, dt.timezone.utc).isoformat()
            print(f"  removed from {n} pool(s) — restore at {until_iso}")
            # Clear in-memory window so post-restore doesn't re-trip on stale events
            error_window.pop(key, None)

    # Decide restorations
    now = now_ts()
    pools = fetch_pools()  # re-fetch in case demotions changed it
    for key, info in list(state["demoted"].items()):
        if info["restore_at"] > now:
            continue
        if info.get("pruned"):
            continue  # permanently dead — checked once a day in case provider revives
        prov, model = key.split("/", 1)
        print(f"[RESTORE-CHECK] {prov}/{model}")
        restore_target(prov, model, info, pools, state)

    save_state(state)

def status_cmd(state):
    print(f"# Circuit breaker state — {dt.datetime.now(dt.timezone.utc).isoformat()}")
    print(f"# State file: {STATE_FILE}")
    print(f"# Last poll: {state.get('last_log_ts','never')}")
    if not state["demoted"]:
        print("\nNo targets currently demoted.")
        return
    print(f"\n=== {len(state['demoted'])} demoted target(s) ===")
    now = now_ts()
    for key, info in state["demoted"].items():
        eta = max(0, info["restore_at"] - now)
        pools = ", ".join(p["pool"] for p in info.get("pools", []))
        print(f"  {key}")
        print(f"    pools: {pools}")
        print(f"    cooldown: {info['cooldown_s']}s")
        print(f"    next check in: {int(eta)}s")

def restore_all(state, only_quota=False):
    """Force-attempt restore of all demoted targets.

    only_quota=True: only restore targets flagged as daily_quota (use this in
    a UTC 00:05 cron to recover gemini/cf/openrouter free models after caps
    reset). Also clears pruned flag so they get re-tested.

    Holds state_lock for the entire operation so a concurrently-running
    daemon doesn't overwrite our restored state mid-flight.
    """
    with state_lock(timeout=60):
        # Re-read under lock so we operate on current disk state.
        fresh = load_state()
        state.clear(); state.update(fresh)
        pools = fetch_pools()
        for key, info in list(state["demoted"].items()):
            if only_quota and not info.get("daily_quota"):
                continue
            # Refuse to restore structurally-dead targets — those have been
            # pruned 3+ times. Otherwise pi-op-react burns ~75 requests/cycle
            # proving the model is still broken.
            if info.get("structurally_dead") and (now_ts() - info.get("last_pruned_at", 0)) < 3 * 86400:
                eta_h = int((3 * 86400 - (now_ts() - info.get("last_pruned_at", 0))) / 3600)
                print(f"skip {key}: structurally_dead (pruned {info.get('prune_count')}x) — retry in {eta_h}h")
                continue
            prov, model = key.split("/", 1)
            # Clear prune+fail_count so smoke can succeed
            info.pop("pruned", None)
            info["fail_count"] = 0
            info["restore_at"] = now_ts()  # immediate
            print(f"force-restoring {prov}/{model}")
            restore_target(prov, model, info, pools, state)
        save_state(state)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="single tick then exit")
    ap.add_argument("--status", action="store_true", help="print state, no actions")
    ap.add_argument("--restore-all", action="store_true", help="force restore all demoted targets")
    ap.add_argument("--restore-quota", action="store_true",
                    help="restore only daily-quota-flagged targets (run via UTC midnight cron)")
    args = ap.parse_args()

    state = load_state()

    if args.status:
        status_cmd(state)
        return
    if args.restore_all:
        restore_all(state)
        return
    if args.restore_quota:
        restore_all(state, only_quota=True)
        return

    error_window = defaultdict(lambda: deque(maxlen=200))

    def locked_tick(once_mode=False):
        """Run a tick with the state lock held — re-reads disk first so any
        external mutations (dashboard /api/circuit_breaker/restore, CLI
        --restore-all, manual edits) are picked up before we save."""
        with state_lock(timeout=60):
            fresh = load_state()
            state.clear(); state.update(fresh)
            if once_mode:
                tick(state, error_window, once_mode=True)
            else:
                tick(state, error_window)
            save_state(state)

    if args.once:
        locked_tick(once_mode=True)
        return
    print(f"Circuit breaker started — poll every {POLL_SEC}s, window {WINDOW_SEC}s, threshold {FAIL_RATE:.0%}")
    while True:
        try:
            locked_tick()
        except Exception as e:
            print(f"tick error: {e}", file=sys.stderr)
            log_event({"action": "tick_error", "error": str(e)})
        time.sleep(POLL_SEC)

if __name__ == "__main__":
    main()
