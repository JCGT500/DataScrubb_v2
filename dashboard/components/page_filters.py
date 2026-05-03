"""Page-specific filter widgets that live in a sidebar expander.

Each function renders a focused set of filters relevant to its page's KPIs
(below the shared global filter sidebar) and returns a dict the page applies
to its data. Designed to be opt-in — if you don't call these, the page just
uses the global filters.

Usage pattern (in a page's render()):

    from dashboard.components.page_filters import trailer_utilization_filters
    page_flt = trailer_utilization_filters(util_df)
    if page_flt["util_band"] == "Bottom quartile":
        df = df[df["utilization_pct"] <= df["utilization_pct"].quantile(0.25)]
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _expander(label: str = "Page-specific filters", expanded: bool = False):
    return st.sidebar.expander(label, expanded=expanded)


def overview_filters() -> dict:
    with _expander():
        errors_only = st.checkbox("Show stops with errors only", value=False, key="ov_errors_only")
        sap_status = st.radio(
            "SAP match status",
            ["All", "Matched only", "Unmatched only"],
            horizontal=False, key="ov_sap_status",
        )
    return {"errors_only": errors_only, "sap_status": sap_status}


def route_kpi_filters(routes_df: pd.DataFrame) -> dict:
    with _expander():
        min_stops = st.number_input("Min stops/route", min_value=1, value=1, step=1, key="rk_min_stops")
        otp_band = st.radio(
            "OTP band",
            ["All", "High (>=85%)", "Mid (50-85%)", "Low (<50%)"],
            horizontal=False, key="rk_otp_band",
        )
    return {"min_stops": int(min_stops), "otp_band": otp_band}


def billing_recon_filters(stops_df: pd.DataFrame) -> dict:
    with _expander():
        prof_band = st.radio(
            "Profitability band",
            ["All", "Profit (>10%)", "Break-even (-10..10%)", "Loss (<-10%)"],
            horizontal=False, key="br_prof_band",
        )
        states = []
        if "state" in stops_df.columns:
            opts = sorted([s for s in stops_df["state"].dropna().unique() if s])
            states = st.multiselect("Origin state", options=opts, key="br_origin_states")
    return {"prof_band": prof_band, "states": states}


def case_variance_filters() -> dict:
    with _expander():
        var_band = st.radio(
            "Variance band",
            ["All", "Large short (<-10)", "Large over (>10)", "Near zero (-10..10)"],
            horizontal=False, key="cv_var_band",
        )
        risk_bands = st.multiselect(
            "Claims-risk band",
            ["HIGH", "MEDIUM", "LOW", "NONE"], key="cv_risk_bands",
        )
    return {"var_band": var_band, "risk_bands": risk_bands}


def stop_explorer_filters() -> dict:
    with _expander():
        err_radio = st.radio(
            "Error flag",
            ["All", "Errors only", "Clean only"],
            horizontal=False, key="se_err_radio",
        )
        fill_band = st.radio(
            "Fill % (cases)",
            ["All", "Empty (<10%)", "Light (10-50%)", "Heavy (50-90%)", "Full (>=90%)"],
            horizontal=False, key="se_fill_band",
        )
        dwell_outlier = st.checkbox("Dwell outliers only (>95th pct)", value=False, key="se_dwell_out")
    return {"err_radio": err_radio, "fill_band": fill_band, "dwell_outlier": dwell_outlier}


def otp_analysis_filters(stops_df: pd.DataFrame) -> dict:
    with _expander():
        late_band = st.radio(
            "Minutes-late band",
            ["All", "Early (<-30)", "On time (-30..30)", "1-30 late", "30-120 late", "Severe (>120)"],
            horizontal=False, key="otp_late_band",
        )
        late_codes = []
        if "late_code" in stops_df.columns:
            opts = sorted([c for c in stops_df["late_code"].dropna().astype(str).str.strip().str.upper().unique() if c and c != "NAN"])
            late_codes = st.multiselect("Late codes", options=opts, key="otp_late_codes")
    return {"late_band": late_band, "late_codes": late_codes}


def telemetry_filters() -> dict:
    with _expander():
        excursion_only = st.checkbox("Excursion stops only (loaded out-of-range)", value=False, key="tel_exc_only")
        empty_hide = st.checkbox("Hide empty-trailer stops", value=False, key="tel_empty_hide")
        alarm_min = st.slider("Min alarm events", min_value=0, max_value=50, value=0, key="tel_alarm_min")
    return {"excursion_only": excursion_only, "empty_hide": empty_hide, "alarm_min": int(alarm_min)}


def driver_filters() -> dict:
    with _expander():
        perf_band = st.radio(
            "Performance band",
            ["All", "Top 10% by score", "Mid 80%", "Bottom 10%"],
            horizontal=False, key="drv_perf_band",
        )
    return {"perf_band": perf_band}


def trailer_filters(util_df: pd.DataFrame) -> dict:
    with _expander():
        util_band = st.radio(
            "Utilization band",
            ["All", "Top quartile", "Middle 50%", "Bottom quartile"],
            horizontal=False, key="tu_util_band",
        )
        prefix = st.text_input("Trailer ID prefix (e.g. RX, RF, DUMMY)", value="", key="tu_prefix")
        batt_band = st.radio(
            "Battery health band",
            ["All", "Critical (<11.5V)", "Warning (11.5-12V)", "OK (>=12V)"],
            horizontal=False, key="tu_batt_band",
        )
        alarm_min = st.slider("Min alarm event total", min_value=0, max_value=2000, value=0, step=50, key="tu_alarm_min")
    return {
        "util_band": util_band,
        "prefix": prefix.strip().upper(),
        "batt_band": batt_band,
        "alarm_min": int(alarm_min),
    }


def customer_filters() -> dict:
    with _expander():
        rev_tier = st.radio(
            "Revenue tier",
            ["All", "Top 10", "11-50", "Tail (51+)"],
            horizontal=False, key="cu_rev_tier",
        )
        churn_bands = st.multiselect(
            "Churn band",
            ["CHURN_RISK", "DECLINING", "STABLE", "GROWING", "NEW"],
            key="cu_churn_bands",
        )
        margin_band = st.radio(
            "Margin band",
            ["All", "Profitable (>10%)", "Marginal (0-10%)", "Loss (<0%)"],
            horizontal=False, key="cu_margin_band",
        )
    return {"rev_tier": rev_tier, "churn_bands": churn_bands, "margin_band": margin_band}


def operations_filters(late_codes_opts: list[str] | None = None) -> dict:
    with _expander():
        codes = []
        if late_codes_opts:
            codes = st.multiselect("Late codes", options=late_codes_opts, key="op_late_codes")
        det_thresh = st.number_input("Detention threshold override (min)", min_value=0, value=120, step=15, key="op_det_thresh")
        forecast_horizon = st.selectbox("Forecast horizon", [1, 2, 4, 8, 12], index=2, key="op_horizon")
    return {"late_codes": codes, "det_thresh": int(det_thresh), "forecast_horizon": int(forecast_horizon)}


def sap_matching_filters() -> dict:
    with _expander():
        max_hours = st.slider("Time-diff filter (hours, |sap-arrive − crst-arrival|)", min_value=0, max_value=72, value=72, step=1, key="sap_max_hrs")
        search = st.text_input("Search shipper / consignee (substring)", value="", key="sap_search")
    return {"max_hours": int(max_hours), "search": search.strip()}


def validation_filters(error_reasons: list[str] | None = None, sources: list[str] | None = None) -> dict:
    with _expander():
        err_type = st.radio(
            "Error type",
            ["All", "Hard", "Soft", "Warning"],
            horizontal=True, key="vr_err_type",
        )
        reasons = st.multiselect("Error reason", options=error_reasons or [], key="vr_reasons")
        srcs = st.multiselect("Source", options=sources or ["CRST", "SAP", "TELEMETRY", "M3PL"], key="vr_sources")
    return {"err_type": err_type, "reasons": reasons, "sources": srcs}
