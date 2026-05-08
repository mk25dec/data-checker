from __future__ import annotations

# NOTE: Finds and checks file types like CSV, JSON, or Parquet.

from pathlib import Path
from typing import Iterable

from .results import EXIT_VALIDATION_ERROR, SharedResult, failure, success

DEFAULT_FORMAT_ALIASES: dict[str, str] = {
    "csv": "csv",
    "json": "json",
    "jsonl": "jsonl",
    "ndjson": "ndjson",
    "parquet": "parquet",
    "pq": "parquet",
    "xls": "xls",
    "xlsx": "xlsx",
    "excel": "xlsx",
    "txt": "txt",
}


def normalize_format_token(
    value: str | None,
    *,
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Clean up a file type name."""
    if value is None:
        return None
    token = str(value).strip().lower().lstrip(".")
    if not token:
        return None
    return (aliases or DEFAULT_FORMAT_ALIASES).get(token, token)


def detect_format_from_path(
    path: str | Path,
    *,
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Find the file type from the file name."""
    suffix = Path(path).suffix.lower().lstrip(".")
    if not suffix:
        return None
    return normalize_format_token(suffix, aliases=aliases)


def validate_format(
    value: str | None,
    *,
    path: str | Path | None = None,
    supported_formats: Iterable[str] | None = None,
    aliases: dict[str, str] | None = None,
    label: str = "format",
) -> SharedResult:
    """Check if a file type is allowed."""
    supported = tuple(dict.fromkeys(supported_formats or ()))
    canonical = normalize_format_token(value, aliases=aliases)
    source = "explicit"

    if canonical is None and path is not None:
        canonical = detect_format_from_path(path, aliases=aliases)
        source = "extension"

    data = {
        "format": canonical,
        "source": source,
        "input": value,
        "path": str(Path(path).expanduser().resolve()) if path is not None else None,
        "supported_formats": list(supported),
    }

    if canonical is None:
        return failure(
            f"Could not determine {label}",
            exit_code=EXIT_VALIDATION_ERROR,
            **data,
        )

    if supported and canonical not in supported:
        return failure(
            f"Unsupported {label}: {canonical}",
            exit_code=EXIT_VALIDATION_ERROR,
            **data,
        )

    return success(f"{label.capitalize()} is valid: {canonical}", **data)


def supported_formats_hint(supported_formats: Iterable[str]) -> str:
    return ", ".join(sorted(dict.fromkeys(supported_formats)))
