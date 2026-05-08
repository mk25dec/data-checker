from __future__ import annotations

# NOTE: Creates output folders and output file names.

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .format_registry import validate_format
from .naming import apply_output_format_to_path, normalize_file_stem
from .path_utils import ensure_directory, ensure_parent_directory
from .results import EXIT_VALIDATION_ERROR, SharedResult, failure, success


def make_run_id(prefix: str | None = None) -> str:
    """Make a short ID for one run."""
    raw = uuid4().hex[:8].upper()
    clean_prefix = normalize_file_stem(prefix, default="").upper() if prefix else ""
    return f"{clean_prefix}_{raw}" if clean_prefix else raw


def default_output_beside_input(
    input_path: str | Path,
    output_format: str,
    *,
    supported_formats: Iterable[str] | None = None,
    aliases: dict[str, str] | None = None,
    extension_map: dict[str, str] | None = None,
    converted_stem_suffix: str = "_converted",
) -> SharedResult:
    """
    Default output next to the input: same directory, same basename, suffix from ``output_format``.

    Validates ``output_format`` via :func:`validate_format` and builds the path via
    :func:`apply_output_format_to_path`. If the result would overwrite the input file, uses
    ``{stem}{converted_stem_suffix}`` before the extension.
    """
    inp = Path(input_path).expanduser().resolve()
    fmt_res = validate_format(
        output_format,
        path=None,
        supported_formats=supported_formats,
        aliases=aliases,
        label="output format",
    )
    if not fmt_res.success:
        return fmt_res

    canon = str(fmt_res.data["format"])
    out_dir = inp.parent
    base = out_dir / inp.stem

    applied = apply_output_format_to_path(base, canon, extension_map=extension_map, aliases=aliases)
    if not applied.success:
        return applied

    out_path = Path(applied.data["path"]).resolve()
    avoided_collision = False
    if out_path == inp.resolve():
        base2 = out_dir / f"{inp.stem}{converted_stem_suffix}"
        applied2 = apply_output_format_to_path(base2, canon, extension_map=extension_map, aliases=aliases)
        if not applied2.success:
            return applied2
        out_path = Path(applied2.data["path"]).resolve()
        avoided_collision = True

    return success(
        "Default output path beside input",
        input_path=str(inp),
        output_dir=str(out_dir.resolve()),
        output_path=str(out_path),
        format=canon,
        format_source=fmt_res.data.get("source"),
        stem=inp.stem,
        avoided_input_collision=avoided_collision,
    )


def make_output_folder(base_dir: str | Path | None, *, run_id: str | None = None) -> SharedResult:
    """Create an output folder for one run."""
    if base_dir is None or str(base_dir).strip() == "":
        return failure("Output base folder is required", exit_code=EXIT_VALIDATION_ERROR, path=None)

    folder_name = normalize_file_stem(run_id, default=make_run_id())
    output_dir = Path(base_dir).expanduser().resolve() / folder_name
    result = ensure_directory(output_dir)
    if not result.success:
        return result
    return success("Output folder is ready", output_dir=str(output_dir), run_id=folder_name)


def make_output_file_path(
    output_dir: str | Path | None,
    *,
    base_name: str | None = None,
    output_format: str,
    timestamp: str | None = None,
) -> SharedResult:
    """Create an output file path inside a folder."""
    if output_dir is None or str(output_dir).strip() == "":
        return failure("Output folder is required", exit_code=EXIT_VALIDATION_ERROR, path=None)

    folder_result = ensure_directory(output_dir)
    if not folder_result.success:
        return folder_result

    stamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stem = normalize_file_stem(base_name, default="output")
    raw_path = Path(folder_result.data["path"]) / f"{stem}_{stamp}"
    format_result = apply_output_format_to_path(raw_path, output_format)
    if not format_result.success:
        return format_result

    parent_result = ensure_parent_directory(format_result.data["path"])
    if not parent_result.success:
        return parent_result

    return success(
        "Output file path is ready",
        output_path=format_result.data["path"],
        output_dir=folder_result.data["path"],
        base_name=stem,
        format=format_result.data["format"],
        timestamp=stamp,
    )
