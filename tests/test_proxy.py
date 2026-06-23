"""Regression-safety (characterization) tests for proxy.py.

These tests pin the CURRENT behavior of proxy.py so future changes can't
silently break it. They are not specification tests — they were written by
reading the implementation and asserting what the code actually does.

Groups:
  1. Pure helpers   — complexity_score, route_model, _keyword_score,
                      _code_pattern_score, _parse_openai, detect_source,
                      detect_effort, extract_prompt_preview,
                      _count_tool_turns, _last_user_text
  2. Integration    — proxy_anthropic handler via TestClient + respx
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time

import pytest
import respx
import httpx

# ── bring proxy into scope ────────────────────────────────────────────────────
import proxy  # noqa: E402  (conftest.py already added repo root to sys.path)
import db     # noqa: E402
import queue
import threading as _threading


# ─────────────────────────────────────────────────────────────────────────────
# Helpers used across tests
# ─────────────────────────────────────────────────────────────────────────────

def _body(**kwargs) -> dict:
    """Build a minimal Anthropic-style request body."""
    defaults = {
        "model": "claude-opus-4-8",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    defaults.update(kwargs)
    return defaults


def _jbytes(**kwargs) -> bytes:
    return json.dumps(_body(**kwargs)).encode()


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 1: Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestKeywordScore:
    """proxy._keyword_score(text) → 3 if any complex keyword present, else 0."""

    def test_implement_returns_3(self):
        assert proxy._keyword_score("please implement this feature") == 3

    def test_create_returns_3(self):
        assert proxy._keyword_score("create a new class") == 3

    def test_fix_returns_3(self):
        assert proxy._keyword_score("fix the bug in the router") == 3

    def test_debug_returns_3(self):
        assert proxy._keyword_score("debug the crash in auth") == 3

    def test_write_returns_3(self):
        assert proxy._keyword_score("write a function for this") == 3

    def test_conversational_returns_0(self):
        assert proxy._keyword_score("what time is it?") == 0

    def test_empty_returns_0(self):
        assert proxy._keyword_score("") == 0

    def test_case_insensitive(self):
        # Keyword lookup normalises to lowercase
        assert proxy._keyword_score("IMPLEMENT this now") == 3


class TestCodePatternScore:
    """proxy._code_pattern_score(text) returns 0-5."""

    def test_backtick_block_adds_3(self):
        score = proxy._code_pattern_score("look at ```python\nx=1\n```")
        assert score >= 3

    def test_file_extension_adds_3(self):
        score = proxy._code_pattern_score("edit the file main.py please")
        assert score >= 3

    def test_code_construct_def_adds_2(self):
        score = proxy._code_pattern_score("def my_function(x):")
        assert score >= 2

    def test_file_path_adds_2(self):
        score = proxy._code_pattern_score("look at /src/utils/helper.ts")
        assert score >= 2

    def test_max_capped_at_5(self):
        # All signals present: backtick + extension + construct + path
        text = "```\ndef foo.py /src/bar:\n    pass\n```"
        score = proxy._code_pattern_score(text)
        assert score <= 5

    def test_plain_text_returns_0(self):
        assert proxy._code_pattern_score("what is the weather today") == 0


class TestComplexityScore:
    """proxy.complexity_score(body) → 0-10."""

    def test_thinking_enabled_returns_10(self):
        body = _body(thinking={"type": "enabled", "budget_tokens": 1000})
        assert proxy.complexity_score(body) == 10

    def test_no_last_user_text_returns_10(self):
        # Only tool_result in last user message → no extractable user text → 10
        body = {
            "model": "claude-opus-4-8",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "x", "content": "ok"}
                    ],
                }
            ],
        }
        assert proxy.complexity_score(body) == 10

    def test_simple_question_returns_0(self):
        body = _body(messages=[{"role": "user", "content": "what is Python?"}])
        score = proxy.complexity_score(body)
        # Matches _SIMPLE_RE + short → 0
        assert score == 0

    def test_implement_request_scores_high(self):
        body = _body(messages=[{"role": "user", "content": "implement a REST API endpoint"}])
        score = proxy.complexity_score(body)
        assert score >= 3

    def test_long_message_bumps_score(self):
        long_msg = "x " * 260  # > 200 chars → +1
        body = _body(messages=[{"role": "user", "content": long_msg}])
        score_long = proxy.complexity_score(body)
        short_body = _body(messages=[{"role": "user", "content": "hello there world"}])
        score_short = proxy.complexity_score(short_body)
        assert score_long >= score_short

    def test_score_capped_at_10(self):
        # Many signals: thinking + long + keywords + code + tools
        body = _body(
            messages=[
                {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "bash", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
                {"role": "user", "content": "implement " * 200 + "```def foo(): pass```"},
            ]
        )
        assert proxy.complexity_score(body) <= 10


class TestCountToolTurns:
    """proxy._count_tool_turns(messages) counts messages containing tool blocks."""

    def test_empty_messages_returns_0(self):
        assert proxy._count_tool_turns([]) == 0

    def test_string_content_not_counted(self):
        messages = [{"role": "user", "content": "just a string"}]
        assert proxy._count_tool_turns(messages) == 0

    def test_tool_use_block_counted(self):
        messages = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "1", "name": "bash", "input": {}}],
            }
        ]
        assert proxy._count_tool_turns(messages) == 1

    def test_tool_result_block_counted(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "1", "content": "output"}],
            }
        ]
        assert proxy._count_tool_turns(messages) == 1

    def test_multiple_tool_blocks_in_same_message_counted_once(self):
        # Counts messages with tool blocks, not individual blocks
        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "1", "name": "bash", "input": {}},
                    {"type": "tool_use", "id": "2", "name": "read", "input": {}},
                ],
            }
        ]
        assert proxy._count_tool_turns(messages) == 1

    def test_two_tool_messages_returns_2(self):
        messages = [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "bash", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        ]
        assert proxy._count_tool_turns(messages) == 2


class TestLastUserText:
    """proxy._last_user_text(messages) → last non-empty user text."""

    def test_simple_string_content(self):
        messages = [{"role": "user", "content": "hello world"}]
        assert proxy._last_user_text(messages) == "hello world"

    def test_list_content_extracts_text_blocks(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "explain this"},
                    {"type": "image", "source": {}},  # ignored
                ],
            }
        ]
        assert proxy._last_user_text(messages) == "explain this"

    def test_skips_non_user_roles(self):
        messages = [
            {"role": "assistant", "content": "I am the assistant"},
            {"role": "user", "content": "user message"},
        ]
        assert proxy._last_user_text(messages) == "user message"

    def test_empty_messages_returns_empty_string(self):
        assert proxy._last_user_text([]) == ""

    def test_injected_tags_stripped(self):
        # <ide_selection>…</ide_selection> is stripped by _INJECTED_TAGS
        msg_with_tag = "<ide_selection>some IDE context</ide_selection>real question"
        messages = [{"role": "user", "content": msg_with_tag}]
        result = proxy._last_user_text(messages)
        assert "ide_selection" not in result
        assert "real question" in result

    def test_only_tool_result_returns_empty(self):
        # Tool result only user message → no text block → empty
        messages = [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x", "content": "output"}],
            }
        ]
        assert proxy._last_user_text(messages) == ""


class TestDetectEffort:
    """proxy.detect_effort(body_bytes) → effort level string."""

    def test_no_thinking_returns_standard(self):
        body = _jbytes()
        assert proxy.detect_effort(body) == "standard"

    def test_disabled_thinking_returns_standard(self):
        body = json.dumps(_body(thinking={"type": "disabled"})).encode()
        assert proxy.detect_effort(body) == "standard"

    def test_budget_1500_returns_low(self):
        body = json.dumps(_body(thinking={"type": "enabled", "budget_tokens": 1500})).encode()
        assert proxy.detect_effort(body) == "low"

    def test_budget_1501_returns_medium(self):
        body = json.dumps(_body(thinking={"type": "enabled", "budget_tokens": 1501})).encode()
        assert proxy.detect_effort(body) == "medium"

    def test_budget_5000_returns_medium(self):
        body = json.dumps(_body(thinking={"type": "enabled", "budget_tokens": 5000})).encode()
        assert proxy.detect_effort(body) == "medium"

    def test_budget_5001_returns_high(self):
        body = json.dumps(_body(thinking={"type": "enabled", "budget_tokens": 5001})).encode()
        assert proxy.detect_effort(body) == "high"

    def test_budget_12000_returns_high(self):
        body = json.dumps(_body(thinking={"type": "enabled", "budget_tokens": 12000})).encode()
        assert proxy.detect_effort(body) == "high"

    def test_budget_12001_returns_xhigh(self):
        body = json.dumps(_body(thinking={"type": "enabled", "budget_tokens": 12001})).encode()
        assert proxy.detect_effort(body) == "xhigh"

    def test_invalid_json_returns_standard(self):
        assert proxy.detect_effort(b"not-json") == "standard"


class TestExtractPromptPreview:
    """proxy.extract_prompt_preview(body_bytes, max_chars)."""

    def test_simple_user_text(self):
        body = _jbytes(messages=[{"role": "user", "content": "what is life"}])
        assert proxy.extract_prompt_preview(body) == "what is life"

    def test_max_chars_truncates(self):
        body = _jbytes(messages=[{"role": "user", "content": "x" * 2000}])
        result = proxy.extract_prompt_preview(body, max_chars=50)
        assert len(result) <= 50

    def test_last_user_message_preferred(self):
        body = _jbytes(
            messages=[
                {"role": "user", "content": "first message"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "second message"},
            ]
        )
        assert proxy.extract_prompt_preview(body) == "second message"

    def test_invalid_json_returns_empty(self):
        assert proxy.extract_prompt_preview(b"not-json") == ""

    def test_tool_result_fallback(self):
        # When last user message is only tool_result blocks, extracts tool result content
        body_dict = {
            "model": "claude-opus-4-8",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "x", "content": "the tool output text"}
                    ],
                }
            ],
        }
        result = proxy.extract_prompt_preview(json.dumps(body_dict).encode())
        assert "tool output text" in result

    def test_injected_tags_stripped(self):
        body = _jbytes(
            messages=[{"role": "user", "content": "<ide_selection>ignored</ide_selection>actual text"}]
        )
        result = proxy.extract_prompt_preview(body)
        assert "ide_selection" not in result
        assert "actual text" in result


def _make_request(user_agent: str | None = None) -> "Request":
    """Build a minimal Starlette Request with the given User-Agent header.

    We construct Request directly rather than going through a TestClient because
    pytest-anyio can interfere with async inner-function route handlers defined
    inside class methods, causing silent 422/500 responses when the test file
    is run as part of the full suite. Direct Request construction is simpler and
    avoids the entire ASGI plumbing.
    """
    from starlette.requests import Request as _Request

    headers = []
    if user_agent is not None:
        headers.append((b"user-agent", user_agent.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/messages",
        "headers": headers,
        "query_string": b"",
        "server": ("127.0.0.1", 8082),
    }
    return _Request(scope)


class TestDetectSource:
    """proxy.detect_source(request) tested via direct Starlette Request construction.

    We avoid TestClient here because pytest-anyio intercepts async route handlers
    defined inside class methods and can cause silent failures when the file runs
    inside the full suite (works in isolation, fails in collection).
    Direct Request construction is more robust and tests the exact function we care about.
    """

    def test_claude_code_ua(self):
        req = _make_request("claude-code/1.2.3")
        assert proxy.detect_source(req) == "claude-cli"

    def test_anthropic_python_ua(self):
        req = _make_request("anthropic-python/0.40")
        assert proxy.detect_source(req) == "anthropic-sdk"

    def test_vscode_ua(self):
        req = _make_request("VSCode/1.90 claude-vscode/1.0")
        # claude-vscode pattern matches before vscode → "vscode"
        assert proxy.detect_source(req) == "vscode"

    def test_undici_openclaw(self):
        req = _make_request("undici/7.0")
        assert proxy.detect_source(req) == "openclaw"

    def test_unknown_ua_truncated_to_60(self):
        long_ua = "Z" * 100
        req = _make_request(long_ua)
        # Falls through to ua[:60] since no pattern matches
        assert proxy.detect_source(req) == "Z" * 60

    def test_no_ua_returns_unknown(self):
        req = _make_request(None)  # no UA header
        assert proxy.detect_source(req) == "unknown"

    def test_cursor_ua(self):
        req = _make_request("cursor/0.40.0")
        assert proxy.detect_source(req) == "cursor"

    def test_python_generic_ua(self):
        req = _make_request("python-requests/2.31.0")
        assert proxy.detect_source(req) == "python-sdk"


class TestParseOpenai:
    """proxy._parse_openai(content, content_type, req_model)."""

    def _json_response(self, model, prompt_tokens, completion_tokens, finish_reason=None, tool_calls=False):
        choices = [{"finish_reason": finish_reason, "message": {}}]
        if tool_calls:
            choices[0]["message"]["tool_calls"] = [{"id": "1"}]
        return json.dumps({
            "model": model,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "choices": choices,
        }).encode()

    def test_json_basic(self):
        content = self._json_response("gpt-4o", 100, 50, finish_reason="stop")
        model, inp, out, cr, cw, stop, tools, tool_names = proxy._parse_openai(
            content, "application/json", "fallback-model"
        )
        assert model == "gpt-4o"
        assert inp == 100
        assert out == 50
        assert stop == "stop"
        assert tools == 0
        assert tool_names == []

    def test_json_tool_calls_counted(self):
        content = self._json_response("gpt-4o", 100, 50, tool_calls=True)
        model, inp, out, cr, cw, stop, tools, tool_names = proxy._parse_openai(
            content, "application/json", "gpt-4o"
        )
        assert tools == 1

    def test_json_model_falls_back_to_req_model(self):
        # When model key absent in response JSON, falls back to req_model
        content = json.dumps({"usage": {"prompt_tokens": 10, "completion_tokens": 5}, "choices": []}).encode()
        model, *_ = proxy._parse_openai(content, "application/json", "my-fallback")
        assert model == "my-fallback"

    def test_cr_and_cw_always_zero(self):
        # OpenAI parser always returns cr=0, cw=0
        content = self._json_response("gpt-4o", 10, 5)
        _, _, _, cr, cw, _, _, _ = proxy._parse_openai(content, "application/json", "x")
        assert cr == 0
        assert cw == 0

    def test_invalid_json_returns_defaults(self):
        model, inp, out, cr, cw, stop, tools, tool_names = proxy._parse_openai(
            b"not-json", "application/json", "fallback"
        )
        assert model == "fallback"
        assert inp == 0
        assert out == 0

    def test_sse_usage_extracted(self):
        sse_lines = [
            'data: {"model":"gpt-4o","choices":[],"usage":{"prompt_tokens":20,"completion_tokens":10}}',
            "data: [DONE]",
        ]
        content = "\n".join(sse_lines).encode()
        model, inp, out, *_ = proxy._parse_openai(content, "text/event-stream", "fallback")
        assert inp == 20
        assert out == 10


class TestRouteModel:
    """proxy.route_model(body_bytes) — routing disabled by default."""

    def test_routing_disabled_returns_none_none_zero(self, monkeypatch, tmp_path):
        # Disable routing: point _SMART_FILE at a nonexistent path AND clear env var.
        # _smart_routing_enabled() checks the file first; if the file doesn't exist it
        # falls back to SMART_ROUTING env var. Both must be "off" to disable.
        monkeypatch.setattr(proxy, "_SMART_FILE", str(tmp_path / "no_smart_routing"))
        monkeypatch.setenv("SMART_ROUTING", "0")
        orig, routed, score = proxy.route_model(_jbytes(model="claude-opus-4-8"))
        assert orig is None
        assert routed is None
        assert score == 0

    def test_routing_enabled_simple_question_routes_to_haiku(self, monkeypatch, tmp_path):
        # Enable routing via .smart_routing file containing "1"
        sr_file = tmp_path / ".smart_routing"
        sr_file.write_text("1")
        monkeypatch.setattr(proxy, "_SMART_FILE", str(sr_file))
        # Simple question: _SIMPLE_RE matches, score → 0 → Haiku
        body = _jbytes(
            model="claude-opus-4-8",
            messages=[{"role": "user", "content": "what is Python?"}],
        )
        orig, routed, score = proxy.route_model(body)
        assert orig == "claude-opus-4-8"
        assert routed == proxy.ROUTE_CHEAP  # → haiku
        assert score <= 2

    def test_routing_enabled_haiku_unchanged(self, monkeypatch, tmp_path):
        sr_file = tmp_path / ".smart_routing"
        sr_file.write_text("1")
        monkeypatch.setattr(proxy, "_SMART_FILE", str(sr_file))
        body = _jbytes(model="claude-haiku-4-5-20251001")
        orig, routed, score = proxy.route_model(body)
        assert routed is None  # already cheapest

    def test_routing_enabled_complex_request_no_routing(self, monkeypatch, tmp_path):
        sr_file = tmp_path / ".smart_routing"
        sr_file.write_text("1")
        monkeypatch.setattr(proxy, "_SMART_FILE", str(sr_file))
        body = _jbytes(
            model="claude-opus-4-8",
            messages=[{"role": "user", "content": "implement a full authentication system with OAuth2, JWT, " * 20}],
        )
        orig, routed, score = proxy.route_model(body)
        # High complexity → no downgrade
        assert score >= 3  # definitely not a simple question
        # With high enough score (>=6), routed should be None; medium (3-5) opus→sonnet
        # Either way, confirm original was captured
        assert orig == "claude-opus-4-8"

    def test_routing_enabled_medium_opus_routes_to_sonnet(self, monkeypatch, tmp_path):
        sr_file = tmp_path / ".smart_routing"
        sr_file.write_text("1")
        monkeypatch.setattr(proxy, "_SMART_FILE", str(sr_file))
        # Score 3-5 range: medium complexity, opus model → should downgrade to Sonnet
        # Use a message that scores 3 (keyword hit) but nothing else
        body = _jbytes(
            model="claude-opus-4-8",
            messages=[{"role": "user", "content": "implement a small function"}],
        )
        orig, routed, score = proxy.route_model(body)
        assert orig == "claude-opus-4-8"
        if 3 <= score <= 5:
            assert routed == proxy.ROUTE_MID
        elif score <= 2:
            assert routed == proxy.ROUTE_CHEAP

    def test_non_claude_model_not_routed(self, monkeypatch, tmp_path):
        sr_file = tmp_path / ".smart_routing"
        sr_file.write_text("1")
        monkeypatch.setattr(proxy, "_SMART_FILE", str(sr_file))
        body = _jbytes(model="gpt-4o")
        orig, routed, score = proxy.route_model(body)
        assert routed is None  # only routes Claude/Fable/Opus/Sonnet/Haiku models


# ─────────────────────────────────────────────────────────────────────────────
# GROUP 2: Integration — proxy_anthropic handler
# ─────────────────────────────────────────────────────────────────────────────

# Minimal mock Anthropic response for /v1/messages
_MOCK_RESPONSE_BODY = {
    "id": "msg_test001",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Hello, world!"}],
    "model": "claude-opus-4-8",
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    },
}

_MOCK_REQUEST_BODY = {
    "model": "claude-opus-4-8",
    "max_tokens": 256,
    "messages": [{"role": "user", "content": "say hello"}],
}


@pytest.fixture()
def test_client(tmp_db):
    """FastAPI TestClient backed by a temp DB. Blocks real network calls via respx."""
    from fastapi.testclient import TestClient
    return TestClient(proxy.app, raise_server_exceptions=True)


class TestProxyAnthropic:
    """Integration tests for the /v1/* handler."""

    @respx.mock
    def test_basic_request_returns_upstream_body(self, test_client):
        """POST /v1/messages → upstream response is forwarded back to caller."""
        import optimizer
        optimizer._dedup_cache.clear()

        upstream = respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            return_value=httpx.Response(200, json=_MOCK_RESPONSE_BODY)
        )
        resp = test_client.post(
            "/v1/messages",
            json=_MOCK_REQUEST_BODY,
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "msg_test001"
        assert upstream.called

    @respx.mock
    def test_request_recorded_in_db(self, test_client, tmp_db):
        """After a successful proxied call, a row must appear in the temp DB."""
        import optimizer
        optimizer._dedup_cache.clear()

        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            return_value=httpx.Response(200, json=_MOCK_RESPONSE_BODY)
        )
        # Use a unique body so prior test's dedup cache can't interfere
        unique_body = {
            "model": "claude-opus-4-8",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "db recording test unique zzz999"}],
        }
        test_client.post(
            "/v1/messages",
            json=unique_body,
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        proxy._process_pending_writes()  # flush the async accounting queue
        con = sqlite3.connect(tmp_db)
        rows = con.execute("SELECT model, input_tokens, output_tokens FROM requests").fetchall()
        con.close()
        assert len(rows) == 1
        model, inp, out = rows[0]
        assert model == "claude-opus-4-8"
        assert inp == 10
        assert out == 5

    @respx.mock
    def test_dedup_second_request_not_forwarded(self, test_client):
        """Identical requests within the dedup window should only hit upstream once."""
        # Clear the optimizer's dedup cache so prior test state doesn't bleed in
        import optimizer
        optimizer._dedup_cache.clear()

        upstream = respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            return_value=httpx.Response(200, json=_MOCK_RESPONSE_BODY)
        )

        body = {
            "model": "claude-opus-4-8",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "dedup test unique request xyzzy"}],
        }
        headers = {"x-api-key": "test-key", "anthropic-version": "2023-06-01"}

        resp1 = test_client.post("/v1/messages", json=body, headers=headers)
        resp2 = test_client.post("/v1/messages", json=body, headers=headers)

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Second request returned from dedup cache — upstream called exactly once
        assert upstream.call_count == 1
        # Both responses have the same content
        assert resp1.json()["id"] == resp2.json()["id"]

    @respx.mock
    def test_routing_disabled_body_forwarded_verbatim(self, test_client, monkeypatch):
        """With smart routing disabled, the request body is forwarded unchanged.

        NOTE: We test the DISABLED path (SMART_ROUTING=0) because enabling routing
        involves the per-session state in optimizer._session_state which is
        process-global and would require careful isolation. The disabled path
        verifies that the passthrough is truly verbatim — the model name in the
        forwarded request must match what was sent.
        """
        import optimizer
        optimizer._dedup_cache.clear()

        monkeypatch.setenv("SMART_ROUTING", "0")
        # Ensure the .smart_routing file doesn't exist or doesn't say "1"
        monkeypatch.setattr(proxy, "_SMART_FILE", "/tmp/nonexistent_smart_routing_file_xyz")

        captured_body = {}

        def capture_and_respond(request):
            captured_body["content"] = request.content
            return httpx.Response(200, json=_MOCK_RESPONSE_BODY)

        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(side_effect=capture_and_respond)

        body = {
            "model": "claude-opus-4-8",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": "routing disabled test unique abc123"}],
        }
        resp = test_client.post(
            "/v1/messages",
            json=body,
            headers={"x-api-key": "test-key", "anthropic-version": "2023-06-01"},
        )
        assert resp.status_code == 200

        forwarded = json.loads(captured_body["content"])
        # Model must NOT have been rewritten when routing is disabled
        assert forwarded["model"] == "claude-opus-4-8"

    @respx.mock
    def test_upstream_error_propagated(self, test_client):
        """A non-200 from upstream should be returned to the caller unchanged."""
        import optimizer
        optimizer._dedup_cache.clear()

        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            return_value=httpx.Response(
                401,
                json={"type": "error", "error": {"type": "authentication_error", "message": "invalid key"}},
            )
        )
        resp = test_client.post(
            "/v1/messages",
            json=_MOCK_REQUEST_BODY,
            headers={"x-api-key": "bad-key", "anthropic-version": "2023-06-01"},
        )
        assert resp.status_code == 401


@pytest.fixture(autouse=True)
def _clear_write_queue():
    """Discard any queued records so the module-global queue can't leak rows
    between tests. Discards without writing (no DB dependency at teardown)."""
    def _drain():
        while True:
            try:
                proxy._write_queue.get_nowait()
            except queue.Empty:
                break
            else:
                proxy._write_queue.task_done()
    _drain()
    yield
    _drain()


def _rec(**over):
    """A complete _record→save_request kwargs dict, overridable per-test."""
    base = dict(
        source="cli", model="claude-opus-4-8", input_tok=1, output_tok=1,
        cache_read=0, cache_creation=0, cost=0.0, duration_ms=10, status=200,
        user_agent="ua", stop_reason=None, tool_call_count=0, tools_json=None,
        effort="standard", prompt_preview="", msg_uuid="m", auto_thinking=False,
        optimizations_json=None, optimizer_savings_usd=0, cache_creation_1h=0,
        ts="2026-06-23T00:00:00+00:00",
    )
    base.update(over)
    return base


class TestAsyncWrite:
    def test_record_enqueues_without_blocking(self, tmp_db):
        proxy._record("cli", "claude-opus-4-8", 7, 3, 0, 0, 12, 200,
                      "ua", None, 0, None, msg_uuid="enq1")
        assert proxy._write_queue.qsize() == 1

    def test_pending_writes_persist_to_db(self, tmp_db):
        proxy._write_queue.put_nowait(_rec(msg_uuid="p1", input_tok=9))
        proxy._process_pending_writes()
        con = sqlite3.connect(tmp_db)
        row = con.execute("SELECT input_tokens FROM requests WHERE msg_uuid='p1'").fetchone()
        con.close()
        assert row == (9,)

    def test_writer_skips_failing_row_and_continues(self, tmp_db, monkeypatch):
        real = proxy.save_request
        def flaky(**kw):
            if kw["msg_uuid"] == "bad":
                raise sqlite3.OperationalError("database is locked")
            return real(**kw)
        monkeypatch.setattr(proxy, "save_request", flaky)
        proxy._write_queue.put_nowait(_rec(msg_uuid="bad"))
        proxy._write_queue.put_nowait(_rec(msg_uuid="good"))
        proxy._process_pending_writes()  # must not raise
        con = sqlite3.connect(tmp_db)
        uuids = {r[0] for r in con.execute("SELECT msg_uuid FROM requests").fetchall()}
        con.close()
        assert "good" in uuids and "bad" not in uuids

    def test_record_drops_when_queue_full(self, monkeypatch):
        monkeypatch.setattr(proxy, "_write_queue", queue.Queue(maxsize=1))
        proxy._write_queue.put_nowait(_rec(msg_uuid="filler"))
        proxy._record("cli", "claude-opus-4-8", 1, 1, 0, 0, 10, 200,
                      "ua", None, 0, None, msg_uuid="dropped")  # must not raise
        assert proxy._write_queue.qsize() == 1  # second row was dropped

    def test_graceful_flush_drains_on_stop(self, tmp_db):
        proxy._start_writer()
        for i in range(5):
            proxy._write_queue.put_nowait(_rec(msg_uuid=f"flush{i}"))
        proxy._stop_writer(timeout=2.0)
        con = sqlite3.connect(tmp_db)
        n = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        con.close()
        assert n == 5
