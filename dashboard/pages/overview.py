"""Overview page — KPI cards, customer/route summaries, daily trends."""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine
from dashboard.components.charts import (
    otp_by_customer,
    otp_trend,
    performance_distribution,
    stops_per_day,
)
from dashboard.components.data import load_table
from dashboard.components.help import kpi_help
from dashboard.components.sidebar import render_global_filters
from dashboard.components.topbar import render_filter_chips, render_run_banner


def _render_welcome_card() -> None:
    """Dismissible orientation card for first-time users."""
    if st.session_state.get("welcomed_v1"):
        return
    with st.container(border=True):
        st.markdown("### 👋 Welcome to DataScrubb")
        st.markdown(
            "Transportation analytics for plasma / refrigerated freight. "
            "Pick the question you're trying to answer:"
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(
            "**Are we on time?**\n\n"
            "→ ⏱️ OTP Analysis\n\n"
            "→ 🛣️ Route KPIs"
        )
        c2.markdown(
            "**Are we making money?**\n\n"
            "→ 💰 Cost vs Revenue\n\n"
            "→ ⚙️ Configuration (rates)"
        )
        c3.markdown(
            "**Is equipment working hard?**\n\n"
            "→ 🚛 Trailer Utilization\n\n"
            "→ 🧑‍✈️ Driver Scorecard"
        )
        c4.markdown(
            "**Are customers happy?**\n\n"
            "→ 🤝 Customer Insights\n\n"
            "→ 📦 Case Variance"
        )
        st.markdown(
            "**No data yet?** Go to **📥 Load Data** to upload CRST / SAP / "
            "Telemetry / M3PL files. **Tuning the math?** See **🔧 Admin**."
        )
        col_a, col_b = st.columns([1, 5])
        with col_a:
            if st.button("Got it — don't show again", key="welcome_dismiss"):
                st.session_state["welcomed_v1"] = True
                st.rerun()


def render():
    st.header("📊 Overview")

    df_all = load_table("stop_master")
    if df_all.empty:
        _render_welcome_card()
        st.info("No data loaded yet. Go to **📥 Load Data** to upload and process files.", icon="ℹ️")
        return

    # Pipeline-run banner
    render_run_banner()

    # First-time orientation
    _render_welcome_card()

    df, flt = render_global_filters(df_all)

    # Active filter chips
    render_filter_chips(flt)

    if df.empty:
        st.warning("No stops match the current filters.")
        return

    # First row: stop / OTP / errors / routes
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Stops", f"{len(df):,}", help="Stops surviving current filters.")
    otp_rate = df["otp_time_pass"].mean() * 100 if df["otp_time_pass"].notna().any() else 0
    c2.metric("OTP (Time Window)", f"{otp_rate:.1f}%", help=kpi_help("otp_time"))
    c3.metric("Stops with Errors", f"{int((df['error_flag'] == 'Y').sum()):,}",
              help="Stops flagged by validation rules (missing arrival, duplicate ID, etc.).")
    c4.metric("Distinct Routes", f"{df['order_number'].nunique():,}",
              help="Unique order_# values in the filtered set.")

    # Second row: customers / dwell / billing
    rcol1, rcol2, rcol3, rcol4 = st.columns(4)
    rcol1.metric(
        "Distinct Customers",
        f"{df['customer'].nunique():,}" if "customer" in df.columns else "—",
        help="Unique customers extracted from CRST location_date.",
    )
    avg_dwell = df["dwell_minutes"].dropna().mean() if "dwell_minutes" in df.columns else None
    rcol2.metric("Avg Dwell (min)", f"{avg_dwell:.0f}" if avg_dwell else "—",
                 help="actual_departure − actual_arrival, averaged. Warehouse stops can skew this.")

    cfg = load_config()
    engine = get_engine(cfg.db_path)
    try:
        billed = pd.read_sql("SELECT SUM(billed_amount) as t FROM billing_snapshot", engine).iloc[0]["t"]
        rcol3.metric("Total Billed (M3PL)", f"${billed:,.0f}" if billed else "$0",
                     help=kpi_help("total_cost"))
    except Exception:
        rcol3.metric("Total Billed (M3PL)", "—")
    try:
        sap_count = pd.read_sql("SELECT COUNT(*) as cnt FROM sap_segment", engine).iloc[0]["cnt"]
        sap_matched = pd.read_sql(
            "SELECT COUNT(*) as cnt FROM sap_segment WHERE sap_match_flag = 'MATCHED'", engine
        ).iloc[0]["cnt"]
        sap_rate = (sap_matched / sap_count * 100) if sap_count > 0 else 0
        rcol4.metric("SAP Match Rate", f"{sap_rate:.1f}%",
                     help="% of SAP segments paired to a CRST stop within the configured time window.")
    except Exception:
        rcol4.metric("SAP Match Rate", "—")

    st.markdown("---")

    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.plotly_chart(performance_distribution(df), use_container_width=True)
    with chart_col2:
        st.plotly_chart(stops_per_day(df), use_container_width=True)

    st.plotly_chart(otp_trend(df), use_container_width=True)

    st.subheader("OTP by Customer")
    st.plotly_chart(otp_by_customer(df), use_container_width=True)

    # Executive PDF download
    st.markdown("---")
    st.subheader("Executive Report")
    st.caption(
        "Generate a one-shot PDF summary suitable for emailing to leadership: "
        "headline KPIs, top customers by margin, churn-risk customers, "
        "highest claims-risk routes, detention exposure, and lane profitability."
    )
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("Generate Executive PDF", type="primary"):
            try:
                from datascrubb.export.pdf import generate_executive_pdf
                pdf_path = generate_executive_pdf()
                st.session_state["last_exec_pdf"] = str(pdf_path)
                st.success(f"PDF written to: `{pdf_path}`")
            except Exception as e:
                st.error(f"PDF generation failed: {e}")
                st.exception(e)
    with col2:
        last = st.session_state.get("last_exec_pdf")
        if last and Path(last).exists():
            with open(last, "rb") as f:
                st.download_button(
                    "Download last PDF",
                    data=f.read(),
                    file_name=Path(last).name,
                    mime="application/pdf",
                )
