"""CRST data adapter — the most complex adapter in the pipeline.

Handles:
- Auto-detecting the header row via keyword search
- Column normalization
- S-Code extraction and stop type classification
- Route day construction and stop sequencing
- TransactionID creation
- Duplicate collapse (keep first by earliest arrival)
- Customer name extraction from location_date
"""

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Trailing " MM/DD HH:MM" or " MM/DD/YYYY HH:MM" pattern at end of location_date
_LOC_DATE_TRAIL_RE = re.compile(r"\s+\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+\d{1,2}:\d{2}\s*$")


def extract_customer(text) -> str | None:
    """Extract a customer name from a CRST location_date cell.

    Examples:
        'RX CROSSROADS - DISTRIBUTION 01/01 08:00' -> 'RX CROSSROADS'
        'BIOLIFE - LENEXA S1304 01/02 07:00'       -> 'BIOLIFE'
        'CSL - TUCSON AZ 624 - S9682 01/06 15:00'  -> 'CSL'
        'CRST International 01/05 07:00'           -> 'CRST International'
    """
    if text is None or pd.isna(text):
        return None
    s = str(text).strip()
    s = _LOC_DATE_TRAIL_RE.sub("", s)
    if " - " in s:
        return s.split(" - ", 1)[0].strip().upper()
    return s.strip().upper()

from datascrubb.adapters.base import BaseAdapter
from datascrubb.classify import ClassifyConfig, classify_stops_df
from datascrubb.config import SourceConfig
from datascrubb.constants import (
    CRST_HEADER_KEYWORDS,
    CRST_HEADER_MAX_ROWS,
    CRST_REQUIRED_COLUMNS,
    STOP_TYPE_PLASMA,
    STOP_TYPE_WAREHOUSE,
)
from datascrubb.utils.columns import normalize_columns
from datascrubb.utils.s_code import extract_s_code

logger = logging.getLogger("datascrubb.adapters.crst")


def find_header_row(df: pd.DataFrame, keywords: list[str], max_rows: int = 15) -> int | None:
    """Search the first `max_rows` rows for a row containing all keywords.

    Returns the row index or None if not found.
    """
    for i in range(min(max_rows, len(df))):
        row_values = df.iloc[i].fillna("").astype(str)
        row_text = " ".join(row_values).lower()
        if all(k in row_text for k in keywords):
            return i
    return None


class CrstAdapter(BaseAdapter):
    def __init__(self, source_config: SourceConfig | None = None):
        super().__init__(source_config)
        self._header_row: int | None = None

    @property
    def header_row(self) -> int | None:
        """The detected header row index (set after load_raw)."""
        return self._header_row

    def load_raw(self, file_path: Path) -> pd.DataFrame:
        """Load CRST Excel file, auto-detecting the header row."""
        # First pass: read without header to find the header row
        preview = pd.read_excel(file_path, header=None)

        keywords = CRST_HEADER_KEYWORDS
        max_rows = CRST_HEADER_MAX_ROWS
        if self.source_config and self.source_config.header_detection.keywords:
            keywords = self.source_config.header_detection.keywords
            max_rows = self.source_config.header_detection.max_rows

        self._header_row = find_header_row(preview, keywords, max_rows)
        if self._header_row is None:
            raise ValueError(
                f"CRST header row not found. "
                f"Searched first {max_rows} rows for keywords: {keywords}"
            )

        logger.info("Detected CRST header at Excel row %d", self._header_row + 1)

        # Second pass: read with correct header
        df = pd.read_excel(file_path, header=self._header_row)
        df = normalize_columns(df)

        logger.info("CRST rows loaded: %d", len(df))
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply all CRST business logic transformations."""
        crst = df.copy()

        # 0. Extract route_name from "route:_ship_date" col (e.g. "AMES LD395889: 01/04/2026" → "AMES LD395889")
        if "route:_ship_date" in crst.columns:
            crst["route_name"] = (
                crst["route:_ship_date"]
                .astype(str).str.split(":").str[0].str.strip()
                .replace({"nan": None, "": None})
            )

        # Split "city,_state" → city, state
        if "city,_state" in crst.columns:
            parts = crst["city,_state"].astype(str).str.split(",", n=1, expand=True)
            crst["city"] = parts[0].str.strip().replace({"nan": None, "": None})
            if parts.shape[1] > 1:
                crst["state"] = parts[1].str.strip().replace({"nan": None, "": None})
            else:
                crst["state"] = None

        # 1. Extract S-Code from location_date
        crst["s_code"] = crst["location_date"].apply(extract_s_code)

        # 1b. Extract customer name from location_date (e.g. 'BIOLIFE - LENEXA S1304 ...' → 'BIOLIFE')
        crst["customer"] = crst["location_date"].apply(extract_customer)

        # 1c. Preserve raw stop direction (PU = pickup, SO = stop-off / delivery)
        # before we overwrite `stop_type` with the PLASMA/WAREHOUSE classification.
        if "stop_type" in crst.columns:
            crst["stop_direction"] = (
                crst["stop_type"].astype(str).str.strip().str.upper()
                .where(crst["stop_type"].notna(), None)
            )
        else:
            crst["stop_direction"] = None

        # 1d. Coerce case columns to numeric for load tracking
        for col in ("tender_cases", "current_cases", "cases_variance", "sum_of_weight"):
            if col in crst.columns:
                crst[col] = pd.to_numeric(crst[col], errors="coerce")

        # 1e. Loaded-at-stop flag.
        # A trailer is loaded during this stop if it has cases on board after the
        # stop (current_cases > 0) OR it just delivered cases at this stop
        # (stop_direction == "SO" and tender_cases > 0 means it WAS loaded going in).
        cur = crst["current_cases"] if "current_cases" in crst.columns else pd.Series(0, index=crst.index)
        ten = crst["tender_cases"] if "tender_cases" in crst.columns else pd.Series(0, index=crst.index)
        is_so = crst["stop_direction"].astype(str).str.upper() == "SO"
        crst["loaded_at_stop"] = (
            (cur.fillna(0) > 0) | (is_so & (ten.fillna(0) > 0))
        ).astype(int)

        # 2. Classify stop type (legacy binary kept for backwards-compat)
        crst["stop_type"] = np.where(
            crst["s_code"].notna(), STOP_TYPE_PLASMA, STOP_TYPE_WAREHOUSE
        )

        # 2b. Finer classification (PLASMA_CENTER / DISTRIBUTION_CENTER /
        # INTERNAL_BASE / OTHER) via configurable regex rules.
        from datascrubb.config import load_config

        try:
            cfg = load_config()
            classify_cfg = ClassifyConfig(
                use_s_code_for_plasma=cfg.stop_classification.use_s_code_for_plasma,
                rules=cfg.stop_classification.rules,
                default_class=cfg.stop_classification.default_class,
            )
        except Exception:
            classify_cfg = ClassifyConfig()
        crst["stop_class"] = classify_stops_df(crst, classify_cfg)

        # 3. Parse datetime columns
        datetime_cols = ["original_appt", "current_appt", "actual_arrival", "actual_departure"]
        for col in datetime_cols:
            if col in crst.columns:
                crst[col] = pd.to_datetime(crst[col], errors="coerce")

        # Dwell minutes (departure − arrival), used for route-level KPIs
        if "actual_departure" in crst.columns:
            crst["dwell_minutes"] = (
                (crst["actual_departure"] - crst["actual_arrival"])
                .dt.total_seconds() / 60
            ).round(1)
        else:
            crst["dwell_minutes"] = pd.NA

        # 4. Derive arrival_date
        crst["arrival_date"] = crst["actual_arrival"].dt.date.astype(str)

        # 5. Build route_day
        crst["route_day"] = (
            crst["order_#"].astype(str) + "_" + crst["arrival_date"].astype(str)
        )

        # 6. Sort stops in physical execution order
        crst = crst.sort_values(
            by=["order_#", "actual_arrival", "location_date"],
            na_position="last",
        )

        # 7. Assign stop sequence per route day (zero-padded)
        crst["stop_seq"] = (
            crst.groupby("route_day").cumcount() + 1
        ).astype(str).str.zfill(2)

        # 8. Resolved appointment: current falls back to original
        crst["resolved_appt"] = crst["current_appt"].combine_first(
            crst["original_appt"]
        )

        # 9. Build TransactionID
        crst["transaction_id"] = (
            crst["order_#"].astype(str)
            + "_"
            + crst["arrival_date"].fillna("NO_DATE").astype(str)
            + "_"
            + crst["stop_seq"]
        )

        # 10. Collapse duplicate TransactionIDs (keep first by earliest arrival)
        crst = (
            crst.sort_values(
                by=["actual_arrival", "resolved_appt"],
                na_position="last",
            )
            .groupby("transaction_id", as_index=False)
            .first()
        )

        logger.info("CRST rows after collapse: %d", len(crst))

        # 11. Re-assert datetime types after collapse
        crst["actual_arrival"] = pd.to_datetime(crst["actual_arrival"], errors="coerce")
        crst["resolved_appt"] = crst["current_appt"].combine_first(crst["original_appt"])

        # 12. Initialize error columns
        crst["error_flag"] = "N"
        crst["error_reason"] = ""

        return crst
