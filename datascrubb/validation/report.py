"""Validation report builder — creates summary DataFrames for display and export."""

import pandas as pd


def build_error_summary(errors: list[dict]) -> pd.DataFrame:
    """Build a summary of errors grouped by type and reason."""
    if not errors:
        return pd.DataFrame(columns=["source", "error_type", "error_reason", "count"])

    df = pd.DataFrame(errors)
    summary = (
        df.groupby(["source", "error_type", "error_reason"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values(["error_type", "count"], ascending=[True, False])
    )
    return summary


def build_error_detail(errors: list[dict]) -> pd.DataFrame:
    """Build a detailed error log with one row per error."""
    if not errors:
        return pd.DataFrame(
            columns=["transaction_id", "source", "error_type", "error_reason"]
        )
    return pd.DataFrame(errors)


def build_error_reference() -> pd.DataFrame:
    """Build the static error reference table (matches notebook's ERROR_REFERENCE sheet)."""
    return pd.DataFrame(
        {
            "Error_Reason": [
                "Missing Actual Arrival",
                "Missing Appointment",
                "Missing S_Code for plasma stop",
                "Duplicate TransactionID",
                "PRO# appears in multiple billing weeks",
                "M3PL PRO# not found in CRST",
                "Reefer temperature excursion",
                "Stop count divergence between CRST and M3PL",
            ],
            "Meaning": [
                "The stop does not have a recorded actual arrival time. OTP cannot be evaluated.",
                "Neither original nor current appointment is present. OTP cannot be evaluated.",
                "A plasma center stop is missing an S-Code. This may affect SAP matching.",
                "Multiple stops collapsed to the same TransactionID. Investigate source data.",
                "The same PRO# was billed in more than one weekly M3PL invoice.",
                "An M3PL-billed PRO# has no corresponding CRST order — billing without service detail.",
                "Reefer min/max S1 temperature fell outside setpoint ± tolerance for one or more stops on the route.",
                "CRST stop count for the route differs from M3PL stop count by more than 10%.",
            ],
            "Severity": [
                "Informational",
                "Informational",
                "Data Quality Issue",
                "Critical",
                "Warning",
                "Warning",
                "Data Quality Issue",
                "Warning",
            ],
            "Action": [
                "Review source data; no action required for OTP reporting.",
                "Review scheduling data; investigate if recurring.",
                "Investigate master data or location mapping.",
                "Pipeline error — stop the run and investigate immediately.",
                "Confirm whether intentional (multi-week service) or duplicate billing.",
                "Confirm CRST file completeness and reconcile with billing.",
                "Investigate reefer unit, door events, or sensor calibration.",
                "Reconcile route stop counts between operations (CRST) and billing (M3PL).",
            ],
        }
    )


def build_info_sheet() -> pd.DataFrame:
    """Build the INFO sheet describing the output file."""
    return pd.DataFrame(
        {
            "Section": [
                "Purpose",
                "Stop Definition",
                "OTP Buckets",
                "SAP Integration",
                "Warehouse Stops",
                "Error Handling",
                "How to Use This File",
            ],
            "Description": [
                "This file represents stop-level transportation performance derived from CRST and SAP data.",
                "Each row in STOP_MASTER represents one physical stop after collapsing operational duplicates.",
                "OTP is calculated using multiple buckets: day-level and time-window against current and original appointments.",
                "SAP data is joined to stops where applicable to provide shipment context. SAP does not drive OTP.",
                "Warehouse stops may not have S-Codes and may not join to SAP. This is expected behavior.",
                "Errors are flagged to indicate data gaps or quality issues. Not all errors indicate failure.",
                "Use STOP_MASTER for performance analysis and SAP_SEGMENT for shipment detail.",
            ],
        }
    )
