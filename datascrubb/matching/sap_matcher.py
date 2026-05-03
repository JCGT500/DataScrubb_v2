"""SAP-to-CRST matching logic.

Matches SAP shipment segments to CRST stops by:
1. Joining on S-Code (plasma center identifier)
2. Filtering to PLASMA_CENTER stops with actual arrivals
3. Picking the closest match by time difference
4. Applying a maximum time window (default 36 hours)
"""

import logging

import numpy as np
import pandas as pd

from datascrubb.constants import SAP_MATCH_MAX_HOURS

logger = logging.getLogger("datascrubb.matching.sap")


def match_sap_to_crst(
    crst_df: pd.DataFrame,
    sap_df: pd.DataFrame,
    max_hours: int = SAP_MATCH_MAX_HOURS,
) -> pd.DataFrame:
    """Match SAP segments to CRST stops.

    Returns a DataFrame of SAP segments enriched with transaction_id
    and a sap_match_flag column (MATCHED or UNMATCHED).
    """
    if sap_df.empty or crst_df.empty:
        logger.warning("Empty input: SAP=%d, CRST=%d", len(sap_df), len(crst_df))
        sap_out = sap_df.copy()
        sap_out["transaction_id"] = None
        sap_out["sap_match_flag"] = "UNMATCHED"
        sap_out["time_diff_hours"] = np.nan
        return sap_out

    # Step 1: Build candidate matches by joining on s_code
    crst_join_cols = ["transaction_id", "s_code", "actual_arrival", "stop_type"]
    crst_subset = crst_df[[c for c in crst_join_cols if c in crst_df.columns]].copy()

    candidates = sap_df.merge(crst_subset, how="left", on="s_code")

    # Step 2: Filter to plasma center stops with actual arrivals
    candidates = candidates[
        (candidates["stop_type"] == "PLASMA_CENTER")
        & (candidates["actual_arrival"].notna())
    ]

    if candidates.empty:
        logger.warning("No SAP-CRST candidates found after filtering")
        sap_out = sap_df.copy()
        sap_out["transaction_id"] = None
        sap_out["sap_match_flag"] = "UNMATCHED"
        sap_out["time_diff_hours"] = np.nan
        return sap_out

    # Step 3: Calculate time difference in hours
    candidates["time_diff_hours"] = (
        (candidates["arrive"] - candidates["actual_arrival"])
        .abs()
        .dt.total_seconds()
        / 3600
    )

    # Step 4: Pick closest match per SAP segment
    enriched = (
        candidates.sort_values("time_diff_hours")
        .groupby(["document_number", "segment_number"], as_index=False)
        .first()
    )

    # Step 5: Apply time window filter
    enriched = enriched[enriched["time_diff_hours"] <= max_hours]

    # Step 6: Set match flag
    enriched["sap_match_flag"] = np.where(
        enriched["transaction_id"].isna(), "UNMATCHED", "MATCHED"
    )

    matched = enriched["sap_match_flag"].value_counts()
    logger.info(
        "SAP matching complete: %d matched, %d unmatched (max %dh window)",
        matched.get("MATCHED", 0),
        matched.get("UNMATCHED", 0),
        max_hours,
    )

    return enriched
