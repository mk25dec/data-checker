from __future__ import annotations

import json
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

from shared_lib import (
    add_timestamp_to_path,
    apply_output_format_to_path,
    check_path,
    default_output_beside_input,
    ensure_directory,
    ensure_parent_directory,
    format_duration,
    normalize_file_stem,
    validate_input_file,
    validate_output_file,
)
from shared_lib.cli_common import validate_output_targets_exclusive
from shared_lib.cli_token_lists import resolve_comma_or_json_cli_list
from shared_lib.format_registry import supported_formats_hint, validate_format
from shared_lib.results import EXIT_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success

SUPPORTED_INPUT_FORMATS = ("csv", "json", "jsonl", "ndjson", "parquet", "xlsx")
SUPPORTED_OUTPUT_FORMATS = ("csv", "json", "jsonl", "ndjson", "parquet", "xlsx")


def default_output_format_beside_input(input_format: str) -> str:
    """
    When ``--output-format`` is omitted and the output is beside the input (no ``-f`` / ``-d``),
    pick a supported target type that differs from the input (typical one-step conversion).
    """
    pref = {
        "csv": "parquet",
        "parquet": "csv",
        "xlsx": "parquet",
        "json": "parquet",
        "jsonl": "parquet",
        "ndjson": "parquet",
    }
    cand = pref.get(input_format)
    if cand is not None and cand != input_format:
        return cand
    for f in SUPPORTED_OUTPUT_FORMATS:
        if f != input_format:
            return f
    return "csv"


class ConvertError(Exception):
    """Internal: mapped to SharedResult in run_convert."""

    def __init__(self, message: str, *, exit_code: int = EXIT_VALIDATION_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _load_duckdb():
    try:
        import duckdb
    except ModuleNotFoundError as exc:
        raise ConvertError(
            "DuckDB is not installed. Run this tool with Pixi or install the duckdb Python package.",
            exit_code=EXIT_ERROR,
        ) from exc
    return duckdb


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@dataclass(frozen=True)
class XlsxSheetJob:
    """One worksheet to read from an ``.xlsx`` (Polars ``read_excel``)."""

    file_stem: str
    sheet_id: int | None = None  # 1-based Polars ``sheet_id`` when selecting by index
    sheet_name: str | None = None  # exact workbook tab name


def _normalize_excel_sheet_tokens(args: SimpleNamespace) -> SharedResult:
    """
    Flatten ``-s`` / ``--excel-sheet`` and optional ``--excel-sheet-json`` into one token list.

    Returns ``success`` with ``data["sheet_tokens"]`` (possibly empty) or a validation ``failure``.
    """
    raw = getattr(args, "excel_sheet", None)
    if raw is None:
        seq: list[str] = []
    elif isinstance(raw, str):
        seq = [raw] if raw.strip() else []
    elif isinstance(raw, (list, tuple)):
        seq = [str(x) for x in raw if str(x).strip()]
    else:
        seq = [str(raw)]
    json_payload = getattr(args, "excel_sheet_json", None)
    return resolve_comma_or_json_cli_list(
        seq,
        json_payload,
        mutual_exclusive=True,
        comma_option_desc="-s/--excel-sheet (comma-separated and/or repeated)",
        json_option_desc="--excel-sheet-json",
        data_key="sheet_tokens",
        non_empty=False,
        json_label="--excel-sheet-json",
    )


def _list_xlsx_sheet_names(path: Path) -> SharedResult:
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read("xl/workbook.xml")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        return failure(
            f"Could not read XLSX workbook sheet list: {exc}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
        )
    root = ET.fromstring(data)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    names: list[str] = []
    for sh in root.findall(".//m:sheets/m:sheet", ns):
        nm = sh.attrib.get("name")
        if nm is not None:
            names.append(nm)
    if not names:
        return failure("No worksheets found in XLSX", exit_code=EXIT_VALIDATION_ERROR, path=str(path))
    return success("ok", sheet_names=names)


def _build_xlsx_sheet_jobs(path: Path, tokens: list[str]) -> SharedResult:
    names_r = _list_xlsx_sheet_names(path)
    if not names_r.success:
        return names_r
    names: list[str] = names_r.data["sheet_names"]
    if not tokens:
        return success("xlsx", jobs=[XlsxSheetJob(file_stem=names[0])])
    if len(tokens) == 1 and tokens[0].casefold() == "all":
        return success("xlsx", jobs=[XlsxSheetJob(file_stem=n, sheet_name=n) for n in names])
    if any(t.casefold() == "all" for t in tokens):
        return failure(
            "Use ALL alone for every sheet (e.g. -s ALL), not combined with other sheet selectors.",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
        )
    jobs: list[XlsxSheetJob] = []
    for t in tokens:
        if t.isdigit():
            idx = int(t)
            if idx < 0 or idx >= len(names):
                return failure(
                    f"Sheet index {idx} is out of range; workbook has {len(names)} sheet(s) (0..{len(names) - 1}).",
                    exit_code=EXIT_VALIDATION_ERROR,
                    path=str(path),
                )
            jobs.append(XlsxSheetJob(file_stem=names[idx], sheet_id=idx + 1))
        else:
            if t not in names:
                return failure(
                    f"Unknown XLSX sheet name {t!r}. Available: {names}.",
                    exit_code=EXIT_VALIDATION_ERROR,
                    path=str(path),
                )
            jobs.append(XlsxSheetJob(file_stem=t, sheet_name=t))
    return success("xlsx", jobs=jobs)


def _multi_sheet_output_paths(
    out_dir: Path,
    jobs: list[XlsxSheetJob],
    output_format: str,
    args: SimpleNamespace,
) -> SharedResult:
    stems = [normalize_file_stem(j.file_stem, default="sheet") for j in jobs]
    if len(set(stems)) != len(stems):
        return failure(
            "Two or more sheets map to the same output file name after sanitization; rename tabs or export fewer sheets.",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    paths: list[Path] = []
    stamps: list[str] = []
    ts = bool(getattr(args, "timestamp_output", False))
    for st in stems:
        base = out_dir / st
        applied = apply_output_format_to_path(base, output_format)
        if not applied.success:
            return applied
        p = Path(applied.data["path"]).resolve()
        if ts:
            ts_res = add_timestamp_to_path(p)
            if not ts_res.success:
                return ts_res
            p = Path(ts_res.data["path"])
            stamps.append(str(ts_res.data.get("timestamp") or ""))
        paths.append(p)
    stamp_used: str | None = stamps[0] if stamps else None
    return success("paths", paths=paths, stamps=stamps, stamp_used=stamp_used)


def _load_polars():
    try:
        import polars as pl
    except ImportError as exc:
        raise ConvertError(
            "polars is required for XLSX I/O. Install file-format-converter dependencies.",
            exit_code=EXIT_ERROR,
        ) from exc
    return pl


def _read_xlsx_dataframe(path: Path, args: SimpleNamespace, job: XlsxSheetJob) -> Any:
    """Load one worksheet with **Polars** (calamine / **fastexcel** engine)."""
    pl = _load_polars()
    kw: dict[str, Any] = {
        "engine": "calamine",
        "has_header": bool(getattr(args, "excel_header", True)),
        "drop_empty_rows": False,
    }
    if job.sheet_id is not None:
        kw["sheet_id"] = job.sheet_id
    elif job.sheet_name is not None:
        kw["sheet_name"] = job.sheet_name
    try:
        return pl.read_excel(path, **kw)
    except Exception as exc:
        raise ConvertError(
            f"Failed to read XLSX with Polars (calamine): {exc}",
            exit_code=EXIT_VALIDATION_ERROR,
        ) from exc


def _count_xlsx_data_rows_sheet_xml(path: Path, *, output_header: bool) -> int:
    """
    Count **data** rows in the first worksheet from Open XML (``sheet1.xml``).

    Polars (and other) Excel readers can drop an all-null trailing row on read; counting
    ``<row>`` elements matches what ``write_excel`` actually wrote (see ``worksheet=`` below).
    """
    sheet_path = "xl/worksheets/sheet1.xml"
    with zipfile.ZipFile(path) as zf:
        xml = zf.read(sheet_path).decode()
    root = ET.fromstring(xml)
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    row_els = root.findall(".//m:sheetData/m:row", ns)
    n = len(row_els)
    if output_header and n > 0:
        return n - 1
    return n


def _write_xlsx_polars(df: Any, path: Path, *, include_header: bool) -> None:
    """Write a Polars ``DataFrame`` to ``.xlsx`` via **Polars** ``write_excel`` (xlsxwriter under the hood)."""
    pl = _load_polars()
    if not isinstance(df, pl.DataFrame):
        raise ConvertError("Internal error: expected Polars DataFrame for XLSX export.", exit_code=EXIT_ERROR)
    try:
        df.write_excel(
            str(path),
            worksheet="Sheet1",
            include_header=include_header,
        )
    except Exception as exc:
        raise ConvertError(
            f"Failed to write XLSX with Polars: {exc}",
            exit_code=EXIT_ERROR,
        ) from exc


def _duckdb_result_to_polars(conn: Any, sql: str) -> Any:
    """Run ``sql`` and return a Polars ``DataFrame`` (``.pl()`` or Arrow fallback)."""
    res = conn.execute(sql)
    pl = _load_polars()
    if hasattr(res, "pl"):
        return res.pl()
    try:
        import pyarrow  # type: ignore[import-untyped]  # noqa: F401 — DuckDB Arrow interop

        return pl.from_arrow(res.fetch_arrow_table())
    except Exception as exc:
        raise ConvertError(
            f"Could not get Polars result from DuckDB: {exc}",
            exit_code=EXIT_ERROR,
        ) from exc


def input_relation_sql(args: SimpleNamespace, input_path: Path, input_format: str) -> str:
    path = sql_string(input_path)

    if input_format == "csv":
        options = ["auto_detect=true"]
        if args.csv_header is not None:
            options.append(f"header={str(args.csv_header).lower()}")
        if args.csv_delimiter is not None:
            options.append(f"delim={sql_string(args.csv_delimiter)}")
        return f"read_csv({path}, {', '.join(options)})"

    if input_format == "parquet":
        return f"read_parquet({path})"

    if input_format in {"json", "jsonl", "ndjson"}:
        options = []
        if input_format in {"jsonl", "ndjson"}:
            options.append("records=true")
        suffix = ", " + ", ".join(options) if options else ""
        return f"read_json_auto({path}{suffix})"

    if input_format == "xlsx":
        raise ConvertError(
            "XLSX input is loaded via Polars (calamine); use the registered table path instead of input_relation_sql.",
            exit_code=EXIT_ERROR,
        )

    raise ConvertError(f"Unsupported input format: {input_format}")


def copy_options(args: SimpleNamespace, output_format: str) -> list[str]:
    if output_format == "csv":
        options = ["FORMAT csv", f"HEADER {str(args.output_header).lower()}"]
        if args.output_csv_delimiter is not None:
            options.append(f"DELIMITER {sql_string(args.output_csv_delimiter)}")
        return options

    if output_format == "parquet":
        options = ["FORMAT parquet"]
        if args.parquet_compression:
            options.append(f"COMPRESSION {sql_string(args.parquet_compression)}")
        return options

    if output_format in {"json", "jsonl", "ndjson"}:
        options = ["FORMAT json"]
        if output_format in {"jsonl", "ndjson"}:
            options.append("ARRAY false")
        else:
            options.append("ARRAY true")
        return options

    if output_format == "xlsx":
        raise ConvertError(
            "XLSX output uses Polars write_excel (xlsxwriter), not DuckDB COPY options.",
            exit_code=EXIT_ERROR,
        )

    raise ConvertError(f"Unsupported output format: {output_format}")


def _execute_one_conversion(
    args: SimpleNamespace,
    *,
    input_path: Path,
    input_format: str,
    output_path: Path,
    output_format: str,
    df_xlsx: Any | None,
) -> SharedResult:
    """Run DuckDB + COPY or Polars XLSX write for one destination file. ``df_xlsx`` only for XLSX input."""
    ed = ensure_parent_directory(output_path)
    if not ed.success:
        return ed

    duckdb = _load_duckdb()
    conn = duckdb.connect(database=":memory:")
    copy_sql = ""
    try:
        if df_xlsx is not None:
            conn.register("__ffc_xlsx_in", df_xlsx)
            relation = "__ffc_xlsx_in"
        else:
            relation = input_relation_sql(args, input_path, input_format)

        where_clause = f" WHERE {args.where}" if args.where else ""
        limit_clause = f" LIMIT {args.limit}" if args.limit is not None else ""
        base_select_sql = f"SELECT * FROM {relation}{where_clause}{limit_clause}"

        if output_format == "xlsx":
            copy_sql = f"/* Polars.write_excel(xlsxwriter) */ {base_select_sql}"
            if getattr(args, "print_sql", False) and not getattr(args, "json_stdout", False):
                print(copy_sql)
            df_out = _duckdb_result_to_polars(conn, base_select_sql)
            _write_xlsx_polars(
                df_out,
                output_path,
                include_header=bool(getattr(args, "output_header", True)),
            )
            n_src = int(df_out.height)
            try:
                n_dst = _count_xlsx_data_rows_sheet_xml(
                    output_path,
                    output_header=bool(getattr(args, "output_header", True)),
                )
            except Exception as exc:
                return failure(
                    f"XLSX export verification failed: {exc}",
                    exit_code=EXIT_ERROR,
                    error=str(exc),
                    input_path=str(input_path),
                    output_path=str(output_path),
                )
            if n_src != n_dst:
                try:
                    output_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return failure(
                    f"XLSX row count mismatch after export (source rows={n_src}, xlsx rows={n_dst}).",
                    exit_code=EXIT_ERROR,
                    input_path=str(input_path),
                    output_path=str(output_path),
                    rows_source=n_src,
                    rows_xlsx=n_dst,
                )
        else:
            options = ", ".join(copy_options(args, output_format))
            copy_sql = f"COPY ({base_select_sql}) TO {sql_string(output_path)} ({options})"
            if getattr(args, "print_sql", False) and not getattr(args, "json_stdout", False):
                print(copy_sql)
            conn.execute(copy_sql)
    finally:
        conn.close()

    out_val = validate_output_file(
        output_path,
        output_format=output_format,
        supported_formats=SUPPORTED_OUTPUT_FORMATS,
        require_non_empty=False,
    )
    if not out_val.success:
        return out_val
    return success("ok", output_path=str(output_path), copy_sql=copy_sql)


def run_convert(args: SimpleNamespace) -> SharedResult:
    """
    Convert one tabular file to another (DuckDB in-memory). Returns SharedResult for chaining and JSON.

    ``args`` uses the same attributes as the CLI: ``input_file``, ``input_format``, ``output_file``,
    ``output_directory``, ``output_format``, ``overwrite``, ``quiet``, ``print_sql``, ``timestamp_output``,
    CSV/Excel/parquet/filter fields. With no ``output_file``, no ``output_directory``, and no
    ``output_format``, the output path is beside the input and the format defaults by input type (see
    ``default_output_format_beside_input``). For XLSX, ``excel_sheet`` may be a list: repeat ``-s`` for
    several sheets, use ``ALL`` alone for every sheet, or omit for the first sheet only; multiple sheets write
    ``<sheetname>.<ext>`` under ``--output-directory`` or beside the input (not with ``--output-file``).
    """
    t0 = perf_counter()
    try:
        return _run_convert_impl(args, t0)
    except ConvertError as exc:
        return failure(str(exc), exit_code=exc.exit_code, error=str(exc))
    except Exception as exc:
        if exc.__class__.__module__.startswith("duckdb"):
            return failure(str(exc), exit_code=EXIT_ERROR, error=str(exc), error_type="duckdb")
        raise


def _run_convert_impl(args: SimpleNamespace, t0: float) -> SharedResult:
    input_path = args.input_file.resolve()

    in_res = validate_input_file(
        input_path,
        input_format=getattr(args, "input_format", None),
        supported_formats=SUPPORTED_INPUT_FORMATS,
        require_non_empty=False,
    )
    if not in_res.success and in_res.exit_code == EXIT_VALIDATION_ERROR and "Could not determine" in (
        in_res.message or ""
    ):
        hint = supported_formats_hint(SUPPORTED_INPUT_FORMATS)
        return failure(
            f"{in_res.message} Use a suffix like .csv or pass -I/--input-format. Allowed: {hint}.",
            exit_code=EXIT_VALIDATION_ERROR,
            error=in_res.error or in_res.message,
            path=str(input_path),
        )
    if not in_res.success:
        return in_res
    input_format = str(in_res.data["format"])

    sheet_res = _normalize_excel_sheet_tokens(args)
    if not sheet_res.success:
        return sheet_res
    sheet_tokens = list(sheet_res.data.get("sheet_tokens") or [])
    if sheet_tokens and input_format != "xlsx":
        return failure(
            "-s/--excel-sheet applies only to XLSX input.",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(input_path),
        )

    xlsx_jobs: list[XlsxSheetJob] | None = None
    if input_format == "xlsx":
        jr = _build_xlsx_sheet_jobs(input_path, sheet_tokens)
        if not jr.success:
            return jr
        xlsx_jobs = list(jr.data["jobs"])
    multi_export = xlsx_jobs is not None and len(xlsx_jobs) > 1

    out_file_arg = getattr(args, "output_file", None)
    output_dir_arg = getattr(args, "output_directory", None)
    explicit_out_fmt = getattr(args, "output_format", None)
    stamp_used: str | None = None

    exclusive = validate_output_targets_exclusive(out_file_arg, output_dir_arg)
    if exclusive is not None:
        return exclusive

    if multi_export and out_file_arg is not None:
        return failure(
            "Multiple XLSX sheets: omit --output-file; use --output-directory DIR with -O/--output-format, "
            "or pass -O/--output-format alone to write <sheetname>.<ext> beside the input.",
            exit_code=EXIT_VALIDATION_ERROR,
            input_path=str(input_path),
        )

    output_format = ""
    paths: list[Path] = []
    beside_output_format_inferred = False

    if output_dir_arg is not None:
        if explicit_out_fmt is None:
            return failure(
                "With --output-directory, pass --output-format (writes <stem>.<ext> in that folder; "
                "use -T/--timestamp-output to add a timestamp to the file name). "
                f"Formats: {supported_formats_hint(SUPPORTED_OUTPUT_FORMATS)}.",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(input_path),
            )
        fmt_dir = validate_format(
            explicit_out_fmt,
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
        if multi_export:
            assert xlsx_jobs is not None
            mpaths = _multi_sheet_output_paths(out_dir, xlsx_jobs, output_format, args)
            if not mpaths.success:
                return mpaths
            paths = [Path(p) for p in mpaths.data["paths"]]
            stamp_used = mpaths.data.get("stamp_used")
        else:
            base = out_dir / input_path.stem
            applied = apply_output_format_to_path(base, canon)
            if not applied.success:
                return applied
            output_path = Path(applied.data["path"]).resolve()
            if output_path == input_path.resolve():
                base2 = out_dir / f"{input_path.stem}_converted"
                applied2 = apply_output_format_to_path(base2, canon)
                if not applied2.success:
                    return applied2
                output_path = Path(applied2.data["path"]).resolve()
            paths = [output_path]
    elif out_file_arg is None:
        resolved_out_fmt = explicit_out_fmt
        if resolved_out_fmt is None:
            resolved_out_fmt = default_output_format_beside_input(input_format)
            beside_output_format_inferred = True
        fmt_b = validate_format(
            resolved_out_fmt,
            path=None,
            supported_formats=SUPPORTED_OUTPUT_FORMATS,
            label="output format",
        )
        if not fmt_b.success:
            return fmt_b
        output_format = str(fmt_b.data["format"])
        if multi_export:
            assert xlsx_jobs is not None
            beside_dir = input_path.parent.resolve()
            mpaths = _multi_sheet_output_paths(beside_dir, xlsx_jobs, output_format, args)
            if not mpaths.success:
                return mpaths
            paths = [Path(p) for p in mpaths.data["paths"]]
            stamp_used = mpaths.data.get("stamp_used")
        else:
            beside = default_output_beside_input(
                input_path,
                output_format,
                supported_formats=SUPPORTED_OUTPUT_FORMATS,
            )
            if not beside.success:
                return beside
            paths = [Path(beside.data["output_path"])]
    else:
        output_path = out_file_arg.resolve()
        out_fmt_result = validate_format(
            getattr(args, "output_format", None),
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

    if not multi_export and bool(getattr(args, "timestamp_output", False)):
        ts_res = add_timestamp_to_path(paths[0])
        if not ts_res.success:
            return ts_res
        paths[0] = Path(ts_res.data["path"])
        stamp_used = str(ts_res.data.get("timestamp") or "") or None

    if input_format == output_format and len(paths) == 1:
        return failure(
            f"Input and output format are both {input_format!r}; no conversion is performed. "
            "Use a different --output-format (or output file suffix) if you need a different container.",
            exit_code=EXIT_VALIDATION_ERROR,
            input_path=str(input_path),
            output_path=str(paths[0]),
            input_format=input_format,
            output_format=output_format,
        )

    for out_p in paths:
        existing = check_path(out_p, expected="file")
        if existing.success and not args.overwrite:
            return failure(
                f"Output file already exists: {out_p}; pass -w/--overwrite to replace it",
                exit_code=EXIT_VALIDATION_ERROR,
                input_path=str(input_path),
                output_path=str(out_p),
            )

    copy_sqls: list[str] = []
    for i, out_p in enumerate(paths):
        if input_format == "xlsx":
            assert xlsx_jobs is not None
            job = xlsx_jobs[i]
            try:
                df_in = _read_xlsx_dataframe(input_path, args, job)
            except ConvertError as exc:
                return failure(str(exc), exit_code=exc.exit_code, error=str(exc))
            df_xlsx: Any | None = df_in
        else:
            df_xlsx = None
        one = _execute_one_conversion(
            args,
            input_path=input_path,
            input_format=input_format,
            output_path=out_p,
            output_format=output_format,
            df_xlsx=df_xlsx,
        )
        if not one.success:
            return one
        copy_sqls.append(str(one.data.get("copy_sql") or ""))

    duration = perf_counter() - t0
    primary = paths[0]
    data: dict[str, Any] = {
        "input_path": str(input_path),
        "output_path": str(primary),
        "output_paths": [str(p) for p in paths],
        "output_directory": str(primary.parent.resolve()),
        "input_format": input_format,
        "output_format": output_format,
        "output_format_inferred": beside_output_format_inferred,
        "duration_sec": round(duration, 4),
        "duration_human": format_duration(duration),
        "copy_sql": copy_sqls[-1] if copy_sqls else "",
        "copy_sqls": copy_sqls,
        "timestamp_output": bool(getattr(args, "timestamp_output", False)),
        "output_filename_timestamp": stamp_used,
    }
    if len(paths) == 1:
        msg = f"Converted {input_path} -> {primary} in {data['duration_human']}"
    else:
        msg = f"Converted {input_path} -> {len(paths)} files under {primary.parent} in {data['duration_human']}"
    return success(msg, **data)


def result_to_json(result: SharedResult, *, indent: int | None = 2) -> str:
    """Serialize a SharedResult to JSON (``indent=None`` for one line)."""
    return json.dumps(result.to_dict(), indent=indent)
