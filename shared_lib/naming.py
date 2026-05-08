from __future__ import annotations

# NOTE: Makes safe file names and output file paths.

import re
from pathlib import Path

from .format_registry import normalize_format_token
from .results import EXIT_VALIDATION_ERROR, SharedResult, failure, success
from .time_utils import timestamp_compact


def normalize_file_stem(name: str | None, *, default: str = "output") -> str:
    """Make a safe file name without the extension."""
    raw = (name or default).strip() or default
    stem = Path(raw).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned or default


def add_timestamp_to_path(path: str | Path, *, timestamp: str | None = None) -> SharedResult:
    """Add a timestamp to a file name."""
    p = Path(path).expanduser()
    stamp = timestamp or timestamp_compact()
    if not stamp.strip():
        return failure("Timestamp is required", exit_code=EXIT_VALIDATION_ERROR, path=str(path))
    stamped = p.with_name(f"{p.stem}_{stamp}{p.suffix}")
    return success("Timestamp added to path", path=str(stamped), timestamp=stamp)


def apply_output_format_to_path(
    path: str | Path,
    output_format: str,
    *,
    extension_map: dict[str, str] | None = None,
    aliases: dict[str, str] | None = None,
) -> SharedResult:
    """Add the right extension for an output file type."""
    fmt = normalize_format_token(output_format, aliases=aliases)
    extensions = extension_map or {
        "csv": ".csv",
        "json": ".json",
        "jsonl": ".jsonl",
        "ndjson": ".ndjson",
        "parquet": ".parquet",
        "txt": ".txt",
        "xls": ".xls",
        "xlsx": ".xlsx",
    }
    if fmt is None or fmt not in extensions:
        return failure(f"Unsupported output format: {output_format}", exit_code=EXIT_VALIDATION_ERROR, format=fmt)

    p = Path(path).expanduser()
    wanted_suffix = extensions[fmt]
    if p.suffix and p.suffix.lower() != wanted_suffix:
        return failure(
            f"Output path extension {p.suffix} conflicts with format {fmt}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(p),
            format=fmt,
            expected_extension=wanted_suffix,
        )
    if not p.suffix:
        p = p.with_suffix(wanted_suffix)
    return success("Output format applied to path", path=str(p), format=fmt, extension=wanted_suffix)
