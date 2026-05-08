"""Vertically stack Parquet tables (DuckDB)."""

from .join_core import result_to_json, run_join_vertical

__version__ = "0.1.0"

__all__ = ["__version__", "result_to_json", "run_join_vertical"]
