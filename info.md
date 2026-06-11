# TokenCost

```bash
# ══ FIRST TIME ══
cd ~ && git clone https://github.com/mr-beaver/tokencost && cd tokencost && bash onbording.sh

# ══ ALREADY INSTALLED — restart/update from local folder ══
cd ~/tokencost && bash onbording.sh

# ══ EVERY TIME AFTER (restart / update) ══
tokencost
```

> After first install, open a new terminal tab — the `tokencost` command will be available.

Local proxy for Anthropic API. Intercepts all requests, calculates cost, shows a dashboard.

---

## Start / Stop

```bash
# Interactive menu (recommended)
bash onbording.sh

# Option 1 — start proxy + open dashboard
# Option 2 — fully disable and remove from environment
```

> ⚠️ **Never kill the proxy manually** — Claude Code routes through it and crashes with exit 143.
> Always restart via onbording.sh → option 1.

---

## Dashboard

```
http://localhost:8082/dashboard
```

**Sections:**
- **Hero KPIs** — total cost for period, monthly projection, cache hit rate, request count
- **How to Reduce Costs** — action plan with concrete savings ($X/mo) and health grade (A–F)
- **By Activity** — breakdown by task type (Code/Bash/Agent/Web/Plan/Search) with columns: turns, 1-shot%, avg input, cost
- **Spend Trend** — cost chart by day
- **By Source** — who's spending (Claude Code, Claude Desktop, VS Code Extensions, OpenClaw, GitHub Copilot usage, API providers) with inline cache hit rate
- **By Model** — table: model, cost, cache%, calls, 1-shot%
- **Core Tools** — most used Claude tools (Edit, Bash, Read...)
- **MCP Servers** — external MCP servers and call counts
- **Effort Breakdown** — standard vs low/medium/high thinking
- **Heatmaps** — activity by day of year and by hour of week
- **Sessions** — each work session: input/output tokens, cost, tools
- **Top 10 / Recent 20** — most expensive and most recent requests

**Periods:** Today / 7d / 30d (buttons at top)

---

## Stats API

```bash
# Raw data for period (JSON)
curl http://localhost:8082/stats?period=7d
curl http://localhost:8082/stats?period=today
curl http://localhost:8082/stats?period=30d
```

---

## Menubar App (macOS)

Shows `$X.XX  B` in the menu bar (daily cost + health grade).
Click — popup with grade, tips, stats, and a link to the dashboard.

Starts automatically via `onbording.sh → option 1`.
Looks for the app in `~/Applications/` → `menubar/TokenCostBar.app`.

```bash
# First time — build (once, ~30 sec)
cd ~/tokencost/menubar
bash build.sh
mv TokenCostBar.app ~/Applications/

# Rebuild after code changes
bash build.sh && open TokenCostBar.app
```

Polls `localhost:8082/stats?period=7d` every 30 seconds.

---

## File Structure

```
tokencost/
├── proxy.py          # FastAPI proxy, port 8082
├── db.py             # SQLite logic, analytics, action plan
├── dashboard.html    # Dashboard (read from disk on each request)
├── tracker.db        # SQLite database
├── onbording.sh      # Start/stop script
├── info.md           # This file
└── menubar/          # SwiftUI menubar app
    ├── Package.swift
    ├── build.sh
    └── Sources/TokenCostBar/
        ├── App.swift
        ├── StatsModel.swift
        └── MenuBarView.swift
```

---

## What Requires Proxy Restart

| File | Restart needed? |
|------|----------------|
| `dashboard.html` | ❌ no — read from disk on each request |
| `db.py` | ✅ yes — imported at Python startup |
| `proxy.py` | ✅ yes |

---

## Supported Providers

The proxy listens for **Anthropic** on `/v1/*` and all **OpenAI-compatible** providers on `/<provider>/v1/*`.

| Provider    | Env var                                          | Upstream                              |
|-------------|--------------------------------------------------|---------------------------------------|
| Anthropic   | `ANTHROPIC_BASE_URL=http://localhost:8082`       | api.anthropic.com                     |
| OpenAI      | `OPENAI_BASE_URL=http://localhost:8082/openai`   | api.openai.com                        |
| Groq        | `GROQ_API_BASE=http://localhost:8082/groq`       | api.groq.com/openai                   |
| Mistral     | `MISTRAL_API_BASE=http://localhost:8082/mistral` | api.mistral.ai                        |
| DeepSeek    | `DEEPSEEK_API_BASE=http://localhost:8082/deepseek` | api.deepseek.com                    |
| xAI (Grok)  | `XAI_API_BASE=http://localhost:8082/xai`         | api.x.ai                              |
| Perplexity  | `PERPLEXITYAI_API_BASE=http://localhost:8082/perplexity` | api.perplexity.ai             |
| Cerebras    | `CEREBRAS_API_BASE=http://localhost:8082/cerebras` | api.cerebras.ai                     |
| Together AI | `TOGETHER_API_BASE=http://localhost:8082/together` | api.together.xyz                    |
| Fireworks   | `FIREWORKS_AI_API_BASE=http://localhost:8082/fireworks` | api.fireworks.ai/inference       |
| Cohere      | `COHERE_API_BASE=http://localhost:8082/cohere`   | api.cohere.ai/compatibility           |
| OpenRouter  | `OPENROUTER_API_BASE=http://localhost:8082/openrouter` | openrouter.ai/api               |
| Ollama      | `OLLAMA_BASE_URL=http://localhost:8082/ollama`   | localhost:11434 (local)               |

### Usage with LiteLLM

```python
import litellm
litellm.api_base = "http://localhost:8082/openai"   # for OpenAI models
# or per-call:
litellm.completion(model="openai/gpt-4o", api_base="http://localhost:8082/openai", ...)
litellm.completion(model="groq/llama-3.3-70b", api_base="http://localhost:8082/groq", ...)
```

### Supported Models (218 entries in pricing)

| Provider | Models | Price input/output ($/M tok) |
|----------|--------|------------------------------|
| **Anthropic** | claude-opus-4-7, opus-4-6, sonnet-4-6, haiku-4-5, 3.5-sonnet, 3-opus, 3-haiku | $0.25–$25 / $1.25–$75 |
| **OpenAI** | gpt-4.1, gpt-4.1-mini/nano, gpt-4o, gpt-4o-mini, o1, o1-pro, o3, o3-mini, o4-mini, gpt-4-turbo, gpt-3.5-turbo | $0.10–$150 / $0.40–$600 |
| **Google Gemini** | gemini-2.5-pro/flash, gemini-2.0-flash/lite, gemini-1.5-pro/flash/flash-8b, gemini-1.0-pro | $0.037–$1.25 / $0.15–$10 |
| **Google Vertex AI** | vertex_ai/gemini-2.5-pro, gemini-2.0-flash, claude-sonnet/opus via vertex | $0.075–$5.0 / $0.30–$25 |
| **Groq** | llama-3.3-70b, llama-3.1-70b/8b, llama-3.2-90b/11b/3b/1b, llama-4-scout/maverick, mixtral-8x7b, gemma2-9b, qwen-qwq-32b, deepseek-r1-distill | $0.04–$0.90 / $0.06–$0.99 |
| **Mistral** | mistral-large-2411, mistral-medium, mistral-small, mistral-nemo, codestral-2501, pixtral-large, mixtral-8x22b, open-mixtral-8x7b | $0.10–$2.0 / $0.30–$6.0 |
| **DeepSeek** | deepseek-chat/v3/v3-0324, deepseek-reasoner/r1/r1-zero | $0.14–$0.55 / $0.28–$2.19 |
| **xAI Grok** | grok-3, grok-3-beta, grok-3-mini, grok-3-fast, grok-2-1212, grok-beta, grok-vision-beta | $0.30–$5.0 / $0.50–$25 |
| **Perplexity** | sonar-pro, sonar, sonar-reasoning-pro, sonar-reasoning, sonar-huge-online | $1.0–$5.0 / $1.0–$15 |
| **Cohere** | command-a-03-2025, command-r-plus, command-r, command-light | $0.15–$2.5 / $0.60–$10 |
| **Cerebras** | llama-3.3-70b, llama3.1-405b/70b/8b, qwen-3-32b | $0.10–$6.0 / $0.10–$6.0 |
| **Together AI** | llama-3.1-405B/70B/8B-Turbo, Mixtral-8x22B, DeepSeek-V3/R1, Qwen2-72B, gemma-2-27b | $0.18–$7.0 / $0.18–$7.0 |
| **Fireworks AI** | llama-v3p3-70b, llama-v3p1-70b/8b, qwen2p5-72b, deepseek-v3/r1 | $0.20–$8.0 / $0.20–$8.0 |
| **Amazon Bedrock** | nova-pro/lite/micro, llama3-70b/8b, llama3.1-70b, llama3.2-90b, mistral-large, mixtral-8x7b, command-r-plus, jamba-1.5 | $0.035–$4.0 / $0.14–$15 |
| **OpenRouter** | gpt-4o, gpt-4o-mini, claude-sonnet/opus, gemini-2.5-pro/flash, llama-3.3-70b, deepseek-chat, grok-3-mini, mistral-large, qwq-32b | varies |
| **HuggingFace** | Llama-3.1-70B, Mixtral-8x7B, gemma-2-27b | $0.50–$0.79 / $0.50–$0.79 |
| **Replicate** | llama-3.1-405b/70b/8b, mixtral-8x7b | $0.05–$9.5 / $0.25–$9.5 |
| **Anyscale** | llama-3-70b/8b, Mixtral-8x22B | $0.15–$1.0 / $0.15–$1.0 |
| **Ollama (local)** | llama3/3.1/3.2/3.3, mistral/nemo, phi3/4, qwen2.5, gemma2, deepseek-r1, codellama | **free** |
| **Azure OpenAI** | gpt-5.4, gpt-5.4-mini/nano/pro, claude-sonnet/haiku/opus | $0.20–$30 / $1.25–$180 |

## How Tracking Works

### Real-time (via proxy)
1. Client sets provider `BASE_URL` → requests go through the proxy
2. Proxy forwards to the real upstream, intercepts the response
3. Anthropic: parses SSE events `message_start/delta/content_block_start`
4. OpenAI-compat: parses `usage.prompt_tokens/completion_tokens` from JSON or last SSE chunk
5. Calculates cost via `calc_cost(model, input, output, cache_read, cache_creation)`
6. Writes to SQLite: source, model, tokens, cost, duration, stop_reason, tools, effort

### From local logs (every 5 minutes)
A launchd daemon (`com.tokencost.sync`) runs `import_history.py` every 300 seconds to sync local application logs:
- **Claude Code / Claude CLI** — reads `~/.claude/projects/**/*.jsonl`
- **Claude Desktop** — reads `~/Library/.../Claude/local-agent-mode-sessions/**/*.jsonl`
- **OpenClaw** — reads `~/.openclaw/agents/**/*.jsonl`
- **VS Code Extensions** (Cline, Roo Code, Kilo Code, IBM Bob) — reads `VSCode/workspaceStorage/*/*/tasks/ui_messages.json`

Deduplication is automatic — same request (by `msg_uuid`) is only recorded once, even if seen by both proxy and local sync.

**Sources are identified by User-Agent (55 patterns):**

| Source | UA patterns |
|--------|------------|
| `vscode` | claude-vscode, vscode, visual studio |
| `claude-cli` | claude-cli, anthropic-cli, claude-code |
| `cursor` | cursor |
| `litellm` | litellm |
| `langchain` | langchain |
| `llama-index` | llama-index, llama_index |
| `openai-sdk` | AsyncOpenAI, SyncOpenAI, openai-python, openai/ |
| `anthropic-sdk` | anthropic-python, anthropic/ |
| `groq-sdk` | groq-python, groq/ |
| `google-sdk` | google-generativeai, google-cloud, googleapiclient |
| `vertex-sdk` | google-cloud-aiplatform |
| `cohere-sdk` | cohere-python-sdk, cohere/ |
| `mistral-sdk` | mistralai, mistral/ |
| `together-sdk` | together-python, together/ |
| `fireworks-sdk` | fireworks-python, fireworks-ai |
| `deepseek-sdk` | deepseek |
| `xai-sdk` | xai-sdk, xai/ |
| `perplexity-sdk` | perplexity |
| `cerebras-sdk` | cerebras-cloud-sdk, cerebras |
| `boto3-bedrock` | botocore, boto3 |
| `openrouter` | openrouter |
| `replicate` | replicate |
| `huggingface` | huggingface-hub, huggingface |
| `openclaw` | openclaw, undici |
| `python-sdk` | python (fallback) |
| `node-sdk` | node, axios, deno |
| `curl` | curl |

---

## Health Grade (A–F)

| Metric | Weight | Good |
|--------|--------|------|
| Cache hit rate | 35 pts | ≥ 60% |
| Avg input size | 25 pts | ≤ 20k tokens |
| Cache write ROI | 25 pts | ≥ 3× (cache read ≥3 times per write) |
| Max tokens stops | 15 pts | ≤ 1% of requests truncated |

`A` ≥90 · `B` ≥75 · `C` ≥60 · `D` ≥45 · `F` <45

---

## Prompt Caching (Anthropic)

| Type | Write cost | Read cost | TTL |
|------|-----------|----------|-----|
| 5 min (default) | 1.25× input | 0.10× input | 5 min |
| 1h | 2.00× input | 0.10× input | 1 hour |

**1h TTL is worthwhile** if there are 5–60 min pauses in a session (cache doesn't expire in 5 min but would within 1h).
Enabled via `{"type": "ephemeral", "ttl": "1h"}` in `cache_control` blocks — **not an env var**.

---

## Smart Model Routing (SMART_ROUTING)

The proxy analyzes the prompt **before** sending and automatically switches the model to a cheaper one.  
Enabled via `onbording.sh → option 1 → "Enable optimizer? [y/N]"`.  
Read from `.smart_routing` file — **no proxy restart needed** when toggling.

**In plain terms:** each request gets a complexity score from 0 to 10. If the request is simple (≤2) — the proxy silently switches the model to Haiku. Nothing changes for you — the response arrives as usual, just cheaper.

### What Gets Switched

| Score | Original model | Result | Savings |
|-------|---------------|--------|---------|
| 0–2   | Sonnet        | → **Haiku** | ~5× cheaper |
| 0–2   | Opus          | → **Haiku** | ~25× cheaper |
| 3–5   | Opus          | → **Sonnet** | ~5× cheaper |
| 3–5   | Sonnet        | stays Sonnet | — |
| 6–10  | any           | stays original | — |

**Note on prompt caching:** When a model is downrouted, the prompt cache is re-keyed to the target model, not the original. This means you lose potential cache reuse on the original model. However, for the simple requests that get downrouted (score ≤2), this cache tradeoff is negligible compared to the 5–25× cost savings. Complex requests that would benefit from cache reuse (score 6–10) are never downrouted.

### How Score Is Calculated (0–10)

The proxy only looks at the **last user message** (not the full context).  
`<ide_selection>`, `<system-reminder>` blocks and images are stripped before scoring.

| Condition | Score |
|-----------|-------|
| Extended thinking (`budget_tokens` > 0) | = **10** (keep) |
| No user text (only tool_result — middle of tool chain) | = **10** (keep) |
| Simple question: starts with `what is / explain` and < 120 chars | = **0** → Haiku |
| Message > 500 chars | +2 |
| Message > 200 chars | +1 |
| Keyword: `implement / fix / write / create / refactor / debug / update` | +3 |
| Code block ` ``` ` in prompt | +3 |
| File extension `.py / .ts / .js / .sql / .go` in prompt | +3 |
| Construct `def / class / function / import` in prompt | +2 |
| File path `/src/ / ./` in prompt | +1 |
| Tool calls in last 4 messages (active tool chain) | +2 |

### Examples

| Prompt | Score | Final model | Why |
|--------|-------|-------------|-----|
| `ping` | 0 | **Haiku** | short, no keywords |
| `test` | 2 | **Haiku** | short |
| `what is a lambda` | 0 | **Haiku** | "what is" pattern |
| `how does cache work` | 0 | **Haiku** | "how does" pattern |
| `how to install Python` | 1 | **Haiku** | short question |
| `implement JWT authentication` | 3 | **Sonnet** | keyword +3 |
| `implement OAuth2 integration` | 3 | **Sonnet** | keyword +3 |
| `fix bug in auth.py` | 4 | **Sonnet** | `fix` +3, file `.py` +1 |
| `write a function that parses JSON` | 5 | **Sonnet** | `write` +3, `function` +2 |
| long request with code and task | 7+ | **Sonnet/Opus** | length + code + keywords |
| Tool-chain steps (Bash/Read/Edit without user text) | 10 | **keep** | don't break active chain |

### What the Proxy Strips from Requests

Some parameters cause 400 errors — the proxy removes them automatically from every request:

| Parameter | Location | Why stripped |
|-----------|----------|--------------|
| `effort` | `output_config.effort` | not supported in current API version |
| `thinking` | top-level | not supported on Haiku |
| `betas` | top-level | not supported on Haiku |

---

## Auto-Optimizations

The proxy applies optimizations automatically on every request. Logic lives in `optimizer.py`, called from `proxy.py` in order: `enforce_max_messages` → `limit_thinking_budget` → `cache_control` → `trim_old_messages`.

### Call Pipeline (proxy.py)

```
incoming request
    ↓
optimize_request(body_data)
    ├── 0a. enforce_max_messages()   — per-session limit
    ├── 0b. limit_thinking_budget()  — budget_tokens limit
    ├── 1.  auto cache_control system prompt
    └── 2.  auto cache_control user message
    ↓
trim_old_messages(body_data)         — trim by total tokens
    ↓
send to Anthropic API
```

---

### 1. Auto cache_control on system prompt
**File:** `optimizer.py → optimize_request()`  
If `system` > 1000 chars and `cache_control` is not already set — adds `{"type": "ephemeral"}`.  
**Savings:** repeated system prompt reads cost 0.10× instead of 1.00× (−90%).

### 2. Auto cache_control on large user message
**File:** `optimizer.py → optimize_request()`  
If last user message > 5000 chars (~1250 tokens) and cache not set — adds `{"type": "ephemeral"}`.  
**Savings:** large context (files, logs) is cheaper to read on repeats.

### 3. Trim old messages (by tokens)
**File:** `optimizer.py → trim_old_messages()`  
Counts total tokens across all messages (rough estimate: chars ÷ 4).  
If > 50 000 tokens — removes old messages until it fits.  
**Protects:** last 3 messages + all messages with `tool_use` / `tool_result` blocks.  
**Savings:** prevents input tokens from growing unboundedly in long sessions.

### 4. Enforce max messages (per-session limit)
**File:** `optimizer.py → enforce_max_messages()`  
Tracks message count per session via `_last_message_count`.  
If > 40 messages — trims history, keeping the last 30.  
Automatically detects a **new session** (count drops sharply — user did `/clear` or opened a new chat) and resets the counter.  
**Protects:** last 3 + `tool_use`/`tool_result` blocks.  
**Benefit:** proactively prevents "prompt too long" error in VS Code — it occurs client-side, before the request reaches the proxy, so `trim_old_messages` can't catch it.

### 5. Limit thinking budget
**File:** `optimizer.py → limit_thinking_budget()`  
If the request has `thinking: {type: "enabled"}` but `budget_tokens` is not set — sets a limit automatically.  
The limit depends on `complexity_score()`:

| complexity_score | budget_tokens | When |
|-----------------|---------------|------|
| 0–3             | 2 000 tokens  | Simple question |
| 4–6             | 5 000 tokens  | Medium request |
| 7–10            | no limit      | Complex task |

`complexity_score()` counts from 0 to 10 based on three factors:
- number of messages (+1 per 2 messages, max +4)
- total content length (+1 per 20k chars, max +4)
- presence of `tool_use`/`tool_result` blocks (+2)

**Savings:** without a limit, thinking can spend 10 000–30 000 tokens on a simple question. With a 2 000 budget — 80–90% savings on thinking tokens.

### 6. Deduplication of identical requests
**File:** `optimizer.py → dedup_check() / dedup_cache_response()`  
If the same request (SHA256 body) arrives twice within 5 seconds — returns the cached response without an API call.  
Triggers on double-click, retries, unstable connection.  
**Savings:** 100% cost of the duplicate request.

---

### How to Read Optimizer Logs in stdout

```
  [cache ] auto-caching system prompt (~12450 chars)
  [cache ] auto-caching user message (~6200 chars)
  [session] trimmed 8 old messages (max 40 per session)
  [thinking] limited budget to 2k tokens (complexity 2)
  [trim  ] removed old messages (saved ~3200 tokens)
```

Each line = one applied optimization. No lines = request was already optimal.

> Restart `onbording.sh → 1` to apply changes to `optimizer.py`.

---

## /compact and /clear — Built-in Claude Code Commands

These are **client-side** commands in the VS Code extension. The proxy doesn't see them and can't call them itself.

### /compact

```
/compact
```

Asks Claude to **compress the current context**: Claude reads the entire conversation history, generates a brief summary, and continues the session with that summary instead of the full context.

**What happens technically:**
1. VS Code sends a special system request "compress the conversation"
2. Claude generates a summary of the entire history (~500–1000 tokens)
3. The summary becomes the new "start" of the conversation
4. The next request goes with a small context

**Costs one API request** (for the compression itself), but every subsequent request is cheaper.  
**Doesn't lose context** — the essence of the session is preserved in compressed form.

**When it's useful:** long session (50+ messages), many code iterations, context starts slowing things down (visible by the `In` column in RAW Logs — growing toward 50k–100k).

### /clear

```
/clear
```

**Full reset** — deletes the entire conversation history. The next request starts from scratch.

**What happens technically:** VS Code simply clears the `messages` array. No API request is made.

**Downside:** loses context about the task, files, decisions made earlier.  
**Upside:** completely free, next requests have minimal input tokens.

**When it's useful:** task is done, starting a new unrelated task.

### New Session (New Chat)

Same as `/clear` — just close and open a new chat in VS Code.  
`enforce_max_messages()` in the proxy automatically detects this: message count drops sharply → counter resets.

---

## Is There Any Point in Doing /clear or /compact on a Timer?

**Short answer: `/compact` — yes, by tokens. `/clear` — no, not on a timer.**

### A Timeout by Itself Doesn't Help

A pause between requests **doesn't affect** context size at all. Claude Code keeps the full session history in memory on the VS Code side — it doesn't "expire" with time. After 5 minutes or 2 hours — the context is the same.

The only thing that "expires" on a timeout — **Anthropic prompt cache** (TTL 5 minutes for ephemeral). If the pause > 5 minutes — the system prompt needs to be cached again; the proxy does this automatically.

### When /compact Is Actually Needed

| Signal | Action |
|--------|--------|
| `In` in RAW Logs grows > 50 000 | `/compact` — compress history |
| "prompt too long" error in VS Code | `/compact` or `/clear` immediately |
| Starting a new task in the same session | `/clear` — cheaper than starting fresh |
| Long session (3h+), many tool calls | `/compact` once per hour |
| Session finished, task done | `/clear` before the next task |

### When /compact/clear Are NOT Needed

- After a pause/break — context doesn't change on its own
- If `In` < 20 000 tokens — not critical yet
- In the middle of an active tool chain — you'll lose task context

### Automatic Overflow Protection

The proxy already does several things to delay when `/compact` is needed:
1. `trim_old_messages` — triggers at > 50k tokens in messages
2. `enforce_max_messages` — triggers at > 40 messages
3. Both work **silently** — you don't notice, context just doesn't grow infinitely

But they work on the **server side** (in the proxy). The "prompt too long" error occurs on the **client side** (VS Code), before the request reaches the proxy — so they can't fully replace `/compact`.

---

## Environment

```bash
# Verify proxy is active
echo $ANTHROPIC_BASE_URL          # should be http://localhost:8082
lsof -i :8082                     # process on port

# Proxy logs (while running — in terminal stdout)
# Each request: [source] model effort=X | in=N cr=N cw=N out=N | $X.XXXXX | Xms

# Database
sqlite3 ~/tokencost/tracker.db
  .tables
  SELECT source, model, cost_usd, ts FROM requests ORDER BY id DESC LIMIT 10;
```
