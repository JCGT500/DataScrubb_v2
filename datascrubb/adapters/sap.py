"""SAP data adapter.

Handles:
- Column normalization
- order_key from document_number
- S-Code extraction from shipper_search_term
- Composite arrive datetime from pick_up_date + arrive columns
"""

import logging
from pathlib import Path

import pandas as pd

from datascrubb.adapters.base import BaseAdapter
from datascrubb.config import SourceConfig
from datascrubb.utils.columns import normalize_columns
from datascrubb.utils.s_code import extract_s_code

logger = logging.getLogger("datascrubb.adapters.sap")


class SapAdapter(BaseAdapter):
    def __init__(self, source_config: SourceConfig | None = None):
        super().__init__(source_config)

    def load_raw(self, file_path: Path) -> pd.DataFrame:
        """Load SAP Excel file with first row as header."""
        df = pd.read_excel(file_path)
        df = normalize_columns(df)
        logger.info("SAP rows loaded: %d", len(df))
        return df

    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply SAP normalization and key extraction."""
        sap = df.copy()

        # 1. Create order_key from document_number
        sap["order_key"] = sap["document_number"].astype(str)

        # 2. Extract S-Code from shipper_search_term
        sap["s_code"] = sap["shipper_search_term"].apply(extract_s_code)

        # 3. Build composite arrive datetime from pick_up_date + arrive
        sap["arrive"] = pd.to_datetime(
            sap["pick_up_date"].astype(str) + " " + sap["arrive"].astype(str),
            errors="coerce",
        )

        # 4. Derive arrival_date
        sap["arrival_date"] = sap["arrive"].dt.date.astype(str)

        logger.info(
            "SAP normalized: %d rows, S-Code coverage: %.1f%%",
            len(sap),
            sap["s_code"].notna().mean() * 100,
        )

        return sap
