from __future__ import annotations
"""
Request-level cost optimizations:
- Auto cache_control on large system prompts
- Deduplication of identical requests within 5-second window
"""

import hashlib
import json

# ── Request deduplication cache ────────────────────────────────────────────────
_dedup_cache: dict = {}  # hash → (response, timestamp, content_type)
_DEDUP_TTL_SEC = 5        # default: 5s for normal requests
_DEDUP_TTL_TOOL_SEC = 15  # extended: 15s for tool_result requests (content stable within a tool chain)

# ── Tool result cache (per-session, cleared on new session) ────────────────────
_tool_result_cache: dict = {}  # (tool_name, tool_input_hash) → (result_text, timestamp)
_TOOL_CACHE_TTL_SEC = 300  # 5 minutes within a session

# ── Per-session message tracking (detect new sessions, enforce max limit) ────
_last_message_count: int = 0
_message_count_threshold: int = 40  # force trim if exceeds this

# ── Session-aware cache tracking — prevents routing from busting prompt cache ──
#
# Problem: routing switches model mid-session (sonnet→haiku→sonnet).
# Each model switch invalidates Anthropic's prompt cache (cache is per-model).
# The 80k-token Claude Code system prompt costs ~$0.30 per cache-write, so a
# model switch can cost MORE than the routing saves.
#
# Fix: only allow routing on the first request of a session, or after the cache
# has expired (>5 min gap). Within an active session we preserve the model.
#
# Session key = SHA256 of the first 500 chars of the system prompt.
# This is stable within one Claude Code session, changes on /compact or new project.
#
# Per session we track:
#   model      — which model the cache was written for
#   last_ts    — timestamp of last request (to detect >5 min gaps)
#   cache_warm — True once we've seen cache_creation_tokens > 0 (exact signal)
#
# _routing_skipped_cache — counter for dashboard

_CACHE_TTL_SEC = 300  # 5 min = Anthropic prompt cache TTL

_session_state: dict = {}   # session_key → {model, last_ts, cache_warm}
_routing_skipped_cache: int = 0


def _session_key(body_bytes: bytes) -> str | None:
    """Derive a stable session key from the system prompt prefix."""
    try:
        body = json.loads(body_bytes)
        system = body.get("system", "")
        if isinstance(system, list):
            # system is array of content blocks
            text = " ".join(b.get("text", "") for b in system if isinstance(b, dict))
        else:
            text = system or ""
        if not text:
            return None
        return hashlib.sha256(text[:500].encode()).hexdigest()[:16]
    except Exception:
        return None


def record_cache_state(model: str, now: float,
                       body_bytes: bytes = b"", cache_write_tokens: int = 0):
    """Called after each response to update per-session cache state."""
    if not model or model == "unknown":
        return
    key = _session_key(body_bytes) if body_bytes else None
    if not key:
        return
    if len(_session_state) > 1000:
        # evict oldest entry by last_ts
        oldest = min(_session_state, key=lambda k: _session_state[k].get("last_ts", 0))
        del _session_state[oldest]
    prev = _session_state.get(key, {})
    _session_state[key] = {
        "model":      model,
        "last_ts":    now,
        # cache_warm: set when write seen; kept True; reset only on gap > TTL
        "cache_warm": prev.get("cache_warm", False) or (cache_write_tokens > 0),
    }


def routing_skipped_count() -> int:
    return _routing_skipped_cache


def should_skip_routing(requested_model: str, target_model: str,
                        now: float, body_bytes: bytes, score: int = 0) -> bool:
    """
    Returns True if routing should be skipped to preserve the prompt cache.

    Routing is ALLOWED when:
      1. No session state exists yet (first request — cache not written)
      2. Cache TTL has expired (> 5 min since last request to this session)
      3. score >= 6 AND session is on Haiku — quality matters more than cache
         (e.g. session started with a simple ping→Haiku, now a complex task arrives)

    Routing is BLOCKED when:
      - Session is active (< 5 min gap) AND cache is warm AND score < 6
      - Switching model would bust a cache that costs ~$0.30 to rebuild
    """
    global _routing_skipped_cache

    if requested_model == target_model:
        return False  # no switch, nothing to skip

    key = _session_key(body_bytes)
    if not key:
        return False  # can't derive session → allow routing (safe default)

    state = _session_state.get(key)
    if not state:
        return False  # first request for this session → allow routing

    gap = now - state.get("last_ts", 0)
    if gap > _CACHE_TTL_SEC:
        return False  # cache expired → allow routing

    # Exception: complex request (score ≥ 6) when session is stuck on Haiku.
    # Cache bust is acceptable — quality matters more than $0.30 cache savings.
    if score >= 6 and "haiku" in state.get("model", "").lower():
        return False

    # Session is active. Block routing if cache was written for a different model.
    if state.get("cache_warm") and state.get("model") != target_model:
        _routing_skipped_cache += 1
        return True

    return False


def _is_tool_result_request(body_bytes: bytes) -> bool:
    """Returns True if the last user message consists only of tool_result blocks (no free text)."""
    try:
        body = json.loads(body_bytes)
        messages = body.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                return False
            return all(
                isinstance(b, dict) and b.get("type") == "tool_result"
                for b in content
            )
    except Exception:
        pass
    return False


def dedup_check(body_bytes: bytes, now: float) -> tuple:
    """
    Check if identical request was processed recently.
    Tool_result requests (mid tool-chain) use a 15s window since their content
    is stable within a single tool chain. Other requests use 5s.
    Returns (cached_response, content_type, req_hash) if found,
    else (None, None, req_hash).
    """
    req_hash = hashlib.sha256(body_bytes).hexdigest()
    ttl = _DEDUP_TTL_TOOL_SEC if _is_tool_result_request(body_bytes) else _DEDUP_TTL_SEC
    if req_hash in _dedup_cache:
        cached_resp, cached_ts, cached_ct = _dedup_cache[req_hash]
        if now - cached_ts < ttl:
            return cached_resp, cached_ct, req_hash
        else:
            del _dedup_cache[req_hash]
    return None, None, req_hash


def dedup_cache_response(req_hash: str, response: bytes, now: float,
                         content_type: str = "application/json"):
    """Store successful response in dedup cache."""
    if len(_dedup_cache) > 500:
        oldest = min(_dedup_cache, key=lambda k: _dedup_cache[k][1])
        del _dedup_cache[oldest]
    _dedup_cache[req_hash] = (response, now, content_type)


def tool_result_get(tool_name: str, tool_input: dict, now: float) -> str | None:
    """Check if we've seen this exact tool call recently. Returns cached result or None."""
    input_hash = hashlib.sha256(json.dumps(tool_input, sort_keys=True).encode()).hexdigest()
    cache_key = (tool_name, input_hash)

    if cache_key in _tool_result_cache:
        cached_result, cached_ts = _tool_result_cache[cache_key]
        if now - cached_ts < _TOOL_CACHE_TTL_SEC:
            return cached_result
        else:
            del _tool_result_cache[cache_key]
    return None


def tool_result_cache(tool_name: str, tool_input: dict, result_text: str, now: float):
    """Store tool result for future dedup."""
    input_hash = hashlib.sha256(json.dumps(tool_input, sort_keys=True).encode()).hexdigest()
    cache_key = (tool_name, input_hash)
    _tool_result_cache[cache_key] = (result_text, now)


def tool_cache_clear():
    """Clear tool cache (call when session ends)."""
    global _tool_result_cache
    _tool_result_cache.clear()


def enforce_max_messages(body_data: dict) -> tuple:
    """
    Detect new sessions and enforce per-session max message count.
    Returns (modified_body_data, trimmed_count_if_applied).
    """
    global _last_message_count
    messages = body_data.get("messages", [])
    current_count = len(messages)

    # Detect new session: message count dropped (user cleared or new session started)
    if current_count < _last_message_count * 0.5:
        _last_message_count = current_count
        return body_data, 0

    _last_message_count = current_count

    # If exceeds threshold, trim to last 30 messages (keep protected ones)
    if current_count > _message_count_threshold:
        protected_indices = set(range(current_count - 3, current_count))
        for i, msg in enumerate(messages):
            content = msg.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                        protected_indices.add(i)

        trimmed = []
        for i in range(current_count - 1, -1, -1):
            if i in protected_indices or len(trimmed) < 30:
                trimmed.insert(0, messages[i])

        trimmed_count = current_count - len(trimmed)
        body_data["messages"] = trimmed
        return body_data, trimmed_count

    return body_data, 0




def _get_message_content_text(content) -> str:
    """Extract all text from message content (handles str or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    text_parts.append("[image]")
            elif isinstance(block, str):
                text_parts.append(block)
        return "".join(text_parts)
    return ""


def has_recent_tool_results(messages: list) -> bool:
    """Check if last 4 messages contain any tool_use or tool_result blocks."""
    recent = messages[-4:] if len(messages) >= 4 else messages
    for msg in recent:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                    return True
    return False


def _count_message_tokens(msg: dict) -> int:
    """Rough token count for a message: role (1) + content."""
    tokens = 1
    content = msg.get("content", "")
    if isinstance(content, str):
        tokens += len(content) // 4
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if text:
                    tokens += len(text) // 4
    return max(1, tokens)


def should_throttle_stream(body_data: dict) -> bool:
    """Check if response should be throttled (stream=true and large request)."""
    if not body_data.get("stream"):
        return False
    messages = body_data.get("messages", [])
    total_tokens = sum(len(_get_message_content_text(m.get("content", ""))) // 4 for m in messages)
    return total_tokens > 10000


def throttle_stream_delay_ms(body_data: dict) -> int:
    """Calculate delay between stream chunks in milliseconds."""
    if not body_data.get("stream"):
        return 0
    messages = body_data.get("messages", [])
    total_tokens = sum(len(_get_message_content_text(m.get("content", ""))) // 4 for m in messages)
    if total_tokens < 5000:
        return 0
    elif total_tokens < 20000:
        return 10
    else:
        return 25


def trim_old_messages(body_data: dict, max_input_tokens: int = 50000) -> tuple:
    """
    Remove oldest non-critical messages if total tokens > max_input_tokens.
    Preserves: system, last 3 messages, and messages with tool_use/tool_result.
    Returns (modified_body_data, token_saved) if trimmed, else (body_data, 0).
    """
    messages = body_data.get("messages", [])
    if len(messages) <= 3:
        return body_data, 0

    total_tokens = sum(_count_message_tokens(m) for m in messages)
    if total_tokens <= max_input_tokens:
        return body_data, 0

    # Protect last 3 messages and any with tool blocks
    protected_indices = set(range(len(messages) - 3, len(messages)))
    for i, msg in enumerate(messages):
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                    protected_indices.add(i)

    # Remove oldest unprotected messages until under budget
    trimmed = []
    for i in range(len(messages) - 1, -1, -1):
        if i in protected_indices or len(trimmed) < 3:
            trimmed.insert(0, messages[i])
        else:
            total_tokens -= _count_message_tokens(messages[i])
            if total_tokens <= max_input_tokens:
                break

    tokens_saved = sum(_count_message_tokens(m) for m in messages) - sum(_count_message_tokens(m) for m in trimmed)
    body_data["messages"] = trimmed
    return body_data, tokens_saved


def complexity_score(body_data: dict) -> int:
	"""
	Estimate request complexity 0-10 based on:
	- number of messages
	- total content length
	- presence of tool calls
	- message diversity
	"""
	score = 0
	messages = body_data.get("messages", [])

	# Message count: +1 per 2 messages (max 4)
	score += min(len(messages) // 2, 4)

	# Content length: +1 per 20k chars (max 4)
	total_chars = sum(len(_get_message_content_text(m.get("content", ""))) for m in messages)
	score += min(total_chars // 20000, 4)

	# Has tool_use or tool_result: +2
	if any(m.get("content") for m in messages):
		for msg in messages:
			content = msg.get("content", [])
			if isinstance(content, list):
				for block in content:
					if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
						score += 2
						break

	return min(score, 10)


_ERROR_KEYWORDS = ("error", "failed", "traceback", "exception", "invalid", "syntax error",
                   "attributeerror", "typeerror", "valueerror", "keyerror", "nameerror",
                   "cannot", "not found", "refused", "denied", "undefined")


def _has_tool_errors(messages: list) -> bool:
    """Return True if any tool_result in last 6 messages contains error keywords."""
    recent = messages[-6:] if len(messages) >= 6 else messages
    for msg in recent:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    # tool_result content can be str or list of blocks
                    inner = block.get("content", "")
                    if isinstance(inner, list):
                        inner = " ".join(b.get("text", "") for b in inner if isinstance(b, dict))
                    if any(kw in (inner or "").lower() for kw in _ERROR_KEYWORDS):
                        return True
    return False


def auto_enable_thinking(body_data: dict) -> tuple:
    """
    Auto-enable adaptive thinking when tool chain has errors.
    Only activates if thinking is not already set.
    Uses adaptive thinking + effort (budget_tokens is deprecated on Opus 4.7+).
    Returns (modified_body_data, opt_tag_or_None).
    """
    # Skip if thinking already set by client
    if body_data.get("thinking"):
        return body_data, None

    messages = body_data.get("messages", [])
    if not messages:
        return body_data, None

    if _has_tool_errors(messages):
        complexity = complexity_score(body_data)
        effort = "high" if complexity >= 5 else "medium"
        body_data["thinking"] = {"type": "adaptive"}
        body_data.setdefault("output_config", {})["effort"] = effort
        return body_data, ("think ", f"auto-enabled adaptive thinking (tool errors, effort={effort}, complexity={complexity})")

    return body_data, None


def limit_thinking_budget(body_data: dict) -> tuple:
    """
    Tune effort for adaptive thinking based on complexity.
    Returns (modified_body_data, optimization_tag_if_applied).

    NOTE: do NOT strip `budget_tokens`. The current API *requires* it for
    `thinking: {type: "enabled"}` and 400s with "thinking.enabled.budget_tokens:
    Field required" without it. (An earlier version stripped it as "deprecated";
    that was wrong and broke every thinking-enabled Claude Code request.)
    """
    thinking = body_data.get("thinking")
    if not thinking or not isinstance(thinking, dict):
        return body_data, None

    # If adaptive thinking is set but no effort, tune by complexity
    if thinking.get("type") == "adaptive" and "output_config" not in body_data:
        complexity = complexity_score(body_data)
        if complexity < 4:
            body_data.setdefault("output_config", {})["effort"] = "medium"
            return body_data, ("thinking", f"set effort=medium for adaptive thinking (complexity {complexity})")
        elif complexity < 7:
            body_data.setdefault("output_config", {})["effort"] = "high"
            return body_data, ("thinking", f"set effort=high for adaptive thinking (complexity {complexity})")

    return body_data, None


def _has_cache_control(body_data: dict) -> bool:
    """True if the request already carries cache_control anywhere — top-level,
    system blocks, tools, or message content blocks.

    Claude Code sets its own cache_control (often ttl='1h') on its blocks.
    Injecting a top-level ephemeral default (ttl='5m') then 400s with
    "Top-level cache_control has ttl='5m' but the target block already has
    cache_control with ttl='1h'". When the client already manages caching,
    our injection is both redundant and harmful, so skip it.
    """
    if body_data.get("cache_control"):
        return True
    system = body_data.get("system")
    if isinstance(system, list) and any(
            isinstance(b, dict) and b.get("cache_control") for b in system):
        return True
    for tool in body_data.get("tools") or []:
        if isinstance(tool, dict) and tool.get("cache_control"):
            return True
    for msg in body_data.get("messages") or []:
        content = msg.get("content")
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("cache_control") for b in content):
            return True
    return False


def optimize_request(body_data: dict) -> tuple:
    """
    Apply all cost optimizations to the request body.
    Returns (modified_body_data, [("tag", "message"), ...]) for logging.
    """
    optimizations = []

    # Limit thinking budget if client already requested thinking
    body_data, thinking_opt = limit_thinking_budget(body_data)
    if thinking_opt:
        optimizations.append(thinking_opt)

    # Skip cache injection if the client already manages cache_control on any
    # block — adding a top-level ephemeral (ttl='5m') default collides with the
    # client's block-level ttl (e.g. Claude Code's '1h') and 400s.
    if _has_cache_control(body_data):
        return body_data, optimizations

    # 1. Auto cache_control on system prompt if not already cached
    if "system" in body_data:
        system = body_data["system"]
        if isinstance(system, str) and len(system) > 1000:
            if "cache_control" not in body_data:
                body_data["cache_control"] = {"type": "ephemeral"}
                optimizations.append(("cache", f"auto-caching system prompt (~{len(system)} chars)"))

    # 2. Auto cache_control on large user messages (if not already cached)
    messages = body_data.get("messages", [])
    if messages and "cache_control" not in body_data:
        last_msg = messages[-1]
        if last_msg.get("role") == "user":
            content = last_msg.get("content")
            content_text = _get_message_content_text(content)
            content_len = len(content_text)
            # Cache user messages > 5000 chars (roughly 1250+ tokens)
            if content_len > 5000:
                body_data["cache_control"] = {"type": "ephemeral"}
                optimizations.append(("cache", f"auto-caching user message (~{content_len} chars)"))

    return body_data, optimizations


def calculate_optimization_savings(optimizations: list, model: str, input_tokens: int,
                                    output_tokens: int, cache_read_tokens: int) -> tuple:
    """
    Calculate actual savings from optimizations.
    Returns (optimizations_json, total_savings_usd) where optimizations_json is a list
    of dicts with {type, saved_usd, ...details}.

    Requires pricing info from db.py's PRICING dict.
    """
    from db import PRICING

    result = []

    for tag, msg in optimizations:
        tag_clean = tag.strip()
        saved = 0

        if tag_clean == "routing":
            # Extract model change from message: "model1 → model2"
            if "→" in msg:
                parts = msg.split("→")
                orig = parts[0].strip().split()[-1]  # last word before arrow
                routed = parts[1].strip().split()[0]  # first word after arrow

                orig_price = PRICING.get(orig, {})
                routed_price = PRICING.get(routed, {})

                if orig_price and routed_price:
                    orig_cost = (input_tokens * orig_price.get("input", 0) +
                                output_tokens * orig_price.get("output", 0)) / 1_000_000
                    routed_cost = (input_tokens * routed_price.get("input", 0) +
                                  output_tokens * routed_price.get("output", 0)) / 1_000_000
                    saved = max(0, orig_cost - routed_cost)

                    result.append({
                        "type": "routing",
                        "from": orig,
                        "to": routed,
                        "saved_usd": round(saved, 6)
                    })

        elif tag_clean == "cache":
            # Cache savings: read_tokens × (input_price - cache_read_price)
            # Assume 90% savings on cache read (0.10× cost)
            if cache_read_tokens > 0:
                model_price = PRICING.get(model, {})
                input_price = model_price.get("input", 0)
                cache_read_price = input_price * 0.1  # 90% cheaper
                saved = (cache_read_tokens * (input_price - cache_read_price)) / 1_000_000
                saved = max(0, saved)

                result.append({
                    "type": "cache",
                    "read_tokens": cache_read_tokens,
                    "saved_usd": round(saved, 6)
                })

        elif tag_clean == "think":
            # Thinking budget limited: rough estimate
            # If complexity low (2k budget) vs high (30k) = save ~8k output tokens
            if "complexity" in msg:
                saved = 0.02  # Conservative estimate
                result.append({
                    "type": "thinking",
                    "reason": msg,
                    "saved_usd": round(saved, 6)
                })

        elif tag_clean == "session":
            # Session trim: rough estimate from message
            if "trimmed" in msg and "messages" in msg:
                try:
                    import re
                    match = re.search(r"trimmed (\d+)", msg)
                    if match:
                        trimmed_msgs = int(match.group(1))
                        # Rough: ~200 tokens per message, input price
                        model_price = PRICING.get(model, {})
                        input_price = model_price.get("input", 0)
                        trimmed_tokens = trimmed_msgs * 200
                        saved = (trimmed_tokens * input_price) / 1_000_000
                        saved = max(0, saved)

                        result.append({
                            "type": "trim",
                            "messages_removed": trimmed_msgs,
                            "saved_usd": round(saved, 6)
                        })
                except:
                    pass

    import json
    return json.dumps(result), sum(s.get("saved_usd", 0) for s in result)
