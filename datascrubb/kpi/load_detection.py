"""Multi-signal trailer-load detection.

Replaces the single-signal `loaded_at_stop` flag (which trusted CRST case
counts absolutely) with a confidence score combining 5 independent signals:

1. **CRST cases** — the existing logic (current_cases > 0, or stop_direction=SO with tender_cases > 0)
2. **SAP paperwork** — a matched SAP segment exists for this stop with cases > 0 or weight > 0
3. **Reefer behavior** — telemetry consistent with cooling cargo (continuous runtime + cold cargo zone)
4. **Route sequence** — walk the route in stop_seq order; trailer becomes loaded at plasma-center pickups, empty at distribution-center deliveries
5. **BOL field** — the CRST `bol` field is populated

Each signal is 0/1 (loaded/empty) or NaN (data missing — doesn't vote). The
final verdict is `confidence = positives / non_nan_signals * 100`, with a
configurable threshold (default 50). Disputed stops (signals disagree) are
flagged for the Load Review dashboard. A persistent `load_override` table
lets the user pin a verdict when auto-detection is wrong.

The legacy `loaded_at_stop` column stays untouched. Excursion logic switches
to `loaded_at_stop_v2` via the `pipeline.excursion_uses_v2` config flag.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from datascrubb.observability import observe, quality_check

logger = logging.getLogger("datascrubb.kpi.load_detection")


# ─── Signal computation ────────────────────────────────────────────

def _signal_crst(stops: pd.DataFrame) -> pd.Series:
    """Existing CRST-cases logic, kept as one signal among five."""
    cur = stops["current_cases"] if "current_cases" in stops.columns else pd.Series(0.0, index=stops.index)
    ten = stops["tender_cases"] if "tender_cases" in stops.columns else pd.Series(0.0, index=stops.index)
    sd = stops["stop_direction"].astype(str).str.upper() if "stop_direction" in stops.columns else pd.Series("", index=stops.index)
    is_so = sd == "SO"
    return ((cur.fillna(0) > 0) | (is_so & (ten.fillna(0) > 0))).astype(int)


def _signal_sap(stops: pd.DataFrame, sap_df: pd.DataFrame | None) -> pd.Series:
    """1 when a SAP segment is matched to this stop with non-zero cases or weight.
    NaN when this stop has no SAP segment at all (no signal to vote)."""
    if sap_df is None or sap_df.empty or "transaction_id" not in sap_df.columns:
        return pd.Series([np.nan] * len(stops), index=stops.index)

    sap = sap_df.copy()
    sap["transaction_id"] = sap["transaction_id"].astype(str)
    cases = pd.to_numeric(sap.get("cases_count"), errors="coerce").fillna(0) if "cases_count" in sap.columns else 0
    weight = pd.to_numeric(sap.get("actual_weight"), errors="coerce").fillna(0) if "actual_weight" in sap.columns else 0
    sap["_loaded"] = ((cases > 0) | (weight > 0)).astype(int)
    # Per stop: 1 if ANY matched SAP segment shows load
    per_stop = sap.groupby("transaction_id")["_loaded"].max()

    out = pd.Series([np.nan] * len(stops), index=stops.index)
    txn = stops["transaction_id"].astype(str)
    matched = txn.isin(per_stop.index)
    out.loc[matched] = txn[matched].map(per_stop).astype(float)
    return out


def _signal_reefer(
    stops: pd.DataFrame,
    telemetry_stops_df: pd.DataFrame | None,
    *,
    runtime_ratio_threshold: float = 0.7,  # kept for API compat; no longer used
    cold_threshold_c: float = -15.0,       # max_cargo_temp ≤ this → loaded
    warm_threshold_c: float = 0.0,         # max_cargo_temp ≥ this → empty
) -> pd.Series:
    """Telemetry-based load signal driven by cargo-zone (S2-S6) max temp.

    The original design also used `reefer_runtime_minutes / dwell_minutes`
    as a continuity-of-cooling test, but in practice the runtime is summed
    over the wider telemetry-match window (±120 min), not just the dwell —
    yielding ratios of 10-30x that aren't comparable. Dropped that test.

    Verdict per stop:
      - 1 (loaded)  → max_cargo_temp ≤ cold_threshold_c (frozen mass keeping it cold)
      - 0 (empty)   → max_cargo_temp ≥ warm_threshold_c (no cold cargo)
      - NaN         → no telemetry, or ambiguous middle band (chilled product
                       OR empty trailer in cool ambient — can't tell)
    """
    if telemetry_stops_df is None or telemetry_stops_df.empty:
        return pd.Series([np.nan] * len(stops), index=stops.index)
    if "max_cargo_temp" not in telemetry_stops_df.columns:
        return pd.Series([np.nan] * len(stops), index=stops.index)

    tel = telemetry_stops_df[["transaction_id", "max_cargo_temp"]].copy()
    tel["transaction_id"] = tel["transaction_id"].astype(str)

    s = stops[["transaction_id"]].copy()
    s["transaction_id"] = s["transaction_id"].astype(str)
    s = s.merge(tel, on="transaction_id", how="left")

    out = pd.Series([np.nan] * len(stops), index=stops.index)
    mct = pd.to_numeric(s["max_cargo_temp"], errors="coerce")
    out.loc[(mct <= cold_threshold_c).values] = 1
    out.loc[(mct >= warm_threshold_c).values] = 0
    # ambiguous middle band stays NaN (doesn't vote)
    return out


def _signal_setpoint(
    stops: pd.DataFrame,
    telemetry_stops_df: pd.DataFrame | None,
    *,
    offline_threshold_c: float = 0.0,    # max_setpoint > this → trailer was off
    plasma_threshold_c: float = -20.0,   # min_setpoint ≤ this → in plasma cooling mode
) -> pd.Series:
    """Setpoint-based load signal (per user domain knowledge).

    Drivers set the reefer setpoint > 0°C (or turn it off entirely) when the
    trailer is unloaded and parked. When prepping for a frozen plasma load,
    they set it to -25 to -35°C and turn it on. So:

      - max_setpoint_c > 0       → trailer was off / unloaded at some point → vote EMPTY
      - min_setpoint_c ≤ -20     → trailer was actively cooling for plasma → vote LOADED
      - else                     → ambiguous (chilled product, transition) → NaN

    NaN when there's no telemetry for this stop or no setpoint data.
    """
    if telemetry_stops_df is None or telemetry_stops_df.empty:
        return pd.Series([np.nan] * len(stops), index=stops.index)
    if not {"min_setpoint_c", "max_setpoint_c"}.issubset(telemetry_stops_df.columns):
        return pd.Series([np.nan] * len(stops), index=stops.index)

    tel = telemetry_stops_df[["transaction_id", "min_setpoint_c", "max_setpoint_c"]].copy()
    tel["transaction_id"] = tel["transaction_id"].astype(str)

    s = stops[["transaction_id"]].copy()
    s["transaction_id"] = s["transaction_id"].astype(str)
    s = s.merge(tel, on="transaction_id", how="left")

    out = pd.Series([np.nan] * len(stops), index=stops.index)
    max_sp = pd.to_numeric(s["max_setpoint_c"], errors="coerce")
    # Asymmetric vote: setpoint > 0 is a STRONG empty signal (trailer was offline);
    # setpoint cold is NOT a loaded signal (drivers keep empty trailers cold for the
    # next load). So: vote empty when offline, abstain (NaN) otherwise.
    # The plasma_threshold_c parameter is preserved for API compat but not used here.
    out.loc[(max_sp > offline_threshold_c).values] = 0
    return out


def _signal_sequence(stops: pd.DataFrame) -> pd.Series:
    """Walk each route in stop_seq order:
        - PICKUP at PLASMA_CENTER (S-Code present) → trailer becomes loaded
        - DELIVERY at DISTRIBUTION_CENTER / INTERNAL_BASE → trailer becomes empty
        - State carries forward between stops
    Returns NaN per row when route_id or stop_seq is missing."""
    if "stop_seq" not in stops.columns or stops["stop_seq"].isna().all():
        return pd.Series([np.nan] * len(stops), index=stops.index)

    out = pd.Series([np.nan] * len(stops), index=stops.index)
    route_col = "route_name" if "route_name" in stops.columns else None
    if route_col is None:
        return out

    direction = stops.get("stop_direction", pd.Series("", index=stops.index)).astype(str).str.upper()
    stop_class = stops.get("stop_class", pd.Series("OTHER", index=stops.index)).astype(str).str.upper()
    s_code = stops.get("s_code", pd.Series(None, index=stops.index)).astype(str).str.strip()

    # Sort by route + stop_seq
    seq_numeric = pd.to_numeric(stops["stop_seq"], errors="coerce")
    order = stops.assign(_seq=seq_numeric).sort_values([route_col, "_seq"]).index

    for route_id, idxs in stops.loc[order].groupby(route_col, sort=False).indices.items():
        # Walk in already-sorted order
        loaded = 0
        for i in idxs:
            row_idx = stops.index[i]
            d = direction.iat[i]
            cls = stop_class.iat[i]
            sc = s_code.iat[i]
            # Pickup at plasma center → load
            if d == "PU" and cls == "PLASMA_CENTER" and sc and sc.lower() != "nan":
                loaded = 1
            # Delivery at non-plasma → unload
            elif d == "SO" and cls in ("DISTRIBUTION_CENTER", "INTERNAL_BASE", "OTHER"):
                # The stop itself was loaded (cargo just delivered); set 1 here, then 0 going forward
                out.at[row_idx] = 1
                loaded = 0
                continue
            out.at[row_idx] = loaded
    return out


def _signal_bol(stops: pd.DataFrame) -> pd.Series:
    """1 when the CRST `bol` field is non-null and non-empty.
    NaN when the column doesn't exist."""
    if "bol" not in stops.columns:
        return pd.Series([np.nan] * len(stops), index=stops.index)
    bol = stops["bol"].astype(str).str.strip()
    return ((bol != "") & (bol.str.lower() != "nan") & (bol.str.lower() != "none")).astype(int)


# ─── Verdict + confidence ──────────────────────────────────────────

@observe("compute_load_signals")
def compute_load_signals(
    stops_df: pd.DataFrame,
    sap_segments_df: pd.DataFrame | None = None,
    telemetry_stops_df: pd.DataFrame | None = None,
    *,
    confidence_threshold: int = 50,
    reefer_runtime_ratio_threshold: float = 0.7,
    reefer_max_cargo_temp_c: float = -15.0,
    enabled_signals: tuple[str, ...] = ("crst", "sap", "reefer", "setpoint", "sequence"),
) -> pd.DataFrame:
    """Compute per-stop load signals + confidence + auto verdict.

    Returns a DataFrame with one row per transaction_id and these columns:
        transaction_id, load_signal_crst, load_signal_sap, load_signal_reefer,
        load_signal_sequence, load_signal_bol, load_confidence,
        load_state_disputed, loaded_at_stop_v2 (auto, before override)
    """
    quality_check("stops_not_empty",
                  stops_df is not None and not stops_df.empty,
                  detail=f"stops={0 if stops_df is None else len(stops_df)}",
                  raise_on_fail=True)
    quality_check("transaction_id_present",
                  "transaction_id" in stops_df.columns,
                  raise_on_fail=True)

    out = pd.DataFrame({"transaction_id": stops_df["transaction_id"].astype(str).values})
    out.index = stops_df.index

    # Compute each enabled signal; disabled signals are NaN throughout (don't vote)
    signals = {
        "crst": _signal_crst(stops_df) if "crst" in enabled_signals else pd.Series([np.nan] * len(stops_df), index=stops_df.index),
        "sap": _signal_sap(stops_df, sap_segments_df) if "sap" in enabled_signals else pd.Series([np.nan] * len(stops_df), index=stops_df.index),
        "reefer": _signal_reefer(stops_df, telemetry_stops_df,
                                  cold_threshold_c=reefer_max_cargo_temp_c)
                  if "reefer" in enabled_signals else pd.Series([np.nan] * len(stops_df), index=stops_df.index),
        "setpoint": _signal_setpoint(stops_df, telemetry_stops_df)
                    if "setpoint" in enabled_signals else pd.Series([np.nan] * len(stops_df), index=stops_df.index),
        "sequence": _signal_sequence(stops_df) if "sequence" in enabled_signals else pd.Series([np.nan] * len(stops_df), index=stops_df.index),
        "bol": _signal_bol(stops_df) if "bol" in enabled_signals else pd.Series([np.nan] * len(stops_df), index=stops_df.index),
    }
    for name, series in signals.items():
        out[f"load_signal_{name}"] = series.values

    # Confidence + verdict
    sig_matrix = pd.DataFrame({k: v.values for k, v in signals.items()})
    non_nan = sig_matrix.notna().sum(axis=1)
    positives = sig_matrix.fillna(0).sum(axis=1)
    confidence = np.where(non_nan > 0, np.round(100 * positives / non_nan).astype(int), 0)
    out["load_confidence"] = confidence
    out["load_state_disputed"] = ((positives > 0) & (positives < non_nan)).astype(int).values
    out["loaded_at_stop_v2"] = (confidence >= confidence_threshold).astype(int)

    # Output invariants
    n_total = len(out)
    n_disputed = int(out["load_state_disputed"].sum())
    quality_check("not_overwhelmingly_disputed",
                  n_total == 0 or n_disputed / n_total < 0.5,
                  detail=f"{n_disputed}/{n_total} stops have signals disagreeing")
    n_no_signals = int((non_nan == 0).sum())
    quality_check("most_stops_have_at_least_one_signal",
                  n_total == 0 or n_no_signals / n_total < 0.1,
                  detail=f"{n_no_signals}/{n_total} stops have zero non-NaN signals")
    return out


# ─── Override application ──────────────────────────────────────────

def apply_load_overrides(
    load_signals_df: pd.DataFrame,
    overrides_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """Apply manual overrides to the auto verdict.

    `overrides_df` columns: transaction_id, override_value (0 or 1).
    Returns the input DataFrame with `loaded_at_stop_v2` adjusted where an
    override exists. Adds a `load_override_applied` column (0/1) for
    traceability.
    """
    out = load_signals_df.copy()
    out["load_override_applied"] = 0
    if overrides_df is None or overrides_df.empty:
        return out

    ov = overrides_df[["transaction_id", "override_value"]].copy()
    ov["transaction_id"] = ov["transaction_id"].astype(str)
    ov = ov.set_index("transaction_id")["override_value"].astype(int)

    txn = out["transaction_id"].astype(str)
    mask = txn.isin(ov.index)
    if mask.any():
        out.loc[mask, "loaded_at_stop_v2"] = txn[mask].map(ov).astype(int).values
        out.loc[mask, "load_override_applied"] = 1
    return out


# ─── Override read/write helpers (DB layer used by dashboard + pipeline) ─

def read_load_overrides(engine) -> pd.DataFrame:
    """Read all current overrides. Returns empty DataFrame if table is empty."""
    try:
        return pd.read_sql("SELECT * FROM load_override", engine)
    except Exception as e:
        logger.debug("read_load_overrides: %s", e)
        return pd.DataFrame(columns=["transaction_id", "override_value", "reason", "set_by", "set_at"])


def upsert_load_override(
    engine,
    transaction_id: str,
    override_value: int,
    reason: str = "",
    set_by: str = "manual",
) -> None:
    """Insert or update a single override. Idempotent."""
    from sqlalchemy import text
    ts = datetime.now(timezone.utc).isoformat()
    sql = text("""
        INSERT INTO load_override (transaction_id, override_value, reason, set_by, set_at)
        VALUES (:tid, :val, :reason, :by, :ts)
        ON CONFLICT(transaction_id) DO UPDATE SET
            override_value = excluded.override_value,
            reason = excluded.reason,
            set_by = excluded.set_by,
            set_at = excluded.set_at
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "tid": str(transaction_id), "val": int(override_value),
            "reason": reason or "", "by": set_by, "ts": ts,
        })


def delete_load_override(engine, transaction_id: str) -> None:
    """Remove an override by transaction_id."""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM load_override WHERE transaction_id = :tid"),
                     {"tid": str(transaction_id)})
