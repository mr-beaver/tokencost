# Design — Stop `tracker.db` writes from blocking agent requests

**Date:** 2026-06-23
**Branch:** `fix/sqlite-write-contention` (off `main`)
**Status:** approved (brainstorming + grilling)
**Tracker task:** #19

---

## Problem

Concurrent writers to the single-file SQLite `tracker.db` occasionally fail with
`OperationalError: database is locked`, and when they do, that request's **usage/cost row is
silently dropped**. Observed 3× in ~24h over ~3000 requests/day (surfaced by the streaming branch's
`[stream] finalize` diagnostics; the bug pre-dates streaming).

Grilling surfaced a deeper issue than "a few dropped rows": **the accounting write runs synchronously
on the asyncio event loop.**

- On **main**: `_record(...)` → `db.save_request` is called inline in `async def proxy_anthropic`
  (proxy.py ~line 752) *before* the `Response` is returned.
- On the **streaming branch** (PR #10): it's called from `finalize` inside the streaming generator
  `gen()` — after content is delivered, but still on the event loop, before the connection closes.

Neither path offloads the write. `sqlite3` is blocking C, so any wait inside the write (e.g. a
`busy_timeout`) **stalls the entire event loop**, delaying *all* concurrent agent requests.

### The two writers

- **Proxy** (`db.save_request`) — frequent, short writes, one per request, on the event loop.
- **Sync daemon** (`import_history.import_all`, launchd `com.tokencost.sync`) — infrequent batch, but
  holds one connection across a multi-provider import, committing per provider.

### Root cause of the lock errors

The handoff's "no `busy_timeout`" is imprecise: Python's `sqlite3.connect()` defaults to `timeout=5.0`,
which already installs a 5s busy-timeout. Getting `database is locked` *despite* that points to
**rollback-journal lock-upgrade deadlocks**: in the default (non-WAL) journal mode, when one
connection holds a read lock and another needs the write lock (or the long import holds it), SQLite
returns `SQLITE_BUSY` **immediately, ignoring the timeout**, because waiting would deadlock. **WAL mode
structurally removes this** — readers never block the single writer, and the writer doesn't deadlock
on lock upgrade.

---

## Goal & constraints

1. **A DB write must never block an agent's request.** This is the primary goal. Agentic clients
   (Claude Code, etc.) route through the proxy; their request latency must not depend on `tracker.db`.
2. **The dashboard may be eventually consistent.** This is metric/behavior data only — no agent
   decision depends on it. A row landing milliseconds (or, under a stall, seconds) late is fine.
3. **Don't over-engineer.** Losing a handful of metric rows on a hard process kill is acceptable;
   elaborate durability machinery for dashboard data is not warranted.

---

## Approach

Two parts: **decouple the write from the request** (the actual unblock) and **make the now-off-path
write resilient to contention** (WAL + busy_timeout).

### Part 1 — Asynchronous accounting write (proxy.py)

The request path must only ever do O(1), non-blocking, non-failing work.

- A module-level **bounded queue** (`queue.Queue(maxsize=10000)`) and a single **daemon writer
  thread**, started in the FastAPI `lifespan` startup.
- The request handler builds the record (including a **request-time timestamp**, see below) and
  enqueues it with `put_nowait`. The agent's request is complete the instant the row is enqueued.
- On `queue.Full` (only possible under a pathological sustained DB stall): **drop the row and log** —
  never block the request. Consistent with the eventual-consistency bar.
- **Writer thread loop:** pull a record, call the (synchronous) `db.save_request`. Each write is
  wrapped in `try/except Exception` → **log and continue**. A single bad row can never kill the
  thread; the thread exits only on intentional shutdown. (Mitigates the single-writer single-point-of
  -failure: today each request writes independently, so the new path must be at least as robust.)
- **Graceful-shutdown flush:** in `lifespan` shutdown (after `yield`), push a sentinel and `join()`
  the writer with a short timeout (~2s) so a routine restart (auto-updater / `launchctl kickstart`)
  drains the backlog. A hard kill still loses the in-memory queue — accepted.
- **Request-time timestamp:** the proxy passes the request-moment `ts` into `save_request` so a queue
  backlog can't skew the dashboard's time buckets. (`save_request` gains an optional `ts` param that
  defaults to "now" for other callers.)

**Single writer thread is deliberate:** it makes proxy-internal write contention *impossible* (only
one proxy writer ever exists), leaving the only real contention as proxy-writer-thread vs. the
separate import process — which Part 2 handles.

### Part 2 — WAL + busy_timeout via a shared connection helper (db.py)

Add one helper all `tracker.db` connections route through:

```python
def _connect(path=None):
    con = sqlite3.connect(path or DB_PATH, timeout=3.0)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=3000")   # ~3s; rides out an import batch
    con.execute("PRAGMA synchronous=NORMAL")  # WAL-safe, no per-commit fsync
    return con
```

- **Resolve `DB_PATH` at call time** (`path=None` → `path or DB_PATH`), NOT as a default-arg value, so
  the `tmp_db` test fixture (which monkeypatches `db.DB_PATH`) works.
- Replace every bare `sqlite3.connect(DB_PATH)` in `db.py` (~22 sites) with `_connect()`.
- `db.save_request` **stays synchronous** — it just gains `_connect` and the optional `ts` param. The
  queue/async concern lives entirely in `proxy.py`; the storage layer stays a plain synchronous API.
- WAL is a persisted DB property; applying it per-connection is harmless belt-and-suspenders.
- `busy_timeout=3000` now only governs the **background** writer thread waiting out the import process
  — off the request path, where waiting is free. **No application-level backoff loop**: it would be
  redundant with `busy_timeout` (both are wait-and-retry) and would *stack* into multi-second stalls.

### Part 3 — Sync daemon (import_history.py)

Route the `tracker.db` write connection (`import_all`, ~line 710) through the shared helper:
`from db import _connect`, called with `import_history`'s own `DB_PATH`. Single source of truth for
the PRAGMAs — do **not** duplicate the pragma list. The import stays correctly **synchronous** (it's a
batch process that *wants* blocking writes); it never touches the proxy's queue. With WAL +
`busy_timeout` on both sides, whichever writer is momentarily blocked now *waits* instead of failing.

`_open_sqlite_ro` (reads *other apps'* source DBs read-only) is **left untouched** — unrelated to
`tracker.db` contention.

---

## What we explicitly are NOT doing (and why)

- **No exponential-backoff retry loop.** Redundant with `busy_timeout` once WAL makes the timeout
  effective; stacking the two risks 9–15s stalls.
- **No spill file / dead-letter (`tracker.spill.jsonl`).** With an off-path single writer + WAL + 3s
  busy_timeout, write failures are vanishingly rare, and this is eventually-consistent dashboard data
  (much of the claude-cli traffic is also re-derivable by the sync importer from source JSONL).
  Drain-on-startup + a lock to defend against losing a few metric rows is the over-engineering the
  goal warns against. **On a failed write: log and move on.**
- **No `aiosqlite` / new dependency.** "Async SQLite" libraries just run blocking `sqlite3` on a
  background thread — exactly our queue+writer, minus single-writer serialization, plus a dependency.
  The project is stdlib-`sqlite3` throughout; keep it that way.
- **No cross-process write queue / IPC.** Proxy and sync are separate processes; WAL + busy_timeout is
  sufficient and far simpler.
- **No sync-cadence / launchd change. No schema or pricing change.**

---

## Files touched

| File | Change |
|------|--------|
| `proxy.py` | Bounded queue + single daemon writer thread; enqueue at the `_record` call sites with request-time `ts`; start writer + register graceful-flush in `lifespan` |
| `db.py` | Add `_connect()`; route ~22 connect sites through it; `save_request` gains optional `ts` param (stays synchronous) |
| `import_history.py` | Route `tracker.db` write connection through `db._connect` |
| `.gitignore` | Add `tracker.db-wal`, `tracker.db-shm` (no spill file) |
| `VERSION`, `RELEASE.md` | Bump version; changelog entry |
| `tests/` | New tests (below) |

---

## Testing

Use existing `tmp_db` / `seed_requests` fixtures in `conftest.py`. **Never** touch the live
`tracker.db`.

1. **WAL applied:** `_connect()` on a fresh DB → `PRAGMA journal_mode` reads back `wal`;
   `PRAGMA busy_timeout` reads back `3000`.
2. **`save_request` still synchronous:** direct call inserts exactly one row, immediate read-back sees
   it (existing tests keep passing); explicit `ts` is honored; omitted `ts` defaults to ~now.
3. **Proxy enqueue is non-blocking & persists:** enqueue N records → writer drains them → all N land
   in the DB.
4. **Writer survives a bad row:** inject a record whose write raises → it's logged and skipped, and
   subsequent records still persist (thread did not die).
5. **Queue full drops, never blocks:** with a stalled/blocked writer, `put_nowait` overflow drops +
   logs rather than raising into the request path.
6. **Graceful flush:** enqueue N, trigger `lifespan` shutdown → writer drains all N within the join
   timeout.
7. **`import_all` smoke:** import path opens its `tracker.db` connection in WAL mode (read back
   `journal_mode`).

Baseline before this work: 345 passing (`./run-tests.sh`). All new tests must pass alongside.

---

## Risks & notes

- **Single writer thread is a SPOF** → mitigated by the crash-proof per-row loop; it can only exit on
  intentional shutdown.
- **Hard kill loses the in-memory queue** (graceful restart does not) — accepted for metrics.
- **WAL sidecar files** (`tracker.db-wal`, `tracker.db-shm`) appear next to the DB; gitignored here.
  Auto-managed; default `wal_autocheckpoint` (1000 pages) is fine at this volume.
- **WAL requires all accessors on the same host** (no network FS) — true (single machine).
- **`synchronous=NORMAL`** under WAL can lose only the *last* committed transaction on OS crash /
  power loss (not app crash) — acceptable for usage analytics.
- **Branch coordination:** the enqueue site differs between main (inline before `Response`) and PR
  #10's streaming `finalize`; the `db.py`/WAL/`import_history` parts are branch-agnostic. Whichever
  merges second adapts only the enqueue call site.
- Behavior change → **bump `VERSION`** and update `RELEASE.md` per the `CLAUDE.md` pre-deploy
  checklist. Keep runtime artifacts gitignored; no personal paths in tracked files.
