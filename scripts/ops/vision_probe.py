#!/usr/bin/env python3
"""vision_probe.py — send ONE image to each model THROUGH THE SHIM (:4001) and check it can SEE:
returns WORKS (non-empty description, ideally mentions the expected keyword) / EMPTY / ERROR, with
latency + a snippet to eyeball. Read-only; used to vet vision models for the coire-vision pool.

  vision_probe.py                     # default vision candidate set
  vision_probe.py provider/model ...  # specific models
  vision_probe.py --file models.txt   # one per line
"""
import json, urllib.request, urllib.error, time, os, sys

URL = os.environ.get("COIRE_URL", "http://localhost:4001/v1/chat/completions")
# Providers (gemini etc.) do NOT fetch external URLs — the image must be sent INLINE as a base64
# data URI. Embedded deterministic test image: the word "CAT" in black on white (320x200 PNG). No
# network, no PIL needed at runtime; a model that can see will read it. EXPECT keyword = "cat".
_CAT_PNG_B64 = (
    "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"
)
IMG = "data:image/png;base64," + _CAT_PNG_B64
EXPECT = "cat"

DEFAULT = [
    "gemini/gemini-3.5-flash", "gemini/gemini-2.5-flash", "gemini/gemini-2.5-flash-lite",
    "gemini/gemini-2.5-pro",
    "sambanova/Llama-4-Maverick-17B-128E-Instruct",
    "nvidia-nim/meta/llama-3.2-90b-vision-instruct",
    "cohere/command-a-vision-07-2025",
    "openrouter/nvidia/nemotron-nano-12b-v2-vl:free",
    "cloudflare/@cf/meta/llama-3.2-11b-vision-instruct",
]

def models(argv):
    if not argv: return DEFAULT
    if argv[0] == "--file":
        return [l.strip() for l in open(argv[1]) if l.strip() and not l.startswith("#")]
    return argv

def probe(model):
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 200, "messages": [
        {"role": "user", "content": [
            {"type": "text", "text": "Describe this image in one short sentence."},
            {"type": "image_url", "image_url": {"url": IMG}}]}]}).encode()
    t0 = time.monotonic()
    try:
        r = urllib.request.urlopen(urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"}), timeout=60)
        dt = time.monotonic() - t0
        d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return ("ERROR", time.monotonic() - t0, f"HTTP {e.code} {e.read()[:80].decode('utf-8','ignore')}", "")
    except Exception as e:
        return ("ERROR", time.monotonic() - t0, str(e)[:80], "")
    served = d.get("model", "?")
    content = ((d.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
    content = content.strip().replace("\n", " ")
    if not content:
        return ("EMPTY", dt, "no content", served)
    verdict = "WORKS" if EXPECT in content.lower() else "SEES?"   # SEES? = described something but not the expected keyword
    return (verdict, dt, content[:90], served)

def main():
    print(f"{'v':1} {'verdict':7} {'lat':>6}  {'model':50} note (served-by | snippet)")
    print("=" * 120)
    for m in models(sys.argv[1:]):
        verdict, dt, note, served = probe(m)
        mark = {"WORKS": "✓", "SEES?": "~", "EMPTY": "∅", "ERROR": "✗"}.get(verdict, "?")
        print(f"{mark} {verdict:7} {dt:5.1f}s  {m:50} {served} | {note}")

if __name__ == "__main__":
    main()
