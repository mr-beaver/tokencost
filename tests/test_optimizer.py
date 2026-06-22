"""
Regression / characterization tests for optimizer.py.

Coverage: dedup_check / dedup_cache_response, should_skip_routing / record_cache_state,
_session_key, enforce_max_messages, _get_message_content_text, has_recent_tool_results,
_count_message_tokens, should_throttle_stream, throttle_stream_delay_ms,
trim_old_messages, complexity_score, _has_tool_errors, auto_enable_thinking,
_is_tool_result_request, tool_result_cache / tool_result_get / tool_cache_clear,
routing_skipped_count.

Excluded (already in test_request_passthrough.py):
  _has_cache_control, optimize_request (cache injection), limit_thinking_budget.
"""

import json
import hashlib

import optimizer


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _clear_module_state():
    """Reset all module-level mutable caches between tests."""
    optimizer._dedup_cache.clear()
    optimizer._tool_result_cache.clear()
    optimizer._session_state.clear()
    optimizer._routing_skipped_cache = 0
    optimizer._last_message_count = 0


def _body(system="hello", messages=None):
    """Minimal body dict."""
    if messages is None:
        messages = [{"role": "user", "content": "hi"}]
    return json.dumps({"system": system, "messages": messages}).encode()


def _body_tool_result():
    """Body whose last user message is pure tool_result blocks."""
    messages = [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "abc", "content": "done"}
        ]}
    ]
    return json.dumps({"messages": messages}).encode()


# ─────────────────────────────────────────────────────────────────────────────
# _session_key
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionKey:
    def test_returns_16_char_hex(self):
        body = json.dumps({"system": "my system prompt"}).encode()
        key = optimizer._session_key(body)
        assert key is not None
        assert len(key) == 16
        assert all(c in "0123456789abcdef" for c in key)

    def test_same_system_same_key(self):
        body1 = json.dumps({"system": "prompt A"}).encode()
        body2 = json.dumps({"system": "prompt A", "messages": [{"role": "user", "content": "x"}]}).encode()
        assert optimizer._session_key(body1) == optimizer._session_key(body2)

    def test_different_system_different_key(self):
        b1 = json.dumps({"system": "prompt A"}).encode()
        b2 = json.dumps({"system": "prompt B"}).encode()
        assert optimizer._session_key(b1) != optimizer._session_key(b2)

    def test_empty_system_returns_none(self):
        body = json.dumps({"system": ""}).encode()
        assert optimizer._session_key(body) is None

    def test_no_system_returns_none(self):
        body = json.dumps({"messages": []}).encode()
        assert optimizer._session_key(body) is None

    def test_list_system_extracts_text(self):
        body = json.dumps({
            "system": [{"type": "text", "text": "list system prompt"}]
        }).encode()
        key = optimizer._session_key(body)
        assert key is not None
        assert len(key) == 16

    def test_list_system_non_dict_blocks_ignored(self):
        body = json.dumps({
            "system": ["raw string block", {"type": "text", "text": "real text"}]
        }).encode()
        # Should not crash; key derived from joined text of dict blocks
        key = optimizer._session_key(body)
        assert key is not None

    def test_invalid_json_returns_none(self):
        assert optimizer._session_key(b"not json at all") is None

    def test_uses_only_first_500_chars_of_text(self):
        short_prompt = "x" * 400
        long_prompt  = "x" * 400 + "y" * 200   # different after 500
        b_short = json.dumps({"system": short_prompt}).encode()
        b_long  = json.dumps({"system": long_prompt}).encode()
        # Both truncate at 500 chars of "x..."; keys differ because the
        # first 500 chars of long_prompt differ from short_prompt at char 400+
        key_short = optimizer._session_key(b_short)
        key_long  = optimizer._session_key(b_long)
        # They differ because long_prompt[:500] = "x"*400+"y"*100 ≠ short_prompt[:500]
        assert key_short != key_long

    def test_keys_agree_when_system_truncation_identical(self):
        # Two bodies whose system prompts share the first 500 chars get the same key.
        prefix = "z" * 500
        b1 = json.dumps({"system": prefix + "extra1"}).encode()
        b2 = json.dumps({"system": prefix + "extra2"}).encode()
        assert optimizer._session_key(b1) == optimizer._session_key(b2)


# ─────────────────────────────────────────────────────────────────────────────
# _is_tool_result_request
# ─────────────────────────────────────────────────────────────────────────────

class TestIsToolResultRequest:
    def test_pure_tool_result_returns_true(self):
        assert optimizer._is_tool_result_request(_body_tool_result()) is True

    def test_mixed_content_returns_false(self):
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "abc", "content": "done"},
                {"type": "text", "text": "also some text"},
            ]}
        ]
        body = json.dumps({"messages": messages}).encode()
        assert optimizer._is_tool_result_request(body) is False

    def test_plain_text_message_returns_false(self):
        body = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        assert optimizer._is_tool_result_request(body) is False

    def test_no_messages_returns_false(self):
        assert optimizer._is_tool_result_request(b"{}") is False

    def test_invalid_json_returns_false(self):
        assert optimizer._is_tool_result_request(b"bad json") is False

    def test_last_user_message_decides(self):
        # Assistant message after the tool_result user turn is ignored;
        # scanning stops at last user message.
        messages = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": "ok"}
            ]},
            {"role": "assistant", "content": "got it"},
        ]
        body = json.dumps({"messages": messages}).encode()
        # No user message at the end → keep scanning backwards → finds
        # the user message with pure tool_result
        assert optimizer._is_tool_result_request(body) is True

    def test_content_is_string_returns_false(self):
        # content is a plain string (not a list) → should return False
        messages = [{"role": "user", "content": "tool_result placeholder"}]
        body = json.dumps({"messages": messages}).encode()
        assert optimizer._is_tool_result_request(body) is False


# ─────────────────────────────────────────────────────────────────────────────
# dedup_check / dedup_cache_response
# ─────────────────────────────────────────────────────────────────────────────

class TestDedupCache:
    def setup_method(self):
        _clear_module_state()

    def test_miss_on_first_call(self):
        body = b'{"model": "x"}'
        cached, _ct, req_hash = optimizer.dedup_check(body, now=1000.0)
        assert cached is None
        assert req_hash == hashlib.sha256(body).hexdigest()

    def test_hit_within_ttl(self):
        body = b'{"model": "x"}'
        _, _ct, req_hash = optimizer.dedup_check(body, now=1000.0)
        optimizer.dedup_cache_response(req_hash, b"response-data", now=1000.0)

        cached, _ct, _ = optimizer.dedup_check(body, now=1004.9)  # < 5s TTL
        assert cached == b"response-data"

    def test_miss_after_ttl_expired(self):
        body = b'{"model": "x"}'
        _, _ct, req_hash = optimizer.dedup_check(body, now=1000.0)
        optimizer.dedup_cache_response(req_hash, b"old-response", now=1000.0)

        cached, _ct, _ = optimizer.dedup_check(body, now=1005.1)  # > 5s TTL
        assert cached is None

    def test_different_body_is_miss(self):
        body1 = b'{"model": "a"}'
        body2 = b'{"model": "b"}'
        _, _ct, h1 = optimizer.dedup_check(body1, now=1000.0)
        optimizer.dedup_cache_response(h1, b"resp-a", now=1000.0)

        cached, _ct, _ = optimizer.dedup_check(body2, now=1001.0)
        assert cached is None

    def test_tool_result_uses_extended_ttl(self):
        body = _body_tool_result()
        _, _ct, req_hash = optimizer.dedup_check(body, now=1000.0)
        optimizer.dedup_cache_response(req_hash, b"tool-resp", now=1000.0)

        # At 14.9s — within the 15s tool TTL, outside the 5s normal TTL
        cached, _ct, _ = optimizer.dedup_check(body, now=1014.9)
        assert cached == b"tool-resp"

    def test_tool_result_ttl_still_expires(self):
        body = _body_tool_result()
        _, _ct, req_hash = optimizer.dedup_check(body, now=1000.0)
        optimizer.dedup_cache_response(req_hash, b"tool-resp", now=1000.0)

        cached, _ct, _ = optimizer.dedup_check(body, now=1015.1)  # > 15s
        assert cached is None

    def test_eviction_at_capacity(self):
        # Fill the cache past the 500-entry limit; the oldest should be evicted.
        for i in range(501):
            key = f"key-{i:04d}"
            optimizer._dedup_cache[key] = (b"resp", float(i), "application/json")
        # Insert one more via the public API (simulates real store)
        body = b'{"model": "new"}'
        _, _ct, req_hash = optimizer.dedup_check(body, now=9999.0)
        optimizer.dedup_cache_response(req_hash, b"new-resp", now=9999.0)
        # Cache should not exceed 501 (evicted one before adding)
        assert len(optimizer._dedup_cache) <= 501

    def test_dedup_roundtrips_content_type(self):
        optimizer._dedup_cache.clear()
        body = b'{"model":"claude-opus-4-8","messages":[{"role":"user","content":"ct test"}]}'
        _, _, req_hash = optimizer.dedup_check(body, now=1000.0)
        optimizer.dedup_cache_response(
            req_hash, b"data: {}\n\n", now=1000.0, content_type="text/event-stream")
        cached, ct, _ = optimizer.dedup_check(body, now=1001.0)
        assert cached == b"data: {}\n\n"
        assert ct == "text/event-stream"

    def test_dedup_default_content_type_is_json(self):
        optimizer._dedup_cache.clear()
        body = b'{"model":"x","messages":[{"role":"user","content":"default ct"}]}'
        _, _, req_hash = optimizer.dedup_check(body, now=2000.0)
        optimizer.dedup_cache_response(req_hash, b"{}", now=2000.0)
        cached, ct, _ = optimizer.dedup_check(body, now=2001.0)
        assert cached == b"{}"
        assert ct == "application/json"


# ─────────────────────────────────────────────────────────────────────────────
# record_cache_state / should_skip_routing / routing_skipped_count
# ─────────────────────────────────────────────────────────────────────────────

class TestRoutingSkip:
    def setup_method(self):
        _clear_module_state()

    def _body_with_system(self, text="stable system prompt for routing tests"):
        return json.dumps({"system": text, "messages": [{"role": "user", "content": "hi"}]}).encode()

    def test_first_request_allows_routing(self):
        body = self._body_with_system()
        result = optimizer.should_skip_routing("sonnet", "haiku", now=1000.0, body_bytes=body)
        assert result is False

    def test_same_model_never_skipped(self):
        body = self._body_with_system()
        result = optimizer.should_skip_routing("haiku", "haiku", now=1000.0, body_bytes=body)
        assert result is False

    def test_no_session_key_allows_routing(self):
        # Body with no system → _session_key returns None
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        result = optimizer.should_skip_routing("sonnet", "haiku", now=1000.0, body_bytes=body)
        assert result is False

    def test_cache_warm_active_session_blocks_routing(self):
        body = self._body_with_system()
        optimizer.record_cache_state("sonnet", now=1000.0, body_bytes=body, cache_write_tokens=100)

        result = optimizer.should_skip_routing("sonnet", "haiku", now=1100.0, body_bytes=body)
        assert result is True

    def test_routing_skipped_count_increments(self):
        body = self._body_with_system()
        optimizer.record_cache_state("sonnet", now=1000.0, body_bytes=body, cache_write_tokens=100)

        before = optimizer.routing_skipped_count()
        optimizer.should_skip_routing("sonnet", "haiku", now=1100.0, body_bytes=body)
        assert optimizer.routing_skipped_count() == before + 1

    def test_cache_expired_allows_routing(self):
        body = self._body_with_system()
        optimizer.record_cache_state("sonnet", now=1000.0, body_bytes=body, cache_write_tokens=100)

        # 301 seconds later — past the 300s TTL
        result = optimizer.should_skip_routing("sonnet", "haiku", now=1301.0, body_bytes=body)
        assert result is False

    def test_no_cache_write_does_not_block(self):
        body = self._body_with_system()
        # Record state with no cache_write_tokens → cache_warm stays False
        optimizer.record_cache_state("sonnet", now=1000.0, body_bytes=body, cache_write_tokens=0)

        result = optimizer.should_skip_routing("sonnet", "haiku", now=1100.0, body_bytes=body)
        assert result is False

    def test_high_score_on_haiku_session_allows_routing(self):
        body = self._body_with_system()
        optimizer.record_cache_state("claude-haiku-4-5", now=1000.0, body_bytes=body, cache_write_tokens=50)

        # score=6 and session is on haiku → routing allowed
        result = optimizer.should_skip_routing("claude-haiku-4-5", "claude-sonnet-4-6",
                                                now=1100.0, body_bytes=body, score=6)
        assert result is False

    def test_low_score_on_haiku_session_blocks_routing(self):
        body = self._body_with_system()
        optimizer.record_cache_state("claude-haiku-4-5", now=1000.0, body_bytes=body, cache_write_tokens=50)

        # score=5 (below 6) — routing should be blocked
        result = optimizer.should_skip_routing("claude-haiku-4-5", "claude-sonnet-4-6",
                                                now=1100.0, body_bytes=body, score=5)
        assert result is True

    def test_record_cache_state_ignores_unknown_model(self):
        body = self._body_with_system()
        optimizer.record_cache_state("unknown", now=1000.0, body_bytes=body, cache_write_tokens=100)
        # Nothing should be written to session_state
        key = optimizer._session_key(body)
        assert key not in optimizer._session_state

    def test_record_cache_state_preserves_cache_warm(self):
        body = self._body_with_system()
        optimizer.record_cache_state("sonnet", now=1000.0, body_bytes=body, cache_write_tokens=50)
        optimizer.record_cache_state("sonnet", now=1100.0, body_bytes=body, cache_write_tokens=0)
        key = optimizer._session_key(body)
        # cache_warm should remain True even when second update has zero tokens
        assert optimizer._session_state[key]["cache_warm"] is True

    def test_routing_skipped_count_initial(self):
        assert optimizer.routing_skipped_count() == 0


# ─────────────────────────────────────────────────────────────────────────────
# tool_result_cache / tool_result_get / tool_cache_clear
# ─────────────────────────────────────────────────────────────────────────────

class TestToolResultCache:
    def setup_method(self):
        optimizer.tool_cache_clear()

    def test_miss_on_first_call(self):
        result = optimizer.tool_result_get("bash", {"cmd": "ls"}, now=1000.0)
        assert result is None

    def test_round_trip(self):
        optimizer.tool_result_cache("bash", {"cmd": "ls"}, "file.txt\n", now=1000.0)
        result = optimizer.tool_result_get("bash", {"cmd": "ls"}, now=1001.0)
        assert result == "file.txt\n"

    def test_miss_after_ttl(self):
        optimizer.tool_result_cache("bash", {"cmd": "ls"}, "file.txt\n", now=1000.0)
        result = optimizer.tool_result_get("bash", {"cmd": "ls"}, now=1301.0)  # > 300s
        assert result is None

    def test_different_input_is_miss(self):
        optimizer.tool_result_cache("bash", {"cmd": "ls"}, "file.txt\n", now=1000.0)
        result = optimizer.tool_result_get("bash", {"cmd": "pwd"}, now=1001.0)
        assert result is None

    def test_different_tool_name_is_miss(self):
        optimizer.tool_result_cache("bash", {"cmd": "ls"}, "file.txt\n", now=1000.0)
        result = optimizer.tool_result_get("python", {"cmd": "ls"}, now=1001.0)
        assert result is None

    def test_clear_removes_all_entries(self):
        optimizer.tool_result_cache("bash", {"cmd": "ls"}, "data", now=1000.0)
        optimizer.tool_cache_clear()
        result = optimizer.tool_result_get("bash", {"cmd": "ls"}, now=1001.0)
        assert result is None

    def test_input_order_does_not_matter(self):
        optimizer.tool_result_cache("bash", {"a": 1, "b": 2}, "result", now=1000.0)
        result = optimizer.tool_result_get("bash", {"b": 2, "a": 1}, now=1001.0)
        assert result == "result"

    def test_hit_near_ttl_boundary(self):
        optimizer.tool_result_cache("bash", {"cmd": "pwd"}, "/home", now=1000.0)
        result = optimizer.tool_result_get("bash", {"cmd": "pwd"}, now=1299.9)  # < 300s
        assert result == "/home"


# ─────────────────────────────────────────────────────────────────────────────
# _get_message_content_text
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMessageContentText:
    def test_plain_string(self):
        assert optimizer._get_message_content_text("hello world") == "hello world"

    def test_list_with_text_blocks(self):
        content = [
            {"type": "text", "text": "foo"},
            {"type": "text", "text": "bar"},
        ]
        assert optimizer._get_message_content_text(content) == "foobar"

    def test_list_with_image_block(self):
        content = [{"type": "image", "source": {"type": "base64", "data": "..."}}]
        assert optimizer._get_message_content_text(content) == "[image]"

    def test_list_mixed_text_and_image(self):
        content = [
            {"type": "text", "text": "see this: "},
            {"type": "image", "source": {}},
        ]
        assert optimizer._get_message_content_text(content) == "see this: [image]"

    def test_list_with_string_element(self):
        assert optimizer._get_message_content_text(["a", "b"]) == "ab"

    def test_empty_list(self):
        assert optimizer._get_message_content_text([]) == ""

    def test_unknown_type_ignored(self):
        content = [{"type": "tool_use", "id": "x", "name": "bash"}]
        assert optimizer._get_message_content_text(content) == ""

    def test_non_string_non_list_returns_empty(self):
        assert optimizer._get_message_content_text(None) == ""
        assert optimizer._get_message_content_text(42) == ""


# ─────────────────────────────────────────────────────────────────────────────
# enforce_max_messages
# ─────────────────────────────────────────────────────────────────────────────

class TestEnforceMaxMessages:
    def setup_method(self):
        optimizer._last_message_count = 0

    def _msgs(self, n):
        return [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"msg {i}"} for i in range(n)]

    def test_under_threshold_no_trim(self):
        body = {"messages": self._msgs(10)}
        out, trimmed = optimizer.enforce_max_messages(body)
        assert trimmed == 0
        assert len(out["messages"]) == 10

    def test_over_threshold_trims(self):
        body = {"messages": self._msgs(45)}
        out, trimmed = optimizer.enforce_max_messages(body)
        assert trimmed > 0
        assert len(out["messages"]) <= 30 + 3  # 30 kept + protected tail

    def test_trim_preserves_last_3(self):
        msgs = self._msgs(45)
        last_3 = msgs[-3:]
        body = {"messages": msgs}
        out, _ = optimizer.enforce_max_messages(body)
        assert out["messages"][-3:] == last_3

    def test_trim_preserves_tool_messages(self):
        msgs = self._msgs(45)
        # Inject a tool_use block at index 5 (in the "to be trimmed" zone)
        msgs[5]["content"] = [{"type": "tool_use", "id": "tu1", "name": "bash", "input": {}}]
        body = {"messages": msgs}
        out, _ = optimizer.enforce_max_messages(body)
        # message at index 5 must be retained
        assert msgs[5] in out["messages"]

    def test_new_session_detected_on_count_drop(self):
        optimizer._last_message_count = 100  # simulate an active session
        body = {"messages": self._msgs(10)}  # count dropped to 10 (< 50)
        out, trimmed = optimizer.enforce_max_messages(body)
        assert trimmed == 0
        assert len(out["messages"]) == 10

    def test_exactly_at_threshold_not_trimmed(self):
        # _message_count_threshold == 40; exactly 40 should NOT trigger trim
        body = {"messages": self._msgs(40)}
        out, trimmed = optimizer.enforce_max_messages(body)
        assert trimmed == 0

    def test_one_over_threshold_triggers_trim(self):
        body = {"messages": self._msgs(41)}
        out, trimmed = optimizer.enforce_max_messages(body)
        assert trimmed > 0


# ─────────────────────────────────────────────────────────────────────────────
# has_recent_tool_results
# ─────────────────────────────────────────────────────────────────────────────

class TestHasRecentToolResults:
    def _msg(self, role="user", content="hi"):
        return {"role": role, "content": content}

    def _tool_msg(self, kind="tool_use"):
        return {"role": "user", "content": [{"type": kind, "id": "x"}]}

    def test_no_tool_blocks_returns_false(self):
        msgs = [self._msg() for _ in range(6)]
        assert optimizer.has_recent_tool_results(msgs) is False

    def test_tool_use_in_last_4_returns_true(self):
        msgs = [self._msg() for _ in range(5)]
        msgs[-2] = self._tool_msg("tool_use")
        assert optimizer.has_recent_tool_results(msgs) is True

    def test_tool_result_in_last_4_returns_true(self):
        msgs = [self._msg() for _ in range(5)]
        msgs[-1] = self._tool_msg("tool_result")
        assert optimizer.has_recent_tool_results(msgs) is True

    def test_tool_use_outside_window_returns_false(self):
        # 10 messages; tool_use at index 0 (not in last 4)
        msgs = [self._msg() for _ in range(10)]
        msgs[0] = self._tool_msg("tool_use")
        assert optimizer.has_recent_tool_results(msgs) is False

    def test_fewer_than_4_messages_all_checked(self):
        msgs = [self._tool_msg("tool_result")]
        assert optimizer.has_recent_tool_results(msgs) is True

    def test_empty_list_returns_false(self):
        assert optimizer.has_recent_tool_results([]) is False

    def test_string_content_ignored(self):
        # String content — no list blocks → False
        msgs = [{"role": "user", "content": "tool_use tool_result"}]
        assert optimizer.has_recent_tool_results(msgs) is False


# ─────────────────────────────────────────────────────────────────────────────
# _count_message_tokens
# ─────────────────────────────────────────────────────────────────────────────

class TestCountMessageTokens:
    def test_empty_content_returns_1(self):
        # Role=1, empty content → max(1, 1) = 1
        msg = {"role": "user", "content": ""}
        assert optimizer._count_message_tokens(msg) == 1

    def test_string_content_adds_chars_div_4(self):
        # 40 chars → 40//4 = 10 tokens; + 1 role = 11
        msg = {"role": "user", "content": "a" * 40}
        assert optimizer._count_message_tokens(msg) == 11

    def test_list_content_sums_text_blocks(self):
        # 80 chars in two text blocks → 80//4=20; + 1 = 21
        msg = {"role": "user", "content": [
            {"type": "text", "text": "a" * 40},
            {"type": "text", "text": "b" * 40},
        ]}
        assert optimizer._count_message_tokens(msg) == 21

    def test_list_content_non_text_blocks_ignored(self):
        # image block has no text → only role token
        msg = {"role": "user", "content": [{"type": "image"}]}
        assert optimizer._count_message_tokens(msg) == 1

    def test_minimum_is_1(self):
        msg = {"role": "assistant", "content": []}
        assert optimizer._count_message_tokens(msg) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# should_throttle_stream / throttle_stream_delay_ms
# ─────────────────────────────────────────────────────────────────────────────

class TestThrottleStream:
    def _body_with_content(self, chars, stream=True):
        return {
            "stream": stream,
            "messages": [{"role": "user", "content": "x" * chars}],
        }

    def test_no_stream_never_throttles(self):
        body = self._body_with_content(chars=50000, stream=False)
        assert optimizer.should_throttle_stream(body) is False

    def test_small_stream_not_throttled(self):
        # 10000 tokens threshold; 10000 chars = 2500 tokens → under
        body = self._body_with_content(chars=39999)
        assert optimizer.should_throttle_stream(body) is False

    def test_large_stream_throttled(self):
        # threshold is total_tokens > 10000; chars//4 must exceed 10000 → need > 40000 chars
        # 40004 chars → 40004//4 = 10001 tokens > 10000
        body = self._body_with_content(chars=40004)
        assert optimizer.should_throttle_stream(body) is True

    def test_no_stream_delay_zero(self):
        body = self._body_with_content(chars=50000, stream=False)
        assert optimizer.throttle_stream_delay_ms(body) == 0

    def test_small_stream_delay_zero(self):
        # < 5000 tokens = < 20000 chars → delay 0
        body = self._body_with_content(chars=19999, stream=True)
        assert optimizer.throttle_stream_delay_ms(body) == 0

    def test_medium_stream_delay_10ms(self):
        # 5000-20000 token range: chars 20001..79999
        body = self._body_with_content(chars=30000, stream=True)
        assert optimizer.throttle_stream_delay_ms(body) == 10

    def test_large_stream_delay_25ms(self):
        # > 20000 tokens = > 80000 chars
        body = self._body_with_content(chars=80001, stream=True)
        assert optimizer.throttle_stream_delay_ms(body) == 25


# ─────────────────────────────────────────────────────────────────────────────
# trim_old_messages
# ─────────────────────────────────────────────────────────────────────────────

class TestTrimOldMessages:
    def _msg(self, chars=0):
        return {"role": "user", "content": "x" * chars}

    def test_no_trim_when_under_token_limit(self):
        body = {"messages": [self._msg(10) for _ in range(5)]}
        out, saved = optimizer.trim_old_messages(body, max_input_tokens=5000)
        assert saved == 0
        assert len(out["messages"]) == 5

    def test_no_trim_when_3_or_fewer_messages(self):
        body = {"messages": [self._msg(1000) for _ in range(3)]}
        out, saved = optimizer.trim_old_messages(body, max_input_tokens=1)
        assert saved == 0
        assert len(out["messages"]) == 3

    def test_trims_over_token_limit(self):
        # 50 messages × 100 chars each ≈ 25 tokens per msg → 1250 tokens total
        # Set max_input_tokens=100 to force trim
        body = {"messages": [self._msg(400) for _ in range(50)]}
        out, saved = optimizer.trim_old_messages(body, max_input_tokens=100)
        assert saved > 0
        assert len(out["messages"]) < 50

    def test_preserves_last_3(self):
        msgs = [self._msg(400) for _ in range(20)]
        last_3 = msgs[-3:]
        body = {"messages": msgs}
        out, _ = optimizer.trim_old_messages(body, max_input_tokens=50)
        assert out["messages"][-3:] == last_3

    def test_preserves_tool_messages(self):
        msgs = [self._msg(400) for _ in range(20)]
        msgs[2] = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
        ]}
        body = {"messages": msgs}
        out, _ = optimizer.trim_old_messages(body, max_input_tokens=50)
        assert msgs[2] in out["messages"]

    def test_returns_tokens_saved(self):
        body = {"messages": [self._msg(400) for _ in range(20)]}
        _, saved = optimizer.trim_old_messages(body, max_input_tokens=50)
        assert isinstance(saved, int)
        assert saved > 0


# ─────────────────────────────────────────────────────────────────────────────
# complexity_score
# ─────────────────────────────────────────────────────────────────────────────

class TestComplexityScore:
    def test_empty_body_is_zero(self):
        assert optimizer.complexity_score({"messages": []}) == 0

    def test_capped_at_10(self):
        # Many messages + huge content + tool use → should cap at 10
        msgs = []
        for _ in range(20):
            msgs.append({"role": "user", "content": "x" * 20000})
        msgs.append({"role": "user", "content": [
            {"type": "tool_use", "id": "x", "name": "bash", "input": {}}
        ]})
        score = optimizer.complexity_score({"messages": msgs})
        assert score == 10

    def test_message_count_contribution(self):
        # 4 messages → min(4//2, 4) = 2 points from messages alone
        msgs = [{"role": "user", "content": "hi"} for _ in range(4)]
        score = optimizer.complexity_score({"messages": msgs})
        assert score >= 2

    def test_content_length_contribution(self):
        # 40000 chars → min(40000//20000, 4) = 2 points
        msgs = [{"role": "user", "content": "x" * 40000}]
        score = optimizer.complexity_score({"messages": msgs})
        assert score >= 2

    def test_tool_use_adds_2(self):
        # Minimal message count & content, but with tool_use block → +2
        msgs = [{"role": "user", "content": [
            {"type": "tool_use", "id": "abc", "name": "bash", "input": {}}
        ]}]
        score = optimizer.complexity_score({"messages": msgs})
        assert score >= 2

    def test_tool_result_adds_2(self):
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "abc", "content": "ok"}
        ]}]
        score = optimizer.complexity_score({"messages": msgs})
        assert score >= 2

    def test_score_is_int(self):
        score = optimizer.complexity_score({"messages": [{"role": "user", "content": "hello"}]})
        assert isinstance(score, int)

    def test_minimum_score_is_zero(self):
        assert optimizer.complexity_score({}) == 0


# ─────────────────────────────────────────────────────────────────────────────
# _has_tool_errors
# ─────────────────────────────────────────────────────────────────────────────

class TestHasToolErrors:
    def _tool_result_msg(self, text):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": text}
        ]}

    def test_no_messages_returns_false(self):
        assert optimizer._has_tool_errors([]) is False

    def test_clean_tool_result_returns_false(self):
        msgs = [self._tool_result_msg("everything worked fine")]
        assert optimizer._has_tool_errors(msgs) is False

    def test_error_keyword_returns_true(self):
        msgs = [self._tool_result_msg("bash: command not found")]
        assert optimizer._has_tool_errors(msgs) is True

    def test_traceback_keyword(self):
        msgs = [self._tool_result_msg("Traceback (most recent call last):")]
        assert optimizer._has_tool_errors(msgs) is True

    def test_case_insensitive(self):
        msgs = [self._tool_result_msg("ERROR: file not found")]
        assert optimizer._has_tool_errors(msgs) is True

    def test_only_last_6_checked(self):
        # 10 messages; error only in message 0 (not in last 6)
        msgs = [self._tool_result_msg("error here")]
        msgs += [self._tool_result_msg("all good") for _ in range(9)]
        assert optimizer._has_tool_errors(msgs) is False

    def test_tool_result_with_list_content(self):
        # tool_result where content is a list of text blocks
        msg = {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "y",
             "content": [{"type": "text", "text": "AttributeError: blah"}]}
        ]}
        assert optimizer._has_tool_errors([msg]) is True

    def test_non_tool_result_blocks_ignored(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "This is an error message but not in a tool_result"}
        ]}]
        assert optimizer._has_tool_errors(msgs) is False

    def test_refused_keyword(self):
        msgs = [self._tool_result_msg("access refused")]
        assert optimizer._has_tool_errors(msgs) is True


# ─────────────────────────────────────────────────────────────────────────────
# auto_enable_thinking
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoEnableThinking:
    def _msg_with_error(self):
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "error: command failed"}
        ]}

    def test_no_messages_no_change(self):
        body = {}
        out, tag = optimizer.auto_enable_thinking(body)
        assert tag is None
        assert "thinking" not in out

    def test_no_tool_errors_no_change(self):
        body = {"messages": [{"role": "user", "content": "all good"}]}
        out, tag = optimizer.auto_enable_thinking(body)
        assert tag is None
        assert "thinking" not in out

    def test_tool_errors_enable_thinking(self):
        body = {"messages": [self._msg_with_error()]}
        out, tag = optimizer.auto_enable_thinking(body)
        assert out["thinking"] == {"type": "adaptive"}
        assert tag is not None

    def test_thinking_already_set_is_skipped(self):
        body = {
            "thinking": {"type": "enabled", "budget_tokens": 5000},
            "messages": [self._msg_with_error()],
        }
        out, tag = optimizer.auto_enable_thinking(body)
        assert tag is None
        # Original thinking config preserved
        assert out["thinking"]["type"] == "enabled"

    def test_high_complexity_uses_high_effort(self):
        # Build a body with score >= 5: many messages + long content + tool errors
        msgs = [{"role": "user", "content": "x" * 40000} for _ in range(10)]
        msgs.append(self._msg_with_error())
        body = {"messages": msgs}
        out, _ = optimizer.auto_enable_thinking(body)
        assert out.get("output_config", {}).get("effort") == "high"

    def test_low_complexity_uses_medium_effort(self):
        body = {"messages": [self._msg_with_error()]}
        out, _ = optimizer.auto_enable_thinking(body)
        assert out.get("output_config", {}).get("effort") == "medium"

    def test_tag_contains_adaptive_thinking(self):
        body = {"messages": [self._msg_with_error()]}
        _, tag = optimizer.auto_enable_thinking(body)
        assert tag is not None
        # tag is a tuple: (prefix, description)
        assert "think" in tag[0]
        assert "adaptive" in tag[1]
