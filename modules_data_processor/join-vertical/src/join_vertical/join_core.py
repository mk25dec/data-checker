from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from shared_lib.cli_token_lists import parse_comma_separated_cli_tokens
from shared_lib import (
    add_timestamp_to_path,
    apply_output_format_to_path,
    check_path,
    ensure_directory,
    ensure_parent_directory,
    format_duration,
    load_manifest_parquet_paths,
    resolve_parquet_file_inputs,
    validate_input_file,
)
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

from .stack_engine import execute_vertical_stack

DEFAULT_INPUT_MAX_FILES_GUARD = 5000
SUPPORTED_INPUT_FORMATS = ("parquet",)
SCRIPT_OUTPUT_NAME = "join_vertical"


def result_to_json(result: SharedResult, *, indent: int | None = 2) -> str:
    """Serialize a ``SharedResult`` to JSON."""
    return json.dumps(result.to_dict(), indent=indent)


def _validate_parquet_inputs(paths: list[Path]) -> SharedResult | None:
    for p in paths:
        vr = validate_input_file(
            p,
            input_format=None,
            supported_formats=SUPPORTED_INPUT_FORMATS,
            require_non_empty=False,
        )
        if not vr.success:
            return vr
        fmt = str(vr.data.get("format") or "")
        if fmt != "parquet":
            return failure(
                f"Only Parquet input is allowed; got format {fmt!r} for {p}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(p),
            )
    return None


def run_join_vertical(args: SimpleNamespace) -> SharedResult:
    """Stack multiple Parquet files. ``args`` matches the CLI namespace from ``cli.py``."""
    t0 = perf_counter()
    try:
        return _run_join_vertical_impl(args, t0)
    except Exception as exc:
        return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc))


def _run_join_vertical_impl(args: SimpleNamespace, t0: float) -> SharedResult:
    input_patterns: list[str] = parse_comma_separated_cli_tokens(list(getattr(args, "input_patterns", []) or []))
    manifest = getattr(args, "input_manifest", None)
    manifest_paths: list[str] = []
    if manifest:
        try:
            manifest_paths = load_manifest_parquet_paths(Path(manifest))
        except (OSError, ValueError) as exc:
            return failure(str(exc), exit_code=EXIT_VALIDATION_ERROR, error=str(exc))

    if not input_patterns and not manifest_paths:
        return failure(
            "Provide at least one -i/--input path or glob, and/or --input-manifest.",
            exit_code=EXIT_VALIDATION_ERROR,
        )

    effective_max = int(getattr(args, "max_files", None) or DEFAULT_INPUT_MAX_FILES_GUARD)
    if effective_max < 1:
        return failure("max_files must be >= 1.", exit_code=EXIT_VALIDATION_ERROR)
    effective_max = min(effective_max, DEFAULT_INPUT_MAX_FILES_GUARD)

    try:
        raw = list(input_patterns) + manifest_paths
        resolved = resolve_parquet_file_inputs(
            raw,
            allow_empty_match=bool(getattr(args, "allow_empty_match", False)),
            max_files=effective_max,
            include_dirs=False,
        )
    except (OSError, ValueError) as exc:
        return failure(str(exc), exit_code=EXIT_VALIDATION_ERROR, error=str(exc))

    if not resolved:
        if getattr(args, "allow_empty_match", False):
            od_arg = getattr(args, "output_directory", None)
            empty_data: dict[str, Any] = {
                "duration_sec": round(perf_counter() - t0, 4),
                "artifacts": [],
            }
            if od_arg is not None:
                empty_data["output_directory"] = str(Path(od_arg).expanduser().resolve())
            return success("No files matched patterns; nothing to do.", **empty_data)
        return failure("No input Parquet files found.", exit_code=EXIT_VALIDATION_ERROR)

    bad = _validate_parquet_inputs(resolved)
    if bad is not None:
        return bad

    out_dir = getattr(args, "output_directory", None)
    if out_dir is None:
        return failure(
            "Missing --output-directory (directory for the stacked output file).",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    out_dir = Path(out_dir).expanduser().resolve()
    ed = ensure_directory(out_dir)
    if not ed.success:
        return ed

    output_format = str(getattr(args, "output_format", "csv") or "csv").lower()
    if output_format not in {"csv", "xlsx", "parquet"}:
        return failure(
            "output_format must be one of: csv, xlsx, parquet.",
            exit_code=EXIT_VALIDATION_ERROR,
        )

    stem = str(getattr(args, "output_stem", None) or SCRIPT_OUTPUT_NAME).strip() or SCRIPT_OUTPUT_NAME
    base = out_dir / stem
    applied = apply_output_format_to_path(base, output_format)
    if not applied.success:
        return applied
    output_path = Path(str(applied.data["path"])).resolve()

    if bool(getattr(args, "timestamp_output", False)):
        ts_res = add_timestamp_to_path(output_path)
        if not ts_res.success:
            return ts_res
        output_path = Path(str(ts_res.data["path"]))

    existing = check_path(output_path, expected="file")
    if existing.success and not bool(getattr(args, "overwrite", False)):
        return failure(
            f"Output file already exists: {output_path}; pass --overwrite to replace it.",
            exit_code=EXIT_VALIDATION_ERROR,
            output_path=str(output_path),
        )

    ep = ensure_parent_directory(output_path)
    if not ep.success:
        return ep

    union_type = str(getattr(args, "union_type", "all") or "all").lower()
    if union_type not in {"all", "distinct"}:
        return failure("union_type must be all or distinct.", exit_code=EXIT_VALIDATION_ERROR)

    on_error = str(getattr(args, "on_error", "skip-file") or "skip-file").lower()
    if on_error not in {"fail-fast", "skip-file"}:
        return failure("on_error must be fail-fast or skip-file.", exit_code=EXIT_VALIDATION_ERROR)

    type_mode = str(getattr(args, "type_mode", "preserve") or "preserve").lower()
    if type_mode not in {"preserve", "string"}:
        return failure("type_mode must be preserve or string.", exit_code=EXIT_VALIDATION_ERROR)

    fill_missing = str(getattr(args, "fill_missing", "") or "")
    output_csv_delim = str(getattr(args, "output_csv_delim", "|") or "|")
    threads = getattr(args, "threads", None)

    try:
        data = execute_vertical_stack(
            input_paths=resolved,
            output_path=output_path,
            fill_missing=fill_missing,
            union_type=union_type,
            on_error=on_error,
            type_mode=type_mode,
            output_csv_delim=output_csv_delim,
            output_format=output_format,
            threads=int(threads) if threads is not None else None,
        )
    except Exception as exc:  # noqa: BLE001
        return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc))

    duration = perf_counter() - t0
    input_paths_str = [str(p) for p in resolved]
    msg = (
        f"Stacked {len(resolved)} Parquet file(s) -> {data['output_path']} "
        f"({data['total_rows']:,} rows, {data['column_count']} columns) in {format_duration(duration)}"
    )
    artifacts = [
        {
            "role": "stacked_output",
            "path": data["output_path"],
            "format": output_format,
            "rows": data["total_rows"],
            "columns": data["column_count"],
        }
    ]
    return success(
        msg,
        duration_sec=round(duration, 4),
        duration_human=format_duration(duration),
        input_paths=input_paths_str,
        output_directory=str(out_dir),
        output_path=data["output_path"],
        total_rows=data["total_rows"],
        column_count=data["column_count"],
        sources_loaded=data["sources_loaded"],
        sources_skipped=data["sources_skipped"],
        sources_failed=data["sources_failed"],
        source_stats=data["source_stats"],
        duckdb_threads=data["duckdb_threads"],
        artifacts=artifacts,
    )
