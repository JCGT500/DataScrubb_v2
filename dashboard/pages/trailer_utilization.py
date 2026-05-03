"""Trailer Utilization — % days in service, idle detection, activity heatmap, Pareto."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine
from dashboard.components.export_button import export_dataframe
from dashboard.components.help import kpi_help
from dashboard.components.page_filters import trailer_filters
from dashboard.components.sidebar import render_global_filters
from dashboard.components.topbar import render_filter_chips, render_run_banner


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
    st.header("Trailer Utilization")
    st.caption(
        "How hard is each trailer working? `utilization_pct` = active_days / period_days. "
        "An idle trailer = capital sitting still."
    )

    util = _load("trailer_utilization")
    if util.empty:
        st.info("Trailer-utilization table not populated. Run the pipeline from **Load Data**.")
        return

    render_run_banner()

    # Shared global filters → restrict trailer set to those whose stops survived
    stops_master = _load("stop_master")
    keep_trailers: set | None = None
    flt = None
    if not stops_master.empty:
        filtered_stops, flt = render_global_filters(stops_master)
        if "trailer" in filtered_stops.columns:
            keep_trailers = set(filtered_stops["trailer"].astype(str).str.strip().str.upper().dropna().unique())
    render_filter_chips(flt)

    # Page-specific qualifier widgets
    with st.sidebar:
        st.subheader("Trailer-page knobs")
        min_stops = st.number_input("Min stops to include", min_value=1, value=1, step=1)
        state_opts = sorted([s for s in util["last_known_state"].dropna().unique() if s])
        sel_states = st.multiselect("Last-known state", options=state_opts)
    page_flt = trailer_filters(util)

    df = util.copy()
    df["_trailer_norm"] = df["trailer"].astype(str).str.strip().str.upper()
    if keep_trailers is not None:
        df = df[df["_trailer_norm"].isin(keep_trailers)]
    df = df[df["total_stops"] >= min_stops]
    if sel_states:
        df = df[df["last_known_state"].isin(sel_states)]
    # Apply page-specific filters
    if page_flt["util_band"] != "All" and "utilization_pct" in df.columns and not df.empty:
        q1 = df["utilization_pct"].quantile(0.25)
        q3 = df["utilization_pct"].quantile(0.75)
        if page_flt["util_band"] == "Top quartile":
            df = df[df["utilization_pct"] >= q3]
        elif page_flt["util_band"] == "Bottom quartile":
            df = df[df["utilization_pct"] <= q1]
        else:
            df = df[(df["utilization_pct"] > q1) & (df["utilization_pct"] < q3)]
    if page_flt["prefix"]:
        df = df[df["_trailer_norm"].str.startswith(page_flt["prefix"])]
    if page_flt["batt_band"] != "All" and "min_battery_seen" in df.columns:
        if page_flt["batt_band"] == "Critical (<11.5V)":
            df = df[df["min_battery_seen"] < 11.5]
        elif page_flt["batt_band"] == "Warning (11.5-12V)":
            df = df[(df["min_battery_seen"] >= 11.5) & (df["min_battery_seen"] < 12)]
        else:
            df = df[df["min_battery_seen"] >= 12]
    if page_flt["alarm_min"] > 0 and "alarm_event_total" in df.columns:
        df = df[df["alarm_event_total"].fillna(0) >= page_flt["alarm_min"]]
    df = df.drop(columns=["_trailer_norm"], errors="ignore")

    if df.empty:
        st.warning("No trailers match the current filters.")
        return

    # Headline KPIs
    period = int(df["period_days"].iloc[0]) if "period_days" in df.columns and not df.empty else 0
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Trailers", f"{len(df):,}")
    c2.metric("Period (days)", f"{period}", help="Max date − min date in the dataset.")
    c3.metric("Avg Utilization", f"{df['utilization_pct'].mean():.1f}%",
              help=kpi_help("utilization_pct"))
    c4.metric("Total Stops", f"{int(df['total_stops'].sum()):,}")
    c5.metric("Total Miles", f"{int(df['total_miles'].fillna(0).sum()):,}",
              help="Sum of M3PL crst_miles attributed to these trailers.")

    # Quartile bands
    q = df["utilization_pct"].quantile([0.25, 0.5, 0.75])
    high = (df["utilization_pct"] >= q[0.75]).sum()
    mid = ((df["utilization_pct"] >= q[0.25]) & (df["utilization_pct"] < q[0.75])).sum()
    low = (df["utilization_pct"] < q[0.25]).sum()
    bc1, bc2, bc3 = st.columns(3)
    bc1.metric(f"Top quartile (≥{q[0.75]:.0f}%)", f"{high:,}")
    bc2.metric(f"Middle 50% ({q[0.25]:.0f}-{q[0.75]:.0f}%)", f"{mid:,}")
    bc3.metric(f"Bottom quartile (<{q[0.25]:.0f}%)", f"{low:,}")

    st.markdown("---")

    # Utilization distribution
    cc1, cc2 = st.columns(2)
    with cc1:
        st.subheader("Utilization Distribution")
        fig = px.histogram(
            df, x="utilization_pct", nbins=30,
            labels={"utilization_pct": "Utilization %"},
            title="How many trailers at each utilization band?",
        )
        fig.add_vline(x=q[0.25], line_dash="dash", line_color="red", annotation_text="Q1")
        fig.add_vline(x=q[0.75], line_dash="dash", line_color="green", annotation_text="Q3")
        st.plotly_chart(fig, use_container_width=True)

    with cc2:
        st.subheader("Bottom 20 Trailers by Utilization")
        bottom = df.nsmallest(20, "utilization_pct").copy()
        bottom["label"] = bottom["trailer"].astype(str) + (
            " · " + bottom["last_known_state"].fillna("?") + ""
        )
        fig = px.bar(
            bottom.sort_values("utilization_pct"),
            x="utilization_pct", y="label", orientation="h",
            color="utilization_pct",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            range_color=[0, 100],
            labels={"utilization_pct": "Utilization %", "label": "Trailer · last state"},
            hover_data=["active_days", "idle_days", "max_consecutive_idle_days", "total_stops", "total_miles"],
        )
        fig.update_layout(height=500, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    # Pareto: cumulative miles share
    st.markdown("---")
    st.subheader("Pareto — cumulative miles share by trailer")
    st.caption("80/20 check: how few trailers move how much of the total mileage?")
    pareto = df.sort_values("total_miles", ascending=False).reset_index(drop=True).copy()
    total_miles_all = pareto["total_miles"].fillna(0).sum() or 1
    pareto["cum_miles"] = pareto["total_miles"].fillna(0).cumsum()
    pareto["cum_miles_pct"] = (pareto["cum_miles"] / total_miles_all * 100).round(1)
    pareto["trailer_rank"] = pareto.index + 1
    fig = px.line(
        pareto, x="trailer_rank", y="cum_miles_pct",
        title="Cumulative miles share — trailers ranked highest to lowest",
        labels={"trailer_rank": "Trailer rank", "cum_miles_pct": "Cumulative miles %"},
        markers=False,
    )
    fig.add_hline(y=80, line_dash="dash", line_color="orange", annotation_text="80%")
    st.plotly_chart(fig, use_container_width=True)

    # Activity heatmap (trailer × date)
    st.markdown("---")
    st.subheader("Activity Heatmap — Trailer × Date")
    st.caption("White = no stops that day. Color intensity = stop count.")
    stops = _load("stop_master")
    if stops.empty:
        st.info("Need stop_master to build heatmap.")
    else:
        sm = stops.copy()
        sm["trailer"] = sm["trailer"].astype(str).str.strip()
        sm = sm[sm["trailer"].isin(df["trailer"])]
        sm["arrival_dt"] = pd.to_datetime(sm["arrival_date"], errors="coerce")
        sm = sm[sm["arrival_dt"].notna()]
        if sm.empty:
            st.info("No stops with valid arrival dates for selected trailers.")
        else:
            # Limit to top 50 trailers by total stops to keep heatmap legible
            top_trailers = (
                sm.groupby("trailer").size().sort_values(ascending=False).head(50).index.tolist()
            )
            sm_top = sm[sm["trailer"].isin(top_trailers)].copy()
            sm_top["d"] = sm_top["arrival_dt"].dt.normalize()
            pivot = (
                sm_top.groupby(["trailer", "d"])
                .size()
                .reset_index(name="stops")
                .pivot(index="trailer", columns="d", values="stops")
                .fillna(0)
                .reindex(top_trailers)
            )
            fig = px.imshow(
                pivot,
                color_continuous_scale="Blues",
                aspect="auto",
                labels={"x": "Date", "y": "Trailer", "color": "Stops"},
                title="Top 50 trailers — daily stop activity",
            )
            fig.update_layout(height=900)
            st.plotly_chart(fig, use_container_width=True)

    # ───────────────── Trailer fill capacity ─────────────────
    st.markdown("---")
    st.subheader("Trailer Fill % to Capacity")
    st.caption(
        "How loaded each trailer was on average. Capacity priority: explicit "
        "config (`config/trailer_capacity.yaml`) → observed 95th-percentile of "
        "historical loads → default. Edit config via the **Rate Matrix** page."
    )

    if "avg_fill_pct_cases" in df.columns and df["avg_fill_pct_cases"].notna().any():
        fc1, fc2, fc3, fc4 = st.columns(4)
        fc1.metric("Avg fill % (cases)", f"{df['avg_fill_pct_cases'].mean():.1f}%")
        fc2.metric("Max fill % (cases)", f"{df['max_fill_pct_cases'].max():.1f}%")
        if "pct_stops_over_90" in df.columns:
            over_n = (df["pct_stops_over_90"] > 0).sum()
            fc3.metric("Trailers with >=1 over-90% stop", f"{over_n:,}")
        if "avg_fill_pct_weight" in df.columns:
            fc4.metric("Avg fill % (weight)", f"{df['avg_fill_pct_weight'].mean():.1f}%")

        # Top + bottom 20 by avg fill
        ftab1, ftab2 = st.tabs(["Most loaded trailers", "Least loaded trailers"])
        with ftab1:
            top = df[df["avg_fill_pct_cases"].notna()].nlargest(20, "avg_fill_pct_cases").copy()
            top["label"] = top["trailer"].astype(str)
            fig = px.bar(
                top.sort_values("avg_fill_pct_cases"),
                x="avg_fill_pct_cases", y="label", orientation="h",
                color="avg_fill_pct_cases",
                color_continuous_scale="RdYlGn",
                range_color=[0, 100],
                title="Top 20 trailers by avg fill % (cases)",
                labels={"avg_fill_pct_cases": "Avg Fill %", "label": "Trailer"},
                hover_data=["max_fill_pct_cases", "total_stops", "loaded_pct"],
            )
            fig.update_layout(height=500, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        with ftab2:
            bot = df[df["avg_fill_pct_cases"].notna()].nsmallest(20, "avg_fill_pct_cases").copy()
            bot["label"] = bot["trailer"].astype(str)
            fig = px.bar(
                bot.sort_values("avg_fill_pct_cases"),
                x="avg_fill_pct_cases", y="label", orientation="h",
                color="avg_fill_pct_cases",
                color_continuous_scale="RdYlGn",
                range_color=[0, 100],
                title="Bottom 20 trailers by avg fill % (right-sizing candidates)",
                labels={"avg_fill_pct_cases": "Avg Fill %", "label": "Trailer"},
                hover_data=["max_fill_pct_cases", "total_stops", "loaded_pct"],
            )
            fig.update_layout(height=500, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        # Fill distribution (per stop, all stops)
        stops_df = _load("stop_master")
        if not stops_df.empty and "fill_pct_cases" in stops_df.columns:
            fig = px.histogram(
                stops_df.dropna(subset=["fill_pct_cases"]),
                x="fill_pct_cases", nbins=50,
                title="Per-stop fill % distribution (cases)",
                labels={"fill_pct_cases": "Fill % cases"},
            )
            fig.add_vline(x=90, line_dash="dash", line_color="red", annotation_text="90%")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No fill % data available — check that `current_cases` is populated and the capacity matrix is loaded.")

    # ───────────────── Weekly revenue per trailer ─────────────────
    st.markdown("---")
    st.subheader("Weekly Revenue per Trailer")
    st.caption("How much money each trailer generates week by week (revenue is attributed by stop count when a route splits across trailers).")
    trw = _load("trailer_revenue_weekly")
    if trw.empty:
        st.info("No trailer-revenue data — needs route_revenue (Rate Matrix populated) to compute.")
    else:
        tot = trw.groupby("trailer").agg(revenue=("revenue", "sum"), margin=("margin", "sum")).reset_index()
        top = tot.nlargest(20, "revenue").copy()
        fig = px.bar(
            top.sort_values("revenue"),
            x="revenue", y="trailer", orientation="h",
            color="margin",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            title="Top 20 trailers by total revenue (color = total margin $)",
            labels={"revenue": "Revenue $", "trailer": "Trailer"},
        )
        fig.update_layout(height=500)
        st.plotly_chart(fig, use_container_width=True)

        trailer_opts = sorted(trw["trailer"].unique())
        sel = st.multiselect(
            "Show trailers (weekly trend)", trailer_opts,
            default=top["trailer"].head(5).tolist(),
            key="trw_select",
        )
        if sel:
            view = trw[trw["trailer"].isin(sel)]
            fig = px.line(
                view, x="week", y="revenue", color="trailer",
                title="Weekly revenue per trailer",
                labels={"week": "Week", "revenue": "Revenue $"},
                markers=True,
            )
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(trw.sort_values(["trailer", "week"]), use_container_width=True, height=300)
        export_dataframe(trw, filename="trailer_revenue_weekly", label="Download Weekly Trailer Revenue")

    # Detail table
    st.markdown("---")
    st.subheader("Per-Trailer Detail")
    sort_options = {
        "Utilization (low first — find idle assets)": ("utilization_pct", True),
        "Utilization (high first)": ("utilization_pct", False),
        "Idle days (most)": ("idle_days", False),
        "Max consecutive idle (longest gap)": ("max_consecutive_idle_days", False),
        "Total miles (most)": ("total_miles", False),
        "Total stops (most)": ("total_stops", False),
        "Loaded % (low — running empty?)": ("loaded_pct", True),
        "Alarm events (most)": ("alarm_event_total", False),
        "Battery low (worst voltage)": ("min_battery_seen", True),
        "Engine hours (highest)": ("max_engine_hours", False),
        "Reefer fuel cost (highest)": ("reefer_fuel_cost_total", False),
        "Trailer (A-Z)": ("trailer", True),
    }
    sort_label = st.selectbox("Sort by", list(sort_options.keys()))
    scol, asc = sort_options[sort_label]
    sorted_df = df.sort_values(scol, ascending=asc, na_position="last")
    st.dataframe(sorted_df, use_container_width=True, height=500)
    export_dataframe(sorted_df, filename="trailer_utilization", label="Download Trailer Utilization")
