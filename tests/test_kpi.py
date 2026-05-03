"""Tests for the KPI engine — locks in the metric formulas against regression."""

import pandas as pd
import pytest

from datascrubb.kpi import (
    compute_claims_risk,
    compute_customer_churn_signal,
    compute_customer_concentration,
    compute_customer_scorecard,
    compute_cycle_time_consistency,
    compute_demand_forecast,
    compute_detention_audit,
    compute_driver_scorecard,
    compute_lane_profitability,
    compute_late_code_analysis,
    compute_loaded_miles,
    compute_route_otp,
    compute_route_revenue,
    compute_route_revenue_weekly,
    compute_temp_compliance,
    compute_trailer_revenue_weekly,
    compute_trailer_utilization,
)


# ─────────────── route_otp ───────────────

def test_route_otp_aggregates_per_route(sample_stops):
    out = compute_route_otp(sample_stops)
    assert {"route_id", "stop_count", "otp_time_pass_rate"}.issubset(out.columns)
    assert len(out) == 2
    a = out[out["route_id"] == "1001"].iloc[0]
    assert a["stop_count"] == 5
    assert a["otp_time_pass_rate"] == 100.0  # all on time
    b = out[out["route_id"] == "1002"].iloc[0]
    assert b["stop_count"] == 5
    assert b["otp_time_pass_rate"] == 60.0  # 3 of 5 on time


def test_route_otp_handles_empty():
    assert compute_route_otp(pd.DataFrame()).empty


# ─────────────── route_revenue ───────────────

def test_revenue_uses_default_when_customer_missing(sample_stops, sample_m3pl):
    matrix = {"default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0}, "customers": {}}
    out = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    a = out[out["route_id"] == "1001"].iloc[0]
    # 1500 mi * 2 + 5 stops * 100 = 3500
    assert a["revenue"] == pytest.approx(3500, abs=1)
    assert a["rate_source"] == "default"
    # cost from M3PL = 2711.5
    assert a["margin"] == pytest.approx(3500 - 2711.5, abs=1)


def test_revenue_uses_customer_when_present(sample_stops, sample_m3pl):
    matrix = {
        "default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0},
        "customers": {"CSL": {"rate_per_mile": 3.0, "rate_per_stop": 200.0, "minimum_charge": 0}},
    }
    out = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    a = out[out["route_id"] == "1001"].iloc[0]  # CSL
    # 1500 * 3 + 5 * 200 = 5500
    assert a["revenue"] == pytest.approx(5500, abs=1)
    assert a["rate_source"] == "customer"
    b = out[out["route_id"] == "1002"].iloc[0]  # BIOLIFE -> default
    assert b["rate_source"] == "default"


def test_revenue_floors_at_minimum_charge(sample_stops, sample_m3pl):
    matrix = {
        "default": {"rate_per_mile": 0.01, "rate_per_stop": 0.01, "minimum_charge": 9999},
        "customers": {},
    }
    out = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    assert (out["revenue"] >= 9999).all()


# ─────────────── loaded_miles ───────────────

def test_loaded_miles_full_load(sample_stops, sample_m3pl):
    out = compute_loaded_miles(sample_stops, sample_m3pl)
    assert {"route_id", "loaded_pct", "estimated_loaded_miles"}.issubset(out.columns)
    a = out[out["route_id"] == "1001"].iloc[0]
    # All 5 stops have current_cases > 0 except the last (cur=0). Segments = 4.
    # Stops 1-4 have cur > 0 (segments originating loaded), stop 5 is the final SO.
    # loaded segments = 4, total segments = 4 -> loaded_pct = 100.
    assert a["total_segments"] == 4
    assert a["loaded_pct"] == 100.0
    assert a["estimated_loaded_miles"] == 1500.0
    assert a["estimated_deadhead_miles"] == 0.0


# ─────────────── temp_compliance (load-aware) ───────────────

def test_temp_compliance_only_counts_loaded_excursions(sample_stops):
    out = compute_temp_compliance(sample_stops)
    # Route B (1002) stop index 2 has min_s1 = -32 (excursion) AND loaded_at_stop = 1
    b = out[out["route_id"] == "1002"].iloc[0]
    assert b["excursion_count"] == 1
    assert b["compliance_flag"] == "EXCURSION"
    # Route A has no out-of-range stops
    a = out[out["route_id"] == "1001"].iloc[0]
    assert a["excursion_count"] == 0
    assert a["compliance_flag"] == "OK"


# ─────────────── claims_risk ───────────────

def test_claims_risk_bands_assigned(sample_stops):
    out = compute_claims_risk(sample_stops)
    assert "risk_score" in out.columns
    assert "risk_band" in out.columns
    assert set(out["risk_band"].unique()).issubset({"HIGH", "MEDIUM", "LOW", "NONE"})


# ─────────────── driver_scorecard ───────────────

def test_driver_scorecard_ranks_drivers(sample_stops):
    out = compute_driver_scorecard(sample_stops)
    assert "score" in out.columns
    assert "rank" in out.columns
    assert len(out) == 2
    # JDOE (Route A — 100% OTP, 0% late) should beat ASMITH (Route B — 60% OTP)
    j = out[out["driver"] == "JDOE"].iloc[0]
    a = out[out["driver"] == "ASMITH"].iloc[0]
    assert j["rank"] < a["rank"]
    assert j["score"] > a["score"]


# ─────────────── trailer_utilization ───────────────

def test_trailer_utilization_basic(sample_stops, sample_m3pl):
    out = compute_trailer_utilization(sample_stops, sample_m3pl)
    assert {"trailer", "utilization_pct", "active_days", "idle_days"}.issubset(out.columns)
    assert len(out) == 2
    rx001 = out[out["trailer"] == "RX001"].iloc[0]
    assert rx001["active_days"] == 5
    assert rx001["total_stops"] == 5


# ─────────────── lane_profitability ───────────────

def test_lane_profitability_origin_first_pu_dest_last_so(sample_stops, sample_m3pl):
    matrix = {"default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0}, "customers": {}}
    rev = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    out = compute_lane_profitability(rev, sample_stops)
    # Route A: first PU is in IA, last SO is in KY
    a = out[(out["origin_state"] == "IA") & (out["dest_state"] == "KY")]
    assert len(a) == 1


# ─────────────── customer_scorecard ───────────────

def test_customer_scorecard_includes_revenue(sample_stops, sample_m3pl):
    matrix = {"default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0}, "customers": {}}
    rev = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    out = compute_customer_scorecard(sample_stops, rev)
    assert {"customer", "stops", "otp_rate", "revenue", "margin"}.issubset(out.columns)
    csl = out[out["customer"] == "CSL"].iloc[0]
    assert csl["stops"] == 5
    assert csl["otp_rate"] == 100.0


def test_customer_scorecard_no_revenue(sample_stops):
    out = compute_customer_scorecard(sample_stops, None)
    assert "revenue" not in out.columns
    assert "stops" in out.columns


# ─────────────── customer_concentration ───────────────

def test_concentration_cumulative_share_monotonic(sample_stops, sample_m3pl):
    matrix = {"default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0}, "customers": {}}
    rev = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    out = compute_customer_concentration(rev)
    assert (out["cumulative_share_pct"].diff().dropna() >= 0).all()
    assert out["cumulative_share_pct"].iloc[-1] == pytest.approx(100.0, abs=0.5)


# ─────────────── churn signal ───────────────

def test_churn_signal_handles_single_week(sample_stops):
    # All sample stops in same week → no prev_pros possible
    out = compute_customer_churn_signal(sample_stops)
    # Either empty or all bands == NEW
    if not out.empty:
        assert out["churn_band"].isin(["NEW", "STABLE", "GROWING", "DECLINING", "CHURN_RISK"]).all()


# ─────────────── cycle_time ───────────────

def test_cycle_time_requires_multiple_instances(sample_stops):
    out = compute_cycle_time_consistency(sample_stops)
    # Each route_name has only 1 PRO# instance; cycle_time needs >= 2 → empty
    assert out.empty


# ─────────────── late_code_analysis ───────────────

def test_late_code_analysis_counts_occurrences(sample_stops):
    out = compute_late_code_analysis(sample_stops)
    assert "late_code" in out.columns
    wx = out[out["late_code"] == "WX"].iloc[0]
    assert wx["occurrences"] == 2  # 2 late stops on Route B with code WX


# ─────────────── detention_audit ───────────────

def test_detention_audit_flags_long_dwells(sample_stops):
    out = compute_detention_audit(sample_stops, threshold_minutes=120)
    biolife = out[out["customer"] == "BIOLIFE"].iloc[0]
    assert biolife["detention_stops"] == 1  # one stop with 220min dwell
    assert biolife["billable_hours"] == pytest.approx(220 / 60, abs=0.1)


# ─────────────── demand_forecast ───────────────

def test_demand_forecast_needs_min_history(sample_stops):
    # All stops in single week → < 3 weeks of history → empty
    out = compute_demand_forecast(sample_stops)
    assert out.empty


# ─────────────── empty handling ───────────────

@pytest.mark.parametrize("fn", [
    compute_route_otp, compute_loaded_miles, compute_temp_compliance,
    compute_claims_risk, compute_driver_scorecard, compute_trailer_utilization,
    compute_late_code_analysis, compute_detention_audit, compute_demand_forecast,
    compute_customer_scorecard, compute_customer_churn_signal,
    compute_cycle_time_consistency,
])
def test_empty_input_returns_empty_dataframe(fn):
    """Every KPI function must gracefully handle an empty input."""
    if fn is compute_loaded_miles:
        result = fn(pd.DataFrame(), None)
    elif fn is compute_customer_scorecard:
        result = fn(pd.DataFrame(), None)
    elif fn is compute_trailer_utilization:
        result = fn(pd.DataFrame(), None)
    else:
        result = fn(pd.DataFrame())
    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_concentration_handles_none():
    assert compute_customer_concentration(None).empty
    assert compute_customer_concentration(pd.DataFrame()).empty


def test_lane_profitability_handles_none(sample_stops):
    assert compute_lane_profitability(None, sample_stops).empty
    assert compute_lane_profitability(pd.DataFrame(), sample_stops).empty


# ─────────────── trailer / route revenue weekly ───────────────

def test_trailer_revenue_weekly_basic(sample_stops, sample_m3pl):
    matrix = {"default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0}, "customers": {}}
    rev = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    out = compute_trailer_revenue_weekly(sample_stops, rev)
    assert {"trailer", "week", "revenue", "cost", "margin", "stops", "routes"}.issubset(out.columns)
    # Each route is run by a single trailer in the fixture, so total revenue
    # across trailer-weeks equals total revenue across routes.
    assert out["revenue"].sum() == pytest.approx(rev["revenue"].sum(), abs=1.0)


def test_trailer_revenue_weekly_handles_no_revenue(sample_stops):
    out = compute_trailer_revenue_weekly(sample_stops, None)
    assert out.empty


def test_route_revenue_weekly_basic(sample_stops, sample_m3pl):
    matrix = {"default": {"rate_per_mile": 2.0, "rate_per_stop": 100.0, "minimum_charge": 0}, "customers": {}}
    rev = compute_route_revenue(sample_stops, sample_m3pl, rate_matrix=matrix)
    out = compute_route_revenue_weekly(sample_stops, rev)
    assert {"route_name", "week", "instances", "revenue", "cost", "margin"}.issubset(out.columns)
    assert (out["instances"] >= 1).all()
    # AMES route from the fixture
    ames = out[out["route_name"] == "AMES"]
    assert not ames.empty


def test_route_revenue_weekly_empty_inputs():
    assert compute_route_revenue_weekly(pd.DataFrame(), None).empty
    assert compute_trailer_revenue_weekly(pd.DataFrame(), None).empty
