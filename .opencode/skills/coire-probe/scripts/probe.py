#!/usr/bin/env python3
"""coire-probe — test a model/provider for tool-calling + latency + rate-limit.

Probes via shim (default), bifrost (raw), or direct (bypass stack entirely if --base-url passed).
Outputs versioned table for quick human + LLM scan.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.request
import urllib.error

SHIM_URL = "http://localhost:4002/v1/chat/completions"
BIFROST_URL = "http://localhost:4001/v1/chat/completions"

TOOL_PAYLOAD = {
    "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }],
    "tool_choice": "auto",
    "max_tokens": 80,
}

SIMPLE_PAYLOAD = {
    "messages": [{"role": "user", "content": "reply: ok"}],
    "max_tokens": 20,
}


def make_big_payload(token_count: int = 30000):
    big = "Analyze carefully. " * (token_count // 3)
    return {
        "messages": [{"role": "user", "content": big + " ack"}],
        "max_tokens": 50,
    }


def fire(url: str, body: dict, headers: dict, timeout: int = 60) -> tuple[int, dict, dict, float]:
    """Returns (status_code, response_body_dict, response_headers, elapsed_sec)."""
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    start = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        elapsed = time.time() - start
        body_bytes = r.read()
        try:
            body_dict = json.loads(body_bytes)
        except json.JSONDecodeError:
            body_dict = {"_raw": body_bytes.decode("utf-8", errors="replace")[:300]}
        return r.status, body_dict, dict(r.headers), elapsed
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        body_bytes = e.read()
        try:
            body_dict = json.loads(body_bytes)
        except json.JSONDecodeError:
            body_dict = {"_raw": body_bytes.decode("utf-8", errors="replace")[:300]}
        return e.code, body_dict, dict(e.headers), elapsed
    except (urllib.error.URLError, TimeoutError) as e:
        elapsed = time.time() - start
        return 0, {"_err": str(e)[:200]}, {}, elapsed


def classify_tool_response(body: dict) -> tuple[str, str]:
    """Returns (verdict, detail)."""
    if "error" in body or "_err" in body:
        err = body.get("error") or body.get("_err") or {}
        if isinstance(err, dict):
            msg = err.get("message") or json.dumps(err)[:120]
        else:
            msg = str(err)[:120]
        return "ERROR", msg
    choices = body.get("choices") or []
    if not choices:
        return "BADRESP", "no choices in response"
    msg = (choices[0] or {}).get("message") or {}
    if msg.get("tool_calls"):
        tc = msg["tool_calls"][0]
        name = (tc.get("function") or {}).get("name", "?")
        return "TOOLCALL", f"called {name}"
    content = (msg.get("content") or "")[:80]
    return "TEXT-ONLY", content


def filter_rate_headers(headers: dict) -> dict:
    """Extract rate-limit related headers."""
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if any(x in kl for x in ["ratelimit", "x-trial", "x-quota", "retry-after"]):
            out[k] = v
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model", help="provider/model spec (e.g., cerebras/qwen-3-235b-a22b-instruct-2507, zai/glm-4.7-flash)")
    p.add_argument("--via", choices=["shim", "bifrost"], default="shim",
                   help="Probe via shim (default — full omo path) or raw bifrost")
    p.add_argument("--no-tools", action="store_true", help="Skip tool-calling test")
    p.add_argument("--big", action="store_true", help="Also test with ~30k token prompt")
    p.add_argument("--ctx", type=int, default=30000, help="Token count for --big test (default 30000)")
    args = p.parse_args()

    url = SHIM_URL if args.via == "shim" else BIFROST_URL
    print(f"## coire-probe v0.1 — model={args.model} via={args.via}")
    print()

    # Test 1: simple (warmup + latency check)
    body = {"model": args.model, **SIMPLE_PAYLOAD}
    code, resp, headers, elapsed = fire(url, body, {"Content-Type": "application/json"}, timeout=30)
    simple_verdict, simple_detail = classify_tool_response(resp)
    print(f"### Simple probe (no tools)")
    print(f"- HTTP {code}, {elapsed:.2f}s")
    print(f"- {simple_verdict}: {simple_detail}")
    print()

    # Test 2: tool-calling
    if not args.no_tools:
        body = {"model": args.model, **TOOL_PAYLOAD}
        code, resp, headers, elapsed = fire(url, body, {"Content-Type": "application/json"}, timeout=30)
        tool_verdict, tool_detail = classify_tool_response(resp)
        print(f"### Tool-calling probe (get_weather)")
        print(f"- HTTP {code}, {elapsed:.2f}s")
        print(f"- {tool_verdict}: {tool_detail}")
        print()

    # Test 3: big context (optional)
    if args.big:
        body = {"model": args.model, **make_big_payload(args.ctx)}
        code, resp, headers, elapsed = fire(url, body, {"Content-Type": "application/json"}, timeout=120)
        big_verdict, big_detail = classify_tool_response(resp)
        print(f"### Big-context probe (~{args.ctx} tokens)")
        print(f"- HTTP {code}, {elapsed:.2f}s")
        print(f"- {big_verdict}: {big_detail}")
        print()

    # Rate-limit headers from last call
    rate = filter_rate_headers(headers)
    if rate:
        print("### Rate-limit headers (from last call)")
        for k, v in rate.items():
            print(f"- `{k}`: {v}")
        print()

    # Verdict
    print("### Verdict")
    flags = []
    if simple_verdict == "ERROR":
        flags.append(f"❌ simple probe ERROR: {simple_detail}")
    elif simple_verdict == "TEXT-ONLY":
        flags.append("✅ simple probe ok (text response)")
    elif simple_verdict == "TOOLCALL":
        flags.append("✅ simple probe ok (model called tool unprompted — strong tool support)")
    if not args.no_tools:
        if tool_verdict == "TOOLCALL":
            flags.append("✅ tool-calling works — usable in omo pools")
        elif tool_verdict == "TEXT-ONLY":
            flags.append("⚠️ TOOL-CALLING BROKEN — model responds text-only to tool prompt. NOT for omo pools.")
        else:
            flags.append(f"❌ tool probe {tool_verdict}: {tool_detail}")
    if args.big:
        if big_verdict in ("TOOLCALL", "TEXT-ONLY"):
            flags.append(f"✅ ~{args.ctx}-token context handled")
        else:
            flags.append(f"⚠️ big-context probe failed: {big_detail}")
    if elapsed > 10:
        flags.append(f"⚠️ slow ({elapsed:.1f}s) — fb=last-resort material only")
    elif elapsed < 1:
        flags.append(f"✅ fast (<1s)")
    for f in flags:
        print(f"- {f}")


if __name__ == "__main__":
    main()
