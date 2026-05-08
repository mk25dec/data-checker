"""Parquet-only data cleaning CLI (DuckDB + Polars)."""

from file_cleaner.clean_core import result_to_json, run_clean

__version__ = "0.1.0"

__all__ = ["__version__", "result_to_json", "run_clean"]
