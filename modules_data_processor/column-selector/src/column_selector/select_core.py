from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import polars as pl

from shared_lib import (
    add_timestamp_to_path,
    apply_output_format_to_path,
    check_path,
    ensure_directory,
    ensure_parent_directory,
    format_duration,
    validate_input_file,
)
from shared_lib.cli_common import validate_output_targets_exclusive
from shared_lib.cli_token_lists import resolve_comma_or_json_cli_list
from shared_lib.format_registry import detect_format_from_path, supported_formats_hint, validate_format
from shared_lib.output_paths import default_output_beside_input
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

SUPPORTED_INPUT_FORMATS = ("parquet",)
SUPPORTED_OUTPUT_FORMATS = ("csv", "parquet")
DEFAULT_OUTPUT_FORMAT = "parquet"
DEFAULT_STEM_SUFFIX = "_selectedColumns"
_SUPPORTED_OUT = frozenset(SUPPORTED_OUTPUT_FORMATS)


def result_to_json(result: SharedResult, *, indent: int | None = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent)


def _explicit_format_token(explicit_out_fmt: str | None) -> str | None:
    if explicit_out_fmt is None:
        return None
    t = str(explicit_out_fmt).strip()
    return t or None


def _resolve_output_paths(
    input_path: Path,
    *,
    out_file_arg: Path | None,
    output_dir_arg: Path | None,
    explicit_out_fmt: str | None,
    timestamp_output: bool,
) -> SharedResult:
    exclusive = validate_output_targets_exclusive(out_file_arg, output_dir_arg)
    if exclusive is not None:
        return exclusive

    paths: list[Path] = []
    output_format = ""
    explicit_clean = _explicit_format_token(explicit_out_fmt)

    if output_dir_arg is not None:
        fmt_token = explicit_clean or DEFAULT_OUTPUT_FORMAT
        fmt_dir = validate_format(
            fmt_token,
            path=None,
            supported_formats=SUPPORTED_OUTPUT_FORMATS,
            label="output format",
        )
        if not fmt_dir.success:
            return fmt_dir
        canon = str(fmt_dir.data["format"])
        output_format = canon
        out_dir = Path(output_dir_arg).expanduser().resolve()
        dir_ok = ensure_directory(out_dir)
        if not dir_ok.success:
            return dir_ok
        base = out_dir / input_path.stem
        applied = apply_output_format_to_path(base, canon)
        if not applied.success:
            return applied
        output_path = Path(applied.data["path"]).resolve()
        if output_path == input_path.resolve():
            base2 = out_dir / f"{input_path.stem}{DEFAULT_STEM_SUFFIX}"
            applied2 = apply_output_format_to_path(base2, canon)
            if not applied2.success:
                return applied2
            output_path = Path(applied2.data["path"]).resolve()
        paths = [output_path]
    elif out_file_arg is None:
        fmt_token = explicit_clean or DEFAULT_OUTPUT_FORMAT
        fmt_b = validate_format(
            fmt_token,
            path=None,
            supported_formats=SUPPORTED_OUTPUT_FORMATS,
            label="output format",
        )
        if not fmt_b.success:
            return fmt_b
        output_format = str(fmt_b.data["format"])
        beside = default_output_beside_input(
            input_path,
            fmt_token,
            supported_formats=SUPPORTED_OUTPUT_FORMATS,
            converted_stem_suffix=DEFAULT_STEM_SUFFIX,
        )
        if not beside.success:
            return beside
        paths = [Path(str(beside.data["output_path"])).resolve()]
    else:
        output_path = Path(out_file_arg).expanduser().resolve()
        if explicit_clean is not None:
            out_fmt_result = validate_format(
                explicit_clean,
                path=output_path,
                supported_formats=SUPPORTED_OUTPUT_FORMATS,
                label="output format",
            )
        else:
            detected = detect_format_from_path(output_path)
            if detected in _SUPPORTED_OUT:
                out_fmt_result = validate_format(
                    detected,
                    path=output_path,
                    supported_formats=SUPPORTED_OUTPUT_FORMATS,
                    label="output format",
                )
            else:
                applied = apply_output_format_to_path(output_path, DEFAULT_OUTPUT_FORMAT)
                if not applied.success:
                    return failure(
                        f"{applied.message} Use a path ending in .csv or .parquet, or pass -O/--output-format. "
                        f"Allowed: {supported_formats_hint(SUPPORTED_OUTPUT_FORMATS)}.",
                        exit_code=EXIT_VALIDATION_ERROR,
                        path=str(input_path),
                        output_path=str(output_path),
                    )
                output_path = Path(str(applied.data["path"])).resolve()
                out_fmt_result = validate_format(
                    DEFAULT_OUTPUT_FORMAT,
                    path=output_path,
                    supported_formats=SUPPORTED_OUTPUT_FORMATS,
                    label="output format",
                )
        if not out_fmt_result.success and "Could not determine" in (out_fmt_result.message or ""):
            hint = supported_formats_hint(SUPPORTED_OUTPUT_FORMATS)
            return failure(
                f"{out_fmt_result.message} Use a suffix like .parquet or pass -O/--output-format. Allowed: {hint}.",
                exit_code=EXIT_VALIDATION_ERROR,
                error=out_fmt_result.error or out_fmt_result.message,
                path=str(input_path),
                output_path=str(output_path),
            )
        if not out_fmt_result.success:
            return out_fmt_result
        output_format = str(out_fmt_result.data["format"])
        paths = [output_path]

    if bool(timestamp_output):
        ts_res = add_timestamp_to_path(paths[0])
        if not ts_res.success:
            return ts_res
        paths[0] = Path(str(ts_res.data["path"]))

    return success(
        "Output path resolved",
        paths=paths,
        output_format=output_format,
        path=str(input_path),
    )


def run_select(args: SimpleNamespace) -> SharedResult:
    """``args`` matches the CLI namespace from ``cli.py``."""
    t0 = perf_counter()
    input_path = Path(args.input_file).expanduser().resolve()

    vr = validate_input_file(
        input_path,
        input_format=None,
        supported_formats=SUPPORTED_INPUT_FORMATS,
        require_non_empty=False,
    )
    if not vr.success:
        if vr.exit_code == EXIT_VALIDATION_ERROR and "Could not determine" in (vr.message or ""):
            hint = supported_formats_hint(SUPPORTED_INPUT_FORMATS)
            return failure(
                f"{vr.message} Only Parquet input is allowed (.parquet / .pq). {hint}.",
                exit_code=EXIT_VALIDATION_ERROR,
                error=vr.error or vr.message,
                path=str(input_path),
            )
        return vr

    raw_cols = list(getattr(args, "column", []) or [])
    columns_json = getattr(args, "columns_json", None)
    resolved_cols = resolve_comma_or_json_cli_list(
        raw_cols,
        columns_json,
        mutual_exclusive=True,
        comma_option_desc="-c/--column (comma-separated and/or repeated)",
        json_option_desc="--columns-json",
        data_key="columns",
        non_empty=True,
        empty_error=(
            "Pass at least one column: -c/--column (comma-separated, e.g. -c 'a,b', quote names with spaces) "
            "or --columns-json '[\"a\",\"b\"]'."
        ),
        json_label="--columns-json",
    )
    if not resolved_cols.success:
        return failure(
            resolved_cols.message,
            exit_code=resolved_cols.exit_code,
            error=resolved_cols.error or resolved_cols.message,
            path=str(input_path),
        )
    keep = list(resolved_cols.data["columns"])

    try:
        schema_names = pl.scan_parquet(str(input_path)).collect_schema().names()
    except Exception as exc:  # noqa: BLE001
        return failure(
            f"Could not read Parquet schema: {exc}",
            exit_code=EXIT_VALIDATION_ERROR,
            error=str(exc),
            path=str(input_path),
        )

    available = set(schema_names)
    missing = [c for c in keep if c not in available]
    if missing:
        sample = ", ".join(schema_names[:40])
        tail = " …" if len(schema_names) > 40 else ""
        return failure(
            f"Unknown column name(s): {missing}. "
            f"Parquet columns (sample): {sample}{tail}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(input_path),
        )

    out_file_arg = getattr(args, "output_file", None)
    output_dir_arg = getattr(args, "output_directory", None)
    explicit_out_fmt = getattr(args, "output_format", None)
    if isinstance(explicit_out_fmt, str) and not explicit_out_fmt.strip():
        explicit_out_fmt = None

    resolved = _resolve_output_paths(
        input_path,
        out_file_arg=out_file_arg,
        output_dir_arg=output_dir_arg,
        explicit_out_fmt=explicit_out_fmt,
        timestamp_output=bool(getattr(args, "timestamp_output", False)),
    )
    if not resolved.success:
        return resolved

    paths = [Path(p) for p in resolved.data["paths"]]
    output_format = str(resolved.data["output_format"])
    out_path = paths[0]

    existing = check_path(out_path, expected="file")
    if existing.success and not bool(getattr(args, "overwrite", False)):
        return failure(
            f"Output file already exists: {out_path}; pass -w/--overwrite to replace it.",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(input_path),
            output_path=str(out_path),
        )

    parent = ensure_parent_directory(out_path)
    if not parent.success:
        return parent

    lf = pl.scan_parquet(str(input_path)).select(keep)
    try:
        if output_format == "parquet":
            lf.sink_parquet(str(out_path), compression="zstd")
        else:
            lf.sink_csv(str(out_path))
    except Exception as exc:  # noqa: BLE001
        return failure(
            f"Failed to write output: {exc}",
            exit_code=EXIT_ERROR,
            error=str(exc),
            path=str(input_path),
            output_path=str(out_path),
        )

    duration = perf_counter() - t0
    msg = (
        f"Wrote {out_path} ({len(keep)} column{'s' if len(keep) != 1 else ''}, {output_format}) "
        f"in {format_duration(duration)}"
    )
    data: dict[str, Any] = {
        "input_path": str(input_path),
        "output_path": str(out_path),
        "output_format": output_format,
        "columns": keep,
        "duration_sec": round(duration, 4),
        "duration_human": format_duration(duration),
    }
    return success(msg, **data)
