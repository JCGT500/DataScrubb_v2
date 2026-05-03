"""Cost vs Revenue page (formerly "Billing Reconciliation").

Cost  = what the carrier (M3PL) bills US — per-PRO billed_amount.
Revenue = what we charge the customer, computed from the rate matrix.
Margin = revenue − cost.
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
from dashboard.components.help import kpi_help
from dashboard.components.sidebar import (
    apply_to_billing,
    render_global_filters,
)
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
    st.header("Cost vs Revenue (Margin Analysis)")
    st.caption(
        "**Cost** = what the carrier (M3PL) bills us. "
        "**Revenue** = what we charge the customer (from the Rate Matrix page). "
        "**Margin** = revenue − cost."
    )

    stops_all = _load("stop_master")
    billing_all = _load("billing_snapshot")  # cost
    revenue_all = _load("route_revenue")     # cost vs revenue rollup

    if billing_all.empty and revenue_all.empty:
        st.info("No M3PL billing or revenue data yet. Upload M3PL files via **Load Data** and ensure the Rate Matrix is populated.")
        return

    render_run_banner()

    _, flt = render_global_filters(stops_all if not stops_all.empty else billing_all)
    render_filter_chips(flt)

    billing = apply_to_billing(billing_all, flt)
    # Filter revenue to selected customers/routes
    revenue = revenue_all.copy()
    if flt.customers and "customer" in revenue.columns:
        revenue = revenue[revenue["customer"].isin(flt.customers)]
    if flt.routes and "route_name" in revenue.columns:
        revenue = revenue[revenue["route_name"].isin(flt.routes)]
    if flt.order_search and "route_id" in revenue.columns:
        revenue = revenue[
            revenue["route_id"].astype(str).str.contains(flt.order_search, case=False, na=False)
        ]

    # Headline metrics — use revenue table when available, else cost only
    if not revenue.empty:
        total_cost = revenue["cost"].sum()
        total_rev = revenue["revenue"].sum()
        total_margin = total_rev - total_cost
        margin_pct = (total_margin / total_rev * 100) if total_rev > 0 else 0
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Cost", f"${total_cost:,.0f}", help=kpi_help("total_cost"))
        c2.metric("Total Revenue", f"${total_rev:,.0f}", help=kpi_help("total_revenue"))
        c3.metric("Total Margin", f"${total_margin:,.0f}", delta=f"{margin_pct:.1f}%",
                  help=kpi_help("total_margin"))
        c4.metric("Routes", f"{len(revenue):,}", help="Distinct routes (PRO# / order_#) in scope.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("PRO# rows", f"{len(billing):,}")
        c2.metric("Total Cost", f"${billing['billed_amount'].sum():,.0f}")
        c3.metric("Total Miles", f"{int(billing['crst_miles'].sum()):,}")
        c4.metric("Total Stops", f"{int(billing['stop_count'].sum()):,}")

    st.markdown("---")

    # Tabs: Margin view, Cost detail, Customer rollup, Lane heatmap
    tab1, tab2, tab3, tab4 = st.tabs(["Margin by Route", "Cost (M3PL) detail", "Customer rollup", "Lane Profitability"])

    with tab1:
        if revenue.empty:
            st.info("No revenue rows. Configure the Rate Matrix and re-run the pipeline.")
        else:
            sort_options = {
                "Margin $ (highest)": ("margin", False),
                "Margin $ (lowest / loss)": ("margin", True),
                "Margin % (highest)": ("margin_pct", False),
                "Margin % (lowest)": ("margin_pct", True),
                "Revenue (highest)": ("revenue", False),
                "Cost (highest)": ("cost", False),
                "Customer (A-Z)": ("customer", True),
            }
            sort_label = st.selectbox("Sort by", list(sort_options.keys()))
            scol, asc = sort_options[sort_label]
            rev_sorted = revenue.sort_values(scol, ascending=asc, na_position="last")

            # Margin distribution histogram
            fig = px.histogram(
                rev_sorted, x="margin_pct", nbins=40,
                title="Margin % distribution across routes",
                labels={"margin_pct": "Margin %"},
            )
            fig.add_vline(x=0, line_dash="solid", line_color="red", annotation_text="break-even")
            st.plotly_chart(fig, use_container_width=True)

            # Top-25 by current sort
            head = rev_sorted.head(25).copy()
            head["label"] = head["customer"].fillna("?") + " — " + head["route_id"].astype(str)
            fig = px.bar(
                head.sort_values("margin"),
                x="margin", y="label", orientation="h", color="margin",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                title=f"Margin $ — {sort_label} (top 25)",
                labels={"margin": "Margin $", "label": "Route"},
                hover_data=["customer", "revenue", "cost", "margin_pct"],
            )
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(rev_sorted, use_container_width=True, height=400)
            export_dataframe(rev_sorted, filename="route_revenue", label="Download Margin Analysis")

    with tab2:
        if billing.empty:
            st.info("No M3PL cost data for current filters.")
        else:
            cc1, cc2 = st.columns(2)
            with cc1:
                st.subheader("Cost by Lane")
                lane_totals = billing.groupby("lane")["billed_amount"].sum().reset_index()
                if not lane_totals.empty:
                    fig = px.pie(
                        lane_totals, names="lane", values="billed_amount",
                        title="Cost share by Lane", hole=0.45,
                    )
                    st.plotly_chart(fig, use_container_width=True)
            with cc2:
                st.subheader("Cost by Week")
                wk = (
                    billing.assign(week=pd.to_datetime(billing["billing_week_end"], errors="coerce"))
                    .dropna(subset=["week"])
                    .groupby(["week", "lane"])["billed_amount"].sum()
                    .reset_index()
                )
                if not wk.empty:
                    fig = px.bar(
                        wk, x="week", y="billed_amount", color="lane",
                        title="Weekly Cost by Lane", barmode="stack",
                        labels={"week": "Week Ending", "billed_amount": "Cost $"},
                    )
                    st.plotly_chart(fig, use_container_width=True)

            st.subheader("Cost detail per PRO#")
            st.dataframe(
                billing.sort_values("billed_amount", ascending=False),
                use_container_width=True, height=400,
            )
            export_dataframe(billing, filename="m3pl_cost", label="Download Cost Detail")

    with tab3:
        if revenue.empty:
            st.info("Need revenue data for customer rollup.")
        else:
            cust_roll = (
                revenue.dropna(subset=["customer"])
                .groupby("customer")
                .agg(
                    routes=("route_id", "nunique"),
                    miles=("miles", "sum"),
                    stops=("stop_count", "sum"),
                    cost=("cost", "sum"),
                    revenue=("revenue", "sum"),
                    margin=("margin", "sum"),
                )
                .reset_index()
            )
            cust_roll["margin_pct"] = (
                cust_roll["margin"] / cust_roll["revenue"].replace(0, pd.NA) * 100
            ).round(1)
            cust_roll = cust_roll.sort_values("margin", ascending=False)

            fig = px.bar(
                cust_roll.sort_values("margin"),
                x="margin", y="customer", orientation="h", color="margin_pct",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                title="Customer Margin",
                labels={"margin": "Margin $"},
                hover_data=["revenue", "cost", "margin_pct", "routes", "miles"],
            )
            fig.update_layout(height=max(400, len(cust_roll) * 22))
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(cust_roll, use_container_width=True, height=400)
            export_dataframe(cust_roll, filename="customer_margin", label="Download Customer Rollup")

    with tab4:
        lane = _load("lane_profitability")
        if lane.empty:
            st.info("Lane profitability not populated. Re-run pipeline.")
        else:
            st.caption(
                "Origin = state of the first PU stop on the route. "
                "Destination = state of the last SO stop. Cells colored by margin %."
            )

            # Pivot for heatmap
            pivot_margin = lane.pivot(index="origin_state", columns="dest_state", values="margin_pct")
            pivot_margin = pivot_margin.sort_index().sort_index(axis=1)
            fig = px.imshow(
                pivot_margin,
                color_continuous_scale="RdYlGn",
                aspect="auto",
                labels={"x": "Dest", "y": "Origin", "color": "Margin %"},
                title="Margin % — Origin × Destination state",
            )
            fig.update_layout(height=max(400, len(pivot_margin) * 22))
            st.plotly_chart(fig, use_container_width=True)

            # Volume pivot
            pivot_routes = lane.pivot(index="origin_state", columns="dest_state", values="routes")
            pivot_routes = pivot_routes.sort_index().sort_index(axis=1)
            fig = px.imshow(
                pivot_routes,
                color_continuous_scale="Blues",
                aspect="auto",
                labels={"x": "Dest", "y": "Origin", "color": "Routes"},
                title="Route Volume — Origin × Destination state",
            )
            fig.update_layout(height=max(400, len(pivot_routes) * 22))
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(
                lane.sort_values("margin", ascending=False),
                use_container_width=True, height=400,
            )
            export_dataframe(lane, filename="lane_profitability", label="Download Lane Profitability")
