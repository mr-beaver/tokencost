#!/usr/bin/env python3
from __future__ import annotations
"""
Import historical LLM usage from all local providers into tracker.db.
Runs every 5 minutes as a launchd daemon. Safe to run multiple times (idempotent).

Supported providers:
  claude-cli       ~/.claude/projects/**/*.jsonl          (JSONL, dedup by message.id)
  claude-desktop   ~/Library/.../local-agent-mode-sessions (JSONL, same format, no cutoff)
  openclaw         ~/.openclaw/agents/**/*.jsonl           (model.completed events)
  cline            ~/Library/.../saoudrizwan.claude-dev/tasks  (ui_messages.json)
  roo-code         ~/Library/.../rooveterinaryinc.roo-cline/tasks (same as cline)
  kilo-code        ~/Library/.../kilocode.kilo-code/tasks  (same as cline)

Deduplication:
  - claude-cli/desktop: msg_uuid = message.id (Anthropic API response ID)
  - openclaw:           msg_uuid = traceId:seq
  - cline/roo/kilo:     msg_uuid = provider:taskId:index

Claude CLI / VS Code / OpenClaw are also captured live by the proxy.
The cutoff prevents double-counting: only records BEFORE proxy start are imported from JSONL
for those providers. Claude Desktop has no cutoff (proxy never sees it).
"""

import json
import glob
import re
import sqlite3
import sys
import os
import time
from pathlib import Path
from datetime import datetime, timezone

# Windows consoles default to cp1252, which can't encode the box-drawing /
# emoji characters this script prints. Force UTF-8 on all platforms.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
from db import PRICING as DB_PRICING, init_db as _init_db

DB_PATH  = Path(__file__).parent / "tracker.db"
HOME     = Path.home()
_DEFAULT = DB_PRICING["default"]

VSCODE_GLOBS = [
    str(HOME / "AppData/Roaming/Code/User/globalStorage"),              # Windows
    str(HOME / "Library/Application Support/Code/User/globalStorage"),  # macOS
    str(HOME / ".config/Code/User/globalStorage"),                      # Linux
    str(HOME / ".vscode-server/data/User/globalStorage"),
]


# ── Pricing ───────────────────────────────────────────────────────────────────

def _price(model: str) -> dict:
    p = DB_PRICING.get(model)
    if not p:
        for k, v in DB_PRICING.items():
            if model and model.startswith(k):
                return v
    return p or _DEFAULT


def calc_cost(model, input_tok, output_tok, cache_write, cache_read):
    p = _price(model)
    M = 1_000_000
    return (
        input_tok   * p["input"]          / M +
        output_tok  * p["output"]         / M +
        cache_write * p["input"] * 1.25   / M +
        cache_read  * p["input"] * 0.10   / M
    )


# ── DB helpers ────────────────────────────────────────────────────────────────

def ensure_schema(conn):
    if not _col_exists(conn, "msg_uuid"):
        conn.execute("ALTER TABLE requests ADD COLUMN msg_uuid TEXT")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_uuid "
        "ON requests(msg_uuid) WHERE msg_uuid IS NOT NULL"
    )
    conn.commit()


def _col_exists(conn, col):
    return col in {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}


def _proxy_cutoff(conn) -> str | None:
    row = conn.execute(
        "SELECT MIN(ts) FROM requests "
        "WHERE source NOT IN ('claude-cli-history','claude-desktop-history','openclaw-history',"
        "'cline-history','roo-code-history','kilo-code-history')"
    ).fetchone()
    return row[0][:19] if (row and row[0]) else None


def _insert(conn, *, ts, source, model, input_tok, output_tok,
            cache_read, cache_write, stop_reason, tools, tool_count,
            msg_uuid, cost_override=None, prompt_preview=""):
    cost = cost_override if cost_override is not None else \
           calc_cost(model, input_tok, output_tok, cache_write, cache_read)
    tools_json = json.dumps(tools) if tools else None
    preview    = (prompt_preview or "")[:800]
    try:
        conn.execute("""
            INSERT OR IGNORE INTO requests
              (ts, source, model, input_tokens, output_tokens, cost_usd,
               duration_ms, status, cache_read_tokens, cache_creation_tokens,
               stop_reason, tool_call_count, tools_json, msg_uuid, prompt_preview)
            VALUES (?,?,?,?,?,?,0,200,?,?,?,?,?,?,?)
        """, (ts, source, model, input_tok, output_tok, cost,
              cache_read, cache_write, stop_reason, tool_count, tools_json, msg_uuid, preview))
        changed = conn.execute("SELECT changes()").fetchone()[0]
        # Backfill preview for records that already exist but have none
        if changed == 0 and preview:
            conn.execute(
                "UPDATE requests SET prompt_preview=? "
                "WHERE msg_uuid=? AND (prompt_preview IS NULL OR prompt_preview='')",
                (preview, msg_uuid),
            )
        return changed
    except sqlite3.Error:
        return 0


def _save_last_sync(conn):
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('last_import_ts',?)", (ts,))


# ── Provider: Claude CLI & Desktop ────────────────────────────────────────────

def _import_claude_jsonl(conn, patterns: list[str], source: str, cutoff: str | None):
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat, recursive=True))

    inserted = skipped = errors = 0

    for fpath in files:
        best: dict[str, dict] = {}
        # msg_id → user text that preceded this assistant turn
        user_before: dict[str, str] = {}
        last_user_text = ""
        try:
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        errors += 1
                        continue

                    dtype = d.get("type", "")

                    # Capture user messages for prompt preview
                    if dtype == "user":
                        content = d.get("message", {}).get("content", "")
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            text = " ".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        else:
                            text = ""
                        text = " ".join(text.split())
                        if text:
                            last_user_text = text
                        continue

                    if dtype != "assistant":
                        continue

                    msg    = d.get("message", {})
                    usage  = msg.get("usage", {})
                    model  = msg.get("model", "")
                    msg_id = msg.get("id", "")
                    ts     = d.get("timestamp", "") or d.get("_audit_timestamp", "")

                    if not model or not ts or not msg_id:
                        continue

                    input_tok   = usage.get("input_tokens", 0) or 0
                    output_tok  = usage.get("output_tokens", 0) or 0
                    cache_write = usage.get("cache_creation_input_tokens", 0) or 0
                    cache_read  = usage.get("cache_read_input_tokens", 0) or 0

                    if input_tok == 0 and output_tok == 0 and cache_read == 0 and cache_write == 0:
                        continue

                    if msg.get("model") == "<synthetic>":
                        continue

                    # Only record user_before on first encounter of this msg_id
                    if msg_id not in best:
                        user_before[msg_id] = last_user_text

                    if cutoff and ts[:19] >= cutoff:
                        # Don't insert (proxy already captured it), but backfill
                        # prompt_preview on any existing -history record with this uuid
                        preview = user_before.get(msg_id, "")
                        if preview and msg_id:
                            conn.execute(
                                "UPDATE requests SET prompt_preview=? "
                                "WHERE msg_uuid=? AND (prompt_preview IS NULL OR prompt_preview='')",
                                (preview[:800], msg_id),
                            )
                        skipped += 1
                        continue

                    content = msg.get("content", [])
                    tools   = list({
                        b["name"] for b in content
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")
                    })
                    # Accumulate tokens across streaming duplicates of the same msg_id.
                    # JSONL may emit the same message twice: first with input tokens,
                    # then with output tokens (or vice-versa). Keep the max of each field.
                    prev = best.get(msg_id)
                    best[msg_id] = {
                        "ts": ts, "model": model,
                        "input_tok":   max(input_tok,   prev["input_tok"]   if prev else 0),
                        "output_tok":  max(output_tok,  prev["output_tok"]  if prev else 0),
                        "cache_write": max(cache_write, prev["cache_write"] if prev else 0),
                        "cache_read":  max(cache_read,  prev["cache_read"]  if prev else 0),
                        "stop_reason": msg.get("stop_reason", "") or (prev["stop_reason"] if prev else ""),
                        "tools": tools or (prev["tools"] if prev else []),
                        "tool_count": len(tools) or (prev["tool_count"] if prev else 0),
                        "msg_id": msg_id,
                    }
        except (OSError, PermissionError):
            continue

        for e in best.values():
            inserted += _insert(
                conn, ts=e["ts"], source=source, model=e["model"],
                input_tok=e["input_tok"], output_tok=e["output_tok"],
                cache_read=e["cache_read"], cache_write=e["cache_write"],
                stop_reason=e["stop_reason"], tools=e["tools"],
                tool_count=e["tool_count"], msg_uuid=e["msg_id"],
                prompt_preview=user_before.get(e["msg_id"], ""),
            )

    return inserted, skipped, errors


# ── Provider: OpenClaw ────────────────────────────────────────────────────────

def import_openclaw(conn, cutoff: str | None):
    patterns = [
        str(HOME / ".openclaw/agents/**/*.jsonl"),
        str(HOME / ".clawdbot/agents/**/*.jsonl"),
        str(HOME / ".moltbot/agents/**/*.jsonl"),
        str(HOME / ".moldbot/agents/**/*.jsonl"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat, recursive=True))

    inserted = skipped = errors = 0

    for fpath in files:
        try:
            # First pass: build traceId → prompt text map from prompt.submitted events
            prompts: dict[str, str] = {}
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if d.get("type") != "prompt.submitted":
                        continue
                    trace  = d.get("traceId", "")
                    prompt = (d.get("data") or {}).get("prompt", "")
                    if trace and prompt:
                        text = " ".join(str(prompt).split())
                        prompts[trace] = text[:800]

            # Second pass: process model.completed events
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        errors += 1
                        continue

                    if d.get("type") != "model.completed":
                        continue

                    ts      = d.get("ts", "")
                    model   = d.get("modelId", "")
                    trace   = d.get("traceId", "")
                    seq     = d.get("seq", 0)
                    data    = d.get("data", {})
                    usage   = data.get("usage", {})

                    if not ts or not model:
                        continue

                    input_tok   = usage.get("input", 0) or 0
                    output_tok  = usage.get("output", 0) or 0
                    cache_write = usage.get("cacheWrite", 0) or 0
                    cache_read  = usage.get("cacheRead", 0) or 0

                    if input_tok == 0 and output_tok == 0:
                        continue

                    msg_uuid  = f"{trace}:{seq}"
                    preview   = prompts.get(trace, "")

                    if cutoff and ts[:19] >= cutoff:
                        if preview and msg_uuid:
                            conn.execute(
                                "UPDATE requests SET prompt_preview=? "
                                "WHERE msg_uuid=? AND (prompt_preview IS NULL OR prompt_preview='')",
                                (preview, msg_uuid),
                            )
                        skipped += 1
                        continue

                    inserted += _insert(
                        conn, ts=ts, source="openclaw-history", model=model,
                        input_tok=input_tok, output_tok=output_tok,
                        cache_read=cache_read, cache_write=cache_write,
                        stop_reason="", tools=[], tool_count=0,
                        msg_uuid=msg_uuid,
                        prompt_preview=preview,
                    )
        except (OSError, PermissionError):
            continue

    return inserted, skipped, errors


# ── Provider: Cline / Roo Code / Kilo Code (VSCode extensions) ───────────────

def _find_vscode_extension_tasks(extension_id: str) -> list[Path]:
    """Find all task directories for a VSCode extension across all install locations."""
    tasks = []
    for base in VSCODE_GLOBS:
        p = Path(base) / extension_id / "tasks"
        if p.exists():
            tasks.extend(p.iterdir())
    return tasks


def _parse_cline_tokens_from_text(text: str) -> dict | None:
    """
    Cline writes token data as a JSON object in the 'text' field of api_req_started events.
    Format: {"request":"...", "tokensIn":N, "tokensOut":N, "cacheWrites":N, "cacheReads":N, "cost":N}
    """
    if not text:
        return None
    try:
        d = json.loads(text)
        if isinstance(d, dict) and ("tokensIn" in d or "tokensOut" in d):
            return d
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def import_cline_family(conn, extension_id: str, source: str, cutoff: str | None):
    """
    Import from Cline-family VSCode extensions (Cline, Roo Code, Kilo Code).
    Each task directory contains ui_messages.json with api_req_started events.
    Dedup key: source:taskId:eventIndex
    """
    task_dirs = _find_vscode_extension_tasks(extension_id)
    inserted = skipped = errors = 0

    for task_dir in task_dirs:
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        ui_file = task_dir / "ui_messages.json"
        if not ui_file.exists():
            continue

        # Get model from api_conversation_history.json if available
        model = "cline-auto"
        conv_file = task_dir / "api_conversation_history.json"
        if conv_file.exists():
            try:
                conv = json.loads(conv_file.read_text())
                if isinstance(conv, list):
                    for msg in conv:
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            m = re.search(r"<model>(.*?)</model>", content)
                            if m:
                                model = m.group(1).strip()
                                break
            except Exception:
                pass

        # Fallback: modelInfo in first ui message
        try:
            messages = json.loads(ui_file.read_text())
        except Exception:
            errors += 1
            continue

        if not isinstance(messages, list):
            continue

        # Extract model from modelInfo field
        for msg in messages:
            mi = msg.get("modelInfo")
            if mi and isinstance(mi, dict):
                raw_model = mi.get("modelId", "")
                if raw_model:
                    # openrouter/anthropic/claude-sonnet-4.5 → claude-sonnet-4-5
                    if "/" in raw_model:
                        raw_model = raw_model.split("/")[-1].replace(".", "-")
                    model = raw_model
                break

        # Parse api_req_started events
        api_index = 0
        for msg in messages:
            if msg.get("say") != "api_req_started":
                continue

            tokens = _parse_cline_tokens_from_text(msg.get("text", ""))
            if not tokens:
                api_index += 1
                continue

            input_tok   = int(tokens.get("tokensIn", 0) or 0)
            output_tok  = int(tokens.get("tokensOut", 0) or 0)
            cache_write = int(tokens.get("cacheWrites", 0) or 0)
            cache_read  = int(tokens.get("cacheReads", 0) or 0)
            cost_raw    = float(tokens.get("cost", 0) or 0)

            if input_tok == 0 and output_tok == 0 and cost_raw == 0:
                api_index += 1
                continue

            # Timestamp: Cline stores unix ms in 'ts'
            ts_ms = msg.get("ts", 0)
            if ts_ms:
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
            else:
                ts = datetime.now(timezone.utc).isoformat()

            if cutoff and ts[:19] >= cutoff:
                skipped += 1
                api_index += 1
                continue

            msg_uuid = f"{source}:{task_id}:{api_index}"
            # Use reported cost if available, else calculate
            cost_override = cost_raw if cost_raw > 0 else None

            inserted += _insert(
                conn, ts=ts, source=source, model=model,
                input_tok=input_tok, output_tok=output_tok,
                cache_read=cache_read, cache_write=cache_write,
                stop_reason="", tools=[], tool_count=0,
                msg_uuid=msg_uuid, cost_override=cost_override,
            )
            api_index += 1

    return inserted, skipped, errors








# ── Provider: IBM Bob (same format as Cline) ─────────────────────────────────

def import_ibm_bob(conn):
    return import_cline_family(conn, "ibm.bob-code", "ibm-bob-history", None)


# ── Provider: SQLite helpers ──────────────────────────────────────────────────

def _open_sqlite_ro(path: Path):
    """Open SQLite in read-only mode (avoids locking issues with running apps)."""
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True)





# ── Provider: GitHub Copilot (usage tracking, no pricing) ────────────────────

def import_copilot(conn):
    """GitHub Copilot: parse log lines for usage tracking (model + time, no token counts)."""
    log_bases = [
        Path(HOME) / "AppData/Roaming/Code/logs",              # Windows
        Path(HOME) / "Library/Application Support/Code/logs",  # macOS
        Path(HOME) / ".config/Code/logs",                      # Linux
    ]
    log_path = next((p for p in log_bases if p.exists()), None)
    if log_path is None:
        return 0, 0, 0

    inserted = errors = 0
    # Find all GitHub Copilot Chat log files
    for log_file in log_path.glob("*/window*/exthost/GitHub.copilot-chat/GitHub Copilot Chat.log"):
        try:
            with open(log_file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # Format: "2026-06-01 10:08:47.566 [info] ccreq:53102d5e.copilotmd | success | gpt-5-mini -> gpt-5-mini-2025-08-07 | 7408ms | [panel/editAgent]"
                    if "ccreq:" not in line or "| success |" not in line:
                        continue
                    try:
                        parts = line.split("|")
                        if len(parts) < 3:
                            continue
                        # Extract timestamp
                        ts_str = line.split("[info]")[0].strip()
                        ts = ts_str if ts_str else None
                        # Extract request ID
                        req_id = line.split("ccreq:")[1].split(".")[0] if "ccreq:" in line else ""
                        # Extract model (may have arrow like "gpt-5-mini -> gpt-5-mini-2025-08-07")
                        model_part = parts[2].strip() if len(parts) > 2 else ""
                        model = model_part.split("->")[-1].strip() if model_part else "copilot-unknown"
                        # Extract duration
                        dur_str = parts[3].strip() if len(parts) > 3 else "0ms"
                        duration_ms = int(dur_str.replace("ms", "")) if "ms" in dur_str else 0

                        if not ts or not req_id:
                            continue

                        msg_uuid = f"copilot:{req_id}"
                        # Insert with cost_usd = 0 (no pricing data available)
                        inserted += _insert(
                            conn, ts=ts or "1970-01-01T00:00:00Z",
                            source="copilot", model=model,
                            input_tok=0, output_tok=0,
                            cache_read=0, cache_write=0,
                            stop_reason="", tools=[], tool_count=0,
                            msg_uuid=msg_uuid, cost_override=0.0,
                            prompt_preview=f"[Copilot {model}]"
                        )
                    except Exception:
                        errors += 1
        except (OSError, PermissionError):
            errors += 1
    return inserted, 0, errors


# ── Fuzzy backfill: fill prompt_preview for proxy records from JSONL ──────────

def _backfill_previews_from_jsonl(conn) -> int:
    """
    Match proxy-captured records (msg_uuid IS NULL, no preview) to their
    corresponding JSONL entries using (model, input_tokens, output_tokens, ts[:16]).
    Returns number of records updated.
    """
    from collections import defaultdict

    cutoff_row = conn.execute("""
        SELECT MIN(ts) FROM requests
        WHERE source NOT IN ('claude-cli-history','claude-desktop-history','openclaw-history',
                             'cline-history','roo-code-history','kilo-code-history')
    """).fetchone()
    cutoff = cutoff_row[0][:19] if (cutoff_row and cutoff_row[0]) else None
    if not cutoff:
        return 0

    # Collect existing msg_uuids to avoid UNIQUE conflicts
    existing_uuids = {r[0] for r in conn.execute(
        "SELECT msg_uuid FROM requests WHERE msg_uuid IS NOT NULL")}

    # Read all Claude CLI JSONL files and collect post-cutoff entries
    msg_map: dict[str, dict] = {}
    patterns = [
        str(HOME / ".claude/projects/**/*.jsonl"),
        str(HOME / "AppData/Roaming/Claude/projects/**/*.jsonl"),              # Windows
        str(HOME / "Library/Application Support/Claude/projects/**/*.jsonl"),  # macOS
        str(HOME / ".config/Claude/projects/**/*.jsonl"),                      # Linux
    ]
    files: list[str] = []
    for pat in patterns:
        files.extend(glob.glob(pat, recursive=True))

    for fpath in files:
        last_user = ""
        try:
            with open(fpath, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    dtype = d.get("type", "")
                    if dtype == "user":
                        content = d.get("message", {}).get("content", "")
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            text = " ".join(
                                b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        else:
                            text = ""
                        text = " ".join(text.split())
                        if text:
                            last_user = text
                        continue
                    if dtype != "assistant":
                        continue
                    msg    = d.get("message", {})
                    msg_id = msg.get("id", "")
                    ts     = d.get("timestamp", "")
                    if not msg_id or not ts or ts[:19] < cutoff:
                        continue
                    if msg_id in existing_uuids or msg_id in msg_map:
                        continue
                    u = msg.get("usage", {})
                    inp = u.get("input_tokens", 0) or 0
                    out = u.get("output_tokens", 0) or 0
                    if inp == 0 and out == 0:
                        continue
                    msg_map[msg_id] = {
                        "ts16":   ts[:16],
                        "ts13":   ts[:13],
                        "inp":    inp,
                        "out":    out,
                        "model":  msg.get("model", ""),
                        "prompt": last_user[:800],
                    }
        except (OSError, PermissionError):
            continue

    if not msg_map:
        return 0

    # Index by (model, inp, out, ts[:16]) for fast lookup
    by_key: dict = defaultdict(list)
    for mid, e in msg_map.items():
        by_key[(e["model"], e["inp"], e["out"], e["ts16"])].append((mid, e["prompt"]))

    # Fetch proxy records without preview
    proxy = conn.execute("""
        SELECT id, ts, model, input_tokens, output_tokens
        FROM requests
        WHERE msg_uuid IS NULL
          AND (prompt_preview IS NULL OR prompt_preview = '')
          AND input_tokens > 0
        ORDER BY ts
    """).fetchall()

    updated = 0
    for rid, ts, model, inp, out in proxy:
        ts16 = ts[:16]
        ts13 = ts[:13]
        # Try exact minute match first, then same hour
        candidates = by_key.get((model, inp, out, ts16), [])
        if not candidates:
            candidates = [
                (mid, p) for key, matches in by_key.items()
                if key[0] == model and key[1] == inp and key[2] == out and key[3][:13] == ts13
                for mid, p in matches
            ]
        if len(candidates) == 1:
            mid, prompt = candidates[0]
            try:
                conn.execute(
                    "UPDATE requests SET prompt_preview=?, msg_uuid=? WHERE id=?",
                    (prompt, mid, rid),
                )
                # Prevent reuse of this uuid for other records
                by_key[(model, inp, out, ts16)] = [(m, p) for m, p in by_key.get((model, inp, out, ts16), []) if m != mid]
                updated += 1
            except Exception:
                pass

    conn.commit()
    return updated


# ── Main ──────────────────────────────────────────────────────────────────────

def import_all(verbose: bool = True) -> dict:
    _init_db()
    conn = sqlite3.connect(DB_PATH)
    ensure_schema(conn)
    cutoff = _proxy_cutoff(conn)

    results = {}

    # 1. Claude CLI (with proxy cutoff — proxy captures live traffic)
    ins, skip, err = _import_claude_jsonl(conn, [
        str(HOME / ".claude/projects/*/*.jsonl"),
        str(HOME / "AppData/Roaming/Claude/projects/*/*.jsonl"),              # Windows
        str(HOME / "Library/Application Support/Claude/projects/*/*.jsonl"),  # macOS
        str(HOME / ".config/Claude/projects/*/*.jsonl"),                      # Linux
    ], "claude-cli-history", cutoff)
    results["Claude CLI"] = (ins, skip, err)

    # 2. Claude Desktop (NO cutoff — proxy never sees Desktop sessions)
    ins, skip, err = _import_claude_jsonl(conn, [
        str(HOME / "AppData/Roaming/Claude/local-agent-mode-sessions/**/*.jsonl"),              # Windows
        str(HOME / "Library/Application Support/Claude/local-agent-mode-sessions/**/*.jsonl"),  # macOS
        str(HOME / ".config/Claude/local-agent-mode-sessions/**/*.jsonl"),                      # Linux
    ], "claude-desktop-history", None)
    results["Claude Desktop"] = (ins, skip, err)

    # 3. OpenClaw (with proxy cutoff)
    ins, skip, err = import_openclaw(conn, cutoff)
    results["OpenClaw"] = (ins, skip, err)

    # 4. Cline VSCode extension (no cutoff — proxy doesn't route Cline)
    ins, skip, err = import_cline_family(
        conn, "saoudrizwan.claude-dev", "cline-history", None)
    results["Cline"] = (ins, skip, err)

    # 5. Roo Code (same format as Cline)
    ins, skip, err = import_cline_family(
        conn, "rooveterinaryinc.roo-cline", "roo-code-history", None)
    results["Roo Code"] = (ins, skip, err)

    # 6. Kilo Code (same format as Cline)
    ins, skip, err = import_cline_family(
        conn, "kilocode.kilo-code", "kilo-code-history", None)
    results["Kilo Code"] = (ins, skip, err)

    # 7. IBM Bob (same JSON format as Cline)
    ins, skip, err = import_ibm_bob(conn)
    results["IBM Bob"] = (ins, skip, err)

    # 8. GitHub Copilot (usage tracking only, no pricing)
    ins, skip, err = import_copilot(conn)
    results["Copilot"] = (ins, skip, err)

    # Backfill prompt_preview for proxy-captured records by matching JSONL files
    backfilled = _backfill_previews_from_jsonl(conn)
    if backfilled:
        results["_backfill"] = (backfilled, 0, 0)

    _save_last_sync(conn)
    conn.commit()
    conn.close()
    return results


_VERSION_CACHE = Path(__file__).parent / ".version_cache.json"
_GITHUB_REPO   = "mr-beaver/tokencost"

def check_version_and_cache():
    import json, urllib.request
    local_ver_path = Path(__file__).parent / "VERSION"
    try:
        current = local_ver_path.read_text().strip()
    except Exception:
        return

    # re-check only once per day
    if _VERSION_CACHE.exists():
        try:
            cached = json.loads(_VERSION_CACHE.read_text())
            age = time.time() - cached.get("checked_at", 0)
            if age < 86400 and cached.get("current") == current:
                return  # still fresh
        except Exception:
            pass

    try:
        url = f"https://raw.githubusercontent.com/{_GITHUB_REPO}/main/VERSION"
        req = urllib.request.Request(url, headers={"User-Agent": "tokencost"})
        with urllib.request.urlopen(req, timeout=5) as r:
            latest = r.read().decode().strip()
        _dir = Path(__file__).parent
        if sys.platform == "win32":
            update_cmd = f'cd /d "{_dir}" && git pull && powershell -ExecutionPolicy Bypass -File onbording.ps1'
        else:
            update_cmd = f"cd {_dir} && git pull && bash onbording.sh"
        result = {
            "current":    current,
            "latest":     latest,
            "up_to_date": latest == current,
            "checked_at": time.time(),
            "update_cmd": update_cmd,
        }
        _VERSION_CACHE.write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    silent = "--silent" in sys.argv
    if not silent:
        print("  Scanning provider logs...")

    t0 = time.time()
    results = import_all()
    elapsed = time.time() - t0

    total_new  = sum(r[0] for r in results.values())
    total_skip = sum(r[1] for r in results.values())

    if not silent:
        for provider, (ins, skip, err) in results.items():
            if ins or skip:
                line = f"  {provider}: +{ins} new"
                if skip:
                    line += f"  (skipped {skip})"
                print(line)
        print(f"  {'─'*38}")
        print(f"  Total new: {total_new}  ({elapsed:.1f}s)")
    elif total_new > 0:
        # Silent mode: only print if something changed (for log file)
        print(f"[import] +{total_new} new records from {sum(1 for r in results.values() if r[0])} providers")

    check_version_and_cache()
