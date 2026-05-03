"""Read-only helpers for reusing previously-persisted source data.

When the pipeline is re-run with only some sources (e.g. only CRST + telemetry,
no M3PL), the matching engine and downstream KPIs would otherwise see None for
the missing inputs and produce a degraded run (miles=0, cost=0, etc.). These
helpers let the pipeline fall back to whatever's already in the SQLite DB
from the previous run, so the user only refreshes the slices they actually
re-uploaded.

What's reusable per source:
- CRST → required, no fallback. Every run must have CRST.
- SAP → ``sap_segment`` table holds matcher OUTPUT; reusing it skips the
  SAP matcher entirely.
- M3PL → ``billing_snapshot`` table holds normalized M3PL adapter output;
  same shape as a fresh adapter run, drop-in for ``m3pl_df``.
- Telemetry → ``telemetry_stop`` holds the per-stop AGGREGATIONS, NOT raw
  events. Raw events are never persisted. Reusing it skips the telemetry
  matcher entirely.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy.engine import Engine

logger = logging.getLogger("datascrubb.db_cache")


def _read_table_or_none(engine: Engine, table: str) -> pd.DataFrame | None:
    """SELECT * FROM <table>; returns None if table doesn't exist or is empty.

    Coerces any DateTime-typed ORM columns back to pandas datetimes (SQLite
    stores them as ISO strings; the ORM rejects strings on re-insert).
    Strips persistence-only columns (``id``, ``created_at``).
    """
    try:
        df = pd.read_sql(f"SELECT * FROM {table}", engine)
    except Exception as e:
        logger.debug("db_cache: could not read %s: %s", table, e)
        return None
    if df is None or df.empty:
        return None

    # Drop persistence-only columns that the upsert layer doesn't accept
    for col in ("id", "created_at"):
        if col in df.columns:
            df = df.drop(columns=[col])

    # Coerce any DateTime columns from string back to datetime
    from sqlalchemy import inspect
    try:
        insp = inspect(engine)
        cols = insp.get_columns(table)
        for col in cols:
            name = col["name"]
            type_str = str(col["type"]).upper()
            if name in df.columns and "DATETIME" in type_str:
                df[name] = pd.to_datetime(df[name], errors="coerce")
    except Exception as e:
        logger.debug("db_cache: could not introspect %s for datetime coercion: %s", table, e)

    return df


def read_cached_sap(engine: Engine) -> pd.DataFrame | None:
    """Cached ``sap_segment`` rows (matcher output). None if empty/missing."""
    return _read_table_or_none(engine, "sap_segment")


def read_cached_m3pl(engine: Engine) -> pd.DataFrame | None:
    """Cached ``billing_snapshot`` rows (normalized M3PL). None if empty/missing."""
    return _read_table_or_none(engine, "billing_snapshot")


def read_cached_telemetry_stop(engine: Engine) -> pd.DataFrame | None:
    """Cached ``telemetry_stop`` aggregations. None if empty/missing."""
    return _read_table_or_none(engine, "telemetry_stop")


def db_source_status(engine: Engine) -> dict[str, dict[str, Any]]:
    """Per-source rollup for the dashboard's "what's already cached" panel.

    Returns a dict like::

        {
          "crst":      {"rows": 3361, "last_run_id": "...", "last_run_ts": "2026-05-03 14:36"},
          "sap":       {"rows": 4348, ...},
          "m3pl":      {"rows": 228,  ...},
          "telemetry": {"rows": 3214, ...},
        }

    Sources with no cached data return ``{"rows": 0, "last_run_id": None, "last_run_ts": None}``.
    """
    sources = {
        "crst": "stop_master",
        "sap": "sap_segment",
        "m3pl": "billing_snapshot",
        "telemetry": "telemetry_stop",
    }

    # Most recent successful run, if any
    last_run_id, last_run_ts = None, None
    try:
        last = pd.read_sql(
            "SELECT run_id, run_timestamp FROM pipeline_run "
            "WHERE status = 'SUCCESS' "
            "ORDER BY run_timestamp DESC LIMIT 1",
            engine,
        )
        if not last.empty:
            last_run_id = str(last.iloc[0]["run_id"])
            last_run_ts = str(last.iloc[0]["run_timestamp"])
    except Exception as e:
        logger.debug("db_cache: could not read pipeline_run: %s", e)

    out: dict[str, dict[str, Any]] = {}
    for source, table in sources.items():
        try:
            df = pd.read_sql(f"SELECT COUNT(*) AS n FROM {table}", engine)
            n = int(df.iloc[0]["n"]) if not df.empty else 0
        except Exception:
            n = 0
        out[source] = {
            "rows": n,
            "last_run_id": last_run_id if n > 0 else None,
            "last_run_ts": last_run_ts if n > 0 else None,
        }
    return out
