#!/usr/bin/env python3
"""
Tune per-provider network_config in Bifrost (timeouts, retries, backoff).

Bifrost PUT /api/providers/<name> REPLACES the whole config object —
keys MUST be re-sent with their `id` to update in place (omitting `id`
hits the (provider,name) unique constraint and 500s).

GET masks key values, so raw keys are read from the container env vars.
Env-var key tails are verified to match the masked tails in stored config
before writing — bails on mismatch to avoid corrupting auth.

Run on the host where the coire-bifrost container is running.
"""
import urllib.request, json, base64, subprocess, sys, os

BASE = os.environ.get("BIFROST_URL", "http://localhost:4001/api")
USER = os.environ.get("BIFROST_USER", "admin")
PASS = os.environ.get("BIFROST_PASS", "")
if not PASS:
    from pathlib import Path as _P
    _ef = _P(__file__).resolve().parent.parent.parent / ".env"
    if _ef.exists():
        for _l in _ef.read_text().splitlines():
            if _l.startswith("BIFROST_PASS="):
                PASS = _l.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not PASS:
    print("FAIL: BIFROST_PASS not set in env or .env", file=sys.stderr)
    sys.exit(1)
CONTAINER = os.environ.get("BIFROST_CONTAINER", "coire-bifrost")
AUTH = "Basic " + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

# provider name -> env var holding raw key (matches container env)
KEY_ENV = {
    "groq":       "GROQ_API_KEY",
    "cerebras":   "CEREBRAS_API_KEY",
    "mistral":    "MISTRAL_API_KEY",
    "gemini":     "GEMINI_API_KEY",
    "cf-openai":  "CLOUDFLARE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia-nim":    "NVIDIA_API_KEY",
    "sambanova":     "SAMBANOVA_API_KEY",
    "github-models": "GITHUB_MODELS_TOKEN",
    "cohere":        "COHERE_API_KEY",
}

# (provider, timeout_s, max_retries, concurrency) — derived from live-probe
# of free-tier quotas (2026-05-10):
#   mistral:    50k TPM (NOT RPM-bound). Compress requests are 30-50k tokens
#               each → 2 parallel = burst. Set concurrency=2.
#   groq:       6k TPM is the bottleneck. Even 2 parallel small calls can
#               burst the 6k TPM. concurrency=2 keeps it sane.
#   cerebras:   60k TPM, 30 RPM. Plenty of headroom. concurrency=4.
#   gemini:     gemini-pro free is 5 RPM (= one call every 12s). Other
#               variants have more headroom. concurrency=3 avoids burst on
#               flash variants.
#   cf-openai:  10k neurons/day. NOT RPM-bound but parallel calls drain
#               cap fast. concurrency=4.
#   openrouter: 50 RPD pooled across all :free models on $0-credit accounts.
#               concurrency=1 — one at a time keeps the daily budget visible.
#   nvidia-nim: dev preview, ~unlimited. concurrency=8 (lots of capacity).
PLAN = [
    ("groq",          30,  2, 2),
    ("cerebras",      30,  2, 4),
    ("mistral",       120, 1, 2),
    ("gemini",        120, 1, 3),
    ("cf-openai",     120, 1, 4),
    ("openrouter",    300, 1, 1),
    ("nvidia-nim",    300, 1, 8),
    ("sambanova",     60,  1, 1),  # 20 RPD free tier — strict conc=1
    ("github-models", 60,  1, 3),  # 20k/60s burst headroom per probe
    ("cohere",        60,  1, 1),  # 20 RPM trial tier — strict conc=1
]
SAMBANOVA_KEY_ENV = "SAMBANOVA_API_KEY"

def req(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
        headers={"Authorization": AUTH, "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=15).read())

def main():
    env_out = subprocess.check_output(["docker", "exec", CONTAINER, "env"]).decode()
    env = dict(line.split("=", 1) for line in env_out.strip().split("\n") if "=" in line)

    # Get list of providers actually configured in bifrost — skip plan
    # entries for providers user didn't set up (no key in .env → seed.sh
    # didn't register the provider → GET /providers/<name> returns 404).
    try:
        configured = {p["name"] for p in req("GET", "/providers").get("providers", [])}
    except Exception as e:
        sys.exit(f"FAIL: cannot list providers: {e}")

    for name, timeout, retries, concurrency in PLAN:
        if name not in configured:
            print(f"SKIP {name}: not configured in bifrost (no key in .env)")
            continue
        cur = req("GET", f"/providers/{name}")
        raw_key = env.get(KEY_ENV[name], "").strip()
        if not raw_key:
            print(f"SKIP {name}: env {KEY_ENV[name]} empty")
            continue

        # Verify env key matches stored masked tail before overwriting
        masked_tail = cur["keys"][0]["value"]["value"][-4:] if cur.get("keys") else ""
        if masked_tail and not raw_key.endswith(masked_tail):
            print(f"FAIL {name}: env tail {raw_key[-4:]} != stored {masked_tail}")
            sys.exit(1)

        # Update keys in-place (id required; without it = create-new = name collision)
        new_keys = []
        for k in cur.get("keys", []):
            new_keys.append({
                "id": k["id"],
                "name": k["name"],
                "value": {"value": raw_key, "env_var": "", "from_env": False},
                "models": k.get("models", []),
                "blacklisted_models": k.get("blacklisted_models", []),
                "weight": k.get("weight", 1),
                "enabled": k.get("enabled", True),
                "use_for_batch_api": k.get("use_for_batch_api", False),
            })

        nc = cur["network_config"]
        nc["default_request_timeout_in_seconds"] = timeout
        nc["max_retries"] = retries
        nc["retry_backoff_initial"] = 500
        nc["retry_backoff_max"] = 10000
        nc["stream_idle_timeout_in_seconds"] = max(60, timeout // 2)
        if "max_conns_per_host" not in nc:
            nc["max_conns_per_host"] = 5000

        cb = cur.get("concurrency_and_buffer_size", {"concurrency": 1000, "buffer_size": 5000})
        cb["concurrency"] = concurrency
        body = {
            "keys": new_keys,
            "network_config": nc,
            "concurrency_and_buffer_size": cb,
            "proxy_config": cur.get("proxy_config"),
            "send_back_raw_request": cur.get("send_back_raw_request", False),
            "send_back_raw_response": cur.get("send_back_raw_response", False),
            "store_raw_request_response": cur.get("store_raw_request_response", False),
        }
        if cur.get("custom_provider_config"):
            body["custom_provider_config"] = cur["custom_provider_config"]

        res = req("PUT", f"/providers/{name}", body)
        nk = len(res.get("keys", []))
        nt = res["network_config"]["default_request_timeout_in_seconds"]
        nr = res["network_config"]["max_retries"]
        nc_out = res.get("concurrency_and_buffer_size", {}).get("concurrency", "?")
        print(f"OK   {name:12} keys={nk} timeout={nt}s retries={nr} conc={nc_out}")

if __name__ == "__main__":
    main()
