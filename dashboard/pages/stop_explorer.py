"""Stop Explorer — sortable, filterable STOP_MASTER table."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine
from dashboard.components.export_button import export_dataframe
from dashboard.components.sidebar import render_global_filters


def _load_data():
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    return pd.read_sql("SELECT * FROM stop_master", engine)


def render():
    st.header("Stop Explorer")

    df_all = _load_data()
    if df_all.empty:
        st.info("No data loaded yet. Go to **Load Data** to upload and process files.")
        return

    filtered, flt = render_global_filters(df_all)

    # Sort control
    sort_options = {
        "Customer (A-Z)": ("customer", True),
        "Customer (Z-A)": ("customer", False),
        "Arrival (newest)": ("actual_arrival", False),
        "Arrival (oldest)": ("actual_arrival", True),
        "OTP minutes (most late)": ("minutes_from_appt", False),
        "OTP minutes (most early)": ("minutes_from_appt", True),
        "Dwell (longest)": ("dwell_minutes", False),
        "Route #": ("order_number", True),
    }
    sort_label = st.selectbox("Sort by", list(sort_options.keys()), index=0)
    sort_col, ascending = sort_options[sort_label]
    if sort_col in filtered.columns:
        filtered = filtered.sort_values(sort_col, ascending=ascending, na_position="last")

    st.markdown(f"**{len(filtered):,}** stops (of {len(df_all):,} total)")

    display_cols = [
        "transaction_id", "order_number", "route_name", "customer", "s_code", "stop_type", "stop_class",
        "stop_direction", "loaded_at_stop", "tender_cases", "current_cases",
        "fill_pct_cases", "fill_pct_weight", "capacity_source",
        "city", "state",
        "arrival_date", "actual_arrival", "actual_departure", "dwell_minutes",
        "resolved_appt", "minutes_from_appt", "stop_performance_status",
        "otp_time_pass", "otp_day_pass", "late_code",
        "trailer", "truck", "drivers",
        "telem_events", "min_amb_temp", "max_amb_temp", "min_s1", "max_s1", "door_open_events",
        "error_flag", "error_reason",
    ]
    display_cols = [c for c in display_cols if c in filtered.columns]
    st.dataframe(filtered[display_cols], use_container_width=True, height=600)

    st.markdown("---")
    export_dataframe(filtered, filename="stop_master_filtered", label="Download Filtered Data")
