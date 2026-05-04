"""One-off migration: add the multi-signal load-detection columns to an
existing `stop_master` table, and create the `load_override` table if it
doesn't exist.

Idempotent — safe to run multiple times. Only adds columns that don't
already exist.

Usage:
    .venv/Scripts/python.exe scripts/migrate_load_signals.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datascrubb.config import load_config

NEW_STOP_MASTER_COLUMNS = [
    ("load_signal_crst", "INTEGER"),
    ("load_signal_sap", "INTEGER"),
    ("load_signal_reefer", "INTEGER"),
    ("load_signal_setpoint", "INTEGER"),
    ("load_signal_sequence", "INTEGER"),
    ("load_signal_bol", "INTEGER"),
    ("load_confidence", "INTEGER"),
    ("load_state_disputed", "INTEGER"),
    ("loaded_at_stop_v2", "INTEGER"),
]

NEW_TELEMETRY_STOP_COLUMNS = [
    ("min_setpoint_c", "FLOAT"),
    ("max_setpoint_c", "FLOAT"),
]

LOAD_OVERRIDE_DDL = """
CREATE TABLE IF NOT EXISTS load_override (
    transaction_id  TEXT PRIMARY KEY,
    override_value  INTEGER NOT NULL,
    reason          TEXT,
    set_by          TEXT,
    set_at          TEXT NOT NULL
)
"""


def main() -> None:
    cfg = load_config()
    db_path = cfg.db_path
    print(f"Migrating: {db_path}")

    if not db_path.exists():
        print(f"DB doesn't exist yet — nothing to migrate. Run the pipeline once first.")
        return

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # Existing columns on stop_master
    existing = {r[1] for r in cur.execute("PRAGMA table_info(stop_master)")}
    added = []
    for name, dtype in NEW_STOP_MASTER_COLUMNS:
        if name in existing:
            continue
        cur.execute(f"ALTER TABLE stop_master ADD COLUMN {name} {dtype}")
        added.append(name)
    if added:
        print(f"  Added {len(added)} columns to stop_master: {', '.join(added)}")
    else:
        print("  stop_master: all load-signal columns already present")

    # telemetry_stop additions for setpoint min/max
    existing_tel = {r[1] for r in cur.execute("PRAGMA table_info(telemetry_stop)")}
    tel_added = []
    for name, dtype in NEW_TELEMETRY_STOP_COLUMNS:
        if name in existing_tel:
            continue
        cur.execute(f"ALTER TABLE telemetry_stop ADD COLUMN {name} {dtype}")
        tel_added.append(name)
    if tel_added:
        print(f"  Added {len(tel_added)} columns to telemetry_stop: {', '.join(tel_added)}")
    else:
        print("  telemetry_stop: setpoint columns already present")

    # load_override table
    cur.execute(LOAD_OVERRIDE_DDL)
    print("  load_override table ensured")

    con.commit()
    con.close()
    print("Done.")


if __name__ == "__main__":
    main()
