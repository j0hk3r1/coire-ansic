"""OpenAI-compat proxy that strips reasoning_content from assistant messages
before forwarding to Bifrost. Handles streaming + non-streaming.

Always-on front door: this shim owns the public :4001 and forwards to bifrost
(internal :8080 / host-debug :4011). Every request is normalized for known
provider quirks (Mistral tool-id format, Kimi/Qwen control-token tool calls,
reasoning-only retries, param-rejection recovery) on /v1; /anthropic and /api
are passed straight through to bifrost unchanged.

Tool-id rewrite: ALL tool_call_ids rewritten to 9-char hex unconditionally.
Mistral requires ^[a-zA-Z0-9]{9}$ — and since clients use pool names
("coire-main"/"coire-fast") in the model field, the shim cannot tell which
provider Bifrost will pick. 9-char hex is alphanumeric and accepted by
every OpenAI-compat provider, so unconditional rewrite is safe.
"""
import hashlib
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response, JSONResponse

BIFROST_URL = os.environ.get("BIFROST_URL", "http://bifrost:8080")
PORT = int(os.environ.get("PORT", "4002"))
# Optional models.json (mounted at /root/.coire/models.json) lets /v1/models
# advertise the live pool/target set. Missing file is fine — we fall back to
# the static pool names below so /v1/models always answers.
MODELS_JSON_PATH = os.environ.get("MODELS_JSON_PATH", "/root/.coire/models.json")

log = logging.getLogger("shim")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10, read=400, write=30, pool=30),
        limits=httpx.Limits(max_keepalive_connections=50, max_connections=200),
    )
    yield
    await app.state.client.aclose()


app = FastAPI(title="bifrost-strip-shim", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    try:
        r = await request.app.state.client.get(f"{BIFROST_URL}/api/providers", timeout=3)
        upstream_ok = r.status_code in (200, 401)
    except Exception as e:
        return JSONResponse({"shim": "ok", "bifrost": f"error: {e}"}, status_code=503)
    return {"shim": "ok", "bifrost": "ok" if upstream_ok else f"http {r.status_code}"}


_FALLBACK_POOLS = ["coire-main", "coire-fast", "coire-vision", "coire-chat"]


def _load_models_doc():
    """Read models.json or return None on any failure."""
    try:
        with open(MODELS_JSON_PATH, "rb") as f:
            doc = json.load(f)
        if isinstance(doc, dict) and isinstance(doc.get("data"), list):
            return doc
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        log.info("models.json unavailable (%s)", e)
    return None


@app.get("/v1/models")
async def list_pool_models():
    """Default model picker — POOL ALIASES ONLY.

    OpenAI-compat clients show every /v1/models entry in
    their picker. Showing all ~50 direct provider/model targets alongside
    the pool aliases is noise. Direct targets are still pinnable by name
    (bifrost accepts 'cerebras/zai-glm-4.7' etc.) and discoverable via
    /v1/models/full for power users + the dashboard.
    """
    doc = _load_models_doc()
    if doc is not None:
        # Filter to pool aliases — direct targets have a "/" in id (e.g.
        # cerebras/gpt-oss-120b); pool aliases don't (coire-main, coire-fast…).
        pools = [m for m in doc["data"] if "/" not in m.get("id", "")]
        if pools:
            return {**doc, "data": pools}
    now = int(time.time())
    return {
        "object": "list",
        "data": [{"id": p, "object": "model", "created": now, "owned_by": "coire-ansic"}
                 for p in _FALLBACK_POOLS],
    }


@app.get("/v1/models/full")
async def list_all_models():
    """Power-user / dashboard endpoint — pool aliases + every direct
    provider/model target with status tags (primary/fallback/unrouted).
    """
    doc = _load_models_doc()
    if doc is not None:
        return doc
    return await list_pool_models()


@app.get("/stub/models")
@app.get("/api/v1/models")
@app.get("/api/tags")
async def stub_models():
    return {"object": "list", "data": []}


@app.get("/version")
async def stub_version():
    return {"version": "shim-1.0"}


def strip_reasoning(messages: list) -> list:
    cleaned = []
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "assistant":
            m = {k: v for k, v in m.items() if k not in (
                "reasoning_content", "reasoning", "reasoning_details",
                "provider_specific_fields",
            )}
        cleaned.append(m)
    return cleaned


# Output caps. Some agentic clients ask for very large maxTokens (extended
# planning/reasoning). 65536 is the highest output any of our free providers
# supports — pass that through on pool aliases (no "/" in the model name); the
# cascade naturally walks on if a provider rejects a too-big request. Direct
# provider/model calls get the conservative DEFAULT_OUTPUT_CAP.
POOL_OUTPUT_CAP = int(os.environ.get("STRIP_SHIM_POOL_OUTPUT_CAP", "65536"))
DEFAULT_OUTPUT_CAP = int(os.environ.get("STRIP_SHIM_MAX_OUTPUT_CAP", "16384"))


def clamp_max_tokens(data: dict) -> None:
    """Cap max_tokens / max_completion_tokens in-place. Pool aliases (no "/")
    get POOL_OUTPUT_CAP; direct provider/model calls get DEFAULT_OUTPUT_CAP."""
    model = data.get("model") or ""
    cap = DEFAULT_OUTPUT_CAP if "/" in model else POOL_OUTPUT_CAP
    for field in ("max_tokens", "max_completion_tokens"):
        mt = data.get(field)
        if isinstance(mt, int) and mt > cap:
            log.info("clamped %s %d -> %d for model=%s", field, mt, cap, model)
            data[field] = cap


def normalize_roles(messages: list) -> list:
    """Coerce OpenAI's newer `developer` role to `system`.

    Cohere returns HTTP 422 'unrecognized role developer' and mistral 422s
    similarly. The `developer` role is an OpenAI convention some clients
    (Codex CLI etc.) emit to mark agent-controller instructions distinct
    from user-installed system prompts. For our routing we treat them as
    the same. Without this normalization every cohere/mistral request
    carrying a developer message wastes a cascade slot.
    """
    out = []
    rewrote = 0
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "developer":
            m = {**m, "role": "system"}
            rewrote += 1
        out.append(m)
    if rewrote:
        log.info("normalized %d developer→system role(s)", rewrote)
    return out


def drop_orphan_tool_results(messages: list) -> list:
    """Drop role:tool messages that have no preceding assistant tool_calls.

    Some clients occasionally construct message history with a bare role:tool
    entry as the only or trailing message — usually when a helper invocation is
    captured as a 'tool result' without the matching assistant call. Cerebras
    (and others) reject these as HTTP 400.
    Bifrost surfaces it as 'provider api error (status 400)'.

    Filter contract: a role:tool message is valid ONLY if a previous
    role:assistant message in the same history has tool_calls including
    the matching tool_call_id. Otherwise drop it. Saves the wasted RPM
    + eliminates a recurring noisy error bucket.
    """
    pending_ids: set[str] = set()
    out = []
    dropped = 0
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        if role == "assistant":
            for tc in (m.get("tool_calls") or []):
                tcid = tc.get("id")
                if tcid:
                    pending_ids.add(tcid)
            out.append(m)
        elif role == "tool":
            tcid = m.get("tool_call_id")
            if tcid and tcid in pending_ids:
                pending_ids.discard(tcid)
                out.append(m)
            else:
                dropped += 1
        else:
            out.append(m)
    if dropped:
        log.warning("dropped %d orphan tool message(s) from request", dropped)
    return out


def _short_id(old: str) -> str:
    return hashlib.sha1(old.encode()).hexdigest()[:9]


# Qwen-style tool calls embed in assistant content as XML:
#   <tool_call>
#     <function=name>
#     <parameter=key>value</parameter>
#   </tool_call>
# Downstream parsers only accept OpenAI tool_calls JSON. Normalize before returning.
_QWEN_TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
_QWEN_FUNC = re.compile(r"<function=([^>\s]+)>")
_QWEN_PARAM = re.compile(r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>", re.DOTALL)


def normalize_qwen_tool_calls(content: str):
    """Extract Qwen-style <tool_call> blocks. Return (cleaned_content, tool_calls_list).
    cleaned_content has tool_call blocks removed. Empty list if none found."""
    matches = _QWEN_TOOL_CALL.findall(content)
    if not matches:
        return content, []
    tool_calls = []
    for block in matches:
        fname_m = _QWEN_FUNC.search(block)
        if not fname_m:
            continue
        args = {}
        for pname, pval in _QWEN_PARAM.findall(block):
            args[pname.strip()] = pval.strip()
        tool_calls.append({
            "id": secrets.token_hex(5)[:9],
            "type": "function",
            "function": {"name": fname_m.group(1).strip(), "arguments": json.dumps(args)},
        })
    cleaned = _QWEN_TOOL_CALL.sub("", content).strip()
    return cleaned, tool_calls


# Kimi emits two known formats:
#   OLD (Cloudflare/Moonshot direct): <|tool_call_begin|>functions.NAME:IDX<|tool_call_argument_begin|>{json}<|tool_call_end|>
#   NEW (NVIDIA NIM/some hosts):     <|tool_call_begin|>HEX_ID<|tool_call_argument_begin|>{json}<|tool_call_end|>
# NEW format has NO function name in the control token — we infer from JSON arg keys against the
# request's tools[] schemas. Both regexes here; we try OLD first (more info), then NEW with inference.
_KIMI_TC_OLD = re.compile(
    r"<\|tool_call_begin\|>\s*functions\.([^:\s]+)\s*:\s*\d+\s*"
    r"<\|tool_call_argument_begin\|>(.*?)<\|tool_call_end\|>",
    re.DOTALL,
)
_KIMI_TC_NEW = re.compile(
    r"<\|tool_call_begin\|>\s*([A-Za-z0-9_-]+)\s*"
    r"<\|tool_call_argument_begin\|>(.*?)<\|tool_call_end\|>",
    re.DOTALL,
)
_KIMI_TC_SECTION = re.compile(
    r"<\|tool_calls_section_begin\|>.*?(?:<\|tool_calls_section_end\|>|\Z)",
    re.DOTALL,
)


def _infer_function_name(args_json: dict, request_tools: list) -> str | None:
    """Given parsed JSON arguments + the request's tools[] declarations,
    return the function name whose required-params set is a SUBSET of args_json's keys.
    Returns the best match, or None if ambiguous/no match.
    """
    if not isinstance(args_json, dict) or not request_tools:
        return None
    arg_keys = set(args_json.keys())
    candidates = []
    for t in request_tools:
        if not isinstance(t, dict):
            continue
        fn = (t.get("function") or {})
        name = fn.get("name")
        params = fn.get("parameters") or {}
        required = set(params.get("required") or [])
        properties = set((params.get("properties") or {}).keys())
        # match if: all required are present AND all arg_keys are in properties
        if required and required.issubset(arg_keys) and arg_keys.issubset(properties or arg_keys):
            candidates.append((name, len(required & arg_keys)))
    if not candidates:
        # fall back: prefer the tool whose property-set best overlaps with arg_keys
        for t in request_tools:
            if not isinstance(t, dict): continue
            fn = (t.get("function") or {})
            name = fn.get("name")
            properties = set(((fn.get("parameters") or {}).get("properties") or {}).keys())
            overlap = len(arg_keys & properties)
            if overlap and arg_keys.issubset(properties):
                candidates.append((name, overlap))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[1])
    return candidates[0][0]


def normalize_kimi_tool_calls(content: str, request_tools: list | None = None):
    """Kimi K2.x emits tool calls as chat-template control tokens in content.
    Lift them into a structured tool_calls array.

    OLD format (Cloudflare/Moonshot direct):
        <|tool_call_begin|>functions.NAME:IDX<|tool_call_argument_begin|>{json}<|tool_call_end|>
    NEW format (NVIDIA NIM/some hosts) — no function name in token:
        <|tool_call_begin|>HEX_ID<|tool_call_argument_begin|>{json}<|tool_call_end|>
        → infer function name from JSON keys vs request_tools schemas.

    Returns (cleaned_content, tool_calls_list).
    """
    if "<|tool_call_begin|>" not in content:
        return content, []
    tool_calls = []
    seen_spans = set()
    # Pass 1: OLD format (function name present)
    for m in _KIMI_TC_OLD.finditer(content):
        fname, args_raw = m.group(1), m.group(2)
        args = args_raw.strip()
        try:
            json.loads(args)
        except json.JSONDecodeError:
            continue
        seen_spans.add(m.span())
        tool_calls.append({
            "id": secrets.token_hex(5)[:9],
            "type": "function",
            "function": {"name": fname.strip(), "arguments": args},
        })
    # Pass 2: NEW format (hex id) — only matches if NOT already captured by OLD
    for m in _KIMI_TC_NEW.finditer(content):
        if m.span() in seen_spans:
            continue
        token_id, args_raw = m.group(1), m.group(2)
        # skip if OLD format prefix already consumed it (e.g. starts with "functions")
        if token_id.startswith("functions"):
            continue
        args = args_raw.strip()
        try:
            args_obj = json.loads(args)
        except json.JSONDecodeError:
            continue
        fname = _infer_function_name(args_obj, request_tools or [])
        if not fname:
            # Cannot infer — skip this block (don't fabricate a tool_call)
            log.warning("normalize_kimi_tool_calls: NEW format without function-name "
                        "and no inference match (token=%s, keys=%s) — dropping",
                        token_id[:12], list(args_obj.keys())[:5])
            continue
        tool_calls.append({
            "id": secrets.token_hex(5)[:9],
            "type": "function",
            "function": {"name": fname, "arguments": args},
        })
    cleaned = _KIMI_TC_SECTION.sub("", content).strip()
    return cleaned, tool_calls


def normalize_json_tool_call(content: str):
    """Llama-3 / smaller-model style: bare JSON object with `name` + `parameters`/
    `arguments` keys posted directly in content. Examples:
        {"name": "execute_code", "parameters": {"code": "print(2+2)"}}
        {"name": "search", "arguments": {"q": "..."}}
    Returns (cleaned_content, tool_calls_list)."""
    s = content.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return content, []
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return content, []
    if not isinstance(obj, dict) or "name" not in obj:
        return content, []
    args = obj.get("arguments") or obj.get("parameters") or {}
    if not isinstance(args, (dict, str)):
        return content, []
    if isinstance(args, dict):
        args_str = json.dumps(args)
    else:
        args_str = args
    return "", [{
        "id": secrets.token_hex(5)[:9],
        "type": "function",
        "function": {"name": str(obj["name"]), "arguments": args_str},
    }]


# Reasoning-model thinking trace can leak into content. Strip <think>...</think>
# blocks (deepseek, kimi, qwen reasoning all use this convention).
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def normalize_response(payload: dict, request_tools: list | None = None) -> dict:
    """Strip <think> leakage and lift Qwen/Kimi/JSON tool calls into tool_calls.

    request_tools: the original request's `tools[]` array — used by the Kimi
    normalizer to infer function names when the new control-token format
    omits them.
    """
    if not isinstance(payload, dict) or "choices" not in payload:
        return payload
    for ch in payload.get("choices", []):
        msg = ch.get("message")
        if not isinstance(msg, dict):
            continue
        # NOTE: do NOT strip reasoning_content / reasoning from RESPONSES.
        # Some clients use the presence of structured reasoning to take a
        # "thinking-only prefill" recovery path; wiping it can leave an empty
        # response and trigger brittle nudge-retry loops client-side. We only
        # strip reasoning from REQUEST messages (the strip_reasoning function
        # in the request path); the response is left to the client.
        # Strip <think>...</think> block from content (still safe — that's
        # in-band leakage, not the structured reasoning field).
        content = msg.get("content") or ""
        if "<think>" in content.lower():
            content = _THINK_BLOCK.sub("", content).strip()
            msg["content"] = content or None
        if msg.get("tool_calls"):
            continue
        if not content:
            continue
        if "<|tool_call_begin|>" in content:
            cleaned, tcs = normalize_kimi_tool_calls(content, request_tools)
        elif "<tool_call>" in content:
            cleaned, tcs = normalize_qwen_tool_calls(content)
        else:
            cleaned, tcs = normalize_json_tool_call(content)
        if tcs:
            msg["content"] = cleaned or None
            msg["tool_calls"] = tcs
            ch["finish_reason"] = "tool_calls"
            log.warning("normalized %d tool_call(s) from content", len(tcs))
    return payload


def rewrite_tool_ids(messages: list) -> list:
    out = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list):
            new_calls = []
            for tc in m["tool_calls"]:
                if isinstance(tc, dict) and isinstance(tc.get("id"), str) and len(tc["id"]) != 9:
                    tc = {**tc, "id": _short_id(tc["id"])}
                new_calls.append(tc)
            m = {**m, "tool_calls": new_calls}
        elif m.get("role") == "tool" and isinstance(m.get("tool_call_id"), str):
            if len(m["tool_call_id"]) != 9:
                m = {**m, "tool_call_id": _short_id(m["tool_call_id"])}
        out.append(m)
    return out


RETRY_REASONING_ONLY = os.environ.get("STRIP_SHIM_RETRY_REASONING_ONLY", "1") == "1"
# Model-name substrings whose responses need buffering (content-embedded
# tool-call formats and/or the reasoning-only freeze): the shim must see the
# full body to normalize/retry, so streaming requests to them are forced
# non-streaming upstream and converted back to SSE. Everything else streams
# through untouched — pool members are curated for native tool_calls, so real
# streaming is the norm and buffering the exception.
RISKY_MODELS = tuple(
    s.strip().lower()
    for s in os.environ.get("STRIP_SHIM_RISKY_MODELS", "kimi,qwen").split(",")
    if s.strip()
)
# Providers verified (by live probe) to parse their models' tool calls
# server-side and return proper OpenAI tool_calls — their kimi/qwen-family
# hosts don't need buffering. ionet + groq confirmed 2026-07-08.
NATIVE_TC_PROVIDERS = frozenset(
    s.strip().lower()
    for s in os.environ.get("STRIP_SHIM_NATIVE_TOOLCALL_PROVIDERS", "ionet,groq").split(",")
    if s.strip()
)


def _model_is_risky(model_id: str) -> bool:
    low = model_id.lower()
    if "/" in low and low.split("/", 1)[0] in NATIVE_TC_PROVIDERS:
        return False
    return any(r in low for r in RISKY_MODELS)

_pool_members_cache = {"mtime": None, "pools": {}}


def _pool_members(pool: str) -> list:
    """Direct provider/model members of a pool, from the rendered models.json
    (coire_pools tags). Cached on file mtime. Unknown pool → []."""
    try:
        mtime = os.stat(MODELS_JSON_PATH).st_mtime
    except OSError:
        return []
    if _pool_members_cache["mtime"] != mtime:
        pools: dict = {}
        doc = _load_models_doc()
        for m in (doc or {}).get("data", []):
            if "/" not in m.get("id", ""):
                continue
            for p in (m.get("coire_pools") or []):
                pools.setdefault(p, []).append(m["id"].lower())
        _pool_members_cache.update(mtime=mtime, pools=pools)
    return _pool_members_cache["pools"].get(pool, [])


def _has_image_content(messages: list) -> bool:
    for m in messages:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "image", "input_image"):
                    return True
    return False


def reroute_images_to_vision(data: dict) -> None:
    """Continuation safety: a conversation carrying images must stay on
    vision-capable models — text-pool members 400 on image parts. If a pool
    alias other than coire-vision receives image content and coire-vision is
    enabled in this install, rewrite the model in place."""
    model = data.get("model") or ""
    if "/" in model or model == "coire-vision":
        return  # direct pin (user's explicit choice) or already vision
    if not _has_image_content(data.get("messages") or []):
        return
    if _pool_members("coire-vision"):
        log.info("image content on pool %s — rerouted to coire-vision", model)
        data["model"] = "coire-vision"


def _needs_buffering(model: str) -> bool:
    """Should a streaming tools-request to `model` be buffered for inspection?

    Direct provider/model pins: buffer only if the model matches RISKY_MODELS.
    Pool aliases: buffer if any member matches — or if membership is unknown
    (no models.json), the conservative pre-risk-awareness behavior.
    """
    low = (model or "").lower()
    if "/" in low:
        return _model_is_risky(low)
    members = _pool_members(low)
    if not members:
        return True  # unknown pool/no models.json — stay conservative
    return any(_model_is_risky(m) for m in members)
RETRY_PARAM_REJECTION = os.environ.get("STRIP_SHIM_RETRY_PARAM_REJECTION", "1") == "1"
RETRY_NUDGE_MESSAGE = (
    "The previous response described what to do but did not emit a "
    "tool_call. If a tool is required to complete the task, emit the "
    "structured tool_call now. If no tool is needed, respond with the "
    "final answer text."
)


# Map of error-message substrings → param to strip from request on retry.
# When bifrost returns 400 with a message matching one of these patterns,
# the upstream model rejected the named param; strip and retry once. This
# preserves capability for models that DO accept the param (other targets
# in the pool keep the param) while keeping rejecting targets reachable.
_PARAM_REJECTION_PATTERNS = [
    ("reasoning_effort is not enabled", "reasoning_effort"),
    ("reasoning_effort not enabled", "reasoning_effort"),
    ("reasoning_effort is not supported", "reasoning_effort"),
    ("reasoning_effort not supported", "reasoning_effort"),
    ("`thinking` parameter is not supported", "thinking"),
    ("thinking parameter is not supported", "thinking"),
    ("thinking is not supported", "thinking"),
    ("thinking is not enabled", "thinking"),
    ("does not support reasoning", "reasoning_effort"),
]


# Provider/model substrings known to reject params silently (bifrost wraps
# their error as generic "provider API error (status 400)" so we can't
# pattern-match the body). Pre-strip + post-retry both use this.
_RE_REJECTERS = (
    "qwen-3-235b",
    "mistral-large", "mistral-medium", "mistral-small",
    "codestral",
    # NB: a research pass claimed cerebras/zai-glm-4.7 (coire-main PRIMARY) rejects
    # reasoning_effort except 'none' — but live testing 2026-05-29 showed it ACCEPTS
    # 'medium' fine (served the request, no 400/retry). So glm-4.7 is intentionally
    # NOT listed here. The post-retry path still recovers if any target 400s on it.
)
_THINKING_REJECTERS = (
    "command-a-03-2025",
    "command-r",
    "command-a-vision",  # cohere vision variants don't accept thinking
)


def _model_rejects(model_id: str, rejecters: tuple) -> bool:
    if not model_id:
        return False
    low = model_id.lower()
    return any(p in low for p in rejecters)


def _detect_param_rejection(status_code: int, body_bytes: bytes,
                            request_data: dict) -> str:
    """If response is a 400-style param rejection, return the param name
    that should be stripped. Otherwise return ''.

    Three detection paths:
    1. Specific error-message substring (works for cohere — error passes through).
    2. Generic "provider API error" + known-rejecter pattern in
       extra_fields.model_requested (covers cerebras — error obscured).
    3. None matched → ''."""
    if status_code != 400:
        return ""
    try:
        body = body_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
    blob = body.lower()
    # Path 1 — specific message
    for pat, param in _PARAM_REJECTION_PATTERNS:
        if pat.lower() in blob:
            return param
    # Path 2 — generic + known-rejecter target
    try:
        body_json = json.loads(body)
        ef = (body_json.get("error_details") or body_json).get("extra_fields", {})
        model_requested = ef.get("model_requested", "")
    except (json.JSONDecodeError, AttributeError, TypeError):
        model_requested = ""
    if model_requested:
        if request_data.get("reasoning_effort") and _model_rejects(model_requested, _RE_REJECTERS):
            return "reasoning_effort"
        if request_data.get("thinking") and _model_rejects(model_requested, _THINKING_REJECTERS):
            return "thinking"
    return ""


def pre_strip_unsupported_params(data: dict) -> None:
    """For direct-model calls (model="provider/model"), strip params we know the
    target rejects BEFORE forwarding. Pool calls (model="coire-main") don't match
    here — bifrost resolves the target, and the post-retry path recovers those.

    reasoning_effort is VALUE-AWARE: the known rejecters (qwen-3-235b, mistral-*,
    glm-4.7) accept 'high'/'none' but reject 'low'/'medium'/'minimal', so we only
    strip the low tiers and keep the capability when the client asked for high.
    """
    model = data.get("model", "") or ""
    if "/" not in model:
        return  # pool alias, leave alone
    re_val = data.get("reasoning_effort")
    if (isinstance(re_val, str) and re_val.lower() in ("low", "medium", "minimal")
            and _model_rejects(model, _RE_REJECTERS)):
        log.info("pre-stripping reasoning_effort=%s for %s (accepts only high/none)", re_val, model)
        del data["reasoning_effort"]
    if "thinking" in data and _model_rejects(model, _THINKING_REJECTERS):
        log.info("pre-stripping thinking for known-rejecter %s", model)
        del data["thinking"]


def _extract_reasoning_text(msg: dict) -> str:
    """Aggregate reasoning text from any of Kimi's known fields."""
    parts = []
    for field in ("reasoning_content", "reasoning"):
        v = msg.get(field)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    details = msg.get("reasoning_details")
    if isinstance(details, list):
        for d in details:
            if isinstance(d, dict):
                t = d.get("text") or d.get("content")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
    return "\n".join(parts)


def _is_reasoning_only_no_action(payload: dict, request_data: dict) -> bool:
    """Detect Kimi-style freeze: model narrates but emits no tool_call + no text.

    Pattern: request supplied tools, response has reasoning text (in any of
    reasoning/reasoning_content/reasoning_details), content empty/whitespace,
    tool_calls empty. finish_reason can be stop OR length (Kimi K2.6 often
    runs out of tokens mid-reasoning without ever firing a tool_call).
    """
    if not isinstance(payload, dict) or not isinstance(request_data, dict):
        return False
    if not request_data.get("tools"):
        return False
    choices = payload.get("choices") or []
    if not choices:
        return False
    choice = choices[0]
    # length = token cap hit mid-reasoning, never got to tool_call (Kimi K2.6)
    # stop/end_turn = normal termination without tool_call
    if choice.get("finish_reason") not in ("stop", "end_turn", "length", None):
        return False
    msg = choice.get("message") or {}
    tool_calls = msg.get("tool_calls") or []
    if tool_calls:
        return False
    content = (msg.get("content") or "").strip()
    reasoning = _extract_reasoning_text(msg)
    return bool(reasoning) and not content


def _payload_to_sse_chunks(payload: dict) -> bytes:
    """Convert a non-streaming chat completion payload into SSE chunks.

    Emits: one delta chunk carrying the full message (role + content +
    tool_calls + reasoning_content), then [DONE]. opencode + AI SDK accept
    this single-shot streaming shape.
    """
    choices = payload.get("choices") or []
    if not choices:
        chunk = {**payload, "object": "chat.completion.chunk", "choices": []}
        return f"data: {json.dumps(chunk)}\n\n".encode() + b"data: [DONE]\n\n"
    choice = choices[0]
    msg = choice.get("message") or {}
    delta = {"role": "assistant"}
    if msg.get("content"):
        delta["content"] = msg["content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = msg["tool_calls"]
    if msg.get("reasoning_content"):
        delta["reasoning_content"] = msg["reasoning_content"]
    if msg.get("reasoning"):
        delta["reasoning"] = msg["reasoning"]
    chunk_payload = {
        "id": payload.get("id", ""),
        "object": "chat.completion.chunk",
        "created": payload.get("created", int(time.time())),
        "model": payload.get("model", ""),
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": None,
        }],
    }
    final_chunk = {
        "id": payload.get("id", ""),
        "object": "chat.completion.chunk",
        "created": payload.get("created", int(time.time())),
        "model": payload.get("model", ""),
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": choice.get("finish_reason", "stop"),
        }],
    }
    return (
        f"data: {json.dumps(chunk_payload)}\n\n".encode() +
        f"data: {json.dumps(final_chunk)}\n\n".encode() +
        b"data: [DONE]\n\n"
    )


@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length")}

    is_stream = False
    data = None
    if body:
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            log.warning("malformed JSON body: %s", e)
            data = None
        if isinstance(data, dict) and "messages" in data:
            reroute_images_to_vision(data)
            clamp_max_tokens(data)
            data["messages"] = normalize_roles(data["messages"])
            data["messages"] = strip_reasoning(data["messages"])
            data["messages"] = drop_orphan_tool_results(data["messages"])
            data["messages"] = rewrite_tool_ids(data["messages"])
            # Guard: if filtering left no messages (orphan-tool-only payload),
            # bail with 400 here instead of forwarding empty to upstream —
            # cerebras / groq / others all 400 on empty messages, which
            # burns RPM + pollutes error logs. Surfaces the client bug.
            if not data["messages"]:
                log.warning("rejecting request to %s — messages empty after filtering "
                            "(likely orphan-tool-only history from %s)",
                            data.get("model", "?"),
                            request.headers.get("user-agent", "unknown"))
                return JSONResponse(
                    {"error": {
                        "message": "strip-shim: messages list empty after filtering "
                                   "(orphan tool-result with no preceding assistant "
                                   "tool_calls). Client must include the assistant "
                                   "message that invoked this tool.",
                        "type": "invalid_request_error",
                        "code": "empty_messages_after_filter",
                    }},
                    status_code=400,
                )
            # deepseek-v4-pro on NIM hangs without enable_thinking=true.
            # Scoped to that model only — kimi-k2 breaks when extras injected.
            model = data.get("model", "")
            if "deepseek-v4-pro" in model:
                ctk = data.setdefault("chat_template_kwargs", {})
                ctk.setdefault("enable_thinking", True)
                ctk.setdefault("thinking", True)
            # UNIVERSAL: strip stream_options whenever stream is not True.
            # nvidia-nim 400s with "Stream options can only be defined when
            # stream=True". some clients send it unconditionally; harmless to strip
            # when not streaming. (Earlier retry-eligible path was narrower.)
            if not data.get("stream"):
                data.pop("stream_options", None)
            # Pre-strip params known to be rejected by the direct target
            # (only fires for "provider/model" calls; pool aliases handled
            # by post-retry in _detect_param_rejection).
            pre_strip_unsupported_params(data)
            is_stream = bool(data.get("stream"))
            body = json.dumps(data).encode()
            headers["content-length"] = str(len(body))
        elif isinstance(data, dict):
            is_stream = bool(data.get("stream"))

    target = f"{BIFROST_URL}/v1/{path}"
    client: httpx.AsyncClient = request.app.state.client

    # Reasoning-only-no-action retry: on a tools request whose model may
    # narrate without emitting a tool_call (Kimi-style freeze) or embed tool
    # calls in content (Qwen control tokens), we must see the full response —
    # so streaming requests to RISKY models are forced non-streaming upstream,
    # inspected/normalized/nudge-retried, then converted back to SSE. Requests
    # to curated pools with no risky member stream straight through — real
    # streaming is the norm. See project_kimi_reasoning_only_freeze memory.
    tools_request = (
        RETRY_REASONING_ONLY
        and path.endswith("chat/completions")
        and isinstance(data, dict)
        and bool(data.get("tools"))
    )
    force_buffer = tools_request and is_stream and _needs_buffering(data.get("model") or "")
    # The nudge-retry can run whenever we hold the full response: any
    # non-streaming tools request, plus the force-buffered streaming ones.
    retry_eligible = tools_request and (not is_stream or force_buffer)

    if is_stream and not force_buffer:
        async def gen():
            async with client.stream(
                request.method, target, content=body, headers=headers,
                params=dict(request.query_params),
            ) as r:
                async for chunk in r.aiter_raw():
                    yield chunk
        return StreamingResponse(gen(), media_type="text/event-stream")

    # Either: client did not request streaming, OR we are forcing
    # non-streaming so we can inspect for the retry pattern.
    upstream_data = data
    upstream_body = body
    if force_buffer:
        upstream_data = {**data, "stream": False}
        # nvidia-nim (and others) 400 with "Stream options can only be
        # defined when stream is True" if stream_options stays in body
        # while stream=False. Strip it when we force non-streaming.
        upstream_data.pop("stream_options", None)
        upstream_body = json.dumps(upstream_data).encode()
        headers["content-length"] = str(len(upstream_body))

    r = await client.request(
        request.method, target, content=upstream_body, headers=headers,
        params=dict(request.query_params),
    )

    out_content = r.content
    out_ct = r.headers.get("content-type", "")
    payload = None
    if r.status_code == 200 and "application/json" in out_ct and path.endswith("chat/completions"):
        try:
            payload = json.loads(r.content)
            normalize_response(payload, (upstream_data.get("tools") if isinstance(upstream_data, dict) else None))
        except (json.JSONDecodeError, TypeError):
            payload = None

    # NVIDIA-Kimi-K2.6 "unhashable type: 'dict'" 500 retry: known NVIDIA
    # NIM bug (forums report this on parallel_tool_calls + large tools
    # array). Non-deterministic — same request often succeeds on retry.
    # Strip parallel_tool_calls if set + retry once.
    if (
        RETRY_PARAM_REJECTION
        and path.endswith("chat/completions")
        and r.status_code in (500, 502, 503)
        and isinstance(upstream_data, dict)
    ):
        try:
            body_text = r.content.decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        if "unhashable type" in body_text.lower():
            log.warning(
                "nvidia 'unhashable type' 500 detected (model=%s) — retrying",
                upstream_data.get("model", "?"),
            )
            retry_data = dict(upstream_data)
            # Strip parallel_tool_calls — per NVIDIA forum it's a known
            # trigger for the bug. Keep tools (capability preserved).
            retry_data.pop("parallel_tool_calls", None)
            retry_body = json.dumps(retry_data).encode()
            retry_headers = {**headers, "content-length": str(len(retry_body))}
            r4 = await client.request(
                request.method, target, content=retry_body, headers=retry_headers,
                params=dict(request.query_params),
            )
            if r4.status_code == 200 and "application/json" in r4.headers.get("content-type", ""):
                try:
                    payload = json.loads(r4.content)
                    normalize_response(payload, (upstream_data.get("tools") if isinstance(upstream_data, dict) else None))
                    r = r4
                    upstream_data = retry_data
                except (json.JSONDecodeError, TypeError):
                    pass

    # Param-rejection retry: if bifrost returned 400 with a known
    # param-rejection message (e.g. cerebras/qwen-3-235b rejects
    # reasoning_effort, cohere/command-a-03-2025 rejects thinking),
    # strip the offending param and retry ONCE. Capability is preserved
    # for targets that accept the param — only the rejecting target
    # loses depth on retry. Logged as "param-rejection ... stripping X".
    if (
        RETRY_PARAM_REJECTION
        and path.endswith("chat/completions")
        and r.status_code == 400
        and isinstance(upstream_data, dict)
    ):
        param = _detect_param_rejection(r.status_code, r.content, upstream_data)
        if param and param in upstream_data:
            log.warning(
                "param-rejection detected (model=%s) — stripping %s and retrying",
                upstream_data.get("model", "?"), param,
            )
            retry_data = {k: v for k, v in upstream_data.items() if k != param}
            retry_body = json.dumps(retry_data).encode()
            retry_headers = {**headers, "content-length": str(len(retry_body))}
            r3 = await client.request(
                request.method, target, content=retry_body, headers=retry_headers,
                params=dict(request.query_params),
            )
            if r3.status_code == 200 and "application/json" in r3.headers.get("content-type", ""):
                try:
                    payload = json.loads(r3.content)
                    normalize_response(payload, (upstream_data.get("tools") if isinstance(upstream_data, dict) else None))
                    r = r3
                    upstream_data = retry_data
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                # retry also failed; let original error propagate to client
                r = r3 if r3.status_code != 200 else r

    # Retry once if reasoning-only-no-action detected
    if (
        retry_eligible
        and payload is not None
        and _is_reasoning_only_no_action(payload, upstream_data)
    ):
        finish = (payload.get("choices") or [{}])[0].get("finish_reason")
        log.warning(
            "reasoning-only-no-action detected for model=%s finish=%s — retrying with nudge",
            upstream_data.get("model", "?"), finish,
        )
        retry_data = {
            **upstream_data,
            "messages": [
                *upstream_data["messages"],
                {"role": "user", "content": RETRY_NUDGE_MESSAGE},
            ],
        }
        # If the model hit the token cap mid-reasoning, bump max_tokens so
        # the retry has headroom to actually emit the tool_call.
        if finish == "length":
            cur = retry_data.get("max_tokens")
            if isinstance(cur, int) and cur < 8192:
                retry_data["max_tokens"] = min(8192, max(cur * 2, 1024))
        retry_body = json.dumps(retry_data).encode()
        retry_headers = {**headers, "content-length": str(len(retry_body))}
        r2 = await client.request(
            request.method, target, content=retry_body, headers=retry_headers,
            params=dict(request.query_params),
        )
        if r2.status_code == 200 and "application/json" in r2.headers.get("content-type", ""):
            try:
                payload = json.loads(r2.content)
                normalize_response(payload, (upstream_data.get("tools") if isinstance(upstream_data, dict) else None))
                r = r2
            except (json.JSONDecodeError, TypeError):
                pass

    if payload is not None:
        out_content = json.dumps(payload).encode()

    if force_buffer and payload is not None:
        # Client wanted streaming. Convert buffered payload to SSE.
        sse_body = _payload_to_sse_chunks(payload)
        return Response(
            content=sse_body,
            status_code=200,
            media_type="text/event-stream",
        )

    return Response(
        content=out_content,
        status_code=r.status_code,
        headers={k: v for k, v in r.headers.items()
                 if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")},
        media_type=out_ct or None,
    )


# ── Transparent passthrough for non-/v1 surfaces ──────────────────────────
# The shim normalizes only OpenAI /v1 traffic. Anthropic-format clients
# (Claude Code → :4001/anthropic) and admin/management calls (/api/*) must
# still reach bifrost UNCHANGED so the shim can be the single :4001 front
# door. No body inspection or tool-call normalization here — pure relay,
# streaming-aware. The earlier /api/tags, /api/v1/models, /stub/models GET
# stubs are registered first and still win for those exact paths.
async def _passthrough(prefix: str, path: str, request: Request):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length")}
    is_stream = False
    if body:
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                is_stream = bool(d.get("stream"))
        except json.JSONDecodeError:
            pass
    target = f"{BIFROST_URL}/{prefix}/{path}"
    client: httpx.AsyncClient = request.app.state.client
    if is_stream:
        async def gen():
            async with client.stream(
                request.method, target, content=body, headers=headers,
                params=dict(request.query_params),
            ) as r:
                async for chunk in r.aiter_raw():
                    yield chunk
        return StreamingResponse(gen(), media_type="text/event-stream")
    r = await client.request(
        request.method, target, content=body, headers=headers,
        params=dict(request.query_params),
    )
    return Response(
        content=r.content,
        status_code=r.status_code,
        headers={k: v for k, v in r.headers.items()
                 if k.lower() not in ("content-length", "transfer-encoding", "content-encoding")},
        media_type=r.headers.get("content-type") or None,
    )


@app.api_route("/anthropic/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_anthropic(path: str, request: Request):
    return await _passthrough("anthropic", path, request)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_api(path: str, request: Request):
    return await _passthrough("api", path, request)
