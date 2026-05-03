"""Vanguard V1 Reefer Diagnostic — VCI computation, baselines, alerts.

Implements the SOP at VanguardV1 SOPv2 Clean.docx. Three pure functions:

    compute_unit_baselines(stops_df, vanguard_cfg) -> per-trailer 30-day baseline
    compute_trailer_vci(stops_df, baselines_df, vanguard_cfg) -> per-trailer VCI + bands
    compute_vanguard_alerts(stops_df, vci_df, vanguard_cfg) -> active alerts table

VCI = Vanguard Cooling Index (the SOP refers to this as TDR / Trailer Danger
Rating; we use VCI throughout the DataScrubb codebase). 0–100, higher = more
risk. Computed as a weighted average of 4 subscores (RH/DR/TS/ABHF) with 5
hard-override rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("datascrubb.kpi.vanguard")


# ─────────────── helpers ───────────────


def _norm_trailer(s) -> str:
    if s is None or pd.isna(s):
        return ""
    out = str(s).strip().upper()
    return out if out and out != "NAN" else ""


def _band_from_vci(vci: float, cfg) -> str:
    if vci >= 100:
        return "CRITICAL"
    if vci >= cfg.band_red_max - 24:  # i.e. ≥75 by default
        return "RED"
    if vci >= cfg.band_orange_max - 24:  # ≥50
        return "ORANGE"
    if vci >= cfg.band_yellow_max - 24:  # ≥25
        return "YELLOW"
    return "GREEN"


def _band_from_vci_simple(vci: float, cfg) -> str:
    """Bands per SOP: 0-24 GREEN, 25-49 YELLOW, 50-74 ORANGE, 75-99 RED, 100 CRITICAL."""
    if vci >= 100:
        return "CRITICAL"
    if vci > cfg.band_orange_max:  # > 74
        return "RED"
    if vci > cfg.band_yellow_max:  # > 49
        return "ORANGE"
    if vci > cfg.band_green_max:   # > 24
        return "YELLOW"
    return "GREEN"


# ─────────────── 1. Baselines ───────────────


def compute_unit_baselines(stops_df: pd.DataFrame, vanguard_cfg) -> pd.DataFrame:
    """Per-trailer 30-day rolling baseline from clean stops.

    Clean = stop has telemetry, no defrost events, no door-open events,
    ≥ 2 telemetry pings (i.e. > sample interval of activity).

    Returns a DataFrame with one row per trailer:
        trailer, baseline_evap_delta, baseline_compliance_pct,
        baseline_defrost_per_day, baseline_window_days, baseline_source.
    """
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()
    if "trailer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["trailer"] = df["trailer"].apply(_norm_trailer)
    df = df[df["trailer"] != ""]
    if df.empty:
        return pd.DataFrame()

    df["arrival_dt"] = pd.to_datetime(df.get("arrival_date"), errors="coerce")

    # Window cut: last N days from max date in dataset
    if df["arrival_dt"].notna().any():
        cutoff = df["arrival_dt"].max() - pd.Timedelta(days=int(vanguard_cfg.baseline_window_days))
        df = df[df["arrival_dt"] >= cutoff]

    # Clean filter
    has_telem = df.get("telem_events", pd.Series(0, index=df.index)).fillna(0) >= 2
    no_door = df.get("door_open_events", pd.Series(0, index=df.index)).fillna(0) == 0
    no_defrost = df.get("defrost_event_count", pd.Series(0, index=df.index)).fillna(0) == 0
    clean = df[has_telem & no_door & no_defrost].copy()

    if clean.empty:
        # Whole-fleet fallback
        trailers = df["trailer"].unique()
        return pd.DataFrame({
            "trailer": trailers,
            "baseline_evap_delta": vanguard_cfg.default_baseline_evap_delta,
            "baseline_compliance_pct": vanguard_cfg.default_baseline_compliance_pct,
            "baseline_defrost_per_day": vanguard_cfg.defrost_baseline_per_day,
            "baseline_window_days": 0,
            "baseline_source": "default",
        })

    out = (
        clean.groupby("trailer")
        .agg(
            baseline_evap_delta=("avg_evap_delta", lambda s: float(pd.to_numeric(s, errors="coerce").mean()) if pd.to_numeric(s, errors="coerce").notna().any() else np.nan),
            baseline_compliance_pct=("setpoint_compliance_pct", lambda s: float(pd.to_numeric(s, errors="coerce").mean()) if pd.to_numeric(s, errors="coerce").notna().any() else np.nan),
            baseline_window_days=("arrival_dt", lambda s: int(s.dt.normalize().nunique())),
        )
        .reset_index()
    )

    # Per-trailer defrost rate (defrost events ÷ active days)
    defrost_per_day = (
        df.assign(_def=df.get("defrost_event_count", 0).fillna(0))
        .groupby("trailer")
        .agg(
            defrost_total=("_def", "sum"),
            active_days=("arrival_dt", lambda s: max(int(s.dt.normalize().nunique()), 1)),
        )
        .reset_index()
    )
    defrost_per_day["baseline_defrost_per_day"] = defrost_per_day["defrost_total"] / defrost_per_day["active_days"]
    out = out.merge(defrost_per_day[["trailer", "baseline_defrost_per_day"]], on="trailer", how="left")

    # Apply fallback when insufficient clean data
    min_days = int(vanguard_cfg.baseline_min_clean_days)
    fallback_mask = (
        (out["baseline_window_days"] < min_days)
        | out["baseline_evap_delta"].isna()
    )
    out.loc[fallback_mask, "baseline_evap_delta"] = vanguard_cfg.default_baseline_evap_delta
    out.loc[fallback_mask, "baseline_compliance_pct"] = vanguard_cfg.default_baseline_compliance_pct
    out["baseline_defrost_per_day"] = out["baseline_defrost_per_day"].fillna(vanguard_cfg.defrost_baseline_per_day)
    out["baseline_source"] = np.where(fallback_mask, "default", "rolling_30d")

    for c in ("baseline_evap_delta", "baseline_compliance_pct", "baseline_defrost_per_day"):
        out[c] = pd.to_numeric(out[c], errors="coerce").round(2)

    return out


# ─────────────── 2. VCI computation ───────────────


def _scale_band(value: float, healthy_min: float, healthy_max: float,
                degrading_max: float, significant_max: float) -> float:
    """Map an evap-delta value (negative = healthy, ≥0 = critical) to a 0-100 risk score.

    Healthy band (e.g. -8..-5) → 0
    Degrading (e.g. -5..-3) → ~35
    Significant (e.g. -3..-1) → ~70
    Critical (≥0) → 100
    """
    if pd.isna(value):
        return 0.0
    v = float(value)
    if v <= healthy_max:        # ≤ -5 → healthy
        return 0.0
    if v <= degrading_max:      # -5 to -3 → degrading
        return 35.0
    if v <= significant_max:    # -3 to -1 → significant
        return 70.0
    return 100.0                # ≥ -1 → critical / failed


def compute_trailer_vci(
    stops_df: pd.DataFrame,
    baselines_df: pd.DataFrame,
    vanguard_cfg,
) -> pd.DataFrame:
    """Per-trailer VCI (Vanguard Cooling Index) + 4 subscores + bands + readiness."""
    if stops_df is None or stops_df.empty:
        return pd.DataFrame()
    if "trailer" not in stops_df.columns:
        return pd.DataFrame()

    df = stops_df.copy()
    df["trailer"] = df["trailer"].apply(_norm_trailer)
    df = df[df["trailer"] != ""]
    if df.empty:
        return pd.DataFrame()
    df["arrival_dt"] = pd.to_datetime(df.get("arrival_date"), errors="coerce")

    # Per-trailer aggregates over the whole period (in absence of true rolling 24h
    # we use the most-recent 7 days as "current"). Same logic for cargo temp.
    if df["arrival_dt"].notna().any():
        cutoff_7d = df["arrival_dt"].max() - pd.Timedelta(days=7)
        recent = df[df["arrival_dt"] >= cutoff_7d]
        cutoff_24h = df["arrival_dt"].max() - pd.Timedelta(days=1)
        very_recent = df[df["arrival_dt"] >= cutoff_24h]
    else:
        recent = df
        very_recent = df

    def _safe_mean(s):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return float(s.mean()) if not s.empty else np.nan

    def _safe_max(s):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return float(s.max()) if not s.empty else np.nan

    def _safe_std(s):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return float(s.std()) if len(s) >= 2 else 0.0

    per_trailer = (
        recent.groupby("trailer")
        .agg(
            current_evap_delta=("avg_evap_delta", _safe_mean),
            current_compliance=("setpoint_compliance_pct", _safe_mean),
            avg_bulkhead_idx=("bulkhead_seal_index", _safe_mean),
            cargo_temp_std=("max_cargo_temp", _safe_std),
        )
        .reset_index()
    )
    # 24-hour windows
    short_window = (
        very_recent.groupby("trailer")
        .agg(
            max_cargo_temp_24h=("max_cargo_temp", _safe_max),
            defrost_count_24h=("defrost_event_count", lambda s: int(pd.to_numeric(s, errors="coerce").fillna(0).sum())),
            loaded_hot_count=("max_cargo_temp", lambda s: 0),  # filled below
        )
        .reset_index()
    )
    # Loaded + hot count needs both columns from the source df
    if "loaded_at_stop" in very_recent.columns and "max_cargo_temp" in very_recent.columns:
        loaded = very_recent.copy()
        loaded["_hot"] = (
            (loaded["loaded_at_stop"].fillna(0).astype(int) == 1)
            & (pd.to_numeric(loaded["max_cargo_temp"], errors="coerce") >= vanguard_cfg.cargo_max_temp_c)
        ).astype(int)
        hot = loaded.groupby("trailer")["_hot"].sum().reset_index(name="loaded_hot_count_real")
        short_window = short_window.merge(hot, on="trailer", how="left")
        short_window["loaded_hot_count"] = short_window["loaded_hot_count_real"].fillna(0).astype(int)
        short_window = short_window.drop(columns=["loaded_hot_count_real"])
    per_trailer = per_trailer.merge(short_window, on="trailer", how="left")

    # Join baselines
    if baselines_df is not None and not baselines_df.empty:
        per_trailer = per_trailer.merge(
            baselines_df[["trailer", "baseline_evap_delta", "baseline_compliance_pct", "baseline_defrost_per_day"]],
            on="trailer", how="left",
        )
    else:
        per_trailer["baseline_evap_delta"] = vanguard_cfg.default_baseline_evap_delta
        per_trailer["baseline_compliance_pct"] = vanguard_cfg.default_baseline_compliance_pct
        per_trailer["baseline_defrost_per_day"] = vanguard_cfg.defrost_baseline_per_day

    # ─── Subscore RH (Refrigeration Health) ───
    def _rh_row(r) -> float:
        delta_pp = _scale_band(
            r["current_evap_delta"],
            vanguard_cfg.evap_delta_healthy_min,
            vanguard_cfg.evap_delta_healthy_max,
            vanguard_cfg.evap_delta_degrading_max,
            vanguard_cfg.evap_delta_significant_max,
        )
        # Compliance penalty: higher when current drops below baseline
        cur = r["current_compliance"]
        base = r["baseline_compliance_pct"]
        if pd.isna(cur) or pd.isna(base) or base <= 0:
            comp_pp = 0.0
        else:
            ratio = max(0.0, 1.0 - (cur / base))
            comp_pp = min(100.0, ratio * 200.0)  # 50% drop → ~100
        return float(max(delta_pp, comp_pp))

    per_trailer["rh_score"] = per_trailer.apply(_rh_row, axis=1).round(1)

    # ─── Subscore DR (Defrost & Recovery) ───
    def _dr_row(r) -> float:
        cycles_today = float(r.get("defrost_count_24h", 0) or 0)
        baseline = float(r.get("baseline_defrost_per_day", vanguard_cfg.defrost_baseline_per_day) or vanguard_cfg.defrost_baseline_per_day)
        # Score by absolute cycles per day vs SOP bands
        if cycles_today <= baseline:
            return 0.0
        if cycles_today < vanguard_cfg.defrost_elevated_per_day:
            return 20.0
        if cycles_today < vanguard_cfg.defrost_abnormal_per_day:
            return 50.0
        return 80.0

    per_trailer["dr_score"] = per_trailer.apply(_dr_row, axis=1).round(1)

    # ─── Subscore TS (Temperature Stability) ───
    def _ts_row(r) -> float:
        # Hot-load instantly maxes TS
        hot = r.get("loaded_hot_count", 0)
        if pd.notna(hot) and float(hot) > 0:
            return 100.0
        # Penalize wide std of cargo max temp (>2°C)
        raw_std = r.get("cargo_temp_std", 0)
        std = float(raw_std) if pd.notna(raw_std) else 0.0
        if std <= 1:
            return 0.0
        if std <= 2:
            return 20.0
        if std <= 4:
            return 50.0
        return 80.0

    per_trailer["ts_score"] = per_trailer.apply(_ts_row, axis=1).round(1)

    # ─── Subscore ABHF (Airflow / Bulkhead / Heat-Flow) ───
    def _abhf_row(r) -> float:
        idx = r.get("avg_bulkhead_idx")
        if pd.isna(idx):
            return 0.0
        # SOP says "high ratios indicate bulkhead misplacement". We use S4 − avg(S5,S6).
        # Negative = healthy (S4 colder than back). Positive = bulkhead suspect.
        idx = float(idx)
        if idx <= 0.5:
            return 0.0
        if idx <= 2:
            return 30.0
        if idx <= 4:
            return 60.0
        return 90.0

    per_trailer["abhf_score"] = per_trailer.apply(_abhf_row, axis=1).round(1)

    # ─── Weighted VCI ───
    per_trailer["vci_calc"] = (
        vanguard_cfg.weight_rh * per_trailer["rh_score"]
        + vanguard_cfg.weight_dr * per_trailer["dr_score"]
        + vanguard_cfg.weight_ts * per_trailer["ts_score"]
        + vanguard_cfg.weight_abhf * per_trailer["abhf_score"]
    ).round(1)

    # ─── Hard overrides ───
    overrides: list[str] = []
    vci = per_trailer["vci_calc"].copy()
    override_reason = pd.Series([""] * len(per_trailer), index=per_trailer.index, dtype=object)

    # 1. Positive evap delta → CRITICAL
    pos_delta = per_trailer["current_evap_delta"].fillna(-99) >= 0
    vci = np.where(pos_delta, 100, vci)
    override_reason = np.where(pos_delta, "POSITIVE_EVAP_DELTA", override_reason)

    # 2. Hot load (cargo ≥ -20 while loaded) → CRITICAL
    hot = per_trailer["loaded_hot_count"].fillna(0) > 0
    vci = np.where(hot, 100, vci)
    override_reason = np.where(hot & (override_reason == ""), "HOT_LOAD", override_reason)

    # 3. Compliance < 75% over 24h → min VCI 75
    comp_low = per_trailer["current_compliance"].fillna(100) < vanguard_cfg.compliance_band_critical_pct
    vci = np.maximum(vci, np.where(comp_low, 75, vci))
    override_reason = np.where(
        comp_low & (override_reason == ""), "LOW_COMPLIANCE", override_reason,
    )

    # 4. Delta drift > 3°C from baseline (current - baseline > drift threshold,
    #    accounting for both being negative — "degraded" means current is closer to 0)
    drift = per_trailer["current_evap_delta"] - per_trailer["baseline_evap_delta"]
    drift_high = drift.fillna(0) > vanguard_cfg.evap_delta_drift_critical_c
    vci = np.maximum(vci, np.where(drift_high, 75, vci))
    override_reason = np.where(
        drift_high & (override_reason == ""), "DELTA_DRIFT", override_reason,
    )

    per_trailer["vci"] = vci.astype(int).clip(0, 100)
    per_trailer["hard_override_applied"] = override_reason

    per_trailer["band"] = per_trailer["vci"].apply(
        lambda v: _band_from_vci_simple(float(v), vanguard_cfg)
    )

    # Readiness check: can_load_frozen
    per_trailer["can_load_frozen"] = (per_trailer["vci"] < 75) & (per_trailer["loaded_hot_count"].fillna(0) == 0)
    per_trailer["block_reason"] = np.where(
        per_trailer["vci"] >= 75,
        per_trailer["hard_override_applied"].where(
            per_trailer["hard_override_applied"] != "", "VCI_HIGH",
        ),
        "",
    )

    cols = [
        "trailer", "vci", "band", "can_load_frozen", "block_reason",
        "rh_score", "dr_score", "ts_score", "abhf_score",
        "current_evap_delta", "baseline_evap_delta",
        "current_compliance", "baseline_compliance_pct",
        "max_cargo_temp_24h", "defrost_count_24h", "loaded_hot_count",
        "avg_bulkhead_idx", "cargo_temp_std",
        "hard_override_applied",
    ]
    return per_trailer[[c for c in cols if c in per_trailer.columns]].sort_values("vci", ascending=False)


# ─────────────── 3. Alerts ───────────────


def compute_vanguard_alerts(
    stops_df: pd.DataFrame,
    vci_df: pd.DataFrame,
    vanguard_cfg,
) -> pd.DataFrame:
    """Persist one row per active alert per trailer."""
    if vci_df is None or vci_df.empty:
        return pd.DataFrame()

    def _safe_int(v) -> int:
        return int(v) if pd.notna(v) else 0

    rows: list[dict] = []
    for _, r in vci_df.iterrows():
        trailer = r["trailer"]
        vci = _safe_int(r.get("vci", 0))
        # ALERT_TEMP_ABOVE_NEG20
        hot_count = _safe_int(r.get("loaded_hot_count", 0))
        if hot_count > 0:
            rows.append({
                "trailer": trailer,
                "alert_code": "ALERT_TEMP_ABOVE_NEG20",
                "severity": "HIGH",
                "evidence": f"loaded_hot_count={hot_count} stops with cargo ≥ {vanguard_cfg.cargo_max_temp_c}°C",
                "vci_at_trigger": vci,
            })
        # ALERT_EVAP_DELTA
        cur = r.get("current_evap_delta")
        base = r.get("baseline_evap_delta")
        if pd.notna(cur) and float(cur) >= 0:
            rows.append({
                "trailer": trailer,
                "alert_code": "ALERT_EVAP_DELTA",
                "severity": "HIGH",
                "evidence": f"positive evap delta {cur:.2f}°C (baseline {base:.2f}°C)",
                "vci_at_trigger": vci,
            })
        elif pd.notna(cur) and pd.notna(base) and (float(cur) - float(base)) > vanguard_cfg.evap_delta_drift_critical_c:
            rows.append({
                "trailer": trailer,
                "alert_code": "ALERT_EVAP_DELTA",
                "severity": "MEDIUM",
                "evidence": f"delta drifted {float(cur) - float(base):.2f}°C (current {cur:.2f}, baseline {base:.2f})",
                "vci_at_trigger": vci,
            })
        # ALERT_HIGH_DEFROST
        defrost_count = _safe_int(r.get("defrost_count_24h", 0))
        if defrost_count >= vanguard_cfg.defrost_abnormal_per_day:
            rows.append({
                "trailer": trailer,
                "alert_code": "ALERT_HIGH_DEFROST",
                "severity": "MEDIUM",
                "evidence": f"{defrost_count} defrost events in 24h (baseline {vanguard_cfg.defrost_baseline_per_day})",
                "vci_at_trigger": vci,
            })
        # ALERT_BULKHEAD_SUSPECT
        bh = r.get("avg_bulkhead_idx")
        if pd.notna(bh) and float(bh) > 2.0:
            rows.append({
                "trailer": trailer,
                "alert_code": "ALERT_BULKHEAD_SUSPECT",
                "severity": "MEDIUM",
                "evidence": f"bulkhead seal index {float(bh):.2f} (S4 vs S5/S6)",
                "vci_at_trigger": vci,
            })

    if not rows:
        return pd.DataFrame(columns=["trailer", "alert_code", "severity", "evidence", "vci_at_trigger"])

    return pd.DataFrame(rows)
