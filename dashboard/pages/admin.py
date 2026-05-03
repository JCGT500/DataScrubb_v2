"""Admin page — edit every tunable threshold the pipeline reads.

Each tab is one domain. Each "Save" button writes only that tab's keys back
to ``config/default.yaml``. A "Reset" button per tab restores defaults.
Saved settings take effect on the next pipeline run.
"""

import sys
from pathlib import Path

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.admin import (
    DEFAULT_YAML_PATH,
    DEFAULTS,
    load_yaml,
    reset_block,
    update_block,
)


def _g(d: dict, k: str, fallback):
    """Read with default if key is missing or None."""
    v = d.get(k, fallback)
    return fallback if v is None else v


def _tab_save_reset(block: str, save_action, key_prefix: str):
    """Render Save / Reset buttons for a tab."""
    c1, c2, c3 = st.columns([1, 1, 5])
    with c1:
        if st.button("Save changes", type="primary", key=f"{key_prefix}_save"):
            try:
                save_action()
                st.success(f"Saved `{block}` block. Re-run the pipeline to apply.")
            except Exception as e:
                st.error(f"Save failed: {e}")
    with c2:
        if st.button("Reset to defaults", key=f"{key_prefix}_reset"):
            try:
                reset_block(block)
                st.success(f"`{block}` reset to defaults. Re-run the pipeline to apply.")
                st.rerun()
            except Exception as e:
                st.error(f"Reset failed: {e}")


def _validate_weights(weights: dict[str, float], target: float = 1.0, tol: float = 0.01) -> tuple[bool, float]:
    s = sum(weights.values())
    return (abs(s - target) <= tol), s


def render():
    st.header("Admin")
    st.caption(
        "Tune every threshold the pipeline reads. Changes write to "
        f"`{DEFAULT_YAML_PATH.name}` and take effect on the next pipeline run."
    )

    full = load_yaml()

    tabs = st.tabs([
        "Matching", "Validation", "Reefer / Telemetry", "Claims-Risk",
        "Driver Scorecard", "Forecast & Detention", "Customer Churn",
        "Capacity", "Revenue", "Map UI", "Warehouse Inclusion",
    ])

    # ───────── Matching ─────────
    with tabs[0]:
        st.subheader("Matching tolerances")
        st.caption("How wide a window each adapter uses to associate records.")
        block = "pipeline"
        cur = full.get(block, {}) or {}
        c1, c2 = st.columns(2)
        otp = c1.number_input("OTP tolerance (minutes)", value=int(_g(cur, "otp_tolerance_minutes", 120)), min_value=0, step=5)
        sap = c2.number_input("SAP match max window (hours)", value=int(_g(cur, "sap_match_max_hours", 36)), min_value=1, step=1)
        c3, c4 = st.columns(2)
        tw = c3.number_input("Telemetry match window (minutes)", value=int(_g(cur, "telemetry_window_minutes", 120)), min_value=0, step=10)
        tmin = c4.number_input("Min telemetry pings/stop", value=int(_g(cur, "telemetry_min_pings_per_stop", 5)), min_value=0, step=1)
        si = st.number_input("Telemetry sample interval (minutes)", value=int(_g(cur, "telemetry_sample_interval_minutes", 15)), min_value=1, step=1)

        def _save():
            update_block(block, {
                "otp_tolerance_minutes": otp,
                "sap_match_max_hours": sap,
                "telemetry_window_minutes": tw,
                "telemetry_min_pings_per_stop": tmin,
                "telemetry_sample_interval_minutes": si,
            })
        _tab_save_reset(block, _save, "match")

    # ───────── Validation ─────────
    with tabs[1]:
        st.subheader("Validation thresholds")
        st.caption("When to raise WARNING-level findings.")
        block = "validation"
        cur = full.get(block, {}) or {}
        c1, c2, c3 = st.columns(3)
        sap_floor = c1.number_input("SAP match-rate floor", value=float(_g(cur, "sap_match_rate_floor", 0.5)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f", help="Warn if matched/total falls below this.")
        tel_floor = c2.number_input("Telemetry coverage floor", value=float(_g(cur, "telemetry_coverage_floor", 0.3)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        miles_var = c3.number_input("Miles variance threshold (%)", value=float(_g(cur, "miles_variance_threshold_pct", 10)), min_value=0.0, max_value=100.0, step=1.0, format="%.1f")

        def _save():
            update_block(block, {
                "sap_match_rate_floor": sap_floor,
                "telemetry_coverage_floor": tel_floor,
                "miles_variance_threshold_pct": miles_var,
            })
        _tab_save_reset(block, _save, "valid")

    # ───────── Reefer ─────────
    with tabs[2]:
        st.subheader("Reefer / Telemetry safety thresholds")
        st.caption("Plasma cold-chain setpoint, excursion criteria, door safety.")
        block = "reefer"
        cur = full.get(block, {}) or {}
        c1, c2 = st.columns(2)
        sp = c1.number_input("Reefer setpoint (°C)", value=float(_g(cur, "setpoint_c", -25)), step=1.0, format="%.1f")
        tol = c2.number_input("Tolerance (± °C)", value=float(_g(cur, "tolerance_c", 5)), min_value=0.0, step=0.5, format="%.1f")
        c3, c4 = st.columns(2)
        exc = c3.number_input("Excursion min duration (minutes)", value=int(_g(cur, "excursion_min_minutes", 15)), min_value=1, step=1)
        ds = c4.number_input("Door-open-while-moving speed threshold (mph)", value=float(_g(cur, "door_open_speed_threshold_mph", 5)), min_value=0.0, step=1.0, format="%.1f")

        def _save():
            update_block(block, {
                "setpoint_c": sp,
                "tolerance_c": tol,
                "excursion_min_minutes": exc,
                "door_open_speed_threshold_mph": ds,
            })
        _tab_save_reset(block, _save, "reefer")

    # ───────── Claims-Risk ─────────
    with tabs[3]:
        st.subheader("Claims-Risk Index")
        st.caption("Weights blend three component signals; bands carve the 0–100 score.")
        block = "claims_risk"
        cur = full.get(block, {}) or {}
        st.markdown("**Weights** (should sum to 1.0)")
        c1, c2, c3 = st.columns(3)
        w_short = c1.number_input("Short-cases weight", value=float(_g(cur, "weight_short_cases", 0.4)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        w_exc = c2.number_input("Excursion weight", value=float(_g(cur, "weight_excursion", 0.4)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        w_door = c3.number_input("Door-events weight", value=float(_g(cur, "weight_door_events", 0.2)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        ok, total = _validate_weights({"a": w_short, "b": w_exc, "c": w_door})
        if not ok:
            st.warning(f"Weights sum to {total:.2f} (should be 1.0).")
        st.markdown("**Band thresholds**")
        c4, c5, c6 = st.columns(3)
        thr = c4.number_input("Door-event count threshold (only count >X)", value=int(_g(cur, "door_event_count_threshold", 5)), min_value=0, step=1)
        bh = c5.number_input("HIGH band score >=", value=int(_g(cur, "band_high", 70)), min_value=0, max_value=100, step=5)
        bm = c6.number_input("MEDIUM band score >=", value=int(_g(cur, "band_medium", 40)), min_value=0, max_value=100, step=5)

        def _save():
            update_block(block, {
                "weight_short_cases": w_short,
                "weight_excursion": w_exc,
                "weight_door_events": w_door,
                "door_event_count_threshold": thr,
                "band_high": bh,
                "band_medium": bm,
            })
        _tab_save_reset(block, _save, "risk")

    # ───────── Driver Scorecard ─────────
    with tabs[4]:
        st.subheader("Driver Scorecard weights")
        st.caption("Blend OTP / late-rate / dwell / cases-variance into the composite 0–100 score.")
        block = "driver_scorecard"
        cur = full.get(block, {}) or {}
        c1, c2 = st.columns(2)
        w_otp = c1.number_input("OTP weight", value=float(_g(cur, "weight_otp", 0.4)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        w_late = c2.number_input("Late-rate weight", value=float(_g(cur, "weight_late_rate", 0.2)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        c3, c4 = st.columns(2)
        w_dw = c3.number_input("Dwell weight", value=float(_g(cur, "weight_dwell", 0.2)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        w_var = c4.number_input("Cases-variance weight", value=float(_g(cur, "weight_cases_variance", 0.2)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        ok, total = _validate_weights({"a": w_otp, "b": w_late, "c": w_dw, "d": w_var})
        if not ok:
            st.warning(f"Weights sum to {total:.2f} (should be 1.0).")

        def _save():
            update_block(block, {
                "weight_otp": w_otp,
                "weight_late_rate": w_late,
                "weight_dwell": w_dw,
                "weight_cases_variance": w_var,
            })
        _tab_save_reset(block, _save, "driver")

    # ───────── Forecast & Detention ─────────
    with tabs[5]:
        st.subheader("Demand forecast")
        f = full.get("forecast", {}) or {}
        c1, c2, c3 = st.columns(3)
        h = c1.number_input("Horizon (weeks)", value=int(_g(f, "horizon_weeks", 4)), min_value=1, step=1)
        a = c2.number_input("Smoothing α (0–1)", value=float(_g(f, "alpha", 0.5)), min_value=0.0, max_value=1.0, step=0.05, format="%.2f")
        mh = c3.number_input("Min weeks of history", value=int(_g(f, "min_weeks_history", 3)), min_value=1, step=1)

        st.markdown("---")
        st.subheader("Detention audit")
        d = full.get("detention", {}) or {}
        det = st.number_input("Detention threshold (dwell minutes)", value=int(_g(d, "threshold_minutes", 120)), min_value=0, step=15)

        def _save():
            update_block("forecast", {"horizon_weeks": h, "alpha": a, "min_weeks_history": mh})
            update_block("detention", {"threshold_minutes": det})
        _tab_save_reset("forecast", _save, "fcst")

    # ───────── Customer Churn ─────────
    with tabs[6]:
        st.subheader("Customer churn bands")
        st.caption("Week-over-week PRO# change buckets. Negative values are drops.")
        block = "churn"
        cur = full.get(block, {}) or {}
        c1, c2, c3 = st.columns(3)
        crisk = c1.number_input("CHURN_RISK if delta % <=", value=float(_g(cur, "band_churn_risk_pct", -50)), step=5.0, format="%.1f")
        decl = c2.number_input("DECLINING if delta % <=", value=float(_g(cur, "band_declining_pct", -20)), step=5.0, format="%.1f")
        grow = c3.number_input("GROWING if delta % >=", value=float(_g(cur, "band_growing_pct", 50)), step=5.0, format="%.1f")

        def _save():
            update_block(block, {
                "band_churn_risk_pct": crisk,
                "band_declining_pct": decl,
                "band_growing_pct": grow,
            })
        _tab_save_reset(block, _save, "churn")

    # ───────── Capacity ─────────
    with tabs[7]:
        st.subheader("Trailer fill-capacity behavior")
        st.caption(
            "Per-trailer max cases / weight live in `config/trailer_capacity.yaml` "
            "(edit on the **Configuration** page). These knobs control HOW the "
            "fallback / fill-% calc works."
        )
        block = "capacity"
        cur = full.get(block, {}) or {}
        c1, c2 = st.columns(2)
        ms = c1.number_input("Min observed stops to use observed cap", value=int(_g(cur, "min_observed_stops", 5)), min_value=1, step=1)
        cap = c2.number_input("Fill % display cap", value=float(_g(cur, "fill_pct_cap", 200)), min_value=100.0, step=10.0, format="%.0f")
        c3, c4 = st.columns(2)
        umax = c3.checkbox("Use MAX for observed (vs quantile)", value=bool(_g(cur, "use_max_for_observed", True)))
        oq = c4.number_input("Observed quantile (when not using MAX)", value=float(_g(cur, "observed_quantile", 0.95)), min_value=0.0, max_value=1.0, step=0.01, format="%.2f")

        def _save():
            update_block(block, {
                "min_observed_stops": ms,
                "use_max_for_observed": umax,
                "fill_pct_cap": cap,
                "observed_quantile": oq,
            })
        _tab_save_reset(block, _save, "cap")

    # ───────── Revenue ─────────
    with tabs[8]:
        st.subheader("Revenue inputs")
        st.caption(
            "Default per-customer rates (mile / stop / cwt / minimum charge) live "
            "in `config/customer_rates.yaml` (edit on the **Configuration** page). "
            "This tab tunes the cost-side fuel price."
        )
        block = "pipeline"
        cur = full.get(block, {}) or {}
        fp = st.number_input("Reefer fuel price ($/gal)", value=float(_g(cur, "fuel_price_per_gallon", 4.50)), min_value=0.0, step=0.05, format="%.2f")

        def _save():
            update_block(block, {"fuel_price_per_gallon": fp})
        _tab_save_reset(block, _save, "rev")

    # ───────── Map UI ─────────
    with tabs[9]:
        st.subheader("Live Map defaults")
        block = "map"
        cur = full.get(block, {}) or {}
        c1, c2 = st.columns(2)
        ms = c1.number_input("Default max stops to render", value=int(_g(cur, "default_max_stops_render", 1500)), min_value=100, max_value=10000, step=100)
        ht = c2.number_input("Default map height (px)", value=int(_g(cur, "default_height_px", 900)), min_value=400, max_value=2000, step=50)

        def _save():
            update_block(block, {
                "default_max_stops_render": ms,
                "default_height_px": ht,
            })
        _tab_save_reset(block, _save, "map")

    # ───────── Warehouse Inclusion ─────────
    with tabs[10]:
        st.subheader("Warehouse-stop inclusion per KPI")
        st.caption(
            "Decide which KPIs include warehouse / distribution-center / "
            "internal-base stops. **Customer-facing KPIs default to plasma-only**; "
            "**fleet/asset KPIs default to all stops**. See the **Warehouse Impact** "
            "page first to understand the effect of each toggle."
        )
        block = "warehouse_inclusion"
        cur = full.get(block, {}) or {}
        toggle_map: dict = {}

        cust_keys = ["otp", "dwell", "customer_scorecard", "customer_churn",
                     "customer_concentration", "claims_risk", "reefer_compliance",
                     "detention_audit", "late_code_analysis", "cycle_time"]
        fleet_keys = ["route_kpi", "trailer_utilization", "driver_scorecard",
                      "lane_profitability", "loaded_miles", "trailer_revenue_weekly",
                      "route_revenue_weekly", "miles_variance", "alarm_log",
                      "route_reefer_cost"]

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Customer-facing KPIs** (default: exclude warehouses)")
            for k in cust_keys:
                toggle_map[k] = st.checkbox(
                    k, value=bool(_g(cur, k, False)), key=f"wi_{k}",
                )
        with c2:
            st.markdown("**Fleet / asset KPIs** (default: include warehouses)")
            for k in fleet_keys:
                toggle_map[k] = st.checkbox(
                    k, value=bool(_g(cur, k, True)), key=f"wi_{k}",
                )

        def _save():
            update_block(block, toggle_map)
        _tab_save_reset(block, _save, "wi")

    st.markdown("---")
    st.caption(
        f"Settings file: `{DEFAULT_YAML_PATH}` — version-control this file to track changes. "
        "After saving any tab, re-run the pipeline (Load Data → Run Pipeline) to apply."
    )
