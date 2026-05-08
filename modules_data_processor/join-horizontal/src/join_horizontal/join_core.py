from __future__ import annotations

import json
import os
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
    format_duration,
    validate_input_file,
)
from shared_lib.cli_token_lists import resolve_comma_or_json_cli_list
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

from .engine import clean_col_name, join_files

SCRIPT_OUTPUT_NAME = "join_horizontal"
SUPPORTED_PARQUET = ("parquet",)


def result_to_json(result: SharedResult, *, indent: int | None = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent)


def _prepare_merge_on(left: Path, right: Path, raw: list[str]) -> tuple[SharedResult | None, list[str]]:
    """Return ``(failure, [])`` or ``(None, cleaned_merge_keys)`` after schema checks."""
    cleaned = [clean_col_name(m) for m in raw if clean_col_name(m)]
    if not cleaned:
        return (
            failure(
                "Pass at least one non-empty -k/--merge-on column (join key).",
                exit_code=EXIT_VALIDATION_ERROR,
            ),
            [],
        )
    if len(set(cleaned)) != len(cleaned):
        return (
            failure(
                "merge_on contains duplicate column names after trimming.",
                exit_code=EXIT_VALIDATION_ERROR,
            ),
            [],
        )
    try:
        left_cols = set(pl.scan_parquet(str(left)).collect_schema().names())
        right_cols = set(pl.scan_parquet(str(right)).collect_schema().names())
    except Exception as exc:
        return (
            failure(
                f"Could not read Parquet schema: {exc}",
                exit_code=EXIT_VALIDATION_ERROR,
                error=str(exc),
                path=str(left),
            ),
            [],
        )
    miss_l = [k for k in cleaned if k not in left_cols]
    if miss_l:
        hint = ", ".join(sorted(left_cols)[:50])
        return (
            failure(
                f"Merge key column(s) not in left Parquet: {miss_l}. "
                f"Left columns (sample): {hint}{' …' if len(left_cols) > 50 else ''}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(left),
            ),
            [],
        )
    miss_r = [k for k in cleaned if k not in right_cols]
    if miss_r:
        hint = ", ".join(sorted(right_cols)[:50])
        return (
            failure(
                f"Merge key column(s) not in right Parquet: {miss_r}. "
                f"Right columns (sample): {hint}{' …' if len(right_cols) > 50 else ''}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(right),
            ),
            [],
        )
    return None, cleaned


def _validate_parquet(path: Path) -> SharedResult | None:
    vr = validate_input_file(
        path,
        input_format=None,
        supported_formats=SUPPORTED_PARQUET,
        require_non_empty=False,
    )
    if not vr.success:
        return vr
    if str(vr.data.get("format") or "") != "parquet":
        return failure(
            f"Only Parquet input is allowed; got {vr.data.get('format')!r} for {path}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
        )
    return None


def _overwrite_targets(output_path: Path, output_format: str, *, timestamp_output: bool) -> list[Path]:
    """Paths the engine creates (main outputs + fixed-name input state when ``-T`` is off)."""
    fmt = output_format.lower()
    out_dir = output_path.parent
    paths: list[Path] = []
    if fmt == "xlsx":
        paths.append(output_path)
    else:
        base, ext = os.path.splitext(str(output_path))
        paths.extend([Path(f"{base}_result{ext}"), Path(f"{base}_stats{ext}")])
    if not timestamp_output:
        if fmt == "xlsx":
            paths.append(out_dir / "join_input_state.xlsx")
        else:
            _, ext = os.path.splitext(str(output_path))
            e = ext.lower()
            if e == ".pq":
                e = ".parquet"
            paths.append(out_dir / f"join_input_state_left{e}")
            paths.append(out_dir / f"join_input_state_right{e}")
    return paths


def run_join_horizontal(args: SimpleNamespace) -> SharedResult:
    """``args`` matches the CLI namespace from ``cli.py``."""
    t0 = perf_counter()
    try:
        return _run_join_horizontal_impl(args, t0)
    except Exception as exc:  # noqa: BLE001
        return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc))


def _run_join_horizontal_impl(args: SimpleNamespace, t0: float) -> SharedResult:
    left = Path(args.left_file).expanduser().resolve()
    right = Path(args.right_file).expanduser().resolve()
    for label, p in (("left", left), ("right", right)):
        bad = _validate_parquet(p)
        if bad is not None:
            return bad

    merge_res = resolve_comma_or_json_cli_list(
        list(getattr(args, "merge_on", []) or []),
        getattr(args, "merge_on_json", None),
        mutual_exclusive=True,
        comma_option_desc="-k/--merge-on (comma-separated and/or repeated)",
        json_option_desc="--merge-on-json",
        data_key="merge_on",
        non_empty=True,
        empty_error="Pass at least one -k/--merge-on column (join key), or --merge-on-json.",
        json_label="--merge-on-json",
    )
    if not merge_res.success:
        return merge_res
    raw_merge = list(merge_res.data["merge_on"])
    prep_err, merge_on = _prepare_merge_on(left, right, raw_merge)
    if prep_err is not None:
        return prep_err

    cfg_path = getattr(args, "config_file", None)
    if cfg_path is not None:
        cp = Path(cfg_path).expanduser().resolve()
        bad_c = _validate_parquet(cp)
        if bad_c is not None:
            return bad_c
        config_file = str(cp)
    else:
        config_file = None

    out_dir_arg = getattr(args, "output_directory", None)
    if out_dir_arg is None:
        out_dir = left.parent.resolve()
    else:
        out_dir = Path(out_dir_arg).expanduser().resolve()

    ed = ensure_directory(out_dir)
    if not ed.success:
        return ed

    output_format = str(getattr(args, "output_format", "parquet") or "parquet").lower()
    if output_format not in {"csv", "xlsx", "parquet"}:
        return failure(
            "output_format must be one of: csv, xlsx, parquet.",
            exit_code=EXIT_VALIDATION_ERROR,
        )

    how = str(getattr(args, "how", "outer") or "outer").lower()
    if how not in {"left", "right", "inner", "outer"}:
        return failure("how must be one of: left, right, inner, outer.", exit_code=EXIT_VALIDATION_ERROR)

    stem = SCRIPT_OUTPUT_NAME
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

    ts_out = bool(getattr(args, "timestamp_output", False))
    if not bool(getattr(args, "overwrite", False)):
        for candidate in _overwrite_targets(output_path, output_format, timestamp_output=ts_out):
            ex = check_path(candidate, expected="file")
            if ex.success:
                return failure(
                    f"Output file already exists: {candidate}; pass -w/--overwrite to replace it.",
                    exit_code=EXIT_VALIDATION_ERROR,
                    output_path=str(candidate),
                )

    try:
        result = join_files(
            file1=str(left),
            file2=str(right),
            config_file=config_file,
            merge_on=merge_on,
            how=how,
            output_file=str(output_path),
            output_file_name=stem,
            output_format=output_format,
            file1_sheet=None,
            file2_sheet=None,
            config_sheet=None,
            file1_name=getattr(args, "left_name", None),
            file2_name=getattr(args, "right_name", None),
            large_mode=bool(getattr(args, "large_mode", False)),
            threads=getattr(args, "threads", None),
            temp_directory=getattr(args, "temp_directory", None),
            memory_limit=getattr(args, "memory_limit", None),
            debug=bool(getattr(args, "debug", False)),
            max_runtime_seconds=getattr(args, "max_runtime_seconds", None),
            timestamp_output=ts_out,
            dedupe_left_keys=bool(getattr(args, "dedupe_left_keys", False)),
            dedupe_right_keys=bool(getattr(args, "dedupe_right_keys", False)),
        )
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as exc:
        return failure(str(exc), exit_code=EXIT_VALIDATION_ERROR, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc))

    duration = perf_counter() - t0
    out_base = str(result.get("output_file") or "")
    if output_format == "xlsx":
        out_main = out_base
    else:
        b, ext = os.path.splitext(out_base)
        out_main = f"{b}_result{ext}"
    msg = f"Joined {left.name} + {right.name} -> {out_main} in {format_duration(duration)}"
    artifacts: list[dict[str, Any]] = [{"role": "join_output", "path": out_main, "format": output_format}]
    if output_format != "xlsx":
        b, ext = os.path.splitext(out_base)
        artifacts.append({"role": "join_stats", "path": f"{b}_stats{ext}", "format": output_format})
    state_paths = result.get("input_state_files")
    if not isinstance(state_paths, list) or not state_paths:
        legacy = str(result.get("input_state_file") or "").strip()
        state_paths = [legacy] if legacy else []
    for i, p in enumerate(state_paths):
        if not p:
            continue
        role = "input_state" if len(state_paths) == 1 else ("input_state_left" if i == 0 else "input_state_right")
        artifacts.append({"role": role, "path": p, "format": output_format})
    return success(
        msg,
        duration_sec=round(duration, 4),
        duration_human=format_duration(duration),
        left_file=str(left),
        right_file=str(right),
        merge_on=merge_on,
        output_directory=str(out_dir),
        output_path=out_main,
        output_format=output_format,
        input_state_file=str(result.get("input_state_file") or ""),
        input_state_files=state_paths,
        execution_time_sec=result.get("execution_time_sec"),
        artifacts=artifacts,
    )
