from __future__ import annotations
"""
Parse Claude Code JSONL conversation logs to produce:
  - By Project  (grouped by cwd)
  - Top Sessions (most expensive across all projects)
  - Shell Commands (individual bash command counts)
"""

import glob
import json
import os
import time

CLAUDE_DIR   = os.path.expanduser("~/.claude/projects")
HOME_PREFIX  = os.path.expanduser("~") + os.sep
CACHE_TTL    = 120   # seconds

PRICING = {
    "claude-opus-4-7":            {"input": 5.0,   "output": 25.0},
    "claude-opus-4-6":            {"input": 5.0,   "output": 25.0},
    "claude-sonnet-4-6":          {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-5":          {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-20250514":   {"input": 3.0,   "output": 15.0},
    "claude-haiku-4-5-20251001":  {"input": 1.0,   "output": 5.0},
    "claude-haiku-4-5":           {"input": 1.0,   "output": 5.0},
    "claude-3-5-sonnet-20241022": {"input": 3.0,   "output": 15.0},
    "claude-3-5-haiku-20241022":  {"input": 0.8,   "output": 4.0},
    "claude-3-opus-20240229":     {"input": 15.0,  "output": 75.0},
    "claude-3-haiku-20240307":    {"input": 0.25,  "output": 1.25},
    "default":                    {"input": 3.0,   "output": 15.0},
}

_cache: dict = {}   # keyed by period


def _cutoff_ts(period: str) -> str | None:
    """Return ISO cutoff timestamp for period, or None for all-time."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    if period == "today":
        # Midnight local time → UTC (DST-safe, cross-platform)
        local_midnight = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0)
        return local_midnight.astimezone(timezone.utc).isoformat()
    if period == "7d":
        return (now - timedelta(days=7)).isoformat()
    if period == "30d":
        return (now - timedelta(days=30)).isoformat()
    return None   # "all" — no filter


def _cost(model: str, inp: int, out: int, cr: int, cw: int) -> float:
    p = PRICING.get(model) or PRICING["default"]
    return (inp * p["input"] + out * p["output"] +
            cr  * p["input"] * 0.10 +
            cw  * p["input"] * 1.25) / 1_000_000


def _abbrev(cwd: str) -> str:
    if cwd.startswith(HOME_PREFIX):
        return cwd[len(HOME_PREFIX):]
    return cwd


def _first_cmd(cmd: str) -> str | None:
    """Return the first executable name from a shell command string."""
    cmd = cmd.strip()
    for wrap in ("source ", "eval ", "sudo ", "env "):
        if cmd.startswith(wrap):
            cmd = cmd[len(wrap):]
    parts = cmd.split()
    if not parts:
        return None
    name = os.path.basename(parts[0])
    if not name or len(name) > 40 or name.startswith("-"):
        return None
    return name


def _process_file(path: str, projects: dict, shell_cmds: dict, cutoff: str | None = None) -> None:
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except Exception:
                continue

            if ev.get("type") != "assistant":
                continue

            ev_ts = ev.get("timestamp") or ""
            if cutoff and ev_ts and ev_ts < cutoff:
                continue

            cwd = ev.get("cwd") or ""
            if not cwd:
                continue

            sid     = ev.get("sessionId") or "unknown"
            proj    = _abbrev(cwd)
            ts      = ev_ts[:10]
            msg     = ev.get("message") or {}
            usage   = msg.get("usage") or {}
            model   = msg.get("model") or "default"

            inp = int(usage.get("input_tokens") or 0)
            out = int(usage.get("output_tokens") or 0)
            cr  = int(usage.get("cache_read_input_tokens") or 0)
            cw  = int(usage.get("cache_creation_input_tokens") or 0)
            cost = _cost(model, inp, out, cr, cw)

            # ── project / session accumulation ──────────────────────────
            proj_data = projects.setdefault(proj, {"sessions": {}})
            sess = proj_data["sessions"].setdefault(sid, {"cost": 0.0, "calls": 0, "date": ts})
            sess["cost"]  += cost
            sess["calls"] += 1
            if ts and not sess["date"]:
                sess["date"] = ts

            # ── shell commands from Bash tool_use ───────────────────────
            for blk in (msg.get("content") or []):
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                    cmd_str = (blk.get("input") or {}).get("command") or ""
                    if cmd_str:
                        name = _first_cmd(cmd_str)
                        if name:
                            shell_cmds[name] = shell_cmds.get(name, 0) + 1


def get_project_stats(period: str = "all") -> dict:
    now = time.time()
    cached = _cache.get(period)
    if cached and now - cached["ts"] < CACHE_TTL:
        return cached["data"]

    cutoff = _cutoff_ts(period)
    projects:   dict = {}
    shell_cmds: dict = {}

    if os.path.isdir(CLAUDE_DIR):
        for path in glob.glob(os.path.join(CLAUDE_DIR, "*", "*.jsonl")):
            try:
                _process_file(path, projects, shell_cmds, cutoff)
            except Exception:
                pass

    # ── By Project ──────────────────────────────────────────────────────
    by_project = []
    for proj, data in projects.items():
        sessions   = data["sessions"]
        total_cost = sum(s["cost"]  for s in sessions.values())
        total_calls= sum(s["calls"] for s in sessions.values())
        n_sess     = len(sessions)
        by_project.append({
            "path":            proj,
            "cost":            round(total_cost, 4),
            "calls":           total_calls,
            "sessions":        n_sess,
            "avg_per_session": round(total_cost / n_sess, 4) if n_sess else 0,
        })
    by_project.sort(key=lambda x: -x["cost"])

    # ── Top Sessions ────────────────────────────────────────────────────
    all_sessions = []
    for proj, data in projects.items():
        for sid, s in data["sessions"].items():
            all_sessions.append({
                "path":    proj,
                "date":    s["date"],
                "cost":    round(s["cost"],  4),
                "calls":   s["calls"],
            })
    all_sessions.sort(key=lambda x: -x["cost"])
    top_sessions = all_sessions[:10]

    # ── Shell Commands ──────────────────────────────────────────────────
    shell_commands = sorted(
        [{"name": k, "count": v} for k, v in shell_cmds.items()],
        key=lambda x: -x["count"],
    )[:20]

    result = {
        "period":        period,
        "by_project":    by_project,
        "top_sessions":  top_sessions,
        "shell_commands": shell_commands,
    }
    _cache[period] = {"ts": now, "data": result}
    return result
