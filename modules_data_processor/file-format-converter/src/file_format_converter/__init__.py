"""DuckDB-backed file format converter."""

from file_format_converter.convert_core import (
    SUPPORTED_INPUT_FORMATS,
    SUPPORTED_OUTPUT_FORMATS,
    result_to_json,
    run_convert,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "result_to_json",
    "run_convert",
    "SUPPORTED_INPUT_FORMATS",
    "SUPPORTED_OUTPUT_FORMATS",
]
