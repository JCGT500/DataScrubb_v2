"""Shared sidebar filter system.

Filter selections live in ``st.session_state`` and are keyed off the widget
``key=`` parameter so they persist across pages. Pages call
``render_global_filters(df)`` once at the top of ``render()``; they get back a
filtered DataFrame and the live ``GlobalFilters`` snapshot.

NOTE: Streamlit raises ``StreamlitAPIException`` if a widget is created with
both ``value=`` and a ``key=`` whose entry already exists in
``st.session_state``. We sidestep that by using ONLY ``key=`` and seeding
defaults via ``st.session_state.setdefault(...)`` BEFORE widget creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
import streamlit as st


@dataclass
class GlobalFilters:
    """Snapshot of the currently-applied global filters."""

    date_start: date | None = None
    date_end: date | None = None
    customers: list[str] = field(default_factory=list)
    s_codes: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    lanes: list[str] = field(default_factory=list)
    stop_type: str | None = None
    performance_statuses: list[str] = field(default_factory=list)
    order_search: str = ""

    def has_date_range(self) -> bool:
        return self.date_start is not None and self.date_end is not None


def _options_from(df: pd.DataFrame, col: str) -> list[str]:
    if df is None or df.empty or col not in df.columns:
        return []
    vals = df[col].dropna().astype(str).str.strip()
    vals = vals[vals != ""]
    return sorted(vals.unique().tolist())


def _date_bounds(df: pd.DataFrame, col: str = "arrival_date") -> tuple[date, date]:
    if df is None or df.empty or col not in df.columns or df[col].isna().all():
        today = date.today()
        return today - timedelta(days=30), today
    dates = pd.to_datetime(df[col], errors="coerce").dropna()
    if dates.empty:
        today = date.today()
        return today - timedelta(days=30), today
    return dates.min().date(), dates.max().date()


# Widget-state keys (single source of truth — Streamlit reads/writes these directly)
_K_DATE_START = "gf_date_start"
_K_DATE_END = "gf_date_end"
_K_CUSTOMERS = "gf_customers"
_K_S_CODES = "gf_s_codes"
_K_ROUTES = "gf_routes"
_K_LANES = "gf_lanes"
_K_STOP_TYPE = "gf_stop_type"
_K_PERF = "gf_perf"
_K_ORDER_SEARCH = "gf_order_search"

_RESET_KEYS = [
    _K_DATE_START, _K_DATE_END, _K_CUSTOMERS, _K_S_CODES, _K_ROUTES,
    _K_LANES, _K_STOP_TYPE, _K_PERF, _K_ORDER_SEARCH,
]


def render_global_filters(
    df: pd.DataFrame,
    *,
    show_stop_type: bool = True,
    show_performance: bool = True,
    show_lane: bool = True,
    extra_caption: str | None = None,
) -> tuple[pd.DataFrame, GlobalFilters]:
    """Render the shared sidebar and return (filtered_stops, GlobalFilters)."""

    min_d, max_d = _date_bounds(df)

    # Seed defaults BEFORE any widget that uses these keys
    ss = st.session_state
    ss.setdefault(_K_DATE_START, min_d)
    ss.setdefault(_K_DATE_END, max_d)
    ss.setdefault(_K_CUSTOMERS, [])
    ss.setdefault(_K_S_CODES, [])
    ss.setdefault(_K_ROUTES, [])
    ss.setdefault(_K_LANES, [])
    ss.setdefault(_K_STOP_TYPE, "All")
    ss.setdefault(_K_PERF, [])
    ss.setdefault(_K_ORDER_SEARCH, "")

    # Clamp persisted dates to the current data window — but only if a widget
    # for these keys does NOT yet exist in this rerun. (We modify session_state
    # before the widget is rendered, which Streamlit allows.)
    try:
        ss[_K_DATE_START] = max(min_d, min(ss[_K_DATE_START], max_d))
        ss[_K_DATE_END] = max(min_d, min(ss[_K_DATE_END], max_d))
    except Exception:
        ss[_K_DATE_START] = min_d
        ss[_K_DATE_END] = max_d

    customer_opts = _options_from(df, "customer")
    s_code_opts = _options_from(df, "s_code")
    route_opts = _options_from(df, "route_name")
    perf_opts = _options_from(df, "stop_performance_status")

    lane_opts: list[str] = []
    if show_lane:
        try:
            from datascrubb.config import load_config
            from datascrubb.db import get_engine

            engine = get_engine(load_config().db_path)
            lanes_df = pd.read_sql("SELECT DISTINCT lane FROM billing_snapshot", engine)
            lane_opts = sorted([x for x in lanes_df["lane"].dropna().tolist() if x])
        except Exception:
            lane_opts = []

    # Drop any persisted selections that no longer exist (e.g. after data swap)
    ss[_K_CUSTOMERS] = [c for c in ss[_K_CUSTOMERS] if c in customer_opts]
    ss[_K_S_CODES] = [s for s in ss[_K_S_CODES] if s in s_code_opts]
    ss[_K_ROUTES] = [r for r in ss[_K_ROUTES] if r in route_opts]
    ss[_K_LANES] = [l for l in ss[_K_LANES] if l in lane_opts]
    ss[_K_PERF] = [p for p in ss[_K_PERF] if p in perf_opts]

    with st.sidebar:
        st.markdown("### Filters")
        if extra_caption:
            st.caption(extra_caption)

        c1, c2 = st.columns(2)
        c1.date_input("Start", min_value=min_d, max_value=max_d, key=_K_DATE_START)
        c2.date_input("End", min_value=min_d, max_value=max_d, key=_K_DATE_END)

        if customer_opts:
            st.multiselect("Customer", options=customer_opts, key=_K_CUSTOMERS)
        if route_opts:
            st.multiselect("Route Name", options=route_opts, key=_K_ROUTES)
        if s_code_opts:
            st.multiselect("S-Code", options=s_code_opts, key=_K_S_CODES)
        if show_lane and lane_opts:
            st.multiselect("Lane", options=lane_opts, key=_K_LANES)
        if show_stop_type and "stop_type" in df.columns:
            st.radio(
                "Stop Type",
                options=["All", "PLASMA_CENTER", "WAREHOUSE"],
                horizontal=True, key=_K_STOP_TYPE,
            )
        if show_performance and perf_opts:
            st.multiselect("Performance", options=perf_opts, key=_K_PERF)
        st.text_input("Search Order#", key=_K_ORDER_SEARCH)

        if st.button("Reset filters", key="gf_reset_btn"):
            for k in _RESET_KEYS:
                ss.pop(k, None)
            st.rerun()

    flt = GlobalFilters(
        date_start=ss[_K_DATE_START],
        date_end=ss[_K_DATE_END],
        customers=ss[_K_CUSTOMERS],
        s_codes=ss[_K_S_CODES],
        routes=ss[_K_ROUTES],
        lanes=ss[_K_LANES],
        stop_type=None if ss[_K_STOP_TYPE] == "All" else ss[_K_STOP_TYPE],
        performance_statuses=ss[_K_PERF],
        order_search=ss[_K_ORDER_SEARCH],
    )
    return apply_to_stops(df, flt), flt


def apply_to_stops(df: pd.DataFrame, flt: GlobalFilters) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    out = df.copy()
    if flt.has_date_range() and "arrival_date" in out.columns:
        d = pd.to_datetime(out["arrival_date"], errors="coerce")
        out = out[(d >= pd.Timestamp(flt.date_start)) & (d <= pd.Timestamp(flt.date_end))]
    if flt.customers and "customer" in out.columns:
        out = out[out["customer"].isin(flt.customers)]
    if flt.s_codes and "s_code" in out.columns:
        out = out[out["s_code"].isin(flt.s_codes)]
    if flt.routes and "route_name" in out.columns:
        out = out[out["route_name"].isin(flt.routes)]
    if flt.stop_type and "stop_type" in out.columns:
        out = out[out["stop_type"] == flt.stop_type]
    if flt.performance_statuses and "stop_performance_status" in out.columns:
        out = out[out["stop_performance_status"].isin(flt.performance_statuses)]
    if flt.order_search and "order_number" in out.columns:
        out = out[out["order_number"].astype(str).str.contains(flt.order_search, case=False, na=False)]
    return out


def apply_to_routes(routes_df: pd.DataFrame, flt: GlobalFilters) -> pd.DataFrame:
    if routes_df is None or routes_df.empty:
        return routes_df
    out = routes_df.copy()
    if flt.routes and "route_name" in out.columns:
        out = out[out["route_name"].isin(flt.routes)]
    if flt.order_search and "route_id" in out.columns:
        out = out[out["route_id"].astype(str).str.contains(flt.order_search, case=False, na=False)]
    return out


def apply_to_billing(billing_df: pd.DataFrame, flt: GlobalFilters) -> pd.DataFrame:
    if billing_df is None or billing_df.empty:
        return billing_df
    out = billing_df.copy()
    if flt.lanes and "lane" in out.columns:
        out = out[out["lane"].isin(flt.lanes)]
    if flt.has_date_range() and "billing_week_end" in out.columns:
        d = pd.to_datetime(out["billing_week_end"], errors="coerce")
        out = out[(d >= pd.Timestamp(flt.date_start)) & (d <= pd.Timestamp(flt.date_end))]
    if flt.order_search and "pro_number" in out.columns:
        out = out[out["pro_number"].astype(str).str.contains(flt.order_search, case=False, na=False)]
    return out
