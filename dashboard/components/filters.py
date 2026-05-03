"""Reusable Streamlit filter widgets."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st


def date_range_filter(df: pd.DataFrame, date_col: str = "arrival_date") -> tuple[date, date]:
    """Render a date range picker and return (start, end)."""
    if date_col in df.columns and not df[date_col].isna().all():
        dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
        min_date = dates.min().date()
        max_date = dates.max().date()
    else:
        min_date = date.today() - timedelta(days=30)
        max_date = date.today()

    col1, col2 = st.columns(2)
    with col1:
        start = st.date_input("Start Date", value=min_date, min_value=min_date, max_value=max_date)
    with col2:
        end = st.date_input("End Date", value=max_date, min_value=min_date, max_value=max_date)

    return start, end


def s_code_filter(df: pd.DataFrame) -> list[str]:
    """Render an S-Code multiselect filter."""
    if "s_code" not in df.columns:
        return []
    s_codes = sorted(df["s_code"].dropna().unique().tolist())
    return st.multiselect("S-Code", options=s_codes, default=[])


def stop_type_filter(df: pd.DataFrame) -> str | None:
    """Render a stop type radio filter."""
    options = ["All", "PLASMA_CENTER", "WAREHOUSE"]
    choice = st.radio("Stop Type", options, horizontal=True)
    return None if choice == "All" else choice


def route_filter(df: pd.DataFrame) -> str:
    """Render a text search for route/order number."""
    return st.text_input("Search Order #", value="")


def performance_filter(df: pd.DataFrame) -> list[str]:
    """Render a performance status multiselect."""
    if "stop_performance_status" not in df.columns:
        return []
    statuses = sorted(df["stop_performance_status"].dropna().unique().tolist())
    return st.multiselect("Performance Status", options=statuses, default=[])


def apply_filters(
    df: pd.DataFrame,
    date_range: tuple[date, date] | None = None,
    s_codes: list[str] | None = None,
    stop_type: str | None = None,
    order_search: str = "",
    performance_statuses: list[str] | None = None,
) -> pd.DataFrame:
    """Apply all active filters to a DataFrame."""
    filtered = df.copy()

    if date_range and "arrival_date" in filtered.columns:
        start, end = date_range
        dates = pd.to_datetime(filtered["arrival_date"], errors="coerce")
        filtered = filtered[
            (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
        ]

    if s_codes and "s_code" in filtered.columns:
        filtered = filtered[filtered["s_code"].isin(s_codes)]

    if stop_type and "stop_type" in filtered.columns:
        filtered = filtered[filtered["stop_type"] == stop_type]

    if order_search and "order_number" in filtered.columns:
        filtered = filtered[
            filtered["order_number"].astype(str).str.contains(order_search, case=False, na=False)
        ]

    if performance_statuses and "stop_performance_status" in filtered.columns:
        filtered = filtered[filtered["stop_performance_status"].isin(performance_statuses)]

    return filtered
