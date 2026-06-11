# TokenCost

**Know exactly what you spend on AI — and automatically spend less.**

A local proxy that sits between your tools and the LLM APIs. It tracks every token, calculates real costs, and optimizes requests on the fly. Works with Claude Code, VS Code, Claude Desktop, OpenClaw, and 15+ API providers (OpenAI, Groq, Mistral, DeepSeek, xAI, Perplexity, Cerebras, Together, Fireworks, Cohere, OpenRouter, Ollama, Amazon Bedrock, Google Gemini, and more).

<img width="1597" height="1214" alt="image" src="https://github.com/user-attachments/assets/d89c62f7-7270-4084-86f2-0760ff3315b0" />
<img width="1601" height="969" alt="image" src="https://github.com/user-attachments/assets/1191d3f7-9d24-4b83-9a78-6b22dc63fb69" />
<img width="1600" height="1192" alt="image" src="https://github.com/user-attachments/assets/8d1bd53f-29f6-4705-9208-edb0a39d3d4f" />
<img width="611" height="826" alt="image" src="https://github.com/user-attachments/assets/25e2c1da-29f2-4329-bcdd-1202fe0d747e" />

---

## Why

If you use Claude Code or VS Code AI tools heavily, you're probably spending $2–10/day without knowing exactly where it goes. This tool:

- Shows you a real-time breakdown by tool, model, session, and task type
- Automatically reduces your bill by 50–80% via prompt caching and smart model routing
- Works locally — your prompts never leave your machine

---

## What it does

### 📊 Live Dashboard
Full spend analytics at `http://localhost:8082/dashboard`:

- **Cost by day / week / month** — trend chart with daily breakdown
- **By task type** — Coding vs Shell vs Agent vs Planning vs Web
- **By model** — Claude Haiku / Sonnet / Opus / GPT-4o / etc.
- **By source** — Claude Code, Claude Desktop, VS Code Extensions, OpenClaw, GitHub Copilot (usage only), API providers
- **Cache analytics** — hit rate, money saved vs. what you'd pay without caching
- **Session view** — every conversation: tokens in/out, cost, tools used
- **RAW Logs** — last 500 requests with full prompt preview
- **Health grade A–F** — scores your efficiency and tells you what to fix

### 🍎 macOS Menu Bar Widget
Always-visible cost tracker in your menu bar. Click to see:
- Today's spend + health grade
- 7-day trend chart with hover tooltips
- Breakdown by task, model, cache, tools
- Smart Routing optimizer stats
- One-click sync from local logs
<img width="380" height="643" alt="image" src="https://github.com/user-attachments/assets/3bec60fb-73c6-456c-8851-1a00faf69af6" />

### ⚡ Automatic Optimizations
The proxy applies these silently on every request:

| Optimization | What it does | Typical savings |
|---|---|---|
| **Prompt Cache** | Auto-tags large system prompts and user messages for caching | **60–90%** on repeat reads |
| **Smart Routing** | Routes simple requests to Haiku instead of Sonnet/Opus | **5–25×** cheaper per request |
| **Thinking Budget** | Caps `budget_tokens` for extended thinking based on complexity | 80–90% on thinking tokens |
| **Message Trim** | Removes old messages when context exceeds 50k tokens | Prevents runaway costs |
| **Session Cap** | Limits history to 40 messages per session | Prevents VS Code "prompt too long" errors |
| **Deduplication** | Returns cached response for identical requests within 5s | 100% on accidental double-sends |

Smart Routing scores each prompt 0–10 (length, keywords, code presence) and silently downgrades model when complexity is low. You get the same response, cheaper.

<img width="1590" height="695" alt="image" src="https://github.com/user-attachments/assets/f5b7e447-41da-4aed-890c-bc047a8ddbc9" />

---

## Install

**Requirements:** Python 3.9+ · macOS (Monterey 12+) **or** Windows 10/11

### macOS

**Step 1 — first time only:**
```bash
cd ~ && git clone https://github.com/mr-beaver/tokencost && cd tokencost && bash onbording.sh
```

The setup script installs everything and adds a `tokencost` command to your terminal.

**Step 2 — every time after (restart / update):**

Open Terminal and type:

```
tokencost
```

> First install adds this command automatically. Open a new terminal tab after step 1 for it to appear.

The setup script:
1. Creates a Python virtualenv and installs dependencies
2. Imports your full Claude usage history from local logs
3. Sets `ANTHROPIC_BASE_URL=http://localhost:8082` in `~/.zshrc` and macOS launchd
4. Adds `tokencost` alias for quick restarts
5. Starts the proxy and opens the dashboard

### Windows

**Step 1 — first time only** (PowerShell):
```powershell
cd $HOME; git clone https://github.com/mr-beaver/tokencost; cd tokencost; powershell -ExecutionPolicy Bypass -File onbording.ps1
```

**Step 2 — every time after:** double-click **`tokencost.bat`** in the repo folder, or run the same `onbording.ps1` command again. Pick option **1** to start, **2** to disable.

The Windows setup script (`onbording.ps1`):
1. Creates a Python virtualenv and installs dependencies (`requirements.txt`)
2. Imports your full Claude usage history from local logs (`%APPDATA%\Claude`, `%APPDATA%\Code`, `~/.claude`)
3. Sets `ANTHROPIC_BASE_URL=http://localhost:8082` as a **User** environment variable (picked up by Claude Code, VS Code, Claude Desktop, and new terminals)
4. Registers autostart at logon — a Scheduled Task if you run it **as Administrator** (this also adds the unattended 5-minute auto-sync task), otherwise a no-admin launcher in your Startup folder
5. Starts the proxy and opens the dashboard

> **Auto-sync needs admin.** Without elevation the proxy still autostarts and captures live traffic in real time; historical logs sync whenever you run setup or click **Sync now** in the dashboard. Run `onbording.ps1` from an **Administrator** PowerShell if you want the unattended 5-minute sync as well.

> The macOS menu-bar widget is not available on Windows — use the web dashboard at `http://localhost:8082/dashboard`.

**Updating (Windows):** open the dashboard, click the version badge, and copy the **Update** command — it's shell-agnostic (runs in cmd.exe *or* PowerShell), does `git pull`, and restarts the proxy non-interactively. Or just re-run `tokencost.bat`.

**After install**, all your Claude Code and VS Code requests flow through the proxy automatically.

### macOS Menu Bar Widget (optional)

The pre-built app is included in the repo — `onbording.sh` installs and launches it automatically.

To install manually:
```bash
cp -R menubar/TokenCostBar.app ~/Applications/
open ~/Applications/TokenCostBar.app
```

**Build from source** (requires Xcode Command Line Tools):
```bash
cd menubar && bash build.sh
mv TokenCostBar.app ~/Applications/
open ~/Applications/TokenCostBar.app
```

---

## Supported Sources

### Local applications (auto-tracked from local logs)
Paths are resolved per-platform — macOS `~/Library/Application Support/...`, Windows `%APPDATA%\...`, Linux `~/.config/...`.

- **Claude Code / Claude CLI** — `~/.claude/projects/**/*.jsonl` (all platforms)
- **Claude Desktop** — `local-agent-mode-sessions/**/*.jsonl` under `Claude` (macOS `~/Library/Application Support/Claude`, Windows `%APPDATA%\Claude`)
- **OpenClaw** — `~/.openclaw/agents/**/*.jsonl`
- **VS Code Extensions** (Cline, Roo Code, Kilo Code, IBM Bob) — `globalStorage/*/tasks/ui_messages.json` under `Code` (macOS `~/Library/Application Support/Code`, Windows `%APPDATA%\Code`)
- **GitHub Copilot** — usage tracking from VS Code logs (model + requests, pricing unavailable)

> Sync cadence: every 5 minutes on macOS (launchd) and on admin Windows (Scheduled Task); on non-admin Windows, on each setup run or via **Sync now** in the dashboard.

### API Providers (real-time via proxy — set base URL)

Set the base URL for each provider you want to track:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8082            # Claude (auto-set by onbording.sh)
export OPENAI_BASE_URL=http://localhost:8082/openai        # OpenAI
export GROQ_API_BASE=http://localhost:8082/groq            # Groq
export MISTRAL_API_BASE=http://localhost:8082/mistral      # Mistral
export DEEPSEEK_API_BASE=http://localhost:8082/deepseek    # DeepSeek
```

On **Windows**, `ANTHROPIC_BASE_URL` is set for you by `onbording.ps1`; set additional providers with PowerShell, e.g.:

```powershell
[Environment]::SetEnvironmentVariable("OPENAI_BASE_URL", "http://localhost:8082/openai", "User")
```

Supported: **Anthropic · OpenAI · Groq · Mistral · DeepSeek · xAI · Perplexity · Cerebras · Together · Fireworks · Cohere · OpenRouter · Ollama · Amazon Bedrock · Google Gemini**

218 models in the pricing database.

---

## How it works

```
Your tool (Claude Code / VS Code / etc.)
        ↓
  localhost:8082  ← proxy runs here
        ↓  scores complexity, applies optimizations, logs tokens+cost
  api.anthropic.com / api.openai.com / etc.
        ↓
  Response back through proxy → your tool
```

The proxy is transparent — your tools see it as the real API. Zero changes to your workflow.

### Chaining behind another proxy

TokenCost forwards every Anthropic path transparently (`/v1/*` **and** `/api/oauth/*`), so it can sit downstream of another Anthropic proxy (e.g. a context-compression layer) that points its upstream at TokenCost:

```
Your tool → other proxy (compress) → localhost:8082 (TokenCost logs cost) → api.anthropic.com
```

Point the upstream proxy's Anthropic target at `http://localhost:8082` (subscription-usage polls pass through untouched and aren't logged).

---

## Smart Routing details

When enabled (setup script → option 1), the proxy scores the last user message:

| Score | Original model | Routes to | Savings |
|---|---|---|---|
| 0–2 | Sonnet | **Haiku** | ~5× cheaper |
| 0–2 | Opus | **Haiku** | ~25× cheaper |
| 3–5 | Opus | **Sonnet** | ~5× cheaper |
| 6–10 | any | unchanged | — |

Simple questions (`what is X`, `explain Y`), short messages, and tool-chain intermediates score 0–2. Long coding tasks with keywords like `implement`, `refactor`, `debug` score 6+.

---

## Stats API

```bash
curl http://localhost:8082/stats?period=today   # today's stats (JSON)
curl http://localhost:8082/stats?period=7d      # last 7 days
curl http://localhost:8082/stats?period=30d     # last 30 days
curl http://localhost:8082/raw-logs?limit=100   # recent requests
```

---

## Data & Privacy

- Everything stored locally in `tracker.db` (SQLite)
- Nothing is sent to any external service
- The proxy only reads your token usage metadata — not the content of your conversations (prompt previews are stored locally, never transmitted)
- Stop the proxy anytime via the setup script's **option 2** (`onbording.sh` on macOS, `tokencost.bat` on Windows)

---

## Stop / Uninstall

**macOS:**
```bash
bash onbording.sh   # choose option 2 — stops proxy, removes env vars
```

**Windows:** run `tokencost.bat` (or `onbording.ps1`) and choose option **2** — stops the proxy, removes the autostart entry (Scheduled Task / Startup launcher), and clears `ANTHROPIC_BASE_URL`.

> ⚠️ Don't kill the proxy process manually if you're using Claude Code — Claude routes through it and the call will fail. Always use the setup script's option 2.

---

## Project structure

```
tokencost/
├── proxy.py           — FastAPI proxy, port 8082
├── optimizer.py       — Request optimizations (cache, routing, trim)
├── db.py              — SQLite schema, cost calculations, analytics
├── import_history.py  — Import historical logs from Claude CLI, Desktop, OpenClaw
├── projects.py        — Project/session tracking
├── dashboard.html     — Web dashboard UI
├── onbording.sh       — Setup / start / stop script (macOS)
├── onbording.ps1      — Setup / start / stop script (Windows)
├── tokencost.bat      — Windows launcher (double-click)
├── requirements.txt   — Python dependencies
└── menubar/           — macOS SwiftUI menu bar app
    ├── build.sh
    └── Sources/TokenCostBar/
        ├── MenuBarView.swift
        └── StatsModel.swift
```

---

## License

MIT
