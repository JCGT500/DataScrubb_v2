"""Datetime parsing and coercion utilities."""

import pandas as pd


def coerce_datetime_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Coerce specified columns to datetime, setting invalid values to NaT."""
    df = df.copy()
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def build_composite_datetime(
    df: pd.DataFrame,
    date_col: str,
    time_col: str,
    output_col: str,
) -> pd.DataFrame:
    """Combine a date column and a time column into a single datetime column.

    Used for SAP data where 'pick_up_date' and 'arrive' are separate.
    """
    df = df.copy()
    df[output_col] = pd.to_datetime(
        df[date_col].astype(str) + " " + df[time_col].astype(str),
        errors="coerce",
    )
    return df
