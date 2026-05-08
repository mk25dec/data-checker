from __future__ import annotations

# NOTE: Pick newest regular files in a directory by mtime or ctime, optional glob filter.

from pathlib import Path
from typing import Literal

SortBy = Literal["mtime", "ctime"]


def _validate_sort_by(sort_by: str) -> SortBy:
    if sort_by == "mtime":
        return "mtime"
    if sort_by == "ctime":
        return "ctime"
    raise ValueError("sort_by must be 'mtime' or 'ctime'")


def _time_ns(path: Path, sort_by: SortBy) -> int:
    st = path.stat()
    if sort_by == "mtime":
        if hasattr(st, "st_mtime_ns"):
            return int(st.st_mtime_ns)
        return int(st.st_mtime * 1_000_000_000)
    if hasattr(st, "st_ctime_ns"):
        return int(st.st_ctime_ns)
    return int(st.st_ctime * 1_000_000_000)


def find_latest_files(
    directory: str | Path,
    *,
    top_n: int = 5,
    pattern: str | None = None,
    sort_by: SortBy = "mtime",
    recursive: bool = False,
) -> list[Path]:
    """
    Return up to ``top_n`` regular files under ``directory``, newest first.

    * ``sort_by="mtime"`` — last data modification (``st_mtime``).
    * ``sort_by="ctime"`` — ``st_ctime`` (metadata change on Unix; often creation time on Windows).

    If ``pattern`` is set, paths are matched with ``Path.glob`` / ``Path.rglob`` (e.g. ``*.csv``,
    ``report_*.parquet``). With ``recursive=False`` (default), only files **directly** in
    ``directory`` are considered. With ``recursive=True``, all matching regular files under
    ``directory`` are considered (``rglob`` semantics).

    Ties are broken by descending path string so ordering is stable.
    """
    root = Path(directory).expanduser().resolve()
    sort_key = _validate_sort_by(str(sort_by))
    if not root.exists():
        raise FileNotFoundError(f"Directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if recursive:
        if pattern is None or str(pattern).strip() == "":
            candidates = [p for p in root.rglob("*") if p.is_file()]
        else:
            candidates = [p for p in root.rglob(str(pattern)) if p.is_file()]
    elif pattern is None or str(pattern).strip() == "":
        candidates = [p for p in root.iterdir() if p.is_file()]
    else:
        candidates = [p for p in root.glob(str(pattern)) if p.is_file()]

    ranked = sorted(
        candidates,
        key=lambda p: (_time_ns(p, sort_key), str(p)),
        reverse=True,
    )
    return ranked[:top_n]


def find_latest_file(
    directory: str | Path,
    *,
    pattern: str | None = None,
    sort_by: SortBy = "mtime",
    recursive: bool = False,
) -> Path | None:
    """Newest single file in ``directory``, or ``None`` if no matching regular file exists."""
    found = find_latest_files(directory, top_n=1, pattern=pattern, sort_by=sort_by, recursive=recursive)
    return found[0] if found else None
