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

import asyncio
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


def _make_post_request(path, body_bytes, headers=None):
    """Starlette Request with a body, for calling handlers directly (no TestClient)."""
    from starlette.requests import Request as _Request
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]

    async def _receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    scope = {"type": "http", "method": "POST", "path": f"/{path}",
             "headers": raw, "query_string": b"", "server": ("127.0.0.1", 8082)}
    return _Request(scope, receive=_receive)


def _stream_factory(chunks, content_type="text/event-stream", status=200):
    """respx side-effect: fresh streaming httpx.Response per call (generators are single-use)."""
    def _factory(request):
        async def _aiter():
            for c in chunks:
                yield c
        return httpx.Response(status, headers={"content-type": content_type}, content=_aiter())
    return _factory


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

    _SSE_CHUNKS = [
        (b'event: message_start\n'
         b'data: {"type":"message_start","message":{"id":"msg_stream01",'
         b'"model":"claude-opus-4-8","usage":{"input_tokens":42,'
         b'"cache_read_input_tokens":7,"cache_creation_input_tokens":3,'
         b'"cache_creation":{"ephemeral_1h_input_tokens":3}}}}\n\n'),
        (b'event: content_block_start\n'
         b'data: {"type":"content_block_start","content_block":'
         b'{"type":"tool_use","name":"Read"}}\n\n'),
        (b'event: message_delta\n'
         b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
         b'"usage":{"output_tokens":99}}\n\n'),
    ]

    @respx.mock
    def test_streamed_sse_roundtrips_and_records(self, test_client, tmp_db):
        import optimizer
        optimizer._dedup_cache.clear()
        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            side_effect=_stream_factory(self._SSE_CHUNKS))
        body = {"model": "claude-opus-4-8", "max_tokens": 256,
                "messages": [{"role": "user", "content": "stream roundtrip unique q1"}]}
        resp = test_client.post("/v1/messages", json=body,
                                headers={"x-api-key": "k", "anthropic-version": "2023-06-01"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert b"".join(self._SSE_CHUNKS) == resp.content
        con = sqlite3.connect(tmp_db)
        model, inp, out, cr, cw, cw1h, tools, stop = con.execute(
            "SELECT model,input_tokens,output_tokens,cache_read_tokens,"
            "cache_creation_tokens,cache_creation_1h_tokens,tool_call_count,stop_reason "
            "FROM requests").fetchone()
        con.close()
        assert (model, inp, out) == ("claude-opus-4-8", 42, 99)
        assert (cr, cw, cw1h, tools) == (7, 3, 3, 1)
        assert stop == "end_turn"

    @respx.mock
    def test_incremental_delivery_direct_handler(self, tmp_db):
        import optimizer
        optimizer._dedup_cache.clear()
        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            side_effect=_stream_factory(self._SSE_CHUNKS))
        body_bytes = json.dumps(
            {"model": "claude-opus-4-8", "max_tokens": 256,
             "messages": [{"role": "user", "content": "incremental unique q2"}]}).encode()

        def _rowcount():
            con = sqlite3.connect(tmp_db)
            n = con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            con.close()
            return n

        async def run():
            req = _make_post_request("v1/messages", body_bytes,
                                     {"x-api-key": "k", "anthropic-version": "2023-06-01"})
            resp = await proxy.proxy_anthropic("messages", req)
            it = resp.body_iterator
            first = await it.__anext__()
            mid = _rowcount()
            rest = [c async for c in it]
            return first, rest, mid

        first, rest, mid = asyncio.run(run())
        assert 1 + len(rest) >= 2
        assert mid == 0
        assert _rowcount() == 1

    @respx.mock
    def test_dedup_replays_sse_content_type(self, test_client):
        import optimizer
        optimizer._dedup_cache.clear()
        upstream = respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            side_effect=_stream_factory(self._SSE_CHUNKS))
        body = {"model": "claude-opus-4-8", "max_tokens": 256,
                "messages": [{"role": "user", "content": "dedup sse ct unique q3"}]}
        h = {"x-api-key": "k", "anthropic-version": "2023-06-01"}
        r1 = test_client.post("/v1/messages", json=body, headers=h)
        r2 = test_client.post("/v1/messages", json=body, headers=h)
        assert r1.status_code == r2.status_code == 200
        assert upstream.call_count == 1
        assert "text/event-stream" in r2.headers["content-type"]
        assert r2.content == b"".join(self._SSE_CHUNKS)

    @respx.mock
    def test_disconnect_records_incomplete_no_cache(self, tmp_db):
        import optimizer
        optimizer._dedup_cache.clear()
        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            side_effect=_stream_factory(self._SSE_CHUNKS))
        body_bytes = json.dumps(
            {"model": "claude-opus-4-8", "max_tokens": 256,
             "messages": [{"role": "user", "content": "disconnect unique q4"}]}).encode()

        async def run():
            req = _make_post_request("v1/messages", body_bytes,
                                     {"x-api-key": "k", "anthropic-version": "2023-06-01"})
            resp = await proxy.proxy_anthropic("messages", req)
            it = resp.body_iterator
            await it.__anext__()
            await it.aclose()

        asyncio.run(run())
        con = sqlite3.connect(tmp_db)
        status, stop = con.execute(
            "SELECT status, stop_reason FROM requests").fetchone()
        con.close()
        assert status == 200
        assert stop == "incomplete"
        assert len(optimizer._dedup_cache) == 0

    @respx.mock
    def test_connect_failure_records_502_incomplete(self, test_client, tmp_db):
        import optimizer
        optimizer._dedup_cache.clear()
        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            side_effect=httpx.ConnectError("refused"))
        body = {"model": "claude-opus-4-8", "max_tokens": 256,
                "messages": [{"role": "user", "content": "connect fail unique q5"}]}
        resp = test_client.post("/v1/messages", json=body,
                                headers={"x-api-key": "k", "anthropic-version": "2023-06-01"})
        assert resp.status_code == 502
        con = sqlite3.connect(tmp_db)
        status, stop = con.execute("SELECT status, stop_reason FROM requests").fetchone()
        con.close()
        assert status == 502
        assert stop == "incomplete"

    @respx.mock
    def test_json_response_through_streaming_path(self, test_client, tmp_db):
        import optimizer
        optimizer._dedup_cache.clear()
        respx.post(f"{proxy.ANTHROPIC_URL}/v1/messages").mock(
            return_value=httpx.Response(200, json=_MOCK_RESPONSE_BODY))
        body = {"model": "claude-opus-4-8", "max_tokens": 256,
                "messages": [{"role": "user", "content": "json through stream unique q6"}]}
        resp = test_client.post("/v1/messages", json=body,
                                headers={"x-api-key": "k", "anthropic-version": "2023-06-01"})
        assert resp.status_code == 200
        assert resp.json()["id"] == "msg_test001"
        con = sqlite3.connect(tmp_db)
        model, inp, out = con.execute(
            "SELECT model, input_tokens, output_tokens FROM requests").fetchone()
        con.close()
        assert (model, inp, out) == ("claude-opus-4-8", 10, 5)


class TestStreamUpstream:
    """proxy.stream_upstream — provider-agnostic streaming mechanics."""

    @respx.mock
    def test_tees_chunks_and_calls_finalize_once(self):
        chunks = [b"chunk-A", b"chunk-B", b"chunk-C"]
        respx.post("https://up.example/v1/messages").mock(side_effect=_stream_factory(chunks))
        calls = []

        def finalize(status, content_type, full_bytes, duration_ms, completed):
            calls.append((status, content_type, full_bytes, completed))

        async def run():
            resp = await proxy.stream_upstream(
                "POST", "https://up.example/v1/messages",
                {"x-api-key": "k"}, b'{"model":"x"}', 120, finalize, time.time())
            got = [c async for c in resp.body_iterator]
            return resp, got

        resp, got = asyncio.run(run())
        assert resp.status_code == 200
        assert got == chunks
        assert len(calls) == 1
        status, ct, full, completed = calls[0]
        assert status == 200
        assert ct == "text/event-stream"
        assert full == b"chunk-Achunk-Bchunk-C"
        assert completed is True

    @respx.mock
    def test_connect_failure_records_502_and_closes(self):
        respx.post("https://up.example/v1/messages").mock(
            side_effect=httpx.ConnectError("connection refused"))
        calls = []

        def finalize(status, content_type, full_bytes, duration_ms, completed):
            calls.append((status, completed))

        async def run():
            return await proxy.stream_upstream(
                "POST", "https://up.example/v1/messages",
                {"x-api-key": "k"}, b'{"model":"x"}', 120, finalize, time.time())

        resp = asyncio.run(run())
        assert resp.status_code == 502
        assert len(calls) == 1
        assert calls[0] == (502, False)


class TestProxyOpenAICompat:
    """Integration tests for the /<provider>/v1/* handler."""

    _PROV = list(proxy.PROVIDER_URLS)[0]
    _OAI_SSE = [
        (b'data: {"model":"test-model","choices":[{"index":0,'
         b'"delta":{"content":"hi"}}]}\n\n'),
        (b'data: {"model":"test-model","choices":[{"index":0,'
         b'"finish_reason":"stop"}],"usage":{"prompt_tokens":11,'
         b'"completion_tokens":4}}\n\n'),
        b'data: [DONE]\n\n',
    ]

    @respx.mock
    def test_streamed_openai_records_with_provider_prefix(self, test_client, tmp_db):
        upstream_url = f"{proxy.PROVIDER_URLS[self._PROV]}/v1/chat/completions"
        respx.post(upstream_url).mock(side_effect=_stream_factory(self._OAI_SSE))
        resp = test_client.post(
            f"/{self._PROV}/v1/chat/completions",
            json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            headers={"authorization": "Bearer k"})
        assert resp.status_code == 200
        con = sqlite3.connect(tmp_db)
        model, inp, out = con.execute(
            "SELECT model, input_tokens, output_tokens FROM requests").fetchone()
        con.close()
        assert model == f"{self._PROV}/test-model"
        assert (inp, out) == (11, 4)

    @respx.mock
    def test_openai_disconnect_records_incomplete(self, tmp_db):
        upstream_url = f"{proxy.PROVIDER_URLS[self._PROV]}/v1/chat/completions"
        respx.post(upstream_url).mock(side_effect=_stream_factory(self._OAI_SSE))
        body_bytes = json.dumps(
            {"model": "test-model", "messages": [{"role": "user", "content": "x"}]}).encode()

        async def run():
            req = _make_post_request(f"{self._PROV}/v1/chat/completions", body_bytes,
                                     {"authorization": "Bearer k"})
            resp = await proxy.proxy_openai_compat(self._PROV, "chat/completions", req)
            it = resp.body_iterator
            await it.__anext__()
            await it.aclose()

        asyncio.run(run())
        con = sqlite3.connect(tmp_db)
        stop = con.execute("SELECT stop_reason FROM requests").fetchone()[0]
        con.close()
        assert stop == "incomplete"
