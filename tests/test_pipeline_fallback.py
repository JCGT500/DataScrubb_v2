"""Tests for the Pipeline.run reuse_cached fallback path.

These tests use the MatchingEngine directly (lighter than spinning up the
whole pipeline) to verify the cached-source-injection paths produce the
right shapes and respect the precedence rules:
  - fresh raw source > cached
  - cached only used when raw is missing
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from datascrubb.matching.engine import MatchingEngine, MatchResults


def _crst(*txns: str) -> pd.DataFrame:
    return pd.DataFrame({"transaction_id": list(txns), "order_#": list(txns)})


def _engine(monkeypatch=None):
    cfg = MagicMock()
    cfg.sap_match_max_hours = 36
    cfg.telemetry_window_minutes = 120
    cfg.telemetry_sample_interval_minutes = 15
    cfg.fuel_price_per_gallon = 4.50
    return MatchingEngine(cfg)


# ─── SAP fallback ──────────────────────────────────────────────────

def test_cached_sap_used_when_raw_sap_missing():
    eng = _engine()
    crst = _crst("t1", "t2", "t3")
    cached = pd.DataFrame([
        {"transaction_id": "t1", "sap_match_flag": "MATCHED"},
        {"transaction_id": "t2", "sap_match_flag": "MATCHED"},
        {"transaction_id": "t99", "sap_match_flag": "MATCHED"},  # stale txn, dropped
    ])

    res = eng.run(crst, sap_df=None, cached_sap_segment=cached)

    assert res.sap_segment is not None
    assert set(res.sap_segment["transaction_id"]) == {"t1", "t2"}
    assert "id" not in res.sap_segment.columns
    assert "created_at" not in res.sap_segment.columns


def test_raw_sap_takes_precedence_over_cached(monkeypatch):
    """Fresh raw SAP should run the matcher; cached should be ignored."""
    from datascrubb.matching import engine as engine_mod
    fake_matcher = MagicMock(return_value=pd.DataFrame([
        {"transaction_id": "t1", "sap_match_flag": "MATCHED"},
    ]))
    monkeypatch.setattr(engine_mod, "match_sap_to_crst", fake_matcher)

    eng = _engine()
    crst = _crst("t1")
    raw_sap = pd.DataFrame([{"foo": "bar"}])
    cached = pd.DataFrame([
        {"transaction_id": "t1", "sap_match_flag": "MATCHED"},
        {"transaction_id": "t2", "sap_match_flag": "MATCHED"},
    ])

    res = eng.run(crst, sap_df=raw_sap, cached_sap_segment=cached)

    # Matcher was called → cached was NOT used
    fake_matcher.assert_called_once()
    assert len(res.sap_segment) == 1


def test_no_sap_at_all_leaves_results_sap_none():
    eng = _engine()
    res = eng.run(_crst("t1"))
    assert res.sap_segment is None


# ─── Telemetry fallback ────────────────────────────────────────────

def test_cached_telemetry_used_when_raw_missing_filters_to_current_crst():
    eng = _engine()
    crst = _crst("t1", "t2", "t3")
    # t99 is stale (no longer in CRST); created_at is a persistence artifact
    import datetime as _dt
    cached = pd.DataFrame([
        {"transaction_id": "t1", "telem_events": 50, "min_s1": -25.0,
         "created_at": _dt.datetime(2026, 5, 3, 12, 0)},
        {"transaction_id": "t2", "telem_events": 30, "min_s1": -27.0,
         "created_at": _dt.datetime(2026, 5, 3, 12, 0)},
        {"transaction_id": "t99", "telem_events": 1, "min_s1": -10.0,
         "created_at": _dt.datetime(2026, 5, 3, 12, 0)},
    ])

    res = eng.run(crst, telemetry_df=None, cached_telemetry_stop=cached)

    assert res.telemetry_stop is not None
    assert set(res.telemetry_stop["transaction_id"]) == {"t1", "t2"}
    # t3 has no telemetry coverage; that's expected (stop is "new" relative to cache)
    assert "created_at" not in res.telemetry_stop.columns

    # crst gets the telemetry merged via left-join (t3 stays, with NaN telem)
    assert len(res.crst) == 3
    assert pd.isna(res.crst[res.crst["transaction_id"] == "t3"]["telem_events"].iat[0])

    # Coverage = 2 stops with telemetry / 3 total
    assert res.telemetry_coverage == pytest.approx(2 / 3)


def test_raw_telemetry_takes_precedence_over_cached(monkeypatch):
    from datascrubb.matching import engine as engine_mod
    fake_matcher = MagicMock(return_value=pd.DataFrame([
        {"transaction_id": "t1", "telem_events": 99},
    ]))
    monkeypatch.setattr(engine_mod, "match_telemetry_to_crst", fake_matcher)

    eng = _engine()
    crst = _crst("t1")
    raw_telem = pd.DataFrame([{"vehicle": "TR1", "ts": "2026-01-01"}])
    cached = pd.DataFrame([{"transaction_id": "t1", "telem_events": 11}])

    res = eng.run(crst, telemetry_df=raw_telem, cached_telemetry_stop=cached)

    fake_matcher.assert_called_once()
    assert int(res.telemetry_stop["telem_events"].iat[0]) == 99


# ─── M3PL handling ─────────────────────────────────────────────────
# M3PL has no separate matcher in MatchingEngine — it just carries through.
# The cache fallback wires cached billing_snapshot in as m3pl_df at the
# pipeline layer (not the engine layer), so the engine sees a DataFrame
# either way and the existing pass-through behavior covers it.

def test_m3pl_passes_through_when_provided():
    eng = _engine()
    crst = _crst("57748100", "57748101")
    m3pl = pd.DataFrame([
        {"pro_number": "57748100", "crst_miles": 100.0, "billed_amount": 250.0},
        {"pro_number": "99999999", "crst_miles": 50.0, "billed_amount": 125.0},
    ])

    res = eng.run(crst, m3pl_df=m3pl)

    assert res.m3pl is not None
    assert len(res.m3pl) == 2
    # 1 of 2 PROs match a CRST order_#
    assert res.m3pl_match_rate == pytest.approx(0.5)
