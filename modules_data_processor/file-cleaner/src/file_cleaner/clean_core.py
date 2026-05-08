from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from shared_lib import ensure_directory, format_duration, validate_input_file
from shared_lib.cli_token_lists import parse_comma_separated_cli_tokens
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

from file_cleaner.clean_engine import (
    DEFAULT_INPUT_MAX_FILES_GUARD,
    HUGE_FILE_MODE_CHUNK_ROWS,
    HUGE_FILE_MODE_THRESHOLD_MB,
    SUPPORTED_INPUT_FORMATS,
    SUPPORTED_INPUT_SUFFIXES,
    _load_manifest_input_paths,
    _normalize_chunking_mode,
    _resolve_input_paths,
    process_single_file,
)

SCRIPT_OUTPUT_NAME = "file_cleaner"


def result_to_json(result: SharedResult, *, indent: int | None = 2) -> str:
    """Serialize a SharedResult to JSON (``indent=None`` for one line)."""
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


def run_clean(args: SimpleNamespace) -> SharedResult:
    """
    Clean one or more Parquet files. ``args`` matches the CLI namespace built in ``cli.py``.
    """
    t0 = perf_counter()
    try:
        return _run_clean_impl(args, t0)
    except Exception as exc:
        return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc))


def _run_clean_impl(args: SimpleNamespace, t0: float) -> SharedResult:
    if bool(getattr(args, "lowercase", False)) and bool(getattr(args, "uppercase", False)):
        return failure(
            "Use either --lowercase or --uppercase, not both.",
            exit_code=EXIT_VALIDATION_ERROR,
        )

    input_patterns: list[str] = parse_comma_separated_cli_tokens(list(getattr(args, "input_patterns", []) or []))
    manifest = getattr(args, "input_manifest", None)
    manifest_paths: list[str] = []
    if manifest:
        try:
            manifest_paths = _load_manifest_input_paths(str(manifest))
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
        resolved = _resolve_input_paths(
            raw,
            allow_empty_match=bool(getattr(args, "allow_empty_match", False)),
            max_files=effective_max,
            include_dirs=False,
            supported_suffixes=set(SUPPORTED_INPUT_SUFFIXES),
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
        # Same idea as file-format-converter: write beside the input (input file's parent folder).
        out_path = resolved[0].parent.resolve()
        if len(resolved) > 1:
            parents = {p.parent.resolve() for p in resolved}
            if len(parents) > 1:
                print(
                    "Note: inputs span multiple directories; using the first input's directory for all outputs: "
                    f"{out_path}. Pass -o/--output-directory to use one folder explicitly.",
                    file=sys.stderr,
                )
    else:
        out_path = Path(out_dir).expanduser().resolve()
    ed = ensure_directory(out_path)
    if not ed.success:
        return ed

    args.output_dir = str(out_path)

    expanded_inputs = [str(p) for p in resolved]
    max_input_mb = 0.0
    for input_path in expanded_inputs:
        try:
            max_input_mb = max(max_input_mb, os.path.getsize(input_path) / (1024 * 1024))
        except OSError:
            continue

    huge_file_mode = (
        _normalize_chunking_mode(getattr(args, "chunking_mode", "auto")) == "auto"
        and max_input_mb >= float(HUGE_FILE_MODE_THRESHOLD_MB)
    )
    if huge_file_mode:
        args.chunking_mode = "on"
        args.chunk_size_rows = min(int(getattr(args, "chunk_size_rows", 100000)), HUGE_FILE_MODE_CHUNK_ROWS)

    max_workers = 1 if huge_file_mode else min(len(expanded_inputs), os.cpu_count() or 1)
    failed = 0
    artifacts: list[dict[str, Any]] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        fut_map = {
            executor.submit(process_single_file, input_path, args, SCRIPT_OUTPUT_NAME): input_path
            for input_path in expanded_inputs
        }
        for fut in as_completed(fut_map):
            input_path = fut_map[fut]
            try:
                results = fut.result()
                if results.get("error"):
                    failed += 1
                    errors.append(f"{input_path}: {results['error']}")
                else:
                    artifacts.append({"input_file": input_path, "results": results})
                    for _fmt, outpath in results.items():
                        if isinstance(outpath, str) and outpath.startswith("Error"):
                            failed += 1
                            errors.append(f"{input_path}: {outpath}")
            except Exception as exc:
                failed += 1
                errors.append(f"{input_path}: {exc}")

    duration = perf_counter() - t0
    data: dict[str, Any] = {
        "output_directory": str(out_path),
        "inputs": expanded_inputs,
        "duration_sec": round(duration, 4),
        "duration_human": format_duration(duration),
        "artifacts": artifacts,
        "failed_count": failed,
    }
    if failed:
        err_msg = "; ".join(errors[:20]) + (" …" if len(errors) > 20 else "")
        return failure(
            err_msg,
            exit_code=EXIT_ERROR,
            error=err_msg,
            **data,
        )

    msg = f"Cleaned {len(expanded_inputs)} Parquet file(s) in {data['duration_human']}"
    return success(msg, **data)
