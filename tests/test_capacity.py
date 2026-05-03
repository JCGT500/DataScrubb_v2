"""Tests for the trailer capacity helper (config + observed fallback)."""

import pandas as pd
import pytest

from datascrubb.kpi.capacity import (
    USE_MAX_FOR_OBSERVED,
    attach_fill_pct,
    derive_observed_capacity,
)


def _stops_for_trailer(trailer: str, n: int, cases_max: float) -> pd.DataFrame:
    """Build n synthetic stops for one trailer with cases ramping to cases_max."""
    return pd.DataFrame({
        "transaction_id": [f"{trailer}_{i:03d}" for i in range(n)],
        "trailer": [trailer] * n,
        "current_cases": [cases_max * (i + 1) / n for i in range(n)],
        "sum_of_weight": [10.0 * (i + 1) for i in range(n)],
    })


def test_observed_capacity_excludes_low_history():
    df = _stops_for_trailer("RX001", n=4, cases_max=100)
    out = derive_observed_capacity(df)
    assert out.empty  # < 5 stops -> excluded


def test_observed_capacity_uses_max_by_default():
    df = _stops_for_trailer("RX001", n=20, cases_max=200)
    out = derive_observed_capacity(df)
    assert len(out) == 1
    obs = out.iloc[0]
    assert obs["trailer"] == "RX001"
    if USE_MAX_FOR_OBSERVED:
        # observed cap = max load seen → 200
        assert obs["observed_max_cases"] == pytest.approx(200.0, abs=0.01)
    else:
        from datascrubb.kpi.capacity import OBSERVED_QUANTILE
        expected = pd.Series([200 * (i + 1) / 20 for i in range(20)]).quantile(OBSERVED_QUANTILE)
        assert obs["observed_max_cases"] == pytest.approx(float(expected), abs=0.01)


def test_fill_pct_cannot_exceed_cap():
    """Even if observed capacity is 100 and a stop reports 999, fill_pct is clipped."""
    df = pd.DataFrame({
        "transaction_id": ["x1"],
        "trailer": ["RX001"],
        "current_cases": [9999.0],  # absurdly high
        "sum_of_weight": [10.0],
    })
    matrix = {
        "default": {"max_cases": 100, "max_weight_lbs": 10000},
        "trailers": {"RX001": {"max_cases": 100, "max_weight_lbs": 10000}},
    }
    out = attach_fill_pct(df, matrix)
    # Should be capped, not 9999%
    assert out.iloc[0]["fill_pct_cases"] <= 200.0


def test_attach_fill_pct_uses_config_first():
    df = _stops_for_trailer("RX001", n=20, cases_max=1000)
    matrix = {
        "default": {"max_cases": 800, "max_weight_lbs": 44000},
        "trailers": {"RX001": {"max_cases": 1000, "max_weight_lbs": 50000}},
    }
    out = attach_fill_pct(df, matrix)
    assert (out["capacity_source"] == "config").all()
    assert (out["cap_max_cases"] == 1000).all()
    # The 20th stop has cases_max=1000 → fill = 100%
    assert out.iloc[-1]["fill_pct_cases"] == pytest.approx(100.0, abs=0.5)


def test_attach_fill_pct_falls_back_to_observed():
    df = _stops_for_trailer("RX002", n=20, cases_max=500)
    matrix = {
        "default": {"max_cases": 800, "max_weight_lbs": 44000},
        "trailers": {},  # RX002 not in config
    }
    out = attach_fill_pct(df, matrix)
    assert (out["capacity_source"] == "observed").all()
    # With USE_MAX_FOR_OBSERVED, cap = the heaviest historical load = 500.
    # If config switches to quantile mode, fall back to that.
    if USE_MAX_FOR_OBSERVED:
        assert out.iloc[0]["cap_max_cases"] == pytest.approx(500.0, abs=0.5)
    else:
        expected = pd.Series([500 * (i + 1) / 20 for i in range(20)]).quantile(0.95)
        assert out.iloc[0]["cap_max_cases"] == pytest.approx(float(expected), abs=0.5)


def test_attach_fill_pct_falls_back_to_default_when_low_history():
    df = _stops_for_trailer("RX003", n=3, cases_max=100)  # only 3 stops, observed excluded
    matrix = {
        "default": {"max_cases": 800, "max_weight_lbs": 44000},
        "trailers": {},
    }
    out = attach_fill_pct(df, matrix)
    assert (out["capacity_source"] == "default").all()
    assert (out["cap_max_cases"] == 800).all()


def test_attach_fill_pct_handles_missing_trailer():
    df = pd.DataFrame({
        "transaction_id": ["x1"],
        "trailer": [None],
        "current_cases": [100.0],
        "sum_of_weight": [10.0],
    })
    matrix = {"default": {"max_cases": 500, "max_weight_lbs": 10000}, "trailers": {}}
    out = attach_fill_pct(df, matrix)
    assert out.iloc[0]["capacity_source"] == "default"


def test_attach_fill_pct_empty_input():
    out = attach_fill_pct(pd.DataFrame(), {"default": {}, "trailers": {}})
    assert isinstance(out, pd.DataFrame)


def test_attach_fill_pct_handles_zero_capacity():
    """A bad config (max_cases = 0) shouldn't divide-by-zero."""
    df = pd.DataFrame({
        "transaction_id": ["x1"],
        "trailer": ["RX001"],
        "current_cases": [100.0],
        "sum_of_weight": [10.0],
    })
    matrix = {
        "default": {"max_cases": 0, "max_weight_lbs": 0},
        "trailers": {"RX001": {"max_cases": 0, "max_weight_lbs": 0}},
    }
    out = attach_fill_pct(df, matrix)
    # NaN, not crash
    assert pd.isna(out.iloc[0]["fill_pct_cases"])
    assert pd.isna(out.iloc[0]["fill_pct_weight"])
