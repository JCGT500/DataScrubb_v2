"""Centralized cached SQLite loaders for dashboard pages.

Use ``load_table(name)`` instead of building per-page _load helpers — this
gives every page the same 60-second cache and avoids re-querying SQLite on
every Streamlit rerun.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from datascrubb.config import load_config
from datascrubb.db import get_engine

CACHE_TTL_SECONDS = 60


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_table(name: str) -> pd.DataFrame:
    """Load a SQLite table into a DataFrame, cached for 60 seconds."""
    cfg = load_config()
    if not cfg.db_path.exists():
        return pd.DataFrame()
    engine = get_engine(cfg.db_path)
    try:
        return pd.read_sql(f"SELECT * FROM {name}", engine)
    except Exception:
        return pd.DataFrame()


def clear_cache() -> None:
    """Manually invalidate the cache — call after a pipeline run."""
    load_table.clear()
