"""Telemetry-to-CRST matching logic.

Matches trailer telemetry events to CRST stops by:
1. Building time windows around each stop (arrival ±2h, or +6h if no departure)
2. Joining on trailer ID
3. Filtering events to the time window
4. Aggregating to stop level — temps, doors, speed, idle, fuel, alarms,
   battery, engine hours, set-point changes
"""

import logging

import numpy as np
import pandas as pd

from datascrubb.constants import TELEMETRY_SAMPLE_INTERVAL_MINUTES, TELEMETRY_WINDOW_MINUTES

logger = logging.getLogger("datascrubb.matching.telemetry")


def match_telemetry_to_crst(
    crst_df: pd.DataFrame,
    telemetry_df: pd.DataFrame,
    window_minutes: int = TELEMETRY_WINDOW_MINUTES,
    sample_interval_minutes: int = TELEMETRY_SAMPLE_INTERVAL_MINUTES,
    fuel_price_per_gallon: float = 4.50,
    door_open_speed_threshold: float = 5.0,
) -> pd.DataFrame:
    """Match telemetry events to CRST stops and aggregate to stop level.

    Returns a DataFrame with one row per transaction_id with a rich set of
    aggregations: temps, doors, speed, idle minutes, reefer runtime, reefer
    fuel cost, alarm events, battery min/avg, engine hours peak, setpoint
    changes, door-open-while-moving events.
    """
    empty_cols = [
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
    if telemetry_df.empty or crst_df.empty:
        logger.warning("Empty input: telemetry=%d, CRST=%d", len(telemetry_df), len(crst_df))
        return pd.DataFrame(columns=empty_cols)

    crst = crst_df.copy()
    window_td = pd.Timedelta(minutes=window_minutes)

    crst["stop_start_ts"] = crst["actual_arrival"] - window_td
    if "actual_departure" in crst.columns:
        crst["stop_end_ts"] = np.where(
            crst["actual_departure"].notna(),
            crst["actual_departure"] + window_td,
            crst["actual_arrival"] + pd.Timedelta(hours=6),
        )
        crst["stop_end_ts"] = pd.to_datetime(crst["stop_end_ts"], errors="coerce")
    else:
        crst["stop_end_ts"] = crst["actual_arrival"] + pd.Timedelta(hours=6)

    join_cols = ["transaction_id", "trailer", "stop_start_ts", "stop_end_ts"]
    crst_subset = crst[[c for c in join_cols if c in crst.columns]].dropna(subset=["trailer"])
    if crst_subset.empty:
        logger.warning("No CRST rows with trailer info for telemetry matching")
        return pd.DataFrame(columns=empty_cols)

    candidates = telemetry_df.merge(
        crst_subset, how="inner", left_on="trailer_id", right_on="trailer",
    )
    candidates = candidates[
        (candidates["event_ts"] >= candidates["stop_start_ts"])
        & (candidates["event_ts"] <= candidates["stop_end_ts"])
    ]

    if candidates.empty:
        logger.warning("No telemetry events fell within any stop time window")
        return pd.DataFrame(columns=empty_cols)

    # Helper accessors
    def col(name: str, default=np.nan) -> pd.Series:
        return candidates[name] if name in candidates.columns else pd.Series(default, index=candidates.index)

    speed = col("speed", 0).fillna(0)
    candidates["_idle"] = ((speed == 0) & (col("engine_rpm", 0).fillna(0) > 0)).astype(int)
    candidates["_door_moving"] = ((col("door_open_flag", 0) == 1) & (speed > door_open_speed_threshold)).astype(int)
    candidates["_power_on"] = col("unit_power_on", 0).fillna(0).astype(int)
    candidates["_alarm"] = col("unit_alarm_flag", 0).fillna(0).astype(int)
    if "da1" in candidates.columns and "ra1" in candidates.columns:
        candidates["_da_ra_delta"] = candidates["ra1"] - candidates["da1"]

    grouped = candidates.groupby("transaction_id", as_index=False)

    # Build aggregations as a flat dict of (col, fn)
    aggs: dict = {"telem_events": ("event_ts", "count")}
    if "amb_temp" in candidates.columns:
        aggs["min_amb_temp"] = ("amb_temp", "min")
        aggs["max_amb_temp"] = ("amb_temp", "max")
        aggs["avg_amb_temp"] = ("amb_temp", "mean")
    if "s1" in candidates.columns:
        aggs["min_s1"] = ("s1", "min")
        aggs["max_s1"] = ("s1", "max")
    if "tl1" in candidates.columns:
        aggs["min_tl1"] = ("tl1", "min")
        aggs["max_tl1"] = ("tl1", "max")
        aggs["avg_tl1"] = ("tl1", "mean")
    if "door_open_flag" in candidates.columns:
        aggs["door_open_events"] = ("door_open_flag", "sum")
    aggs["door_open_while_moving"] = ("_door_moving", "sum")
    if "speed" in candidates.columns:
        aggs["max_speed"] = ("speed", "max")
        aggs["avg_speed"] = ("speed", "mean")
    aggs["_idle_events"] = ("_idle", "sum")
    aggs["_power_on_events"] = ("_power_on", "sum")
    if "avg_fuel_rate" in candidates.columns:
        aggs["_avg_fuel_rate_when_on"] = (
            "avg_fuel_rate",
            lambda s: float(pd.to_numeric(s.where(candidates.loc[s.index, "_power_on"] == 1), errors="coerce").mean()),
        )
    aggs["alarm_events"] = ("_alarm", "sum")
    if "battery_voltage" in candidates.columns:
        aggs["min_battery"] = ("battery_voltage", "min")
        aggs["avg_battery"] = ("battery_voltage", "mean")
    if "engine_hours" in candidates.columns:
        aggs["max_engine_hours"] = ("engine_hours", "max")
    if "total_hours" in candidates.columns:
        aggs["max_total_hours"] = ("total_hours", "max")
    if "sp1" in candidates.columns:
        aggs["setpoint_changes"] = ("sp1", lambda s: max(int(s.dropna().nunique()) - 1, 0))
    if "_da_ra_delta" in candidates.columns:
        aggs["avg_da_ra_delta"] = ("_da_ra_delta", "mean")

    result = grouped.agg(**aggs)

    # Convert event-counts to runtime / idle minutes
    result["idle_minutes"] = (result["_idle_events"] * sample_interval_minutes).astype(float)
    result["reefer_runtime_minutes"] = (result["_power_on_events"] * sample_interval_minutes).astype(float)

    if "_avg_fuel_rate_when_on" in result.columns:
        # gallons = avg_fuel_rate (gal/hr) × runtime hours
        result["reefer_gallons"] = (
            result["_avg_fuel_rate_when_on"].fillna(0)
            * (result["reefer_runtime_minutes"] / 60)
        ).round(2)
        result["reefer_fuel_cost"] = (result["reefer_gallons"] * fuel_price_per_gallon).round(2)
    else:
        result["reefer_gallons"] = 0.0
        result["reefer_fuel_cost"] = 0.0

    # Drop scratch columns
    result = result.drop(columns=[c for c in ("_idle_events", "_power_on_events", "_avg_fuel_rate_when_on") if c in result.columns])

    # Round numeric columns
    for c in ("avg_speed", "max_speed", "avg_amb_temp", "min_amb_temp", "max_amb_temp",
              "min_s1", "max_s1", "min_tl1", "max_tl1", "avg_tl1",
              "min_battery", "avg_battery", "avg_da_ra_delta"):
        if c in result.columns:
            result[c] = result[c].round(1)

    logger.info(
        "Telemetry matching complete: %d stops with telemetry data; "
        "%d alarm events, %d door-open-while-moving events",
        len(result),
        int(result["alarm_events"].fillna(0).sum()) if "alarm_events" in result.columns else 0,
        int(result["door_open_while_moving"].fillna(0).sum()) if "door_open_while_moving" in result.columns else 0,
    )

    return result
