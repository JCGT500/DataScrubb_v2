"""Validation engine — runs all validation rules and collects results."""

import logging

import pandas as pd

from datascrubb.matching.engine import MatchResults
from datascrubb.validation.rules import (
    check_billing_dup_pro,
    check_duplicate_transaction_ids,
    check_m3pl_pro_not_in_crst,
    check_miles_variance,
    check_missing_appointment,
    check_missing_arrival,
    check_missing_scode_plasma,
    check_sap_match_rate,
    check_telemetry_coverage,
    check_temp_excursions,
)

logger = logging.getLogger("datascrubb.validation")


class ValidationEngine:
    """Runs all validation rules against match results."""

    def __init__(self, validation_config=None):
        # ValidationConfig from datascrubb.config; None = use rule defaults
        self.cfg = validation_config

    def validate(self, results: MatchResults) -> list[dict]:
        """Run all validation rules and return a flat list of error dicts."""
        errors: list[dict] = []

        crst = results.crst

        # CRST validation rules
        errors.extend(check_missing_arrival(crst))
        errors.extend(check_missing_appointment(crst))
        errors.extend(check_missing_scode_plasma(crst))
        errors.extend(check_duplicate_transaction_ids(crst))

        # SAP validation
        if results.sap_segment is not None:
            sap_floor = self.cfg.sap_match_rate_floor if self.cfg else 0.5
            errors.extend(check_sap_match_rate(results.sap_segment, threshold=sap_floor))

        # Telemetry validation
        tel_floor = self.cfg.telemetry_coverage_floor if self.cfg else 0.3
        errors.extend(check_telemetry_coverage(crst, results.telemetry_stop, threshold=tel_floor))

        # M3PL billing validation
        if results.m3pl is not None:
            errors.extend(check_billing_dup_pro(results.m3pl))
            errors.extend(check_m3pl_pro_not_in_crst(results.m3pl, crst))

        # KPI-derived validations (depend on Step 4.5 having run)
        if getattr(results, "temp_compliance", None) is not None:
            errors.extend(check_temp_excursions(results.temp_compliance))
        if getattr(results, "miles_variance", None) is not None:
            mv_threshold = self.cfg.miles_variance_threshold_pct if self.cfg else 10.0
            errors.extend(check_miles_variance(results.miles_variance, threshold_pct=mv_threshold))

        # Update error flags on the CRST DataFrame
        self._apply_error_flags(results, errors)

        # Log summary
        by_type = {}
        for e in errors:
            t = e["error_type"]
            by_type[t] = by_type.get(t, 0) + 1

        logger.info(
            "Validation complete: %d errors (%s)",
            len(errors),
            ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())),
        )

        return errors

    def _apply_error_flags(self, results: MatchResults, errors: list[dict]) -> None:
        """Update error_flag and error_reason columns on the CRST DataFrame."""
        crst = results.crst

        # Build a mapping of transaction_id -> first error reason
        error_map: dict[str, str] = {}
        for e in errors:
            txn_id = e.get("transaction_id")
            if txn_id and txn_id not in error_map:
                error_map[txn_id] = e["error_reason"]

        crst["error_flag"] = "N"
        crst["error_reason"] = ""

        for txn_id, reason in error_map.items():
            mask = crst["transaction_id"] == txn_id
            crst.loc[mask, "error_flag"] = "Y"
            crst.loc[mask, "error_reason"] = reason
