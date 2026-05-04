"""Load Review — inspect the multi-signal load verdict and override per-stop.

Three sections:
1. **Disputed stops** — where the load signals disagree (e.g., CRST says
   loaded but reefer + SAP say empty). Each row shows every signal's vote
   plus the current verdict, and lets you flip it with one click.
2. **Active overrides** — every manual override currently in effect, with
   a remove button.
3. **Per-stop diagnostic** — paste a transaction_id to see all signals,
   the telemetry summary, and the SAP segment.

See ``CLAUDE.md`` Section 1 (load detection) and ``LOGIC.md`` →
Loaded-at-stop for the multi-signal logic.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SIGNAL_COLUMNS = [
    "load_signal_crst",
    "load_signal_sap",
    "load_signal_reefer",
    "load_signal_setpoint",
    "load_signal_sequence",
    "load_signal_bol",
]
SIGNAL_LABELS = {
    "load_signal_crst": "CRST cases",
    "load_signal_sap": "SAP paperwork",
    "load_signal_reefer": "Reefer cargo temp",
    "load_signal_setpoint": "Setpoint pattern",
    "load_signal_sequence": "Route sequence",
    "load_signal_bol": "BOL field",
}


def _vote_emoji(v) -> str:
    if pd.isna(v):
        return "—"
    return "✓" if int(v) == 1 else "✗"


@st.cache_data(ttl=15, show_spinner=False)
def _engine_path() -> str:
    from datascrubb.config import load_config
    cfg = load_config()
    return str(cfg.db_path)


def _engine():
    from datascrubb.db import get_engine
    return get_engine(_engine_path())


def render():
    st.header("🔎 Load Review")
    st.caption(
        "Audit the multi-signal load verdict. When auto-detection gets it wrong, "
        "override per-stop — overrides persist across pipeline runs and the "
        "excursion KPIs respect them on the next pipeline run."
    )

    engine = _engine()

    # Quick verdict summary
    try:
        summary = pd.read_sql("""
            SELECT
              SUM(CASE WHEN loaded_at_stop_v2 = 1 THEN 1 ELSE 0 END) AS loaded,
              SUM(CASE WHEN loaded_at_stop_v2 = 0 THEN 1 ELSE 0 END) AS empty,
              SUM(CASE WHEN load_state_disputed = 1 THEN 1 ELSE 0 END) AS disputed,
              COUNT(*) AS total
            FROM stop_master
        """, engine).iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total stops", f"{int(summary['total']):,}")
        c2.metric("Verdict: loaded (v2)", f"{int(summary['loaded'] or 0):,}")
        c3.metric("Verdict: empty (v2)", f"{int(summary['empty'] or 0):,}")
        c4.metric("Disputed (signals disagree)", f"{int(summary['disputed'] or 0):,}")
    except Exception as e:
        st.error(f"Could not read stop_master: {e}")
        return

    # ─── Disputed stops ─────────────────────────────────────────
    st.markdown("---")
    st.subheader("Disputed stops (signals disagree)")

    f1, f2, f3 = st.columns([2, 2, 1])
    cust_filter = f1.text_input("Customer contains", key="lr_cust", value="").strip().upper()
    verdict_filter = f2.selectbox(
        "Current verdict", ["any", "loaded", "empty"], key="lr_verdict",
    )
    limit = f3.number_input("Max rows", min_value=10, max_value=2000, value=200, step=50, key="lr_limit")

    where = ["load_state_disputed = 1"]
    if cust_filter:
        where.append(f"UPPER(customer) LIKE '%{cust_filter}%'")
    if verdict_filter == "loaded":
        where.append("loaded_at_stop_v2 = 1")
    elif verdict_filter == "empty":
        where.append("loaded_at_stop_v2 = 0")

    sql = f"""
        SELECT transaction_id, route_name, customer, arrival_date,
               {', '.join(SIGNAL_COLUMNS)},
               load_confidence, loaded_at_stop_v2,
               loaded_at_stop AS legacy_loaded
        FROM stop_master
        WHERE {' AND '.join(where)}
        ORDER BY load_confidence ASC, route_name
        LIMIT {int(limit)}
    """
    disputed = pd.read_sql(sql, engine)

    if disputed.empty:
        st.info("No disputed stops match the current filter.")
    else:
        st.caption(
            f"Showing {len(disputed)} disputed stop(s). The lowest-confidence rows "
            "(signals strongly disagree) are first — most likely to need an override."
        )
        # Render with vote emoji + override button
        for idx, row in disputed.head(50).iterrows():
            with st.expander(
                f"📍 {row['transaction_id']} · {row.get('customer') or '?'} · "
                f"{row.get('route_name') or '?'} · "
                f"{'LOADED' if row['loaded_at_stop_v2'] == 1 else 'EMPTY'} "
                f"({int(row['load_confidence'])}% conf)",
                expanded=False,
            ):
                # Signal grid
                cols = st.columns(len(SIGNAL_COLUMNS))
                for i, sig in enumerate(SIGNAL_COLUMNS):
                    cols[i].metric(SIGNAL_LABELS[sig], _vote_emoji(row[sig]))

                # Override buttons
                ob1, ob2, ob3 = st.columns(3)
                if ob1.button("Mark EMPTY", key=f"mark_empty_{row['transaction_id']}"):
                    _save_override(row["transaction_id"], 0, reason="manual: not loaded")
                    st.success(f"Override saved: {row['transaction_id']} → EMPTY")
                    st.rerun()
                if ob2.button("Mark LOADED", key=f"mark_loaded_{row['transaction_id']}"):
                    _save_override(row["transaction_id"], 1, reason="manual: confirmed loaded")
                    st.success(f"Override saved: {row['transaction_id']} → LOADED")
                    st.rerun()
                ob3.caption("Override applies to next pipeline run.")

        if len(disputed) > 50:
            st.caption(f"… and {len(disputed) - 50} more (raise Max rows or filter).")

    # ─── Active overrides ───────────────────────────────────────
    st.markdown("---")
    st.subheader("Active overrides")
    overrides = pd.read_sql(
        "SELECT transaction_id, override_value, reason, set_by, set_at "
        "FROM load_override ORDER BY set_at DESC",
        engine,
    )
    if overrides.empty:
        st.info("No manual overrides set.")
    else:
        st.dataframe(overrides, use_container_width=True, height=min(400, 60 + 35 * len(overrides)))
        rm_id = st.text_input("Remove override for transaction_id", key="rm_override").strip()
        if rm_id and st.button("Remove override", key="rm_button"):
            _delete_override(rm_id)
            st.success(f"Removed override for {rm_id}")
            st.rerun()

    # ─── Per-stop diagnostic ────────────────────────────────────
    st.markdown("---")
    st.subheader("Per-stop diagnostic")
    txn = st.text_input("Transaction ID", key="lr_diag_txn").strip()
    if txn:
        row = pd.read_sql(
            f"SELECT * FROM stop_master WHERE transaction_id = '{txn}'", engine,
        )
        if row.empty:
            st.warning(f"No stop found with transaction_id = {txn}")
        else:
            r = row.iloc[0]
            st.markdown(f"**Customer:** {r.get('customer')}  ·  **Route:** {r.get('route_name')}  ·  "
                        f"**Date:** {r.get('arrival_date')}  ·  **Direction:** {r.get('stop_direction')}  ·  "
                        f"**Class:** {r.get('stop_class')}")

            cols = st.columns(len(SIGNAL_COLUMNS))
            for i, sig in enumerate(SIGNAL_COLUMNS):
                cols[i].metric(SIGNAL_LABELS[sig], _vote_emoji(r[sig]))
            st.markdown(
                f"**Verdict (v2):** {'LOADED' if r['loaded_at_stop_v2'] == 1 else 'EMPTY'}  ·  "
                f"**Confidence:** {int(r['load_confidence'] or 0)}%  ·  "
                f"**Disputed:** {'yes' if r['load_state_disputed'] == 1 else 'no'}  ·  "
                f"**Legacy verdict:** {'loaded' if r['loaded_at_stop'] == 1 else 'empty'}"
            )

            # Telemetry detail
            tel = pd.read_sql(
                f"SELECT * FROM telemetry_stop WHERE transaction_id = '{txn}'", engine,
            )
            if not tel.empty:
                st.markdown("**Telemetry summary**")
                t = tel.iloc[0]
                tcols = st.columns(4)
                tcols[0].metric("Reefer runtime (min)", f"{t.get('reefer_runtime_minutes', 0):.0f}" if pd.notna(t.get("reefer_runtime_minutes")) else "—")
                tcols[1].metric("Max cargo temp (°C)", f"{t.get('max_cargo_temp', 0):.1f}" if pd.notna(t.get("max_cargo_temp")) else "—")
                tcols[2].metric("Min S1 (°C)", f"{t.get('min_s1', 0):.1f}" if pd.notna(t.get("min_s1")) else "—")
                tcols[3].metric("Max S1 (°C)", f"{t.get('max_s1', 0):.1f}" if pd.notna(t.get("max_s1")) else "—")
            else:
                st.caption("No telemetry recorded for this stop.")

            # SAP segment
            sap = pd.read_sql(
                f"SELECT document_number, segment_number, cases_count, actual_weight, sap_match_flag "
                f"FROM sap_segment WHERE transaction_id = '{txn}'", engine,
            )
            if not sap.empty:
                st.markdown("**SAP segment(s)**")
                st.dataframe(sap, use_container_width=True)
            else:
                st.caption("No SAP segment matched to this stop.")


def _save_override(transaction_id: str, value: int, reason: str = "") -> None:
    from datascrubb.kpi.load_detection import upsert_load_override
    upsert_load_override(_engine(), transaction_id, value, reason=reason, set_by="dashboard")


def _delete_override(transaction_id: str) -> None:
    from datascrubb.kpi.load_detection import delete_load_override
    delete_load_override(_engine(), transaction_id)
