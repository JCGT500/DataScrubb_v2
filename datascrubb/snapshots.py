"""Period snapshots — copy KPI tables into archive tables tagged with a label.

Lets you compare multiple periods side-by-side (e.g. "Jan 2026" vs "Feb 2026")
without re-ingesting raw data. After a pipeline run, call ``snapshot(label)``
to freeze the current KPI tables under that label. Then the Multi-Month
Compare page can pull from the archive.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from datascrubb.config import load_config
from datascrubb.db import get_engine

logger = logging.getLogger("datascrubb.snapshots")

# Tables we snapshot. Anything else (raw inputs, pipeline_run, validation_error)
# is excluded — they're per-run, not per-period summaries.
SNAPSHOT_TABLES = [
    "stop_master", "billing_snapshot",
    "route_kpi", "route_revenue", "route_reefer_cost",
    "loaded_miles", "miles_variance", "billing_recon",
    "claims_risk", "lane_profitability",
    "equip_util_tractor", "equip_util_trailer", "equip_util_driver",
    "driver_scorecard", "trailer_utilization", "alarm_log",
    "temp_compliance",
    "customer_scorecard", "customer_churn", "customer_concentration",
    "cycle_time", "late_code_analysis", "detention_audit", "demand_forecast",
]

ARCHIVE_PREFIX = "snap_"
INDEX_TABLE = "snapshot_index"


def _ensure_index(engine) -> None:
    with engine.begin() as con:
        con.execute(text(
            f"CREATE TABLE IF NOT EXISTS {INDEX_TABLE} ("
            "label TEXT PRIMARY KEY, "
            "created_at TEXT NOT NULL, "
            "tables TEXT NOT NULL, "
            "row_counts TEXT)"
        ))


def list_snapshots() -> pd.DataFrame:
    """List all snapshots currently archived in the DB."""
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    _ensure_index(engine)
    try:
        return pd.read_sql(f"SELECT * FROM {INDEX_TABLE} ORDER BY created_at DESC", engine)
    except Exception:
        return pd.DataFrame()


def snapshot(label: str, db_path: Path | None = None) -> dict:
    """Copy current KPI tables into archive tables prefixed with the label.

    The label is normalised to lowercase, stripped, with spaces -> underscores.
    Existing snapshot under same label is overwritten.

    Returns: {"label": ..., "tables": [...], "row_counts": {table: rows}}.
    """
    cfg = load_config()
    db = Path(db_path) if db_path else cfg.db_path
    engine = get_engine(db)
    _ensure_index(engine)

    norm = label.strip().lower().replace(" ", "_").replace("-", "_")
    if not norm:
        raise ValueError("Snapshot label cannot be empty.")

    saved: list[str] = []
    counts: dict[str, int] = {}

    with engine.begin() as con:
        # Drop any existing snapshot under this label
        existing = pd.read_sql(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '{ARCHIVE_PREFIX}{norm}__%'",
            con,
        )
        for t in existing["name"].tolist():
            con.execute(text(f"DROP TABLE IF EXISTS {t}"))

        for table in SNAPSHOT_TABLES:
            try:
                df = pd.read_sql(f"SELECT * FROM {table}", con)
            except Exception:
                continue
            if df.empty:
                continue
            target = f"{ARCHIVE_PREFIX}{norm}__{table}"
            df.to_sql(target, con, if_exists="replace", index=False)
            saved.append(target)
            counts[table] = len(df)

        # Index entry
        con.execute(text(f"DELETE FROM {INDEX_TABLE} WHERE label = :l"), {"l": norm})
        con.execute(
            text(
                f"INSERT INTO {INDEX_TABLE} (label, created_at, tables, row_counts) "
                "VALUES (:l, :c, :t, :r)"
            ),
            {
                "l": norm,
                "c": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "t": ",".join(sorted(counts.keys())),
                "r": str(counts),
            },
        )

    logger.info("Snapshot '%s' written: %d tables, %d total rows",
                norm, len(counts), sum(counts.values()))
    return {"label": norm, "tables": saved, "row_counts": counts}


def load_snapshot_table(label: str, table: str) -> pd.DataFrame:
    """Load a single archived table for the given snapshot label."""
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    norm = label.strip().lower().replace(" ", "_").replace("-", "_")
    target = f"{ARCHIVE_PREFIX}{norm}__{table}"
    try:
        return pd.read_sql(f"SELECT * FROM {target}", engine)
    except Exception:
        return pd.DataFrame()


def delete_snapshot(label: str) -> int:
    """Remove all archive tables for a snapshot label. Returns count dropped."""
    cfg = load_config()
    engine = get_engine(cfg.db_path)
    _ensure_index(engine)
    norm = label.strip().lower().replace(" ", "_").replace("-", "_")
    dropped = 0
    with engine.begin() as con:
        existing = pd.read_sql(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '{ARCHIVE_PREFIX}{norm}__%'",
            con,
        )
        for t in existing["name"].tolist():
            con.execute(text(f"DROP TABLE IF EXISTS {t}"))
            dropped += 1
        con.execute(text(f"DELETE FROM {INDEX_TABLE} WHERE label = :l"), {"l": norm})
    return dropped
