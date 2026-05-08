"""Join two Parquet tables horizontally (DuckDB)."""

__version__ = "0.1.0"

from .join_core import result_to_json, run_join_horizontal

__all__ = ["__version__", "result_to_json", "run_join_horizontal"]
