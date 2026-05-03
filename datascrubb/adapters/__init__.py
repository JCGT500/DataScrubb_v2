"""Adapter registry for data source plugins."""

from datascrubb.adapters.base import BaseAdapter
from datascrubb.adapters.crst import CrstAdapter
from datascrubb.adapters.m3pl import M3plAdapter
from datascrubb.adapters.sap import SapAdapter
from datascrubb.adapters.telemetry import TelemetryAdapter

ADAPTER_REGISTRY: dict[str, type[BaseAdapter]] = {
    "crst": CrstAdapter,
    "sap": SapAdapter,
    "telemetry": TelemetryAdapter,
    "m3pl": M3plAdapter,
}


def get_adapter(source_name: str) -> type[BaseAdapter]:
    """Look up an adapter class by source name.

    To add a new data source, create a new adapter class inheriting from BaseAdapter
    and register it here.
    """
    if source_name not in ADAPTER_REGISTRY:
        raise KeyError(
            f"Unknown adapter '{source_name}'. "
            f"Available: {list(ADAPTER_REGISTRY.keys())}"
        )
    return ADAPTER_REGISTRY[source_name]
