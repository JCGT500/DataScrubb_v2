"""Matching engine — orchestrates all cross-source joins."""

import logging
from dataclasses import dataclass, field

import pandas as pd

from datascrubb.config import Config, PipelineConfig
from datascrubb.matching.sap_matcher import match_sap_to_crst
from datascrubb.matching.telemetry_matcher import match_telemetry_to_crst

logger = logging.getLogger("datascrubb.matching.engine")


@dataclass
class MatchResults:
    """Container for all matching outputs."""

    crst: pd.DataFrame = field(default_factory=pd.DataFrame)
    sap_segment: pd.DataFrame | None = None
    telemetry_stop: pd.DataFrame | None = None
    m3pl: pd.DataFrame | None = None
    sap_match_rate: float = 0.0
    telemetry_coverage: float = 0.0
    m3pl_match_rate: float = 0.0

    # Route-level KPI rollups (populated in pipeline.py after matching)
    route_kpi: pd.DataFrame | None = None
    miles_variance: pd.DataFrame | None = None
    billing_recon: pd.DataFrame | None = None
    equip_util_tractor: pd.DataFrame | None = None
    equip_util_trailer: pd.DataFrame | None = None
    equip_util_driver: pd.DataFrame | None = None
    temp_compliance: pd.DataFrame | None = None
    route_revenue: pd.DataFrame | None = None
    loaded_miles: pd.DataFrame | None = None
    driver_scorecard: pd.DataFrame | None = None
    lane_profitability: pd.DataFrame | None = None
    claims_risk: pd.DataFrame | None = None
    trailer_utilization: pd.DataFrame | None = None
    route_reefer_cost: pd.DataFrame | None = None
    alarm_log: pd.DataFrame | None = None
    customer_scorecard: pd.DataFrame | None = None
    customer_churn: pd.DataFrame | None = None
    customer_concentration: pd.DataFrame | None = None
    cycle_time: pd.DataFrame | None = None
    late_code_analysis: pd.DataFrame | None = None
    detention_audit: pd.DataFrame | None = None
    demand_forecast: pd.DataFrame | None = None
    trailer_revenue_weekly: pd.DataFrame | None = None
    route_revenue_weekly: pd.DataFrame | None = None
    vanguard_baselines: pd.DataFrame | None = None
    trailer_vci: pd.DataFrame | None = None
    vanguard_alerts: pd.DataFrame | None = None


class MatchingEngine:
    """Orchestrates SAP and telemetry matching against CRST stops."""

    def __init__(self, config: PipelineConfig, full_config: Config | None = None):
        self.config = config
        self.full_config = full_config  # used for cross-block settings (e.g. reefer.door_open_speed_threshold_mph)

    def run(
        self,
        crst_df: pd.DataFrame,
        sap_df: pd.DataFrame | None = None,
        telemetry_df: pd.DataFrame | None = None,
        m3pl_df: pd.DataFrame | None = None,
    ) -> MatchResults:
        """Run all matching and return consolidated results."""
        results = MatchResults(crst=crst_df.copy())

        # SAP matching
        if sap_df is not None and not sap_df.empty:
            logger.info("Running SAP matching (%d SAP rows)", len(sap_df))
            results.sap_segment = match_sap_to_crst(
                crst_df, sap_df, max_hours=self.config.sap_match_max_hours
            )
            matched = (results.sap_segment["sap_match_flag"] == "MATCHED").sum()
            results.sap_match_rate = matched / len(sap_df) if len(sap_df) > 0 else 0
            logger.info("SAP match rate: %.1f%%", results.sap_match_rate * 100)

        # Telemetry matching
        if telemetry_df is not None and not telemetry_df.empty:
            logger.info("Running telemetry matching (%d events)", len(telemetry_df))
            door_speed = (
                self.full_config.reefer.door_open_speed_threshold_mph
                if self.full_config is not None
                else 5.0
            )
            results.telemetry_stop = match_telemetry_to_crst(
                crst_df, telemetry_df,
                window_minutes=self.config.telemetry_window_minutes,
                sample_interval_minutes=self.config.telemetry_sample_interval_minutes,
                fuel_price_per_gallon=self.config.fuel_price_per_gallon,
                door_open_speed_threshold=door_speed,
            )

            # Merge telemetry summary back onto CRST
            if not results.telemetry_stop.empty:
                results.crst = results.crst.merge(
                    results.telemetry_stop, on="transaction_id", how="left"
                )
                stops_with_telem = results.telemetry_stop["transaction_id"].nunique()
                total_stops = crst_df["transaction_id"].nunique()
                results.telemetry_coverage = stops_with_telem / total_stops if total_stops > 0 else 0
                logger.info("Telemetry coverage: %.1f%%", results.telemetry_coverage * 100)

        # M3PL billing — no per-stop matching; carry the snapshot through.
        # Match rate = fraction of M3PL PROs that are present in CRST as order_#.
        if m3pl_df is not None and not m3pl_df.empty:
            results.m3pl = m3pl_df.copy()
            crst_orders = set(
                crst_df["order_#"].astype(str).str.strip().tolist()
                if "order_#" in crst_df.columns
                else []
            )
            m3pl_pros = m3pl_df["pro_number"].astype(str).str.strip()
            matched = m3pl_pros.isin(crst_orders).sum()
            results.m3pl_match_rate = matched / len(m3pl_pros) if len(m3pl_pros) > 0 else 0
            logger.info(
                "M3PL→CRST match: %d/%d PROs (%.1f%%) found in CRST orders",
                matched, len(m3pl_pros), results.m3pl_match_rate * 100,
            )

        return results
