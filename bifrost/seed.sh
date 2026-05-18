#!/usr/bin/env bash
# Seed Bifrost with: 7 providers + 4 routing rules.
# Idempotent — safe to re-run. POSTs only what's missing.
# Reads provider keys from ../.env.
#
# Bifrost API (as of v1.x):
#   POST /api/providers              {provider:"groq",network_config:{}, custom_provider_config:{}}
#   POST /api/providers/<name>/keys  {name:"k1",value:"<key>",weight:1,enabled:true}
#   POST /api/governance/routing-rules
set -euo pipefail

HOST="${BIFROST_HOST:-http://localhost:4001}"
ENV_FILE="${ENV_FILE:-$(dirname "$0")/../.env}"

[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE"; exit 1; }
set -a; source "$ENV_FILE"; set +a

# Wait for Bifrost.
echo "→ waiting for bifrost @ $HOST"
for i in {1..30}; do
  curl -sf "$HOST/api/providers" >/dev/null 2>&1 && break
  sleep 2
done

provider_exists() {
  curl -sf "$HOST/api/providers" | python3 -c "import json,sys; d=json.load(sys.stdin); print(any(p['name']==sys.argv[1] for p in d.get('providers',[])))" "$1"
}

provider_has_key() {
  # Returns "True" if provider already has at least one key.
  curl -sf "$HOST/api/providers/$1/keys" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(bool(d.get('keys')))" 2>/dev/null || echo "False"
}

post_provider() {
  local name=$1; local body=$2; local key_body=$3
  # Bifrost upstream no longer accepts POST /api/providers/<name>/keys
  # (returns 405). Keys must be embedded in the initial provider CREATE
  # body, or PUT alongside full provider config on update.
  if [ "$(provider_exists "$name")" = "True" ]; then
    if [ "$(provider_has_key "$name")" = "True" ]; then
      echo "  $name: provider exists with key — skipping"
      return
    fi
    # Provider exists but is keyless — update via PUT.
    local current; current=$(curl -sf -u "admin:${BIFROST_PASS:-}" "$HOST/api/providers/$name" 2>/dev/null)
    [ -z "$current" ] && { echo "  $name: cannot fetch current config"; return; }
    local merged; merged=$(python3 -c '
import json, sys
cur = json.loads(sys.argv[1])
key = json.loads(sys.argv[2])
cur["keys"] = [key]
print(json.dumps(cur))
' "$current" "$key_body")
    local pc
    pc=$(curl -s -X PUT "$HOST/api/providers/$name" -H "Content-Type: application/json" -d "$merged" -o /tmp/bifrost-$name.out -w "%{http_code}")
    if [ "$pc" = "200" ]; then
      echo "  $name: key added via PUT (HTTP 200)"
    else
      echo "  $name: PUT FAILED HTTP $pc"
      cat /tmp/bifrost-$name.out
    fi
    return
  fi
  # New provider: POST with keys embedded — single call.
  local create_body; create_body=$(python3 -c '
import json, sys
body = json.loads(sys.argv[1])
key = json.loads(sys.argv[2])
body["keys"] = [key]
print(json.dumps(body))
' "$body" "$key_body")
  local code
  code=$(curl -s -X POST "$HOST/api/providers" -H "Content-Type: application/json" -d "$create_body" -o /tmp/bifrost-$name.out -w "%{http_code}")
  if [ "$code" = "200" ]; then
    echo "  $name: provider created with key (HTTP 200)"
  else
    echo "  $name: provider FAILED HTTP $code"
    cat /tmp/bifrost-$name.out
  fi
}

post_rule() {
  local name=$1; local targets=$2; local fallbacks=$3; local priority=$4
  local existing
  existing=$(curl -sf "$HOST/api/governance/routing-rules" | python3 -c "import json,sys; d=json.load(sys.stdin); print(any(r.get('name')==sys.argv[1] for r in d.get('rules',[])))" "$name")
  if [ "$existing" = "True" ]; then
    echo "  rule $name: exists, skipping"
    return
  fi
  curl -s -X POST "$HOST/api/governance/routing-rules" -H "Content-Type: application/json" -d "{
    \"name\":\"$name\",
    \"enabled\":true,
    \"cel_expression\":\"model == \\\"$name\\\"\",
    \"targets\":$targets,
    \"fallbacks\":$fallbacks,
    \"scope\":\"global\",
    \"priority\":$priority
  }" -o /dev/null -w "  rule $name: HTTP %{http_code}\n"
}

simple_key() {
  # Bifrost upstream now expects `value` as an object, not a string:
  #   value: {value: "<raw>", env_var: "", from_env: false}
  # The flat-string form was accepted in earlier versions.
  jq -n --arg n "$1" --arg k "$2" '{name:$n, value:{value:$k, env_var:"", from_env:false}, weight:1, enabled:true, models:[], blacklisted_models:[], use_for_batch_api:false}'
}

echo "→ adding providers"

[ -n "${GROQ_API_KEY:-}" ]       && post_provider groq       '{"provider":"groq"}'        "$(simple_key groq-1 "$GROQ_API_KEY")"
[ -n "${GEMINI_API_KEY:-}" ]     && post_provider gemini     '{"provider":"gemini"}'      "$(simple_key gemini-1 "$GEMINI_API_KEY")"
[ -n "${MISTRAL_API_KEY:-}" ]    && post_provider mistral    '{"provider":"mistral"}'     "$(simple_key mistral-1 "$MISTRAL_API_KEY")"
[ -n "${CEREBRAS_API_KEY:-}" ]   && post_provider cerebras   '{"provider":"cerebras"}'    "$(simple_key cerebras-1 "$CEREBRAS_API_KEY")"
[ -n "${OPENROUTER_API_KEY:-}" ] && post_provider openrouter '{"provider":"openrouter"}'  "$(simple_key or-1 "$OPENROUTER_API_KEY")"

# nvidia-nim — needs request_path_overrides + Authorization header (Bifrost issue #2356)
if [ -n "${NVIDIA_API_KEY:-}" ]; then
  body=$(jq -n --arg k "$NVIDIA_API_KEY" '{
    provider:"nvidia-nim",
    network_config:{
      base_url:"https://integrate.api.nvidia.com/v1",
      extra_headers:{"Authorization":("Bearer "+$k)}
    },
    custom_provider_config:{
      base_provider_type:"openai",
      allowed_requests:{chat_completion:true,chat_completion_stream:true},
      request_path_overrides:{
        chat_completion:"https://integrate.api.nvidia.com/v1/chat/completions",
        chat_completion_stream:"https://integrate.api.nvidia.com/v1/chat/completions"
      }
    }
  }')
  post_provider nvidia-nim "$body" "$(simple_key nim-1 "$NVIDIA_API_KEY")"
fi

# cf-openai — same pattern, account-scoped URL
if [ -n "${CLOUDFLARE_API_KEY:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  CF_BASE="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1"
  body=$(jq -n --arg k "$CLOUDFLARE_API_KEY" --arg b "$CF_BASE" '{
    provider:"cf-openai",
    network_config:{
      base_url:$b,
      extra_headers:{"Authorization":("Bearer "+$k)}
    },
    custom_provider_config:{
      base_provider_type:"openai",
      allowed_requests:{chat_completion:true,chat_completion_stream:true},
      request_path_overrides:{
        chat_completion:($b+"/chat/completions"),
        chat_completion_stream:($b+"/chat/completions")
      }
    }
  }')
  post_provider cf-openai "$body" "$(simple_key cf-1 "$CLOUDFLARE_API_KEY")"
fi

# deepseek — DeepSeek's own OpenAI-compat API (api.deepseek.com)
# Models: deepseek-chat (V4-Pro non-reasoning), deepseek-reasoner (V4-Pro reasoning).
# Free credit on signup; paid after credit exhausts. Cheaper than NIM-hosted.
if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  DS_BASE="https://api.deepseek.com/v1"
  body=$(jq -n --arg k "$DEEPSEEK_API_KEY" --arg b "$DS_BASE" '{
    provider:"deepseek",
    network_config:{
      base_url:$b,
      extra_headers:{"Authorization":("Bearer "+$k)},
      default_request_timeout_in_seconds:300
    },
    custom_provider_config:{
      base_provider_type:"openai",
      allowed_requests:{chat_completion:true,chat_completion_stream:true},
      request_path_overrides:{
        chat_completion:($b+"/chat/completions"),
        chat_completion_stream:($b+"/chat/completions")
      }
    }
  }')
  post_provider deepseek "$body" "$(simple_key deepseek-1 "$DEEPSEEK_API_KEY")"
fi

echo "→ adding routing rules (4 pools, IQ-first weighting)"

# Build set of configured providers — used to filter fallbacks so we don't
# post rules referencing providers that aren't set up.
PROVS=$(curl -sf "$HOST/api/providers" | python3 -c "import json,sys; d=json.load(sys.stdin); print(' '.join(p['name'] for p in d.get('providers',[])))")

filter_fbs() {
  python3 -c "
import json, sys
fbs = json.loads(sys.argv[1])
provs = set(sys.argv[2].split())
kept = [f for f in fbs if f.split('/')[0] in provs]
print(json.dumps(kept))
" "$1" "$PROVS"
}

# best — frontier general reasoning. CF Kimi K2.6 (AA IQ 53.9, highest free) as final fallback escape valve.
post_rule best \
  '[{"provider":"nvidia-nim","model":"deepseek-ai/deepseek-v4-pro","weight":0.40},{"provider":"cerebras","model":"qwen-3-235b-a22b-instruct-2507","weight":0.30},{"provider":"groq","model":"openai/gpt-oss-120b","weight":0.20},{"provider":"gemini","model":"gemini-3-flash-preview","weight":0.10}]' \
  "$(filter_fbs '["groq/openai/gpt-oss-120b","openrouter/openai/gpt-oss-120b:free","mistral/mistral-medium-2505","gemini/gemini-flash-latest","cf-openai/@cf/moonshotai/kimi-k2.6"]')" 11

# code — code+reasoning. Kimi K2.6 in fallbacks (code score 47.1, beats most peers).
post_rule code \
  '[{"provider":"nvidia-nim","model":"qwen/qwen3-coder-480b-a35b-instruct","weight":0.40},{"provider":"cerebras","model":"qwen-3-235b-a22b-instruct-2507","weight":0.25},{"provider":"mistral","model":"magistral-medium-2509","weight":0.20},{"provider":"groq","model":"openai/gpt-oss-120b","weight":0.15}]' \
  "$(filter_fbs '["groq/openai/gpt-oss-120b","mistral/magistral-small-latest","openrouter/openai/gpt-oss-120b:free","gemini/gemini-3-flash-preview","cf-openai/@cf/moonshotai/kimi-k2.6"]')" 12

# fast — low-latency 8B. CF llama-3.1-8b as 3rd source diversity.
post_rule fast \
  '[{"provider":"groq","model":"llama-3.1-8b-instant","weight":0.70},{"provider":"cerebras","model":"llama3.1-8b","weight":0.30}]' \
  "$(filter_fbs '["mistral/mistral-small-latest","gemini/gemini-3.1-flash-lite-preview","cf-openai/@cf/meta/llama-3.1-8b-instruct-fp8"]')" 13

# vision — multimodal
post_rule vision \
  '[{"provider":"gemini","model":"gemini-2.5-flash","weight":0.50},{"provider":"gemini","model":"gemini-flash-latest","weight":0.30},{"provider":"gemini","model":"gemini-3-flash-preview","weight":0.20}]' \
  "$(filter_fbs '["nvidia-nim/meta/llama-3.2-90b-vision-instruct"]')" 14

# mid — IQ 25-35 cheap-fast workhorse for aux tasks (compression, web_extract, approval).
# Per Nous docs: aux summarization/judging tasks should use cheap-fast tier, not flagship.
post_rule mid \
  '[{"provider":"mistral","model":"mistral-small-latest","weight":0.40},{"provider":"gemini","model":"gemini-3.1-flash-lite-preview","weight":0.25},{"provider":"cf-openai","model":"@cf/google/gemma-4-26b-a4b-it","weight":0.20},{"provider":"groq","model":"openai/gpt-oss-20b","weight":0.15}]' \
  "$(filter_fbs '["cerebras/qwen-3-235b-a22b-instruct-2507","groq/llama-3.1-8b-instant"]')" 15

echo "✓ seed complete"
