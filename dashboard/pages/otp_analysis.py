"""OTP Analysis — drill into on-time performance metrics."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine
from dashboard.components.charts import (
    late_stops_city_map,
    late_stops_state_choropleth,
    minutes_histogram,
    otp_by_customer,
    otp_by_s_code,
    otp_trend,
    performance_distribution,
)
from dashboard.components.sidebar import render_global_filters


def _load_data():
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    return pd.read_sql("SELECT * FROM stop_master", engine)


def render():
    st.header("OTP Analysis")

    df_all = _load_data()
    if df_all.empty:
        st.info("No data loaded yet. Go to **Load Data** to upload and process files.")
        return

    filtered, flt = render_global_filters(df_all)

    if filtered.empty:
        st.warning("No stops match the current filters.")
        return

    metric_options = [
        ("Time Window (Current Appt)", "otp_time_pass"),
        ("Same Day (Current Appt)", "otp_day_pass"),
        ("Time Window (Original Appt)", "otp_time_original_pass"),
        ("Same Day (Original Appt)", "otp_original_pass"),
    ]
    sel = st.selectbox("OTP Metric", metric_options, format_func=lambda x: x[0])
    otp_col = sel[1]

    c1, c2, c3, c4 = st.columns(4)
    pct = filtered[otp_col].mean() * 100 if filtered[otp_col].notna().any() else 0
    c1.metric("OTP Rate", f"{pct:.1f}%")
    c2.metric("Evaluable Stops", f"{int(filtered[otp_col].notna().sum()):,}")
    c3.metric("Total Stops", f"{len(filtered):,}")
    late_min = filtered.loc[filtered["minutes_from_appt"] > 0, "minutes_from_appt"].mean()
    c4.metric("Avg Late (min)", f"{late_min:.0f}" if pd.notna(late_min) else "—")

    st.markdown("---")

    cc1, cc2 = st.columns(2)
    with cc1:
        st.plotly_chart(performance_distribution(filtered), use_container_width=True)
    with cc2:
        st.plotly_chart(otp_trend(filtered, otp_col), use_container_width=True)

    st.subheader("OTP by S-Code")
    st.plotly_chart(otp_by_s_code(filtered, otp_col), use_container_width=True)

    st.subheader("OTP by Customer")
    st.plotly_chart(otp_by_customer(filtered, otp_col), use_container_width=True)

    st.subheader("Minutes from Appointment")
    st.plotly_chart(minutes_histogram(filtered), use_container_width=True)

    st.markdown("---")
    st.subheader("Where are the late stops?")
    map_tab1, map_tab2 = st.tabs(["State heat map", "City pins"])
    with map_tab1:
        st.plotly_chart(late_stops_state_choropleth(filtered), use_container_width=True)
    with map_tab2:
        st.plotly_chart(late_stops_city_map(filtered, max_cities=100), use_container_width=True)
