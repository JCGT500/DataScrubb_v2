"""Observability — KPI calculation audit trail browser.

Streamlit equivalent of the standalone Flask debug_dashboard.py from the
VanguardV1 reference implementation. Three sections:

1. **Quality check pass rates (last 24h)** — sorted ascending so failures float to top.
2. **Recent calculations** — filterable by status (ok / flagged / error), drill-down by clicking a correlation_id.
3. **Trace explorer** — paste a correlation_id (or pipeline run_id) to see every calc + check that fired under it.

See `CLAUDE.md` Section 2 for the conventions behind `@observe` and `quality_check`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@st.cache_data(ttl=10, show_spinner=False)
def _ensure_configured() -> tuple[bool, str]:
    """Configure the observability module from current config + return (enabled, db_path).
    Cached so we don't re-read config on every widget interaction.
    """
    try:
        from datascrubb.config import load_config
        from datascrubb.observability import configure
        cfg = load_config()
        obs = getattr(cfg, "observability", None)
        if obs is None:
            return (False, "data/observability.db")
        configure(
            enabled=bool(getattr(obs, "enabled", False)),
            db_path=getattr(obs, "db_path", "data/observability.db"),
            summarize_dataframes=bool(getattr(obs, "summarize_dataframes", True)),
        )
        return (bool(obs.enabled), obs.db_path)
    except Exception as e:
        st.warning(f"Could not configure observability: {e}")
        return (False, "data/observability.db")


def render():
    st.header("🔍 Observability")
    st.caption(
        "Audit trail of every wrapped KPI calculation. Drill from "
        "'something looks wrong' to the exact inputs that produced it."
    )

    enabled, db_path = _ensure_configured()
    if not enabled:
        st.info(
            "Observability is **disabled**. Enable it in **Admin → Observability**, "
            "then re-run the pipeline. Existing audit data (if any) is still browsable below."
        )

    from datascrubb.observability import (
        quality_summary, recent_calcs, trace, total_row_counts,
    )

    counts = total_row_counts()
    c1, c2, c3 = st.columns(3)
    c1.metric("Calculations recorded", f"{counts['calculations']:,}")
    c2.metric("Quality checks recorded", f"{counts['quality_checks']:,}")
    c3.caption(f"Audit DB: `{db_path}`")

    if counts["calculations"] == 0:
        st.warning(
            "Audit DB is empty. Run the pipeline at least once with observability enabled."
        )
        return

    # ─── 1. Quality check pass rates ──────────────────────────────
    st.markdown("---")
    st.subheader("Quality check pass rates (last 24h)")
    hours = st.slider("Window (hours)", min_value=1, max_value=168, value=24, key="obs_hours")
    summary = quality_summary(hours=hours)
    if not summary:
        st.info("No quality checks in the selected window.")
    else:
        sdf = pd.DataFrame(summary)

        def _row_style(row):
            color = "#fde" if row["pass_rate_pct"] < 100 else ""
            return [f"background-color: {color}" if color else "" for _ in row]

        st.dataframe(
            sdf.style.apply(_row_style, axis=1),
            use_container_width=True,
            height=min(400, 60 + 35 * len(sdf)),
        )

    # ─── 2. Recent calculations ──────────────────────────────────
    st.markdown("---")
    st.subheader("Recent calculations")
    f1, f2 = st.columns([2, 1])
    status_filter = f1.multiselect(
        "Status filter",
        options=["ok", "flagged", "error"],
        default=["flagged", "error"],
        key="obs_status_filter",
    )
    limit = f2.number_input("Max rows", min_value=10, max_value=1000, value=100, step=10, key="obs_limit")

    rows = []
    for status in (status_filter or [None]):
        rows.extend(recent_calcs(limit=int(limit), status=status))
    if rows:
        df = pd.DataFrame(rows)[
            ["started_at", "calc_name", "status", "duration_ms", "flag_count", "correlation_id", "error"]
        ].copy()
        df["started_at"] = df["started_at"].astype(str).str.slice(0, 19)
        df["duration_ms"] = df["duration_ms"].round(1)
        st.dataframe(df, use_container_width=True, height=400)

        # Quick "click a row to trace" via selectbox
        cids = sorted({r["correlation_id"] for r in rows})
        st.caption(f"{len(cids)} distinct correlation IDs in the result.")
    else:
        st.info("No calculations match the current filter.")

    # ─── 3. Trace explorer ───────────────────────────────────────
    st.markdown("---")
    st.subheader("Trace explorer")
    st.caption(
        "Paste a correlation ID (or a pipeline `run_id` like `20260504_120000_abc123`) "
        "to see every calc + check that fired under it."
    )
    cid = st.text_input("Correlation ID", key="obs_cid_input").strip()
    if cid:
        t = trace(cid)
        if not t["calcs"] and not t["checks"]:
            st.warning(f"No calcs or checks found for correlation_id `{cid}`.")
        else:
            st.markdown(f"**{len(t['calcs'])}** calc(s) · **{len(t['checks'])}** quality check(s)")

            # Calcs
            for c in t["calcs"]:
                status = c.get("status", "?")
                emoji = {"ok": "✅", "flagged": "⚠️", "error": "❌"}.get(status, "•")
                with st.expander(
                    f"{emoji} **{c['calc_name']}** — {status} — {round(c.get('duration_ms') or 0, 1)} ms",
                    expanded=(status != "ok"),
                ):
                    if c.get("error"):
                        st.error(c["error"])
                    inputs = c.get("inputs_json")
                    if inputs:
                        st.markdown("**Inputs**")
                        try:
                            st.json(json.loads(inputs))
                        except Exception:
                            st.code(inputs[:2000])
                    output = c.get("output_json")
                    if output:
                        st.markdown("**Output**")
                        try:
                            st.json(json.loads(output))
                        except Exception:
                            st.code(output[:2000])

            # Checks
            if t["checks"]:
                st.markdown("**Quality checks**")
                checks_df = pd.DataFrame(t["checks"])[["calc_name", "check_name", "passed", "detail"]]
                checks_df["passed"] = checks_df["passed"].map({1: "✅ PASS", 0: "❌ FAIL"})
                st.dataframe(checks_df, use_container_width=True, height=min(400, 60 + 35 * len(checks_df)))
