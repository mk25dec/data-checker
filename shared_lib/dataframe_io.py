from __future__ import annotations

# NOTE: Saves table-like data without leaving half-written files.

import os
import tempfile
from pathlib import Path
from typing import Any

from .path_utils import ensure_parent_directory, resolve_path
from .results import EXIT_IO_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success


def write_dataframe_atomic(path: str | Path | None, dataframe: Any, *, output_format: str) -> SharedResult:
    """Save a table-like object as CSV, JSON, Parquet, or XLSX."""
    if path is None or str(path).strip() == "":
        return failure("Output path is required", exit_code=EXIT_VALIDATION_ERROR, path=None)

    fmt = str(output_format or "").strip().lower().lstrip(".")
    if fmt == "excel":
        fmt = "xlsx"
    suffixes = {"csv": ".csv", "json": ".json", "parquet": ".parquet", "xlsx": ".xlsx"}
    if fmt not in suffixes:
        return failure(f"Unsupported dataframe output format: {output_format}", exit_code=EXIT_VALIDATION_ERROR, format=fmt)

    parent_result = ensure_parent_directory(path)
    if not parent_result.success:
        return parent_result

    target = resolve_path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=suffixes[fmt], dir=str(target.parent))
    os.close(fd)
    try:
        if fmt == "csv":
            dataframe.to_csv(tmp_name, index=False)
        elif fmt == "json":
            dataframe.to_json(tmp_name, orient="records")
        elif fmt == "parquet":
            dataframe.to_parquet(tmp_name, index=False)
        elif fmt == "xlsx":
            dataframe.to_excel(tmp_name, index=False)
        os.replace(tmp_name, target)
    except Exception as exc:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        return failure(
            f"Could not write dataframe file: {target}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(target),
            format=fmt,
        )

    return success(f"Dataframe file written: {target}", path=str(target), format=fmt, size_bytes=target.stat().st_size)
