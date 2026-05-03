"""Route-level KPI computation module.

Pure functions that consume already-normalized pipeline outputs (stops with
OTP, M3PL billing snapshots, telemetry stop aggregates) and produce route- /
equipment- / billing-level rollup tables.

These functions never recompute OTP — they read the columns produced by
``datascrubb.otp.calculator``.
"""

from datascrubb.kpi.capacity import (
    attach_fill_pct,
    derive_observed_capacity,
    load_trailer_capacity,
    save_trailer_capacity,
)
from datascrubb.kpi.revenue import (
    compute_route_revenue,
    compute_route_revenue_weekly,
    compute_trailer_revenue_weekly,
    load_rate_matrix,
    rate_for,
    save_rate_matrix,
)
from datascrubb.kpi.route_kpi import (
    compute_alarm_log,
    compute_billing_recon,
    compute_claims_risk,
    compute_customer_churn_signal,
    compute_customer_concentration,
    compute_customer_scorecard,
    compute_cycle_time_consistency,
    compute_demand_forecast,
    compute_detention_audit,
    compute_driver_scorecard,
    compute_equipment_util,
    compute_lane_profitability,
    compute_late_code_analysis,
    compute_loaded_miles,
    compute_miles_variance,
    compute_route_otp,
    compute_route_reefer_cost,
    compute_temp_compliance,
    compute_trailer_utilization,
)

__all__ = [
    "compute_route_otp",
    "compute_miles_variance",
    "compute_billing_recon",
    "compute_equipment_util",
    "compute_temp_compliance",
    "compute_route_revenue",
    "compute_loaded_miles",
    "compute_driver_scorecard",
    "compute_lane_profitability",
    "compute_claims_risk",
    "compute_trailer_utilization",
    "compute_route_reefer_cost",
    "compute_alarm_log",
    "compute_customer_scorecard",
    "compute_customer_churn_signal",
    "compute_customer_concentration",
    "compute_cycle_time_consistency",
    "compute_late_code_analysis",
    "compute_detention_audit",
    "compute_demand_forecast",
    "load_trailer_capacity",
    "save_trailer_capacity",
    "derive_observed_capacity",
    "attach_fill_pct",
    "compute_trailer_revenue_weekly",
    "compute_route_revenue_weekly",
    "load_rate_matrix",
    "save_rate_matrix",
    "rate_for",
]
