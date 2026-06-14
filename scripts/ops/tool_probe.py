#!/usr/bin/env python3
"""tool_probe.py — fire ONE tool-call at each model THROUGH THE SHIM (:4001, the real path) and
classify: WORKS (valid tool_calls) / NOTOOL (answered, no call) / EMPTY / ERROR, with latency +
served-by. Read-only; used to vet models before wiring them into a pool's fallbacks.

  tool_probe.py                       # default set (current coire-main chain)
  tool_probe.py provider/model ...    # probe specific models
  tool_probe.py --file models.txt     # one provider/model per line (# comments ok)
  COIRE_URL=http://host:4001/v1/chat/completions tool_probe.py ...   # override endpoint
"""
import json, urllib.request, urllib.error, time, os, sys

URL = os.environ.get("COIRE_URL", "http://localhost:4001/v1/chat/completions")
TOOL = [{"type": "function", "function": {
    "name": "get_weather", "description": "Get current weather for a city",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]

DEFAULT = [
    "cerebras/zai-glm-4.7", "cerebras/gpt-oss-120b", "gemini/gemini-3.5-flash",
    "mistral/mistral-large-2512", "sambanova/DeepSeek-V3.2",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free", "mistral/mistral-medium-2604",
    "cohere/command-a-03-2025", "github-models/openai/gpt-4.1-mini",
    "opencode-zen/deepseek-v4-flash-free",
]

def models_from_args(argv):
    if not argv:
        return DEFAULT
    if argv[0] == "--file":
        with open(argv[1]) as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    return argv

def probe(model):
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 600,
                       "messages": [{"role": "user", "content": "What is the weather in Lisbon right now? Call the get_weather tool."}],
                       "tools": TOOL, "tool_choice": "auto"}).encode()
    t0 = time.monotonic()
    try:
        r = urllib.request.urlopen(urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}), timeout=50)
        dt = time.monotonic() - t0
        d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return ("ERROR", time.monotonic() - t0, f"HTTP {e.code} {e.read()[:90].decode('utf-8','ignore')}", "")
    except Exception as e:
        return ("ERROR", time.monotonic() - t0, str(e)[:90], "")
    served = d.get("model", "?")
    msg = (d.get("choices") or [{}])[0].get("message", {}) or {}
    tcs = msg.get("tool_calls") or []
    if tcs:
        fn = tcs[0].get("function") or {}
        name = fn.get("name", "?")
        try:
            args = json.loads(fn.get("arguments") or "{}"); ok = name == "get_weather" and "city" in args
        except Exception:
            ok = name == "get_weather"; args = fn.get("arguments")
        return ("WORKS" if ok else "ODDTOOL", dt, f"{name}({args})", served)
    content = (msg.get("content") or "").strip()
    if content:
        return ("NOTOOL", dt, content[:70].replace("\n", " "), served)
    return ("EMPTY", dt, "no content, no tool_call", served)

def main():
    models = models_from_args(sys.argv[1:])
    print(f"{'v':1} {'verdict':7} {'lat':>6}  {'model':54} served-by / note")
    print("=" * 120)
    for m in models:
        verdict, dt, note, served = probe(m)
        mark = {"WORKS": "✓", "NOTOOL": "·", "ODDTOOL": "~", "EMPTY": "∅", "ERROR": "✗"}.get(verdict, "?")
        print(f"{mark} {verdict:7} {dt:5.1f}s  {m:54} {served}  | {note}")

if __name__ == "__main__":
    main()
