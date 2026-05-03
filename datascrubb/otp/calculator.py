"""On-Time Performance (OTP) calculation logic.

Pure functions with no side effects. Receives a CRST DataFrame and returns it
with OTP columns added.

OTP Buckets:
- otp_day_pass:           Did arrival happen on the same day as the (resolved) appointment?
- otp_time_pass:          Was arrival within ±tolerance minutes of the resolved appointment?
- otp_original_pass:      Did arrival happen on the same day as the original appointment?
- otp_time_original_pass: Was arrival within ±tolerance of the original appointment?
- stop_performance_status: Human-readable label (On Time / Early / Late / Missing)
"""

import logging

import numpy as np
import pandas as pd

from datascrubb.constants import OTP_TOLERANCE_MINUTES

logger = logging.getLogger("datascrubb.otp")


def calculate_otp(df: pd.DataFrame, tolerance_minutes: int = OTP_TOLERANCE_MINUTES) -> pd.DataFrame:
    """Add all OTP columns to a CRST DataFrame.

    Expects columns: actual_arrival, resolved_appt, original_appt
    (all as datetime). Returns a new DataFrame with OTP columns added.
    """
    crst = df.copy()

    # Ensure resolved_appt exists
    if "resolved_appt" not in crst.columns:
        crst["resolved_appt"] = crst["current_appt"].combine_first(crst["original_appt"])

    # Minutes from resolved appointment
    crst["minutes_from_appt"] = (
        (crst["actual_arrival"] - crst["resolved_appt"])
        .dt.total_seconds() / 60
    ).round(1)

    # OTP: same day as resolved appointment
    crst["otp_day_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["resolved_appt"].isna(),
        np.nan,
        (crst["actual_arrival"].dt.date == crst["resolved_appt"].dt.date).astype(int),
    )

    # OTP: within time window of resolved appointment
    crst["otp_time_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["resolved_appt"].isna(),
        np.nan,
        (crst["minutes_from_appt"].abs() <= tolerance_minutes).astype(int),
    )

    # OTP: same day as original appointment
    crst["otp_original_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["original_appt"].isna(),
        np.nan,
        (crst["actual_arrival"].dt.date == crst["original_appt"].dt.date).astype(int),
    )

    # Minutes from original appointment
    crst["minutes_from_original_appt"] = (
        (crst["actual_arrival"] - crst["original_appt"])
        .dt.total_seconds() / 60
    ).round(1)

    # OTP: within time window of original appointment
    crst["otp_time_original_pass"] = np.where(
        crst["actual_arrival"].isna() | crst["original_appt"].isna(),
        np.nan,
        (crst["minutes_from_original_appt"].abs() <= tolerance_minutes).astype(int),
    )

    # Human-readable performance status
    crst["stop_performance_status"] = crst.apply(_derive_status, axis=1, tolerance=tolerance_minutes)

    logger.info(
        "OTP calculated: %d stops, %.1f%% on-time (time window)",
        len(crst),
        crst["otp_time_pass"].mean() * 100 if crst["otp_time_pass"].notna().any() else 0,
    )

    return crst


def _derive_status(row, tolerance: int = OTP_TOLERANCE_MINUTES) -> str:
    """Derive a human-readable performance status for a single stop."""
    if pd.isna(row["actual_arrival"]):
        return "Missing Arrival"
    if pd.isna(row["resolved_appt"]):
        return "Missing Appointment"
    if abs(row["minutes_from_appt"]) <= tolerance:
        return "On Time"
    if row["minutes_from_appt"] < 0:
        return "Early"
    return "Late"
