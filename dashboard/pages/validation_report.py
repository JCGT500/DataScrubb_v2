"""Validation Report page — error summary, trends, and drill-down."""

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
from datascrubb.validation.report import build_error_reference
from dashboard.components.export_button import export_dataframe
from dashboard.components.page_filters import validation_filters
from dashboard.components.sidebar import render_global_filters


def _load_data():
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        return pd.DataFrame(), pd.DataFrame()
    engine = get_engine(db_path)

    try:
        errors = pd.read_sql("SELECT * FROM validation_error", engine)
    except Exception:
        errors = pd.DataFrame()

    try:
        runs = pd.read_sql("SELECT * FROM pipeline_run ORDER BY run_timestamp DESC", engine)
    except Exception:
        runs = pd.DataFrame()

    return errors, runs


def render():
    st.header("Validation Report")

    errors_df, runs_df = _load_data()

    if errors_df.empty and runs_df.empty:
        st.info("No pipeline runs yet. Go to **Load Data** to process files.")
        return

    # Shared global filters (limit to errors whose transaction_id passes the filter)
    cfg = load_config()
    engine = get_engine(cfg.db_path)
    try:
        sm = pd.read_sql("SELECT * FROM stop_master", engine)
    except Exception:
        sm = pd.DataFrame()
    keep_txn: set | None = None
    if not sm.empty:
        filtered_stops, _flt = render_global_filters(sm)
        keep_txn = set(filtered_stops["transaction_id"].dropna().tolist())

    reasons = sorted(errors_df["error_reason"].dropna().unique().tolist()) if not errors_df.empty else []
    sources = sorted(errors_df["source"].dropna().unique().tolist()) if not errors_df.empty else []
    page_flt = validation_filters(error_reasons=reasons, sources=sources)

    if keep_txn is not None and not errors_df.empty:
        errors_df = errors_df[errors_df["transaction_id"].isin(keep_txn) | errors_df["transaction_id"].isna()]
    if page_flt["err_type"] != "All" and not errors_df.empty:
        errors_df = errors_df[errors_df["error_type"].str.upper() == page_flt["err_type"].upper()]
    if page_flt["reasons"] and not errors_df.empty:
        errors_df = errors_df[errors_df["error_reason"].isin(page_flt["reasons"])]
    if page_flt["sources"] and not errors_df.empty:
        errors_df = errors_df[errors_df["source"].isin(page_flt["sources"])]

    # Pipeline run history
    if not runs_df.empty:
        st.subheader("Pipeline Run History")
        display_cols = [c for c in [
            "run_id", "run_timestamp", "status", "error_message",
        ] if c in runs_df.columns]
        st.dataframe(runs_df[display_cols], use_container_width=True, height=200)

    if errors_df.empty:
        st.success("No validation errors found across all runs.")
        return

    st.markdown("---")

    # Error summary KPIs
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Errors", len(errors_df))
    col2.metric("Hard", len(errors_df[errors_df["error_type"] == "HARD"]))
    col3.metric("Soft", len(errors_df[errors_df["error_type"] == "SOFT"]))
    col4.metric("Warnings", len(errors_df[errors_df["error_type"] == "WARNING"]))

    st.markdown("---")

    # Error breakdown chart
    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        by_reason = (
            errors_df.groupby("error_reason").size().reset_index(name="count")
            .sort_values("count", ascending=True)
        )
        fig = px.bar(
            by_reason, x="count", y="error_reason", orientation="h",
            title="Errors by Reason",
            labels={"count": "Count", "error_reason": "Error Reason"},
        )
        st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        by_type = (
            errors_df.groupby(["error_type", "source"]).size().reset_index(name="count")
        )
        fig = px.bar(
            by_type, x="error_type", y="count", color="source",
            title="Errors by Type and Source",
            labels={"count": "Count", "error_type": "Type"},
            barmode="group",
        )
        st.plotly_chart(fig, use_container_width=True)

    # Filter by run
    st.subheader("Error Detail")
    if "run_id" in errors_df.columns:
        run_ids = ["All"] + errors_df["run_id"].unique().tolist()
        selected_run = st.selectbox("Filter by Run", options=run_ids)
        if selected_run != "All":
            errors_df = errors_df[errors_df["run_id"] == selected_run]

    # Error detail table
    display_cols = [c for c in [
        "transaction_id", "source", "error_type", "error_reason", "run_id", "created_at",
    ] if c in errors_df.columns]

    st.dataframe(errors_df[display_cols], use_container_width=True, height=400)
    export_dataframe(errors_df, filename="validation_errors", label="Download Errors")

    # Error reference
    st.markdown("---")
    st.subheader("Error Reference")
    st.dataframe(build_error_reference(), use_container_width=True)
