from __future__ import annotations

# NOTE: Checks whether files and folders exist.

from pathlib import Path
from typing import Literal

from .results import (
    EXIT_IO_ERROR,
    EXIT_NOT_FOUND,
    EXIT_TYPE_MISMATCH,
    EXIT_VALIDATION_ERROR,
    SharedResult,
    failure,
    success,
)

ExpectedPath = Literal["file", "directory", "any"]
PathKind = Literal["file", "directory", "other", "missing"]
PathCheck = SharedResult


def resolve_path(path: str | Path) -> Path:
    """Show the full version of a path."""
    return Path(path).expanduser().resolve()


def _path_kind(path: Path) -> PathKind:
    if path.is_file():
        return "file"
    if path.is_dir():
        return "directory"
    return "other"


def check_path(
    path: str | Path | None,
    *,
    expected: ExpectedPath = "any",
) -> SharedResult:
    """Check if a path exists and is a file or folder."""
    if expected not in {"file", "directory", "any"}:
        return failure(
            f"Unsupported expected path kind: {expected}",
            exit_code=EXIT_VALIDATION_ERROR,
            expected=expected,
        )

    if path is None or str(path).strip() == "":
        return failure("Path is required", exit_code=EXIT_VALIDATION_ERROR, path=None, expected=expected)

    resolved = resolve_path(path)
    base_data = {"path": str(resolved), "expected": expected}

    if not resolved.exists():
        return failure(
            f"Path does not exist: {resolved}",
            exit_code=EXIT_NOT_FOUND,
            kind="missing",
            exists=False,
            **base_data,
        )

    kind = _path_kind(resolved)
    data = {"kind": kind, "exists": True, **base_data}

    if kind == "other":
        return failure(
            f"Path exists but is not a regular file or directory: {resolved}",
            exit_code=EXIT_TYPE_MISMATCH,
            **data,
        )

    if expected != "any" and kind != expected:
        return failure(
            f"Expected {expected}, got {kind}: {resolved}",
            exit_code=EXIT_TYPE_MISMATCH,
            **data,
        )

    return success(f"{kind.capitalize()} exists: {resolved}", **data)


def ensure_directory(path: str | Path | None) -> SharedResult:
    """Make sure a folder exists."""
    if path is None or str(path).strip() == "":
        return failure("Directory path is required", exit_code=EXIT_VALIDATION_ERROR, path=None)

    resolved = resolve_path(path)
    if resolved.exists() and not resolved.is_dir():
        return failure(
            f"Path exists but is not a directory: {resolved}",
            exit_code=EXIT_TYPE_MISMATCH,
            path=str(resolved),
            kind=_path_kind(resolved),
            exists=True,
        )

    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return failure(
            f"Could not create directory: {resolved}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(resolved),
            kind="missing",
            exists=False,
        )

    return success(
        f"Directory is ready: {resolved}",
        path=str(resolved),
        kind="directory",
        exists=True,
    )


def ensure_parent_directory(path: str | Path | None) -> SharedResult:
    """Make sure the folder for a file exists."""
    if path is None or str(path).strip() == "":
        return failure("File path is required", exit_code=EXIT_VALIDATION_ERROR, path=None)
    return ensure_directory(resolve_path(path).parent)


def require_file(path: str | Path) -> Path:
    """Return a file path, or stop if it is not a file."""
    result = check_path(path, expected="file")
    if result.success:
        return Path(result.data["path"])
    if result.exit_code == EXIT_NOT_FOUND:
        raise FileNotFoundError(result.error)
    raise ValueError(result.error)


def require_directory(path: str | Path) -> Path:
    """Return a folder path, or stop if it is not a folder."""
    result = check_path(path, expected="directory")
    if result.success:
        return Path(result.data["path"])
    if result.exit_code == EXIT_NOT_FOUND:
        raise FileNotFoundError(result.error)
    raise ValueError(result.error)
