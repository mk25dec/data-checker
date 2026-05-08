from __future__ import annotations

# NOTE: Saves text, bytes, or JSON without leaving half-written files.

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .path_utils import ensure_parent_directory, resolve_path
from .results import EXIT_IO_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success


def write_text_atomic(path: str | Path | None, text: str, *, encoding: str = "utf-8") -> SharedResult:
    """Save text to a file safely."""
    if path is None or str(path).strip() == "":
        return failure("Output path is required", exit_code=EXIT_VALIDATION_ERROR, path=None)

    parent_result = ensure_parent_directory(path)
    if not parent_result.success:
        return parent_result

    target = resolve_path(path)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding=encoding,
            delete=False,
            dir=str(target.parent),
            prefix=f".{target.name}.",
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(text)
        os.replace(tmp_name, target)
    except OSError as exc:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
        return failure(f"Could not write text file: {target}", exit_code=EXIT_IO_ERROR, error=str(exc), path=str(target))

    return success(f"Text file written: {target}", path=str(target), size_bytes=target.stat().st_size)


def write_bytes_atomic(path: str | Path | None, payload: bytes) -> SharedResult:
    """Save bytes to a file safely."""
    if path is None or str(path).strip() == "":
        return failure("Output path is required", exit_code=EXIT_VALIDATION_ERROR, path=None)

    parent_result = ensure_parent_directory(path)
    if not parent_result.success:
        return parent_result

    target = resolve_path(path)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            delete=False,
            dir=str(target.parent),
            prefix=f".{target.name}.",
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(payload)
        os.replace(tmp_name, target)
    except OSError as exc:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)
        return failure(f"Could not write binary file: {target}", exit_code=EXIT_IO_ERROR, error=str(exc), path=str(target))

    return success(f"Binary file written: {target}", path=str(target), size_bytes=target.stat().st_size)


def write_json_atomic(
    path: str | Path | None,
    payload: Any,
    *,
    indent: int | None = 2,
    encoding: str = "utf-8",
) -> SharedResult:
    """Save JSON data to a file safely."""
    try:
        text = json.dumps(payload, indent=indent, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        return failure("Payload is not JSON serializable", exit_code=EXIT_VALIDATION_ERROR, error=str(exc), path=str(path))
    return write_text_atomic(path, text, encoding=encoding)
