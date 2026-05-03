"""Customer Insights — service scorecard, churn signal, concentration risk, weekly trend."""

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
from dashboard.components.page_filters import customer_filters
from dashboard.components.sidebar import render_global_filters


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
    st.header("Customer Insights")
    st.caption(
        "Service scorecard, churn risk, revenue concentration, and weekly volume "
        "trend — what your account team needs before every customer call."
    )

    score = _load("customer_scorecard")
    churn = _load("customer_churn")
    conc = _load("customer_concentration")

    if score.empty:
        st.info("No customer data yet. Run the pipeline from **Load Data**.")
        return

    # Shared global filters → restrict customer set
    stops_master = _load("stop_master")
    keep_customers: set | None = None
    if not stops_master.empty:
        filtered_stops, _flt = render_global_filters(stops_master)
        if "customer" in filtered_stops.columns:
            keep_customers = set(filtered_stops["customer"].astype(str).str.strip().str.upper().dropna().unique())
    page_flt = customer_filters()

    if keep_customers is not None and "customer" in score.columns:
        score = score[score["customer"].astype(str).str.strip().str.upper().isin(keep_customers)]
        if not churn.empty and "customer" in churn.columns:
            churn = churn[churn["customer"].astype(str).str.strip().str.upper().isin(keep_customers)]
        if not conc.empty and "customer" in conc.columns:
            conc = conc[conc["customer"].astype(str).str.strip().str.upper().isin(keep_customers)]
    if page_flt["churn_bands"] and not churn.empty and "churn_band" in churn.columns:
        churn = churn[churn["churn_band"].isin(page_flt["churn_bands"])]
    if page_flt["margin_band"] != "All" and "margin_pct" in score.columns:
        if page_flt["margin_band"] == "Profitable (>10%)":
            score = score[score["margin_pct"].fillna(-999) > 10]
        elif page_flt["margin_band"] == "Marginal (0-10%)":
            score = score[(score["margin_pct"].fillna(-999) >= 0) & (score["margin_pct"].fillna(-999) <= 10)]
        else:
            score = score[score["margin_pct"].fillna(0) < 0]
    if page_flt["rev_tier"] != "All" and "revenue" in score.columns:
        ranked = score.sort_values("revenue", ascending=False).reset_index(drop=True)
        if page_flt["rev_tier"] == "Top 10":
            score = ranked.head(10)
        elif page_flt["rev_tier"] == "11-50":
            score = ranked.iloc[10:50]
        else:
            score = ranked.iloc[50:]

    # Headline KPIs
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Customers", f"{len(score):,}")
    c2.metric("Avg OTP", f"{score['otp_rate'].mean():.1f}%")
    if "revenue" in score.columns:
        c3.metric("Total Revenue", f"${score['revenue'].fillna(0).sum():,.0f}")
        c4.metric("Total Margin", f"${score['margin'].fillna(0).sum():,.0f}")
    else:
        c3.metric("Total Stops", f"{int(score['stops'].sum()):,}")
        c4.metric("Total Routes", f"{int(score['distinct_routes'].sum()):,}")

    st.markdown("---")

    tab1, tab2, tab3, tab4 = st.tabs(["Scorecard", "Churn signal", "Concentration risk", "Weekly trend"])

    # ───────────────── Scorecard ─────────────────
    with tab1:
        st.subheader("Per-customer service scorecard")
        sort_options = {
            "Revenue (high)": ("revenue", False),
            "Margin (high)": ("margin", False),
            "OTP (low)": ("otp_rate", True),
            "Late rate (high)": ("late_rate_pct", False),
            "Avg dwell (long)": ("avg_dwell_min", False),
            "Short cases (high)": ("short_cases_total", False),
            "Excursion stops (high)": ("excursion_stops", False),
            "Stops (high)": ("stops", False),
            "Customer (A-Z)": ("customer", True),
        }
        sort_label = st.selectbox("Sort by", list(sort_options.keys()), key="sc_sort")
        scol, asc = sort_options[sort_label]
        if scol in score.columns:
            sorted_score = score.sort_values(scol, ascending=asc, na_position="last")
        else:
            sorted_score = score
        st.dataframe(sorted_score, use_container_width=True, height=500)
        export_dataframe(sorted_score, filename="customer_scorecard", label="Download Scorecard")

        # OTP vs revenue scatter (margin-aware)
        if {"otp_rate", "stops"}.issubset(score.columns):
            scatter = score.dropna(subset=["otp_rate"]).copy()
            color_col = "margin" if "margin" in scatter.columns else None
            fig = px.scatter(
                scatter, x="stops", y="otp_rate",
                color=color_col,
                size="stops",
                hover_name="customer",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"] if color_col else None,
                title=("Customer: stop volume × OTP" + (" (color = margin)" if color_col else "")),
                labels={"stops": "Stops", "otp_rate": "OTP %"},
            )
            st.plotly_chart(fig, use_container_width=True)

    # ───────────────── Churn signal ─────────────────
    with tab2:
        st.subheader("Week-over-week PRO# trend (latest week)")
        st.caption(
            "Bands: **CHURN_RISK** ≤ -50% drop · **DECLINING** -20 to -50% · "
            "**STABLE** -20 to +50% · **GROWING** > +50% · **NEW** = no prior week."
        )
        if churn.empty:
            st.info("Not enough weeks in data to compute churn signal.")
        else:
            band_counts = churn["churn_band"].value_counts().to_dict()
            cc1, cc2, cc3, cc4, cc5 = st.columns(5)
            cc1.metric("CHURN_RISK", band_counts.get("CHURN_RISK", 0))
            cc2.metric("DECLINING", band_counts.get("DECLINING", 0))
            cc3.metric("STABLE", band_counts.get("STABLE", 0))
            cc4.metric("GROWING", band_counts.get("GROWING", 0))
            cc5.metric("NEW", band_counts.get("NEW", 0))

            risky = churn[churn["churn_band"].isin(["CHURN_RISK", "DECLINING"])]
            if not risky.empty:
                fig = px.bar(
                    risky.sort_values("delta_pct"),
                    x="delta_pct", y="customer", orientation="h",
                    color="churn_band",
                    color_discrete_map={"CHURN_RISK": "#ef4444", "DECLINING": "#f59e0b"},
                    title="Customers trending down (latest week vs prior)",
                    labels={"delta_pct": "Δ PRO# %"},
                    hover_data=["pros", "prev_pros", "delta_pros", "week"],
                )
                fig.update_layout(height=max(300, len(risky) * 22))
                st.plotly_chart(fig, use_container_width=True)

            st.dataframe(churn, use_container_width=True, height=400)
            export_dataframe(churn, filename="customer_churn", label="Download Churn Signal")

    # ───────────────── Concentration risk ─────────────────
    with tab3:
        st.subheader("Revenue concentration (Pareto)")
        st.caption("How dependent are you on your top customers? 80% line is the warning threshold.")
        if conc.empty:
            st.info("Need revenue data (Rate Matrix + M3PL).")
        else:
            top1 = conc.iloc[0]["share_pct"] if len(conc) >= 1 else 0
            top5 = conc.head(5)["share_pct"].sum() if len(conc) >= 1 else 0
            top10 = conc.head(10)["share_pct"].sum() if len(conc) >= 1 else 0
            cc1, cc2, cc3, cc4 = st.columns(4)
            cc1.metric("Top 1 share", f"{top1:.1f}%")
            cc2.metric("Top 5 share", f"{top5:.1f}%")
            cc3.metric("Top 10 share", f"{top10:.1f}%")
            n_to_80 = int((conc["cumulative_share_pct"] >= 80).idxmax()) + 1 if (conc["cumulative_share_pct"] >= 80).any() else len(conc)
            cc4.metric("Customers to 80%", f"{n_to_80}")

            fig = px.line(
                conc, x="rank", y="cumulative_share_pct",
                title="Cumulative revenue share by customer rank",
                labels={"rank": "Customer rank", "cumulative_share_pct": "Cumulative revenue %"},
                markers=True,
            )
            fig.add_hline(y=80, line_dash="dash", line_color="orange", annotation_text="80%")
            st.plotly_chart(fig, use_container_width=True)

            head = conc.head(20)
            fig = px.bar(
                head.sort_values("revenue"),
                x="revenue", y="customer", orientation="h", color="margin",
                color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"],
                title="Top 20 customers by revenue (color = margin $)",
                labels={"revenue": "Revenue $"},
                hover_data=["share_pct", "cumulative_share_pct", "cost", "margin"],
            )
            fig.update_layout(height=600)
            st.plotly_chart(fig, use_container_width=True)

            st.dataframe(conc, use_container_width=True, height=400)
            export_dataframe(conc, filename="customer_concentration", label="Download Concentration")

    # ───────────────── Weekly trend ─────────────────
    with tab4:
        st.subheader("Weekly stops per customer")
        stops = _load("stop_master")
        if stops.empty:
            st.info("No stop data yet.")
        else:
            sm = stops.copy()
            sm["customer"] = sm["customer"].astype(str).str.strip().str.upper()
            sm = sm[sm["customer"].notna() & (sm["customer"] != "") & (sm["customer"] != "NAN")]
            sm["arrival_dt"] = pd.to_datetime(sm["arrival_date"], errors="coerce")
            sm = sm[sm["arrival_dt"].notna()]
            if sm.empty:
                st.info("No stops with valid dates.")
            else:
                sm["week"] = sm["arrival_dt"].dt.to_period("W").dt.start_time
                weekly = sm.groupby(["week", "customer"]).size().reset_index(name="stops")
                top_customers = (
                    weekly.groupby("customer")["stops"].sum()
                    .sort_values(ascending=False).head(10).index.tolist()
                )
                weekly_top = weekly[weekly["customer"].isin(top_customers)]
                fig = px.line(
                    weekly_top, x="week", y="stops", color="customer",
                    title="Weekly stops — top 10 customers",
                    labels={"week": "Week", "stops": "Stops"},
                    markers=True,
                )
                fig.update_layout(height=500)
                st.plotly_chart(fig, use_container_width=True)

                # Heatmap variant
                pivot = weekly.pivot(index="customer", columns="week", values="stops").fillna(0)
                pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).head(30).index]
                fig = px.imshow(
                    pivot, color_continuous_scale="Blues", aspect="auto",
                    labels={"x": "Week", "y": "Customer", "color": "Stops"},
                    title="Weekly stop volume — top 30 customers",
                )
                fig.update_layout(height=700)
                st.plotly_chart(fig, use_container_width=True)
