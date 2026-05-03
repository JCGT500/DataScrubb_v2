"""Route KPI page — route-level OTP, dwell, stops, equipment utilization."""

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
from dashboard.components.sidebar import (
    apply_to_routes,
    render_global_filters,
)


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
    st.header("Route KPIs")

    stops_all = _load("stop_master")
    routes_all = _load("route_kpi")

    if routes_all.empty:
        st.info("No route KPI data yet. Run the pipeline from **Load Data**.")
        return

    # Render shared filters; we only really need the route_id list back from stops
    stops, flt = render_global_filters(stops_all)

    # Restrict route_kpi rows to those whose route_id is in filtered stop set
    if not stops.empty and "order_number" in stops.columns:
        keep_routes = set(stops["order_number"].astype(str).str.strip().tolist())
        routes = routes_all[routes_all["route_id"].astype(str).str.strip().isin(keep_routes)].copy()
    else:
        routes = routes_all.copy()
    routes = apply_to_routes(routes, flt)

    if routes.empty:
        st.warning("No routes match the current filters.")
        return

    # Headline KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Routes", f"{len(routes):,}")
    c2.metric(
        "Avg OTP (Time)",
        f"{routes['otp_time_pass_rate'].mean():.1f}%" if not routes.empty else "—",
    )
    c3.metric("Total Stops", f"{int(routes['stop_count'].sum()):,}")
    c4.metric(
        "Avg Dwell (min)",
        f"{routes['dwell_minutes_avg'].mean():.0f}" if routes['dwell_minutes_avg'].notna().any() else "—",
    )

    st.markdown("---")

    sort_options = {
        "OTP rate (lowest first)": ("otp_time_pass_rate", True),
        "OTP rate (highest first)": ("otp_time_pass_rate", False),
        "Stop count (highest first)": ("stop_count", False),
        "Dwell avg (longest first)": ("dwell_minutes_avg", False),
        "Avg minutes late (most late)": ("avg_minutes_late", False),
        "Route name (A-Z)": ("route_name", True),
    }
    sort_label = st.selectbox("Sort routes by", list(sort_options.keys()))
    sort_col, asc = sort_options[sort_label]
    if sort_col in routes.columns:
        routes_sorted = routes.sort_values(sort_col, ascending=asc, na_position="last")
    else:
        routes_sorted = routes

    # OTP by route bar (top 25 of current sort)
    head = routes_sorted.head(25).copy()
    head["label"] = head["route_name"].fillna("?") + " (" + head["route_id"].astype(str) + ")"
    fig = px.bar(
        head.sort_values("otp_time_pass_rate"),
        x="otp_time_pass_rate", y="label", orientation="h",
        color="otp_time_pass_rate",
        color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
        range_color=[0, 100],
        title=f"OTP % — {sort_label} (top 25)",
        labels={"otp_time_pass_rate": "OTP %", "label": "Route"},
        hover_data=["stop_count", "dwell_minutes_avg"],
    )
    fig.update_layout(height=600, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)

    # Dwell vs stops scatter
    if "dwell_minutes_avg" in routes.columns:
        scatter_df = routes[routes["dwell_minutes_avg"].notna() & (routes["dwell_minutes_avg"] > 0)]
        if not scatter_df.empty:
            fig = px.scatter(
                scatter_df, x="stop_count", y="dwell_minutes_avg",
                color="otp_time_pass_rate", hover_data=["route_id", "route_name"],
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"], range_color=[0, 100],
                title="Stops × Avg Dwell (color = OTP %)",
                labels={"stop_count": "Stop Count", "dwell_minutes_avg": "Avg Dwell (min)"},
            )
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("Route Detail")
    display_cols = [
        "route_id", "route_name", "stop_count",
        "otp_time_pass_rate", "otp_day_pass_rate", "avg_minutes_late",
        "dwell_minutes_avg", "dwell_minutes_total",
        "first_arrival", "last_departure",
    ]
    display_cols = [c for c in display_cols if c in routes_sorted.columns]
    st.dataframe(routes_sorted[display_cols], use_container_width=True, height=500)
    export_dataframe(routes_sorted, filename="route_kpi", label="Download Route KPIs")

    # Loaded vs deadhead miles
    st.markdown("---")
    st.subheader("Loaded vs Deadhead Miles")
    st.caption(
        "Estimated split of route miles into loaded (carrying cargo) vs deadhead (empty), "
        "based on stop-to-stop transitions and `current_cases` after each stop."
    )
    loaded = _load("loaded_miles")
    if not loaded.empty and "route_id" in loaded.columns:
        keep = set(stops["order_number"].astype(str).str.strip().tolist()) if not stops.empty else None
        if keep:
            loaded = loaded[loaded["route_id"].astype(str).str.strip().isin(keep)]
        if not loaded.empty:
            tot_miles = loaded["total_miles"].sum()
            tot_loaded = loaded["estimated_loaded_miles"].sum()
            tot_dead = loaded["estimated_deadhead_miles"].sum()
            lc1, lc2, lc3, lc4 = st.columns(4)
            lc1.metric("Total Miles", f"{int(tot_miles):,}")
            lc2.metric("Loaded Miles", f"{int(tot_loaded):,}")
            lc3.metric("Deadhead Miles", f"{int(tot_dead):,}")
            lc4.metric(
                "Loaded %",
                f"{(tot_loaded / tot_miles * 100):.1f}%" if tot_miles else "—",
            )
            top_dh = loaded.nlargest(20, "estimated_deadhead_miles").copy()
            top_dh["label"] = top_dh["route_name"].fillna("?") + " — " + top_dh["route_id"].astype(str)
            fig = px.bar(
                top_dh.sort_values("estimated_deadhead_miles"),
                x="estimated_deadhead_miles", y="label", orientation="h",
                color="loaded_pct",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                range_color=[0, 100],
                title="Top 20 Routes by Deadhead Miles",
                labels={"estimated_deadhead_miles": "Deadhead Miles", "label": "Route", "loaded_pct": "Loaded %"},
                hover_data=["customer", "total_miles", "estimated_loaded_miles"],
            )
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(loaded.sort_values("estimated_deadhead_miles", ascending=False),
                         use_container_width=True, height=400)
            export_dataframe(loaded, filename="loaded_miles", label="Download Loaded vs Deadhead")
        else:
            st.info("No loaded-miles rows for current route filter.")
    else:
        st.info("Loaded-miles table not populated. Re-run pipeline.")

    # Weekly revenue per route
    st.markdown("---")
    st.subheader("Weekly Revenue per Route")
    st.caption("Revenue / margin generated by each named route, week by week.")
    rrw = _load("route_revenue_weekly")
    if rrw.empty:
        st.info("Weekly route-revenue not populated. Re-run pipeline (and ensure route_revenue is computed).")
    else:
        tot = rrw.groupby("route_name").agg(revenue=("revenue", "sum"), margin=("margin", "sum")).reset_index()
        top = tot.nlargest(20, "revenue").copy()
        fig = px.bar(
            top.sort_values("revenue"),
            x="revenue", y="route_name", orientation="h",
            color="margin",
            color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
            title="Top 20 named routes by total revenue (color = total margin $)",
            labels={"revenue": "Revenue $", "route_name": "Route"},
        )
        fig.update_layout(height=500)
        st.plotly_chart(fig, use_container_width=True)

        route_opts = sorted(rrw["route_name"].dropna().unique())
        sel = st.multiselect(
            "Show routes (weekly trend)", route_opts,
            default=top["route_name"].head(5).tolist(),
            key="rrw_select",
        )
        if sel:
            view = rrw[rrw["route_name"].isin(sel)]
            fig = px.line(
                view, x="week", y="revenue", color="route_name",
                title="Weekly revenue per route",
                labels={"week": "Week", "revenue": "Revenue $"},
                markers=True,
            )
            st.plotly_chart(fig, use_container_width=True)
        st.dataframe(rrw.sort_values(["route_name", "week"]), use_container_width=True, height=300)
        export_dataframe(rrw, filename="route_revenue_weekly", label="Download Weekly Route Revenue")

    # Equipment utilization tabs
    st.markdown("---")
    st.subheader("Equipment & Driver Utilization")
    tabs = st.tabs(["Drivers", "Tractors", "Trailers"])
    for tab, table in zip(tabs, ["equip_util_driver", "equip_util_tractor", "equip_util_trailer"]):
        with tab:
            df = _load(table)
            if df.empty:
                st.info("No data.")
                continue
            sort_keys = [c for c in ["total_stops", "otp_rate", "total_miles"] if c in df.columns]
            sort_col_eq = st.selectbox(
                "Sort by", sort_keys, index=0, key=f"sort_{table}"
            )
            ascending = st.checkbox("Ascending", value=False, key=f"asc_{table}")
            df_sorted = df.sort_values(sort_col_eq, ascending=ascending, na_position="last")
            st.dataframe(df_sorted, use_container_width=True, height=400)
            export_dataframe(df_sorted, filename=table, label=f"Download {table}")
