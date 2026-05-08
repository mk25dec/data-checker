from __future__ import annotations

# NOTE: Shows the size of a file or folder.

from pathlib import Path

from .path_utils import check_path
from .results import EXIT_IO_ERROR, SharedResult, failure, success


def human_readable_size(size_bytes: int, *, precision: int | None = None) -> str:
    """Turn bytes into a size label like 12 KB or 3 MB."""
    if size_bytes < 0:
        raise ValueError("size_bytes must be greater than or equal to zero")

    units = ("B", "KB", "MB", "GB", "TB", "PB")
    size = float(size_bytes)
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{size_bytes} B"

    if precision is None:
        if size >= 100:
            precision = 0
        elif size >= 10:
            precision = 1
        else:
            precision = 2

    value = f"{size:.{precision}f}"
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return f"{value} {units[unit_index]}"


def get_file_size(path: str | Path | None, *, precision: int | None = None) -> SharedResult:
    """Get the size of one file."""
    path_result = check_path(path, expected="file")
    if not path_result.success:
        return path_result

    resolved = Path(path_result.data["path"])
    try:
        size_bytes = resolved.stat().st_size
    except OSError as exc:
        return failure(
            f"Could not read file size: {resolved}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(resolved),
        )

    size_kb = size_bytes / 1024
    size_mb = size_kb / 1024

    return success(
        f"File size read: {resolved}",
        path=str(resolved),
        file_name=resolved.name,
        size_bytes=size_bytes,
        size_kb=size_kb,
        size_mb=size_mb,
        size_human=human_readable_size(size_bytes, precision=precision),
    )


def _size_data(
    path: Path,
    size_bytes: int,
    *,
    precision: int | None,
    kind: str,
    skipped_paths: list[str] | None = None,
) -> dict:
    size_kb = size_bytes / 1024
    size_mb = size_kb / 1024
    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "size_bytes": size_bytes,
        "size_kb": size_kb,
        "size_mb": size_mb,
        "size_human": human_readable_size(size_bytes, precision=precision),
        "skipped_paths": skipped_paths or [],
    }


def get_directory_size(
    path: str | Path | None,
    *,
    precision: int | None = None,
    follow_symlinks: bool = False,
    fail_on_error: bool = False,
) -> SharedResult:
    """Get the total size of a folder."""
    path_result = check_path(path, expected="directory")
    if not path_result.success:
        return path_result

    resolved = Path(path_result.data["path"])
    total_bytes = 0
    file_count = 0
    directory_count = 0
    skipped_paths: list[str] = []
    errors: list[str] = []

    try:
        walker = resolved.rglob("*")
        for entry in walker:
            try:
                if entry.is_symlink() and not follow_symlinks:
                    skipped_paths.append(str(entry))
                    continue
                if entry.is_dir():
                    directory_count += 1
                    continue
                if entry.is_file():
                    total_bytes += entry.stat().st_size
                    file_count += 1
            except OSError as exc:
                skipped_paths.append(str(entry))
                errors.append(f"{entry}: {exc}")
                if fail_on_error:
                    return failure(
                        f"Could not read directory entry size: {entry}",
                        exit_code=EXIT_IO_ERROR,
                        error=str(exc),
                        **_size_data(
                            resolved,
                            total_bytes,
                            precision=precision,
                            kind="directory",
                            skipped_paths=skipped_paths,
                        ),
                        file_count=file_count,
                        directory_count=directory_count,
                        errors=errors,
                    )
    except OSError as exc:
        return failure(
            f"Could not scan directory: {resolved}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(resolved),
        )

    return success(
        f"Directory size read: {resolved}",
        **_size_data(resolved, total_bytes, precision=precision, kind="directory", skipped_paths=skipped_paths),
        file_count=file_count,
        directory_count=directory_count,
        errors=errors,
    )


def get_path_size(
    path: str | Path | None,
    *,
    precision: int | None = None,
    follow_symlinks: bool = False,
    fail_on_error: bool = False,
) -> SharedResult:
    """Get the size of a file or folder."""
    path_result = check_path(path, expected="any")
    if not path_result.success:
        return path_result

    kind = path_result.data["kind"]
    if kind == "file":
        return get_file_size(path, precision=precision)
    if kind == "directory":
        return get_directory_size(
            path,
            precision=precision,
            follow_symlinks=follow_symlinks,
            fail_on_error=fail_on_error,
        )
    return path_result
