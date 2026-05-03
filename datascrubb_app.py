"""
DataScrubb — Transportation Data Integration, Validation & OTP Analysis
========================================================================
Single-file application: pipeline + Streamlit dashboard.

Usage:
    # Run the dashboard
    streamlit run datascrubb_app.py

    # Run the pipeline from command line
    python datascrubb_app.py --crst "path/to/crst.xlsx" --sap "path/to/sap.xlsx" --telemetry "path/to/telemetry.csv"
"""

import argparse
import html
import io
import json
import logging
import re
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

# =========================================================================
# CONSTANTS
# =========================================================================

OTP_TOLERANCE_MINUTES = 120
SAP_MATCH_MAX_HOURS = 36
TELEMETRY_WINDOW_MINUTES = 120
TELEMETRY_MIN_PINGS_PER_STOP = 5
TELEMETRY_DEFAULT_HEADER_ROW = 6

CRST_HEADER_KEYWORDS = ["order", "location", "actual", "original", "current"]
CRST_HEADER_MAX_ROWS = 15
CRST_REQUIRED_COLUMNS = ["order_#", "location_date", "original_appt", "current_appt", "actual_arrival"]
SAP_REQUIRED_COLUMNS = ["document_number", "segment_number", "shipper_search_term", "pick_up_date", "arrive"]

TELEMETRY_TEMP_COLUMNS = ["amb_temp", "da1", "ra1", "s1", "s2", "s3", "s4", "s5", "s6", "tl1"]

DB_PATH = Path("data/datascrubb.db")
OUTPUT_DIR = Path("output/")
LOG_FILE = Path("logs/pipeline.log")

# =========================================================================
# LOGGING
# =========================================================================

def setup_logging(log_file: Path = LOG_FILE, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("datascrubb")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger

log = logging.getLogger("datascrubb")

# =========================================================================
# UTILITIES
# =========================================================================

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip().str.lower().str.replace(" ", "_")
    return df


def extract_s_code(text) -> str | None:
    if pd.isna(text):
        return None
    match = re.search(r"S\d{3,5}", str(text))
    return match.group(0) if match else None


def find_header_row(df: pd.DataFrame, keywords: list[str], max_rows: int = 15) -> int | None:
    for i in range(min(max_rows, len(df))):
        row_text = " ".join(df.iloc[i].fillna("").astype(str)).lower()
        if all(k in row_text for k in keywords):
            return i
    return None

# =========================================================================
# DATABASE MODELS
# =========================================================================

class Base(DeclarativeBase):
    pass


class StopMaster(Base):
    __tablename__ = "stop_master"

    transaction_id = Column(String, primary_key=True)
    order_number = Column(String, index=True)
    location_date = Column(String)
    s_code = Column(String, index=True)
    stop_type = Column(String)
    arrival_date = Column(String)
    actual_arrival = Column(DateTime)
    original_appt = Column(DateTime)
    current_appt = Column(DateTime)
    resolved_appt = Column(DateTime)
    route_day = Column(String, index=True)
    stop_seq = Column(String)
    minutes_from_appt = Column(Float)
    otp_day_pass = Column(Float)
    otp_time_pass = Column(Float)
    otp_original_pass = Column(Float)
    minutes_from_original_appt = Column(Float)
    otp_time_original_pass = Column(Float)
    stop_performance_status = Column(String)
    error_flag = Column(String, default="N")
    error_reason = Column(String, default="")
    telem_events = Column(Integer)
    min_amb_temp = Column(Float)
    max_amb_temp = Column(Float)
    avg_amb_temp = Column(Float)
    min_s1 = Column(Float)
    max_s1 = Column(Float)
    door_open_events = Column(Integer)
    trailer = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    sap_segments = relationship("SapSegment", back_populates="stop")
    telemetry_stop_rel = relationship("TelemetryStop", back_populates="stop", uselist=False)
    validation_errors = relationship("ValidationError", back_populates="stop")


class SapSegment(Base):
    __tablename__ = "sap_segment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String, ForeignKey("stop_master.transaction_id"), index=True)
    order_key = Column(String)
    document_number = Column(String, index=True)
    segment_number = Column(String)
    carrier_bol = Column(String)
    shipper_name = Column(String)
    consignee_name = Column(String)
    cases_count = Column(Float)
    actual_weight = Column(Float)
    tractor = Column(String)
    trailer = Column(String)
    sap_match_flag = Column(String)
    time_diff_hours = Column(Float)
    s_code = Column(String)
    arrive = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stop = relationship("StopMaster", back_populates="sap_segments")


class TelemetryStop(Base):
    __tablename__ = "telemetry_stop"

    transaction_id = Column(String, ForeignKey("stop_master.transaction_id"), primary_key=True)
    telem_events = Column(Integer)
    min_amb_temp = Column(Float)
    max_amb_temp = Column(Float)
    avg_amb_temp = Column(Float)
    min_s1 = Column(Float)
    max_s1 = Column(Float)
    door_open_events = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stop = relationship("StopMaster", back_populates="telemetry_stop_rel")


class ValidationError(Base):
    __tablename__ = "validation_error"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String, ForeignKey("stop_master.transaction_id"), nullable=True)
    source = Column(String)
    error_type = Column(String)
    error_reason = Column(String)
    run_id = Column(String, ForeignKey("pipeline_run.run_id"), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stop = relationship("StopMaster", back_populates="validation_errors")
    pipeline_run = relationship("PipelineRun", back_populates="validation_errors")


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    run_id = Column(String, primary_key=True)
    run_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    source_files = Column(Text)
    records_read = Column(Text)
    records_output = Column(Text)
    match_rates = Column(Text)
    status = Column(String, default="RUNNING")
    error_message = Column(Text, nullable=True)

    validation_errors = relationship("ValidationError", back_populates="pipeline_run")

# =========================================================================
# DATABASE ENGINE
# =========================================================================

def get_engine(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine):
    Base.metadata.create_all(engine)


@contextmanager
def get_session(engine):
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _safe(val):
    """Return None if val is NaN/NaT, else val."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def upsert_stops(session: Session, df: pd.DataFrame) -> int:
    count = 0
    for _, r in df.iterrows():
        session.merge(StopMaster(
            transaction_id=r.get("transaction_id"),
            order_number=str(r.get("order_#", "")),
            location_date=str(r.get("location_date", "")),
            s_code=_safe(r.get("s_code")),
            stop_type=r.get("stop_type"),
            arrival_date=str(r.get("arrival_date", "")),
            actual_arrival=_safe(r.get("actual_arrival")),
            original_appt=_safe(r.get("original_appt")),
            current_appt=_safe(r.get("current_appt")),
            resolved_appt=_safe(r.get("resolved_appt")),
            route_day=r.get("route_day"),
            stop_seq=r.get("stop_seq"),
            minutes_from_appt=_safe(r.get("minutes_from_appt")),
            otp_day_pass=_safe(r.get("otp_day_pass")),
            otp_time_pass=_safe(r.get("otp_time_pass")),
            otp_original_pass=_safe(r.get("otp_original_pass")),
            minutes_from_original_appt=_safe(r.get("minutes_from_original_appt")),
            otp_time_original_pass=_safe(r.get("otp_time_original_pass")),
            stop_performance_status=r.get("stop_performance_status"),
            error_flag=r.get("error_flag", "N"),
            error_reason=r.get("error_reason", ""),
            telem_events=int(r["telem_events"]) if _safe(r.get("telem_events")) is not None else None,
            min_amb_temp=_safe(r.get("min_amb_temp")),
            max_amb_temp=_safe(r.get("max_amb_temp")),
            avg_amb_temp=_safe(r.get("avg_amb_temp")),
            min_s1=_safe(r.get("min_s1")),
            max_s1=_safe(r.get("max_s1")),
            door_open_events=int(r["door_open_events"]) if _safe(r.get("door_open_events")) is not None else None,
            trailer=_safe(r.get("trailer")),
        ))
        count += 1
    log.info("Upserted %d StopMaster rows", count)
    return count


def upsert_sap_segments(session: Session, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    txn_ids = df["transaction_id"].dropna().unique().tolist()
    if txn_ids:
        session.query(SapSegment).filter(SapSegment.transaction_id.in_(txn_ids)).delete(synchronize_session=False)
    count = 0
    for _, r in df.iterrows():
        session.add(SapSegment(
            transaction_id=_safe(r.get("transaction_id")),
            order_key=str(r.get("order_key", "")),
            document_number=str(r.get("document_number", "")),
            segment_number=str(r.get("segment_number", "")),
            carrier_bol=str(r.get("carrier_bol#", r.get("carrier_bol", ""))),
            shipper_name=str(r.get("shipper_name", "")),
            consignee_name=str(r.get("consignee_name", "")),
            cases_count=_safe(r.get("cases_#")),
            actual_weight=_safe(r.get("actual_weight")),
            tractor=str(r.get("tractor", "")),
            trailer=str(r.get("trailer", "")),
            sap_match_flag=r.get("sap_match_flag", "UNMATCHED"),
            time_diff_hours=_safe(r.get("time_diff_hours")),
            s_code=_safe(r.get("s_code")),
            arrive=_safe(r.get("arrive")),
        ))
        count += 1
    log.info("Inserted %d SapSegment rows", count)
    return count


def upsert_telemetry_stops(session: Session, df: pd.DataFrame) -> int:
    count = 0
    for _, r in df.iterrows():
        session.merge(TelemetryStop(
            transaction_id=r.get("transaction_id"),
            telem_events=int(r["telem_events"]) if _safe(r.get("telem_events")) is not None else None,
            min_amb_temp=_safe(r.get("min_amb_temp")),
            max_amb_temp=_safe(r.get("max_amb_temp")),
            avg_amb_temp=_safe(r.get("avg_amb_temp")),
            min_s1=_safe(r.get("min_s1")),
            max_s1=_safe(r.get("max_s1")),
            door_open_events=int(r["door_open_events"]) if _safe(r.get("door_open_events")) is not None else None,
        ))
        count += 1
    log.info("Upserted %d TelemetryStop rows", count)
    return count


def persist_errors(session: Session, errors: list[dict], run_id: str) -> int:
    for e in errors:
        session.add(ValidationError(
            transaction_id=e.get("transaction_id"),
            source=e.get("source", "UNKNOWN"),
            error_type=e.get("error_type", "SOFT"),
            error_reason=e.get("error_reason", ""),
            run_id=run_id,
        ))
    log.info("Persisted %d validation errors for run %s", len(errors), run_id)
    return len(errors)


def persist_run(session: Session, run: PipelineRun):
    session.merge(run)

# =========================================================================
# CRST ADAPTER
# =========================================================================

def load_crst(file_path: Path) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Load and process CRST data. Returns (processed_df, raw_df, header_row)."""
    preview = pd.read_excel(file_path, header=None)
    header_row = find_header_row(preview, CRST_HEADER_KEYWORDS, CRST_HEADER_MAX_ROWS)
    if header_row is None:
        raise ValueError(f"CRST header row not found in first {CRST_HEADER_MAX_ROWS} rows")

    log.info("Detected CRST header at Excel row %d", header_row + 1)
    raw = pd.read_excel(file_path, header=header_row)
    raw = normalize_columns(raw)

    missing = [c for c in CRST_REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"CRST missing required columns: {missing}")

    crst = raw.copy()

    # S-Code & stop type
    crst["s_code"] = crst["location_date"].apply(extract_s_code)
    crst["stop_type"] = np.where(crst["s_code"].notna(), "PLASMA_CENTER", "WAREHOUSE")

    # Parse datetimes
    for col in ["original_appt", "current_appt", "actual_arrival"]:
        if col in crst.columns:
            crst[col] = pd.to_datetime(crst[col], errors="coerce")

    # Arrival date & route day
    crst["arrival_date"] = crst["actual_arrival"].dt.date.astype(str)
    crst["route_day"] = crst["order_#"].astype(str) + "_" + crst["arrival_date"]

    # Stop sequencing
    crst = crst.sort_values(by=["order_#", "actual_arrival", "location_date"], na_position="last")
    crst["stop_seq"] = (crst.groupby("route_day").cumcount() + 1).astype(str).str.zfill(2)

    # Resolved appointment
    crst["resolved_appt"] = crst["current_appt"].combine_first(crst["original_appt"])

    # Transaction ID
    crst["transaction_id"] = (
        crst["order_#"].astype(str) + "_"
        + crst["arrival_date"].fillna("NO_DATE").astype(str) + "_"
        + crst["stop_seq"]
    )

    # Collapse duplicates
    crst = (
        crst.sort_values(by=["actual_arrival", "resolved_appt"], na_position="last")
        .groupby("transaction_id", as_index=False)
        .first()
    )

    # Re-assert types after collapse
    crst["actual_arrival"] = pd.to_datetime(crst["actual_arrival"], errors="coerce")
    crst["resolved_appt"] = crst["current_appt"].combine_first(crst["original_appt"])
    crst["error_flag"] = "N"
    crst["error_reason"] = ""

    log.info("CRST processed: %d stops", len(crst))
    return crst, raw, header_row

# =========================================================================
# SAP ADAPTER
# =========================================================================

def load_sap(file_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and process SAP data. Returns (processed_df, raw_df)."""
    raw = pd.read_excel(file_path)
    raw = normalize_columns(raw)

    missing = [c for c in SAP_REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"SAP missing required columns: {missing}")

    sap = raw.copy()
    sap["order_key"] = sap["document_number"].astype(str)
    sap["s_code"] = sap["shipper_search_term"].apply(extract_s_code)
    sap["arrive"] = pd.to_datetime(
        sap["pick_up_date"].astype(str) + " " + sap["arrive"].astype(str),
        errors="coerce",
    )
    sap["arrival_date"] = sap["arrive"].dt.date.astype(str)

    log.info("SAP processed: %d rows, S-Code coverage: %.1f%%", len(sap), sap["s_code"].notna().mean() * 100)
    return sap, raw

# =========================================================================
# TELEMETRY ADAPTER
# =========================================================================

def load_telemetry(file_path: Path, header_row: int = TELEMETRY_DEFAULT_HEADER_ROW) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load and process telemetry data. Returns (processed_df, raw_df)."""
    raw = pd.read_csv(file_path, header=header_row)
    raw.columns = [html.unescape(str(c)) for c in raw.columns]
    raw = normalize_columns(raw)

    tel = raw.copy()

    # Event timestamp
    if "date_&_time" in tel.columns:
        tel["event_ts"] = pd.to_datetime(tel["date_&_time"], errors="coerce")
    else:
        for candidate in ["datetime", "timestamp", "event_time"]:
            if candidate in tel.columns:
                tel["event_ts"] = pd.to_datetime(tel[candidate], errors="coerce")
                break
        else:
            tel["event_ts"] = pd.NaT

    # Trailer ID
    tel["trailer_id"] = tel["vehicle_name"].astype(str).str.strip() if "vehicle_name" in tel.columns else ""

    # Door flag
    tel["door_open_flag"] = (
        np.where(tel["door_1"].astype(str).str.upper() == "O", 1, 0)
        if "door_1" in tel.columns else 0
    )

    # Temperature columns
    for col in TELEMETRY_TEMP_COLUMNS:
        if col in tel.columns:
            tel[col] = pd.to_numeric(tel[col], errors="coerce")

    log.info("Telemetry processed: %d events, %d trailers", len(tel), tel["trailer_id"].nunique() if "trailer_id" in tel.columns else 0)
    return tel, raw

# =========================================================================
# OTP CALCULATOR
# =========================================================================

def calculate_otp(df: pd.DataFrame, tolerance: int = OTP_TOLERANCE_MINUTES) -> pd.DataFrame:
    crst = df.copy()

    if "resolved_appt" not in crst.columns:
        crst["resolved_appt"] = crst["current_appt"].combine_first(crst["original_appt"])

    crst["minutes_from_appt"] = (
        (crst["actual_arrival"] - crst["resolved_appt"]).dt.total_seconds() / 60
    ).round(1)

    crst["otp_day_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["resolved_appt"].isna(), np.nan,
        (crst["actual_arrival"].dt.date == crst["resolved_appt"].dt.date).astype(int),
    )
    crst["otp_time_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["resolved_appt"].isna(), np.nan,
        (crst["minutes_from_appt"].abs() <= tolerance).astype(int),
    )
    crst["otp_original_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["original_appt"].isna(), np.nan,
        (crst["actual_arrival"].dt.date == crst["original_appt"].dt.date).astype(int),
    )
    crst["minutes_from_original_appt"] = (
        (crst["actual_arrival"] - crst["original_appt"]).dt.total_seconds() / 60
    ).round(1)
    crst["otp_time_original_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["original_appt"].isna(), np.nan,
        (crst["minutes_from_original_appt"].abs() <= tolerance).astype(int),
    )

    def _status(row):
        if pd.isna(row["actual_arrival"]):
            return "Missing Arrival"
        if pd.isna(row["resolved_appt"]):
            return "Missing Appointment"
        if abs(row["minutes_from_appt"]) <= tolerance:
            return "On Time"
        if row["minutes_from_appt"] < 0:
            return "Early"
        return "Late"

    crst["stop_performance_status"] = crst.apply(_status, axis=1)
    log.info("OTP calculated: %d stops", len(crst))
    return crst

# =========================================================================
# SAP MATCHER
# =========================================================================

def match_sap_to_crst(crst_df: pd.DataFrame, sap_df: pd.DataFrame, max_hours: int = SAP_MATCH_MAX_HOURS) -> pd.DataFrame:
    if sap_df.empty or crst_df.empty:
        sap_out = sap_df.copy()
        sap_out["transaction_id"] = None
        sap_out["sap_match_flag"] = "UNMATCHED"
        sap_out["time_diff_hours"] = np.nan
        return sap_out

    crst_cols = ["transaction_id", "s_code", "actual_arrival", "stop_type"]
    candidates = sap_df.merge(crst_df[[c for c in crst_cols if c in crst_df.columns]], how="left", on="s_code")
    candidates = candidates[(candidates["stop_type"] == "PLASMA_CENTER") & (candidates["actual_arrival"].notna())]

    if candidates.empty:
        sap_out = sap_df.copy()
        sap_out["transaction_id"] = None
        sap_out["sap_match_flag"] = "UNMATCHED"
        sap_out["time_diff_hours"] = np.nan
        return sap_out

    candidates["time_diff_hours"] = (candidates["arrive"] - candidates["actual_arrival"]).abs().dt.total_seconds() / 3600

    enriched = (
        candidates.sort_values("time_diff_hours")
        .groupby(["document_number", "segment_number"], as_index=False)
        .first()
    )
    enriched = enriched[enriched["time_diff_hours"] <= max_hours]
    enriched["sap_match_flag"] = np.where(enriched["transaction_id"].isna(), "UNMATCHED", "MATCHED")

    matched_count = (enriched["sap_match_flag"] == "MATCHED").sum()
    log.info("SAP matching: %d matched out of %d (max %dh)", matched_count, len(sap_df), max_hours)
    return enriched

# =========================================================================
# TELEMETRY MATCHER
# =========================================================================

def match_telemetry_to_crst(crst_df: pd.DataFrame, telemetry_df: pd.DataFrame, window_minutes: int = TELEMETRY_WINDOW_MINUTES) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["transaction_id", "telem_events", "min_amb_temp", "max_amb_temp", "avg_amb_temp", "min_s1", "max_s1", "door_open_events"])
    if telemetry_df.empty or crst_df.empty:
        return empty

    crst = crst_df.copy()
    window = pd.Timedelta(minutes=window_minutes)

    crst["stop_start_ts"] = crst["actual_arrival"] - window
    if "actual_departure" in crst.columns:
        crst["stop_end_ts"] = np.where(
            crst["actual_departure"].notna(),
            crst["actual_departure"] + window,
            crst["actual_arrival"] + pd.Timedelta(hours=6),
        )
        crst["stop_end_ts"] = pd.to_datetime(crst["stop_end_ts"], errors="coerce")
    else:
        crst["stop_end_ts"] = crst["actual_arrival"] + pd.Timedelta(hours=6)

    join_cols = ["transaction_id", "trailer", "stop_start_ts", "stop_end_ts"]
    crst_sub = crst[[c for c in join_cols if c in crst.columns]].dropna(subset=["trailer"])
    if crst_sub.empty:
        return empty

    cands = telemetry_df.merge(crst_sub, how="inner", left_on="trailer_id", right_on="trailer")
    cands = cands[(cands["event_ts"] >= cands["stop_start_ts"]) & (cands["event_ts"] <= cands["stop_end_ts"])]
    if cands.empty:
        return empty

    # Aggregate
    agg = cands.groupby("transaction_id", as_index=False).agg(
        telem_events=("event_ts", "count"),
        min_amb_temp=("amb_temp", "min") if "amb_temp" in cands.columns else ("event_ts", "count"),
        max_amb_temp=("amb_temp", "max") if "amb_temp" in cands.columns else ("event_ts", "count"),
        avg_amb_temp=("amb_temp", "mean") if "amb_temp" in cands.columns else ("event_ts", "count"),
        min_s1=("s1", "min") if "s1" in cands.columns else ("event_ts", "count"),
        max_s1=("s1", "max") if "s1" in cands.columns else ("event_ts", "count"),
        door_open_events=("door_open_flag", "sum") if "door_open_flag" in cands.columns else ("event_ts", "count"),
    )

    log.info("Telemetry matching: %d stops with data", len(agg))
    return agg

# =========================================================================
# VALIDATION
# =========================================================================

def run_validation(crst_df: pd.DataFrame, sap_df: pd.DataFrame | None, telemetry_stop_df: pd.DataFrame | None) -> list[dict]:
    errors = []

    # Missing arrival (soft)
    for _, r in crst_df[crst_df["actual_arrival"].isna()].iterrows():
        errors.append({"transaction_id": r["transaction_id"], "source": "CRST", "error_type": "SOFT", "error_reason": "Missing Actual Arrival"})

    # Missing appointment (soft)
    for _, r in crst_df[crst_df["resolved_appt"].isna()].iterrows():
        errors.append({"transaction_id": r["transaction_id"], "source": "CRST", "error_type": "SOFT", "error_reason": "Missing Appointment"})

    # Missing S-Code for plasma (soft)
    mask = (crst_df["stop_type"] == "PLASMA_CENTER") & (crst_df["s_code"].isna())
    for _, r in crst_df[mask].iterrows():
        errors.append({"transaction_id": r["transaction_id"], "source": "CRST", "error_type": "SOFT", "error_reason": "Missing S_Code for plasma stop"})

    # Duplicate transaction IDs (hard)
    dups = crst_df["transaction_id"].duplicated(keep=False)
    for _, r in crst_df[dups].iterrows():
        errors.append({"transaction_id": r["transaction_id"], "source": "CRST", "error_type": "HARD", "error_reason": "Duplicate TransactionID"})

    # SAP match rate (warning)
    if sap_df is not None and not sap_df.empty and "sap_match_flag" in sap_df.columns:
        rate = (sap_df["sap_match_flag"] == "MATCHED").mean()
        if rate < 0.5:
            errors.append({"transaction_id": None, "source": "SAP", "error_type": "WARNING", "error_reason": f"SAP match rate {rate:.1%} below 50%"})

    # Telemetry coverage (warning)
    if telemetry_stop_df is None or telemetry_stop_df.empty:
        errors.append({"transaction_id": None, "source": "TELEMETRY", "error_type": "WARNING", "error_reason": "No telemetry data matched to any stops"})

    # Apply error flags to DataFrame
    error_map = {}
    for e in errors:
        tid = e.get("transaction_id")
        if tid and tid not in error_map:
            error_map[tid] = e["error_reason"]

    crst_df["error_flag"] = "N"
    crst_df["error_reason"] = ""
    for tid, reason in error_map.items():
        crst_df.loc[crst_df["transaction_id"] == tid, "error_flag"] = "Y"
        crst_df.loc[crst_df["transaction_id"] == tid, "error_reason"] = reason

    log.info("Validation: %d errors (HARD=%d, SOFT=%d, WARNING=%d)",
             len(errors),
             sum(1 for e in errors if e["error_type"] == "HARD"),
             sum(1 for e in errors if e["error_type"] == "SOFT"),
             sum(1 for e in errors if e["error_type"] == "WARNING"))
    return errors

# =========================================================================
# EXCEL EXPORT
# =========================================================================

def export_to_excel(output_path: Path, run_metadata: pd.DataFrame, crst_raw: pd.DataFrame,
                    sap_raw: pd.DataFrame | None, telemetry_raw: pd.DataFrame | None,
                    stop_master: pd.DataFrame, sap_segment: pd.DataFrame | None,
                    telemetry_stop: pd.DataFrame | None):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    info_sheet = pd.DataFrame({
        "Section": ["Purpose", "Stop Definition", "OTP Buckets", "SAP Integration", "Warehouse Stops", "Error Handling", "How to Use"],
        "Description": [
            "Stop-level transportation performance from CRST and SAP data.",
            "Each row = one physical stop after collapsing duplicates.",
            "OTP: day-level and time-window against current and original appointments.",
            "SAP joined to stops for shipment context. SAP does not drive OTP.",
            "Warehouse stops may not have S-Codes or SAP matches. Expected.",
            "Errors flag data gaps. Not all errors indicate failure.",
            "STOP_MASTER for performance, SAP_SEGMENT for shipment detail.",
        ],
    })
    error_ref = pd.DataFrame({
        "Error_Reason": ["Missing Actual Arrival", "Missing Appointment", "Missing S_Code for plasma stop", "Duplicate TransactionID"],
        "Severity": ["Informational", "Informational", "Data Quality Issue", "Critical"],
        "Action": ["Review source data", "Review scheduling data", "Investigate master data", "Stop and investigate"],
    })

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        run_metadata.to_excel(writer, sheet_name="RUN_METADATA", index=False)
        crst_raw.to_excel(writer, sheet_name="CRST_RAW", index=False)
        if sap_raw is not None:
            sap_raw.to_excel(writer, sheet_name="SAP_RAW", index=False)
        if telemetry_raw is not None:
            telemetry_raw.to_excel(writer, sheet_name="TELEMETRY_RAW", index=False)
        stop_master.to_excel(writer, sheet_name="STOP_MASTER", index=False)
        if sap_segment is not None:
            sap_segment.to_excel(writer, sheet_name="SAP_SEGMENT", index=False)
        if telemetry_stop is not None:
            telemetry_stop.to_excel(writer, sheet_name="TRAILER_TELEMETRY_STOP", index=False)
        info_sheet.to_excel(writer, sheet_name="INFO", index=False)
        error_ref.to_excel(writer, sheet_name="ERROR_REFERENCE", index=False)

    log.info("Excel written: %s", output_path)

# =========================================================================
# PIPELINE ORCHESTRATOR
# =========================================================================

def run_pipeline(source_files: dict[str, str | Path], export_excel: bool = True, output_filename: str | None = None) -> dict:
    """Run the full pipeline end to end."""
    setup_logging()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_start = datetime.now(timezone.utc)

    log.info("=" * 60)
    log.info("Pipeline run %s started | Sources: %s", run_id, list(source_files.keys()))

    engine = get_engine()
    init_db(engine)

    raw_data = {}
    records_read = {}

    try:
        # --- Load sources ---
        if "crst" not in source_files:
            raise ValueError("CRST source file is required")

        crst_df, crst_raw, header_row = load_crst(Path(source_files["crst"]))
        raw_data["crst"] = crst_raw
        records_read["crst"] = len(crst_raw)

        sap_df, sap_segment, sap_raw = None, None, None
        sap_match_rate = 0.0
        if "sap" in source_files:
            sap_df, sap_raw = load_sap(Path(source_files["sap"]))
            raw_data["sap"] = sap_raw
            records_read["sap"] = len(sap_raw)

        telemetry_df, telemetry_raw = None, None
        if "telemetry" in source_files:
            telemetry_df, telemetry_raw = load_telemetry(Path(source_files["telemetry"]))
            raw_data["telemetry"] = telemetry_raw
            records_read["telemetry"] = len(telemetry_raw)

        # --- OTP ---
        crst_df = calculate_otp(crst_df)

        # --- Matching ---
        telemetry_stop_df = None
        if sap_df is not None:
            sap_segment = match_sap_to_crst(crst_df, sap_df)
            matched = (sap_segment["sap_match_flag"] == "MATCHED").sum()
            sap_match_rate = matched / len(sap_df) if len(sap_df) > 0 else 0

        telemetry_coverage = 0.0
        if telemetry_df is not None:
            telemetry_stop_df = match_telemetry_to_crst(crst_df, telemetry_df)
            if not telemetry_stop_df.empty:
                crst_df = crst_df.merge(telemetry_stop_df, on="transaction_id", how="left")
                telemetry_coverage = telemetry_stop_df["transaction_id"].nunique() / crst_df["transaction_id"].nunique()

        # --- Validation ---
        errors = run_validation(crst_df, sap_segment, telemetry_stop_df)

        # --- Persist to SQLite ---
        with get_session(engine) as session:
            upsert_stops(session, crst_df)
            if sap_segment is not None:
                upsert_sap_segments(session, sap_segment)
            if telemetry_stop_df is not None:
                upsert_telemetry_stops(session, telemetry_stop_df)
            persist_errors(session, errors, run_id)
            persist_run(session, PipelineRun(
                run_id=run_id, run_timestamp=run_start,
                source_files=json.dumps({k: str(v) for k, v in source_files.items()}),
                records_read=json.dumps(records_read),
                records_output=json.dumps({"stop_master": len(crst_df)}),
                match_rates=json.dumps({"sap": f"{sap_match_rate:.2%}", "telemetry": f"{telemetry_coverage:.2%}"}),
                status="SUCCESS",
            ))

        # --- Excel export ---
        output_path = None
        if export_excel:
            fname = output_filename or f"Trans_KPI_Validation_{run_id}.xlsx"
            output_path = OUTPUT_DIR / fname

            meta_rows = [
                ("Run_ID", run_id),
                ("Run_Timestamp", run_start.strftime("%Y-%m-%d %H:%M:%S")),
                ("Source_CRST_File", str(source_files.get("crst", "N/A"))),
                ("CRST_Header_Row", str(header_row + 1)),
                ("CRST_Rows_Read", str(records_read.get("crst", 0))),
                ("CRST_Stops_Final", str(len(crst_df))),
                ("OTP_Tolerance_Minutes", str(OTP_TOLERANCE_MINUTES)),
            ]
            if "sap" in source_files:
                meta_rows += [("Source_SAP_File", str(source_files["sap"])), ("SAP_Rows_Read", str(records_read.get("sap", 0))), ("SAP_Match_Rate", f"{sap_match_rate:.2%}")]
            if "telemetry" in source_files:
                meta_rows += [("Source_Telemetry_File", str(source_files["telemetry"])), ("Telemetry_Rows_Read", str(records_read.get("telemetry", 0))), ("Telemetry_Coverage", f"{telemetry_coverage:.2%}")]
            meta_rows.append(("Stops_With_Errors", str(sum(1 for e in errors if e.get("transaction_id")))))

            export_to_excel(
                output_path, pd.DataFrame(meta_rows, columns=["Field", "Value"]),
                raw_data.get("crst", pd.DataFrame()), raw_data.get("sap"), raw_data.get("telemetry"),
                crst_df, sap_segment, telemetry_stop_df,
            )

        log.info("Pipeline %s completed successfully", run_id)
        return {
            "run_id": run_id, "status": "SUCCESS",
            "output_path": str(output_path) if output_path else None,
            "records_read": records_read, "stops_final": len(crst_df),
            "sap_match_rate": f"{sap_match_rate:.2%}",
            "telemetry_coverage": f"{telemetry_coverage:.2%}",
            "errors_total": len(errors),
            "errors_hard": sum(1 for e in errors if e["error_type"] == "HARD"),
            "errors_soft": sum(1 for e in errors if e["error_type"] == "SOFT"),
            "errors_warning": sum(1 for e in errors if e["error_type"] == "WARNING"),
        }

    except Exception as exc:
        log.error("Pipeline %s FAILED: %s", run_id, exc, exc_info=True)
        try:
            with get_session(engine) as session:
                persist_run(session, PipelineRun(
                    run_id=run_id, run_timestamp=run_start,
                    source_files=json.dumps({k: str(v) for k, v in source_files.items()}),
                    records_read=json.dumps(records_read), status="FAILED", error_message=str(exc),
                ))
        except Exception:
            pass
        raise

# =========================================================================
# STREAMLIT DASHBOARD
# =========================================================================

def _is_streamlit():
    """Check if we're running inside Streamlit."""
    try:
        import streamlit as st
        return bool(st.runtime.exists())
    except Exception:
        return False


def dashboard():
    import streamlit as st
    import plotly.express as px

    st.set_page_config(page_title="DataScrubb", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

    # --- DB helper ---
    def load_table(table: str) -> pd.DataFrame:
        if not DB_PATH.exists():
            return pd.DataFrame()
        try:
            engine = get_engine()
            return pd.read_sql(f"SELECT * FROM {table}", engine)
        except Exception:
            return pd.DataFrame()

    # --- Sidebar nav ---
    st.sidebar.title("DataScrubb")
    st.sidebar.markdown("---")
    page = st.sidebar.radio("Navigation", ["Overview", "Load Data", "Stop Explorer", "OTP Analysis", "SAP Matching", "Telemetry", "Validation Report"])
    st.sidebar.markdown("---")
    st.sidebar.caption("Transportation Data Integration & Validation")

    # --- Shared filter widgets ---
    def sidebar_filters(df):
        with st.sidebar:
            st.subheader("Filters")
            # Date range
            start_date, end_date = None, None
            if "arrival_date" in df.columns and not df["arrival_date"].isna().all():
                dates = pd.to_datetime(df["arrival_date"], errors="coerce").dropna()
                if not dates.empty:
                    c1, c2 = st.columns(2)
                    start_date = c1.date_input("Start", value=dates.min().date())
                    end_date = c2.date_input("End", value=dates.max().date())

            s_codes = []
            if "s_code" in df.columns:
                opts = sorted(df["s_code"].dropna().unique().tolist())
                s_codes = st.multiselect("S-Code", opts)

            stop_type = st.radio("Stop Type", ["All", "PLASMA_CENTER", "WAREHOUSE"], horizontal=True)
            order_search = st.text_input("Search Order #")
            perf = []
            if "stop_performance_status" in df.columns:
                perf = st.multiselect("Performance", sorted(df["stop_performance_status"].dropna().unique().tolist()))

        # Apply
        filtered = df.copy()
        if start_date and end_date and "arrival_date" in filtered.columns:
            d = pd.to_datetime(filtered["arrival_date"], errors="coerce")
            filtered = filtered[(d >= pd.Timestamp(start_date)) & (d <= pd.Timestamp(end_date))]
        if s_codes:
            filtered = filtered[filtered["s_code"].isin(s_codes)]
        if stop_type != "All" and "stop_type" in filtered.columns:
            filtered = filtered[filtered["stop_type"] == stop_type]
        if order_search and "order_number" in filtered.columns:
            filtered = filtered[filtered["order_number"].astype(str).str.contains(order_search, case=False, na=False)]
        if perf and "stop_performance_status" in filtered.columns:
            filtered = filtered[filtered["stop_performance_status"].isin(perf)]
        return filtered

    def download_btns(df, name="export"):
        c1, c2 = st.columns(2)
        c1.download_button("Download CSV", df.to_csv(index=False).encode(), f"{name}.csv", "text/csv")
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False)
        c2.download_button("Download Excel", buf.getvalue(), f"{name}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # =====================================================================
    # PAGE: Overview
    # =====================================================================
    if page == "Overview":
        st.header("Overview")
        df = load_table("stop_master")
        if df.empty:
            st.info("No data loaded. Go to **Load Data**.")
            return

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Stops", f"{len(df):,}")
        otp = df["otp_time_pass"].mean() * 100 if df["otp_time_pass"].notna().any() else 0
        c2.metric("OTP (Time Window)", f"{otp:.1f}%")
        err = (df["error_flag"] == "Y").sum()
        c3.metric("Stops with Errors", f"{err:,}")

        sap = load_table("sap_segment")
        sap_rate = (sap["sap_match_flag"] == "MATCHED").mean() * 100 if not sap.empty and "sap_match_flag" in sap.columns else 0
        c4.metric("SAP Match Rate", f"{sap_rate:.1f}%")

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if "stop_performance_status" in df.columns:
                counts = df["stop_performance_status"].value_counts().reset_index()
                counts.columns = ["status", "count"]
                fig = px.pie(counts, names="status", values="count", title="Performance Distribution",
                             color="status", color_discrete_map={"On Time": "#22c55e", "Late": "#ef4444", "Early": "#3b82f6", "Missing Arrival": "#9ca3af", "Missing Appointment": "#6b7280"})
                st.plotly_chart(fig, use_container_width=True)
        with col2:
            if "arrival_date" in df.columns:
                daily = df.assign(d=pd.to_datetime(df["arrival_date"], errors="coerce")).dropna(subset=["d"]).groupby("d").size().reset_index(name="stops")
                fig = px.bar(daily, x="d", y="stops", title="Stops Per Day", labels={"d": "Date", "stops": "Count"})
                st.plotly_chart(fig, use_container_width=True)

        if "arrival_date" in df.columns:
            daily_otp = df.assign(d=pd.to_datetime(df["arrival_date"], errors="coerce")).dropna(subset=["d"]).groupby("d")["otp_time_pass"].mean().reset_index()
            daily_otp["otp_time_pass"] = (daily_otp["otp_time_pass"] * 100).round(1)
            fig = px.line(daily_otp, x="d", y="otp_time_pass", title="OTP Trend", labels={"d": "Date", "otp_time_pass": "OTP %"}, markers=True)
            fig.update_layout(yaxis_range=[0, 105])
            st.plotly_chart(fig, use_container_width=True)

    # =====================================================================
    # PAGE: Load Data
    # =====================================================================
    elif page == "Load Data":
        st.header("Load Data")
        st.markdown("Upload files and run the pipeline.")

        c1, c2, c3 = st.columns(3)
        crst_file = c1.file_uploader("**CRST** (Required)", type=["xlsx", "xls"], key="crst")
        sap_file = c2.file_uploader("**SAP** (Optional)", type=["xlsx", "xls"], key="sap")
        tel_file = c3.file_uploader("**Telemetry** (Optional)", type=["csv"], key="tel")

        st.markdown("---")
        out_name = st.text_input("Output filename (optional)", placeholder="Trans_KPI_Validation.xlsx")

        if st.button("Run Pipeline", type="primary", disabled=(crst_file is None)):
            import tempfile
            with st.spinner("Running pipeline..."):
                prog = st.progress(0, "Saving files...")
                with tempfile.TemporaryDirectory() as tmp:
                    tmp = Path(tmp)
                    sources = {}
                    p = tmp / crst_file.name; p.write_bytes(crst_file.getvalue()); sources["crst"] = p
                    prog.progress(10, "CRST saved")
                    if sap_file:
                        p = tmp / sap_file.name; p.write_bytes(sap_file.getvalue()); sources["sap"] = p
                        prog.progress(20, "SAP saved")
                    if tel_file:
                        p = tmp / tel_file.name; p.write_bytes(tel_file.getvalue()); sources["telemetry"] = p
                        prog.progress(30, "Telemetry saved")
                    prog.progress(40, "Processing...")

                    try:
                        result = run_pipeline(sources, output_filename=out_name or None)
                        prog.progress(100, "Done!")
                        st.success(f"Run **{result['run_id']}** completed!")
                        mc1, mc2, mc3, mc4 = st.columns(4)
                        mc1.metric("Stops", result["stops_final"])
                        mc2.metric("SAP Match", result["sap_match_rate"])
                        mc3.metric("Telemetry", result["telemetry_coverage"])
                        mc4.metric("Errors", result["errors_total"])
                        if result.get("output_path"):
                            st.info(f"Excel: `{result['output_path']}`")
                        if result["errors_hard"] > 0:
                            st.error("Hard errors detected — check Validation Report.")
                    except Exception as e:
                        prog.progress(100, "Failed")
                        st.error(f"Pipeline failed: {e}")
                        st.exception(e)
        elif crst_file is None:
            st.info("Upload at least the CRST file to start.")

    # =====================================================================
    # PAGE: Stop Explorer
    # =====================================================================
    elif page == "Stop Explorer":
        st.header("Stop Explorer")
        df = load_table("stop_master")
        if df.empty:
            st.info("No data loaded. Go to **Load Data**."); return

        filtered = sidebar_filters(df)
        st.markdown(f"**{len(filtered):,}** stops (of {len(df):,})")

        cols = [c for c in ["transaction_id", "order_number", "s_code", "stop_type", "arrival_date",
                            "actual_arrival", "resolved_appt", "minutes_from_appt",
                            "stop_performance_status", "otp_time_pass", "otp_day_pass",
                            "error_flag", "error_reason"] if c in filtered.columns]
        st.dataframe(filtered[cols], use_container_width=True, height=600)
        st.markdown("---")
        download_btns(filtered, "stop_master_filtered")

    # =====================================================================
    # PAGE: OTP Analysis
    # =====================================================================
    elif page == "OTP Analysis":
        st.header("OTP Analysis")
        df = load_table("stop_master")
        if df.empty:
            st.info("No data loaded. Go to **Load Data**."); return

        filtered = sidebar_filters(df)
        otp_col = st.selectbox("OTP Metric", [
            ("Time Window (Current)", "otp_time_pass"),
            ("Same Day (Current)", "otp_day_pass"),
            ("Time Window (Original)", "otp_time_original_pass"),
            ("Same Day (Original)", "otp_original_pass"),
        ], format_func=lambda x: x[0])[1]

        c1, c2, c3 = st.columns(3)
        pct = filtered[otp_col].mean() * 100 if filtered[otp_col].notna().any() else 0
        c1.metric("OTP Rate", f"{pct:.1f}%")
        c2.metric("Evaluable", f"{filtered[otp_col].notna().sum():,}")
        c3.metric("Total", f"{len(filtered):,}")

        st.markdown("---")
        cc1, cc2 = st.columns(2)
        with cc1:
            by_sc = filtered[filtered["s_code"].notna()].groupby("s_code")[otp_col].mean().sort_values().reset_index()
            by_sc[otp_col] = (by_sc[otp_col] * 100).round(1)
            fig = px.bar(by_sc, x=otp_col, y="s_code", orientation="h", title="OTP by S-Code",
                         color=otp_col, color_continuous_scale=["#ef4444", "#f59e0b", "#22c55e"], range_color=[0, 100])
            fig.update_layout(height=max(300, len(by_sc) * 25), showlegend=False)
            st.plotly_chart(fig, use_container_width=True)
        with cc2:
            if "stop_performance_status" in filtered.columns:
                counts = filtered["stop_performance_status"].value_counts().reset_index(); counts.columns = ["status", "count"]
                fig = px.pie(counts, names="status", values="count", title="Performance Distribution",
                             color="status", color_discrete_map={"On Time": "#22c55e", "Late": "#ef4444", "Early": "#3b82f6", "Missing Arrival": "#9ca3af"})
                st.plotly_chart(fig, use_container_width=True)

        if "minutes_from_appt" in filtered.columns:
            data = filtered["minutes_from_appt"].dropna()
            fig = px.histogram(data, nbins=50, title="Minutes from Appointment", labels={"value": "Minutes", "count": "Stops"})
            fig.add_vline(x=-120, line_dash="dash", line_color="orange", annotation_text="-120m")
            fig.add_vline(x=120, line_dash="dash", line_color="orange", annotation_text="+120m")
            fig.add_vline(x=0, line_dash="solid", line_color="green", annotation_text="On Time")
            st.plotly_chart(fig, use_container_width=True)

    # =====================================================================
    # PAGE: SAP Matching
    # =====================================================================
    elif page == "SAP Matching":
        st.header("SAP Matching")
        sap = load_table("sap_segment")
        if sap.empty:
            st.info("No SAP data. Upload via **Load Data**."); return

        total = len(sap)
        matched = (sap["sap_match_flag"] == "MATCHED").sum()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Segments", f"{total:,}")
        c2.metric("Matched", f"{matched:,}")
        c3.metric("Unmatched", f"{total - matched:,}")
        c4.metric("Match Rate", f"{matched / total * 100:.1f}%" if total else "0%")

        st.markdown("---")
        t1, t2 = st.tabs(["Matched", "Unmatched"])
        with t1:
            m = sap[sap["sap_match_flag"] == "MATCHED"]
            if not m.empty and "time_diff_hours" in m.columns:
                fig = px.histogram(m["time_diff_hours"].dropna(), nbins=30, title="Time Difference (hours)", labels={"value": "Hours"})
                st.plotly_chart(fig, use_container_width=True)
            cols = [c for c in ["transaction_id", "document_number", "segment_number", "shipper_name", "s_code", "time_diff_hours"] if c in m.columns]
            st.dataframe(m[cols] if not m.empty else m, use_container_width=True, height=400)
        with t2:
            u = sap[sap["sap_match_flag"] != "MATCHED"]
            cols = [c for c in ["document_number", "segment_number", "shipper_name", "s_code", "arrive"] if c in u.columns]
            st.dataframe(u[cols] if not u.empty else u, use_container_width=True, height=400)
            if not u.empty:
                download_btns(u, "sap_unmatched")

    # =====================================================================
    # PAGE: Telemetry
    # =====================================================================
    elif page == "Telemetry":
        st.header("Telemetry")
        stops = load_table("stop_master")
        if stops.empty:
            st.info("No data. Go to **Load Data**."); return

        total = len(stops)
        with_tel = stops["telem_events"].notna().sum() if "telem_events" in stops.columns else 0
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Stops", f"{total:,}")
        c2.metric("With Telemetry", f"{with_tel:,}")
        c3.metric("Coverage", f"{with_tel / total * 100:.1f}%" if total else "0%")

        has = stops[stops["telem_events"].notna() & (stops["telem_events"] > 0)] if "telem_events" in stops.columns else pd.DataFrame()
        if has.empty:
            st.warning("No telemetry matched yet."); return

        st.markdown("---")
        cc1, cc2 = st.columns(2)
        with cc1:
            if "avg_amb_temp" in has.columns:
                fig = px.histogram(has["avg_amb_temp"].dropna(), nbins=30, title="Avg Ambient Temp Distribution")
                st.plotly_chart(fig, use_container_width=True)
        with cc2:
            if "door_open_events" in has.columns:
                fig = px.histogram(has["door_open_events"].dropna(), nbins=20, title="Door Open Events per Stop")
                st.plotly_chart(fig, use_container_width=True)

        cols = [c for c in ["transaction_id", "order_number", "s_code", "arrival_date", "telem_events", "min_amb_temp", "max_amb_temp", "avg_amb_temp", "door_open_events"] if c in has.columns]
        st.dataframe(has[cols], use_container_width=True, height=400)
        download_btns(has, "telemetry_stops")

    # =====================================================================
    # PAGE: Validation Report
    # =====================================================================
    elif page == "Validation Report":
        st.header("Validation Report")
        errors = load_table("validation_error")
        runs = load_table("pipeline_run")

        if errors.empty and runs.empty:
            st.info("No runs yet. Go to **Load Data**."); return

        if not runs.empty:
            st.subheader("Run History")
            cols = [c for c in ["run_id", "run_timestamp", "status", "error_message"] if c in runs.columns]
            st.dataframe(runs[cols].sort_values("run_timestamp", ascending=False) if "run_timestamp" in runs.columns else runs[cols], use_container_width=True, height=200)

        if errors.empty:
            st.success("No validation errors!"); return

        st.markdown("---")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(errors))
        c2.metric("Hard", len(errors[errors["error_type"] == "HARD"]))
        c3.metric("Soft", len(errors[errors["error_type"] == "SOFT"]))
        c4.metric("Warnings", len(errors[errors["error_type"] == "WARNING"]))

        cc1, cc2 = st.columns(2)
        with cc1:
            by_r = errors.groupby("error_reason").size().reset_index(name="count").sort_values("count", ascending=True)
            fig = px.bar(by_r, x="count", y="error_reason", orientation="h", title="Errors by Reason")
            st.plotly_chart(fig, use_container_width=True)
        with cc2:
            by_t = errors.groupby(["error_type", "source"]).size().reset_index(name="count")
            fig = px.bar(by_t, x="error_type", y="count", color="source", title="By Type & Source", barmode="group")
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Error Detail")
        if "run_id" in errors.columns:
            run_opts = ["All"] + errors["run_id"].unique().tolist()
            sel = st.selectbox("Filter by Run", run_opts)
            if sel != "All":
                errors = errors[errors["run_id"] == sel]
        cols = [c for c in ["transaction_id", "source", "error_type", "error_reason", "run_id", "created_at"] if c in errors.columns]
        st.dataframe(errors[cols], use_container_width=True, height=400)
        download_btns(errors, "validation_errors")

# =========================================================================
# ENTRY POINT
# =========================================================================

if __name__ == "__main__" and not _is_streamlit():
    # CLI mode
    parser = argparse.ArgumentParser(description="DataScrubb Pipeline")
    parser.add_argument("--crst", required=True, help="Path to CRST Excel file")
    parser.add_argument("--sap", help="Path to SAP Excel file")
    parser.add_argument("--telemetry", help="Path to telemetry CSV file")
    parser.add_argument("--output", help="Output Excel filename")
    parser.add_argument("--no-excel", action="store_true", help="Skip Excel export")
    args = parser.parse_args()

    sources = {"crst": args.crst}
    if args.sap:
        sources["sap"] = args.sap
    if args.telemetry:
        sources["telemetry"] = args.telemetry

    result = run_pipeline(sources, export_excel=not args.no_excel, output_filename=args.output)
    print("\n" + "=" * 50)
    print(f"Run ID:              {result['run_id']}")
    print(f"Status:              {result['status']}")
    print(f"Stops:               {result['stops_final']}")
    print(f"SAP Match Rate:      {result['sap_match_rate']}")
    print(f"Telemetry Coverage:  {result['telemetry_coverage']}")
    print(f"Errors:              {result['errors_total']} (Hard={result['errors_hard']}, Soft={result['errors_soft']}, Warn={result['errors_warning']})")
    if result.get("output_path"):
        print(f"Output:              {result['output_path']}")
    print("=" * 50)

elif _is_streamlit():
    dashboard()
