"""Unit tests for the observability primitives."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from datascrubb.observability import (
    apply_retention,
    configure,
    correlation,
    is_enabled,
    observe,
    quality_check,
    quality_summary,
    recent_calcs,
    total_row_counts,
    trace,
)


@pytest.fixture
def obs_db(tmp_path: Path):
    """Configure observability with a fresh per-test SQLite path."""
    db = tmp_path / "obs.db"
    configure(enabled=True, db_path=db)
    yield db
    # Tear down: disable so the next test starts clean
    configure(enabled=False, db_path="data/observability.db")


# ─── disabled mode ─────────────────────────────────────────────────

def test_disabled_observe_is_pass_through(tmp_path):
    configure(enabled=False, db_path=tmp_path / "should_not_exist.db")
    assert not is_enabled()

    @observe("noop")
    def f(x):
        return x * 2

    assert f(3) == 6
    # No DB file created
    assert not (tmp_path / "should_not_exist.db").exists()


def test_disabled_quality_check_returns_truth_value():
    configure(enabled=False)
    assert quality_check("x", True) is True
    assert quality_check("x", False) is False


def test_disabled_quality_check_still_raises_when_asked():
    configure(enabled=False)
    with pytest.raises(AssertionError, match="must be true"):
        quality_check("x", False, detail="must be true", raise_on_fail=True)


# ─── @observe basics ───────────────────────────────────────────────

def test_observe_records_one_calc_row(obs_db):
    @observe("simple")
    def f(x):
        return x + 1

    f(10)
    rows = recent_calcs()
    assert len(rows) == 1
    assert rows[0]["calc_name"] == "simple"
    assert rows[0]["status"] == "ok"
    assert rows[0]["flag_count"] == 0
    assert rows[0]["duration_ms"] is not None
    assert rows[0]["duration_ms"] >= 0


def test_observe_status_flagged_when_quality_check_fails(obs_db):
    @observe("with_flag")
    def f(x):
        quality_check("positive", x > 0, detail=f"x={x}")
        return x

    f(-5)
    rows = recent_calcs()
    assert rows[0]["status"] == "flagged"
    assert rows[0]["flag_count"] == 1


def test_observe_status_error_when_function_raises(obs_db):
    @observe("explodes")
    def f():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        f()
    rows = recent_calcs()
    assert rows[0]["status"] == "error"
    assert "boom" in (rows[0]["error"] or "")


def test_observe_captures_input_and_output(obs_db):
    @observe("double")
    def f(x):
        return x * 2

    f(7)
    row = recent_calcs()[0]
    assert "7" in (row["inputs_json"] or "")
    assert "14" in (row["output_json"] or "")


def test_observe_capture_args_false_omits_inputs(obs_db):
    @observe("secret", capture_args=False)
    def f(api_key):
        return "ok"

    f(api_key="SHOULD-NOT-APPEAR")
    row = recent_calcs()[0]
    assert row["inputs_json"] is None


# ─── correlation IDs ────────────────────────────────────────────────

def test_nested_observe_calls_inherit_correlation_id(obs_db):
    @observe("inner")
    def inner(x):
        return x + 1

    @observe("outer")
    def outer(x):
        return inner(x) * 2

    outer(5)
    cids = {r["correlation_id"] for r in recent_calcs(limit=10)}
    assert len(cids) == 1, f"expected one shared correlation_id, got {cids}"


def test_correlation_context_manager_groups_calcs(obs_db):
    @observe("c")
    def calc(x):
        return x

    with correlation("manual-cid-123"):
        calc(1)
        calc(2)
        calc(3)

    rows = recent_calcs(limit=10)
    assert all(r["correlation_id"] == "manual-cid-123" for r in rows)
    assert len(rows) == 3


# ─── quality_check persistence ─────────────────────────────────────

def test_quality_check_persists_pass_and_fail_rows(obs_db):
    @observe("checker")
    def f():
        quality_check("a", True)
        quality_check("b", False, detail="oops")

    f()
    summary = {r["check_name"]: r for r in quality_summary()}
    assert summary["a"]["passed"] == 1 and summary["a"]["failed"] == 0
    assert summary["b"]["passed"] == 0 and summary["b"]["failed"] == 1


def test_quality_check_raise_on_fail(obs_db):
    @observe("hard")
    def f():
        quality_check("must_pass", False, detail="nope", raise_on_fail=True)

    with pytest.raises(AssertionError, match="must_pass"):
        f()
    rows = recent_calcs()
    assert rows[0]["status"] == "error"


def test_quality_summary_sorts_by_pass_rate_ascending(obs_db):
    @observe("c")
    def f():
        quality_check("always_pass", True)
        quality_check("always_pass", True)
        quality_check("sometimes_fails", True)
        quality_check("sometimes_fails", False)
        quality_check("never_passes", False)

    f()
    rows = quality_summary()
    # Lowest pass rate should be first
    assert rows[0]["check_name"] == "never_passes"
    assert rows[0]["pass_rate_pct"] == 0.0
    assert rows[-1]["check_name"] == "always_pass"


# ─── DataFrame summarization ───────────────────────────────────────

def test_dataframe_args_summarized_not_dumped(obs_db):
    @observe("with_df")
    def f(df):
        return len(df)

    big_df = pd.DataFrame({"a": range(1000), "b": range(1000)})
    f(big_df)
    row = recent_calcs()[0]
    inputs = row["inputs_json"]
    assert "_dataframe" in inputs
    assert "shape" in inputs
    # Should NOT contain the full data — total row count would push >>4KB if dumped
    assert "999" not in inputs or len(inputs) < 5000


def test_dataframe_summary_captures_shape_and_columns(obs_db):
    @observe("with_df")
    def f(df):
        return df

    df = pd.DataFrame({"alpha": [1, 2, 3], "beta": [4, 5, 6]})
    f(df)
    inputs = recent_calcs()[0]["inputs_json"]
    assert '"shape": [3, 2]' in inputs
    assert "alpha" in inputs
    assert "beta" in inputs


# ─── trace ─────────────────────────────────────────────────────────

def test_trace_returns_calcs_and_checks_for_one_correlation(obs_db):
    @observe("a")
    def a():
        quality_check("a_check", True)

    @observe("b")
    def b():
        quality_check("b_check", False, detail="nope")

    with correlation("trace-test-1"):
        a()
        b()

    t = trace("trace-test-1")
    assert t["correlation_id"] == "trace-test-1"
    assert len(t["calcs"]) == 2
    assert len(t["checks"]) == 2
    calc_names = {c["calc_name"] for c in t["calcs"]}
    assert calc_names == {"a", "b"}


def test_trace_for_unknown_id_returns_empty_lists(obs_db):
    t = trace("does-not-exist")
    assert t == {"correlation_id": "does-not-exist", "calcs": [], "checks": []}


# ─── retention ─────────────────────────────────────────────────────

def test_apply_retention_no_op_when_zero(obs_db):
    @observe("c")
    def f():
        quality_check("k", True)

    f()
    assert apply_retention(0) == (0, 0)
    assert total_row_counts()["calculations"] == 1


def test_apply_retention_no_op_when_disabled(tmp_path):
    configure(enabled=False, db_path=tmp_path / "obs.db")
    assert apply_retention(7) == (0, 0)


def test_apply_retention_deletes_old_rows(obs_db):
    @observe("c")
    def f():
        pass

    f()  # row "now"
    # Manually backdate that row to 100 days ago
    with sqlite3.connect(obs_db) as conn:
        conn.execute("UPDATE calculations SET started_at = datetime('now', '-100 days')")
        conn.execute("UPDATE quality_checks SET ts = datetime('now', '-100 days')")

    deleted_calcs, _ = apply_retention(retention_days=30)
    assert deleted_calcs == 1
    assert total_row_counts()["calculations"] == 0


# ─── total_row_counts ───────────────────────────────────────────────

def test_total_row_counts(obs_db):
    @observe("c")
    def f():
        quality_check("k1", True)
        quality_check("k2", False)

    f()
    f()
    counts = total_row_counts()
    assert counts["calculations"] == 2
    assert counts["quality_checks"] == 4
