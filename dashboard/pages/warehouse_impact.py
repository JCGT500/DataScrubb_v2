"""Warehouse Impact diagnostic — see every KPI computed with vs without warehouses.

This page does NOT change any saved data. It just computes summary stats two
ways from the existing ``stop_master`` table so the user can see exactly how
much each metric shifts when warehouses are included. After reviewing here,
the user goes to **Admin → Warehouse Inclusion** to set persistent toggles.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datascrubb.config import load_config
from datascrubb.db import get_engine


def _load(table: str) -> pd.DataFrame:
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    try:
        return pd.read_sql(f"SELECT * FROM {table}", engine)
    except Exception:
        return pd.DataFrame()


def _safe_mean(s: pd.Series) -> float | None:
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.mean()) if not s.empty else None


def _safe_pct(s: pd.Series) -> float | None:
    """Treat 0/1 column as a rate; return % or None."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(s.mean() * 100) if not s.empty else None


def _delta_metric(label: str, with_val, without_val, fmt: str = "{:.1f}", suffix: str = "", higher_is_better: bool = True) -> None:
    """Render a Streamlit metric with delta colored green/red based on direction."""
    if with_val is None and without_val is None:
        st.metric(label, "—")
        return
    val_str = "—" if without_val is None else f"{fmt.format(without_val)}{suffix}"
    if with_val is None or without_val is None:
        st.metric(label, val_str)
        return
    delta = without_val - with_val
    delta_str = f"{delta:+.1f}{suffix}"
    # Streamlit's metric uses delta_color="normal" (green=+, red=-) by default.
    # Flip when "lower is better" so green = improvement.
    delta_color = "normal" if higher_is_better else "inverse"
    st.metric(label, val_str, delta=delta_str, delta_color=delta_color, help=f"With warehouses: {fmt.format(with_val)}{suffix}")


def render():
    st.header("Warehouse Impact")
    st.caption(
        "Every metric computed two ways: **with warehouses** (all stops) vs "
        "**plasma centers only** (S-Code stops). The Δ tells you how much each "
        "metric is being skewed today by warehouse / distribution-center / "
        "internal-base stops. Use this to decide which **Admin → Warehouse "
        "Inclusion** toggles to flip."
    )

    sm = _load("stop_master")
    if sm.empty:
        st.info("No stop_master data yet — run the pipeline first.")
        return

    # Decide which column drives the partition
    if "stop_class" in sm.columns:
        plasma_mask = sm["stop_class"] == "PLASMA_CENTER"
    elif "stop_type" in sm.columns:
        plasma_mask = sm["stop_type"] == "PLASMA_CENTER"
    else:
        st.error("stop_master is missing both `stop_class` and `stop_type` — cannot partition.")
        return

    plasma = sm[plasma_mask].copy()
    full = sm.copy()

    # ─── Class breakdown ───
    st.subheader("Class breakdown")
    class_col = "stop_class" if "stop_class" in sm.columns else "stop_type"
    counts = sm[class_col].value_counts(dropna=False)

    cc1, cc2, cc3, cc4, cc5 = st.columns(5)
    cc1.metric("Total stops", f"{len(sm):,}")
    cc2.metric(
        "Plasma centers",
        f"{counts.get('PLASMA_CENTER', 0):,}",
        delta=f"{counts.get('PLASMA_CENTER', 0)/len(sm)*100:.0f}%",
    )
    cc3.metric(
        "Distribution",
        f"{counts.get('DISTRIBUTION_CENTER', 0):,}",
        delta=f"{counts.get('DISTRIBUTION_CENTER', 0)/len(sm)*100:.0f}%",
    )
    cc4.metric(
        "Internal base",
        f"{counts.get('INTERNAL_BASE', 0):,}",
        delta=f"{counts.get('INTERNAL_BASE', 0)/len(sm)*100:.0f}%",
    )
    cc5.metric(
        "Other",
        f"{counts.get('OTHER', 0):,}",
        delta=f"{counts.get('OTHER', 0)/len(sm)*100:.0f}%",
    )

    if class_col == "stop_class":
        cls_df = counts.reset_index()
        cls_df.columns = ["class", "stops"]
        fig = px.pie(cls_df, names="class", values="stops", title="Stop class distribution", hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("`stop_class` column not present yet — only legacy PLASMA / WAREHOUSE binary available. Re-run the pipeline to get the finer 4-category split.")

    st.markdown("---")

    # ─── Side-by-side KPI cards ───
    st.subheader("Metric comparison: with warehouses vs plasma-only")

    # Compute key metrics both ways
    # Higher-is-better: OTP rate, margin %, on-time count
    # Lower-is-better: avg dwell, avg cases-variance magnitude, late count

    rows = []
    rows.append({
        "label": "OTP rate (time)",
        "with": _safe_pct(full.get("otp_time_pass", pd.Series())),
        "without": _safe_pct(plasma.get("otp_time_pass", pd.Series())),
        "fmt": "{:.1f}", "suffix": "%", "higher_is_better": True,
    })
    rows.append({
        "label": "OTP rate (day)",
        "with": _safe_pct(full.get("otp_day_pass", pd.Series())),
        "without": _safe_pct(plasma.get("otp_day_pass", pd.Series())),
        "fmt": "{:.1f}", "suffix": "%", "higher_is_better": True,
    })
    rows.append({
        "label": "Avg dwell (min)",
        "with": _safe_mean(full.get("dwell_minutes", pd.Series())),
        "without": _safe_mean(plasma.get("dwell_minutes", pd.Series())),
        "fmt": "{:.0f}", "suffix": " min", "higher_is_better": False,
    })
    rows.append({
        "label": "Late stops",
        "with": int((full.get("stop_performance_status") == "Late").sum()) if "stop_performance_status" in full.columns else None,
        "without": int((plasma.get("stop_performance_status") == "Late").sum()) if "stop_performance_status" in plasma.columns else None,
        "fmt": "{:.0f}", "suffix": "", "higher_is_better": False,
    })
    rows.append({
        "label": "Stops with errors",
        "with": int((full.get("error_flag") == "Y").sum()) if "error_flag" in full.columns else None,
        "without": int((plasma.get("error_flag") == "Y").sum()) if "error_flag" in plasma.columns else None,
        "fmt": "{:.0f}", "suffix": "", "higher_is_better": False,
    })
    if "cases_variance" in full.columns:
        full_short = pd.to_numeric(full["cases_variance"], errors="coerce").fillna(0)
        plasma_short = pd.to_numeric(plasma["cases_variance"], errors="coerce").fillna(0)
        rows.append({
            "label": "Total cases short",
            "with": float(full_short[full_short < 0].abs().sum()),
            "without": float(plasma_short[plasma_short < 0].abs().sum()),
            "fmt": "{:.0f}", "suffix": "", "higher_is_better": False,
        })
    if "telem_events" in full.columns and "min_s1" in full.columns:
        full_excursion = ((full["telem_events"].fillna(0) > 0)
                          & ((pd.to_numeric(full["min_s1"], errors="coerce") < -30)
                             | (pd.to_numeric(full.get("max_s1"), errors="coerce") > -20))).sum()
        plasma_excursion = ((plasma["telem_events"].fillna(0) > 0)
                            & ((pd.to_numeric(plasma["min_s1"], errors="coerce") < -30)
                               | (pd.to_numeric(plasma.get("max_s1"), errors="coerce") > -20))).sum()
        rows.append({
            "label": "Excursion stops (min_s1 OOR)",
            "with": int(full_excursion),
            "without": int(plasma_excursion),
            "fmt": "{:.0f}", "suffix": "", "higher_is_better": False,
        })
    rows.append({
        "label": "Distinct customers",
        "with": int(full["customer"].nunique()) if "customer" in full.columns else None,
        "without": int(plasma["customer"].nunique()) if "customer" in plasma.columns else None,
        "fmt": "{:.0f}", "suffix": "", "higher_is_better": True,
    })
    rows.append({
        "label": "Distinct routes",
        "with": int(full["order_number"].nunique()) if "order_number" in full.columns else None,
        "without": int(plasma["order_number"].nunique()) if "order_number" in plasma.columns else None,
        "fmt": "{:.0f}", "suffix": "", "higher_is_better": True,
    })

    # Render in a 4-wide grid
    for i in range(0, len(rows), 4):
        cols = st.columns(4)
        for j, r in enumerate(rows[i:i+4]):
            with cols[j]:
                _delta_metric(
                    r["label"], r["with"], r["without"],
                    fmt=r["fmt"], suffix=r["suffix"],
                    higher_is_better=r["higher_is_better"],
                )

    st.markdown("---")

    # ─── Per-customer drill-down ───
    st.subheader("Per-customer drill-down — which customers shift the most?")
    st.caption("Each row shows the customer's metrics with vs without warehouses. Sorted by the largest absolute change.")
    if "customer" in sm.columns:
        cust_full = (
            full.groupby("customer", dropna=False)
            .agg(
                stops_with=("transaction_id", "count"),
                otp_with=("otp_time_pass", lambda s: float(pd.to_numeric(s, errors="coerce").mean() * 100) if pd.to_numeric(s, errors="coerce").notna().any() else None),
                dwell_with=("dwell_minutes", lambda s: float(pd.to_numeric(s, errors="coerce").mean()) if pd.to_numeric(s, errors="coerce").notna().any() else None),
            )
            .reset_index()
        )
        cust_plasma = (
            plasma.groupby("customer", dropna=False)
            .agg(
                stops_without=("transaction_id", "count"),
                otp_without=("otp_time_pass", lambda s: float(pd.to_numeric(s, errors="coerce").mean() * 100) if pd.to_numeric(s, errors="coerce").notna().any() else None),
                dwell_without=("dwell_minutes", lambda s: float(pd.to_numeric(s, errors="coerce").mean()) if pd.to_numeric(s, errors="coerce").notna().any() else None),
            )
            .reset_index()
        )
        merged = cust_full.merge(cust_plasma, on="customer", how="left")
        # Δ columns
        merged["stops_diff"] = merged["stops_with"] - merged["stops_without"].fillna(0)
        merged["otp_diff_pp"] = (merged["otp_without"] - merged["otp_with"]).round(1)
        merged["dwell_diff_min"] = (merged["dwell_without"] - merged["dwell_with"]).round(0)
        for c in ("otp_with", "otp_without"):
            merged[c] = merged[c].round(1)
        for c in ("dwell_with", "dwell_without"):
            merged[c] = merged[c].round(0)
        # Sort by biggest stops drop (i.e. customers most "made of" warehouse stops)
        merged = merged.sort_values("stops_diff", ascending=False)
        st.dataframe(merged, use_container_width=True, height=500)

    st.markdown("---")

    # ─── Per-class drill-down ───
    if class_col == "stop_class":
        st.subheader("Per-class detail")
        per_class = (
            sm.groupby("stop_class", dropna=False)
            .agg(
                stops=("transaction_id", "count"),
                distinct_customers=("customer", "nunique"),
                distinct_routes=("order_number", "nunique"),
                otp=("otp_time_pass", lambda s: float(pd.to_numeric(s, errors="coerce").mean() * 100) if pd.to_numeric(s, errors="coerce").notna().any() else None),
                avg_dwell=("dwell_minutes", lambda s: float(pd.to_numeric(s, errors="coerce").mean()) if pd.to_numeric(s, errors="coerce").notna().any() else None),
            )
            .reset_index()
        )
        per_class["otp"] = per_class["otp"].round(1)
        per_class["avg_dwell"] = per_class["avg_dwell"].round(0)
        st.dataframe(per_class, use_container_width=True)

        # Show the top customers in each non-plasma class — useful audit
        for cls in ("DISTRIBUTION_CENTER", "INTERNAL_BASE", "OTHER"):
            sub = sm[sm["stop_class"] == cls]
            if sub.empty:
                continue
            with st.expander(f"Top customers in {cls}"):
                top = (
                    sub.groupby("customer")
                    .size()
                    .reset_index(name="stops")
                    .sort_values("stops", ascending=False)
                    .head(15)
                )
                st.dataframe(top, use_container_width=True)
