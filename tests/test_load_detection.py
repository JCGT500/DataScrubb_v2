"""Unit tests for the multi-signal load-detection module."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from datascrubb.kpi.load_detection import (
    apply_load_overrides,
    compute_load_signals,
    _signal_bol,
    _signal_crst,
    _signal_reefer,
    _signal_sap,
    _signal_sequence,
)


# ─── helpers ───────────────────────────────────────────────────────

def _stops(rows: list[dict]) -> pd.DataFrame:
    """Build a stops_df with sensible defaults for missing fields."""
    defaults = dict(
        transaction_id="t",
        order_number="O1",
        route_name="R1",
        stop_seq=1,
        stop_direction="PU",
        stop_class="OTHER",
        s_code="",
        bol="",
        current_cases=0,
        tender_cases=0,
        dwell_minutes=60,
    )
    return pd.DataFrame([{**defaults, **r} for r in rows])


# ─── _signal_crst ──────────────────────────────────────────────────

def test_crst_signal_loaded_when_current_cases_positive():
    df = _stops([{"transaction_id": "t1", "current_cases": 50}])
    assert _signal_crst(df).iat[0] == 1


def test_crst_signal_loaded_when_so_with_tender():
    df = _stops([{"transaction_id": "t1", "stop_direction": "SO",
                  "current_cases": 0, "tender_cases": 30}])
    assert _signal_crst(df).iat[0] == 1


def test_crst_signal_empty_when_no_cases():
    df = _stops([{"transaction_id": "t1", "current_cases": 0, "tender_cases": 0}])
    assert _signal_crst(df).iat[0] == 0


# ─── _signal_sap ──────────────────────────────────────────────────

def test_sap_signal_nan_when_no_sap_match():
    stops = _stops([{"transaction_id": "t1"}])
    sap = pd.DataFrame([{"transaction_id": "t99", "cases_count": 100}])
    assert math.isnan(_signal_sap(stops, sap).iat[0])


def test_sap_signal_loaded_when_matched_sap_has_cases():
    stops = _stops([{"transaction_id": "t1"}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 100, "actual_weight": 0}])
    assert _signal_sap(stops, sap).iat[0] == 1


def test_sap_signal_loaded_when_matched_sap_has_weight():
    stops = _stops([{"transaction_id": "t1"}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 0, "actual_weight": 5000}])
    assert _signal_sap(stops, sap).iat[0] == 1


def test_sap_signal_empty_when_matched_sap_has_zero_cases_and_weight():
    stops = _stops([{"transaction_id": "t1"}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 0, "actual_weight": 0}])
    assert _signal_sap(stops, sap).iat[0] == 0


def test_sap_signal_returns_all_nan_when_sap_df_is_none():
    stops = _stops([{"transaction_id": "t1"}, {"transaction_id": "t2"}])
    out = _signal_sap(stops, None)
    assert out.isna().all()


# ─── _signal_reefer ───────────────────────────────────────────────

def test_reefer_signal_loaded_when_cargo_cold():
    """Cold cargo zone (≤ -15°C) → frozen mass present → loaded."""
    stops = _stops([{"transaction_id": "t1"}])
    tel = pd.DataFrame([{"transaction_id": "t1", "max_cargo_temp": -22.0}])
    assert _signal_reefer(stops, tel).iat[0] == 1


def test_reefer_signal_empty_when_cargo_warm():
    """Warm cargo zone (≥ 0°C) → no cold cargo → empty."""
    stops = _stops([{"transaction_id": "t1"}])
    tel = pd.DataFrame([{"transaction_id": "t1", "max_cargo_temp": 5.0}])
    assert _signal_reefer(stops, tel).iat[0] == 0


def test_reefer_signal_nan_in_ambiguous_middle_band():
    """Cargo zone between -15 and 0°C is ambiguous (chilled product OR cool ambient empty)."""
    stops = _stops([{"transaction_id": "t1"}])
    tel = pd.DataFrame([{"transaction_id": "t1", "max_cargo_temp": -10.0}])
    assert math.isnan(_signal_reefer(stops, tel).iat[0])


def test_reefer_signal_nan_when_no_telemetry_for_stop():
    stops = _stops([{"transaction_id": "t1"}])
    tel = pd.DataFrame([{"transaction_id": "t99", "max_cargo_temp": -22.0}])
    assert math.isnan(_signal_reefer(stops, tel).iat[0])


# ─── _signal_sequence ─────────────────────────────────────────────

def test_sequence_signal_pickup_at_plasma_loads_subsequent_stops():
    """Stop 1 = pickup at plasma → loaded onward; stop 2 = transit → still loaded."""
    df = _stops([
        {"transaction_id": "t1", "route_name": "R1", "stop_seq": 1,
         "stop_direction": "PU", "stop_class": "PLASMA_CENTER", "s_code": "S1234"},
        {"transaction_id": "t2", "route_name": "R1", "stop_seq": 2,
         "stop_direction": "PU", "stop_class": "OTHER", "s_code": ""},
    ])
    out = _signal_sequence(df)
    # The plasma-pickup stop itself: load state at the stop is 1 (we picked up)
    assert out.iat[0] == 1
    # Subsequent stop: still loaded
    assert out.iat[1] == 1


def test_sequence_signal_delivery_clears_load_after():
    df = _stops([
        {"transaction_id": "t1", "route_name": "R1", "stop_seq": 1,
         "stop_direction": "PU", "stop_class": "PLASMA_CENTER", "s_code": "S1234"},
        {"transaction_id": "t2", "route_name": "R1", "stop_seq": 2,
         "stop_direction": "SO", "stop_class": "DISTRIBUTION_CENTER", "s_code": ""},
        {"transaction_id": "t3", "route_name": "R1", "stop_seq": 3,
         "stop_direction": "PU", "stop_class": "OTHER", "s_code": ""},
    ])
    out = _signal_sequence(df)
    # Pickup → loaded
    assert out.iat[0] == 1
    # Delivery itself → still has the cargo (was loaded coming in)
    assert out.iat[1] == 1
    # After delivery → empty
    assert out.iat[2] == 0


def test_sequence_signal_nan_when_stop_seq_missing():
    df = _stops([{"transaction_id": "t1"}])
    df["stop_seq"] = pd.NA
    assert _signal_sequence(df).isna().all()


# ─── _signal_bol ──────────────────────────────────────────────────

def test_bol_signal_loaded_when_bol_present():
    df = _stops([{"transaction_id": "t1", "bol": "BOL12345"}])
    assert _signal_bol(df).iat[0] == 1


def test_bol_signal_empty_when_bol_blank():
    df = _stops([{"transaction_id": "t1", "bol": ""}])
    assert _signal_bol(df).iat[0] == 0


def test_bol_signal_empty_when_bol_is_string_nan():
    df = _stops([{"transaction_id": "t1", "bol": "nan"}])
    assert _signal_bol(df).iat[0] == 0


# ─── compute_load_signals (integration) ───────────────────────────

def test_confidence_score_with_all_signals_positive():
    stops = _stops([{"transaction_id": "t1", "current_cases": 10}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 10, "actual_weight": 0}])
    tel = pd.DataFrame([{"transaction_id": "t1", "max_cargo_temp": -22.0}])
    out = compute_load_signals(stops, sap, tel)
    # 4 signals enabled by default. CRST=1, SAP=1, reefer=1, sequence=0 → 3/4 = 75%
    assert out["load_confidence"].iat[0] >= 75
    assert out["loaded_at_stop_v2"].iat[0] == 1


def test_confidence_score_with_only_crst_positive_low_confidence():
    """Classic false-positive case: CRST says loaded but no other signals confirm."""
    stops = _stops([{"transaction_id": "t1", "current_cases": 10}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 0, "actual_weight": 0}])
    tel = pd.DataFrame([{"transaction_id": "t1", "max_cargo_temp": 5.0}])
    out = compute_load_signals(stops, sap, tel)
    # CRST=1, SAP=0, reefer=0, sequence=0 → 1/4 = 25%
    assert out["load_confidence"].iat[0] == 25
    assert out["loaded_at_stop_v2"].iat[0] == 0
    assert out["load_state_disputed"].iat[0] == 1


def test_disputed_flag_true_when_signals_disagree():
    stops = _stops([{"transaction_id": "t1", "current_cases": 10, "bol": "BOL1"}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 0, "actual_weight": 0}])
    out = compute_load_signals(stops, sap)
    assert out["load_state_disputed"].iat[0] == 1


def test_disputed_flag_false_when_all_signals_agree():
    stops = _stops([{"transaction_id": "t1", "current_cases": 0, "bol": ""}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 0, "actual_weight": 0}])
    out = compute_load_signals(stops, sap)
    assert out["load_state_disputed"].iat[0] == 0


def test_nan_signals_dont_vote():
    """When telemetry + SAP are missing, only CRST + sequence vote."""
    stops = _stops([{"transaction_id": "t1", "current_cases": 10}])
    out = compute_load_signals(stops, sap_segments_df=None, telemetry_stops_df=None)
    # CRST=1, sequence=0 → 1/2 = 50%
    assert out["load_confidence"].iat[0] == 50
    assert out["loaded_at_stop_v2"].iat[0] == 1  # threshold default 50, ≥ passes


def test_threshold_changes_verdict():
    stops = _stops([{"transaction_id": "t1", "current_cases": 10}])
    sap = pd.DataFrame([{"transaction_id": "t1", "cases_count": 0, "actual_weight": 0}])
    tel = pd.DataFrame([{"transaction_id": "t1", "max_cargo_temp": -22.0}])
    # CRST=1, SAP=0, reefer=1, sequence=0 → 2/4 = 50%
    out_high = compute_load_signals(stops, sap, tel, confidence_threshold=75)
    out_low = compute_load_signals(stops, sap, tel, confidence_threshold=30)
    assert out_high["loaded_at_stop_v2"].iat[0] == 0
    assert out_low["loaded_at_stop_v2"].iat[0] == 1


def test_disabled_signal_does_not_vote():
    stops = _stops([{"transaction_id": "t1", "current_cases": 10}])
    out = compute_load_signals(stops, enabled_signals=("crst",))
    # Only crst votes → confidence 100
    assert out["load_confidence"].iat[0] == 100
    # The disabled signals should be NaN
    assert pd.isna(out["load_signal_sequence"].iat[0])


# ─── apply_load_overrides ─────────────────────────────────────────

def test_override_flips_loaded_to_empty():
    sigs = pd.DataFrame([{
        "transaction_id": "t1", "loaded_at_stop_v2": 1, "load_confidence": 90,
    }])
    overrides = pd.DataFrame([{"transaction_id": "t1", "override_value": 0}])
    out = apply_load_overrides(sigs, overrides)
    assert out["loaded_at_stop_v2"].iat[0] == 0
    assert out["load_override_applied"].iat[0] == 1


def test_override_flips_empty_to_loaded():
    sigs = pd.DataFrame([{
        "transaction_id": "t1", "loaded_at_stop_v2": 0, "load_confidence": 10,
    }])
    overrides = pd.DataFrame([{"transaction_id": "t1", "override_value": 1}])
    out = apply_load_overrides(sigs, overrides)
    assert out["loaded_at_stop_v2"].iat[0] == 1
    assert out["load_override_applied"].iat[0] == 1


def test_no_override_means_no_change():
    sigs = pd.DataFrame([{
        "transaction_id": "t1", "loaded_at_stop_v2": 1, "load_confidence": 90,
    }])
    out = apply_load_overrides(sigs, None)
    assert out["loaded_at_stop_v2"].iat[0] == 1
    assert out["load_override_applied"].iat[0] == 0


def test_override_for_unrelated_stop_doesnt_affect_others():
    sigs = pd.DataFrame([
        {"transaction_id": "t1", "loaded_at_stop_v2": 1, "load_confidence": 90},
        {"transaction_id": "t2", "loaded_at_stop_v2": 1, "load_confidence": 90},
    ])
    overrides = pd.DataFrame([{"transaction_id": "t1", "override_value": 0}])
    out = apply_load_overrides(sigs, overrides)
    assert out["loaded_at_stop_v2"].tolist() == [0, 1]
    assert out["load_override_applied"].tolist() == [1, 0]
