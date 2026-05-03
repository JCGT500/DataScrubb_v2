"""Trailer capacity reference + fill-% calculator.

Capacity priority (per trailer):
    1. Explicit config entry in ``config/trailer_capacity.yaml``.
    2. Observed 95th-percentile of historical ``current_cases`` /
       ``sum_of_weight`` for that trailer (when >= 5 historical stops).
    3. The ``default:`` block from the YAML.

Each stop gets a `capacity_source` column ("config" / "observed" / "default")
so users can audit which inputs drove the fill %.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger("datascrubb.kpi.capacity")

DEFAULT_CAPACITY_FILE = Path(__file__).resolve().parents[2] / "config" / "trailer_capacity.yaml"
MIN_OBSERVED_STOPS = 5
# Observed-capacity policy: use the trailer's historical MAX load rather than
# 95th-percentile. With max, the heaviest-ever stop reads as 100% fill (never
# above) and outlier stops can't blow the ratio past 100%. If you configure
# explicit capacity in trailer_capacity.yaml, that always takes precedence.
USE_MAX_FOR_OBSERVED = True
OBSERVED_QUANTILE = 0.95  # kept for tests / callers who pass this in
FILL_PCT_CAP = 200.0  # display-side cap to prevent runaway numbers from bad inputs


def _normalize_trailer(name: str | None) -> str | None:
    if name is None or pd.isna(name):
        return None
    s = str(name).strip().upper()
    return s if s and s != "NAN" else None


def load_trailer_capacity(path: Path | None = None) -> dict:
    """Load the capacity matrix from YAML.

    Returns ``{"default": {...}, "trailers": {"RX001": {...}, ...}}`` with
    trailer keys uppercased.
    """
    p = Path(path) if path else DEFAULT_CAPACITY_FILE
    if not p.exists():
        logger.warning("Trailer capacity file not found: %s — using empty matrix", p)
        return {"default": {"max_cases": 800, "max_weight_lbs": 44000}, "trailers": {}}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    trailers = {
        _normalize_trailer(k): v
        for k, v in (data.get("trailers") or {}).items()
        if _normalize_trailer(k)
    }
    return {
        "default": data.get("default", {}) or {"max_cases": 800, "max_weight_lbs": 44000},
        "trailers": trailers,
    }


def save_trailer_capacity(matrix: dict, path: Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_CAPACITY_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(matrix, f, sort_keys=False)


def derive_observed_capacity(stops_df: pd.DataFrame) -> pd.DataFrame:
    """Per trailer: 95th-percentile of historical current_cases and sum_of_weight.

    Returns a DataFrame with columns: trailer, observed_max_cases,
    observed_max_weight_lbs, observation_count. Trailers with fewer than
    ``MIN_OBSERVED_STOPS`` stops are EXCLUDED (caller should fall back to default).
    """
    if stops_df is None or stops_df.empty or "trailer" not in stops_df.columns:
        return pd.DataFrame(columns=["trailer", "observed_max_cases", "observed_max_weight_lbs", "observation_count"])

    df = stops_df.copy()
    df["trailer"] = df["trailer"].apply(_normalize_trailer)
    df = df[df["trailer"].notna()]
    if df.empty:
        return pd.DataFrame(columns=["trailer", "observed_max_cases", "observed_max_weight_lbs", "observation_count"])

    cases = pd.to_numeric(df.get("current_cases"), errors="coerce")
    weight = pd.to_numeric(df.get("sum_of_weight"), errors="coerce")

    if USE_MAX_FOR_OBSERVED:
        agg_cases = lambda s: float(s.dropna().max()) if s.dropna().size else np.nan  # noqa: E731
        agg_weight = lambda s: float(s.dropna().max()) if s.dropna().size else np.nan  # noqa: E731
    else:
        agg_cases = lambda s: float(s.dropna().quantile(OBSERVED_QUANTILE)) if s.dropna().size else np.nan  # noqa: E731
        agg_weight = lambda s: float(s.dropna().quantile(OBSERVED_QUANTILE)) if s.dropna().size else np.nan  # noqa: E731

    out = (
        df.assign(_cases=cases, _weight=weight)
        .groupby("trailer")
        .agg(
            observed_max_cases=("_cases", agg_cases),
            observed_max_weight_lbs=("_weight", agg_weight),
            observation_count=("trailer", "count"),
        )
        .reset_index()
    )
    out = out[out["observation_count"] >= MIN_OBSERVED_STOPS]
    return out


def attach_fill_pct(stops_df: pd.DataFrame, matrix: dict | None = None) -> pd.DataFrame:
    """Add fill-% columns to a copy of stops_df.

    Adds: cap_max_cases, cap_max_weight_lbs, capacity_source,
    fill_pct_cases, fill_pct_weight.

    Capacity priority per trailer: config > observed > default.
    """
    if stops_df is None or stops_df.empty:
        return stops_df

    matrix = matrix if matrix is not None else load_trailer_capacity()
    default = matrix.get("default", {}) or {}
    config_trailers = matrix.get("trailers", {}) or {}

    out = stops_df.copy()
    out["_trailer_norm"] = out["trailer"].apply(_normalize_trailer) if "trailer" in out.columns else None

    observed = derive_observed_capacity(stops_df)
    observed_map = (
        observed.set_index("trailer")[["observed_max_cases", "observed_max_weight_lbs"]].to_dict("index")
        if not observed.empty
        else {}
    )

    def _resolve(trailer: str | None) -> tuple[float | None, float | None, str]:
        if trailer is None:
            return (default.get("max_cases"), default.get("max_weight_lbs"), "default")
        if trailer in config_trailers:
            entry = config_trailers[trailer]
            return (
                entry.get("max_cases", default.get("max_cases")),
                entry.get("max_weight_lbs", default.get("max_weight_lbs")),
                "config",
            )
        if trailer in observed_map:
            obs = observed_map[trailer]
            return (
                obs.get("observed_max_cases") or default.get("max_cases"),
                obs.get("observed_max_weight_lbs") or default.get("max_weight_lbs"),
                "observed",
            )
        return (default.get("max_cases"), default.get("max_weight_lbs"), "default")

    resolved = out["_trailer_norm"].apply(_resolve)
    out["cap_max_cases"] = [r[0] for r in resolved]
    out["cap_max_weight_lbs"] = [r[1] for r in resolved]
    out["capacity_source"] = [r[2] for r in resolved]

    cases = pd.to_numeric(out.get("current_cases"), errors="coerce").fillna(0)
    weight = pd.to_numeric(out.get("sum_of_weight"), errors="coerce").fillna(0)
    cap_c = pd.to_numeric(out["cap_max_cases"], errors="coerce")
    cap_w = pd.to_numeric(out["cap_max_weight_lbs"], errors="coerce")

    out["fill_pct_cases"] = (
        (cases / cap_c.replace(0, np.nan)) * 100
    ).clip(upper=FILL_PCT_CAP).round(1)
    out["fill_pct_weight"] = (
        (weight / cap_w.replace(0, np.nan)) * 100
    ).clip(upper=FILL_PCT_CAP).round(1)

    out = out.drop(columns=["_trailer_norm"])
    logger.info(
        "Fill-% attached: %d stops, capacity_source counts=%s",
        len(out), out["capacity_source"].value_counts().to_dict(),
    )
    return out
