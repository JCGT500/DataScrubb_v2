"""SAP Matching page — match status, unmatched rows, diagnostics."""

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
from dashboard.components.page_filters import sap_matching_filters
from dashboard.components.sidebar import render_global_filters


def _load_data():
    config = load_config()
    db_path = config.db_path
    if not db_path.exists():
        return pd.DataFrame(), pd.DataFrame()
    engine = get_engine(db_path)
    try:
        sap = pd.read_sql("SELECT * FROM sap_segment", engine)
    except Exception:
        sap = pd.DataFrame()
    try:
        stops = pd.read_sql("SELECT transaction_id, s_code, order_number FROM stop_master", engine)
    except Exception:
        stops = pd.DataFrame()
    return sap, stops


def render():
    st.header("SAP Matching")

    sap_df, stops_df = _load_data()

    if sap_df.empty:
        st.info("No SAP data loaded yet. Upload SAP data via the **Load Data** page.")
        return

    # Shared global filters → restrict by transaction_id intersection
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
    page_flt = sap_matching_filters()
    if keep_txn is not None and "transaction_id" in sap_df.columns:
        sap_df = sap_df[sap_df["transaction_id"].isin(keep_txn) | sap_df["transaction_id"].isna()]
    if page_flt["max_hours"] < 72 and "time_diff_hours" in sap_df.columns:
        sap_df = sap_df[sap_df["time_diff_hours"].fillna(999) <= page_flt["max_hours"]]
    if page_flt["search"]:
        s = page_flt["search"].lower()
        mask = pd.Series(False, index=sap_df.index)
        for col in ("shipper_name", "consignee_name"):
            if col in sap_df.columns:
                mask |= sap_df[col].astype(str).str.lower().str.contains(s, na=False)
        sap_df = sap_df[mask]

    # KPIs
    total = len(sap_df)
    matched = (sap_df["sap_match_flag"] == "MATCHED").sum()
    unmatched = total - matched
    match_rate = matched / total * 100 if total > 0 else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total SAP Segments", f"{total:,}")
    col2.metric("Matched", f"{matched:,}")
    col3.metric("Unmatched", f"{unmatched:,}")
    col4.metric("Match Rate", f"{match_rate:.1f}%")

    st.markdown("---")

    # Match status distribution
    tab1, tab2 = st.tabs(["Matched Segments", "Unmatched Segments"])

    with tab1:
        matched_df = sap_df[sap_df["sap_match_flag"] == "MATCHED"]
        if not matched_df.empty:
            # Time difference distribution
            if "time_diff_hours" in matched_df.columns:
                fig = px.histogram(
                    matched_df["time_diff_hours"].dropna(),
                    nbins=30,
                    labels={"value": "Time Difference (hours)", "count": "Segments"},
                    title="Time Difference Distribution (SAP arrive vs CRST arrival)",
                )
                st.plotly_chart(fig, use_container_width=True)

            display_cols = [c for c in [
                "transaction_id", "document_number", "segment_number",
                "shipper_name", "consignee_name", "s_code",
                "time_diff_hours", "sap_match_flag",
            ] if c in matched_df.columns]
            st.dataframe(matched_df[display_cols], use_container_width=True, height=400)
        else:
            st.warning("No matched segments.")

    with tab2:
        unmatched_df = sap_df[sap_df["sap_match_flag"] != "MATCHED"]
        if not unmatched_df.empty:
            display_cols = [c for c in [
                "document_number", "segment_number",
                "shipper_name", "consignee_name", "s_code",
                "arrive", "sap_match_flag",
            ] if c in unmatched_df.columns]
            st.dataframe(unmatched_df[display_cols], use_container_width=True, height=400)
            export_dataframe(unmatched_df, filename="sap_unmatched", label="Download Unmatched")
        else:
            st.success("All SAP segments matched!")
