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

# cloudflare — same pattern, account-scoped URL
if [ -n "${CLOUDFLARE_API_KEY:-}" ] && [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
  CF_BASE="https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1"
  body=$(jq -n --arg k "$CLOUDFLARE_API_KEY" --arg b "$CF_BASE" '{
    provider:"cloudflare",
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
  post_provider cloudflare "$body" "$(simple_key cf-1 "$CLOUDFLARE_API_KEY")"
fi

# github-models — GitHub-hosted Azure inference; massive free tier (20k RPM, 2M TPM)
# Endpoint: https://models.github.ai/inference  (NO trailing /v1)
# Auth header: Authorization: Bearer <PAT-with-models-scope>
if [ -n "${GITHUB_MODELS_TOKEN:-}" ]; then
  GH_BASE="https://models.github.ai/inference"
  body=$(jq -n --arg k "$GITHUB_MODELS_TOKEN" --arg b "$GH_BASE" '{
    provider:"github-models",
    network_config:{
      base_url:$b,
      extra_headers:{"Authorization":("Bearer "+$k)},
      default_request_timeout_in_seconds:120
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
  post_provider github-models "$body" "$(simple_key gh-1 "$GITHUB_MODELS_TOKEN")"
fi

# cohere — bifrost has native cohere provider; cannot use custom_provider_config.
# Free trial: 20 RPM, 1000 calls/month per endpoint.
if [ -n "${COHERE_API_KEY:-}" ]; then
  post_provider cohere '{"provider":"cohere"}' "$(simple_key cohere-1 "$COHERE_API_KEY")"
fi

# sambanova — OpenAI-compat at api.sambanova.ai/v1
# Free tier: 20 RPD hard cap. Tiny but still useful as a fallback.
if [ -n "${SAMBANOVA_API_KEY:-}" ]; then
  SN_BASE="https://api.sambanova.ai/v1"
  body=$(jq -n --arg k "$SAMBANOVA_API_KEY" --arg b "$SN_BASE" '{
    provider:"sambanova",
    network_config:{
      base_url:$b,
      extra_headers:{"Authorization":("Bearer "+$k)},
      default_request_timeout_in_seconds:60
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
  post_provider sambanova "$body" "$(simple_key sn-1 "$SAMBANOVA_API_KEY")"
fi

# opencode-zen — opencode's paid-tier preview, OpenAI-compat
# Demo tier: ~5-10 calls/day pooled. Useful as deep last-resort cushion.
if [ -n "${OPENCODE_ZEN_API_KEY:-}" ]; then
  ZEN_BASE="https://opencode.ai/zen/v1"
  body=$(jq -n --arg k "$OPENCODE_ZEN_API_KEY" --arg b "$ZEN_BASE" '{
    provider:"opencode-zen",
    network_config:{
      base_url:$b,
      extra_headers:{"Authorization":("Bearer "+$k)},
      default_request_timeout_in_seconds:90
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
  post_provider opencode-zen "$body" "$(simple_key zen-1 "$OPENCODE_ZEN_API_KEY")"
fi

# zai (Zhipu) — GLM models native source. Path-rewrite via strip-shim because
# Z.ai uses /api/paas/v4/chat/completions (no /v1/) while bifrost openai-compat
# hardcodes /v1/chat/completions suffix. Shim catches /zai-proxy/v4/v1/* and
# rewrites to Z.ai's native path. ZAI_API_KEY env var passed to shim container.
if [ -n "${ZAI_API_KEY:-}" ]; then
  ZAI_BASE="http://coire-strip-shim:4002/zai-proxy/v4"
  body=$(jq -n --arg k "$ZAI_API_KEY" --arg b "$ZAI_BASE" '{
    provider:"zai",
    network_config:{
      base_url:$b,
      default_request_timeout_in_seconds:90
    },
    custom_provider_config:{
      base_provider_type:"openai",
      allowed_requests:{chat_completion:true,chat_completion_stream:true}
    }
  }')
  post_provider zai "$body" "$(simple_key zai-1 "$ZAI_API_KEY")"
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

# Routing rules are NOT created here — apply_snapshot.py + apply_pool_weights.py
# handle them based on bifrost/snapshot/routing-rules.json + scripts/runtime/pool_weights.yaml.
# seed.sh's job is just to create providers from .env keys.

echo "✓ seed complete (providers only — rules applied next via apply_snapshot + apply_pool_weights)"
