"""Top-of-page UX widgets: pipeline-run banner + active-filter chips."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from dashboard.components.data import load_table


def render_run_banner() -> None:
    """One-line banner showing the latest pipeline run.

    "Last run · 2026-05-02 14:23 · 3,361 stops · 4 sources · 95.6% telem coverage"
    Renders nothing if no runs exist.
    """
    runs = load_table("pipeline_run")
    if runs.empty:
        st.info("No pipeline run yet. Go to **Load Data** to process source files.", icon="ℹ️")
        return

    runs = runs.copy()
    runs["run_timestamp"] = pd.to_datetime(runs["run_timestamp"], errors="coerce")
    latest = runs.sort_values("run_timestamp", ascending=False).iloc[0]

    ts = latest["run_timestamp"]
    ts_str = ts.strftime("%Y-%m-%d %H:%M") if pd.notna(ts) else "?"

    bits = [f"**Last run:** {ts_str}"]
    try:
        records = json.loads(latest.get("records_read") or "{}")
        bits.append(f"**Sources:** {len(records)}")
    except Exception:
        records = {}

    try:
        out = json.loads(latest.get("records_output") or "{}")
        if "stop_master" in out:
            bits.append(f"**Stops:** {int(out['stop_master']):,}")
        if "billing_snapshot" in out and out["billing_snapshot"]:
            bits.append(f"**Billing rows:** {int(out['billing_snapshot']):,}")
    except Exception:
        pass

    try:
        rates = json.loads(latest.get("match_rates") or "{}")
        if "sap_match_rate" in rates:
            bits.append(f"**SAP match:** {rates['sap_match_rate']}")
        if "telemetry_coverage" in rates:
            bits.append(f"**Telem cov:** {rates['telemetry_coverage']}")
    except Exception:
        pass

    status = latest.get("status", "?")
    icon = "✅" if status == "SUCCESS" else "⚠️"
    st.caption(f"{icon} " + " · ".join(bits))


def render_filter_chips(flt) -> None:
    """Render active filter chips with a clear-all button.

    Pass in the GlobalFilters dataclass returned by render_global_filters.
    Renders nothing if no filters are active.
    """
    if flt is None:
        return

    active: list[str] = []

    if flt.has_date_range():
        active.append(f"📅 {flt.date_start} → {flt.date_end}")
    if getattr(flt, "customers", None):
        s = ", ".join(flt.customers[:3])
        more = f" +{len(flt.customers) - 3}" if len(flt.customers) > 3 else ""
        active.append(f"🏢 {s}{more}")
    if getattr(flt, "routes", None):
        s = ", ".join(flt.routes[:3])
        more = f" +{len(flt.routes) - 3}" if len(flt.routes) > 3 else ""
        active.append(f"🛣️ {s}{more}")
    if getattr(flt, "s_codes", None):
        active.append(f"🔢 {len(flt.s_codes)} S-codes")
    if getattr(flt, "lanes", None):
        active.append(f"🛤️ {len(flt.lanes)} lanes")
    if getattr(flt, "stop_type", None):
        active.append(f"📍 {flt.stop_type}")
    if getattr(flt, "performance_statuses", None):
        active.append(f"⏱️ {', '.join(flt.performance_statuses)}")
    if getattr(flt, "order_search", "").strip():
        active.append(f"🔍 \"{flt.order_search}\"")

    if not active:
        return

    chip_html = " ".join(
        f'<span style="background:#e0e7ff;color:#3730a3;padding:3px 10px;border-radius:12px;'
        f'font-size:0.85em;margin-right:6px;display:inline-block;">{c}</span>'
        for c in active
    )
    cols = st.columns([8, 1])
    with cols[0]:
        st.markdown(f"**Filtering:** {chip_html}", unsafe_allow_html=True)
    with cols[1]:
        if st.button("Clear", key="topbar_clear_filters", help="Clear all active filters"):
            for k in [
                "gf_date_start", "gf_date_end", "gf_customers", "gf_s_codes",
                "gf_routes", "gf_lanes", "gf_stop_type", "gf_perf", "gf_order_search",
            ]:
                st.session_state.pop(k, None)
            st.rerun()
