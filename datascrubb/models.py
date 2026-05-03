"""SQLAlchemy ORM models for the DataScrubb database."""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class StopMaster(Base):
    """One row per physical stop after collapsing operational duplicates."""

    __tablename__ = "stop_master"

    transaction_id = Column(String, primary_key=True)
    order_number = Column(String, index=True)
    location_date = Column(String)
    s_code = Column(String, index=True)
    stop_type = Column(String)  # PLASMA_CENTER or WAREHOUSE (legacy binary)
    stop_class = Column(String, index=True)  # PLASMA_CENTER / DISTRIBUTION_CENTER / INTERNAL_BASE / OTHER

    # Dates / times
    arrival_date = Column(String)
    actual_arrival = Column(DateTime)
    actual_departure = Column(DateTime)
    dwell_minutes = Column(Float)
    original_appt = Column(DateTime)
    current_appt = Column(DateTime)
    resolved_appt = Column(DateTime)
    bol = Column(String)
    truck = Column(String)
    drivers = Column(String)
    dm = Column(String)
    city = Column(String)
    state = Column(String)
    late_code = Column(String)
    route_name = Column(String, index=True)
    customer = Column(String, index=True)
    stop_direction = Column(String)  # PU or SO from raw CRST
    tender_cases = Column(Float)
    current_cases = Column(Float)
    cases_variance = Column(Float)
    sum_of_weight = Column(Float)
    loaded_at_stop = Column(Integer)  # 1 if trailer had product during this stop

    # Trailer capacity / fill % (config > observed > default)
    cap_max_cases = Column(Float)
    cap_max_weight_lbs = Column(Float)
    capacity_source = Column(String)  # "config" / "observed" / "default"
    fill_pct_cases = Column(Float)
    fill_pct_weight = Column(Float)

    # Route / sequencing
    route_day = Column(String, index=True)
    stop_seq = Column(String)

    # OTP
    minutes_from_appt = Column(Float)
    otp_day_pass = Column(Float)
    otp_time_pass = Column(Float)
    otp_original_pass = Column(Float)
    minutes_from_original_appt = Column(Float)
    otp_time_original_pass = Column(Float)
    stop_performance_status = Column(String)

    # Error tracking
    error_flag = Column(String, default="N")
    error_reason = Column(String, default="")

    # Telemetry summary (merged from TelemetryStop)
    telem_events = Column(Integer)
    min_amb_temp = Column(Float)
    max_amb_temp = Column(Float)
    avg_amb_temp = Column(Float)
    min_s1 = Column(Float)
    max_s1 = Column(Float)
    door_open_events = Column(Integer)

    # Trailer info (from CRST)
    trailer = Column(String)

    # Metadata
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    sap_segments = relationship("SapSegment", back_populates="stop")
    telemetry_stop = relationship("TelemetryStop", back_populates="stop", uselist=False)
    validation_errors = relationship("ValidationError", back_populates="stop")


class SapSegment(Base):
    """SAP shipment segment matched to a CRST stop."""

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
    sap_match_flag = Column(String)  # MATCHED or UNMATCHED
    time_diff_hours = Column(Float)
    s_code = Column(String)
    arrive = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stop = relationship("StopMaster", back_populates="sap_segments")


class TelemetryStop(Base):
    """Stop-level telemetry aggregation (one row per transaction_id)."""

    __tablename__ = "telemetry_stop"

    transaction_id = Column(String, ForeignKey("stop_master.transaction_id"), primary_key=True)
    telem_events = Column(Integer)
    min_amb_temp = Column(Float)
    max_amb_temp = Column(Float)
    avg_amb_temp = Column(Float)
    min_s1 = Column(Float)
    max_s1 = Column(Float)
    avg_tl1 = Column(Float)
    min_tl1 = Column(Float)
    max_tl1 = Column(Float)
    door_open_events = Column(Integer)
    door_open_while_moving = Column(Integer)
    max_speed = Column(Float)
    avg_speed = Column(Float)
    idle_minutes = Column(Float)
    reefer_runtime_minutes = Column(Float)
    reefer_gallons = Column(Float)
    reefer_fuel_cost = Column(Float)
    alarm_events = Column(Integer)
    min_battery = Column(Float)
    avg_battery = Column(Float)
    max_engine_hours = Column(Float)
    max_total_hours = Column(Float)
    setpoint_changes = Column(Integer)
    avg_da_ra_delta = Column(Float)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stop = relationship("StopMaster", back_populates="telemetry_stop")


class ValidationError(Base):
    """Individual validation error flagged during a pipeline run."""

    __tablename__ = "validation_error"

    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(String, ForeignKey("stop_master.transaction_id"), nullable=True)
    source = Column(String)  # CRST, SAP, TELEMETRY
    error_type = Column(String)  # SOFT, HARD, WARNING
    error_reason = Column(String)
    run_id = Column(String, ForeignKey("pipeline_run.run_id"), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    stop = relationship("StopMaster", back_populates="validation_errors")
    pipeline_run = relationship("PipelineRun", back_populates="validation_errors")


class PipelineRun(Base):
    """Metadata for each pipeline execution."""

    __tablename__ = "pipeline_run"

    run_id = Column(String, primary_key=True)
    run_timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    source_files = Column(Text)  # JSON string
    records_read = Column(Text)  # JSON string
    records_output = Column(Text)  # JSON string
    match_rates = Column(Text)  # JSON string
    status = Column(String, default="RUNNING")  # RUNNING, SUCCESS, FAILED
    error_message = Column(Text, nullable=True)

    validation_errors = relationship("ValidationError", back_populates="pipeline_run")


class BillingSnapshot(Base):
    """One row per PRO# per billing week from the M3PL invoice files."""

    __tablename__ = "billing_snapshot"

    pro_number = Column(String, primary_key=True)
    billing_week_end = Column(DateTime, primary_key=True)

    legacy_route = Column(String, index=True)
    lane = Column(String, index=True)

    crst_miles = Column(Float)
    stop_count = Column(Float)
    team_miles = Column(Float)
    solo_miles = Column(Float)
    team_deficit_miles = Column(Float)
    solo_deficit_miles = Column(Float)
    tolls = Column(Float)

    stop_rate = Column(Float)
    team_rate = Column(Float)
    solo_rate = Column(Float)
    team_deficit_rate = Column(Float)
    solo_deficit_rate = Column(Float)

    billed_miles_amount = Column(Float)
    billed_stops_amount = Column(Float)
    billed_deficit_amount = Column(Float)
    billed_amount = Column(Float)

    tractor = Column(String)
    trailer = Column(String)

    source_file = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
