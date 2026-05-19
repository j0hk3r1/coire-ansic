#!/usr/bin/env python3
"""Watch the chosen frontend service for crash-loops, escalate if seen.

Reads systemd journal for the frontend user-service (default
hermes-gateway.service — override with FRONTEND_SERVICE env var) and
counts how many times the service hit its main process restart in the
last 60 seconds. If the count is ≥3, the loop is unhealthy and an
op-log JSONL entry is appended for the operator to triage.

systemd's RestartMaxDelaySec already throttles restart attempts (default
hermes config: 5s baseline, max 300s), but doesn't tell anyone when a
service is stuck restart-looping. This script is the alarm.

Designed to run every minute via systemd timer (frontend-watchdog.timer).
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OP_LOG_BIN = ROOT / "operator" / "bin" / "op-log"
LOG_DIR = Path.home() / ".coire" / "operator" / "logs"
STATE = Path.home() / ".coire" / "curator-pool" / "frontend_watchdog.json"

DEFAULT_SERVICE = os.environ.get("FRONTEND_SERVICE", "hermes-gateway.service")
CRASH_THRESHOLD = int(os.environ.get("FRONTEND_CRASH_THRESHOLD", 3))
WINDOW_SEC = int(os.environ.get("FRONTEND_WATCH_WINDOW_SEC", 60))


def count_recent_restarts(service: str, window_sec: int) -> int:
    """systemd records each main-process exit + restart in the journal.
    Count 'Main process exited' lines for the service in the last window."""
    since = dt.datetime.now() - dt.timedelta(seconds=window_sec)
    since_str = since.strftime("%Y-%m-%d %H:%M:%S")
    try:
        out = subprocess.run(
            ["journalctl", "--user", "-u", service, "--since", since_str,
             "--no-pager", "-q"],
            check=False, capture_output=True, text=True, timeout=20,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  journalctl failed: {e}", file=sys.stderr)
        return 0
    n = 0
    for line in (out.stdout or "").splitlines():
        if "Main process exited" in line or "Failed with result" in line:
            n += 1
    return n


def escalate(service: str, restarts: int, window_sec: int) -> None:
    """Write an op-log JSONL line so the operator audit picks it up."""
    payload = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "job": "frontend_watchdog",
        "service": service,
        "restarts_in_window": restarts,
        "window_sec": window_sec,
        "severity": "warn",
        "message": f"{service} crash-looping: {restarts} restarts in {window_sec}s",
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    with (LOG_DIR / f"{day}.jsonl").open("a") as f:
        f.write(json.dumps(payload) + "\n")
    # Also use op-log helper if available (gives structured pipeline)
    if OP_LOG_BIN.exists() and OP_LOG_BIN.is_file() and os.access(OP_LOG_BIN, os.X_OK):
        try:
            p = subprocess.Popen([str(OP_LOG_BIN), "-"], stdin=subprocess.PIPE)
            p.communicate(input=json.dumps(payload).encode(), timeout=5)
        except Exception:
            pass


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--service", default=DEFAULT_SERVICE)
    ap.add_argument("--window-sec", type=int, default=WINDOW_SEC)
    ap.add_argument("--threshold", type=int, default=CRASH_THRESHOLD)
    args = ap.parse_args()

    n = count_recent_restarts(args.service, args.window_sec)
    state = load_state()
    last = state.get(args.service, {})
    last_alert = last.get("last_alert_ts", 0)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    print(f"watchdog: {args.service} restarts last {args.window_sec}s = {n}")
    if n >= args.threshold:
        # Throttle alerts — don't fire more than once per 10 minutes
        if (now - last_alert) >= 600:
            print(f"  ESCALATING — {n} >= {args.threshold}")
            escalate(args.service, n, args.window_sec)
            last["last_alert_ts"] = now
            last["last_count"] = n
            state[args.service] = last
            save_state(state)
        else:
            print(f"  (alert recently sent — throttled)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
