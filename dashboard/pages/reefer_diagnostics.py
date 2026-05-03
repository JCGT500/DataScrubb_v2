"""Reefer Diagnostics — Vanguard V1 VCI per trailer + readiness check + alerts.

Implements the Vanguard SOP cooling-index model:
  VCI = 0.40·RH + 0.20·DR + 0.20·TS + 0.20·ABHF, with 5 hard overrides.
Bands: GREEN (0–24) · YELLOW (25–49) · ORANGE (50–74) · RED (75–99) · CRITICAL (100).
"""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.components.data import load_table
from dashboard.components.export_button import export_dataframe
from dashboard.components.topbar import render_run_banner

BAND_COLORS = {
    "GREEN": "#22c55e",
    "YELLOW": "#eab308",
    "ORANGE": "#f97316",
    "RED": "#ef4444",
    "CRITICAL": "#7f1d1d",
}


def _band_badge(band: str) -> str:
    color = BAND_COLORS.get(band, "#9ca3af")
    return f'<span style="background:{color};color:white;padding:2px 10px;border-radius:10px;font-weight:600;">{band}</span>'


def render():
    st.header("🛡️ Reefer Diagnostics (Vanguard)")
    st.caption(
        "Per-trailer Vanguard Cooling Index (VCI) for frozen plasma operations. "
        "Combines refrigeration health, defrost behavior, temperature stability, "
        "and bulkhead/airflow into a single 0–100 risk score. "
        "See LOGIC.md → Vanguard for the full formula."
    )

    render_run_banner()

    vci = load_table("trailer_vci")
    baselines = load_table("vanguard_baselines")
    alerts = load_table("vanguard_alerts")

    if vci.empty:
        st.info(
            "No VCI data yet. Run the pipeline (Load Data → Run Pipeline) with "
            "telemetry attached. Vanguard needs DA1, RA1, S1–S6, and OP_1 (defrost) "
            "columns from the reefer telemetry CSV."
        )
        return

    # ─── Fleet summary ───
    st.subheader("Fleet VCI summary")
    band_counts = vci["band"].value_counts().to_dict()
    cols = st.columns(5)
    for i, b in enumerate(["GREEN", "YELLOW", "ORANGE", "RED", "CRITICAL"]):
        cols[i].metric(b, f"{band_counts.get(b, 0):,}")

    cleared = int(vci["can_load_frozen"].sum())
    blocked = int((~vci["can_load_frozen"]).sum())
    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("✅ Cleared for frozen plasma", f"{cleared:,}",
               help="Trailers with VCI < 75 AND no current hot-load. Can be assigned a frozen plasma load.")
    rc2.metric("⛔ Blocked", f"{blocked:,}",
               help="Trailers with VCI ≥ 75 OR active hot-load incident.")
    rc3.metric("Total trailers analyzed", f"{len(vci):,}")

    # ─── Active alerts ───
    st.markdown("---")
    st.subheader("Active alerts")
    if alerts.empty:
        st.success("No active Vanguard alerts in the current period. ✨")
    else:
        st.caption(f"{len(alerts):,} active alert(s) across {alerts['trailer'].nunique()} trailer(s).")
        sev_filter = st.multiselect(
            "Severity", ["HIGH", "MEDIUM", "LOW"], default=["HIGH", "MEDIUM"], key="vci_alert_sev",
        )
        code_filter = st.multiselect(
            "Alert code", sorted(alerts["alert_code"].unique()),
            default=list(alerts["alert_code"].unique()),
            key="vci_alert_code",
        )
        view = alerts[alerts["severity"].isin(sev_filter) & alerts["alert_code"].isin(code_filter)]
        st.dataframe(
            view.sort_values(["severity", "vci_at_trigger"], ascending=[True, False]),
            use_container_width=True, height=320,
        )
        export_dataframe(view, filename="vanguard_alerts", label="Download Alerts")

    # ─── VCI by trailer (top + bottom) ───
    st.markdown("---")
    st.subheader("VCI by trailer")
    sort_options = {
        "VCI (highest risk first)": ("vci", False),
        "VCI (lowest risk first)": ("vci", True),
        "Trailer (A-Z)": ("trailer", True),
    }
    sort_label = st.selectbox("Sort by", list(sort_options.keys()))
    scol, asc = sort_options[sort_label]
    sorted_vci = vci.sort_values(scol, ascending=asc, na_position="last")

    # Color-coded bar of top 30 by VCI
    top = sorted_vci.head(30).copy()
    if not top.empty:
        fig = px.bar(
            top.sort_values("vci"),
            x="vci", y="trailer", orientation="h",
            color="band",
            color_discrete_map=BAND_COLORS,
            title="VCI per trailer (top 30 by current sort)",
            labels={"vci": "VCI", "trailer": "Trailer"},
            hover_data=["rh_score", "dr_score", "ts_score", "abhf_score",
                        "current_evap_delta", "loaded_hot_count"],
        )
        fig.update_layout(height=max(400, len(top) * 22))
        st.plotly_chart(fig, use_container_width=True)

    display_cols = [
        "trailer", "vci", "band", "can_load_frozen", "block_reason",
        "rh_score", "dr_score", "ts_score", "abhf_score",
        "current_evap_delta", "baseline_evap_delta",
        "current_compliance", "baseline_compliance_pct",
        "max_cargo_temp_24h", "defrost_count_24h", "loaded_hot_count",
    ]
    display_cols = [c for c in display_cols if c in sorted_vci.columns]
    st.dataframe(sorted_vci[display_cols], use_container_width=True, height=400)
    export_dataframe(sorted_vci, filename="trailer_vci", label="Download VCI Detail")

    # ─── Per-trailer drill-down ───
    st.markdown("---")
    st.subheader("Per-trailer drill-down")
    pick_options = sorted_vci["trailer"].tolist()
    if not pick_options:
        return
    pick = st.selectbox("Select trailer", pick_options, key="vci_drilldown_trailer")
    sel = sorted_vci[sorted_vci["trailer"] == pick].iloc[0]

    def _safe_int(v, default=0) -> int:
        return int(v) if pd.notna(v) else default

    def _safe_float_str(v, fmt: str, suffix: str = "") -> str:
        return f"{float(v):{fmt}}{suffix}" if pd.notna(v) else "—"

    band = sel["band"]
    can_load = bool(sel.get("can_load_frozen"))
    block = sel.get("block_reason") or ""

    dc1, dc2, dc3 = st.columns([2, 1, 2])
    with dc1:
        st.markdown(f"### {pick}")
        st.markdown(f"**Band:** {_band_badge(band)} · **VCI:** {_safe_int(sel.get('vci'))} / 100", unsafe_allow_html=True)
    with dc2:
        if can_load:
            st.success("✅ Cleared for frozen plasma load")
        else:
            st.error(f"⛔ BLOCKED: {block}")
    with dc3:
        st.metric("Hot-load incidents (24h)", f"{_safe_int(sel.get('loaded_hot_count'))}")

    # Subscore radar / bar
    sub_df = pd.DataFrame({
        "subscore": ["RH (Refrigeration Health)", "DR (Defrost & Recovery)",
                     "TS (Temperature Stability)", "ABHF (Airflow / Bulkhead)"],
        "score": [sel.get("rh_score", 0), sel.get("dr_score", 0),
                  sel.get("ts_score", 0), sel.get("abhf_score", 0)],
        "weight": [40, 20, 20, 20],
    })
    fig = px.bar(
        sub_df, x="subscore", y="score",
        color="score",
        color_continuous_scale=["#22c55e", "#eab308", "#f97316", "#ef4444"],
        range_color=[0, 100],
        title="Subscore breakdown (0 = healthy, 100 = critical)",
        text="score",
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(yaxis_range=[0, 110], showlegend=False, height=400)
    st.plotly_chart(fig, use_container_width=True)

    # Baseline vs current comparison
    bc1, bc2, bc3, bc4 = st.columns(4)
    bc1.metric("Current evap delta", _safe_float_str(sel.get("current_evap_delta"), ".2f", " °C"))
    bc2.metric("Baseline evap delta", _safe_float_str(sel.get("baseline_evap_delta"), ".2f", " °C"))
    bc3.metric("Current compliance", _safe_float_str(sel.get("current_compliance"), ".1f", " %"))
    bc4.metric("Baseline compliance", _safe_float_str(sel.get("baseline_compliance_pct"), ".1f", " %"))

    # Per-trailer alerts
    if not alerts.empty:
        own_alerts = alerts[alerts["trailer"] == pick]
        if not own_alerts.empty:
            st.markdown(f"**Active alerts for {pick}**")
            st.dataframe(own_alerts, use_container_width=True, height=200)

    # Baseline source
    if not baselines.empty:
        b_row = baselines[baselines["trailer"] == pick]
        if not b_row.empty:
            br = b_row.iloc[0]
            st.caption(
                f"Baseline source: **{br.get('baseline_source', 'unknown')}** "
                f"(window: {_safe_int(br.get('baseline_window_days'))} clean days · "
                f"defrost baseline: {_safe_float_str(br.get('baseline_defrost_per_day'), '.1f', '/day')})"
            )

    st.markdown("---")
    st.caption(
        "VCI = Vanguard Cooling Index. Methodology per "
        "**VanguardV1 SOPv2**. See **LOGIC.md → Vanguard** for the full subscore formulas, "
        "the 5 hard-override rules, and the band thresholds. Tune in **Admin → Reefer (Vanguard SOP)**."
    )
