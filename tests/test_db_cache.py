"""Tests for the read-only db_cache fallback helpers."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine

from datascrubb.db_cache import (
    db_source_status,
    read_cached_m3pl,
    read_cached_sap,
    read_cached_telemetry_stop,
)


@pytest.fixture
def empty_db(tmp_path: Path):
    """Engine pointing at a brand-new SQLite file with no tables."""
    return create_engine(f"sqlite:///{tmp_path / 'empty.db'}")


@pytest.fixture
def populated_db(tmp_path: Path):
    """Engine pointing at a SQLite file with the four source tables populated."""
    db = tmp_path / "populated.db"
    engine = create_engine(f"sqlite:///{db}")

    pd.DataFrame([
        {"transaction_id": "t1", "document_number": "D1", "sap_match_flag": "MATCHED"},
        {"transaction_id": "t2", "document_number": "D2", "sap_match_flag": "MATCHED"},
    ]).to_sql("sap_segment", engine, index=False)

    pd.DataFrame([
        {"pro_number": "P1", "crst_miles": 100.0, "billed_amount": 250.0},
        {"pro_number": "P2", "crst_miles": 200.0, "billed_amount": 500.0},
        {"pro_number": "P3", "crst_miles": 50.0,  "billed_amount": 125.0},
    ]).to_sql("billing_snapshot", engine, index=False)

    pd.DataFrame([
        {"transaction_id": "t1", "telem_events": 50, "min_s1": -25.0, "max_s1": -22.0},
        {"transaction_id": "t2", "telem_events": 30, "min_s1": -27.0, "max_s1": -24.0},
    ]).to_sql("telemetry_stop", engine, index=False)

    pd.DataFrame([
        {"transaction_id": "t1", "customer": "BIOLIFE"},
        {"transaction_id": "t2", "customer": "CSL"},
    ]).to_sql("stop_master", engine, index=False)

    pd.DataFrame([
        {"run_id": "20260503_120000_aaaa", "status": "SUCCESS", "run_timestamp": "2026-05-03 12:00:00"},
        {"run_id": "20260503_140000_bbbb", "status": "SUCCESS", "run_timestamp": "2026-05-03 14:00:00"},
        {"run_id": "20260503_150000_cccc", "status": "FAILED",  "run_timestamp": "2026-05-03 15:00:00"},
    ]).to_sql("pipeline_run", engine, index=False)

    return engine


# ─── read_cached_* on empty DB ──────────────────────────────────────

def test_read_cached_sap_returns_none_when_table_missing(empty_db):
    assert read_cached_sap(empty_db) is None


def test_read_cached_m3pl_returns_none_when_table_missing(empty_db):
    assert read_cached_m3pl(empty_db) is None


def test_read_cached_telemetry_stop_returns_none_when_table_missing(empty_db):
    assert read_cached_telemetry_stop(empty_db) is None


def test_read_cached_returns_none_when_table_exists_but_empty(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty_tables.db'}")
    pd.DataFrame(columns=["pro_number", "crst_miles"]).to_sql("billing_snapshot", engine, index=False)
    assert read_cached_m3pl(engine) is None


# ─── read_cached_* on populated DB ─────────────────────────────────

def test_read_cached_sap_returns_dataframe(populated_db):
    df = read_cached_sap(populated_db)
    assert df is not None
    assert len(df) == 2
    assert {"transaction_id", "document_number", "sap_match_flag"}.issubset(df.columns)


def test_read_cached_m3pl_returns_dataframe(populated_db):
    df = read_cached_m3pl(populated_db)
    assert df is not None
    assert len(df) == 3
    assert {"pro_number", "crst_miles", "billed_amount"}.issubset(df.columns)


def test_read_cached_telemetry_stop_returns_dataframe(populated_db):
    df = read_cached_telemetry_stop(populated_db)
    assert df is not None
    assert len(df) == 2
    assert {"transaction_id", "telem_events", "min_s1"}.issubset(df.columns)


# ─── db_source_status ──────────────────────────────────────────────

def test_db_source_status_empty_db(empty_db):
    status = db_source_status(empty_db)
    assert set(status.keys()) == {"crst", "sap", "m3pl", "telemetry"}
    for src, info in status.items():
        assert info["rows"] == 0
        assert info["last_run_id"] is None
        assert info["last_run_ts"] is None


def test_db_source_status_reports_row_counts_and_latest_successful_run(populated_db):
    status = db_source_status(populated_db)
    assert status["crst"]["rows"] == 2
    assert status["sap"]["rows"] == 2
    assert status["m3pl"]["rows"] == 3
    assert status["telemetry"]["rows"] == 2

    # All sources point at the most recent SUCCESS (the FAILED 15:00 run is skipped)
    assert status["crst"]["last_run_id"] == "20260503_140000_bbbb"
    assert status["m3pl"]["last_run_ts"] == "2026-05-03 14:00:00"


def test_db_source_status_zero_rows_returns_null_run_metadata(tmp_path: Path):
    """Sources with no data should not claim association with a pipeline run."""
    db = tmp_path / "partial.db"
    engine = create_engine(f"sqlite:///{db}")
    pd.DataFrame([{"transaction_id": "t1"}]).to_sql("stop_master", engine, index=False)
    pd.DataFrame([
        {"run_id": "r1", "status": "SUCCESS", "run_timestamp": "2026-05-03 12:00:00"},
    ]).to_sql("pipeline_run", engine, index=False)

    status = db_source_status(engine)
    assert status["crst"]["rows"] == 1
    assert status["crst"]["last_run_id"] == "r1"
    # Other sources have no rows → no run metadata attributed
    assert status["sap"]["rows"] == 0
    assert status["sap"]["last_run_id"] is None
