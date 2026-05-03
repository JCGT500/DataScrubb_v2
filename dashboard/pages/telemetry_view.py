"""Telemetry page — temperature data, door events, coverage, heat maps."""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine
from dashboard.components.charts import (
    heatmap_door_events_by_dow_hour,
    heatmap_excursions_by_customer_dow,
    heatmap_temp_by_route_seq,
)
from dashboard.components.export_button import export_dataframe
from dashboard.components.sidebar import render_global_filters


def _load(table: str) -> pd.DataFrame:
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    try:
        return pd.read_sql(f"SELECT * FROM {table}", engine)
    except Exception:
        return pd.DataFrame()


def render():
    st.header("Telemetry & Reefer Compliance")

    stops_all = _load("stop_master")
    if stops_all.empty:
        st.info("No data loaded yet. Go to **Load Data** to upload and process files.")
        return

    stops, flt = render_global_filters(stops_all)

    if stops.empty:
        st.warning("No stops match the current filters.")
        return

    # Coverage KPIs
    total = len(stops)
    has_telem_mask = stops["telem_events"].fillna(0) > 0 if "telem_events" in stops.columns else pd.Series(False, index=stops.index)
    with_telem = int(has_telem_mask.sum())
    coverage = with_telem / total * 100 if total > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Stops", f"{total:,}")
    c2.metric("Stops with Telemetry", f"{with_telem:,}")
    c3.metric("Coverage", f"{coverage:.1f}%")
    door_total = int(stops["door_open_events"].fillna(0).sum()) if "door_open_events" in stops.columns else 0
    c4.metric("Door Events (sum)", f"{door_total:,}")

    has = stops[has_telem_mask].copy()
    if has.empty:
        st.warning("No telemetry data for the current filters.")
        return

    st.markdown("---")
    st.subheader("Temperature & Door Distributions")
    cc1, cc2 = st.columns(2)
    with cc1:
        if "avg_amb_temp" in has.columns and has["avg_amb_temp"].notna().any():
            fig = px.histogram(
                has["avg_amb_temp"].dropna(), nbins=40,
                labels={"value": "Avg Ambient Temp (°C)", "count": "Stops"},
                title="Average Ambient Temperature Distribution",
            )
            st.plotly_chart(fig, use_container_width=True)
    with cc2:
        if "min_s1" in has.columns and has["min_s1"].notna().any():
            fig = px.histogram(
                has["min_s1"].dropna(), nbins=40,
                labels={"value": "Min S1 Reefer Temp (°C)", "count": "Stops"},
                title="Min S1 Reefer Temp — should hover near setpoint",
            )
            fig.add_vline(x=-25, line_dash="dash", line_color="green", annotation_text="setpoint")
            st.plotly_chart(fig, use_container_width=True)

    # Heat maps
    st.markdown("---")
    st.subheader("Heat Maps")
    st.caption("Patterns over routes, customers, time-of-week.")

    tab1, tab2, tab3 = st.tabs([
        "Reefer temp: Route × Stop #",
        "Door events: Day × Hour",
        "Excursions: Customer × Day",
    ])
    with tab1:
        st.plotly_chart(heatmap_temp_by_route_seq(has), use_container_width=True)
    with tab2:
        st.plotly_chart(heatmap_door_events_by_dow_hour(has), use_container_width=True)
    with tab3:
        st.plotly_chart(heatmap_excursions_by_customer_dow(has), use_container_width=True)

    # Stop-level table
    st.markdown("---")
    st.subheader("Stop-Level Telemetry")
    sort_options = {
        "Customer (A-Z)": ("customer", True),
        "Min S1 (coldest)": ("min_s1", True),
        "Max S1 (warmest)": ("max_s1", False),
        "Door events (most)": ("door_open_events", False),
        "Telemetry events (most)": ("telem_events", False),
        "Arrival (newest)": ("actual_arrival", False),
    }
    sort_label = st.selectbox("Sort by", list(sort_options.keys()))
    scol, asc = sort_options[sort_label]
    if scol in has.columns:
        has_sorted = has.sort_values(scol, ascending=asc, na_position="last")
    else:
        has_sorted = has

    display_cols = [c for c in [
        "transaction_id", "order_number", "route_name", "customer", "s_code",
        "arrival_date", "trailer", "telem_events",
        "min_amb_temp", "max_amb_temp", "avg_amb_temp",
        "min_s1", "max_s1", "door_open_events",
    ] if c in has_sorted.columns]
    st.dataframe(has_sorted[display_cols], use_container_width=True, height=400)
    export_dataframe(has_sorted, filename="telemetry_stops", label="Download Telemetry Data")

    # Route-level temp compliance summary (load-aware)
    st.markdown("---")
    st.subheader("Route-Level Reefer Compliance — loaded stops only")
    st.caption(
        "Excursions count only when the trailer was loaded with product "
        "(`current_cases > 0` or a delivery stop with cases tendered). "
        "Empty-trailer stops are tracked separately."
    )
    compliance = _load("temp_compliance")
    if compliance.empty:
        st.info("Compliance table not populated yet.")
        return

    keep_routes = set(stops["order_number"].astype(str).str.strip().tolist())
    compliance = compliance[compliance["route_id"].astype(str).str.strip().isin(keep_routes)]

    setpoint = compliance["setpoint_c"].iloc[0] if not compliance.empty and "setpoint_c" in compliance.columns else None
    tolerance = compliance["tolerance_c"].iloc[0] if not compliance.empty and "tolerance_c" in compliance.columns else None
    if setpoint is not None:
        st.caption(f"Setpoint: {setpoint:.0f} °C ± {tolerance:.0f} °C")

    excursion_routes = compliance[compliance["compliance_flag"] == "EXCURSION"]
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Routes Tracked", f"{len(compliance):,}")
    cc2.metric("Excursion Routes", f"{len(excursion_routes):,}")
    cc3.metric(
        "Excursion Min",
        f"{int(excursion_routes['excursion_minutes'].sum()):,}" if not excursion_routes.empty else "0",
    )
    if "empty_stops_skipped" in compliance.columns:
        cc4.metric(
            "Empty Stops (skipped)",
            f"{int(compliance['empty_stops_skipped'].sum()):,}",
        )

    if not excursion_routes.empty:
        top_exc = excursion_routes.nlargest(20, "excursion_count")
        fig = px.bar(
            top_exc.sort_values("excursion_count"),
            x="excursion_count", y="route_id", orientation="h",
            title="Top 20 Routes by Loaded-Stop Excursion Count",
            labels={"excursion_count": "Excursion Stops (loaded)", "route_id": "Route"},
        )
        fig.update_layout(height=500)
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(compliance.sort_values("excursion_count", ascending=False), use_container_width=True, height=400)
    export_dataframe(compliance, filename="temp_compliance", label="Download Compliance")

    # ---- Equipment Health: alarms, battery, engine hours, door safety ----
    st.markdown("---")
    st.subheader("Equipment Health & Safety")

    alarm_df = _load("alarm_log")
    util_df = _load("trailer_utilization")
    if not util_df.empty:
        util_df = util_df.copy()

    h1, h2, h3, h4 = st.columns(4)
    h1.metric(
        "Total alarm events",
        f"{int(stops['alarm_events'].fillna(0).sum()):,}" if "alarm_events" in stops.columns else "—",
    )
    h2.metric(
        "Door-open while moving",
        f"{int(stops['door_open_while_moving'].fillna(0).sum()):,}" if "door_open_while_moving" in stops.columns else "—",
    )
    h3.metric(
        "Total idle minutes",
        f"{int(stops['idle_minutes'].fillna(0).sum()):,}" if "idle_minutes" in stops.columns else "—",
    )
    if not util_df.empty and "min_battery_seen" in util_df.columns:
        low_batt = util_df[util_df["min_battery_seen"] < 11.5]
        h4.metric("Trailers w/ low battery", f"{len(low_batt):,}", help="min battery < 11.5V")
    else:
        h4.metric("Trailers w/ low battery", "—")

    # Alarm leaderboard
    if not alarm_df.empty:
        st.markdown("**Top trailers by alarm event count**")
        top_alarm = alarm_df.head(20).copy()
        fig = px.bar(
            top_alarm.sort_values("alarm_event_total"),
            x="alarm_event_total", y="trailer", orientation="h",
            color="alarm_event_total",
            color_continuous_scale="Reds",
            title="Top 20 trailers by total alarm events",
            labels={"alarm_event_total": "Alarm Events", "trailer": "Trailer"},
            hover_data=["stops_with_alarms", "first_alarm_date", "last_alarm_date", "last_known_state"],
        )
        fig.update_layout(height=500, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(alarm_df, use_container_width=True, height=300)
        export_dataframe(alarm_df, filename="alarm_log", label="Download Alarm Log")
    else:
        st.info("No alarm events captured.")

    # Battery health
    if not util_df.empty and "min_battery_seen" in util_df.columns:
        st.markdown("---")
        st.markdown("**Battery health — bottom 20 trailers by minimum voltage**")
        batt = util_df[util_df["min_battery_seen"].notna()].nsmallest(20, "min_battery_seen").copy()
        if not batt.empty:
            fig = px.bar(
                batt.sort_values("min_battery_seen", ascending=False),
                x="min_battery_seen", y="trailer", orientation="h",
                color="min_battery_seen",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                range_color=[10, 13],
                title="Lowest battery voltage seen per trailer",
                labels={"min_battery_seen": "Min Battery (V)", "trailer": "Trailer"},
                hover_data=["avg_battery", "alarm_event_total", "last_known_state"],
            )
            fig.update_layout(height=500, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    # Door open while moving — security/temp risk
    if "door_open_while_moving" in stops.columns:
        dom = stops[stops["door_open_while_moving"].fillna(0) > 0].copy()
        if not dom.empty:
            st.markdown("---")
            st.markdown("**Door open while moving (safety / temp-loss events)**")
            display = [c for c in [
                "transaction_id", "trailer", "drivers", "customer", "route_name",
                "arrival_date", "door_open_while_moving", "max_speed", "alarm_events",
            ] if c in dom.columns]
            st.dataframe(
                dom.sort_values("door_open_while_moving", ascending=False)[display],
                use_container_width=True, height=300,
            )
            export_dataframe(dom, filename="door_open_while_moving", label="Download Detail")
