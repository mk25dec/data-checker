from __future__ import annotations

import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from shared_lib import check_path, format_duration, validate_input_file
from shared_lib.parquet_row_audit import (
    export_tagged_data_report_csv,
    run_parquet_row_audit,
    write_parquet_row_audit_report,
)
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

SUPPORTED_PARQUET = ("parquet",)


def result_to_json(result: SharedResult, *, indent: int | None = 2) -> str:
    return json.dumps(result.to_dict(), indent=indent)


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


def run_data_check(args: SimpleNamespace) -> SharedResult:
    """``args`` matches the CLI namespace from ``cli.py``."""
    t0 = perf_counter()
    try:
        return _run_data_check_impl(args, t0)
    except Exception as exc:  # noqa: BLE001
        return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc))


def _run_data_check_impl(args: SimpleNamespace, t0: float) -> SharedResult:
    inp = Path(args.input_file).expanduser().resolve()
    bad = _validate_parquet(inp)
    if bad is not None:
        return bad

    keys = list(getattr(args, "key_columns", None) or [])
    keys = [str(k).strip() for k in keys if str(k).strip()]

    audit = run_parquet_row_audit(inp, key_columns=keys if keys else None)
    if not audit.success:
        return audit

    fmt_arg = str(getattr(args, "report_format", "") or "").strip().lower().lstrip(".")
    explicit = getattr(args, "report_file", None)
    if explicit is not None and str(explicit).strip():
        rp = Path(str(explicit)).expanduser().resolve()
    else:
        if fmt_arg in {"csv", "parquet"}:
            ext = "parquet" if fmt_arg == "parquet" else "csv"
        elif fmt_arg == "":
            ext = "csv"
        else:
            return failure(
                "--report-format must be csv or parquet when set.",
                exit_code=EXIT_VALIDATION_ERROR,
            )
        rp = inp.parent / f"{inp.stem}_row_audit.{ext}"

    if fmt_arg in {"csv", "parquet"}:
        fmt: str = fmt_arg
    else:
        suf = rp.suffix.lower()
        if suf == ".csv":
            fmt = "csv"
        elif suf in {".parquet", ".pq"}:
            fmt = "parquet"
        else:
            return failure(
                "Could not infer report format: use .csv or .parquet on --report-file, "
                "or pass --report-format csv|parquet (default report is written beside the input).",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(rp),
            )

    if not bool(getattr(args, "overwrite", False)):
        exists = check_path(rp, expected="file")
        if exists.success:
            return failure(
                f"Report file already exists: {rp}; pass -w/--overwrite to replace it.",
                exit_code=EXIT_VALIDATION_ERROR,
                output_path=str(rp),
            )

    wr = write_parquet_row_audit_report(audit.data, rp, output_format=fmt)  # type: ignore[arg-type]
    if not wr.success:
        return wr
    merged = dict(audit.data)
    merged["report_path"] = str(rp)
    merged["report_format"] = fmt
    artifacts: list[dict[str, Any]] = [{"role": "row_audit_report", "path": str(rp), "format": fmt}]

    if bool(getattr(args, "data_report", False)):
        dr = inp.parent / f"{inp.stem}_data_report.csv"
        if not bool(getattr(args, "overwrite", False)):
            dr_exists = check_path(dr, expected="file")
            if dr_exists.success:
                return failure(
                    f"Data report file already exists: {dr}; pass -w/--overwrite to replace it.",
                    exit_code=EXIT_VALIDATION_ERROR,
                    output_path=str(dr),
                )
        tagged = export_tagged_data_report_csv(inp, dr, key_columns=keys if keys else None)
        if not tagged.success:
            return tagged
        merged["data_report_path"] = str(dr)
        artifacts.append({"role": "tagged_data_report", "path": str(dr), "format": "csv"})

    duration = perf_counter() - t0
    extra = f"; data report: {merged['data_report_path']}" if merged.get("data_report_path") else ""
    return success(
        f"Row audit for {inp.name} in {format_duration(duration)}; report: {rp}{extra}",
        duration_sec=round(duration, 4),
        duration_human=format_duration(duration),
        artifacts=artifacts,
        **merged,
    )
