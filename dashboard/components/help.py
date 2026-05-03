"""KPI definitions for inline ``help=`` tooltips.

Use ``kpi_help("OTP rate")`` to get a one-line definition suitable for
``st.metric(..., help=kpi_help("OTP rate"))``. Definitions cross-reference
LOGIC.md so detail-seekers can dig in.
"""

KPI_HELP: dict[str, str] = {
    # OTP / lateness
    "otp_time": "Share of stops where actual_arrival lands within ±OTP-tolerance (default 120 min) of resolved_appt. See LOGIC.md → On-Time Performance.",
    "otp_day": "Share of stops where actual_arrival's date matches resolved_appt's date.",
    "minutes_late": "Mean minutes_from_appt across stops with actual_arrival and resolved_appt populated. Negative = early.",
    "late_count": "Count of stops with stop_performance_status == 'Late'.",

    # Cost / revenue / margin
    "total_cost": "Sum of M3PL billed_amount across in-scope routes. This is what the carrier bills US.",
    "total_revenue": "Sum of (miles × $/mi + stops × $/stop + cwt × $/cwt) per route, floored at minimum_charge from the customer rate matrix. This is what we bill the CUSTOMER.",
    "total_margin": "Total revenue − total cost. The dollar margin we keep.",
    "margin_pct": "Margin / Revenue × 100. The percentage margin per route or rollup.",

    # Trailer
    "utilization_pct": "Active days (distinct dates with at least one stop) ÷ period days (max-min date in dataset). 100% = trailer worked every single day in the period.",
    "loaded_pct": "Loaded stops ÷ total stops. A stop is loaded if current_cases > 0 OR (delivery stop AND tender_cases > 0).",
    "fill_pct_cases": "current_cases ÷ trailer max_cases × 100. Capacity priority: explicit config → observed historical max → default 800. Edit on Configuration page.",
    "max_consecutive_idle_days": "Longest streak of days where this trailer had no stops at all.",
    "alarm_event_total": "Sum of telemetry alarm events (Unit Alarm == Yes) for this trailer.",
    "min_battery_seen": "Lowest battery voltage observed across all telemetry events for this trailer. Critical < 11.5V.",

    # Driver
    "driver_score": "0-100 composite. 40% OTP + 20% low-late-rate + 20% low-dwell + 20% low-cases-variance, all min-max scaled across the driver pool. Tunable in Admin.",

    # Reefer / claims
    "excursion_count": "Count of stops where loaded_at_stop=1 AND min_s1 outside (setpoint ± tolerance). Default setpoint -25°C, tolerance ±5°C.",
    "claims_risk_score": "0-100 composite per route. 40% case shortages + 40% loaded reefer excursions + 20% excess door-open events. Banded HIGH ≥70, MEDIUM ≥40, LOW >0.",
    "door_open_while_moving": "Telemetry events where Door 1 == open AND Speed > 5 mph. Safety / temp-loss flag.",

    # Customer
    "churn_band": "Week-over-week PRO# change. CHURN_RISK ≤ -50%, DECLINING -20 to -50%, STABLE -20 to +50%, GROWING ≥ +50%, NEW = no prior week.",
    "concentration_share_pct": "% of total revenue this customer represents. Cumulative % shows how few customers drive 80% of revenue.",
    "claims_per_stop": "Total |cases_variance| where < 0, divided by stop count. Higher = more shortages per visit.",

    # Detention
    "detention_billable_hours": "Sum of dwell_minutes (÷60) for stops where dwell exceeds the threshold (default 120 min). NOTE: counts the FULL dwell, not just the over-threshold portion.",

    # Forecast
    "forecast_stops": "Projected weekly stop count. Simple exponential smoothing (α=0.5) on customer's recent history. Needs ≥3 weeks of data.",

    # Loaded vs deadhead
    "estimated_loaded_miles": "M3PL miles × loaded_pct (% of inter-stop segments where the trailer carried cargo). Approximation — CRST has no per-segment mileage.",

    # Capacity
    "stop_class": "PLASMA_CENTER (S-code present), DISTRIBUTION_CENTER (RX/warehouse names), INTERNAL_BASE (CRST/THERMOKING/fuel), or OTHER. Drives the warehouse-inclusion toggles in Admin.",
}


def kpi_help(key: str) -> str:
    """Return the help text for a KPI key, or empty string if unknown."""
    return KPI_HELP.get(key, "")
