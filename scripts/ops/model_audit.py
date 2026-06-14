#!/usr/bin/env python3
"""model_audit.py — enumerate currently-available models per provider, diff against what
bifrost/config.json already references. Surfaces NEW models (candidates) + free flags.

Run where the provider keys live (the router host, e.g. .93) with the env loaded:
    set -a; . ~/coire-ansic/.env; set +a; python3 scripts/ops/model_audit.py

Read-only. Pairs with tool_probe.py / vision_probe.py (vet a candidate before wiring it into a
pool). See scripts/ops/README.md and the model-audit TODO."""
import os, json, urllib.request, urllib.error, re, pathlib

CFG = os.environ.get("COIRE_CONFIG", str(pathlib.Path(__file__).resolve().parents[2] / "bifrost" / "config.json"))
ACCT = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")

def bearer(k): return f"Bearer {os.environ.get(k,'')}"

# provider -> (url, auth_header_value_or_None, json_path_to_list, id_field)
PROVIDERS = {
    "groq":          ("https://api.groq.com/openai/v1/models",            bearer("GROQ_API_KEY"),         "data", "id"),
    "cerebras":      ("https://api.cerebras.ai/v1/models",                bearer("CEREBRAS_API_KEY"),     "data", "id"),
    "mistral":       ("https://api.mistral.ai/v1/models",                 bearer("MISTRAL_API_KEY"),      "data", "id"),
    "sambanova":     ("https://api.sambanova.ai/v1/models",               bearer("SAMBANOVA_API_KEY"),    "data", "id"),
    "nvidia-nim":    ("https://integrate.api.nvidia.com/v1/models",       bearer("NVIDIA_API_KEY"),       "data", "id"),
    "opencode-zen":  ("https://opencode.ai/zen/v1/models",                bearer("OPENCODE_ZEN_API_KEY"), "data", "id"),
    "cohere":        ("https://api.cohere.com/v1/models",                 bearer("COHERE_API_KEY"),       "models", "name"),
    "openrouter":    ("https://openrouter.ai/api/v1/models",              None,                           "data", "id"),
    "github-models": ("https://models.github.ai/catalog/models",          bearer("GITHUB_MODELS_TOKEN"),  None, "id"),
    "zai":           ("https://api.z.ai/api/paas/v4/models",              bearer("ZAI_API_KEY"),          "data", "id"),
    "gemini":        (f"https://generativelanguage.googleapis.com/v1beta/models?key={os.environ.get('GEMINI_API_KEY','')}", None, "models", "name"),
    "cloudflare":    (f"https://api.cloudflare.com/client/v4/accounts/{ACCT}/ai/models/search?task=Text%20Generation&per_page=100", bearer("CLOUDFLARE_API_KEY"), "result", "name"),
    "deepseek":      ("https://api.deepseek.com/models",                  bearer("DEEPSEEK_API_KEY"),     "data", "id"),
}

def fetch(url, auth):
    h = {"User-Agent": "coire-audit", "Accept": "application/json"}
    if auth: h["Authorization"] = auth
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25)
        return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code} {e.read()[:120].decode('utf-8','ignore')}"
    except Exception as e:
        return None, str(e)[:140]

def extract(payload, path, idf):
    lst = payload.get(path) if path else payload
    if isinstance(lst, dict): lst = lst.get("data") or lst.get("models") or []
    out = []
    for m in (lst or []):
        if isinstance(m, str): out.append((m, m)); continue
        mid = re.sub(r"^models/", "", m.get(idf) or m.get("id") or m.get("name") or "")  # gemini prefixes "models/"
        free = None
        pr = m.get("pricing") or {}
        if pr:
            try: free = (float(pr.get("prompt", 1)) == 0 and float(pr.get("completion", 1)) == 0)
            except Exception: free = None
        if mid.endswith(":free"): free = True
        out.append((mid, free))
    return out

cfg = json.load(open(CFG))
referenced = set()
for rule in cfg["governance"]["routing_rules"]:
    for t in rule.get("targets", []): referenced.add(f"{t['provider']}/{t['model']}")
    referenced.update(rule.get("fallbacks", []))
def ref_has(prov, mid): return f"{prov}/{mid}" in referenced

print("PROVIDERS IN CONFIG:", ", ".join(sorted(cfg["providers"].keys())))
print("=" * 90)
for prov, (url, auth, path, idf) in PROVIDERS.items():
    payload, err = fetch(url, auth)
    if err:
        print(f"\n### {prov}: ERROR — {err}"); continue
    models = extract(payload, path, idf)
    note = "  (NOT a configured provider)" if prov not in cfg["providers"] else ""
    print(f"\n### {prov}: {len(models)} models{note}")
    for mid, free in sorted(models, key=lambda x: x[0]):
        tag = "★used" if ref_has(prov, mid) else "     "
        ftag = "FREE" if free is True else ("paid" if free is False else "    ")
        print(f"   {tag} {ftag}  {mid}")
