"""Configuration loader for DataScrubb pipeline."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


def _project_root() -> Path:
    """Return the project root (parent of datascrubb/ package)."""
    return Path(__file__).resolve().parent.parent


@dataclass
class DatabaseConfig:
    path: str = "data/datascrubb.db"

    def resolve_path(self, root: Path) -> Path:
        """Resolve the DB path relative to project root, respecting env override."""
        override = os.environ.get("DATASCRUBB_DB_PATH")
        if override:
            return Path(override)
        return root / self.path


@dataclass
class PipelineConfig:
    otp_tolerance_minutes: int = 120
    sap_match_max_hours: int = 36
    telemetry_window_minutes: int = 120
    telemetry_min_pings_per_stop: int = 5
    telemetry_sample_interval_minutes: int = 15
    fuel_price_per_gallon: float = 4.50


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/pipeline.log"


@dataclass
class ExportConfig:
    output_dir: str = "output/"


@dataclass
class ReeferConfig:
    setpoint_c: float = -25.0
    tolerance_c: float = 5.0
    excursion_min_minutes: int = 15
    door_open_speed_threshold_mph: float = 5.0


@dataclass
class ClaimsRiskConfig:
    weight_short_cases: float = 0.40
    weight_excursion: float = 0.40
    weight_door_events: float = 0.20
    door_event_count_threshold: int = 5
    band_high: int = 70
    band_medium: int = 40


@dataclass
class DriverScorecardConfig:
    weight_otp: float = 0.40
    weight_late_rate: float = 0.20
    weight_dwell: float = 0.20
    weight_cases_variance: float = 0.20


@dataclass
class ForecastConfig:
    horizon_weeks: int = 4
    alpha: float = 0.5
    min_weeks_history: int = 3


@dataclass
class DetentionConfig:
    threshold_minutes: int = 120


@dataclass
class ChurnConfig:
    band_churn_risk_pct: float = -50.0
    band_declining_pct: float = -20.0
    band_growing_pct: float = 50.0


@dataclass
class CapacityConfig:
    min_observed_stops: int = 5
    use_max_for_observed: bool = True
    fill_pct_cap: float = 200.0
    observed_quantile: float = 0.95


@dataclass
class ValidationConfig:
    sap_match_rate_floor: float = 0.50
    telemetry_coverage_floor: float = 0.30
    miles_variance_threshold_pct: float = 10.0


@dataclass
class MapConfig:
    default_max_stops_render: int = 1500
    default_height_px: int = 900


@dataclass
class VanguardConfig:
    cargo_max_temp_c: float = -20.0
    evap_delta_healthy_min: float = -8.0
    evap_delta_healthy_max: float = -5.0
    evap_delta_degrading_max: float = -3.0
    evap_delta_significant_max: float = -1.0
    evap_delta_drift_critical_c: float = 3.0
    compliance_band_critical_pct: float = 75.0
    compliance_baseline_target_pct: float = 92.0
    defrost_baseline_per_day: float = 6.0
    defrost_elevated_per_day: float = 8.0
    defrost_abnormal_per_day: float = 9.0
    defrost_max_duration_min: float = 40.0
    weight_rh: float = 0.40
    weight_dr: float = 0.20
    weight_ts: float = 0.20
    weight_abhf: float = 0.20
    band_green_max: int = 24
    band_yellow_max: int = 49
    band_orange_max: int = 74
    band_red_max: int = 99
    baseline_window_days: int = 30
    baseline_min_clean_days: int = 7
    default_baseline_evap_delta: float = -6.5
    default_baseline_compliance_pct: float = 90.0


@dataclass
class StopClassificationConfig:
    use_s_code_for_plasma: bool = True
    rules: list[dict] = field(default_factory=list)
    default_class: str = "OTHER"


@dataclass
class WarehouseInclusionConfig:
    # Customer-facing KPIs default to plasma-only.
    otp: bool = False
    dwell: bool = False
    customer_scorecard: bool = False
    customer_churn: bool = False
    customer_concentration: bool = False
    claims_risk: bool = False
    reefer_compliance: bool = False
    detention_audit: bool = False
    late_code_analysis: bool = False
    cycle_time: bool = False
    # Fleet / asset KPIs default to all stops.
    route_kpi: bool = True
    trailer_utilization: bool = True
    driver_scorecard: bool = True
    lane_profitability: bool = True
    loaded_miles: bool = True
    trailer_revenue_weekly: bool = True
    route_revenue_weekly: bool = True
    miles_variance: bool = True
    alarm_log: bool = True
    route_reefer_cost: bool = True


@dataclass
class HeaderDetectionConfig:
    method: str = "first_row"
    keywords: list[str] = field(default_factory=list)
    max_rows: int = 15
    row: int = 0


@dataclass
class SourceConfig:
    adapter: str = ""
    file_type: str = "excel"
    header_detection: HeaderDetectionConfig = field(default_factory=HeaderDetectionConfig)
    required_columns: list[str] = field(default_factory=list)


@dataclass
class Config:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    reefer: ReeferConfig = field(default_factory=ReeferConfig)
    claims_risk: ClaimsRiskConfig = field(default_factory=ClaimsRiskConfig)
    driver_scorecard: DriverScorecardConfig = field(default_factory=DriverScorecardConfig)
    forecast: ForecastConfig = field(default_factory=ForecastConfig)
    detention: DetentionConfig = field(default_factory=DetentionConfig)
    churn: ChurnConfig = field(default_factory=ChurnConfig)
    capacity: CapacityConfig = field(default_factory=CapacityConfig)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    map: MapConfig = field(default_factory=MapConfig)
    vanguard: VanguardConfig = field(default_factory=VanguardConfig)
    stop_classification: StopClassificationConfig = field(default_factory=StopClassificationConfig)
    warehouse_inclusion: WarehouseInclusionConfig = field(default_factory=WarehouseInclusionConfig)
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    root: Path = field(default_factory=_project_root)

    @property
    def db_path(self) -> Path:
        return self.database.resolve_path(self.root)


def _parse_source(data: dict) -> SourceConfig:
    header_data = data.get("header_detection", {})
    header = HeaderDetectionConfig(
        method=header_data.get("method", "first_row"),
        keywords=header_data.get("keywords", []),
        max_rows=header_data.get("max_rows", 15),
        row=header_data.get("row", 0),
    )
    return SourceConfig(
        adapter=data.get("adapter", ""),
        file_type=data.get("file_type", "excel"),
        header_detection=header,
        required_columns=data.get("required_columns", []),
    )


def load_config(
    config_path: str | Path | None = None,
    sources_path: str | Path | None = None,
) -> Config:
    """Load configuration from YAML files.

    Falls back to config/default.yaml and config/sources.yaml relative to project root.
    """
    root = _project_root()

    # Load default config
    cfg_file = Path(config_path) if config_path else root / "config" / "default.yaml"
    cfg_data = {}
    if cfg_file.exists():
        with open(cfg_file, "r") as f:
            cfg_data = yaml.safe_load(f) or {}

    # Load sources config
    src_file = Path(sources_path) if sources_path else root / "config" / "sources.yaml"
    src_data = {}
    if src_file.exists():
        with open(src_file, "r") as f:
            src_data = yaml.safe_load(f) or {}

    db_section = cfg_data.get("database", {})
    pipeline_section = cfg_data.get("pipeline", {})
    logging_section = cfg_data.get("logging", {})
    export_section = cfg_data.get("export", {})
    reefer_section = cfg_data.get("reefer", {}) or {}
    claims_section = cfg_data.get("claims_risk", {}) or {}
    driver_section = cfg_data.get("driver_scorecard", {}) or {}
    forecast_section = cfg_data.get("forecast", {}) or {}
    detention_section = cfg_data.get("detention", {}) or {}
    churn_section = cfg_data.get("churn", {}) or {}
    capacity_section = cfg_data.get("capacity", {}) or {}
    validation_section = cfg_data.get("validation", {}) or {}
    map_section = cfg_data.get("map", {}) or {}
    vanguard_section = cfg_data.get("vanguard", {}) or {}
    classify_section = cfg_data.get("stop_classification", {}) or {}
    inclusion_section = cfg_data.get("warehouse_inclusion", {}) or {}

    sources = {}
    for name, sdata in src_data.get("sources", {}).items():
        sources[name] = _parse_source(sdata)

    def _g(d: dict, k: str, default):
        v = d.get(k, default)
        return default if v is None else v

    return Config(
        database=DatabaseConfig(path=db_section.get("path", "data/datascrubb.db")),
        pipeline=PipelineConfig(
            otp_tolerance_minutes=pipeline_section.get("otp_tolerance_minutes", 120),
            sap_match_max_hours=pipeline_section.get("sap_match_max_hours", 36),
            telemetry_window_minutes=pipeline_section.get("telemetry_window_minutes", 120),
            telemetry_min_pings_per_stop=pipeline_section.get("telemetry_min_pings_per_stop", 5),
            telemetry_sample_interval_minutes=pipeline_section.get("telemetry_sample_interval_minutes", 15),
            fuel_price_per_gallon=pipeline_section.get("fuel_price_per_gallon", 4.50),
        ),
        logging=LoggingConfig(
            level=logging_section.get("level", "INFO"),
            file=logging_section.get("file", "logs/pipeline.log"),
        ),
        export=ExportConfig(output_dir=export_section.get("output_dir", "output/")),
        reefer=ReeferConfig(
            setpoint_c=_g(reefer_section, "setpoint_c", -25.0),
            tolerance_c=_g(reefer_section, "tolerance_c", 5.0),
            excursion_min_minutes=_g(reefer_section, "excursion_min_minutes", 15),
            door_open_speed_threshold_mph=_g(reefer_section, "door_open_speed_threshold_mph", 5.0),
        ),
        claims_risk=ClaimsRiskConfig(
            weight_short_cases=_g(claims_section, "weight_short_cases", 0.40),
            weight_excursion=_g(claims_section, "weight_excursion", 0.40),
            weight_door_events=_g(claims_section, "weight_door_events", 0.20),
            door_event_count_threshold=_g(claims_section, "door_event_count_threshold", 5),
            band_high=_g(claims_section, "band_high", 70),
            band_medium=_g(claims_section, "band_medium", 40),
        ),
        driver_scorecard=DriverScorecardConfig(
            weight_otp=_g(driver_section, "weight_otp", 0.40),
            weight_late_rate=_g(driver_section, "weight_late_rate", 0.20),
            weight_dwell=_g(driver_section, "weight_dwell", 0.20),
            weight_cases_variance=_g(driver_section, "weight_cases_variance", 0.20),
        ),
        forecast=ForecastConfig(
            horizon_weeks=_g(forecast_section, "horizon_weeks", 4),
            alpha=_g(forecast_section, "alpha", 0.5),
            min_weeks_history=_g(forecast_section, "min_weeks_history", 3),
        ),
        detention=DetentionConfig(
            threshold_minutes=_g(detention_section, "threshold_minutes", 120),
        ),
        churn=ChurnConfig(
            band_churn_risk_pct=_g(churn_section, "band_churn_risk_pct", -50.0),
            band_declining_pct=_g(churn_section, "band_declining_pct", -20.0),
            band_growing_pct=_g(churn_section, "band_growing_pct", 50.0),
        ),
        capacity=CapacityConfig(
            min_observed_stops=_g(capacity_section, "min_observed_stops", 5),
            use_max_for_observed=_g(capacity_section, "use_max_for_observed", True),
            fill_pct_cap=_g(capacity_section, "fill_pct_cap", 200.0),
            observed_quantile=_g(capacity_section, "observed_quantile", 0.95),
        ),
        validation=ValidationConfig(
            sap_match_rate_floor=_g(validation_section, "sap_match_rate_floor", 0.50),
            telemetry_coverage_floor=_g(validation_section, "telemetry_coverage_floor", 0.30),
            miles_variance_threshold_pct=_g(validation_section, "miles_variance_threshold_pct", 10.0),
        ),
        map=MapConfig(
            default_max_stops_render=_g(map_section, "default_max_stops_render", 1500),
            default_height_px=_g(map_section, "default_height_px", 900),
        ),
        vanguard=VanguardConfig(
            cargo_max_temp_c=_g(vanguard_section, "cargo_max_temp_c", -20.0),
            evap_delta_healthy_min=_g(vanguard_section, "evap_delta_healthy_min", -8.0),
            evap_delta_healthy_max=_g(vanguard_section, "evap_delta_healthy_max", -5.0),
            evap_delta_degrading_max=_g(vanguard_section, "evap_delta_degrading_max", -3.0),
            evap_delta_significant_max=_g(vanguard_section, "evap_delta_significant_max", -1.0),
            evap_delta_drift_critical_c=_g(vanguard_section, "evap_delta_drift_critical_c", 3.0),
            compliance_band_critical_pct=_g(vanguard_section, "compliance_band_critical_pct", 75.0),
            compliance_baseline_target_pct=_g(vanguard_section, "compliance_baseline_target_pct", 92.0),
            defrost_baseline_per_day=_g(vanguard_section, "defrost_baseline_per_day", 6.0),
            defrost_elevated_per_day=_g(vanguard_section, "defrost_elevated_per_day", 8.0),
            defrost_abnormal_per_day=_g(vanguard_section, "defrost_abnormal_per_day", 9.0),
            defrost_max_duration_min=_g(vanguard_section, "defrost_max_duration_min", 40.0),
            weight_rh=_g(vanguard_section, "weight_rh", 0.40),
            weight_dr=_g(vanguard_section, "weight_dr", 0.20),
            weight_ts=_g(vanguard_section, "weight_ts", 0.20),
            weight_abhf=_g(vanguard_section, "weight_abhf", 0.20),
            band_green_max=_g(vanguard_section, "band_green_max", 24),
            band_yellow_max=_g(vanguard_section, "band_yellow_max", 49),
            band_orange_max=_g(vanguard_section, "band_orange_max", 74),
            band_red_max=_g(vanguard_section, "band_red_max", 99),
            baseline_window_days=_g(vanguard_section, "baseline_window_days", 30),
            baseline_min_clean_days=_g(vanguard_section, "baseline_min_clean_days", 7),
            default_baseline_evap_delta=_g(vanguard_section, "default_baseline_evap_delta", -6.5),
            default_baseline_compliance_pct=_g(vanguard_section, "default_baseline_compliance_pct", 90.0),
        ),
        stop_classification=StopClassificationConfig(
            use_s_code_for_plasma=_g(classify_section, "use_s_code_for_plasma", True),
            rules=_g(classify_section, "rules", []) or [],
            default_class=_g(classify_section, "default_class", "OTHER"),
        ),
        warehouse_inclusion=WarehouseInclusionConfig(
            otp=_g(inclusion_section, "otp", False),
            dwell=_g(inclusion_section, "dwell", False),
            customer_scorecard=_g(inclusion_section, "customer_scorecard", False),
            customer_churn=_g(inclusion_section, "customer_churn", False),
            customer_concentration=_g(inclusion_section, "customer_concentration", False),
            claims_risk=_g(inclusion_section, "claims_risk", False),
            reefer_compliance=_g(inclusion_section, "reefer_compliance", False),
            detention_audit=_g(inclusion_section, "detention_audit", False),
            late_code_analysis=_g(inclusion_section, "late_code_analysis", False),
            cycle_time=_g(inclusion_section, "cycle_time", False),
            route_kpi=_g(inclusion_section, "route_kpi", True),
            trailer_utilization=_g(inclusion_section, "trailer_utilization", True),
            driver_scorecard=_g(inclusion_section, "driver_scorecard", True),
            lane_profitability=_g(inclusion_section, "lane_profitability", True),
            loaded_miles=_g(inclusion_section, "loaded_miles", True),
            trailer_revenue_weekly=_g(inclusion_section, "trailer_revenue_weekly", True),
            route_revenue_weekly=_g(inclusion_section, "route_revenue_weekly", True),
            miles_variance=_g(inclusion_section, "miles_variance", True),
            alarm_log=_g(inclusion_section, "alarm_log", True),
            route_reefer_cost=_g(inclusion_section, "route_reefer_cost", True),
        ),
        sources=sources,
        root=root,
    )
