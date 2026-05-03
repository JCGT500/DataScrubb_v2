"""M3PL billing data adapter.

Reads weekly M3PL invoice workbooks containing two sheets:
- INVOICE: lane-level totals (used to grab the billing week-end date from C2)
- SUMMARY: per-PRO data split across multiple sub-tables (one per lane). Each
  sub-table has its own header row whose column labels embed the per-mile and
  per-stop rates (e.g. "Team 1.75", "STOPS 76.30").

Canonical normalized output (one row per PRO# per source file):
    pro_number, legacy_route, lane, crst_miles, stop_count,
    team_miles, solo_miles, team_deficit_miles, solo_deficit_miles, tolls,
    stop_rate, team_rate, solo_rate, team_deficit_rate, solo_deficit_rate,
    billed_miles_amount, billed_stops_amount, billed_deficit_amount,
    billed_amount, tractor, trailer, billing_week_end, source_file
"""

import logging
import re
from pathlib import Path

import pandas as pd

from datascrubb.adapters.base import BaseAdapter
from datascrubb.config import SourceConfig

logger = logging.getLogger("datascrubb.adapters.m3pl")

_RATE_RE = re.compile(r"(\d*\.?\d+)")
_LANE_FROM_HEADER = {
    "legacy route": "Erlanger Legacy",
    "indianapolis": "Whitestown",
    "dallas route": "Dallas",
    "shuttles kankakee": "Kankakee Shuttle",
    "straight trucks": "Solo Straight Trucks",
}


def _to_float(val) -> float:
    """Coerce a cell value to float; return 0.0 on failure / blank."""
    if val is None or pd.isna(val):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        s = str(val).strip().replace(",", "").replace("$", "")
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0


def _extract_rate(label) -> float:
    """Pull the first numeric token out of a header label like 'Team 1.75'."""
    if label is None or pd.isna(label):
        return 0.0
    m = _RATE_RE.search(str(label))
    return float(m.group(1)) if m else 0.0


def _classify_lane(header_text: str) -> str:
    """Map a section's anchor cell to a canonical lane name."""
    text = (header_text or "").strip().lower()
    for needle, lane in _LANE_FROM_HEADER.items():
        if needle in text:
            return lane
    return header_text.strip() if header_text else "Unknown"


def _is_section_header(row) -> bool:
    """A section header row contains 'PRO' or 'ORDER' in column 2."""
    cell = str(row.iloc[2]).strip().upper() if len(row) > 2 else ""
    return cell in ("PRO #", "PRO#", "PRO", "ORDER")


def _is_data_row(row) -> bool:
    """A data row has a numeric-looking PRO# in column 2."""
    cell = row.iloc[2]
    if cell is None or pd.isna(cell):
        return False
    s = str(cell).strip()
    return bool(s) and s[0].isdigit() and len(s) >= 4


def _build_column_map(header_row: pd.Series) -> dict[str, int]:
    """Map canonical column names → column index by inspecting header text."""
    col_map: dict[str, int] = {}
    for i, val in enumerate(header_row):
        if val is None or pd.isna(val):
            continue
        text = str(val).strip().upper()
        if text in ("PRO #", "PRO#", "PRO", "ORDER"):
            col_map["pro_number"] = i
        elif text in ("LEGACY ROUTE",) or "ROUTE" in text and "LEGACY" in text:
            col_map["legacy_route"] = i
        elif "MILES" in text and "TEAM" not in text and "SOLO" not in text and "DEF" not in text and "DEFICIT" not in text:
            col_map["crst_miles"] = i
        elif text.startswith("STOP"):
            col_map["stop_count"] = i
        elif text.startswith("TEAM") and "DEF" in text:
            col_map["team_deficit_miles"] = i
        elif text.startswith("TEAM"):
            col_map["team_miles"] = i
        elif text.startswith("SOLO") and "DEF" in text:
            col_map["solo_deficit_miles"] = i
        elif text.startswith("SOLO"):
            col_map["solo_miles"] = i
        elif text == "TOLLS":
            col_map["tolls"] = i
        elif text.startswith("TRACTOR"):
            col_map["tractor"] = i
        elif text.startswith("TRAILER"):
            col_map["trailer"] = i
    return col_map


def _build_rate_map(header_row: pd.Series, col_map: dict[str, int]) -> dict[str, float]:
    """Pull per-mile and per-stop rates from header labels."""
    rates: dict[str, float] = {
        "stop_rate": 0.0,
        "team_rate": 0.0,
        "solo_rate": 0.0,
        "team_deficit_rate": 0.0,
        "solo_deficit_rate": 0.0,
    }
    for key, rate_key in (
        ("stop_count", "stop_rate"),
        ("team_miles", "team_rate"),
        ("solo_miles", "solo_rate"),
        ("team_deficit_miles", "team_deficit_rate"),
        ("solo_deficit_miles", "solo_deficit_rate"),
    ):
        idx = col_map.get(key)
        if idx is not None:
            rates[rate_key] = _extract_rate(header_row.iloc[idx])
    return rates


def _read_billing_week_end(file_path: Path) -> pd.Timestamp | None:
    """Pull the billing week-end date from cell C2 of the INVOICE sheet."""
    try:
        xl = pd.ExcelFile(file_path)
        target = next(
            (s for s in xl.sheet_names if str(s).strip().lower() == "invoice"),
            None,
        )
        if target is None:
            logger.debug("No INVOICE sheet in %s; will fall back to filename date parse", file_path.name)
            return None
        invoice = pd.read_excel(xl, sheet_name=target, header=None, nrows=3)
    except Exception as exc:
        logger.warning("Could not read INVOICE sheet from %s: %s", file_path.name, exc)
        return None

    # C2 = row index 1, col index 2
    if invoice.shape[0] >= 2 and invoice.shape[1] >= 3:
        cell = invoice.iat[1, 2]
        ts = pd.to_datetime(cell, errors="coerce")
        if pd.notna(ts):
            return ts

    # Fallback: regex on filename for date variants like 01032026, 01.17.26, WE 1.31.2026
    name = file_path.name
    patterns = [
        r"WE[\s_]*(\d{1,2})[._\-](\d{1,2})[._\-](\d{2,4})",
        r"(\d{1,2})[._\-](\d{1,2})[._\-](\d{2,4})",
        r"(\d{2})(\d{2})(\d{4})",
    ]
    for pat in patterns:
        m = re.search(pat, name)
        if m:
            try:
                month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if year < 100:
                    year += 2000
                return pd.Timestamp(year=year, month=month, day=day)
            except (ValueError, TypeError):
                continue

    return None


class M3plAdapter(BaseAdapter):
    """Adapter for M3PL weekly invoice/summary workbooks."""

    def __init__(self, source_config: SourceConfig | None = None):
        super().__init__(source_config)
        self._billing_week_end: pd.Timestamp | None = None
        self._source_file: str = ""

    @property
    def billing_week_end(self) -> pd.Timestamp | None:
        return self._billing_week_end

    def load_raw(self, file_path: Path) -> pd.DataFrame:
        """Read SUMMARY sheet (no header). Stash week-end date and source filename.

        Sheet name lookup is case-insensitive and tolerates a few common variants
        ("SUMMARY", "Summary", "summary", "Summary Sheet"). If no match is
        found, the error message lists the actual sheet names so the user can
        rename or pick the right file.
        """
        self._billing_week_end = _read_billing_week_end(file_path)
        self._source_file = file_path.name

        xl = pd.ExcelFile(file_path)
        target = None
        for name in xl.sheet_names:
            n = str(name).strip().lower()
            if n == "summary" or n.startswith("summary"):
                target = name
                break
        if target is None:
            raise ValueError(
                f"M3PL file '{file_path.name}' has no SUMMARY sheet. "
                f"Sheets found: {xl.sheet_names}. "
                "Expected an M3PL invoice workbook with INVOICE + SUMMARY sheets — "
                "double-check that this file is an M3PL backup (not CRST, SAP, or telemetry)."
            )

        df = pd.read_excel(xl, sheet_name=target, header=None)
        logger.info(
            "M3PL %s loaded: %s shape=%s, week_end=%s",
            file_path.name, target, df.shape, self._billing_week_end,
        )
        return df

    def validate_schema(self, df: pd.DataFrame) -> list[str]:
        # Schema is multi-section; required-column check is done implicitly via
        # the section header detection. Return [] so process() doesn't reject.
        return []

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict] = []
        i = 0
        n = len(df)

        while i < n:
            row = df.iloc[i]
            if _is_section_header(row):
                # Anchor cell (col 1) tells us the lane
                anchor = str(row.iloc[1]).strip() if pd.notna(row.iloc[1]) else ""
                lane = _classify_lane(anchor)
                col_map = _build_column_map(row)
                rate_map = _build_rate_map(row, col_map)

                # Read data rows below header until non-data row
                j = i + 1
                while j < n and _is_data_row(df.iloc[j]):
                    drow = df.iloc[j]
                    rec = self._parse_data_row(drow, col_map, rate_map, lane)
                    if rec:
                        rows.append(rec)
                    j += 1
                i = j
            else:
                i += 1

        out = pd.DataFrame(rows)
        if out.empty:
            logger.warning("M3PL normalize produced 0 rows from %s", self._source_file)
            return out

        # Compute total billed amount per row
        out["billed_miles_amount"] = (
            out["team_miles"] * out["team_rate"]
            + out["solo_miles"] * out["solo_rate"]
            + out["team_deficit_miles"] * out["team_deficit_rate"]
            + out["solo_deficit_miles"] * out["solo_deficit_rate"]
        ).round(2)
        out["billed_stops_amount"] = (out["stop_count"] * out["stop_rate"]).round(2)
        out["billed_deficit_amount"] = (
            out["team_deficit_miles"] * out["team_deficit_rate"]
            + out["solo_deficit_miles"] * out["solo_deficit_rate"]
        ).round(2)
        out["billed_amount"] = (
            out["billed_miles_amount"] + out["billed_stops_amount"] + out["tolls"]
        ).round(2)

        out["billing_week_end"] = self._billing_week_end
        out["source_file"] = self._source_file

        logger.info(
            "M3PL normalized: %d PRO rows across %d lanes, total billed=$%s",
            len(out), out["lane"].nunique(), f"{out['billed_amount'].sum():,.2f}",
        )
        return out

    def _parse_data_row(
        self,
        drow: pd.Series,
        col_map: dict[str, int],
        rate_map: dict[str, float],
        lane: str,
    ) -> dict | None:
        """Convert a SUMMARY data row into the canonical record."""
        def _get(key: str, default=None):
            idx = col_map.get(key)
            if idx is None or idx >= len(drow):
                return default
            return drow.iloc[idx]

        pro_raw = _get("pro_number")
        if pro_raw is None or pd.isna(pro_raw):
            return None
        pro_number = str(pro_raw).strip()
        # Strip trailing .0 if pandas read the PRO# as a float
        if pro_number.endswith(".0"):
            pro_number = pro_number[:-2]

        legacy_route = str(_get("legacy_route", "")).strip()
        tractor = str(_get("tractor", "")).strip()
        trailer = str(_get("trailer", "")).strip()
        # Strip trailing .0 from numeric IDs
        for var, val in [("tractor", tractor), ("trailer", trailer)]:
            if val.endswith(".0"):
                if var == "tractor":
                    tractor = val[:-2]
                else:
                    trailer = val[:-2]

        return {
            "pro_number": pro_number,
            "legacy_route": legacy_route,
            "lane": lane,
            "crst_miles": _to_float(_get("crst_miles")),
            "stop_count": _to_float(_get("stop_count")),
            "team_miles": _to_float(_get("team_miles")),
            "solo_miles": _to_float(_get("solo_miles")),
            "team_deficit_miles": _to_float(_get("team_deficit_miles")),
            "solo_deficit_miles": _to_float(_get("solo_deficit_miles")),
            "tolls": _to_float(_get("tolls")),
            "stop_rate": rate_map["stop_rate"],
            "team_rate": rate_map["team_rate"],
            "solo_rate": rate_map["solo_rate"],
            "team_deficit_rate": rate_map["team_deficit_rate"],
            "solo_deficit_rate": rate_map["solo_deficit_rate"],
            "tractor": tractor,
            "trailer": trailer,
        }
