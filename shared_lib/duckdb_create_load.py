from __future__ import annotations

# NOTE: Load large supported files into a persistent DuckDB file with bounded resources.
# CSV: faulty lines are recorded via DuckDB store_rejects and exported to a rejects CSV.

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .file_size import human_readable_size
from .path_utils import ensure_parent_directory, resolve_path
from .results import EXIT_IO_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success
from .validate_input import validate_input_file

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

DUCKDB_LOAD_SUPPORTED_FORMATS: tuple[str, ...] = ("csv", "json", "jsonl", "ndjson", "parquet", "xlsx")


@dataclass(frozen=True)
class DuckdbResourceLimits:
    """Bound DuckDB memory, parallelism, and spill directory for large-file ingest."""

    memory_limit: str = "2GB"
    threads: int | None = None
    temp_directory: str | Path | None = None


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _validate_identifier(name: str, label: str) -> SharedResult | None:
    if not name or not _IDENT.match(name):
        return failure(
            f"Invalid {label}: use only letters, digits, underscore; max 63 chars; must start with letter or _",
            exit_code=EXIT_VALIDATION_ERROR,
            identifier=name,
            label=label,
        )
    return None


def _duck_ident(name: str) -> str:
    """Quote a validated SQL identifier for DuckDB."""
    return '"' + name.replace('"', '""') + '"'


def _count_physical_newlines(path: Path, max_bytes: int) -> tuple[int | None, str | None]:
    """Return (newline_count, skip_reason). Bounded read for large text inputs."""
    try:
        size = path.stat().st_size
    except OSError:
        return None, "could not stat input file"
    if size > max_bytes:
        return None, f"skipped (input is {size} bytes; max for line scan is {max_bytes})"
    total = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            total += chunk.count(b"\n")
    return total, None


def _import_duckdb() -> Any | SharedResult:
    try:
        import duckdb
    except ModuleNotFoundError:
        return failure(
            "duckdb package is not installed; install duckdb to use load_file_into_database",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    return duckdb


def _ensure_excel_extension(conn: Any, duckdb_mod: Any) -> SharedResult:
    try:
        conn.execute("LOAD excel")
    except duckdb_mod.Error:
        try:
            conn.execute("INSTALL excel")
            conn.execute("LOAD excel")
        except duckdb_mod.Error as exc:
            return failure(
                "DuckDB Excel extension could not be loaded; install/load excel or use another format",
                exit_code=EXIT_IO_ERROR,
                error=str(exc),
            )
    return success("Excel extension ready")


def _apply_resource_limits(conn: Any, limits: DuckdbResourceLimits) -> None:
    conn.execute(f"SET memory_limit = {_sql_string(limits.memory_limit)}")
    if limits.threads is not None:
        conn.execute(f"SET threads = {int(limits.threads)}")
    if limits.temp_directory is not None:
        td = resolve_path(limits.temp_directory)
        td.mkdir(parents=True, exist_ok=True)
        conn.execute(f"SET temp_directory = {_sql_string(str(td))}")


def _read_relation_sql(
    input_path: Path,
    fmt: str,
    *,
    csv_header: bool | None,
    csv_delimiter: str | None,
    csv_max_line_size: int | None,
    excel_sheet: str | None,
    excel_header: bool | None,
    store_rejects: bool,
    rejects_table: str,
    rejects_scan: str,
    rejects_limit: int,
) -> str:
    p = _sql_string(input_path)

    if fmt == "csv":
        parts = [
            "auto_detect=true",
            f"store_rejects={str(store_rejects).lower()}",
            f"rejects_table={_sql_string(rejects_table)}",
            f"rejects_scan={_sql_string(rejects_scan)}",
            f"rejects_limit={int(rejects_limit)}",
        ]
        if csv_header is not None:
            parts.append(f"header={str(csv_header).lower()}")
        if csv_delimiter is not None:
            if len(csv_delimiter) != 1:
                raise ValueError("csv_delimiter must be a single character")
            parts.append(f"delim={_sql_string(csv_delimiter)}")
        if csv_max_line_size is not None:
            parts.append(f"max_line_size={int(csv_max_line_size)}")
        return f"read_csv({p}, {', '.join(parts)})"

    if fmt == "parquet":
        return f"read_parquet({p})"

    if fmt in {"json", "jsonl", "ndjson"}:
        opts: list[str] = []
        if fmt in {"jsonl", "ndjson"}:
            opts.append("records=true")
        suffix = ", " + ", ".join(opts) if opts else ""
        return f"read_json_auto({p}{suffix})"

    if fmt == "xlsx":
        parts = [f"header={str(excel_header if excel_header is not None else True).lower()}"]
        if excel_sheet:
            parts.append(f"sheet={_sql_string(excel_sheet)}")
        return f"read_xlsx({p}, {', '.join(parts)})"

    raise ValueError(f"unsupported format: {fmt}")


def load_file_into_database(
    input_path: str | Path,
    database_path: str | Path,
    *,
    input_format: str | None = None,
    table_name: str = "loaded_data",
    rejects_csv_path: str | Path | None = None,
    supported_formats: Iterable[str] | None = None,
    limits: DuckdbResourceLimits | None = None,
    rejects_limit: int = 1_000_000,
    csv_header: bool | None = None,
    csv_delimiter: str | None = None,
    csv_max_line_size: int | None = None,
    excel_sheet: str | None = None,
    excel_header: bool | None = None,
    require_non_empty_input: bool = False,
    rejects_table: str = "sl_reject_errors",
    rejects_scan: str = "sl_reject_scans",
    scan_input_physical_lines: bool = True,
    line_count_max_input_bytes: int = 512 * 1024 * 1024,
) -> SharedResult:
    """
    Create or open a DuckDB database file and load one supported table from ``input_path``.

    Resource usage is bounded via ``DuckdbResourceLimits`` (memory, threads, temp directory).

    **CSV:** uses DuckDB ``store_rejects`` so faulty lines are skipped for the main table and
    recorded in a rejects table; up to ``rejects_limit`` reject rows are kept (see DuckDB docs;
    ``0`` means no cap). Those rows are written to ``rejects_csv_path`` when provided, or by
    default to ``<input_stem>_rejects.csv`` beside ``database_path``.

    **Parquet / JSON / XLSX:** DuckDB does not expose the same line-level rejects; the main table
    is loaded with a normal read. On success, ``rejects_distinct_lines`` and
    ``rejects_error_entries`` are ``0``; ``rejects_csv_path`` is ``None`` unless you passed an
    explicit path, in which case a short CSV note is written.

    Returns ``SharedResult`` with ``data`` including ``input_size_bytes``, ``input_size_human``,
    ``input_physical_lines`` (newline count for text-like inputs when scanned; else ``null``),
    ``input_physical_lines_note``, ``columns_loaded``, ``column_names``, ``rows_loaded``,
    ``rejects_distinct_lines``, ``rejects_error_entries``, ``rejects_csv_path``, ``database_path``,
    ``table_name``, ``user_notice``.

    Physical line counting scans the raw file (bounded by ``line_count_max_input_bytes`` when
    ``scan_input_physical_lines`` is true). It is a newline count, not DuckDB's logical CSV rows.
    """
    bad = _validate_identifier(table_name, "table_name")
    if bad is not None:
        return bad
    bad = _validate_identifier(rejects_table, "rejects_table")
    if bad is not None:
        return bad
    bad = _validate_identifier(rejects_scan, "rejects_scan")
    if bad is not None:
        return bad

    supported = tuple(dict.fromkeys(supported_formats or DUCKDB_LOAD_SUPPORTED_FORMATS))
    in_res = validate_input_file(
        input_path,
        input_format=input_format,
        supported_formats=supported,
        require_non_empty=require_non_empty_input,
    )
    if not in_res.success:
        return in_res

    duckdb_mod = _import_duckdb()
    if isinstance(duckdb_mod, SharedResult):
        return duckdb_mod

    inp = Path(in_res.data["path"])
    fmt = str(in_res.data["format"])
    try:
        input_size_bytes = int(inp.stat().st_size)
    except OSError as exc:
        return failure(
            f"Could not stat input file: {inp}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(inp),
        )
    input_size_human = human_readable_size(input_size_bytes)

    text_like_formats = frozenset({"csv", "json", "jsonl", "ndjson"})
    input_physical_lines: int | None = None
    input_physical_lines_note: str | None = None
    if fmt in text_like_formats and scan_input_physical_lines:
        input_physical_lines, input_physical_lines_note = _count_physical_newlines(inp, line_count_max_input_bytes)
        if input_physical_lines is None and input_physical_lines_note is None:
            input_physical_lines_note = "skipped"
    elif fmt in text_like_formats:
        input_physical_lines_note = "skipped (scan_input_physical_lines is false)"
    else:
        input_physical_lines_note = f"not scanned for format {fmt!r} (use rows_loaded for row count)"

    db_path = resolve_path(database_path)

    parent = ensure_parent_directory(db_path)
    if not parent.success:
        return parent

    limits = limits or DuckdbResourceLimits()
    use_csv_rejects = fmt == "csv"
    if use_csv_rejects:
        if rejects_csv_path is None:
            rejects_csv_path = db_path.parent / f"{inp.stem}_rejects.csv"
        rejects_csv_path = resolve_path(rejects_csv_path)
        rp_rej = ensure_parent_directory(rejects_csv_path)
        if not rp_rej.success:
            return rp_rej
    elif rejects_csv_path is not None:
        rejects_csv_path = resolve_path(rejects_csv_path)
        rp_rej = ensure_parent_directory(rejects_csv_path)
        if not rp_rej.success:
            return rp_rej

    conn = duckdb_mod.connect(str(db_path))
    try:
        _apply_resource_limits(conn, limits)

        if fmt == "xlsx":
            xl = _ensure_excel_extension(conn, duckdb_mod)
            if not xl.success:
                return xl

        try:
            relation = _read_relation_sql(
                inp,
                fmt,
                csv_header=csv_header,
                csv_delimiter=csv_delimiter,
                csv_max_line_size=csv_max_line_size,
                excel_sheet=excel_sheet,
                excel_header=excel_header,
                store_rejects=use_csv_rejects,
                rejects_table=rejects_table,
                rejects_scan=rejects_scan,
                rejects_limit=rejects_limit,
            )
        except ValueError as exc:
            return failure(str(exc), exit_code=EXIT_VALIDATION_ERROR)

        tq = _duck_ident(table_name)
        create_sql = f"CREATE OR REPLACE TABLE {tq} AS SELECT * FROM {relation}"
        conn.execute(create_sql)

        rows_loaded = int(conn.execute(f"SELECT COUNT(*) FROM {tq}").fetchone()[0])

        pragma_rows = conn.execute(f"PRAGMA table_info({tq})").fetchall()
        columns_loaded = len(pragma_rows)
        column_names = [str(row[1]) for row in pragma_rows]

        rejects_error_entries = 0
        rejects_distinct_lines = 0
        rejects_out: str | None = None
        notice_parts: list[str] = []

        if use_csv_rejects:
            rq = _duck_ident(rejects_table)
            rej = conn.execute(f"SELECT COUNT(*) FROM {rq}").fetchone()
            rejects_error_entries = int(rej[0]) if rej is not None else 0
            rej_lines = conn.execute(f"SELECT COUNT(DISTINCT line) FROM {rq}").fetchone()
            rejects_distinct_lines = int(rej_lines[0]) if rej_lines is not None else 0
            if rejects_csv_path is not None:
                rej_path = Path(rejects_csv_path)
                copy_sql = (
                    f"COPY (SELECT line, line_byte_position, column_name, error_type, "
                    f"csv_line, error_message FROM {rq}) "
                    f"TO {_sql_string(str(rej_path))} (FORMAT csv, HEADER true)"
                )
                conn.execute(copy_sql)
                rejects_out = str(rej_path.resolve())
            if rejects_error_entries:
                notice_parts.append(
                    f"{rejects_distinct_lines} CSV source line(s) had errors "
                    f"({rejects_error_entries} detail row(s) in rejects); see rejects CSV."
                )
            else:
                notice_parts.append("No CSV lines were rejected.")
        else:
            if rejects_csv_path is not None:
                rej_path = Path(rejects_csv_path)
                rej_path.write_text(
                    "format,line,note\n"
                    f"{fmt},,\n"
                    "Line-level rejects are only produced for CSV inputs with DuckDB store_rejects.\n",
                    encoding="utf-8",
                )
                rejects_out = str(rej_path.resolve())
                notice_parts.append(
                    f"Wrote a short note to {rejects_out}; line rejects apply to CSV only."
                )
            else:
                notice_parts.append("Line-level rejects CSV is only used for CSV inputs.")

        notice = " ".join(notice_parts)
        lines_part = (
            f"input physical lines (newline count)={input_physical_lines}"
            if input_physical_lines is not None
            else f"input physical lines=n/a ({input_physical_lines_note or 'unknown'})"
        )
        summary = (
            f"Input {input_size_human} ({input_size_bytes} bytes), {lines_part}, "
            f"columns_loaded={columns_loaded}, rows_loaded={rows_loaded}. {notice}"
        )
        return success(
            f"Loaded {rows_loaded} row(s) into {db_path} table {table_name}. {summary}",
            database_path=str(db_path),
            table_name=table_name,
            input_path=str(inp),
            format=fmt,
            input_size_bytes=input_size_bytes,
            input_size_human=input_size_human,
            input_physical_lines=input_physical_lines,
            input_physical_lines_note=input_physical_lines_note,
            columns_loaded=columns_loaded,
            column_names=column_names,
            rows_loaded=rows_loaded,
            rejects_distinct_lines=rejects_distinct_lines,
            rejects_error_entries=rejects_error_entries,
            rejects_csv_path=rejects_out,
            user_notice=notice,
        )
    except duckdb_mod.Error as exc:
        return failure(
            f"DuckDB error while loading: {exc}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            database_path=str(db_path),
            input_path=str(inp),
            format=fmt,
        )
    except OSError as exc:
        return failure(
            f"I/O error while loading: {exc}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            database_path=str(db_path),
            input_path=str(inp),
            format=fmt,
        )
    finally:
        conn.close()
