"""Case Count Variance — where tendered cases ≠ actual cases on board.

Tracks discrepancies between ``tender_cases`` (what the BOL said) and
``current_cases`` (what was on the trailer after the stop). Surfaces customers,
routes, and drivers with the biggest case shortages or overages.
"""

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
from dashboard.components.sidebar import render_global_filters


def _load() -> pd.DataFrame:
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    return pd.read_sql("SELECT * FROM stop_master", engine)


def _load_table(name: str) -> pd.DataFrame:
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    try:
        return pd.read_sql(f"SELECT * FROM {name}", engine)
    except Exception:
        return pd.DataFrame()


def render():
    st.header("Case Count Variance")
    st.caption(
        "Difference between tendered cases (BOL) and the cases that actually landed "
        "on the trailer. Negative = short. Positive = over."
    )

    df_all = _load()
    if df_all.empty:
        st.info("No data loaded yet. Go to **Load Data** to upload and process files.")
        return

    df, flt = render_global_filters(df_all)

    if df.empty:
        st.warning("No stops match the current filters.")
        return

    if "cases_variance" not in df.columns:
        st.warning("`cases_variance` column not present — re-run the pipeline so the new column populates.")
        return

    var = df.copy()
    var["cases_variance"] = pd.to_numeric(var["cases_variance"], errors="coerce")
    nonzero = var[var["cases_variance"].fillna(0) != 0].copy()

    # Headline metrics
    total_var = var["cases_variance"].fillna(0).sum()
    short_total = var.loc[var["cases_variance"] < 0, "cases_variance"].sum()
    over_total = var.loc[var["cases_variance"] > 0, "cases_variance"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Stops with variance", f"{len(nonzero):,}")
    c2.metric("Net variance (cases)", f"{int(total_var):+,}")
    c3.metric("Total short", f"{int(short_total):,}")
    c4.metric("Total over", f"+{int(over_total):,}")

    if nonzero.empty:
        st.success("No case-count discrepancies under the current filters.")
        return

    st.markdown("---")

    # Variance by customer
    st.subheader("Variance by Customer")
    cust = (
        nonzero.dropna(subset=["customer"])
        .assign(short=lambda d: d["cases_variance"].clip(upper=0).abs(), over=lambda d: d["cases_variance"].clip(lower=0))
        .groupby("customer")
        .agg(stops=("transaction_id", "count"), short=("short", "sum"), over=("over", "sum"))
        .reset_index()
    )
    cust["net"] = cust["over"] - cust["short"]
    cust = cust.sort_values("short", ascending=False).head(25)
    if not cust.empty:
        cust_long = cust.melt(
            id_vars=["customer", "stops", "net"],
            value_vars=["short", "over"],
            var_name="direction", value_name="cases",
        )
        fig = px.bar(
            cust_long, x="cases", y="customer", color="direction", orientation="h",
            color_discrete_map={"short": "#ef4444", "over": "#3b82f6"},
            title="Top 25 customers by case-count variance (short vs over)",
            labels={"cases": "Cases", "customer": "Customer"},
            barmode="stack",
        )
        fig.update_layout(height=max(400, len(cust) * 22))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(cust, use_container_width=True, height=300)

    # Variance by driver
    st.markdown("---")
    st.subheader("Variance by Driver")
    drv = (
        nonzero.dropna(subset=["drivers"])
        .assign(short=lambda d: d["cases_variance"].clip(upper=0).abs(), over=lambda d: d["cases_variance"].clip(lower=0))
        .groupby("drivers")
        .agg(stops=("transaction_id", "count"), short=("short", "sum"), over=("over", "sum"))
        .reset_index()
    )
    drv["net"] = drv["over"] - drv["short"]
    drv = drv.sort_values("short", ascending=False).head(25)
    if not drv.empty:
        st.dataframe(drv, use_container_width=True, height=300)

    # Variance by route
    st.markdown("---")
    st.subheader("Variance by Route")
    rt = (
        nonzero.assign(short=lambda d: d["cases_variance"].clip(upper=0).abs(), over=lambda d: d["cases_variance"].clip(lower=0))
        .groupby("order_number")
        .agg(stops=("transaction_id", "count"), short=("short", "sum"), over=("over", "sum"), customer=("customer", "first"), route_name=("route_name", "first"))
        .reset_index()
    )
    rt["net"] = rt["over"] - rt["short"]
    rt = rt.sort_values("short", ascending=False).head(50)
    st.dataframe(rt, use_container_width=True, height=300)

    # Detail table
    st.markdown("---")
    st.subheader("Per-Stop Variance Detail")
    sort_options = {
        "Variance (most short)": ("cases_variance", True),
        "Variance (most over)": ("cases_variance", False),
        "Customer (A-Z)": ("customer", True),
        "Arrival (newest)": ("actual_arrival", False),
    }
    sort_label = st.selectbox("Sort by", list(sort_options.keys()))
    scol, asc = sort_options[sort_label]
    nonzero_sorted = nonzero.sort_values(scol, ascending=asc, na_position="last")

    display_cols = [c for c in [
        "transaction_id", "order_number", "route_name", "customer", "stop_direction",
        "city", "state", "arrival_date", "actual_arrival",
        "tender_cases", "current_cases", "cases_variance",
        "drivers", "trailer", "truck", "late_code",
    ] if c in nonzero_sorted.columns]
    st.dataframe(nonzero_sorted[display_cols], use_container_width=True, height=400)
    export_dataframe(nonzero_sorted, filename="case_variance", label="Download Variance Detail")

    # ---- Claims Risk Index (per route) ----
    st.markdown("---")
    st.subheader("Claims-Risk Index (per route)")
    st.caption(
        "0–100 composite combining case shortages (40%), reefer excursion stops (40%), "
        "and excessive door-open events (20%). Bands: HIGH ≥ 70, MEDIUM ≥ 40, LOW > 0."
    )
    risk = _load_table("claims_risk")
    if risk.empty:
        st.info("Claims-risk table not populated. Re-run pipeline.")
        return

    # Filter to routes inside current stop set
    keep = set(df["order_number"].astype(str).str.strip().tolist())
    risk = risk[risk["route_id"].astype(str).str.strip().isin(keep)]
    if risk.empty:
        st.info("No risk rows match the current filters.")
        return

    band_counts = risk["risk_band"].value_counts().to_dict()
    bc1, bc2, bc3, bc4 = st.columns(4)
    bc1.metric("HIGH risk routes", band_counts.get("HIGH", 0))
    bc2.metric("MEDIUM risk routes", band_counts.get("MEDIUM", 0))
    bc3.metric("LOW risk routes", band_counts.get("LOW", 0))
    bc4.metric("NONE", band_counts.get("NONE", 0))

    top_risk = risk.nlargest(20, "risk_score").copy()
    if not top_risk.empty:
        top_risk["label"] = top_risk["route_name"].fillna("?") + " (" + top_risk["route_id"].astype(str) + ")"
        fig = px.bar(
            top_risk.sort_values("risk_score"),
            x="risk_score", y="label", orientation="h", color="risk_band",
            color_discrete_map={"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#3b82f6", "NONE": "#9ca3af"},
            title="Top 20 Routes by Claims-Risk Score",
            labels={"risk_score": "Risk Score (0-100)", "label": "Route"},
            hover_data=["customer", "short_cases", "excursion_stops", "excess_door_events"],
        )
        fig.update_layout(height=600)
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(risk.sort_values("risk_score", ascending=False), use_container_width=True, height=400)
    export_dataframe(risk, filename="claims_risk", label="Download Claims Risk")
