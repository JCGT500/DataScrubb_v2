"""DataScrubb Dashboard — Streamlit entry point."""

import sys
from pathlib import Path

# Ensure the project root is on sys.path so absolute imports of
# `dashboard.*` and `datascrubb.*` resolve when Streamlit runs this file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st

st.set_page_config(
    page_title="DataScrubb",
    page_icon="🚛",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "Get Help": "https://github.com/JCGT500/datascrubb",
        "About": "**DataScrubb** — transportation analytics for plasma / refrigerated freight. "
                 "See LOGIC.md in the repo for metric formulas.",
    },
)

# Page registry grouped by section. Each entry: (display_label, module_name).
NAV_GROUPS = {
    "📊 Overview & Setup": [
        ("📊 Overview", "overview"),
        ("📥 Load Data", "data_load"),
        ("⚙️ Configuration", "rate_matrix"),
        ("🔧 Admin", "admin"),
    ],
    "🛣️ Operations": [
        ("🛣️ Route KPIs", "route_kpi"),
        ("⏱️ OTP Analysis", "otp_analysis"),
        ("🔍 Stop Explorer", "stop_explorer"),
        ("🗺️ Live Map", "live_map"),
        ("📋 Operations Insights", "operations"),
        ("📅 Multi-Period Compare", "multi_period"),
    ],
    "💰 Financial": [
        ("💰 Cost vs Revenue", "billing_recon"),
    ],
    "🚛 Equipment & People": [
        ("🚛 Trailer Utilization", "trailer_utilization"),
        ("🛡️ Reefer Diagnostics (Vanguard)", "reefer_diagnostics"),
        ("🧑‍✈️ Driver Scorecard", "driver_scorecard"),
    ],
    "🤝 Customers & Quality": [
        ("🤝 Customer Insights", "customer_insights"),
        ("🌡️ Telemetry & Reefer", "telemetry_view"),
        ("🏭 Warehouse Impact", "warehouse_impact"),
        ("📦 Case Variance", "case_variance"),
        ("🔗 SAP Matching", "sap_matching"),
        ("✅ Validation Report", "validation_report"),
    ],
}


def _flatten_pages() -> dict[str, str]:
    """Build a flat {label: module_name} dict for fast lookup."""
    out = {}
    for group, items in NAV_GROUPS.items():
        for label, mod in items:
            out[label] = mod
    return out


PAGES = _flatten_pages()


def main() -> None:
    st.sidebar.title("🚛 DataScrubb")
    st.sidebar.caption("Transportation analytics")
    st.sidebar.markdown("---")

    # Two-step nav: pick group, then page.
    group_labels = list(NAV_GROUPS.keys())
    # Default group = the one that contains the most-used page (Overview).
    if "nav_group" not in st.session_state:
        st.session_state["nav_group"] = group_labels[0]
    group = st.sidebar.selectbox(
        "Section",
        group_labels,
        index=group_labels.index(st.session_state["nav_group"]) if st.session_state["nav_group"] in group_labels else 0,
        key="nav_group",
    )
    page_options = [label for label, _ in NAV_GROUPS[group]]
    page = st.sidebar.radio("Page", page_options, key=f"nav_page_{group}")

    st.sidebar.markdown("---")

    # Dynamic dispatch
    module_name = PAGES.get(page)
    if not module_name:
        st.error(f"Unknown page: {page}")
        return

    import importlib
    mod = importlib.import_module(f"dashboard.pages.{module_name}")
    mod.render()


if __name__ == "__main__":
    main()
