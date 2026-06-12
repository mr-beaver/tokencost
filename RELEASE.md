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
