"""Pipeline orchestrator — the main entry point for running the data pipeline.

Flow: load adapters -> calculate OTP -> run matching -> validate -> persist to SQLite -> export
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datascrubb.adapters import get_adapter
from datascrubb.config import Config, load_config
from datascrubb.db import (
    get_engine,
    get_session,
    init_db,
    persist_errors,
    persist_run,
    upsert_billing,
    upsert_sap_segments,
    upsert_stops,
    upsert_telemetry_stops,
)
from datascrubb.export.excel import export_to_excel
from datascrubb.kpi import (
    attach_fill_pct,
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
    compute_route_revenue,
    compute_route_revenue_weekly,
    compute_trailer_revenue_weekly,
    compute_trailer_vci,
    compute_unit_baselines,
    compute_vanguard_alerts,
    compute_temp_compliance,
    compute_trailer_utilization,
)
from datascrubb.matching.engine import MatchingEngine
from datascrubb.models import PipelineRun
from datascrubb.otp.calculator import calculate_otp
from datascrubb.utils.logging_setup import setup_logging
from datascrubb.validation.engine import ValidationEngine

logger = logging.getLogger("datascrubb.pipeline")


class Pipeline:
    """Orchestrates the full data pipeline."""

    def __init__(self, config: Config | None = None):
        self.config = config or load_config()
        self.logger = setup_logging(
            log_file=self.config.root / self.config.logging.file,
            level=self.config.logging.level,
        )

    def run(
        self,
        source_files: dict[str, str | Path],
        export_excel: bool = True,
        output_filename: str | None = None,
    ) -> dict:
        """Run the full pipeline.

        Args:
            source_files: Mapping of source name to file path or list of paths.
                          Keys: "crst" (required), "sap" (optional),
                          "telemetry" (optional), "m3pl" (optional, accepts a
                          single path or a list of weekly invoice files).
            export_excel: Whether to export results to Excel.
            output_filename: Custom output filename. Defaults to timestamped name.

        Returns:
            dict with run_id, output_path, stats, and errors.
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        run_start = datetime.now(timezone.utc)

        logger.info("=" * 60)
        logger.info("Pipeline run %s started", run_id)
        logger.info("Sources: %s", list(source_files.keys()))

        # Initialize database
        engine = get_engine(self.config.db_path)
        init_db(engine)

        # Track raw DataFrames for Excel export
        raw_data: dict[str, pd.DataFrame] = {}
        records_read: dict[str, int] = {}

        try:
            # ─── Step 1: Load and normalize each source ───
            crst_df = None
            sap_df = None
            telemetry_df = None
            m3pl_df = None

            if "crst" not in source_files:
                raise ValueError("CRST source file is required")

            def _as_paths(value) -> list[Path]:
                if isinstance(value, (list, tuple)):
                    return [Path(p) for p in value]
                return [Path(value)]

            # CRST (required, multi-file supported)
            crst_source_config = self.config.sources.get("crst")
            crst_paths = _as_paths(source_files["crst"])
            crst_raw_frames = []
            crst_norm_frames = []
            crst_adapter = None
            for p in crst_paths:
                adapter = get_adapter("crst")(crst_source_config)
                raw = adapter.load_raw(p)
                crst_raw_frames.append(raw)
                norm = adapter.normalize(raw)
                norm["_source_file"] = p.name
                crst_norm_frames.append(norm)
                crst_adapter = adapter  # last one wins; used for header_row in run metadata
            raw_data["crst"] = pd.concat(crst_raw_frames, ignore_index=True) if crst_raw_frames else pd.DataFrame()
            records_read["crst"] = len(raw_data["crst"])
            crst_df = pd.concat(crst_norm_frames, ignore_index=True) if crst_norm_frames else pd.DataFrame()
            # De-dupe stops that appeared in multiple files (keep first)
            if "transaction_id" in crst_df.columns:
                before = len(crst_df)
                crst_df = crst_df.drop_duplicates(subset=["transaction_id"], keep="first")
                if len(crst_df) < before:
                    logger.info("CRST: dropped %d duplicate transaction_ids across files", before - len(crst_df))
            logger.info("CRST processed: %d stops from %d file(s)", len(crst_df), len(crst_paths))

            # SAP (optional, multi-file supported)
            if "sap" in source_files:
                sap_source_config = self.config.sources.get("sap")
                sap_paths = _as_paths(source_files["sap"])
                sap_raw_frames, sap_norm_frames = [], []
                for p in sap_paths:
                    adapter = get_adapter("sap")(sap_source_config)
                    raw = adapter.load_raw(p)
                    sap_raw_frames.append(raw)
                    sap_norm_frames.append(adapter.normalize(raw))
                raw_data["sap"] = pd.concat(sap_raw_frames, ignore_index=True)
                records_read["sap"] = len(raw_data["sap"])
                sap_df = pd.concat(sap_norm_frames, ignore_index=True)
                logger.info("SAP processed: %d rows from %d file(s)", len(sap_df), len(sap_paths))

            # Telemetry (optional, multi-file supported)
            if "telemetry" in source_files:
                tel_source_config = self.config.sources.get("telemetry")
                tel_paths = _as_paths(source_files["telemetry"])
                tel_raw_frames, tel_norm_frames = [], []
                for p in tel_paths:
                    adapter = get_adapter("telemetry")(tel_source_config)
                    raw = adapter.load_raw(p)
                    tel_raw_frames.append(raw)
                    tel_norm_frames.append(adapter.normalize(raw))
                raw_data["telemetry"] = pd.concat(tel_raw_frames, ignore_index=True)
                records_read["telemetry"] = len(raw_data["telemetry"])
                telemetry_df = pd.concat(tel_norm_frames, ignore_index=True)
                logger.info("Telemetry processed: %d events from %d file(s)", len(telemetry_df), len(tel_paths))

            # M3PL billing (optional, accepts list of weekly files)
            if "m3pl" in source_files:
                m3pl_paths = source_files["m3pl"]
                if not isinstance(m3pl_paths, (list, tuple)):
                    m3pl_paths = [m3pl_paths]
                m3pl_source_config = self.config.sources.get("m3pl")
                m3pl_frames: list[pd.DataFrame] = []
                m3pl_rows_read = 0
                for p in m3pl_paths:
                    m3pl_adapter = get_adapter("m3pl")(m3pl_source_config)
                    m3pl_raw = m3pl_adapter.load_raw(Path(p))
                    m3pl_rows_read += len(m3pl_raw)
                    frame = m3pl_adapter.normalize(m3pl_raw)
                    if not frame.empty:
                        m3pl_frames.append(frame)
                if m3pl_frames:
                    m3pl_df = pd.concat(m3pl_frames, ignore_index=True)
                    raw_data["m3pl"] = m3pl_df.copy()
                    records_read["m3pl"] = m3pl_rows_read
                    logger.info(
                        "M3PL processed: %d billing rows across %d weekly files",
                        len(m3pl_df), len(m3pl_paths),
                    )

            # ─── Step 2: Calculate OTP on CRST ───
            crst_df = calculate_otp(crst_df, self.config.pipeline.otp_tolerance_minutes)

            # ─── Step 2b: Attach trailer fill % from capacity matrix ───
            crst_df = attach_fill_pct(crst_df)

            # ─── Step 3: Run cross-source matching ───
            matching_engine = MatchingEngine(self.config.pipeline, full_config=self.config)
            results = matching_engine.run(crst_df, sap_df, telemetry_df, m3pl_df)

            # ─── Step 4: Compute route-level KPIs (before validation so KPI-derived rules can fire) ───
            cfg = self.config
            inc = cfg.warehouse_inclusion

            # Pre-filter helper: shortcut to plasma-only view
            from datascrubb.kpi.route_kpi import _filter_warehouses
            crst_full = results.crst
            crst_plasma = _filter_warehouses(crst_full, include_warehouses=False)

            results.route_kpi = compute_route_otp(_filter_warehouses(crst_full, inc.route_kpi))
            results.miles_variance = compute_miles_variance(
                _filter_warehouses(crst_full, inc.miles_variance), results.m3pl,
            )
            results.billing_recon = compute_billing_recon(results.m3pl)
            equip = compute_equipment_util(
                _filter_warehouses(crst_full, inc.driver_scorecard), results.m3pl,
            )
            results.equip_util_tractor = equip.get("tractor")
            results.equip_util_trailer = equip.get("trailer")
            results.equip_util_driver = equip.get("driver")
            results.temp_compliance = compute_temp_compliance(
                _filter_warehouses(crst_full, inc.reefer_compliance),
                setpoint_c=cfg.reefer.setpoint_c,
                tolerance_c=cfg.reefer.tolerance_c,
                min_excursion_minutes=cfg.reefer.excursion_min_minutes,
            )
            results.route_revenue = compute_route_revenue(crst_full, results.m3pl)  # revenue uses full crst for stop count
            results.loaded_miles = compute_loaded_miles(
                _filter_warehouses(crst_full, inc.loaded_miles), results.m3pl,
            )
            results.driver_scorecard = compute_driver_scorecard(
                _filter_warehouses(crst_full, inc.driver_scorecard),
                weight_otp=cfg.driver_scorecard.weight_otp,
                weight_late_rate=cfg.driver_scorecard.weight_late_rate,
                weight_dwell=cfg.driver_scorecard.weight_dwell,
                weight_cases_variance=cfg.driver_scorecard.weight_cases_variance,
            )
            results.lane_profitability = compute_lane_profitability(
                results.route_revenue, _filter_warehouses(crst_full, inc.lane_profitability),
            )
            results.claims_risk = compute_claims_risk(
                _filter_warehouses(crst_full, inc.claims_risk),
                setpoint_c=cfg.reefer.setpoint_c,
                tolerance_c=cfg.reefer.tolerance_c,
                weight_short_cases=cfg.claims_risk.weight_short_cases,
                weight_excursion=cfg.claims_risk.weight_excursion,
                weight_door_events=cfg.claims_risk.weight_door_events,
                door_event_count_threshold=cfg.claims_risk.door_event_count_threshold,
                band_high=cfg.claims_risk.band_high,
                band_medium=cfg.claims_risk.band_medium,
            )
            results.trailer_utilization = compute_trailer_utilization(
                _filter_warehouses(crst_full, inc.trailer_utilization), results.m3pl,
            )
            results.route_reefer_cost = compute_route_reefer_cost(
                _filter_warehouses(crst_full, inc.route_reefer_cost),
            )
            results.alarm_log = compute_alarm_log(_filter_warehouses(crst_full, inc.alarm_log))
            results.customer_scorecard = compute_customer_scorecard(
                _filter_warehouses(crst_full, inc.customer_scorecard), results.route_revenue,
            )
            results.customer_churn = compute_customer_churn_signal(
                _filter_warehouses(crst_full, inc.customer_churn),
                band_churn_risk_pct=cfg.churn.band_churn_risk_pct,
                band_declining_pct=cfg.churn.band_declining_pct,
                band_growing_pct=cfg.churn.band_growing_pct,
            )
            results.customer_concentration = compute_customer_concentration(results.route_revenue)
            results.cycle_time = compute_cycle_time_consistency(
                _filter_warehouses(crst_full, inc.cycle_time),
            )
            results.late_code_analysis = compute_late_code_analysis(
                _filter_warehouses(crst_full, inc.late_code_analysis),
            )
            results.detention_audit = compute_detention_audit(
                _filter_warehouses(crst_full, inc.detention_audit),
                threshold_minutes=cfg.detention.threshold_minutes,
            )
            results.demand_forecast = compute_demand_forecast(
                _filter_warehouses(crst_full, inc.customer_scorecard),
                horizon_weeks=cfg.forecast.horizon_weeks,
                alpha=cfg.forecast.alpha,
                min_weeks_history=cfg.forecast.min_weeks_history,
            )
            results.trailer_revenue_weekly = compute_trailer_revenue_weekly(
                _filter_warehouses(crst_full, inc.trailer_revenue_weekly), results.route_revenue,
            )
            results.route_revenue_weekly = compute_route_revenue_weekly(
                _filter_warehouses(crst_full, inc.route_revenue_weekly), results.route_revenue,
            )

            # ─── Vanguard Reefer Diagnostics ───
            results.vanguard_baselines = compute_unit_baselines(crst_full, cfg.vanguard)
            results.trailer_vci = compute_trailer_vci(crst_full, results.vanguard_baselines, cfg.vanguard)
            results.vanguard_alerts = compute_vanguard_alerts(crst_full, results.trailer_vci, cfg.vanguard)
            logger.info(
                "KPI rollups: routes=%d, billing_recon_rows=%d, drivers=%d, revenue_rows=%d, "
                "loaded_miles=%d, scorecards=%d, lanes=%d, risk_rows=%d",
                0 if results.route_kpi is None else len(results.route_kpi),
                0 if results.billing_recon is None else len(results.billing_recon),
                0 if results.equip_util_driver is None else len(results.equip_util_driver),
                0 if results.route_revenue is None else len(results.route_revenue),
                0 if results.loaded_miles is None else len(results.loaded_miles),
                0 if results.driver_scorecard is None else len(results.driver_scorecard),
                0 if results.lane_profitability is None else len(results.lane_profitability),
                0 if results.claims_risk is None else len(results.claims_risk),
            )

            # ─── Step 5: Run validation (uses KPI rollups for some checks) ───
            validation_engine = ValidationEngine(validation_config=cfg.validation)
            errors = validation_engine.validate(results)

            # ─── Step 5: Persist to SQLite ───
            with get_session(engine) as session:
                # Persist run record first so validation_error FK references resolve
                run_record = PipelineRun(
                    run_id=run_id,
                    run_timestamp=run_start,
                    source_files=json.dumps({k: str(v) for k, v in source_files.items()}),
                    records_read=json.dumps(records_read),
                    records_output=json.dumps({
                        "stop_master": len(results.crst),
                        "billing_snapshot": int(0 if results.m3pl is None else len(results.m3pl)),
                    }),
                    match_rates=json.dumps({
                        "sap_match_rate": f"{results.sap_match_rate:.2%}",
                        "telemetry_coverage": f"{results.telemetry_coverage:.2%}",
                        "m3pl_match_rate": f"{results.m3pl_match_rate:.2%}",
                    }),
                    status="SUCCESS",
                )
                persist_run(session, run_record)
                session.flush()

                upsert_stops(session, results.crst)
                if results.sap_segment is not None:
                    upsert_sap_segments(session, results.sap_segment)
                if results.telemetry_stop is not None:
                    upsert_telemetry_stops(session, results.telemetry_stop)
                if results.m3pl is not None:
                    upsert_billing(session, results.m3pl)
                persist_errors(session, errors, run_id)

            # Persist KPI rollups outside the session block — pd.to_sql
            # opens its own connection and would otherwise deadlock on SQLite.
            self._persist_kpi(engine, results)

            # ─── Step 6: Export Excel ───
            output_path = None
            if export_excel:
                if output_filename is None:
                    output_filename = f"Trans_KPI_Validation_{run_id}.xlsx"

                output_path = self.config.root / self.config.export.output_dir / output_filename

                # Build run metadata DataFrame for Excel
                run_meta_df = self._build_run_metadata_df(
                    run_id, run_start, source_files, records_read,
                    crst_df, crst_adapter, results, errors,
                )

                export_to_excel(
                    output_path=output_path,
                    run_metadata=run_meta_df,
                    crst_raw=raw_data.get("crst", pd.DataFrame()),
                    sap_raw=raw_data.get("sap"),
                    telemetry_raw=raw_data.get("telemetry"),
                    stop_master=results.crst,
                    sap_segment=results.sap_segment,
                    telemetry_stop=results.telemetry_stop,
                    billing_snapshot=results.m3pl,
                    route_kpi=results.route_kpi,
                    miles_variance=results.miles_variance,
                    billing_recon=results.billing_recon,
                    equip_util_tractor=results.equip_util_tractor,
                    equip_util_trailer=results.equip_util_trailer,
                    equip_util_driver=results.equip_util_driver,
                    temp_compliance=results.temp_compliance,
                    route_revenue=results.route_revenue,
                    loaded_miles=results.loaded_miles,
                    driver_scorecard=results.driver_scorecard,
                    lane_profitability=results.lane_profitability,
                    claims_risk=results.claims_risk,
                    trailer_utilization=results.trailer_utilization,
                    route_reefer_cost=results.route_reefer_cost,
                    alarm_log=results.alarm_log,
                    customer_scorecard=results.customer_scorecard,
                    customer_churn=results.customer_churn,
                    customer_concentration=results.customer_concentration,
                    cycle_time=results.cycle_time,
                    late_code_analysis=results.late_code_analysis,
                    detention_audit=results.detention_audit,
                    demand_forecast=results.demand_forecast,
                    trailer_revenue_weekly=results.trailer_revenue_weekly,
                    route_revenue_weekly=results.route_revenue_weekly,
                    vanguard_baselines=results.vanguard_baselines,
                    trailer_vci=results.trailer_vci,
                    vanguard_alerts=results.vanguard_alerts,
                )

            logger.info("Pipeline run %s completed successfully", run_id)

            return {
                "run_id": run_id,
                "status": "SUCCESS",
                "output_path": str(output_path) if output_path else None,
                "records_read": records_read,
                "stops_final": len(results.crst),
                "billing_rows": int(0 if results.m3pl is None else len(results.m3pl)),
                "sap_match_rate": f"{results.sap_match_rate:.2%}",
                "telemetry_coverage": f"{results.telemetry_coverage:.2%}",
                "m3pl_match_rate": f"{results.m3pl_match_rate:.2%}",
                "errors_total": len(errors),
                "errors_hard": sum(1 for e in errors if e["error_type"] == "HARD"),
                "errors_soft": sum(1 for e in errors if e["error_type"] == "SOFT"),
                "errors_warning": sum(1 for e in errors if e["error_type"] == "WARNING"),
            }

        except Exception as exc:
            logger.error("Pipeline run %s FAILED: %s", run_id, exc, exc_info=True)

            # Persist the failed run record
            try:
                with get_session(engine) as session:
                    run_record = PipelineRun(
                        run_id=run_id,
                        run_timestamp=run_start,
                        source_files=json.dumps({k: str(v) for k, v in source_files.items()}),
                        records_read=json.dumps(records_read),
                        status="FAILED",
                        error_message=str(exc),
                    )
                    persist_run(session, run_record)
            except Exception:
                logger.error("Failed to persist error run record", exc_info=True)

            raise

    def _persist_kpi(self, engine, results) -> None:
        """Write KPI rollup tables to SQLite via pandas to_sql (replace each run).

        Tables: route_kpi, miles_variance, billing_recon,
        equip_util_tractor, equip_util_trailer, equip_util_driver, temp_compliance.
        """
        for name, frame in [
            ("route_kpi", results.route_kpi),
            ("miles_variance", results.miles_variance),
            ("billing_recon", results.billing_recon),
            ("equip_util_tractor", results.equip_util_tractor),
            ("equip_util_trailer", results.equip_util_trailer),
            ("equip_util_driver", results.equip_util_driver),
            ("temp_compliance", results.temp_compliance),
            ("route_revenue", results.route_revenue),
            ("loaded_miles", results.loaded_miles),
            ("driver_scorecard", results.driver_scorecard),
            ("lane_profitability", results.lane_profitability),
            ("claims_risk", results.claims_risk),
            ("trailer_utilization", results.trailer_utilization),
            ("route_reefer_cost", results.route_reefer_cost),
            ("alarm_log", results.alarm_log),
            ("customer_scorecard", results.customer_scorecard),
            ("customer_churn", results.customer_churn),
            ("customer_concentration", results.customer_concentration),
            ("cycle_time", results.cycle_time),
            ("late_code_analysis", results.late_code_analysis),
            ("detention_audit", results.detention_audit),
            ("demand_forecast", results.demand_forecast),
            ("trailer_revenue_weekly", results.trailer_revenue_weekly),
            ("route_revenue_weekly", results.route_revenue_weekly),
            ("vanguard_baselines", results.vanguard_baselines),
            ("trailer_vci", results.trailer_vci),
            ("vanguard_alerts", results.vanguard_alerts),
        ]:
            if frame is not None and not frame.empty:
                frame.to_sql(name, engine, if_exists="replace", index=False)
                logger.info("Wrote %s table: %d rows", name, len(frame))

    def _build_run_metadata_df(
        self, run_id, run_start, source_files, records_read,
        crst_df, crst_adapter, results, errors,
    ) -> pd.DataFrame:
        """Build the RUN_METADATA DataFrame for Excel export."""
        rows = [
            ("Run_ID", run_id),
            ("Run_Timestamp", run_start.strftime("%Y-%m-%d %H:%M:%S")),
            ("Source_CRST_File", str(source_files.get("crst", "N/A"))),
            ("CRST_Header_Row_Used", str((crst_adapter.header_row or 0) + 1)),
            ("CRST_Rows_Read", str(records_read.get("crst", 0))),
            ("CRST_Stops_Final", str(len(crst_df))),
            ("OTP_Tolerance_Minutes", str(self.config.pipeline.otp_tolerance_minutes)),
        ]

        if "sap" in source_files:
            rows.extend([
                ("Source_SAP_File", str(source_files["sap"])),
                ("SAP_Rows_Read", str(records_read.get("sap", 0))),
                ("SAP_Match_Rate", f"{results.sap_match_rate:.2%}"),
            ])

        if "telemetry" in source_files:
            rows.extend([
                ("Source_Telemetry_File", str(source_files["telemetry"])),
                ("Telemetry_Rows_Read", str(records_read.get("telemetry", 0))),
                ("Telemetry_Coverage", f"{results.telemetry_coverage:.2%}"),
            ])

        if "m3pl" in source_files:
            m3pl_files = source_files["m3pl"]
            if not isinstance(m3pl_files, (list, tuple)):
                m3pl_files = [m3pl_files]
            rows.extend([
                ("Source_M3PL_Files", "; ".join(str(p) for p in m3pl_files)),
                ("M3PL_Rows_Read", str(records_read.get("m3pl", 0))),
                ("M3PL_Billing_Rows", str(0 if results.m3pl is None else len(results.m3pl))),
                ("M3PL_Match_Rate", f"{results.m3pl_match_rate:.2%}"),
                ("M3PL_Total_Billed", (
                    f"${results.m3pl['billed_amount'].sum():,.2f}"
                    if results.m3pl is not None and not results.m3pl.empty else "$0.00"
                )),
            ])

        rows.append(("Stops_With_Errors", str(sum(1 for e in errors if e.get("transaction_id")))))

        return pd.DataFrame(rows, columns=["Field", "Value"])
