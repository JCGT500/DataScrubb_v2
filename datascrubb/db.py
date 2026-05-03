"""Database engine, session management, and incremental upsert logic."""

import logging
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from datascrubb.models import (
    Base,
    BillingSnapshot,
    PipelineRun,
    SapSegment,
    StopMaster,
    TelemetryStop,
    ValidationError,
)

logger = logging.getLogger("datascrubb.db")


def get_engine(db_path: str | Path):
    """Create a SQLAlchemy engine for a SQLite database with WAL mode enabled."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    # Enable WAL mode for concurrent read/write
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine) -> None:
    """Create all tables if they don't exist. Safe to call repeatedly."""
    Base.metadata.create_all(engine)
    logger.info("Database initialized")


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine)


@contextmanager
def get_session(engine):
    """Context manager yielding a database session with auto-commit/rollback."""
    session_factory = get_session_factory(engine)
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _to_records(df: pd.DataFrame, columns: list[str], renames: dict[str, str] | None = None) -> list[dict]:
    """Convert a DataFrame slice into a list of dicts suitable for bulk insert.

    Replaces NaN/NaT with None; coerces ints where source can be float-like-NaN.
    """
    renames = renames or {}
    out_records: list[dict] = []
    for _, row in df.iterrows():
        rec: dict = {}
        for src in columns:
            tgt = renames.get(src, src)
            val = row.get(src)
            if val is None:
                rec[tgt] = None
                continue
            try:
                if pd.isna(val):
                    rec[tgt] = None
                    continue
            except (TypeError, ValueError):
                pass
            rec[tgt] = val
        out_records.append(rec)
    return out_records


def upsert_stops(session: Session, df: pd.DataFrame) -> int:
    """Bulk upsert StopMaster rows via DELETE + bulk INSERT."""
    if df is None or df.empty:
        return 0

    df = df.copy()
    df["order_number"] = df.get("order_#", "").astype(str)
    df["location_date"] = df.get("location_date", "").astype(str)
    df["arrival_date"] = df.get("arrival_date", "").astype(str)

    cols = [
        "transaction_id", "order_number", "location_date", "s_code", "stop_type",
        "stop_class",
        "arrival_date", "actual_arrival", "actual_departure", "dwell_minutes",
        "original_appt", "current_appt", "resolved_appt", "route_day", "stop_seq",
        "minutes_from_appt", "otp_day_pass", "otp_time_pass", "otp_original_pass",
        "minutes_from_original_appt", "otp_time_original_pass",
        "stop_performance_status", "error_flag", "error_reason",
        "telem_events", "min_amb_temp", "max_amb_temp", "avg_amb_temp",
        "min_s1", "max_s1", "door_open_events", "trailer",
        "bol", "truck", "drivers", "dm", "city", "state", "late_code", "route_name",
        "customer", "stop_direction", "tender_cases", "current_cases", "loaded_at_stop",
        "cases_variance", "sum_of_weight",
        "cap_max_cases", "cap_max_weight_lbs", "capacity_source",
        "fill_pct_cases", "fill_pct_weight",
    ]
    # Ensure missing columns exist as None
    for c in cols:
        if c not in df.columns:
            df[c] = None

    records = _to_records(df, cols)

    # Delete dependent rows first (FK constraints), then existing stop rows,
    # then bulk insert. Done in chunks because SQLite limits ~1000 params per
    # IN clause.
    txn_ids = df["transaction_id"].dropna().unique().tolist()
    if txn_ids:
        chunk_size = 500
        for start in range(0, len(txn_ids), chunk_size):
            chunk = txn_ids[start:start + chunk_size]
            session.query(ValidationError).filter(
                ValidationError.transaction_id.in_(chunk)
            ).delete(synchronize_session=False)
            session.query(SapSegment).filter(
                SapSegment.transaction_id.in_(chunk)
            ).delete(synchronize_session=False)
            session.query(TelemetryStop).filter(
                TelemetryStop.transaction_id.in_(chunk)
            ).delete(synchronize_session=False)
            session.query(StopMaster).filter(
                StopMaster.transaction_id.in_(chunk)
            ).delete(synchronize_session=False)
        session.flush()

    session.bulk_insert_mappings(StopMaster, records)
    logger.info("Upserted %d StopMaster rows", len(records))
    return len(records)


def upsert_sap_segments(session: Session, df: pd.DataFrame) -> int:
    """Replace SAP segments for matched transaction_ids, then insert new ones.

    SAP data comes as a full re-export, so we delete existing segments for the
    transaction_ids in the incoming data and re-insert.
    """
    if df.empty:
        return 0

    txn_ids = df["transaction_id"].dropna().unique().tolist()
    if txn_ids:
        session.query(SapSegment).filter(SapSegment.transaction_id.in_(txn_ids)).delete(
            synchronize_session=False
        )

    count = 0
    for _, row in df.iterrows():
        seg = SapSegment(
            transaction_id=row.get("transaction_id"),
            order_key=str(row.get("order_key", "")),
            document_number=str(row.get("document_number", "")),
            segment_number=str(row.get("segment_number", "")),
            carrier_bol=str(row.get("carrier_bol#", row.get("carrier_bol", ""))),
            shipper_name=str(row.get("shipper_name", "")),
            consignee_name=str(row.get("consignee_name", "")),
            cases_count=row.get("cases_#") if pd.notna(row.get("cases_#")) else None,
            actual_weight=row.get("actual_weight") if pd.notna(row.get("actual_weight")) else None,
            tractor=str(row.get("tractor", "")),
            trailer=str(row.get("trailer", "")),
            sap_match_flag=row.get("sap_match_flag", "UNMATCHED"),
            time_diff_hours=row.get("time_diff_hours") if pd.notna(row.get("time_diff_hours")) else None,
            s_code=row.get("s_code"),
            arrive=row.get("arrive") if pd.notna(row.get("arrive")) else None,
        )
        session.add(seg)
        count += 1

    logger.info("Inserted %d SapSegment rows", count)
    return count


def upsert_telemetry_stops(session: Session, df: pd.DataFrame) -> int:
    """Bulk upsert stop-level telemetry aggregations."""
    if df is None or df.empty:
        return 0

    cols = [
        "transaction_id", "telem_events",
        "min_amb_temp", "max_amb_temp", "avg_amb_temp",
        "min_s1", "max_s1", "avg_tl1", "min_tl1", "max_tl1",
        "door_open_events", "door_open_while_moving",
        "max_speed", "avg_speed", "idle_minutes",
        "reefer_runtime_minutes", "reefer_gallons", "reefer_fuel_cost",
        "alarm_events", "min_battery", "avg_battery",
        "max_engine_hours", "max_total_hours",
        "setpoint_changes", "avg_da_ra_delta",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None

    records = _to_records(df, cols)

    txn_ids = df["transaction_id"].dropna().unique().tolist()
    if txn_ids:
        session.query(TelemetryStop).filter(
            TelemetryStop.transaction_id.in_(txn_ids)
        ).delete(synchronize_session=False)

    session.bulk_insert_mappings(TelemetryStop, records)
    logger.info("Upserted %d TelemetryStop rows", len(records))
    return len(records)


def persist_errors(session: Session, errors: list[dict], run_id: str) -> int:
    """Persist validation errors for a pipeline run."""
    count = 0
    for err in errors:
        ve = ValidationError(
            transaction_id=err.get("transaction_id"),
            source=err.get("source", "UNKNOWN"),
            error_type=err.get("error_type", "SOFT"),
            error_reason=err.get("error_reason", ""),
            run_id=run_id,
        )
        session.add(ve)
        count += 1

    logger.info("Persisted %d validation errors for run %s", count, run_id)
    return count


def persist_run(session: Session, run: PipelineRun) -> None:
    """Persist or update a pipeline run record."""
    session.merge(run)
    logger.info("Persisted pipeline run %s (status=%s)", run.run_id, run.status)


def upsert_billing(session: Session, df: pd.DataFrame) -> int:
    """Bulk upsert M3PL billing snapshots (PK = pro_number + billing_week_end)."""
    if df is None or df.empty:
        return 0

    deduped = df.drop_duplicates(
        subset=["pro_number", "billing_week_end"], keep="last"
    ).copy()
    deduped["pro_number"] = deduped["pro_number"].astype(str)

    cols = [
        "pro_number", "billing_week_end", "legacy_route", "lane",
        "crst_miles", "stop_count", "team_miles", "solo_miles",
        "team_deficit_miles", "solo_deficit_miles", "tolls",
        "stop_rate", "team_rate", "solo_rate",
        "team_deficit_rate", "solo_deficit_rate",
        "billed_miles_amount", "billed_stops_amount",
        "billed_deficit_amount", "billed_amount",
        "tractor", "trailer", "source_file",
    ]
    for c in cols:
        if c not in deduped.columns:
            deduped[c] = None

    records = _to_records(deduped, cols)

    # Delete matching composite keys, then bulk insert
    keys = [
        (rec["pro_number"], rec["billing_week_end"])
        for rec in records
        if rec.get("pro_number") and rec.get("billing_week_end")
    ]
    if keys:
        from sqlalchemy import and_, or_, tuple_

        session.query(BillingSnapshot).filter(
            tuple_(
                BillingSnapshot.pro_number, BillingSnapshot.billing_week_end
            ).in_(keys)
        ).delete(synchronize_session=False)

    session.bulk_insert_mappings(BillingSnapshot, records)
    logger.info("Upserted %d BillingSnapshot rows", len(records))
    return len(records)
