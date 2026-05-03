"""Multi-Period Compare — snapshot the current run and compare against past snapshots."""

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.snapshots import (
    delete_snapshot,
    list_snapshots,
    load_snapshot_table,
    snapshot,
)


def _safe_metric(label: str, value, fmt: str = "{}") -> None:
    if value is None or pd.isna(value):
        st.metric(label, "—")
    else:
        st.metric(label, fmt.format(value))


def render():
    st.header("Multi-Period Compare")
    st.caption(
        "Take a labeled snapshot of the current pipeline run, then compare KPIs "
        "across multiple periods (e.g. Jan vs Feb vs Mar) without re-ingesting raw data."
    )

    # ─── Snapshot management ───
    st.subheader("Snapshots")
    snaps = list_snapshots()

    sc1, sc2 = st.columns([2, 3])
    with sc1:
        new_label = st.text_input(
            "New snapshot label",
            placeholder="e.g. 2026_01 or jan_2026",
            help="Use a stable label like YYYY_MM. Re-using a label overwrites the old snapshot.",
        )
        if st.button("Save current run as snapshot", type="primary", disabled=not new_label.strip()):
            try:
                result = snapshot(new_label)
                st.success(
                    f"Snapshot **{result['label']}** saved — "
                    f"{len(result['row_counts'])} tables, "
                    f"{sum(result['row_counts'].values()):,} total rows."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Snapshot failed: {e}")

    with sc2:
        if snaps.empty:
            st.info("No snapshots yet. Save one above first.")
        else:
            st.dataframe(snaps, use_container_width=True, height=180)
            del_label = st.selectbox("Delete snapshot", [""] + snaps["label"].tolist(), key="snap_del")
            if del_label and st.button("Delete", key="snap_del_btn"):
                n = delete_snapshot(del_label)
                st.success(f"Dropped {n} archive table(s) for '{del_label}'.")
                st.rerun()

    if snaps.empty or len(snaps) < 1:
        st.info("Save at least one snapshot to enable comparisons.")
        return

    st.markdown("---")

    # ─── Compare picker ───
    st.subheader("Compare snapshots")
    options = snaps["label"].tolist()
    sel = st.multiselect(
        "Pick 2 or more snapshots to compare",
        options=options,
        default=options[:2] if len(options) >= 2 else options,
    )
    if len(sel) < 2:
        st.warning("Pick at least two snapshots.")
        return

    # ─── Headline KPI table ───
    rows = []
    for label in sel:
        stops = load_snapshot_table(label, "stop_master")
        rev = load_snapshot_table(label, "route_revenue")
        risk = load_snapshot_table(label, "claims_risk")
        det = load_snapshot_table(label, "detention_audit")

        otp = stops["otp_time_pass"].mean() * 100 if (
            not stops.empty and "otp_time_pass" in stops.columns
            and stops["otp_time_pass"].notna().any()
        ) else None
        rows.append({
            "snapshot": label,
            "stops": len(stops),
            "routes": stops["order_number"].nunique() if not stops.empty and "order_number" in stops.columns else 0,
            "customers": stops["customer"].nunique() if not stops.empty and "customer" in stops.columns else 0,
            "otp_pct": round(otp, 1) if otp is not None else None,
            "revenue": rev["revenue"].sum() if not rev.empty else 0,
            "cost": rev["cost"].sum() if not rev.empty else 0,
            "margin": rev["margin"].sum() if not rev.empty else 0,
            "high_risk_routes": int((risk["risk_band"] == "HIGH").sum()) if not risk.empty else 0,
            "billable_detention_hrs": det["billable_hours"].sum() if not det.empty else 0,
        })
    df = pd.DataFrame(rows)
    df["margin_pct"] = (df["margin"] / df["revenue"].replace(0, pd.NA) * 100).round(1)

    st.markdown("**Headline KPIs**")
    st.dataframe(df, use_container_width=True, height=200)

    # ─── Bar charts (one metric at a time) ───
    metric_cols = ["stops", "routes", "customers", "otp_pct", "revenue", "cost", "margin", "margin_pct",
                   "high_risk_routes", "billable_detention_hrs"]
    pick_metric = st.selectbox("Chart this metric", metric_cols, index=metric_cols.index("margin"))
    fig = px.bar(
        df, x="snapshot", y=pick_metric,
        title=f"{pick_metric} across snapshots",
        labels={"snapshot": "Snapshot", pick_metric: pick_metric},
        text=pick_metric,
    )
    fig.update_traces(textposition="outside")
    st.plotly_chart(fig, use_container_width=True)

    # ─── Customer drill-down ───
    st.markdown("---")
    st.subheader("Customer comparison")
    cust_frames = []
    for label in sel:
        cs = load_snapshot_table(label, "customer_scorecard")
        if cs.empty:
            continue
        cs = cs[["customer"] + [c for c in ["stops", "otp_rate", "revenue", "margin", "margin_pct"] if c in cs.columns]].copy()
        cs.insert(0, "snapshot", label)
        cust_frames.append(cs)

    if not cust_frames:
        st.info("No customer scorecard data in any selected snapshot.")
        return

    custs = pd.concat(cust_frames, ignore_index=True)
    customers = sorted(custs["customer"].unique())
    pick_customers = st.multiselect(
        "Show customers", customers,
        default=customers[:5] if len(customers) > 5 else customers,
        key="multi_period_customers",
    )

    view = custs[custs["customer"].isin(pick_customers)] if pick_customers else custs
    if "margin" in view.columns:
        fig = px.bar(
            view, x="snapshot", y="margin", color="customer",
            barmode="group",
            title="Margin per customer × snapshot",
            labels={"margin": "Margin $"},
        )
        st.plotly_chart(fig, use_container_width=True)
    if "stops" in view.columns:
        fig = px.bar(
            view, x="snapshot", y="stops", color="customer",
            barmode="group",
            title="Stops per customer × snapshot",
            labels={"stops": "Stops"},
        )
        st.plotly_chart(fig, use_container_width=True)

    st.dataframe(view.sort_values(["customer", "snapshot"]), use_container_width=True, height=400)
