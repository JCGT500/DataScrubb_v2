"""Individual validation rule functions.

Each rule receives a DataFrame and returns a list of error dicts:
    {"transaction_id": ..., "source": ..., "error_type": ..., "error_reason": ...}

Error types:
- SOFT: Expected data gaps that don't invalidate the stop
- HARD: Pipeline-breaking issues that need investigation
- WARNING: Quality concerns that should be monitored
"""

import pandas as pd


def check_missing_arrival(crst_df: pd.DataFrame) -> list[dict]:
    """Flag stops with missing actual arrival (SOFT)."""
    mask = crst_df["actual_arrival"].isna()
    return [
        {
            "transaction_id": row["transaction_id"],
            "source": "CRST",
            "error_type": "SOFT",
            "error_reason": "Missing Actual Arrival",
        }
        for _, row in crst_df[mask].iterrows()
    ]


def check_missing_appointment(crst_df: pd.DataFrame) -> list[dict]:
    """Flag stops with missing resolved appointment (SOFT)."""
    mask = crst_df["resolved_appt"].isna()
    return [
        {
            "transaction_id": row["transaction_id"],
            "source": "CRST",
            "error_type": "SOFT",
            "error_reason": "Missing Appointment",
        }
        for _, row in crst_df[mask].iterrows()
    ]


def check_missing_scode_plasma(crst_df: pd.DataFrame) -> list[dict]:
    """Flag plasma center stops missing an S-Code (SOFT)."""
    mask = (crst_df["stop_type"] == "PLASMA_CENTER") & (crst_df["s_code"].isna())
    return [
        {
            "transaction_id": row["transaction_id"],
            "source": "CRST",
            "error_type": "SOFT",
            "error_reason": "Missing S_Code for plasma stop",
        }
        for _, row in crst_df[mask].iterrows()
    ]


def check_duplicate_transaction_ids(crst_df: pd.DataFrame) -> list[dict]:
    """Flag duplicate transaction IDs — this is a HARD error."""
    dup_mask = crst_df["transaction_id"].duplicated(keep=False)
    if not dup_mask.any():
        return []

    return [
        {
            "transaction_id": row["transaction_id"],
            "source": "CRST",
            "error_type": "HARD",
            "error_reason": "Duplicate TransactionID",
        }
        for _, row in crst_df[dup_mask].iterrows()
    ]


def check_sap_match_rate(sap_df: pd.DataFrame, threshold: float = 0.5) -> list[dict]:
    """Warn if SAP match rate drops below threshold (WARNING)."""
    if sap_df is None or sap_df.empty:
        return []

    if "sap_match_flag" not in sap_df.columns:
        return []

    match_rate = (sap_df["sap_match_flag"] == "MATCHED").mean()
    if match_rate < threshold:
        return [
            {
                "transaction_id": None,
                "source": "SAP",
                "error_type": "WARNING",
                "error_reason": f"SAP match rate {match_rate:.1%} below threshold {threshold:.0%}",
            }
        ]
    return []


def check_telemetry_coverage(
    crst_df: pd.DataFrame,
    telemetry_stop_df: pd.DataFrame | None,
    threshold: float = 0.3,
) -> list[dict]:
    """Warn if telemetry coverage is below threshold (WARNING)."""
    if telemetry_stop_df is None or telemetry_stop_df.empty:
        return [
            {
                "transaction_id": None,
                "source": "TELEMETRY",
                "error_type": "WARNING",
                "error_reason": "No telemetry data matched to any stops",
            }
        ]

    total = crst_df["transaction_id"].nunique()
    covered = telemetry_stop_df["transaction_id"].nunique()
    coverage = covered / total if total > 0 else 0

    if coverage < threshold:
        return [
            {
                "transaction_id": None,
                "source": "TELEMETRY",
                "error_type": "WARNING",
                "error_reason": f"Telemetry coverage {coverage:.1%} below threshold {threshold:.0%}",
            }
        ]
    return []


def check_billing_dup_pro(m3pl_df: pd.DataFrame | None) -> list[dict]:
    """Warn when the same PRO# appears in more than one M3PL billing week."""
    if m3pl_df is None or m3pl_df.empty:
        return []
    counts = m3pl_df.groupby("pro_number")["billing_week_end"].nunique()
    dups = counts[counts > 1]
    return [
        {
            "transaction_id": None,
            "source": "M3PL",
            "error_type": "WARNING",
            "error_reason": f"PRO# {pro} appears in {n} billing weeks",
        }
        for pro, n in dups.items()
    ]


def check_m3pl_pro_not_in_crst(
    m3pl_df: pd.DataFrame | None, crst_df: pd.DataFrame | None
) -> list[dict]:
    """Warn for M3PL PROs that aren't represented in CRST as an order_#."""
    if m3pl_df is None or m3pl_df.empty or crst_df is None or crst_df.empty:
        return []
    crst_orders = set(crst_df["order_#"].astype(str).str.strip().tolist()) if "order_#" in crst_df.columns else set()
    if not crst_orders:
        return []
    m3pl_pros = m3pl_df["pro_number"].astype(str).str.strip().unique()
    missing = [p for p in m3pl_pros if p not in crst_orders]
    return [
        {
            "transaction_id": None,
            "source": "M3PL",
            "error_type": "WARNING",
            "error_reason": f"PRO# {pro} billed but no matching CRST order",
        }
        for pro in missing
    ]


def check_temp_excursions(temp_compliance_df: pd.DataFrame | None) -> list[dict]:
    """Flag routes with reefer temperature excursions (SOFT)."""
    if temp_compliance_df is None or temp_compliance_df.empty:
        return []
    if "compliance_flag" not in temp_compliance_df.columns:
        return []
    bad = temp_compliance_df[temp_compliance_df["compliance_flag"] == "EXCURSION"]
    return [
        {
            "transaction_id": None,
            "source": "TELEMETRY",
            "error_type": "SOFT",
            "error_reason": (
                f"Route {row['route_id']}: {int(row['excursion_count'])} excursion stops "
                f"({int(row['excursion_minutes'])} min)"
            ),
        }
        for _, row in bad.iterrows()
    ]


def check_miles_variance(miles_variance_df: pd.DataFrame | None, threshold_pct: float = 10.0) -> list[dict]:
    """Flag routes whose CRST stop count deviates from M3PL stop count by > threshold."""
    if miles_variance_df is None or miles_variance_df.empty:
        return []
    if "stop_variance" not in miles_variance_df.columns or "m3pl_stop_count" not in miles_variance_df.columns:
        return []
    df = miles_variance_df.dropna(subset=["m3pl_stop_count"])
    df = df[df["m3pl_stop_count"] > 0].copy()
    df["pct_diff"] = (df["stop_variance"].abs() / df["m3pl_stop_count"] * 100)
    bad = df[df["pct_diff"] > threshold_pct]
    return [
        {
            "transaction_id": None,
            "source": "M3PL",
            "error_type": "WARNING",
            "error_reason": (
                f"Route {row['route_id']} stop count diverges by {row['pct_diff']:.0f}% "
                f"(CRST {int(row['crst_stop_count'])} vs M3PL {int(row['m3pl_stop_count'])})"
            ),
        }
        for _, row in bad.iterrows()
    ]
