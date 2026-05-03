"""CSV export for individual tables."""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("datascrubb.export.csv")


def export_table_csv(df: pd.DataFrame, output_path: str | Path, table_name: str = "") -> Path:
    """Export a single DataFrame to CSV."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info("CSV exported: %s (%d rows) -> %s", table_name, len(df), output_path)
    return output_path
