"""Regression-safety suite for numeric aggregations in db.py.

Strategy: seed a known SQLite DB via the `seed_requests` fixture, call the
aggregation with period="all" (clock-independent), then assert exact figures
computed by hand from the seed values.

Intentionally OUT OF SCOPE (prose / heuristic engines):
  - _recommendations  — free-text advice, not a numeric rollup
  - _action_plan      — same
  - _health_grade     — letter-grade heuristic
  - _projection       — calendar-day estimate, inherently time-dependent
"""

import datetime
import json

import pytest

import db


# ── Pure helper functions ─────────────────────────────────────────────────────

class TestPeriodClause:
    def test_all_returns_empty_string(self):
        assert db._period_clause("all") == ""

    def test_unknown_period_returns_empty_string(self):
        assert db._period_clause("never") == ""

    def test_today_clause(self):
        clause = db._period_clause("today")
        assert "localtime" in clause
        assert "date(" in clause

    def test_7d_clause(self):
        clause = db._period_clause("7d")
        assert "-7 days" in clause

    def test_30d_clause(self):
        clause = db._period_clause("30d")
        assert "-30 days" in clause


class TestNaiveDt:
    def test_plain_iso_string_returned_as_is(self):
        dt = db._naive_dt("2026-01-15T10:30:00")
        assert dt == datetime.datetime(2026, 1, 15, 10, 30, 0)
        assert dt.tzinfo is None

    def test_tz_aware_string_strips_tzinfo(self):
        dt = db._naive_dt("2026-01-15T10:30:00+00:00")
        assert dt.tzinfo is None
        assert dt == datetime.datetime(2026, 1, 15, 10, 30, 0)


class TestFmtMs:
    def test_zero(self):
        assert db._fmt_ms(0) == "0s"

    def test_none_falsy(self):
        assert db._fmt_ms(None) == "0s"

    def test_sub_second(self):
        assert db._fmt_ms(500) == "500ms"

    def test_999ms(self):
        assert db._fmt_ms(999) == "999ms"

    def test_exactly_one_second(self):
        assert db._fmt_ms(1000) == "1s"

    def test_seconds(self):
        assert db._fmt_ms(5000) == "5s"

    def test_59_seconds(self):
        assert db._fmt_ms(59_000) == "59s"

    def test_exactly_one_minute(self):
        assert db._fmt_ms(60_000) == "1m"

    def test_minutes_and_seconds(self):
        assert db._fmt_ms(90_000) == "1m 30s"

    def test_two_minutes_exact(self):
        assert db._fmt_ms(120_000) == "2m"

    def test_large_value(self):
        # 3661 seconds = 61m 1s
        assert db._fmt_ms(3_661_000) == "61m 1s"


# ── _cost_breakdown ───────────────────────────────────────────────────────────
#
# Formula (from source):
#   input:          inp * p["input"]        / 1e6
#   output:         out * p["output"]       / 1e6
#   cache_read:     cr  * p["input"] * 0.10 / 1e6
#   cache_creation: cw  * p["input"] * 1.25 / 1e6  (always 1.25x in breakdown)
#
# Seed: 1 opus-4-8 request: 200k inp, 10k out, 50k cr, 20k cw
#   p = {"input": 5.0, "output": 25.0}
#   input          = 200_000 * 5.0  / 1e6 = 1.0
#   output         = 10_000  * 25.0 / 1e6 = 0.25
#   cache_read     = 50_000  * 5.0  * 0.10 / 1e6 = 0.025
#   cache_creation = 20_000  * 5.0  * 1.25 / 1e6 = 0.125

class TestCostBreakdown:
    def test_single_opus_row(self, seed_requests):
        seed_requests(
            model="claude-opus-4-8",
            input_tokens=200_000,
            output_tokens=10_000,
            cache_read_tokens=50_000,
            cache_creation_tokens=20_000,
        )
        bd = db._cost_breakdown("all")
        assert bd["input"]          == pytest.approx(1.0,     rel=1e-4)
        assert bd["output"]         == pytest.approx(0.25,    rel=1e-4)
        assert bd["cache_read"]     == pytest.approx(0.025,   rel=1e-4)
        assert bd["cache_creation"] == pytest.approx(0.125,   rel=1e-4)

    def test_two_models_add_up(self, seed_requests):
        # sonnet-4-6: p = {"input": 3.0, "output": 15.0}
        # opus: 100k inp → 0.5; sonnet: 100k inp → 0.3  → total = 0.8
        seed_requests(model="claude-opus-4-8",    input_tokens=100_000)
        seed_requests(model="claude-sonnet-4-6",  input_tokens=100_000)
        bd = db._cost_breakdown("all")
        assert bd["input"] == pytest.approx(0.8, rel=1e-4)  # 0.5 + 0.3

    def test_empty_db_returns_zeros(self, seed_requests):
        # seed_requests fixture depends on tmp_db; calling no inserts gives empty.
        bd = db._cost_breakdown("all")
        assert bd == {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_creation": 0.0}

    def test_unknown_model_uses_default_pricing(self, seed_requests):
        # default = {"input": 3.0, "output": 15.0}; 1M inp → 3.0
        seed_requests(model="some-unknown-model", input_tokens=1_000_000)
        bd = db._cost_breakdown("all")
        assert bd["input"] == pytest.approx(3.0, rel=1e-4)


# ── _cache_savings ────────────────────────────────────────────────────────────
#
# Formula: saved = cr * p["input"] * 0.9 / 1e6
# Seed: 1M cache_read on opus-4-8 (input=$5): 1_000_000 * 5.0 * 0.9 / 1e6 = 4.5

class TestCacheSavings:
    def test_opus_1m_cache_read(self, seed_requests):
        seed_requests(model="claude-opus-4-8", cache_read_tokens=1_000_000)
        assert db._cache_savings("all") == pytest.approx(4.5, rel=1e-4)

    def test_sonnet_500k_cache_read(self, seed_requests):
        # sonnet input=$3: 500_000 * 3.0 * 0.9 / 1e6 = 1.35
        seed_requests(model="claude-sonnet-4-6", cache_read_tokens=500_000)
        assert db._cache_savings("all") == pytest.approx(1.35, rel=1e-4)

    def test_two_model_savings_add_up(self, seed_requests):
        # opus 1M = 4.5; haiku 1M = haiku input=$1: 1_000_000 * 1.0 * 0.9 / 1e6 = 0.9
        # total = 5.4
        seed_requests(model="claude-opus-4-8",   cache_read_tokens=1_000_000)
        seed_requests(model="claude-haiku-4-5",  cache_read_tokens=1_000_000)
        assert db._cache_savings("all") == pytest.approx(5.4, rel=1e-4)

    def test_no_cache_reads_returns_zero(self, seed_requests):
        seed_requests(model="claude-opus-4-8", input_tokens=1000)
        assert db._cache_savings("all") == 0.0


# ── _haiku_savings ────────────────────────────────────────────────────────────
#
# Computes what it would cost if all non-haiku requests used haiku pricing.
# haiku_pricing = {"input": 1.0, "output": 5.0}
#
# Seed 1 opus request: 1000 inp, 200 out, cost_usd=0.01
#   haiku_equiv = (1000*1.0 + 200*5.0) / 1e6 = 0.0012
#   savings     = 0.01 - 0.0012 = 0.0088

class TestHaikuSavings:
    def test_single_opus_row(self, seed_requests):
        # haiku_equiv = (1000*1.0 + 200*5.0) / 1e6 = (1000+1000) / 1e6 = 0.002
        # savings     = 0.01 - 0.002 = 0.008
        seed_requests(
            model="claude-opus-4-8",
            input_tokens=1_000,
            output_tokens=200,
            cost_usd=0.01,
        )
        hs = db._haiku_savings("all")
        assert hs["actual"]           == pytest.approx(0.01,  rel=1e-4)
        assert hs["haiku_equivalent"] == pytest.approx(0.002, rel=1e-4)
        assert hs["savings"]          == pytest.approx(0.008, rel=1e-4)
        assert hs["requests"]         == 1

    def test_haiku_rows_excluded(self, seed_requests):
        # Haiku requests are excluded from the "savings if we'd used haiku" calc
        seed_requests(model="claude-haiku-4-5", input_tokens=5000, cost_usd=0.05)
        hs = db._haiku_savings("all")
        assert hs["requests"] == 0
        assert hs["actual"] == 0.0
        assert hs["haiku_equivalent"] == 0.0

    def test_avg_tokens_computed_correctly(self, seed_requests):
        seed_requests(model="claude-opus-4-8", input_tokens=1000, output_tokens=200)
        seed_requests(model="claude-opus-4-8", input_tokens=3000, output_tokens=400)
        hs = db._haiku_savings("all")
        assert hs["avg_input_tokens"]  == 2000  # (1000+3000)/2
        assert hs["avg_output_tokens"] == 300   # (200+400)/2

    def test_effort_counts_rollup(self, seed_requests):
        seed_requests(model="claude-opus-4-8", effort="standard")
        seed_requests(model="claude-opus-4-8", effort="standard")
        seed_requests(model="claude-opus-4-8", effort="high")
        hs = db._haiku_savings("all")
        assert hs["effort_counts"]["standard"] == 2
        assert hs["effort_counts"]["high"]     == 1


# ── get_optimizer_stats ───────────────────────────────────────────────────────

class TestGetOptimizerStats:
    def test_empty_returns_zero_totals(self, seed_requests):
        # No rows with optimizer_savings_usd > 0
        seed_requests(cost_usd=0.05)
        stats = db.get_optimizer_stats("all")
        assert stats["total_saved"]  == 0
        assert stats["event_count"]  == 0
        assert stats["by_type"]      == {}

    def test_single_routing_optimization(self, seed_requests):
        opt = json.dumps([{"type": "routing", "saved_usd": 0.05, "from": "claude-opus-4-8", "to": "claude-haiku-4-5"}])
        seed_requests(
            model="claude-haiku-4-5",
            cost_usd=0.01,
            optimizer_savings_usd=0.05,
            optimizations_json=opt,
        )
        stats = db.get_optimizer_stats("all")
        assert stats["total_saved"]              == pytest.approx(0.05, rel=1e-4)
        assert stats["actual_spent"]             == pytest.approx(0.01, rel=1e-4)
        assert stats["by_type"]["routing"]["count"]  == 1
        assert stats["by_type"]["routing"]["saved"]  == pytest.approx(0.05, rel=1e-4)

    def test_roi_percent_formula(self, seed_requests):
        # roi = saved / (saved + spent) * 100
        # saved=1.0, spent=9.0 → roi = 1/(1+9)*100 = 10.0%
        opt = json.dumps([{"type": "cache", "saved_usd": 1.0}])
        seed_requests(cost_usd=9.0, optimizer_savings_usd=1.0, optimizations_json=opt)
        stats = db.get_optimizer_stats("all")
        assert stats["roi_percent"] == pytest.approx(10.0, rel=1e-3)

    def test_multiple_optimization_types(self, seed_requests):
        opt = json.dumps([
            {"type": "routing", "saved_usd": 0.10},
            {"type": "cache",   "saved_usd": 0.05},
        ])
        seed_requests(cost_usd=0.02, optimizer_savings_usd=0.15, optimizations_json=opt)
        stats = db.get_optimizer_stats("all")
        assert stats["total_saved"]               == pytest.approx(0.15, rel=1e-4)
        assert stats["by_type"]["routing"]["saved"] == pytest.approx(0.10, rel=1e-4)
        assert stats["by_type"]["cache"]["saved"]   == pytest.approx(0.05, rel=1e-4)

    def test_actual_spent_includes_all_requests(self, seed_requests):
        # actual_spent = SUM(cost_usd) for ALL rows in period, not just optimized ones
        seed_requests(cost_usd=5.0)           # plain request, no optimizer
        opt = json.dumps([{"type": "routing", "saved_usd": 0.10}])
        seed_requests(cost_usd=1.0, optimizer_savings_usd=0.10, optimizations_json=opt)
        stats = db.get_optimizer_stats("all")
        assert stats["actual_spent"] == pytest.approx(6.0, rel=1e-4)


# ── _effort_breakdown ─────────────────────────────────────────────────────────

class TestEffortBreakdown:
    def test_single_effort_group(self, seed_requests):
        seed_requests(model="claude-opus-4-8", effort="standard", cost_usd=0.10,
                      input_tokens=500, output_tokens=100)
        seed_requests(model="claude-opus-4-8", effort="standard", cost_usd=0.20,
                      input_tokens=1000, output_tokens=200)
        rows = db._effort_breakdown("all")
        assert len(rows) == 1
        r = rows[0]
        assert r["model"]  == "claude-opus-4-8"
        assert r["effort"] == "standard"
        assert r["reqs"]   == 2
        assert r["cost"]   == pytest.approx(0.30, rel=1e-4)
        assert r["avg_inp"] == pytest.approx(750)   # (500+1000)/2
        assert r["avg_out"] == pytest.approx(150)   # (100+200)/2

    def test_two_effort_groups_sorted_by_cost(self, seed_requests):
        seed_requests(model="claude-opus-4-8", effort="standard", cost_usd=0.01)
        seed_requests(model="claude-opus-4-8", effort="high",     cost_usd=0.99)
        rows = db._effort_breakdown("all")
        assert len(rows) == 2
        # sorted by cost DESC — high first
        assert rows[0]["effort"] == "high"
        assert rows[1]["effort"] == "standard"

    def test_null_effort_coalesces_to_standard(self, seed_requests):
        # Insert a row without specifying effort — defaults to "standard" in seed
        seed_requests(model="claude-opus-4-8", effort="standard", cost_usd=0.05)
        rows = db._effort_breakdown("all")
        assert rows[0]["effort"] == "standard"


# ── _tool_breakdown ───────────────────────────────────────────────────────────

class TestToolBreakdown:
    def test_single_tool_counted(self, seed_requests):
        seed_requests(tools_json='["read", "edit"]')
        rows = db._tool_breakdown("all")
        names = {r["name"] for r in rows}
        assert "Read" in names
        assert "Edit" in names

    def test_counts_across_multiple_rows(self, seed_requests):
        seed_requests(tools_json='["read"]')
        seed_requests(tools_json='["read", "write"]')
        rows = db._tool_breakdown("all")
        by_name = {r["name"]: r["count"] for r in rows}
        assert by_name["Read"]  == 2
        assert by_name["Write"] == 1

    def test_sorted_by_count_descending(self, seed_requests):
        seed_requests(tools_json='["bash", "bash", "read"]')
        rows = db._tool_breakdown("all")
        # First-letter capitalisation: "bash"→"Bash", "read"→"Read"
        assert rows[0]["name"] == "Bash"
        assert rows[0]["count"] >= rows[1]["count"]

    def test_empty_tools_json_skipped(self, seed_requests):
        # No tools_json set → no breakdown rows
        seed_requests()
        rows = db._tool_breakdown("all")
        assert rows == []


# ── get_stats (top-level summary) ────────────────────────────────────────────
#
# Seeds two opus rows with known values; asserts the summary-level aggregates.

class TestGetStatsSummary:
    def _seed_two_rows(self, seed_requests):
        # Row 1: 1000 inp, 100 out, 0 cache, cost=0.0075
        seed_requests(
            model="claude-opus-4-8",
            input_tokens=1000,
            output_tokens=100,
            cost_usd=0.0075,
            duration_ms=200,
        )
        # Row 2: 2000 inp, 200 out, 500 cache_read, cost=0.015
        seed_requests(
            model="claude-opus-4-8",
            input_tokens=2000,
            output_tokens=200,
            cache_read_tokens=500,
            cost_usd=0.015,
            duration_ms=400,
        )

    def test_total_requests(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        assert stats["summary"]["total_requests"] == 2

    def test_total_input_tokens(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        assert stats["summary"]["total_input"] == 3000  # 1000 + 2000

    def test_total_output_tokens(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        assert stats["summary"]["total_output"] == 300  # 100 + 200

    def test_total_cache_read(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        assert stats["summary"]["total_cache_read"] == 500

    def test_total_cost(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        assert stats["summary"]["total_cost"] == pytest.approx(0.0225, rel=1e-4)

    def test_avg_ms(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        assert stats["summary"]["avg_ms"] == pytest.approx(300)  # (200+400)/2

    def test_total_api_fmt(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        # total_api_ms = 600 → "600ms"
        assert stats["summary"]["total_api_fmt"] == "600ms"

    def test_output_per_dollar(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        # 300 output / 0.0225 cost = 13333.33 → round → 13333
        assert stats["summary"]["output_per_dollar"] == round(300 / 0.0225)

    def test_output_per_dollar_zero_cost(self, seed_requests):
        seed_requests(output_tokens=100, cost_usd=0.0)
        stats = db.get_stats("all")
        assert stats["summary"]["output_per_dollar"] == 0

    def test_by_model_cache_hit_rate(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        model_row = stats["by_model"][0]
        # total_toks = inp(3000) + cache_read(500) + cache_creation(0) = 3500
        # cache_hit_rate = 500/3500*100 = 14.3
        assert model_row["cache_hit_rate"] == pytest.approx(500 / 3500 * 100, rel=1e-2)

    def test_by_model_avg_inp_out(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        model_row = stats["by_model"][0]
        assert model_row["avg_inp"] == round(3000 / 2)  # 1500
        assert model_row["avg_out"] == round(300  / 2)  # 150

    def test_cache_saved_key_present_and_correct(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        # 500 cache_read * 5.0 * 0.9 / 1e6 = 0.00225, rounded to 4dp = 0.0022 (banker's rounding)
        assert stats["cache_saved"] == pytest.approx(round(500 * 5.0 * 0.9 / 1e6, 4), rel=1e-3)

    def test_cost_breakdown_present(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        bd = stats["cost_breakdown"]
        assert "input" in bd
        assert "output" in bd
        assert "cache_read" in bd
        assert "cache_creation" in bd

    def test_haiku_savings_key_present(self, seed_requests):
        self._seed_two_rows(seed_requests)
        stats = db.get_stats("all")
        hs = stats["haiku_savings"]
        assert "actual" in hs
        assert "savings" in hs
        assert "requests" in hs

    def test_by_source_collapses_history_suffix(self, seed_requests):
        seed_requests(source="vscode", cost_usd=0.10)
        seed_requests(source="vscode-history", cost_usd=0.05)
        stats = db.get_stats("all")
        sources = {r["source"] for r in stats["by_source"]}
        assert "vscode" in sources
        assert "vscode-history" not in sources

    def test_empty_db_returns_zero_totals(self, seed_requests):
        # seed_requests fixture gives us a fresh tmp_db; no inserts
        stats = db.get_stats("all")
        assert stats["summary"]["total_requests"]  == 0
        assert stats["summary"]["total_cost"]      is None or stats["summary"]["total_cost"] == 0 or stats["summary"]["total_cost"] == pytest.approx(0)


# ── get_sessions ──────────────────────────────────────────────────────────────
#
# Session gap = 30 min. Two rows within the same session.

class TestGetSessions:
    def test_single_session_two_requests(self, seed_requests):
        base = datetime.datetime(2026, 3, 1, 10, 0, 0)
        seed_requests(ts=base,                                     cost_usd=0.05, duration_ms=1000)
        seed_requests(ts=base + datetime.timedelta(minutes=5),     cost_usd=0.10, duration_ms=2000)
        sessions = db.get_sessions("all")
        assert len(sessions) == 1
        s = sessions[0]
        assert s["req_count"]  == 2
        assert s["total_cost"] == pytest.approx(0.15, rel=1e-4)

    def test_two_sessions_separated_by_gap(self, seed_requests):
        base = datetime.datetime(2026, 3, 1, 10, 0, 0)
        seed_requests(ts=base,                                     cost_usd=0.05, duration_ms=500)
        # 31-minute gap → new session
        seed_requests(ts=base + datetime.timedelta(minutes=31),    cost_usd=0.10, duration_ms=500)
        sessions = db.get_sessions("all")
        assert len(sessions) == 2

    def test_session_api_ms(self, seed_requests):
        base = datetime.datetime(2026, 3, 1, 10, 0, 0)
        seed_requests(ts=base,                                  duration_ms=1000)
        seed_requests(ts=base + datetime.timedelta(minutes=2),  duration_ms=2000)
        sessions = db.get_sessions("all")
        assert sessions[0]["api_ms"] == 3000

    def test_single_request_session(self, seed_requests):
        seed_requests(cost_usd=0.07)
        sessions = db.get_sessions("all")
        assert len(sessions) == 1
        assert sessions[0]["req_count"] == 1

    def test_empty_db_returns_empty_list(self, seed_requests):
        sessions = db.get_sessions("all")
        assert sessions == []


# ── WAL connection helper + request-time ts ──────────────────────────────────
import sqlite3 as _sqlite3
import db as _db


class TestConnectHelper:
    def test_connect_enables_wal(self, tmp_db):
        con = _db._connect()
        try:
            mode = con.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            con.close()

    def test_connect_sets_busy_timeout(self, tmp_db):
        con = _db._connect()
        try:
            assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 3000
        finally:
            con.close()

    def test_connect_resolves_db_path_at_call_time(self, tmp_db):
        # tmp_db already monkeypatched db.DB_PATH; _connect must honor it,
        # not a path bound at import time.
        con = _db._connect()
        try:
            dbfile = con.execute("PRAGMA database_list").fetchone()[2]
            assert dbfile == tmp_db
        finally:
            con.close()


class TestSaveRequestTimestamp:
    def test_explicit_ts_is_stored(self, tmp_db):
        _db.save_request("cli", "claude-opus-4-8", 1, 1, 0, 0, 0.0, 10, 200,
                         msg_uuid="ts-explicit", ts="2026-06-23T00:00:00+00:00")
        con = _sqlite3.connect(tmp_db)
        ts = con.execute("SELECT ts FROM requests WHERE msg_uuid='ts-explicit'").fetchone()[0]
        con.close()
        assert ts == "2026-06-23T00:00:00+00:00"

    def test_omitted_ts_defaults_to_now(self, tmp_db):
        _db.save_request("cli", "claude-opus-4-8", 1, 1, 0, 0, 0.0, 10, 200,
                         msg_uuid="ts-default")
        con = _sqlite3.connect(tmp_db)
        ts = con.execute("SELECT ts FROM requests WHERE msg_uuid='ts-default'").fetchone()[0]
        con.close()
        assert ts and ts.startswith("20")  # an ISO timestamp was stamped
