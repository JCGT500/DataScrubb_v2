"""Telemetry data adapter.

Handles:
- CSV loading with configurable header row
- HTML entity unescaping on column names
- Event timestamp parsing
- Trailer ID normalization
- Door state flag normalization
- Temperature column numeric coercion
"""

import html
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from datascrubb.adapters.base import BaseAdapter
from datascrubb.config import SourceConfig
from datascrubb.constants import (
    TELEMETRY_DEFAULT_HEADER_ROW,
    TELEMETRY_NUMERIC_COLUMNS,
    TELEMETRY_TEMP_COLUMNS,
)
from datascrubb.utils.columns import normalize_columns

logger = logging.getLogger("datascrubb.adapters.telemetry")


class TelemetryAdapter(BaseAdapter):
    def __init__(self, source_config: SourceConfig | None = None):
        super().__init__(source_config)

    def load_raw(self, file_path: Path) -> pd.DataFrame:
        """Load telemetry CSV with configurable header row."""
        header_row = TELEMETRY_DEFAULT_HEADER_ROW
        if self.source_config and self.source_config.header_detection.method == "fixed_row":
            header_row = self.source_config.header_detection.row

        df = pd.read_csv(file_path, header=header_row)

        # Unescape HTML entities in column names (some vendors encode &amp; etc.)
        df.columns = [html.unescape(str(c)) for c in df.columns]

        df = normalize_columns(df)
        logger.info("Telemetry rows loaded: %d", len(df))
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Parse timestamps, normalize trailer IDs, door flags, and temperatures."""
        tel = df.copy()

        # 1. Parse event timestamp
        if "date_&_time" in tel.columns:
            tel["event_ts"] = pd.to_datetime(tel["date_&_time"], errors="coerce")
        elif "date_&_time" in tel.columns:
            tel["event_ts"] = pd.to_datetime(tel["date_&_time"], errors="coerce")
        else:
            # Try common alternatives
            for candidate in ["datetime", "timestamp", "event_time"]:
                if candidate in tel.columns:
                    tel["event_ts"] = pd.to_datetime(tel[candidate], errors="coerce")
                    break
            else:
                logger.warning("No recognized timestamp column found in telemetry data")
                tel["event_ts"] = pd.NaT

        # 2. Normalize trailer ID
        if "vehicle_name" in tel.columns:
            tel["trailer_id"] = tel["vehicle_name"].astype(str).str.strip()
        else:
            tel["trailer_id"] = ""

        # 3. Door open flag
        if "door_1" in tel.columns:
            tel["door_open_flag"] = np.where(
                tel["door_1"].astype(str).str.upper() == "O", 1, 0
            )
        else:
            tel["door_open_flag"] = 0

        # 4. Numeric coercion for temperature columns
        for col in TELEMETRY_TEMP_COLUMNS:
            if col in tel.columns:
                tel[col] = pd.to_numeric(tel[col], errors="coerce")

        # 4b. Numeric coercion for other operational columns
        for col in TELEMETRY_NUMERIC_COLUMNS:
            if col in tel.columns:
                tel[col] = pd.to_numeric(tel[col], errors="coerce")

        # 4c. Fuel rate column comes through as text with '-' for null
        if "avg_fuel_rate_controller_on" in tel.columns:
            s = tel["avg_fuel_rate_controller_on"].astype(str).str.strip()
            tel["avg_fuel_rate"] = pd.to_numeric(
                s.where(s.str.replace(".", "", regex=False).str.replace("-", "", regex=False).str.isdigit(), other=None),
                errors="coerce",
            )

        # 4d. Unit Alarm flag — "Yes"/"No" → 1/0
        if "unit_alarm" in tel.columns:
            tel["unit_alarm_flag"] = (
                tel["unit_alarm"].astype(str).str.strip().str.upper() == "YES"
            ).astype(int)

        # 4e. Unit Power on/off → 1/0 (case-insensitive "On")
        if "unit_power" in tel.columns:
            tel["unit_power_on"] = (
                tel["unit_power"].astype(str).str.strip().str.upper() == "ON"
            ).astype(int)

        logger.info(
            "Telemetry normalized: %d rows, %d unique trailers, "
            "alarms=%d, power-on events=%d",
            len(tel),
            tel["trailer_id"].nunique(),
            int(tel.get("unit_alarm_flag", pd.Series([0])).sum()) if "unit_alarm_flag" in tel.columns else 0,
            int(tel.get("unit_power_on", pd.Series([0])).sum()) if "unit_power_on" in tel.columns else 0,
        )

        return tel
