"""Helpers for reading / writing admin settings to ``config/default.yaml``.

The Admin page in the dashboard uses these to expose every tunable
threshold the pipeline reads. Values take effect on the next pipeline run.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from datascrubb.config import _project_root  # noqa: SLF001 — re-using the project-root helper

logger = logging.getLogger("datascrubb.admin")

DEFAULT_YAML_PATH = _project_root() / "config" / "default.yaml"

# Per-tab key map: which YAML block + which keys belong to each Admin tab.
# Used by the Admin page to render a focused form per tab and write back
# only the relevant block on save.
TAB_BLOCKS = {
    "Matching": {
        "yaml_block": "pipeline",
        "keys": [
            "otp_tolerance_minutes",
            "sap_match_max_hours",
            "telemetry_window_minutes",
            "telemetry_min_pings_per_stop",
            "telemetry_sample_interval_minutes",
        ],
    },
    "Validation": {
        "yaml_block": "validation",
        "keys": [
            "sap_match_rate_floor",
            "telemetry_coverage_floor",
            "miles_variance_threshold_pct",
        ],
    },
    "Reefer / Telemetry": {
        "yaml_block": "reefer",
        "keys": [
            "setpoint_c",
            "tolerance_c",
            "excursion_min_minutes",
            "door_open_speed_threshold_mph",
        ],
    },
    "Claims-Risk": {
        "yaml_block": "claims_risk",
        "keys": [
            "weight_short_cases",
            "weight_excursion",
            "weight_door_events",
            "door_event_count_threshold",
            "band_high",
            "band_medium",
        ],
    },
    "Driver Scorecard": {
        "yaml_block": "driver_scorecard",
        "keys": [
            "weight_otp",
            "weight_late_rate",
            "weight_dwell",
            "weight_cases_variance",
        ],
    },
    "Forecast & Detention": {
        "yaml_block_multi": [
            ("forecast", ["horizon_weeks", "alpha", "min_weeks_history"]),
            ("detention", ["threshold_minutes"]),
        ],
    },
    "Customer Churn": {
        "yaml_block": "churn",
        "keys": [
            "band_churn_risk_pct",
            "band_declining_pct",
            "band_growing_pct",
        ],
    },
    "Capacity": {
        "yaml_block": "capacity",
        "keys": [
            "min_observed_stops",
            "use_max_for_observed",
            "fill_pct_cap",
            "observed_quantile",
        ],
    },
    "Revenue": {
        "yaml_block": "pipeline",
        "keys": ["fuel_price_per_gallon"],
    },
    "Map UI": {
        "yaml_block": "map",
        "keys": [
            "default_max_stops_render",
            "default_height_px",
        ],
    },
    "Reefer (Vanguard SOP)": {
        "yaml_block": "vanguard",
        "keys": [
            "cargo_max_temp_c",
            "evap_delta_healthy_min", "evap_delta_healthy_max",
            "evap_delta_degrading_max", "evap_delta_significant_max",
            "evap_delta_drift_critical_c",
            "compliance_band_critical_pct", "compliance_baseline_target_pct",
            "defrost_baseline_per_day", "defrost_elevated_per_day",
            "defrost_abnormal_per_day", "defrost_max_duration_min",
            "weight_rh", "weight_dr", "weight_ts", "weight_abhf",
            "band_green_max", "band_yellow_max", "band_orange_max", "band_red_max",
            "baseline_window_days", "baseline_min_clean_days",
            "default_baseline_evap_delta", "default_baseline_compliance_pct",
        ],
    },
    "SharePoint": {
        "yaml_block": "sharepoint",
        "keys": [
            "enabled", "tenant_id", "client_id", "site_url",
            "source_folder", "db_backup_folder",
            "auto_push_db", "auto_push_excel", "keep_last_n_backups",
        ],
    },
    "Observability": {
        "yaml_block": "observability",
        "keys": ["enabled", "db_path", "summarize_dataframes", "retention_days"],
    },
    "Warehouse Inclusion": {
        "yaml_block": "warehouse_inclusion",
        "keys": [
            "otp", "dwell", "customer_scorecard", "customer_churn",
            "customer_concentration", "claims_risk", "reefer_compliance",
            "detention_audit", "late_code_analysis", "cycle_time",
            "route_kpi", "trailer_utilization", "driver_scorecard",
            "lane_profitability", "loaded_miles", "trailer_revenue_weekly",
            "route_revenue_weekly", "miles_variance", "alarm_log",
            "route_reefer_cost",
        ],
    },
}

# Built-in defaults — used by "Reset to defaults" buttons.
DEFAULTS = {
    "pipeline": {
        "otp_tolerance_minutes": 120,
        "sap_match_max_hours": 36,
        "telemetry_window_minutes": 120,
        "telemetry_min_pings_per_stop": 5,
        "telemetry_sample_interval_minutes": 15,
        "fuel_price_per_gallon": 4.50,
    },
    "validation": {
        "sap_match_rate_floor": 0.50,
        "telemetry_coverage_floor": 0.30,
        "miles_variance_threshold_pct": 10,
    },
    "reefer": {
        "setpoint_c": -25,
        "tolerance_c": 5,
        "excursion_min_minutes": 15,
        "door_open_speed_threshold_mph": 5,
    },
    "claims_risk": {
        "weight_short_cases": 0.40,
        "weight_excursion": 0.40,
        "weight_door_events": 0.20,
        "door_event_count_threshold": 5,
        "band_high": 70,
        "band_medium": 40,
    },
    "driver_scorecard": {
        "weight_otp": 0.40,
        "weight_late_rate": 0.20,
        "weight_dwell": 0.20,
        "weight_cases_variance": 0.20,
    },
    "forecast": {
        "horizon_weeks": 4,
        "alpha": 0.5,
        "min_weeks_history": 3,
    },
    "detention": {"threshold_minutes": 120},
    "churn": {
        "band_churn_risk_pct": -50,
        "band_declining_pct": -20,
        "band_growing_pct": 50,
    },
    "capacity": {
        "min_observed_stops": 5,
        "use_max_for_observed": True,
        "fill_pct_cap": 200,
        "observed_quantile": 0.95,
    },
    "map": {
        "default_max_stops_render": 1500,
        "default_height_px": 900,
    },
    "vanguard": {
        "cargo_max_temp_c": -20,
        "evap_delta_healthy_min": -8, "evap_delta_healthy_max": -5,
        "evap_delta_degrading_max": -3, "evap_delta_significant_max": -1,
        "evap_delta_drift_critical_c": 3,
        "compliance_band_critical_pct": 75, "compliance_baseline_target_pct": 92,
        "defrost_baseline_per_day": 6, "defrost_elevated_per_day": 8,
        "defrost_abnormal_per_day": 9, "defrost_max_duration_min": 40,
        "weight_rh": 0.40, "weight_dr": 0.20, "weight_ts": 0.20, "weight_abhf": 0.20,
        "band_green_max": 24, "band_yellow_max": 49,
        "band_orange_max": 74, "band_red_max": 99,
        "baseline_window_days": 30, "baseline_min_clean_days": 7,
        "default_baseline_evap_delta": -6.5, "default_baseline_compliance_pct": 90,
    },
    "sharepoint": {
        "enabled": False,
        "tenant_id": "", "client_id": "", "site_url": "",
        "source_folder": "Shared Documents/DataScrubb/Sources",
        "db_backup_folder": "Shared Documents/DataScrubb/Backups",
        "auto_push_db": True, "auto_push_excel": True,
        "keep_last_n_backups": 12,
    },
    "observability": {
        "enabled": False,
        "db_path": "data/observability.db",
        "summarize_dataframes": True,
        "retention_days": 30,
    },
    "warehouse_inclusion": {
        "otp": False, "dwell": False, "customer_scorecard": False,
        "customer_churn": False, "customer_concentration": False,
        "claims_risk": False, "reefer_compliance": False,
        "detention_audit": False, "late_code_analysis": False, "cycle_time": False,
        "route_kpi": True, "trailer_utilization": True, "driver_scorecard": True,
        "lane_profitability": True, "loaded_miles": True,
        "trailer_revenue_weekly": True, "route_revenue_weekly": True,
        "miles_variance": True, "alarm_log": True, "route_reefer_cost": True,
    },
}


def load_yaml(path: Path | None = None) -> dict:
    """Load the full default.yaml as a dict (returns empty dict if missing)."""
    p = Path(path) if path else DEFAULT_YAML_PATH
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(data: dict, path: Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_YAML_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    logger.info("Wrote admin settings to %s", p)


def update_block(
    block: str,
    new_values: dict,
    path: Path | None = None,
) -> dict:
    """Merge ``new_values`` into the named YAML block and save. Returns the new full dict."""
    full = load_yaml(path)
    full.setdefault(block, {})
    full[block].update(new_values)
    save_yaml(full, path)
    return full


def reset_block(block: str, path: Path | None = None) -> dict:
    """Reset a block to built-in defaults."""
    if block not in DEFAULTS:
        raise KeyError(f"No defaults defined for block: {block}")
    full = load_yaml(path)
    full[block] = dict(DEFAULTS[block])
    save_yaml(full, path)
    return full
