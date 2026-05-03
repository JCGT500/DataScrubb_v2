"""Abstract base class for data source adapters."""

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

from datascrubb.config import SourceConfig


class SchemaError(Exception):
    """Raised when required columns are missing from the source data."""
    pass


class BaseAdapter(ABC):
    """Base adapter that all data source adapters must implement.

    The template method `process()` handles the standard flow:
    load_raw -> validate_schema -> normalize.
    """

    def __init__(self, source_config: SourceConfig | None = None):
        self.source_config = source_config

    @abstractmethod
    def load_raw(self, file_path: Path) -> pd.DataFrame:
        """Load raw data from a file, handling header detection if needed."""

    @abstractmethod
    def normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names, parse dates, extract keys, apply business logic."""

    def validate_schema(self, df: pd.DataFrame) -> list[str]:
        """Return a list of required columns missing from the DataFrame.

        Uses required_columns from source_config if available.
        """
        if not self.source_config or not self.source_config.required_columns:
            return []
        return [c for c in self.source_config.required_columns if c not in df.columns]

    def process(self, file_path: Path | str) -> pd.DataFrame:
        """Template method: load -> validate schema -> normalize.

        Raises SchemaError if required columns are missing.
        """
        file_path = Path(file_path)
        raw = self.load_raw(file_path)
        missing = self.validate_schema(raw)
        if missing:
            raise SchemaError(
                f"[{self.__class__.__name__}] Missing required columns: {missing}. "
                f"Found: {list(raw.columns)}"
            )
        return self.normalize(raw)
