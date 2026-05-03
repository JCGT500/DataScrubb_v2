"""Multi-sheet Excel export — preserves the current notebook output format."""

import logging
from pathlib import Path

import pandas as pd

from datascrubb.validation.report import build_error_reference, build_info_sheet

logger = logging.getLogger("datascrubb.export.excel")


def export_to_excel(
    output_path: str | Path,
    run_metadata: pd.DataFrame,
    crst_raw: pd.DataFrame,
    sap_raw: pd.DataFrame | None,
    telemetry_raw: pd.DataFrame | None,
    stop_master: pd.DataFrame,
    sap_segment: pd.DataFrame | None,
    telemetry_stop: pd.DataFrame | None,
    billing_snapshot: pd.DataFrame | None = None,
    route_kpi: pd.DataFrame | None = None,
    miles_variance: pd.DataFrame | None = None,
    billing_recon: pd.DataFrame | None = None,
    equip_util_tractor: pd.DataFrame | None = None,
    equip_util_trailer: pd.DataFrame | None = None,
    equip_util_driver: pd.DataFrame | None = None,
    temp_compliance: pd.DataFrame | None = None,
    route_revenue: pd.DataFrame | None = None,
    loaded_miles: pd.DataFrame | None = None,
    driver_scorecard: pd.DataFrame | None = None,
    lane_profitability: pd.DataFrame | None = None,
    claims_risk: pd.DataFrame | None = None,
    trailer_utilization: pd.DataFrame | None = None,
    route_reefer_cost: pd.DataFrame | None = None,
    alarm_log: pd.DataFrame | None = None,
    customer_scorecard: pd.DataFrame | None = None,
    customer_churn: pd.DataFrame | None = None,
    customer_concentration: pd.DataFrame | None = None,
    cycle_time: pd.DataFrame | None = None,
    late_code_analysis: pd.DataFrame | None = None,
    detention_audit: pd.DataFrame | None = None,
    demand_forecast: pd.DataFrame | None = None,
    trailer_revenue_weekly: pd.DataFrame | None = None,
    route_revenue_weekly: pd.DataFrame | None = None,
    vanguard_baselines: pd.DataFrame | None = None,
    trailer_vci: pd.DataFrame | None = None,
    vanguard_alerts: pd.DataFrame | None = None,
) -> Path:
    """Write all pipeline outputs to a multi-sheet Excel file.

    Core sheets: RUN_METADATA, CRST_RAW, SAP_RAW, TELEMETRY_RAW, STOP_MASTER,
    SAP_SEGMENT, TRAILER_TELEMETRY_STOP, INFO, ERROR_REFERENCE.

    Extended sheets (when provided): BILLING_SNAPSHOT, ROUTE_KPI,
    MILES_VARIANCE, BILLING_RECON, EQUIP_UTIL_TRACTOR/_TRAILER/_DRIVER,
    TEMP_COMPLIANCE.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(output_path, engine="openpyxl", mode="w") as writer:
        run_metadata.to_excel(writer, sheet_name="RUN_METADATA", index=False)
        crst_raw.to_excel(writer, sheet_name="CRST_RAW", index=False)

        if sap_raw is not None:
            sap_raw.to_excel(writer, sheet_name="SAP_RAW", index=False)

        if telemetry_raw is not None:
            telemetry_raw.to_excel(writer, sheet_name="TELEMETRY_RAW", index=False)

        stop_master.to_excel(writer, sheet_name="STOP_MASTER", index=False)

        if sap_segment is not None:
            sap_segment.to_excel(writer, sheet_name="SAP_SEGMENT", index=False)

        if telemetry_stop is not None:
            telemetry_stop.to_excel(writer, sheet_name="TRAILER_TELEMETRY_STOP", index=False)

        # Extended billing/KPI sheets (additive, only written when populated)
        for name, frame in [
            ("BILLING_SNAPSHOT", billing_snapshot),
            ("ROUTE_KPI", route_kpi),
            ("MILES_VARIANCE", miles_variance),
            ("BILLING_RECON", billing_recon),
            ("EQUIP_UTIL_TRACTOR", equip_util_tractor),
            ("EQUIP_UTIL_TRAILER", equip_util_trailer),
            ("EQUIP_UTIL_DRIVER", equip_util_driver),
            ("TEMP_COMPLIANCE", temp_compliance),
            ("ROUTE_REVENUE", route_revenue),
            ("LOADED_MILES", loaded_miles),
            ("DRIVER_SCORECARD", driver_scorecard),
            ("LANE_PROFITABILITY", lane_profitability),
            ("CLAIMS_RISK", claims_risk),
            ("TRAILER_UTILIZATION", trailer_utilization),
            ("ROUTE_REEFER_COST", route_reefer_cost),
            ("ALARM_LOG", alarm_log),
            ("CUSTOMER_SCORECARD", customer_scorecard),
            ("CUSTOMER_CHURN", customer_churn),
            ("CUSTOMER_CONCENTRATION", customer_concentration),
            ("CYCLE_TIME", cycle_time),
            ("LATE_CODE_ANALYSIS", late_code_analysis),
            ("DETENTION_AUDIT", detention_audit),
            ("DEMAND_FORECAST", demand_forecast),
            ("TRAILER_REVENUE_WEEKLY", trailer_revenue_weekly),
            ("ROUTE_REVENUE_WEEKLY", route_revenue_weekly),
            ("VANGUARD_BASELINES", vanguard_baselines),
            ("TRAILER_VCI", trailer_vci),
            ("VANGUARD_ALERTS", vanguard_alerts),
        ]:
            if frame is not None and not frame.empty:
                frame.to_excel(writer, sheet_name=name, index=False)

        build_info_sheet().to_excel(writer, sheet_name="INFO", index=False)
        build_error_reference().to_excel(writer, sheet_name="ERROR_REFERENCE", index=False)

    logger.info("Excel file written: %s", output_path)
    return output_path
