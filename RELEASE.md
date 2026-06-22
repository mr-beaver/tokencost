# v1.1.6 — Stream upstream responses (fixes long-request timeouts)

The proxy **buffered** the entire upstream response before sending any bytes to the
client. On a long request it sent zero bytes until the upstream was 100% complete,
so a streaming client (Claude Code, or any `stream: true` request) hit its
idle/per-read timeout and aborted with `API error · Retrying` — even though the
proxy then completed and recorded a 200 for the request the client had abandoned.

- **Responses now stream.** A shared `stream_upstream` helper opens the upstream
  request with `client.send(stream=True)` and returns a `StreamingResponse` that tees
  each chunk to the client while accumulating the full body. Per-read timeout is kept
  (resets on every chunk); there is **no** total-duration cap. Covers both the
  Anthropic `/v1/*` and OpenAI-compat `/{provider}/v1/*` handlers. Request-side
  handling is unchanged (`docs/adr/0001`).
- **Usage accounting is unchanged.** The full teed body is parsed once after the
  stream ends, so token/cache/tool accounting, optimizer savings, and cache-state
  tracking are identical to the old buffered parse.
- **Dedup cache round-trips the content-type.** Cached streaming bodies now replay as
  `text/event-stream` instead of a hardcoded `application/json`. Dedup still caches
  only fully-received 200s.
- **Partial/aborted streams are visible, not silent.** Client disconnect, mid-stream
  error, and connect-time failure record `stop_reason="incomplete"` (connect failures
  also record status 502 and return a real error instead of a raw 500) — no schema
  change. Previously a disconnected stream masqueraded as a clean 200.
- **Tests:** 344 passing (12 new streaming tests — incremental delivery, SSE
  accounting, dedup content-type round-trip, disconnect/partial, connect failure,
  JSON-through-stream, OpenAI-compat streaming).

# v1.1.5 — Add pytest suite and CI workflow

- **332 tests** across 6 modules: request passthrough, routing normalization, cache injection, cost accounting (TTL multipliers), response parsing, optimizer dedup/routing-skip, importer dedup/cutoff guard, DB aggregations.
- **GitHub Actions CI** on every push/PR (`python 3.11`, pinned action SHAs, `contents: read` only).
- **Refactor:** routing normalization extracted to `_normalize_for_downgrade()` for unit testability — logic unchanged.

# v1.1.4 — Cache-write TTL accuracy + clearer cost labelling

Three related changes. All token-accounting math is unchanged for existing rows.

- **1-hour cache writes are now priced correctly.** `calc_cost` charged *all* cache writes at 1.25× base input (the 5-minute rate). 1-hour-TTL writes actually cost 2× base input. The proxy now parses the `usage.cache_creation.ephemeral_1h_input_tokens` split, stores it in a new `cache_creation_1h_tokens` column, and prices `(total − 1h)×1.25 + 1h×2.0`. Old rows have `1h=0`, so their cost is unchanged.
- **The "Enable 1-hour cache TTL" recommendation is now TTL-aware.** It previously assumed a 5-minute TTL (`current_ttl: "5 min"`) and recommended 1h whenever it saw 5–60 min pauses — including for sessions already writing at 1h (e.g. Claude Code), where those gaps are already absorbed, so the projected re-writes don't actually occur. It now derives the real TTL from the observed write breakdown and, when you're already predominantly on 1h, shows an "already on 1-hour (optimal)" card instead of an inapplicable savings estimate. Degrades to prior behaviour on split-less historical data.
- **The dashboard cost figure is labelled as notional.** "Total Cost" now carries a caption ("≈ API pricing equivalent") and a hover tooltip explaining that the figure reflects Anthropic API pricing, that subscription (Pro/Max/Team) marginal cost is $0, and that per-token prices don't vary by tier. `calc_cost` deliberately does **not** branch on subscription/tier — see `CONTEXT.md`.

# v1.1.3 — Forward the request body verbatim (fixes modern Claude Code 400s)

The proxy's request path mutated parts of the body/headers it had no reason to
touch — code written against an older API that broke current Claude Code with a
chain of 400s (orphaned `clear_thinking` strategy, missing `thinking.budget_tokens`,
top-level `cache_control` TTL collision, `context-1m` header on a downgraded model).

This replaces those mutations with one principle: **forward the client's request
verbatim, with two scoped exceptions** (see `docs/adr/0001`):

- **Routing normalization** — when routing rewrites `model` to a cheaper target,
  also strip what that target rejects: the `context-1m` beta header (smaller
  context window), and `output_config.effort` **only when routing to Haiku 4.5**
  (which 400s on the effort param; Sonnet 4.6 keeps it). Previously `effort` was
  stripped unconditionally — needlessly dropping it on requests that stayed on Opus.
- **Opt-in cache optimization** — inject a top-level `cache_control` only when the
  client set none anywhere (top-level/system/tools/messages). Clients that manage
  their own caching (e.g. Claude Code's 1h TTL) are left untouched.

Removed: unconditional `effort`/`thinking`/`betas` body stripping; the dead-and-
harmful `effort:"low"` Haiku injection (it was popped immediately, and would 400
on Haiku if it weren't). `thinking` and `budget_tokens` now pass through unchanged.

- **Fix: disable restores ANTHROPIC_BASE_URL** — instead of unsetting the variable, disable now points it back to `api.anthropic.com` so VS Code and Claude CLI keep working without restart

# v1.1.2 — docs: update info.md with v1.1.1 scoring changes

- Updated scoring table: file path +1→+2, tool calls flat→depth-proportional, "add a" removal documented
- Added "Haiku Upgrade Path" section explaining score≥6 cache-bust exception
- Updated examples table with new scores and edge cases

---

# v1.1.1 — Scoring and routing accuracy fixes

- **Fix: "add a" false positive removed** from `_COMPLEX_KW` — "add a newline here" was scoring +3 and routing to Sonnet. Kept "add feature" and "add support" as legitimate complex signals
- **Fix: file path signal strengthened** from +1 to +2 — `/src/auth/middleware.py` style references are real codebase questions that Haiku handles poorly without project context
- **Fix: tool call depth now scales** — was flat +2 for any number of tool calls, now `min(count, 4)`. 1 tool call and 10 tool calls should not score the same
- **Fix: Haiku upgrade path** — if session started on Haiku (e.g. first request was `ping`) and a complex request (score≥6) arrives later, routing is now allowed to upgrade the model. Previously `should_skip_routing` blocked all switches to preserve cache, trapping complex requests on Haiku. Cache bust is acceptable when task quality matters more than $0.30 cache savings. `score` is now passed into `should_skip_routing`

---

# v1.1.0 — Memory leak fix: LRU eviction on in-memory caches

- **LRU eviction on `_session_state`**: capped at 1000 entries — oldest session evicted when limit reached. Previously grew unboundedly for the lifetime of the proxy process
- **LRU eviction on `_dedup_cache`**: capped at 500 entries. In practice dedup entries expire by TTL on access, but without a size cap a burst of unique requests could accumulate stale entries indefinitely

In practice the leak was ~200 KB/month at normal usage — not critical, but now bounded.

---

# v1.0.9 — Cache-aware smart routing + optimizer improvements

- **Cache-aware routing**: Smart Routing now detects active prompt cache sessions and skips model switching when the cache is warm. Each model switch previously invalidated Anthropic's per-model KV cache (~$0.30 rebuild cost for 80k-token system prompts). Routing is now only allowed on the first request of a session or after the 5-minute cache TTL expires
- **Session tracking**: proxy tracks session state via SHA256 of the system prompt prefix — stable within a Claude Code session, resets on `/compact` or new project
- **`routing_skipped_for_cache` counter**: `/optimizer-status` now reports how many routing decisions were blocked to preserve cache — visible in dashboard
- **Fixed deprecated thinking API**: `auto_enable_thinking` and `limit_thinking_budget` now use `thinking: {type: "adaptive"}` + `output_config.effort` instead of `budget_tokens` (which caused 400 errors on Opus 4.7+ and runaway $15–43/day loops)
- **Extended dedup window for tool chains**: tool_result requests (mid tool-chain) use 15s dedup window instead of 5s — content is stable within a single tool chain
- **effort:low for simple requests**: requests routed to Haiku (score 0–2) now get `output_config.effort=low` to further reduce output tokens
- **Optimizer status banner**: `/dashboard#optimizer` now shows a status banner at the top — green when Smart Routing is enabled, grey with enable instructions when disabled
- **Fix: disable proxy keeps `tokencost` alias**: `onbording.sh` disable action no longer removes the `tokencost` alias from `~/.zshrc` — the command stays available to re-enable later
- **Routing table fix**: Smart Routing table now uses DB-aggregated `routing_groups` (full history, all periods) instead of `recent_events` (capped at 100) — COUNT, IN TOK AVG, OUT TOK AVG, EFFORT columns now show real data

---

# v1.0.8 — Dashboard clarity: avg tokens, cache write visibility, smart routing count

- **Avg In/Out tokens**: all dashboard tables now show average input and output tokens per request, making cost driver analysis clearer
- **Cache Write (CW tok) column**: added to Recent 20, Top 10, Sessions, and Raw Logs tables — shows cache_creation_tokens (billed at 125% of input price). Previously hidden, this is often the largest cost component
- **Smart Routing Count column**: explicit column in Optimizer tab replaces confusing "30+" suffix on model names — routing event counts are now clearly labeled
- **Model×Effort table**: now displays Avg In tok and Avg Out tok for each model+effort combination
- **FAQ section**: added explanation of how model switching interacts with prompt caching — clarifies that each model has its own isolated KV cache (no cross-model reuse) and why savings still compound
- **Fix**: removed auto_enable_thinking which was causing $15–43/day in runaway loops on tool errors — Claude now only uses extended thinking when explicitly requested
- **Menubar app**: labeled "AVG INPUT" and "AVG OUTPUT" as tokens (AVG IN TOK / AVG OUT TOK) for clarity; Models tab shows "Cache%" instead of "Cache"

---

# v1.0.7 — Claude Fable 5 & Opus 4.8 support + routing dashboard

- **New models**: Fable 5 ($10/$50M) and Opus 4.8 ($5/$25M) added to pricing and smart routing
- **Fable 5 routing**: score 0–2 → Haiku (50× cheaper), score 3–5 → Sonnet (17× cheaper), score 6+ → keep original
- **Routing dashboard**: Optimizer tab now shows each model switch individually (Fable→Haiku, Opus→Sonnet) with timestamps and savings — dynamic labels replace hardcoded "Sonnet / Opus"
- **Fix**: routing events now saved to `optimizations_json` so savings are tracked correctly
- **Fix Windows update command**: `import_history.py` now emits the same `powershell -Command "Set-Location..."` form as `proxy.py` — the previous `cd /d` was cmd.exe-only and broke in PowerShell (Windows 11 default)
- **Menubar app**: retry logic on startup — waits up to 10s for proxy to boot; Sync Logs retries 3× before showing "error"

---

# v1.0.6 — Working self-update on Windows

The dashboard's "Update" command now works on Windows:

- **Shell-agnostic update command**: the copyable `update_cmd` is wrapped in
  `powershell -Command`, so it runs whether pasted into **cmd.exe or PowerShell**
  (the old `cd /d ... && ...` form was cmd-only and broke in PowerShell, the
  Windows 11 default terminal).
- **Non-interactive `onbording.ps1 -Update`**: pulls and restarts the proxy with
  the new code without showing the menu. The full flow (`git pull` -> import ->
  restart -> dashboard) is verified end-to-end on Windows 11.

---

# v1.0.5 — Chainable behind another proxy

- **Transparent `/api/oauth/*` passthrough**: TokenCost can now sit chained behind
  another Anthropic proxy (e.g. headroom) that forwards *all* Anthropic traffic
  here, not just `/v1/*`. Subscription-usage polls (`/api/oauth/usage`) are proxied
  through transparently (not logged — they aren't billable LLM calls), so the
  upstream proxy's subscription tracking keeps working through the chain.

Example chain: `Claude Code -> headroom (compress) -> TokenCost (log cost) -> api.anthropic.com`

---

# v1.0.4 — Windows setup script hardening

- **ASCII-only `onbording.ps1`**: Unicode box-drawing/em-dash characters in the
  script were misread as smart-quote string delimiters when PowerShell read the
  file without a BOM, breaking parsing. Script is now pure ASCII.
- **No-admin autostart fallback**: `Register-ScheduledTask` needs elevation; when
  it's denied, setup now installs a hidden Startup-folder launcher (`TokenCost.vbs`)
  so the proxy still autostarts at logon without admin
- **Clean stop**: Disable/restart now kills the full uvicorn supervisor+worker pair
  (by port *and* command line), leaving no orphaned `proxy.py` process

Verified end-to-end on Windows 11: full Start flow (Python check -> deps -> import
-> env var -> autostart fallback -> proxy start -> dashboard) runs from the script.

---

# v1.0.3 — Windows support

Full Windows 10/11 port alongside the existing macOS build.

## Added

- **`onbording.ps1`** — PowerShell setup/start/stop script (Windows equivalent of `onbording.sh`): creates the venv, installs deps, imports history, sets `ANTHROPIC_BASE_URL` as a User env var, registers Scheduled Tasks for proxy autostart + 5-min sync, starts the proxy, opens the dashboard
- **`tokencost.bat`** — double-click launcher for `onbording.ps1`
- **`requirements.txt`** — pinned dependency list (`fastapi`, `uvicorn`, `httpx`)

## Fixes (cross-platform)

- **UTF-8 console**: `proxy.py` and `import_history.py` now force UTF-8 on stdout/stderr — Windows' default cp1252 console crashed on box-drawing / emoji output
- **Dashboard read**: `/dashboard` opened `dashboard.html` without an encoding — crashed with `UnicodeDecodeError` on Windows; now reads as UTF-8
- **Local log paths**: history import now searches Windows locations (`%APPDATA%\Claude`, `%APPDATA%\Code`) in addition to macOS `~/Library/...` and Linux `~/.config/...` for Claude CLI, Claude Desktop, VS Code extensions, and Copilot logs
- **Update command**: `/version` `update_cmd` and the version-cache writer now emit a PowerShell command on Windows instead of `bash onbording.sh`
- **Path/timezone**: `projects.py` uses `os.sep` for home-prefix abbreviation and a DST-safe `astimezone()` for the "today" cutoff (replaces the fragile `time.timezone` math)

## Result

Proxy, dashboard, history import, and live sync all verified working on Windows 11 (Python 3.14).
