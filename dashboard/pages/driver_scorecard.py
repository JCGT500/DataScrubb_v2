"""Driver Scorecard — composite driver ranking + drill-down."""

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
from dashboard.components.page_filters import driver_filters
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
    st.header("Driver Scorecard")
    st.caption(
        "Composite **0–100** score per driver, blending OTP (40%), low late-rate (20%), "
        "low avg dwell (20%), low cases-variance per stop (20%). Each input is "
        "min-max scaled across the driver pool."
    )

    df = _load("driver_scorecard")
    if df.empty:
        st.info("No driver scorecard data yet. Run the pipeline from **Load Data**.")
        return

    # Shared global filters → restrict driver pool by stops surviving the filter
    stops_master = _load("stop_master")
    keep_drivers: set | None = None
    if not stops_master.empty:
        filtered_stops, _flt = render_global_filters(stops_master)
        if "drivers" in filtered_stops.columns:
            keep_drivers = set(filtered_stops["drivers"].astype(str).str.strip().str.upper().dropna().unique())

    with st.sidebar:
        st.subheader("Driver-page knobs")
        min_stops = st.number_input("Min stops to qualify", min_value=1, value=5, step=1)
    page_flt = driver_filters()

    qualified = df.copy()
    if keep_drivers is not None and "driver" in qualified.columns:
        qualified = qualified[qualified["driver"].astype(str).str.strip().str.upper().isin(keep_drivers)]
    qualified = qualified[qualified["total_stops"] >= min_stops].copy()
    if page_flt["perf_band"] != "All" and not qualified.empty:
        if page_flt["perf_band"] == "Top 10% by score":
            cutoff = qualified["score"].quantile(0.90)
            qualified = qualified[qualified["score"] >= cutoff]
        elif page_flt["perf_band"] == "Bottom 10%":
            cutoff = qualified["score"].quantile(0.10)
            qualified = qualified[qualified["score"] <= cutoff]
        else:  # mid 80%
            lo = qualified["score"].quantile(0.10)
            hi = qualified["score"].quantile(0.90)
            qualified = qualified[(qualified["score"] > lo) & (qualified["score"] < hi)]
    qualified["rank"] = qualified["score"].rank(method="min", ascending=False).astype(int)
    qualified = qualified.sort_values("rank")

    if qualified.empty:
        st.warning(f"No drivers with ≥ {min_stops} stops.")
        return

    # KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Drivers", f"{len(qualified):,}")
    c2.metric("Avg Score", f"{qualified['score'].mean():.1f}")
    c3.metric("Avg OTP", f"{qualified['otp_rate'].mean():.1f}%")
    c4.metric("Total Stops", f"{int(qualified['total_stops'].sum()):,}")

    st.markdown("---")

    # Top + bottom 15
    top = qualified.head(15).copy()
    bot = qualified.tail(15).sort_values("rank", ascending=False).copy()

    cc1, cc2 = st.columns(2)
    with cc1:
        st.subheader("Top 15")
        fig = px.bar(
            top.sort_values("score"),
            x="score", y="driver", orientation="h", color="score",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            range_color=[0, 100],
            labels={"score": "Score (0-100)", "driver": "Driver"},
            hover_data=["total_stops", "otp_rate", "late_rate", "avg_dwell"],
        )
        fig.update_layout(height=500, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with cc2:
        st.subheader("Bottom 15")
        fig = px.bar(
            bot.sort_values("score"),
            x="score", y="driver", orientation="h", color="score",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            range_color=[0, 100],
            labels={"score": "Score (0-100)", "driver": "Driver"},
            hover_data=["total_stops", "otp_rate", "late_rate", "avg_dwell"],
        )
        fig.update_layout(height=500, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    # Score distribution
    st.subheader("Score Distribution")
    fig = px.histogram(qualified, x="score", nbins=30, labels={"score": "Score"})
    fig.add_vline(x=qualified["score"].median(), line_dash="dash", line_color="orange",
                  annotation_text="median")
    st.plotly_chart(fig, use_container_width=True)

    # Detail table — sortable
    st.markdown("---")
    st.subheader("Full Roster")
    sort_options = {
        "Rank": ("rank", True),
        "Score (high)": ("score", False),
        "Stops (most)": ("total_stops", False),
        "OTP (low)": ("otp_rate", True),
        "Late rate (high)": ("late_rate", False),
        "Avg dwell (long)": ("avg_dwell", False),
        "Cases variance (high)": ("cases_variance_per_stop", False),
        "Max speed (highest)": ("max_speed_mph", False),
        "Idle minutes (most)": ("idle_minutes_total", False),
        "Door open while moving": ("door_moving_events", False),
        "Driver (A-Z)": ("driver", True),
    }
    sort_label = st.selectbox("Sort by", list(sort_options.keys()))
    scol, asc = sort_options[sort_label]
    sorted_df = qualified.sort_values(scol, ascending=asc, na_position="last")
    st.dataframe(sorted_df, use_container_width=True, height=500)
    export_dataframe(sorted_df, filename="driver_scorecard", label="Download Scorecard")
