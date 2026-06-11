#!/usr/bin/env python3
from __future__ import annotations
import sys
# UTF-8 + line buffering: Windows consoles default to cp1252 and would crash
# on the box-drawing / emoji characters this proxy logs.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass
"""
TokenCost — proxy for Anthropic + OpenAI-compatible APIs
  ANTHROPIC_BASE_URL=http://localhost:8082          (Claude / Anthropic)
  OPENAI_BASE_URL=http://localhost:8082/openai      (OpenAI)
  GROQ_API_BASE=http://localhost:8082/groq          (Groq)
  <PROVIDER>_API_BASE=http://localhost:8082/<name>  (any OpenAI-compat)
Dashboard: http://localhost:8082/dashboard
"""

import json
import os
import re as _re
import time
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
import uvicorn
from optimizer import (
    optimize_request,
    dedup_check, dedup_cache_response
)

from db import calc_cost, init_db, save_request, get_stats, get_raw_logs, weekly_digest, DB_PATH
from projects import get_project_stats

PROXY_PORT    = 8082
ANTHROPIC_URL = "https://api.anthropic.com"
_DASH         = os.path.join(os.path.dirname(__file__), "dashboard.html")
_DIR          = os.path.dirname(os.path.abspath(__file__))

# ── Version / update check ────────────────────────────────────────────────────
import threading
import subprocess

def _read_local_version() -> str:
    try:
        return open(os.path.join(_DIR, "VERSION")).read().strip()
    except Exception:
        return "unknown"

_CURRENT_VERSION = _read_local_version()
_GITHUB_REPO     = "mr-beaver/tokencost"
_version_cache   = {"latest": None, "checked_at": 0.0}  # cache for 24h

def _fetch_latest_version() -> str | None:
    now = time.time()
    if _version_cache["latest"] and now - _version_cache["checked_at"] < 86400:
        return _version_cache["latest"]
    try:
        import urllib.request
        url = f"https://raw.githubusercontent.com/{_GITHUB_REPO}/main/VERSION"
        req = urllib.request.Request(url, headers={"User-Agent": "tokencost"})
        with urllib.request.urlopen(req, timeout=5) as r:
            ver = r.read().decode().strip()
        _version_cache["latest"] = ver
        _version_cache["checked_at"] = now
        return ver
    except Exception:
        return _version_cache.get("latest")

def _auto_update_on_startup():
    """Check and auto-apply git pull if newer version is available on startup."""
    try:
        latest = _fetch_latest_version()
        if latest and latest != _CURRENT_VERSION:
            print(f"  ⬆️  New version available: {_CURRENT_VERSION} → {latest}")
            print(f"  🔄 Auto-updating via git pull...")
            result = subprocess.run(
                ["git", "-C", _DIR, "pull", "--ff-only"],
                capture_output=True, timeout=30
            )
            if result.returncode == 0:
                new_ver = _read_local_version()
                print(f"  ✅ Updated to {new_ver}. Restart the proxy to use new code.")
            else:
                err = result.stderr.decode().strip()
                print(f"  ⚠️  Auto-update failed: {err}")
    except Exception as e:
        print(f"  ⚠️  Auto-update error: {e}")

def _bg_version_check():
    _fetch_latest_version()

def _update_in_background(result_holder: list):
    try:
        subprocess.run(["git", "-C", _DIR, "pull", "--ff-only"],
                       capture_output=True, timeout=30, check=True)
        ver = _read_local_version()
        result_holder.append({"ok": True, "version": ver})
    except subprocess.CalledProcessError as e:
        result_holder.append({"ok": False, "error": e.stderr.decode().strip()})
    except Exception as e:
        result_holder.append({"ok": False, "error": str(e)})

_update_status: dict = {}  # shared update state

# ── OpenAI-compatible provider upstreams ─────────────────────────────────────
PROVIDER_URLS: dict[str, str] = {
    "openai":      "https://api.openai.com",
    "groq":        "https://api.groq.com/openai",
    "mistral":     "https://api.mistral.ai",
    "deepseek":    "https://api.deepseek.com",
    "xai":         "https://api.x.ai",
    "perplexity":  "https://api.perplexity.ai",
    "cerebras":    "https://api.cerebras.ai",
    "together":    "https://api.together.xyz",
    "fireworks":   "https://api.fireworks.ai/inference",
    "cohere":      "https://api.cohere.ai/compatibility",
    "openrouter":  "https://openrouter.ai/api",
    "ollama":      os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
}

# ── Source detection ──────────────────────────────────────────────────────────
_UA_MAP = [
    # ── Claude / Anthropic tools ──────────────────────────────────────────────
    ("claude-vscode",             "vscode"),
    ("cursor",                    "cursor"),
    ("anthropic-cli",             "claude-cli"),
    ("claude-cli",                "claude-cli"),
    ("claude-code",               "claude-cli"),
    ("vscode",                    "vscode"),
    ("visual studio",             "vscode"),
    # ── LiteLLM — routes any provider ─────────────────────────────────────────
    ("litellm",                   "litellm"),
    # ── LangChain / LlamaIndex ────────────────────────────────────────────────
    ("langchain",                 "langchain"),
    ("llama-index",               "llama-index"),
    ("llama_index",               "llama-index"),
    ("llamaindex",                "llama-index"),
    # ── OpenAI SDK (python: "AsyncOpenAI/1.x", "SyncOpenAI/1.x", "openai/1.x") ─
    ("asyncopenai",               "openai-sdk"),
    ("syncopenai",                "openai-sdk"),
    ("openai-python",             "openai-sdk"),
    ("openai/",                   "openai-sdk"),
    # ── Anthropic SDK ─────────────────────────────────────────────────────────
    ("anthropic-python",          "anthropic-sdk"),
    ("anthropic/",                "anthropic-sdk"),
    # ── Groq SDK ("Groq/0.x.x") ──────────────────────────────────────────────
    ("groq-python",               "groq-sdk"),
    ("groq/",                     "groq-sdk"),
    # ── Google SDKs ───────────────────────────────────────────────────────────
    ("google-generativeai",       "google-sdk"),
    ("google-cloud-aiplatform",   "vertex-sdk"),
    ("google-cloud",              "google-sdk"),
    ("googleapiclient",           "google-sdk"),
    # ── Cohere SDK ("cohere-python-sdk/X") ────────────────────────────────────
    ("cohere-python-sdk",         "cohere-sdk"),
    ("cohere/",                   "cohere-sdk"),
    # ── Mistral SDK ("mistralai/X") ───────────────────────────────────────────
    ("mistralai",                 "mistral-sdk"),
    ("mistral/",                  "mistral-sdk"),
    # ── Together AI ───────────────────────────────────────────────────────────
    ("together-python",           "together-sdk"),
    ("together/",                 "together-sdk"),
    # ── Fireworks AI ──────────────────────────────────────────────────────────
    ("fireworks-python",          "fireworks-sdk"),
    ("fireworks-ai",              "fireworks-sdk"),
    # ── DeepSeek SDK ──────────────────────────────────────────────────────────
    ("deepseek",                  "deepseek-sdk"),
    # ── xAI SDK ───────────────────────────────────────────────────────────────
    ("xai-sdk",                   "xai-sdk"),
    ("xai/",                      "xai-sdk"),
    # ── Perplexity ────────────────────────────────────────────────────────────
    ("perplexity",                "perplexity-sdk"),
    # ── Cerebras SDK ("cerebras-cloud-sdk/X") ────────────────────────────────
    ("cerebras-cloud-sdk",        "cerebras-sdk"),
    ("cerebras",                  "cerebras-sdk"),
    # ── AWS Bedrock (boto3/botocore) ──────────────────────────────────────────
    ("botocore",                  "boto3-bedrock"),
    ("boto3",                     "boto3-bedrock"),
    # ── OpenRouter ────────────────────────────────────────────────────────────
    ("openrouter",                "openrouter"),
    # ── Replicate ─────────────────────────────────────────────────────────────
    ("replicate",                 "replicate"),
    # ── HuggingFace ───────────────────────────────────────────────────────────
    ("huggingface-hub",           "huggingface"),
    ("huggingface",               "huggingface"),
    # ── OpenClaw ──────────────────────────────────────────────────────────────
    ("openclaw",                  "openclaw"),
    ("undici",                    "openclaw"),
    # ── Language runtimes (fallback) ──────────────────────────────────────────
    ("python",                    "python-sdk"),
    ("node",                      "node-sdk"),
    ("axios",                     "node-sdk"),
    ("deno",                      "node-sdk"),
    ("go-http",                   "go-sdk"),
    ("ruby",                      "ruby-sdk"),
    ("rust",                      "rust-sdk"),
    ("java",                      "java-sdk"),
    ("curl",                      "curl"),
]


def detect_source(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    u  = ua.lower()
    for pattern, label in _UA_MAP:
        if pattern in u:
            return label
    return ua[:60] or "unknown"


def detect_effort(body_bytes: bytes) -> str:
    try:
        body     = json.loads(body_bytes)
        thinking = body.get("thinking") or {}
        t_type   = thinking.get("type", "")
        if t_type == "disabled" or not t_type:
            return "standard"
        budget = thinking.get("budget_tokens") or 0
        if budget <= 1500:  return "low"
        if budget <= 5000:  return "medium"
        if budget <= 12000: return "high"
        return "xhigh"
    except Exception:
        return "standard"


# ── Smart model routing ───────────────────────────────────────────────────────
# Enable by writing "1" to .smart_routing file (via onbording.sh) or SMART_ROUTING=1 env var.
# Checked dynamically on every request — no proxy restart needed to toggle.
_SMART_FILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smart_routing")
ROUTE_CHEAP   = os.getenv("ROUTE_CHEAP", "claude-haiku-4-5-20251001")
ROUTE_MID     = os.getenv("ROUTE_MID",   "claude-sonnet-4-6")

def _smart_routing_enabled() -> bool:
    try:
        return open(_SMART_FILE).read().strip() == "1"
    except OSError:
        return os.getenv("SMART_ROUTING", "0") not in ("0", "false", "off", "")


_INJECTED_TAGS = _re.compile(
    r"<(ide_selection|system-reminder|transcript|antml:function_calls)[^>]*>.*?</\1>|"
    r"\[Image:[^\]]*\]",
    _re.DOTALL | _re.IGNORECASE,
)


def _last_user_text(messages: list) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            )
        else:
            continue
        # Strip injected context blocks added by VS Code / Claude Code harness
        text = _INJECTED_TAGS.sub("", text)
        text = " ".join(text.split())
        if text:
            return text
    return ""


def _count_tool_turns(messages: list) -> int:
    count = 0
    for msg in messages:
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                count += 1
                break
    return count


import re as _re

# ── Keyword lists for content-based complexity scoring ────────────────────────

# Action verbs that imply a non-trivial coding/architecture task
_COMPLEX_KW = {
    "implement",
    "create",
    "build",
    "refactor",
    "migrate",
    "integrate",
    "optimize", "optimise",
    "architect",
    "design",
    "redesign",
    "rewrite",
    "fix",
    "debug", "debugg",
    "analyze", "analyse",
    "review", "code review",
    "add feature",
    "add support",
    "add a",
    "extend",
    "update",
    "modify",
    "authentication",
    "authorization", "permissions",
    "database schema",
    "api design", "rest api", "graphql",
    "microservice",
    "deploy",
    "infrastructure",
    "security",
    "performance",
    "concurrency",
    "algorithm",
    "write", "generate code", "generate a",
}

# Interrogative / conversational patterns → simple
_SIMPLE_RE = _re.compile(
    r"^(what (is|are|does|do|was)\b"
    r"|how (does|do|can|to)\b"
    r"|(can you )?(explain|describe|tell me|show me|list|summarize|translate)\b"
    r"|why (is|are|does|do)\b"
    r"|where (is|are|can)\b"
    r"|is (it|there|this|that)\b"
    r"|does (it|this|that)\b"
    r"|what('s| is) (the )?(difference|meaning|purpose)\b)",
    _re.IGNORECASE,
)


def _keyword_score(text: str) -> int:
    t = text.lower()
    for kw in _COMPLEX_KW:
        if kw in t:
            return 3
    return 0


def _code_pattern_score(text: str) -> int:
    score = 0
    if "```" in text:
        score += 3
    # file extensions — strong coding signal
    if _re.search(r"\b\w+\.(py|ts|js|tsx|jsx|go|rs|java|cpp|c|rb|sh|yaml|yml|json|sql|md)\b", text):
        score += 3
    # code constructs
    if _re.search(r"\b(def |class |function |const |let |var |async |await |import |export )", text):
        score += 2
    # file paths  /src/foo  ./bar
    if _re.search(r"[/\\]\w[\w/\\.-]{3,}", text):
        score += 1
    return min(score, 5)


def complexity_score(body: dict) -> int:
    """
    Returns 0-10. 0-2 = simple → Haiku, 3-5 = medium → Sonnet, 6+ = complex → keep.

    Scoring is based on the current user message content only.
    Active tool-calling steps (tool_result in last 2 messages) get a small bump
    to avoid switching models mid-chain, but short conversational messages
    can still be routed to Haiku even inside an ongoing session.
    """
    if (body.get("thinking") or {}).get("type") == "enabled":
        return 10

    messages  = body.get("messages", [])
    last_text = _last_user_text(messages)

    # Active tool-calling step: last message is a tool_result block (no user text)
    # These are intermediate chain steps — keep original model.
    if not last_text:
        return 10

    # Trivially simple: short conversational opener
    if _SIMPLE_RE.match(last_text.strip()) and len(last_text) < 120:
        return 0

    score = 0

    # Length signal (weak)
    if len(last_text) > 500:   score += 2
    elif len(last_text) > 200: score += 1

    # Content signals (strong)
    score += _keyword_score(last_text)
    score += _code_pattern_score(last_text)

    # Bump if actively in the middle of a tool chain (last 4 messages have tool calls)
    recent = messages[-4:] if len(messages) >= 4 else messages
    if _count_tool_turns(recent) > 0:
        score += 2

    return min(score, 10)


def _tool_result_summary(blocks: list, max_chars: int = 400) -> str:
    """Build a human-readable summary from tool_result blocks in a user message."""
    parts = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "tool_result":
            continue
        content = b.get("content", "")
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            text = " ".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ).strip()
        else:
            continue
        text = _INJECTED_TAGS.sub("", text)
        text = " ".join(text.split())
        if text:
            parts.append(text[:200])
    return (" | ".join(parts))[:max_chars]


def extract_prompt_preview(body_bytes: bytes, max_chars: int = 800) -> str:
    """Extract last user message text from request body for display in RAW logs.

    For tool_result turns (Claude reading back tool output), extracts the tool
    result content so the log shows what the tool returned rather than being blank.
    Falls back to scanning earlier messages for the original user text.
    """
    try:
        body = json.loads(body_bytes)
        messages = body.get("messages", [])
        # Walk backwards: find last user message with actual text or tool_result content
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                text = _INJECTED_TAGS.sub("", content)
                text = " ".join(text.split())
                if text:
                    return text[:max_chars]
            elif isinstance(content, list):
                # First try plain text blocks
                text = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                text = _INJECTED_TAGS.sub("", text)
                text = " ".join(text.split())
                if text:
                    return text[:max_chars]
                # Fall back to tool_result blocks — show what the tool returned
                summary = _tool_result_summary(content, max_chars)
                if summary:
                    return summary
    except Exception:
        pass
    return ""


def route_model(body_bytes: bytes) -> tuple[str | None, str | None, int]:
    """
    Returns (original_model, routed_model, score).
    routed_model is None if no change needed or routing disabled.

    Routing rules:
    - Score 0-2: any model → Haiku (except Haiku stays Haiku)
    - Score 3-5: Opus/Fable → Sonnet (Sonnet/Haiku unchanged)
    - Score 6-10: no routing
    """
    if not _smart_routing_enabled():
        return None, None, 0
    try:
        body  = json.loads(body_bytes)
    except Exception:
        return None, None, 0

    original = body.get("model", "")
    low = original.lower()

    if not any(m in low for m in ("claude", "fable", "opus", "sonnet", "haiku")):
        return original, None, 0
    if "haiku" in low:
        return original, None, 0  # already cheapest

    score = complexity_score(body)

    if score <= 2:
        return original, ROUTE_CHEAP, score
    if score <= 5 and any(m in low for m in ("opus", "fable")):
        return original, ROUTE_MID, score

    return original, None, score


def _parse_anthropic(content: bytes, content_type: str):
    """Parse Anthropic SSE or JSON response → (model, input, output, cr, cw, stop, tools, tool_names, msg_id)."""
    input_tok = output_tok = cache_read = cache_creation = tool_count = 0
    tool_names: list = []
    model = "unknown"
    stop_reason = None
    msg_id = None
    try:
        if "text/event-stream" in content_type:
            for line in content.decode("utf-8", errors="replace").splitlines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                t = ev.get("type")
                if t == "message_start":
                    msg = ev.get("message", {})
                    if model == "unknown":
                        model = msg.get("model", "unknown")
                    if not msg_id:
                        msg_id = msg.get("id")
                    u = msg.get("usage", {})
                    input_tok     += u.get("input_tokens", 0)
                    cache_read     = u.get("cache_read_input_tokens", 0)
                    cache_creation = u.get("cache_creation_input_tokens", 0)
                elif t == "message_delta":
                    d = ev.get("delta", {})
                    if d.get("stop_reason"):
                        stop_reason = d["stop_reason"]
                    u = ev.get("usage", {})
                    if u.get("output_tokens"):
                        output_tok = u["output_tokens"]
                elif t == "content_block_start":
                    cb = ev.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        tool_count += 1
                        if cb.get("name"):
                            tool_names.append(cb["name"])
        else:
            data           = json.loads(content)
            model          = data.get("model", "unknown")
            msg_id         = data.get("id")
            stop_reason    = data.get("stop_reason")
            u              = data.get("usage", {})
            input_tok      = u.get("input_tokens", 0)
            output_tok     = u.get("output_tokens", 0)
            cache_read     = u.get("cache_read_input_tokens", 0)
            cache_creation = u.get("cache_creation_input_tokens", 0)
            tool_count     = sum(1 for b in data.get("content", []) if b.get("type") == "tool_use")
    except Exception:
        pass
    return model, input_tok, output_tok, cache_read, cache_creation, stop_reason, tool_count, tool_names, msg_id


def _parse_openai(content: bytes, content_type: str, req_model: str):
    """Parse OpenAI-compatible SSE or JSON response."""
    input_tok = output_tok = tool_count = 0
    model = req_model or "unknown"
    stop_reason = None
    try:
        if "text/event-stream" in content_type:
            for line in content.decode("utf-8", errors="replace").splitlines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if ev.get("model") and model == req_model:
                    model = ev["model"]
                # Usage in the last streaming chunk
                u = ev.get("usage") or {}
                if u.get("prompt_tokens"):
                    input_tok  = u["prompt_tokens"]
                if u.get("completion_tokens"):
                    output_tok = u["completion_tokens"]
                for ch in (ev.get("choices") or []):
                    if ch.get("finish_reason"):
                        stop_reason = ch["finish_reason"]
        else:
            data        = json.loads(content)
            model       = data.get("model", model)
            u           = data.get("usage", {})
            input_tok   = u.get("prompt_tokens", 0)
            output_tok  = u.get("completion_tokens", 0)
            tool_count  = sum(
                1 for ch in data.get("choices", [])
                if (ch.get("message") or {}).get("tool_calls")
            )
            for ch in data.get("choices", []):
                if ch.get("finish_reason"):
                    stop_reason = ch["finish_reason"]
    except Exception:
        pass
    return model, input_tok, output_tok, 0, 0, stop_reason, tool_count, []


def _record(source, model, input_tok, output_tok, cr, cw,
            duration_ms, status, ua, stop_reason, tool_count, tool_names,
            effort="standard", prompt_preview="", msg_uuid=None, auto_thinking=False,
            optimizations_json=None, optimizer_savings_usd=0):
    cost = calc_cost(model, input_tok, output_tok, cr, cw)
    save_request(source, model, input_tok, output_tok, cr, cw,
                 cost, duration_ms, status, ua, stop_reason, tool_count,
                 json.dumps(tool_names) if tool_names else None, effort,
                 prompt_preview, msg_uuid, auto_thinking, optimizations_json, optimizer_savings_usd)
    print(f"  [{source}] {model} | in={input_tok} cr={cr} cw={cw} out={output_tok} | ${cost:.5f} | {duration_ms}ms")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    weekly_digest()
    # Auto-update on startup if newer version available
    threading.Thread(target=_auto_update_on_startup, daemon=True).start()
    print(f"\n🚀  TokenCost Proxy  →  http://localhost:{PROXY_PORT}  (v{_CURRENT_VERSION})")
    print(f"📊  Dashboard  →  http://localhost:{PROXY_PORT}/dashboard")
    providers = ", ".join(PROVIDER_URLS)
    print(f"🔌  Providers  →  anthropic  +  {providers}\n")
    threading.Thread(target=_bg_version_check, daemon=True).start()
    yield


app = FastAPI(lifespan=lifespan)


# ── Anthropic proxy (/v1/*) ───────────────────────────────────────────────────

@app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_anthropic(path: str, request: Request):
    body_bytes = await request.body()
    skip = {"host", "content-length", "accept-encoding", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}

    # ── Deduplication: check if identical request came <5s ago ──────────────────
    now = time.time()
    cached_resp, req_hash = dedup_check(body_bytes, now)
    if cached_resp:
        print(f"  [dedup]   returning cached response (saved API call)")
        return Response(content=cached_resp, status_code=200,
                      headers={"content-type": "application/json"})

    t0      = time.time()
    source  = detect_source(request)
    effort  = detect_effort(body_bytes)
    ua      = request.headers.get("user-agent", "")
    preview = extract_prompt_preview(body_bytes)

    orig_model, routed, score = route_model(body_bytes)
    try:
        body_data = json.loads(body_bytes)
        if routed:
            body_data["model"] = routed
            print(f"  [routing] {orig_model} → {routed} (score={score})")
            optimizations = [("routing", f"{orig_model} → {routed} (score={score})")]
        else:
            optimizations = []
        # strip effort/thinking/betas — effort causes 400 on current API version
        for key in ("effort", "thinking", "betas"):
            body_data.pop(key, None)
        # effort may also live inside output_config
        if "output_config" in body_data:
            body_data["output_config"].pop("effort", None)
            if not body_data["output_config"]:
                body_data.pop("output_config")

        # ── Cost optimizations ─────────────────────────────────────────────────
        body_data, cost_optimizations = optimize_request(body_data)
        optimizations = optimizations + cost_optimizations
        auto_thinking = any(tag.strip() == "think" for tag, _ in optimizations)
        for tag, msg in optimizations:
            print(f"  [{tag:6}] {msg}")

        # Re-detect effort after optimizer may have injected thinking
        effort = detect_effort(json.dumps(body_data).encode())

        body_bytes = json.dumps(body_data).encode()
    except Exception as e:
        print(f"  [error ] {e}")
        optimizations = []
        pass

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.request(
            method=request.method,
            url=f"{ANTHROPIC_URL}/v1/{path}",
            headers=headers,
            content=body_bytes,
        )

    duration_ms  = int((time.time() - t0) * 1000)
    content_type = resp.headers.get("content-type", "")
    model, inp, out, cr, cw, stop, tools, tool_names, msg_id = _parse_anthropic(
        resp.content, content_type)

    # Calculate optimizer savings
    from optimizer import calculate_optimization_savings
    opt_json, opt_savings = calculate_optimization_savings(optimizations, model, inp, out, cr)

    _record(source, model, inp, out, cr, cw, duration_ms,
            resp.status_code, ua, stop, tools, tool_names, effort, preview, msg_id, auto_thinking,
            opt_json, opt_savings)

    # ── Cache successful response for deduplication ──────────────────────────────
    if resp.status_code == 200:
        dedup_cache_response(req_hash, resp.content, now)

    skip_resp = {"content-encoding", "content-length", "transfer-encoding"}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in skip_resp},
        media_type=resp.headers.get("content-type"),
    )


# ── Transparent passthrough for Anthropic /api/oauth/* ────────────────────────
# Not logged — these are subscription-usage polls (e.g. /api/oauth/usage), not
# billable LLM calls. Needed so TokenCost can sit chained behind another proxy
# (e.g. headroom) that forwards ALL Anthropic traffic here, not just /v1/*.
@app.api_route("/api/oauth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_anthropic_oauth(path: str, request: Request):
    body_bytes = await request.body()
    skip = {"host", "content-length", "accept-encoding", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    url = f"{ANTHROPIC_URL}/api/oauth/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method=request.method, url=url, headers=headers, content=body_bytes,
        )
    skip_resp = {"content-encoding", "content-length", "transfer-encoding"}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in skip_resp},
        media_type=resp.headers.get("content-type"),
    )


# ── OpenAI-compatible proxy (/<provider>/v1/*) ────────────────────────────────

@app.api_route("/{provider}/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy_openai_compat(provider: str, path: str, request: Request):
    if provider not in PROVIDER_URLS:
        return Response(
            content=json.dumps({"error": f"Unknown provider '{provider}'. "
                                         f"Known: {list(PROVIDER_URLS)}"}),
            status_code=404, media_type="application/json")

    body_bytes = await request.body()
    skip = {"host", "content-length", "accept-encoding", "transfer-encoding"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}

    # Extract requested model name for fallback
    req_model = provider
    try:
        req_model = json.loads(body_bytes).get("model", provider)
    except Exception:
        pass

    t0      = time.time()
    source  = detect_source(request)
    ua      = request.headers.get("user-agent", "")
    preview = extract_prompt_preview(body_bytes)
    if source in ("python-sdk", "unknown"):
        source = provider  # label by provider when UA is generic

    upstream = PROVIDER_URLS[provider]
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.request(
            method=request.method,
            url=f"{upstream}/v1/{path}",
            headers=headers,
            content=body_bytes,
        )

    duration_ms  = int((time.time() - t0) * 1000)
    content_type = resp.headers.get("content-type", "")
    model, inp, out, cr, cw, stop, tools, tool_names = _parse_openai(
        resp.content, content_type, req_model)

    # Tag model with provider prefix if bare name
    if "/" not in model:
        model = f"{provider}/{model}"

    _record(source, model, inp, out, cr, cw, duration_ms,
            resp.status_code, ua, stop, tools, tool_names, prompt_preview=preview)

    skip_resp = {"content-encoding", "content-length", "transfer-encoding"}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in skip_resp},
        media_type=resp.headers.get("content-type"),
    )


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/raw-logs")
def raw_logs(limit: int = 500):
    return get_raw_logs(min(limit, 500))


@app.get("/stats")
def stats(period: str = "7d"):
    return get_stats(period)


@app.get("/optimizer-stats")
def optimizer_stats(period: str = "7d"):
    from db import get_optimizer_stats
    return get_optimizer_stats(period)


@app.get("/projects")
def projects(period: str = "7d"):
    return get_project_stats(period)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(
        content=open(_DASH, encoding="utf-8").read(),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/img/{path:path}")
def serve_img(path: str):
    import mimetypes
    from fastapi.responses import FileResponse
    img_path = os.path.join(_DIR, "img", path)
    if not os.path.exists(img_path):
        return {"error": "not found"}, 404
    mime, _ = mimetypes.guess_type(img_path)
    return FileResponse(img_path, media_type=mime or "application/octet-stream")


@app.get("/logs", response_class=HTMLResponse)
def logs_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>RAW Logs — TokenCost</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root{--bg:#f0f2f7;--surface:#ffffff;--border:#dde1ea;--accent:#16a34a;--accent2:#2563eb;--warn:#ea580c;--good:#16a34a;--text:#111827;--muted:#6b7280;--mono:'IBM Plex Mono',monospace;--sans:'IBM Plex Sans',sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;min-height:100vh}
header{padding:12px 28px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:16px;position:sticky;top:0;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.06);z-index:100}
.logo{font-family:var(--mono);font-size:12px;font-weight:600;letter-spacing:2px;text-transform:uppercase}
.logo span{color:var(--muted)}
.back{font-family:var(--mono);font-size:11px;color:var(--accent2);text-decoration:none;padding:5px 12px;border:1px solid var(--border);border-radius:6px;margin-left:auto;transition:all .15s}
.back:hover{border-color:var(--accent2)}
.live-dot{width:8px;height:8px;border-radius:50%;background:#16a34a;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 rgba(22,163,74,.4)}50%{opacity:.7;box-shadow:0 0 0 5px rgba(22,163,74,0)}}
main{padding:20px 28px}
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}
.search{font-family:var(--mono);font-size:12px;border:1px solid var(--border);border-radius:6px;padding:6px 12px;outline:none;flex:1;min-width:200px;max-width:400px;background:#fff;color:var(--text)}
.search:focus{border-color:var(--accent2)}
.filter-btn{font-family:var(--mono);font-size:11px;padding:5px 12px;border:1px solid var(--border);border-radius:6px;background:#fff;color:var(--muted);cursor:pointer;transition:all .15s}
.filter-btn:hover{border-color:var(--accent2);color:var(--accent2)}
.filter-btn.active{background:var(--accent2);border-color:var(--accent2);color:#fff}
.count{font-family:var(--mono);font-size:11px;color:var(--muted);margin-left:auto}
.card{background:#fff;border:1px solid var(--border);border-radius:8px;padding:0;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:13px;min-width:960px}
th{text-align:left;padding:9px 12px;color:#374151;font-weight:700;font-size:11px;letter-spacing:.5px;text-transform:uppercase;border-bottom:2px solid #c7d2e0;white-space:nowrap;background:#eef2f8}
td{padding:8px 12px;border-bottom:1px solid #edf0f5;vertical-align:middle}
tr:nth-child(even) td{background:#f7f9fc}
tr:hover td{background:#eef4ff!important}
.c-muted{color:var(--muted)}.c-ok{color:var(--good)}.c-good{color:var(--good)}.c-hi{color:var(--warn)}
.stop-end{color:var(--good);font-size:11px;font-weight:600}
.stop-tool{color:var(--accent2);font-size:11px;font-weight:600}
.stop-max{color:var(--warn);font-size:11px;font-weight:600}
.tag{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500;white-space:nowrap}
.m-haiku{color:#16a34a;font-weight:600}.m-sonnet{color:#2563eb;font-weight:600}.m-opus{color:#ea580c;font-weight:600}.m-other{color:var(--muted)}
.prompt-cell{max-width:420px;cursor:pointer}
.prompt-short{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:420px}
.prompt-cell:hover .prompt-short{color:var(--text)}
.prompt-full{display:none;font-size:11px;color:var(--text);white-space:pre-wrap;word-break:break-word;background:#f8fafc;border:1px solid var(--border);border-radius:4px;padding:8px;margin-top:4px;max-height:200px;overflow-y:auto;line-height:1.55}
.prompt-cell.expanded .prompt-full{display:block}
.prompt-cell.expanded .prompt-short{white-space:normal;overflow:visible;text-overflow:unset;font-weight:500;color:var(--text)}
.note{font-family:var(--mono);font-size:11px;color:var(--muted);text-align:right;padding:8px 0}
.empty{text-align:center;padding:40px;color:var(--muted);font-family:var(--mono);font-size:13px}
</style>
</head>
<body>
<header>
  <div class="logo">LLM <span>//</span> Cost Tracker</div>
  <div class="live-dot"></div>
  <a href="/dashboard" class="back">← Dashboard</a>
</header>
<main>
  <div class="toolbar">
    <input class="search" id="search" type="text" placeholder="Filter: prompt, model, source…" oninput="render()">
    <button class="filter-btn active" data-tier="all"    onclick="setTier(this)">All</button>
    <button class="filter-btn"        data-tier="haiku"  onclick="setTier(this)">Haiku</button>
    <button class="filter-btn"        data-tier="sonnet" onclick="setTier(this)">Sonnet</button>
    <button class="filter-btn"        data-tier="opus"   onclick="setTier(this)">Opus</button>
    <button class="filter-btn"        data-tier="other"  onclick="setTier(this)">Other</button>
    <span class="count" id="count"></span>
    <button class="filter-btn" onclick="load()" style="margin-left:4px">↺ Refresh</button>
  </div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th style="min-width:40px;text-align:right">#</th>
          <th style="min-width:130px">Time</th>
          <th style="min-width:90px">Source</th>
          <th style="min-width:130px">Model</th>
          <th style="min-width:60px;text-align:right">In</th>
          <th style="min-width:60px;text-align:right">Cache↑</th>
          <th style="min-width:60px;text-align:right">Out</th>
          <th style="min-width:45px;text-align:right">Tools</th>
          <th style="min-width:55px">Stop</th>
          <th style="min-width:80px;text-align:right">Cost $</th>
          <th style="min-width:55px;text-align:right">Lat</th>
          <th>Prompt</th>
        </tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>
  <div class="note" id="note"></div>
</main>
<script>
let _data = [], _tier = 'all';

const fmt      = n  => n == null ? '—' : Number(n).toLocaleString();
const fmtCost5 = c  => c == null || c === 0 ? '—' : '$' + Number(c).toFixed(5);
const fmtDate  = ts => { const d = new Date(ts); return d.toLocaleDateString('en',{day:'2-digit',month:'2-digit'}) + ' ' + d.toLocaleTimeString('en',{hour:'2-digit',minute:'2-digit',second:'2-digit'}); };
const fmtMs    = ms => { if (!ms) return '—'; const s=Math.floor(ms/1000); if(ms<1000)return ms+'ms'; if(s<60)return s+'s'; const m=Math.floor(s/60); return m+'m'+(s%60?String(s%60).padStart(2,'0')+'s':''); };
const esc      = s  => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');

function tier(model) {
  const m = (model||'').toLowerCase();
  if (m.includes('haiku'))  return 'haiku';
  if (m.includes('sonnet')) return 'sonnet';
  if (m.includes('opus'))   return 'opus';
  return 'other';
}
function modelCls(model) { return 'm-' + tier(model); }
function shortModel(m) { return (m||'').replace('claude-','').replace(/-20\\d{6}$/,''); }

function sourceTag(s) {
  const tags = {
    vscode:      ['VS Code',  '#eff6ff','#1d4ed8','#bfdbfe'],
    'claude-cli':['CLI',      '#f0fdf4','#15803d','#bbf7d0'],
    cursor:      ['Cursor',   '#faf5ff','#6d28d9','#ddd6fe'],
    openclaw:    ['OpenClaw', '#fefce8','#a16207','#fde68a'],
    'openclaw-history':['OpenClaw↑','#fefce8','#a16207','#fde68a'],
    'claude-cli-history':['CLI↑','#f0fdf4','#15803d','#bbf7d0'],
    'claude-desktop-history':['Desktop↑','#f0fdf4','#15803d','#bbf7d0'],
  };
  const t = tags[s];
  if (t) return `<span class="tag" style="background:${t[1]};color:${t[2]};border:1px solid ${t[3]}">${t[0]}</span>`;
  return `<span class="tag" style="background:#fff7ed;color:#c2410c;border:1px solid #fed7aa">${s||'?'}</span>`;
}

function stopBadge(row) {
  const r = row.stop_reason;
  if (!r) return '<span class="c-muted">—</span>';
  if (r==='end_turn')   return '<span class="stop-end">end</span>';
  if (r==='tool_use') {
    const tools = row.tools || [];
    const label = tools.length ? tools.slice(0,2).join('+') : 'tool';
    return `<span class="stop-tool" title="${tools.join(', ')}">${label}</span>`;
  }
  if (r==='max_tokens') return '<span class="stop-max">max!</span>';
  return `<span class="c-muted">${r}</span>`;
}

function render() {
  const q = (document.getElementById('search').value||'').toLowerCase();
  const rows = _data.filter(r => {
    if (_tier !== 'all' && tier(r.model) !== _tier) return false;
    if (q) {
      const h = ((r.prompt_preview||'')+' '+(r.model||'')+' '+(r.source||'')).toLowerCase();
      if (!h.includes(q)) return false;
    }
    return true;
  });
  document.getElementById('count').textContent = rows.length + ' / ' + _data.length + ' requests';
  const tbody = document.getElementById('tbody');
  if (!rows.length) { tbody.innerHTML = '<tr><td colspan="12" class="empty">No data</td></tr>'; return; }
  tbody.innerHTML = rows.map(r => {
    const preview = (r.prompt_preview||'').trim();
    const short   = preview.length > 120 ? preview.slice(0,120)+'…' : preview;
    const promptHtml = preview
      ? `<div class="prompt-cell" onclick="this.classList.toggle('expanded')"><div class="prompt-short">${esc(short)}</div><div class="prompt-full">${esc(preview)}</div></div>`
      : `<span style="color:#d1d5db;font-size:11px">—</span>`;
    return `<tr>
      <td style="text-align:right;color:var(--muted);font-size:11px">${r.id}</td>
      <td class="c-muted" style="white-space:nowrap;font-size:12px">${fmtDate(r.ts)}</td>
      <td>${sourceTag(r.source)}</td>
      <td class="${modelCls(r.model)}" style="white-space:nowrap">${shortModel(r.model)}</td>
      <td style="text-align:right" class="c-muted">${fmt(r.input_tokens)}</td>
      <td style="text-align:right" class="c-good">${r.cache_read_tokens ? fmt(r.cache_read_tokens) : '—'}</td>
      <td style="text-align:right" class="c-muted">${fmt(r.output_tokens)}</td>
      <td style="text-align:right" class="c-muted">${r.tool_call_count||'—'}</td>
      <td>${stopBadge(r)}</td>
      <td style="text-align:right" class="${r.cost_usd>0.01?'c-hi':'c-ok'}">${fmtCost5(r.cost_usd)}</td>
      <td style="text-align:right" class="c-muted">${fmtMs(r.duration_ms)}</td>
      <td>${promptHtml}</td>
    </tr>`;
  }).join('');
}

function setTier(btn) {
  _tier = btn.dataset.tier;
  document.querySelectorAll('.filter-btn[data-tier]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  render();
}

async function load() {
  document.getElementById('note').textContent = 'loading…';
  try {
    const data = await fetch('/raw-logs?limit=500').then(r => r.json());
    _data = Array.isArray(data) ? data : [];
    render();
    document.getElementById('note').textContent = 'updated ' + new Date().toLocaleTimeString() + ' · ' + _data.length + ' records · click prompt to expand';
  } catch(e) {
    document.getElementById('note').textContent = 'load error: ' + e.message;
  }
}

load();
setInterval(load, 10000);
</script>
</body>
</html>"""


@app.get("/sync-status")
def sync_status():
    import sqlite3 as _sq
    con = _sq.connect(DB_PATH)
    row = con.execute("SELECT value FROM settings WHERE key='last_import_ts'").fetchone()
    con.close()
    return {"last_import_ts": row[0] if row else None}


@app.post("/sync-now")
def sync_now():
    import subprocess, sys
    _import_script = os.path.join(os.path.dirname(__file__), "import_history.py")
    result = subprocess.run(
        [sys.executable, _import_script, "--silent"],
        capture_output=True, text=True, timeout=60
    )
    import sqlite3 as _sq
    con = _sq.connect(DB_PATH)
    row = con.execute("SELECT value FROM settings WHERE key='last_import_ts'").fetchone()
    con.close()
    return {
        "ok": result.returncode == 0,
        "output": result.stdout.strip() or result.stderr.strip(),
        "last_import_ts": row[0] if row else None,
    }


def _update_cmd() -> str:
    if sys.platform == "win32":
        # Wrapped in `powershell -Command` so it runs whether the user pastes it
        # into cmd.exe or PowerShell. onbording.ps1 -Update pulls + restarts
        # non-interactively (no menu prompt).
        inner = f"Set-Location '{_DIR}'; git pull; & '{_DIR}\\onbording.ps1' -Update"
        return f'powershell -NoProfile -ExecutionPolicy Bypass -Command "{inner}"'
    return f"cd {_DIR} && git pull && bash onbording.sh"


@app.get("/version")
def version_info():
    import json as _json
    _cache_file = os.path.join(_DIR, ".version_cache.json")
    # prefer file cache written by import_history.py daemon (updated every 24h)
    if os.path.exists(_cache_file):
        try:
            cached = _json.loads(open(_cache_file, encoding="utf-8").read())
            # refresh current version in case it changed on disk
            cached["current"] = _CURRENT_VERSION
            cached["up_to_date"] = cached.get("latest") == _CURRENT_VERSION
            # update_cmd must reflect the current OS, not whatever wrote the cache
            cached["update_cmd"] = _update_cmd()
            return cached
        except Exception:
            pass
    # fallback: fetch live (happens only before first daemon run)
    latest  = _fetch_latest_version()
    up2date = (latest is None) or (latest == _CURRENT_VERSION)
    update_cmd = _update_cmd()
    return {
        "current":    _CURRENT_VERSION,
        "latest":     latest,
        "up_to_date": up2date,
        "update_cmd": update_cmd,
    }

@app.post("/api/update")
def api_update():
    if _update_status.get("running"):
        return {"status": "running", "message": "Update already in progress"}
    _update_status.clear()
    _update_status["running"] = True
    result_holder: list = []

    def run():
        _update_in_background(result_holder)
        r = result_holder[0] if result_holder else {"ok": False, "error": "no result"}
        _update_status["running"] = False
        _update_status["result"]  = r
        if r.get("ok"):
            _version_cache["latest"]     = None
            _version_cache["checked_at"] = 0.0

    threading.Thread(target=run, daemon=True).start()
    return {"status": "started"}

@app.get("/api/update-status")
def api_update_status():
    return {
        "running": _update_status.get("running", False),
        "result":  _update_status.get("result"),
    }

@app.get("/")
def root():
    return {
        "status":    "ok",
        "dashboard": f"http://localhost:{PROXY_PORT}/dashboard",
        "providers": list(PROVIDER_URLS),
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PROXY_PORT, log_level="warning")
