"""Operations Insights — cycle time, late codes, detention, demand forecast."""

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
from dashboard.components.export_button import export_dataframe
from dashboard.components.page_filters import operations_filters
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
    st.header("Operations Insights")
    st.caption(
        "Cycle-time consistency, late-code root cause, billable detention, "
        "and a 4-week demand forecast per customer."
    )

    # Shared global filters (just to populate the sidebar — filtering operations
    # tables by individual stops is awkward; these surface the global controls
    # so users see consistent filters across pages).
    stops_master = _load("stop_master")
    if not stops_master.empty:
        render_global_filters(stops_master)
    # Page-specific knobs (visible in sidebar; not all wired to filtering yet).
    lc = _load("late_code_analysis")
    code_opts = sorted(lc["late_code"].dropna().unique().tolist()) if not lc.empty else []
    operations_filters(late_codes_opts=code_opts)

    tab1, tab2, tab3, tab4 = st.tabs([
        "Cycle Time Consistency",
        "Late Code Root Cause",
        "Detention Audit",
        "Demand Forecast",
    ])

    # ───────────────── Cycle time ─────────────────
    with tab1:
        st.subheader("How predictable is each named route?")
        st.caption(
            "Std dev of (last departure − first arrival) across all PRO# instances "
            "of the route. Lower std dev = consistent. Consistency % = "
            "100 − (std/avg) × 100."
        )
        ct = _load("cycle_time")
        if ct.empty:
            st.info("Not enough multi-instance routes to compute cycle time.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Routes analyzed", f"{len(ct):,}")
            c2.metric("Avg cycle (min)", f"{ct['avg_cycle_min'].mean():.0f}")
            c3.metric("Avg consistency", f"{ct['consistency_pct'].mean():.1f}%")

            worst = ct.nsmallest(20, "consistency_pct").copy()
            if not worst.empty:
                fig = px.bar(
                    worst.sort_values("consistency_pct"),
                    x="consistency_pct", y="route_name", orientation="h",
                    color="consistency_pct",
                    color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                    range_color=[0, 100],
                    title="20 least-consistent routes (highest variability)",
                    labels={"consistency_pct": "Consistency %", "route_name": "Route"},
                    hover_data=["instances", "avg_cycle_min", "std_cycle_min", "min_cycle_min", "max_cycle_min"],
                )
                fig.update_layout(height=500, showlegend=False)
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(ct, use_container_width=True, height=400)
            export_dataframe(ct, filename="cycle_time", label="Download Cycle Time")

    # ───────────────── Late codes ─────────────────
    with tab2:
        st.subheader("Late codes — what's actually causing the late stops?")
        lc = _load("late_code_analysis")
        if lc.empty:
            st.info("No late codes recorded.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Distinct codes", f"{len(lc):,}")
            c2.metric("Total late stops with codes", f"{int(lc['occurrences'].sum()):,}")
            top_code = lc.iloc[0]["late_code"] if not lc.empty else "—"
            c3.metric("#1 code", top_code)

            fig = px.bar(
                lc.head(20).sort_values("occurrences"),
                x="occurrences", y="late_code", orientation="h",
                color="avg_minutes_late",
                color_continuous_scale="Reds",
                title="Top 20 late codes by occurrence",
                labels={"occurrences": "Occurrences", "late_code": "Late Code", "avg_minutes_late": "Avg min late"},
                hover_data=["distinct_customers", "distinct_routes", "distinct_drivers"],
            )
            fig.update_layout(height=500)
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(lc, use_container_width=True, height=400)
            export_dataframe(lc, filename="late_code_analysis", label="Download Late Codes")

    # ───────────────── Detention ─────────────────
    with tab3:
        st.subheader("Customers running long dwells (billable detention candidates)")
        det = _load("detention_audit")
        if det.empty:
            st.info("No stops over the dwell threshold.")
        else:
            threshold = int(det["threshold_minutes"].iloc[0]) if "threshold_minutes" in det.columns else 120
            st.caption(f"Threshold: dwell > {threshold} min ({threshold/60:.1f} hr)")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Customers", f"{len(det):,}")
            c2.metric("Detention stops", f"{int(det['detention_stops'].sum()):,}")
            c3.metric("Total billable hours", f"{det['billable_hours'].sum():,.1f}")
            c4.metric("Top customer", det.iloc[0]["customer"])

            fig = px.bar(
                det.head(20).sort_values("billable_hours"),
                x="billable_hours", y="customer", orientation="h",
                color="billable_hours",
                color_continuous_scale="Reds",
                title="Top 20 customers by billable detention hours",
                labels={"billable_hours": "Hours (dwell over threshold)", "customer": "Customer"},
                hover_data=["detention_stops", "avg_dwell_min", "max_dwell_min", "distinct_routes"],
            )
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(det, use_container_width=True, height=400)
            export_dataframe(det, filename="detention_audit", label="Download Detention Audit")

    # ───────────────── Demand forecast ─────────────────
    with tab4:
        st.subheader("4-week demand forecast (simple ES, alpha=0.5)")
        st.caption(
            "Per-customer projection of weekly stops based on recent history. "
            "Customers with < 3 weeks of data are excluded."
        )
        fc = _load("demand_forecast")
        if fc.empty:
            st.info("Not enough weekly history. Need at least 3 weeks per customer.")
        else:
            customers_in_fc = sorted(fc["customer"].unique())
            sel = st.multiselect(
                "Show customers", customers_in_fc,
                default=customers_in_fc[:5] if len(customers_in_fc) > 5 else customers_in_fc,
                key="fc_customers",
            )
            view = fc[fc["customer"].isin(sel)] if sel else fc

            # History + forecast on one chart
            stops = _load("stop_master")
            if not stops.empty:
                sm = stops.copy()
                sm["customer"] = sm["customer"].astype(str).str.strip().str.upper()
                sm["arrival_dt"] = pd.to_datetime(sm["arrival_date"], errors="coerce")
                sm = sm[sm["arrival_dt"].notna() & sm["customer"].isin(sel)]
                sm["week"] = sm["arrival_dt"].dt.to_period("W").dt.start_time
                hist = sm.groupby(["customer", "week"]).size().reset_index(name="stops")
                hist["kind"] = "actual"

                fc_view = view.rename(columns={"forecast_week": "week", "forecast_stops": "stops"})
                fc_view = fc_view[["customer", "week", "stops"]].copy()
                fc_view["kind"] = "forecast"

                combined = pd.concat([hist, fc_view], ignore_index=True)
                fig = px.line(
                    combined, x="week", y="stops", color="customer",
                    line_dash="kind", line_dash_map={"actual": "solid", "forecast": "dash"},
                    title="Weekly stops — actual (solid) vs forecast (dashed)",
                    markers=True,
                )
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(view, use_container_width=True, height=400)
            export_dataframe(fc, filename="demand_forecast", label="Download Forecast")
