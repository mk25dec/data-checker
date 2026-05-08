from __future__ import annotations

# NOTE: Checks that an input file is ready to use.

import os
from pathlib import Path
from typing import Iterable

from .format_registry import validate_format
from .path_utils import check_path
from .results import EXIT_IO_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success


def validate_input_file(
    input_path: str | Path | None,
    *,
    input_format: str | None = None,
    supported_formats: Iterable[str] | None = None,
    require_non_empty: bool = True,
    check_readable: bool = True,
) -> SharedResult:
    """Check that an input file exists, is readable, and has an allowed type."""
    path_result = check_path(input_path, expected="file")
    if not path_result.success:
        return path_result

    path = Path(path_result.data["path"])
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        return failure(
            f"Could not stat input file: {path}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(path),
        )

    if require_non_empty and size_bytes == 0:
        return failure(
            f"Input file is empty: {path}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
            size_bytes=size_bytes,
        )

    if check_readable and not os.access(path, os.R_OK):
        return failure(
            f"Input file is not readable: {path}",
            exit_code=EXIT_IO_ERROR,
            path=str(path),
            size_bytes=size_bytes,
        )

    format_result = validate_format(
        input_format,
        path=path,
        supported_formats=supported_formats,
        label="input format",
    )
    if not format_result.success:
        return failure(
            format_result.message,
            exit_code=format_result.exit_code,
            error=format_result.error,
            path=str(path),
            size_bytes=size_bytes,
            format=format_result.data.get("format"),
            format_source=format_result.data.get("source"),
            supported_formats=format_result.data.get("supported_formats", []),
        )

    return success(
        f"Input file is valid: {path}",
        path=str(path),
        size_bytes=size_bytes,
        format=format_result.data["format"],
        format_source=format_result.data["source"],
    )
