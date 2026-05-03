"""Stop classification: PLASMA_CENTER / DISTRIBUTION_CENTER / INTERNAL_BASE / OTHER.

Priority:
1. Any stop with an S-code → PLASMA_CENTER (regardless of customer name).
2. Customer-name regex rules from config (case-insensitive). First match wins.
3. Fallback: ``default_class`` (typically "OTHER").

Rules are loaded from ``config/default.yaml::stop_classification`` so users can
re-classify by editing YAML — no code change needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

PLASMA_CENTER = "PLASMA_CENTER"
DISTRIBUTION_CENTER = "DISTRIBUTION_CENTER"
INTERNAL_BASE = "INTERNAL_BASE"
OTHER = "OTHER"

DEFAULT_RULES = [
    {"class": DISTRIBUTION_CENTER, "pattern": r"RX CROSSROADS"},
    {"class": DISTRIBUTION_CENTER, "pattern": r"WAREHOUSE|WHSE|DISTRIBUTION"},
    {"class": INTERNAL_BASE, "pattern": r"CRST INTERNATIONAL|CRST LOUISVILLE|^CRST "},
    {"class": INTERNAL_BASE, "pattern": r"THERMOKING|FUEL|TERMINAL|^BASE"},
]


@dataclass
class ClassifyConfig:
    use_s_code_for_plasma: bool = True
    rules: list[dict] | None = None  # [{"class": "...", "pattern": "..."}, ...]
    default_class: str = OTHER

    def compiled_rules(self) -> list[tuple[str, re.Pattern]]:
        rules = self.rules if self.rules is not None else DEFAULT_RULES
        out: list[tuple[str, re.Pattern]] = []
        for r in rules:
            cls = r.get("class")
            pat = r.get("pattern")
            if not cls or not pat:
                continue
            try:
                out.append((cls, re.compile(pat, re.IGNORECASE)))
            except re.error:
                continue
        return out


def classify_stop(customer: str | None, s_code: str | None, cfg: ClassifyConfig) -> str:
    """Classify a single stop. Returns one of PLASMA_CENTER / DISTRIBUTION_CENTER /
    INTERNAL_BASE / OTHER (or whatever the YAML rule maps to)."""
    if cfg.use_s_code_for_plasma and s_code is not None and not _is_blank(s_code):
        return PLASMA_CENTER
    name = "" if customer is None else str(customer).strip()
    if not name:
        return cfg.default_class
    for cls, pat in cfg.compiled_rules():
        if pat.search(name):
            return cls
    return cfg.default_class


def classify_stops_df(df: pd.DataFrame, cfg: ClassifyConfig) -> pd.Series:
    """Vectorised classification for a DataFrame. Expects ``customer`` and
    optionally ``s_code`` columns. Returns a Series of class labels aligned
    to the DataFrame's index."""
    if df is None or df.empty:
        return pd.Series([], dtype=object)

    custs = df["customer"].astype(str) if "customer" in df.columns else pd.Series("", index=df.index)
    s_codes = df["s_code"] if "s_code" in df.columns else pd.Series([None] * len(df), index=df.index)

    result = pd.Series(cfg.default_class, index=df.index, dtype=object)

    if cfg.use_s_code_for_plasma:
        plasma_mask = s_codes.notna() & (s_codes.astype(str).str.strip() != "")
        result[plasma_mask] = PLASMA_CENTER

    rules = cfg.compiled_rules()
    if not rules:
        return result

    # Anyone not already PLASMA_CENTER goes through the regex rules.
    remaining_mask = result == cfg.default_class
    if not remaining_mask.any():
        return result

    for cls, pat in rules:
        # Only check rows that haven't matched yet
        candidates = remaining_mask & ~result.isin([PLASMA_CENTER])
        if not candidates.any():
            break
        match_mask = candidates & custs.str.contains(pat, regex=True, na=False)
        result[match_mask] = cls
        remaining_mask = result == cfg.default_class

    return result


def _is_blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s == "" or s.lower() == "nan"
