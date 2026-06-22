# Design: Stream upstream LLM responses through the proxy

**Date:** 2026-06-22
**Status:** Approved (design) — implementation pending
**Branch (planned):** off `main` @ v1.1.5
**Related:** `docs/adr/0001-proxy-request-mutation-boundary.md`, `CONTEXT.md`

## Problem

`proxy.py` **buffers** the entire upstream LLM response before returning any
bytes to the client. On a long request this sends zero bytes until the upstream
response is 100% complete, so a streaming client (Claude Code, and any client
sending `stream: true`) hits its idle/stream timeout and aborts with
`API error · Retrying` — even though the proxy then completes and records a
`200` for the request the client already abandoned.

Confirmed in code:

- `proxy_anthropic` (`proxy.py:676`) buffers at `:731-732`
  (`httpx.AsyncClient(timeout=120)` → `resp = await client.request(...)`) and
  returns all-at-once at `:761` (`Response(content=resp.content, ...)`).
- `proxy_openai_compat` (`proxy.py:796`) has the identical pattern (buffer
  `:823`, return `:844`).
- `proxy_anthropic_oauth` (`proxy.py:773`) also buffers, but carries only tiny
  non-streaming subscription-usage polls and records no usage.

Confirmed behavior: `tracker.db` recorded 200s at 607s/428s/312s/200s durations;
removing `ANTHROPIC_BASE_URL` (bypassing the proxy → true end-to-end streaming)
fixed the user's sessions. This bug is pre-existing in upstream `mr-beaver/main`.

**Verified facts (claude-api docs):**

1. The Anthropic SSE event shape the proxy parses is the documented streaming
   format: `message_start` carries `message.usage` (input / cache-read /
   cache-creation / `cache_creation.ephemeral_1h_input_tokens`); `message_delta`
   carries cumulative `usage.output_tokens` + `delta.stop_reason`;
   `content_block_start` carries `tool_use`. `_parse_anthropic` (`proxy.py:502`)
   already keys on exactly these and parses the full concatenated buffer.
2. Claude Code's streaming timeout is **idle/per-read, not total-duration**. The
   SDK guard refuses non-streaming requests it estimates will exceed ~10 min
   because idle connections drop. Streaming bytes through resets the idle timer
   continuously, which is the fix.

## Goal

Stream upstream response bytes to the client incrementally while still capturing
the full body for usage accounting, optimizer savings, cache-state tracking, and
dedup caching — without re-introducing a total-duration timeout cap.

## Non-goals

- No change to request-side handling (source detection, effort, preview, smart
  routing, optimizer, body mutation) — the ADR-0001 request-mutation boundary is
  untouched. Only the response side changes.
- No change to `_parse_anthropic` / `_parse_openai` — they already parse the full
  SSE/JSON buffer.
- The `/api/oauth/*` passthrough stays buffered (tiny non-streaming polls, no
  usage row) — out of scope.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | `/v1/*` (`proxy_anthropic`) + OpenAI-compat (`proxy_openai_compat`) via one shared helper | Both real-streaming handlers have the identical bug; a shared helper makes covering both ~the same work as one. `/api/oauth/*` left buffered. |
| Parse strategy | Parse-after (accumulate full buffer, parse once in `finalize`) | `_parse_anthropic`/`_parse_openai` already work on the full buffer; no tee-while-parsing complexity. |
| Client disconnect | Best-effort partial record in `finally`; dedup cache **only** on `completed and status==200` | Single code path; captures input-token reality (`message_start` arrives first). A partial/abandoned body is never a valid response to replay, so it must not be cached. |
| JSON vs SSE | Same path for both — no content-type branching (**default; flagged for maintainer review**, see note below) | The parser already detects `text/event-stream` vs JSON; a JSON body simply arrives as one/few chunks. |
| httpx timeout | Keep per-read (`timeout=120`); **never** a total-duration cap | Per-read resets on each chunk — a 600s stream that keeps emitting never trips it; a hung upstream does. |
| Partial-stream representation | On `completed=False`, stamp `stop_reason="incomplete"` when the parsed `stop_reason` is `NULL` | Head was 200 so `status=200` is truthful; without a marker a disconnected/errored stream masquerades as a clean short 200 and undercounts usage. Sentinel needs **no schema migration**, is queryable, and is grep-confirmed safe across `db.py` aggregations (new bucket only) and `dashboard.html` `stopBadge` (graceful fallthrough). A dedicated `completed` column was rejected as scope creep. Residual output-token undercount on disconnect is accepted (those tokens were never received). |

### Open question for maintainer review: uniform streaming vs. branch

The no-branching decision means **every** response through these handlers
(`count_tokens`, model-list `GET`s, `stream:false` completions, error bodies) goes
from a buffered `Response` with `Content-Length` to a chunked `StreamingResponse` —
the blast radius is *all* `/v1/*` + OpenAI-compat traffic, not just streaming
clients. The response head (status, content-type, presence of upstream
`Content-Length`) is known after `send()` and before the body, so a branch is
cheaply available: *"if not `text/event-stream` and `Content-Length` present, buffer
and return as today; else stream."*

- **Recommended default (this design): uniform streaming.** Chunked delivery of a
  small JSON body is universally handled by HTTP clients; one code path avoids the
  dual-path drift ADR-0001 was written to kill; the accounting pipeline already runs
  for every response, so it's parity there.
- **Accepted trade-off:** a client or chained proxy that *requires* `Content-Length`
  on a non-streaming endpoint would now see chunked encoding.
- **Escape hatch if the maintainer prefers minimal behavior change:** branch on the
  response head to keep non-streaming responses buffered. Reversible later — not a
  one-way door.

Deferred to the project owner (`mr-beaver`) to confirm on the PR.

## Architecture

Split **streaming mechanics** (identical everywhere) from **accounting**
(handler-specific).

### Shared helper (`proxy.py`)

```python
async def stream_upstream(method, url, headers, body_bytes, timeout, finalize):
    """Open an upstream streaming request and return a StreamingResponse that
    tees each chunk to the client while accumulating the full body. After the
    stream ends — cleanly, on client disconnect, or on upstream error —
    `finalize(status, content_type, full_bytes, duration_ms, completed)` is
    called exactly once for usage accounting + dedup caching."""
```

- Helper is provider-agnostic: stream bytes, hand the assembled body to a callback.
- Each handler passes its own `finalize` closure running the logic it already has:
  - `proxy_anthropic`: `_parse_anthropic` (incl. 1h-cache split) → `record_cache_state`
    → `calculate_optimization_savings` → `_record` → dedup-cache (if `completed and status==200`).
  - `proxy_openai_compat`: `_parse_openai` → provider-prefix tagging → `_record`.
- Keeps each handler's domain logic where it lives (ADR-0001); streaming change is
  one well-tested unit.

The dedup short-circuit (`proxy.py:684`) still returns a buffered `Response`
immediately on a cache hit — but its **content-type handling changes**. Today the
hit hardcodes `content-type: application/json` and `dedup_cache_response` stores
only body bytes (`optimizer.py:180`). Once finalize caches completed streams, the
cached body is commonly `text/event-stream`, so replaying it as JSON hands a
streaming client (the exact traffic this change targets, which retries) a malformed
body. Fix: widen the cache entry to carry the content-type —
`_dedup_cache[req_hash] = (response, content_type, now)` — and replay with the
stored `media_type`. A buffered SSE body replayed with the correct content-type is
safe because a dedup hit is instant (no idle-timeout concern). This touches
`dedup_cache_response`, `dedup_check`, and the short-circuit return (~3 lines), and
each handler's `finalize` passes the captured `content_type` when caching.

### Data flow & lifecycle

```
t0 = now()
client = httpx.AsyncClient(timeout=…)          # NOT async-with — generator owns lifecycle
resp  = await client.send(build_request(...), stream=True)
status, content_type = resp.status_code, resp.headers.get("content-type", "")  # head available pre-body

return StreamingResponse(gen(), status_code=status, media_type=content_type, headers=passthrough)

async def gen():
    buf, completed = bytearray(), False
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk          # client first — zero added latency
            buf.extend(chunk)    # tee
        completed = True
    finally:
        await resp.aclose(); await client.aclose()
        try:
            finalize(status, content_type, bytes(buf),
                     int((now()-t0)*1000), completed)
        except Exception:
            pass                 # bookkeeping must never break the response
```

Why this shape:

- **`client.send(..., stream=True)` not `async with client.stream(...)`** — the
  `async with` form closes the client when the handler returns, before the
  generator runs. The generator owns client/response and closes both in `finally`.
- **Status + content-type known before the body streams** (httpx returns the head
  after `send`), so `StreamingResponse` gets correct status/media-type upfront;
  upstream errors (401, etc.) propagate correctly.
- **`finalize` runs after the last byte is yielded** — accounting adds no
  client-visible latency. It runs on every exit path (clean / disconnect /
  upstream error) via `finally`.

## Edge cases

1. **JSON (non-streaming) responses** (`count_tokens`, `stream:false`, model
   lists, errors) use the same path — no branching. Body arrives as one/few
   chunks; `finalize` calls the same parser (detects JSON vs SSE via `content_type`).
2. **Upstream error mid-stream** — `aiter_bytes()` raises; exception propagates
   out of the generator (correct: terminates the client connection, signalling an
   incomplete response). `finally` still runs: `completed=False`, partial record
   (`stop_reason="incomplete"` when parsed reason is `NULL`), **skip** dedup cache.
   We do not swallow the exception (that would make a truncated stream look clean).
3. **Upstream non-200 (401/429/5xx)** — comes back as the response head before
   any body; `StreamingResponse` built with the right status; small JSON error
   body streams through; `finalize` records the real status. Preserves current
   `test_upstream_error_propagated` behavior.
4. **Headers** — keep the existing response skip-set: strip `content-encoding`,
   `content-length`, `transfer-encoding`. `content-length` is unknown when
   streaming; `StreamingResponse` sets chunked `transfer-encoding` itself. Safe to
   forward raw bytes because the request side already strips `accept-encoding`
   (`proxy.py:678`) → upstream returns identity-coded bytes.
5. **httpx timeout** — keep per-read `timeout=120` (or explicit
   `httpx.Timeout(connect=…, read=120, write=…, pool=…)`). **No total-duration
   cap.** Add a code comment so it isn't "tidied" back to a total timeout later.
6. **Client disconnect (`GeneratorExit`)** — Starlette calls `.aclose()` on the
   generator, raising `GeneratorExit` at the `yield`. `finally` runs (awaits
   during async-gen close are permitted as long as we don't `yield`): close
   upstream, partial record (`stop_reason="incomplete"`), skip cache. `completed=False`.
7. **`finalize` wrapped in try/except** — accounting (DB write, optimizer, parse)
   must never propagate an exception that corrupts the response lifecycle
   (matches the existing `except Exception: pass` philosophy around optimizer calls).
8. **Connect-time / pre-body transport failure** (DNS, connection refused, connect
   timeout) — `client.send(..., stream=True)` raises in the handler body *before*
   `StreamingResponse`/`gen()` exist, so `finalize` never runs and the client (not
   opened via `async with`) would leak. Wrap `send()` in try/except: on failure
   `await client.aclose()`, write a best-effort `completed=False` record (status 502),
   and return a small JSON error `Response` with a real status. This is the one
   failure mode the streaming shape introduces that the buffered `async with` form
   handled implicitly (it at least closed the client).

## Test plan (TDD, respx)

Existing integration tests use buffered `httpx.Response(200, json=…)` mocks; most
keep passing (a JSON body still round-trips through the streaming path) but no
longer prove streaming. Write streaming assertions first.

New tests in `tests/test_proxy.py`:

- **Incremental delivery (direct-handler)** — the test that earns the name. Call
  `proxy_anthropic` directly with a constructed `Request` (per the existing
  `TestDetectSource` precedent — bypasses the pytest-anyio TestClient interference),
  get the `StreamingResponse` back, and iterate `response.body_iterator`. respx mock
  yields N discrete chunks via an `aiter_bytes` side-effect; assert the iterator
  **yields ≥2 times** and that `finalize`'s DB row is **not** written until the
  iterator is exhausted. This proves tee-while-streaming and guards against silent
  re-buffering (a buffered body would still reassemble and record — only this test
  fails if someone reintroduces buffering).
- **Streamed response round-trips and records (TestClient)** — realistic full-path
  coverage: respx streamed mock emitting `message_start` … `message_delta`; assert
  the handler returns a `text/event-stream` response, body reassembles correctly,
  and a DB row with parsed usage is written. (TestClient consumes the whole stream,
  so this proves correct reassembly + recording, not incremental arrival — that's
  the direct-handler test above.)
- **SSE usage accounting through streaming** — multi-event SSE body; assert the
  row has model (from `message_start`), `output_tokens` (from `message_delta`),
  cache-read/creation + 1h-split, tool counts (from `content_block_start`) —
  proving `finalize` parses the teed buffer identically to the old buffered parse.
- **Dedup still works** — stream a 200 to completion; assert full body is
  dedup-cached and a second identical request is served from cache (one upstream call).
- **Disconnect → partial record, no cache** — consumer abandons mid-stream; assert
  a best-effort row is written with `stop_reason="incomplete"`, `status=200`, and
  the dedup cache is **not** populated (next identical request hits upstream again).
- **Upstream error mid-stream** — connection terminates; partial row recorded with
  the `"incomplete"` sentinel; no cache entry.
- **Connect-time failure** (Q1) — respx mock raises a transport error on `send()`;
  assert the client is closed (no leak), a best-effort `completed=False` row is
  recorded (status 502, `stop_reason="incomplete"`), and a JSON error `Response`
  with a real status is returned — not a raw 500.
- **Dedup content-type round-trips** (Q3) — stream a 200 `text/event-stream` body to
  completion, then issue an identical request; assert the dedup hit replays with
  `content-type: text/event-stream` (not the old hardcoded `application/json`) and
  no second upstream call.
- **Non-200 propagates** — keep/adapt `test_upstream_error_propagated`.
- **JSON (non-SSE) response** still records correctly through the streaming path
  (guards the no-branching decision).
- **OpenAI-compat streaming** — at least one test exercising the `_parse_openai`
  path + provider-prefix tagging, so the shared helper is covered on both handlers.

Run: `./run-tests.sh` (332 existing + new), per `TESTING.md`. Fixtures `tmp_db` /
`seed_requests` from `conftest.py`; never touch the live `tracker.db`.

**Empirical verification** (after green suite, before re-enabling
`ANTHROPIC_BASE_URL`): reproduce the **original repro** through the redeployed
proxy — a long request (the 300–600s class that produced `API error · Retrying`
and an abandoned-then-200 row). Confirm the binary, bug-tied outcome:

1. The Claude Code client **completes the request with no `API error · Retrying`
   and no abort.**
2. `tracker.db` records a **single clean 200** for it — not an abandoned-then-200
   pair, and not a `stop_reason="incomplete"` row.
3. *(Optional, mechanism-level)* `curl -N` (unbuffered) against the proxy on a
   streaming request shows bytes arriving incrementally on the wire — the one place
   incremental delivery is directly eyeballable, since the SDK otherwise abstracts
   it.

## Rollout

Land as its own branch off `main`, bump VERSION + RELEASE.md (per
`CLAUDE.md` pre-deploy checklist), open a PR to `mr-beaver` (collaborative
framing, as with #7/#8), then a local redeploy + empirical verification before
re-enabling the proxy in the user's Claude Code env.
