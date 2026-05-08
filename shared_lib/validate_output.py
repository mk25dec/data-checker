from __future__ import annotations

# NOTE: Checks that an output file was created correctly.

import os
from pathlib import Path
from typing import Callable, Iterable

from .format_registry import validate_format
from .path_utils import check_path
from .results import EXIT_IO_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

RowCountReader = Callable[[Path, str | None], int | None]


def validate_output_file(
    output_path: str | Path | None,
    *,
    output_format: str | None = None,
    supported_formats: Iterable[str] | None = None,
    require_non_empty: bool = True,
    check_readable: bool = True,
    min_rows: int | None = None,
    row_count_reader: RowCountReader | None = None,
) -> SharedResult:
    """Check that an output file exists, is readable, and has an allowed type."""
    path_result = check_path(output_path, expected="file")
    if not path_result.success:
        return path_result

    path = Path(path_result.data["path"])
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        return failure(
            f"Could not stat output file: {path}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(path),
        )

    if require_non_empty and size_bytes == 0:
        return failure(
            f"Output file is empty: {path}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
            size_bytes=size_bytes,
        )

    if check_readable and not os.access(path, os.R_OK):
        return failure(
            f"Output file is not readable: {path}",
            exit_code=EXIT_IO_ERROR,
            path=str(path),
            size_bytes=size_bytes,
        )

    format_result = validate_format(
        output_format,
        path=path,
        supported_formats=supported_formats,
        label="output format",
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

    row_count: int | None = None
    if min_rows is not None:
        if min_rows < 0:
            return failure("min_rows must be greater than or equal to zero", exit_code=EXIT_VALIDATION_ERROR)
        if row_count_reader is None:
            return failure(
                "row_count_reader is required when min_rows is provided",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(path),
                size_bytes=size_bytes,
                format=format_result.data["format"],
            )
        try:
            row_count = row_count_reader(path, format_result.data["format"])
        except Exception as exc:
            return failure(
                f"Could not read output row count: {path}",
                exit_code=EXIT_IO_ERROR,
                error=str(exc),
                path=str(path),
                size_bytes=size_bytes,
                format=format_result.data["format"],
            )
        if row_count is None:
            return failure(
                "row_count_reader returned no row count",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(path),
                size_bytes=size_bytes,
                format=format_result.data["format"],
            )
        if row_count < min_rows:
            return failure(
                f"Output has {row_count} rows, expected at least {min_rows}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(path),
                size_bytes=size_bytes,
                format=format_result.data["format"],
                rows=row_count,
                min_rows=min_rows,
            )

    return success(
        f"Output file is valid: {path}",
        path=str(path),
        size_bytes=size_bytes,
        format=format_result.data["format"],
        format_source=format_result.data["source"],
        rows=row_count,
    )
