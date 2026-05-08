"""Parquet row-quality metrics and shared DuckDB predicates for blanks / merge keys.

**Reuse in other tools:** ``blankish_cell_sql`` / ``blankish_cell_sql_expr`` define which cells
count as empty (NULL, trim-empty, ``nan`` / ``none`` / ``null``). ``blank_rows_union_sql`` /
``blank_rows_union_sql_exprs`` and ``keep_row_if_not_blank_sql_exprs`` define the same blank-row
detection as data_checker (and file-cleaner ``--empty-rows``). ``normalized_merge_key_sql`` and
``dedupe_partition_expressions`` match join-horizontal / file-cleaner dedupe partitions.

``run_parquet_row_audit`` / ``write_parquet_row_audit_report`` / ``export_tagged_data_report_csv``
support the data-checker CLI.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence

from .duckdb_sql import sql_string_literal
from .path_utils import ensure_parent_directory, resolve_path
from .results import EXIT_ERROR, EXIT_IO_ERROR, EXIT_VALIDATION_ERROR, SharedResult, failure, success
from .validate_input import validate_input_file

SUPPORTED_INPUT = ("parquet",)


def _quote_ident(col: str) -> str:
    return '"' + str(col).replace('"', '""') + '"'


def blankish_cell_sql_expr(expr: str) -> str:
    """SQL predicate: ``expr`` is NULL, blank after trim, or null-like tokens ``nan`` / ``none`` / ``null``."""
    e = str(expr).strip()
    return (
        f"({e} IS NULL OR trim(CAST({e} AS VARCHAR)) = '' OR "
        f"lower(trim(CAST({e} AS VARCHAR))) IN ('nan','none','null'))"
    )


def blankish_cell_sql(col: str) -> str:
    """SQL predicate for a bare column name (double-quoted identifier in DuckDB)."""
    return blankish_cell_sql_expr(_quote_ident(col))


def normalized_merge_key_sql(column_name: str, *, table_alias: str | None = None) -> str:
    """
    DuckDB expression for join / dedupe key comparison: ``COALESCE(trim(CAST(col AS VARCHAR)), '')``.

    Pass ``table_alias`` (e.g. ``\"z\"``) when the column must be qualified in SQL.
    """
    q = _quote_ident(column_name)
    prefix = f"{table_alias}." if table_alias else ""
    return f"COALESCE(trim(CAST({prefix}{q} AS VARCHAR)), '')"


def _all_columns_null_sql_exprs(exprs: Sequence[str]) -> str:
    if not exprs:
        return "TRUE"
    return " AND ".join(f"(({e}) IS NULL)" for e in exprs)


def _all_columns_blankish_sql_exprs(exprs: Sequence[str]) -> str:
    if not exprs:
        return "TRUE"
    return " AND ".join(f"({blankish_cell_sql_expr(e)})" for e in exprs)


def _whitespace_only_row_sql_exprs(exprs: Sequence[str]) -> str:
    """Row where every expr is NULL or trim-empty, and at least one expr is non-NULL (e.g. space-only)."""
    if not exprs:
        return "FALSE"
    all_ws = " AND ".join(
        f"(({e}) IS NULL OR trim(CAST(({e}) AS VARCHAR)) = '')" for e in exprs
    )
    any_nonnull = " OR ".join(f"(({e}) IS NOT NULL)" for e in exprs)
    return f"(({all_ws}) AND ({any_nonnull}))"


def _distinct_star_sql(table: str) -> str:
    return f"SELECT COUNT(*) FROM (SELECT DISTINCT * FROM {table})"


def _distinct_keys_sql(table: str, keys: Sequence[str]) -> str:
    sel = ", ".join(normalized_merge_key_sql(k) for k in keys)
    return f"SELECT COUNT(*) FROM (SELECT DISTINCT {sel} FROM {table})"


def blank_rows_union_sql_exprs(column_sql_exprs: Sequence[str]) -> str:
    """
    SQL predicate (balanced parens): true when the row matches the same blank definition as
    ``run_parquet_row_audit`` / data_checker (all-NULL, all-blankish, or whitespace-only), evaluated
    on the given per-column SQL expressions (e.g. ``TRY_CAST(\"a\" AS BIGINT)`` for typed checks).
    """
    if not column_sql_exprs:
        return "FALSE"
    return (
        f"(({_all_columns_null_sql_exprs(column_sql_exprs)}) OR ({_all_columns_blankish_sql_exprs(column_sql_exprs)}) "
        f"OR ({_whitespace_only_row_sql_exprs(column_sql_exprs)}))"
    )


def blank_rows_union_sql(columns: Sequence[str]) -> str:
    """Same as ``blank_rows_union_sql_exprs`` but each entry is a bare column name (quoted id)."""
    if not columns:
        return "FALSE"
    return blank_rows_union_sql_exprs([_quote_ident(c) for c in columns])


def keep_row_if_not_blank_sql_exprs(column_sql_exprs: Sequence[str]) -> str:
    """SQL expression that is true for rows to **keep** when dropping blank rows (negation of blank union)."""
    if not column_sql_exprs:
        return "TRUE"
    return f"NOT ({blank_rows_union_sql_exprs(column_sql_exprs)})"


def dedupe_partition_expressions(
    column_names: Sequence[str],
    *,
    table_alias: str | None = None,
) -> str:
    """Comma-separated ``normalized_merge_key_sql`` expressions for ``PARTITION BY``."""
    return ", ".join(normalized_merge_key_sql(c, table_alias=table_alias) for c in column_names)


def run_parquet_row_audit(
    input_path: str | Path,
    *,
    key_columns: Optional[Sequence[str]] = None,
) -> SharedResult:
    """
    Scan one Parquet file and return row counts.

    **blank_rows:** rows that are all-NULL, *or* every column blankish (NULL / trim-empty /
    ``nan`` / ``none`` / ``null``), *or* the whitespace-only pattern (all cells empty-looking with
    at least one non-NULL cell). Each row is counted at most once.

    **duplicate_rows_on_keys** (when ``key_columns`` is set): ``row_count - unique_key_count`` —
    how many rows would be removed if you kept one row per distinct *normalized* composite key
    (same trim + coalesce rule as join-horizontal dedupe). Not extra duplicate *groups*; it is the
    surplus *row* count beyond unique keys.
    """
    path = resolve_path(input_path)
    vr = validate_input_file(
        path,
        input_format=None,
        supported_formats=SUPPORTED_INPUT,
        require_non_empty=False,
    )
    if not vr.success:
        return vr
    if str(vr.data.get("format") or "") != "parquet":
        return failure(
            f"Only Parquet input is supported; got format {vr.data.get('format')!r}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
        )
    if not path.is_file():
        return failure(f"Input not found: {path}", exit_code=EXIT_NOT_FOUND, path=str(path))

    try:
        import duckdb
    except ImportError as exc:
        return failure(
            "duckdb package is required for parquet row audit.",
            exit_code=EXIT_ERROR,
            error=str(exc),
        )

    keys = [str(k).strip() for k in (key_columns or []) if str(k).strip()]
    lit = sql_string_literal(str(path))

    try:
        con = duckdb.connect(database=":memory:")
        con.execute("PRAGMA threads=4")
        base = "src_audit"
        con.execute(f"CREATE OR REPLACE VIEW {base} AS SELECT * FROM read_parquet({lit})")
        cols = [r[0] for r in con.execute(f"DESCRIBE {base}").fetchall()]
        if not cols:
            con.close()
            return failure("Parquet has no columns.", exit_code=EXIT_VALIDATION_ERROR, path=str(path))

        unknown = [k for k in keys if k not in cols]
        if unknown:
            con.close()
            return failure(
                f"Unknown key column(s): {unknown}. Available: {cols[:40]}{' …' if len(cols) > 40 else ''}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(path),
            )

        row_count = int(con.execute(f"SELECT COUNT(*) FROM {base}").fetchone()[0])
        blank_rows = int(
            con.execute(f"SELECT COUNT(*) FROM {base} WHERE {blank_rows_union_sql(cols)}").fetchone()[0]
        )
        full_row_distinct = int(con.execute(_distinct_star_sql(base)).fetchone()[0])
        full_dup_surplus = max(0, row_count - full_row_distinct)

        unique_key_count: Optional[int] = None
        duplicate_rows_on_keys: Optional[int] = None
        if keys:
            unique_key_count = int(con.execute(_distinct_keys_sql(base, keys)).fetchone()[0])
            duplicate_rows_on_keys = max(0, row_count - unique_key_count)

        con.close()
    except Exception as exc:  # noqa: BLE001
        return failure(
            f"Row audit failed: {exc}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(path),
        )

    payload: dict[str, Any] = {
        "input_path": str(path),
        "column_count": len(cols),
        "row_count": row_count,
        "blank_rows": blank_rows,
        "full_row_distinct_count": full_row_distinct,
        "full_row_duplicate_surplus_rows": full_dup_surplus,
        "key_columns": keys if keys else None,
        "unique_key_count": unique_key_count,
        "duplicate_rows_on_keys": duplicate_rows_on_keys,
    }
    return success("Parquet row audit completed.", **payload)


def write_parquet_row_audit_report(
    report_row: Mapping[str, Any],
    output_path: str | Path,
    *,
    output_format: Literal["csv", "parquet"],
) -> SharedResult:
    """Write a single-row audit report to CSV or Parquet (flat string values for list fields)."""
    fmt = str(output_format).lower().strip().lstrip(".")
    if fmt not in {"csv", "parquet"}:
        return failure(
            f"Unsupported report format: {output_format!r} (use csv or parquet).",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    target = resolve_path(output_path)
    parent = ensure_parent_directory(target)
    if not parent.success:
        return parent

    flat: dict[str, Any] = {}
    for k, v in report_row.items():
        if k == "key_columns" and v is None:
            flat[k] = ""
        elif k == "key_columns" and isinstance(v, list):
            flat[k] = ",".join(str(x) for x in v)
        else:
            flat[k] = v

    try:
        if fmt == "csv":
            with target.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(flat.keys()))
                w.writeheader()
                w.writerow({k: ("" if flat[k] is None else flat[k]) for k in flat})
        else:
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
            except ImportError as exc:
                return failure(
                    "pyarrow is required to write Parquet audit reports.",
                    exit_code=EXIT_ERROR,
                    error=str(exc),
                )
            row_out: dict[str, Any] = {k: ("" if v is None else v) for k, v in flat.items()}
            table = pa.Table.from_pylist([row_out])
            pq.write_table(table, str(target))
    except OSError as exc:
        return failure(str(exc), exit_code=EXIT_IO_ERROR, error=str(exc), path=str(target))
    except Exception as exc:  # noqa: BLE001
        return failure(
            f"Could not write report: {exc}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(target),
        )

    return success(f"Wrote row audit report: {target}", path=str(target), format=fmt)


def export_tagged_data_report_csv(
    input_path: str | Path,
    output_path: str | Path,
    *,
    key_columns: Optional[Sequence[str]] = None,
) -> SharedResult:
    """
    Write a CSV with ``__tag__`` (``blank`` / ``duplicate`` / empty), ``__key__`` (``-k`` columns or
    all columns, values joined by ``|``), then all original columns. Duplicate tagging uses the same
    normalized partition as ``normalized_merge_key_sql`` (``-k`` if set, else every column).
    """
    path = resolve_path(input_path)
    vr = validate_input_file(
        path,
        input_format=None,
        supported_formats=SUPPORTED_INPUT,
        require_non_empty=False,
    )
    if not vr.success:
        return vr
    if str(vr.data.get("format") or "") != "parquet":
        return failure(
            f"Only Parquet input is supported; got format {vr.data.get('format')!r}",
            exit_code=EXIT_VALIDATION_ERROR,
            path=str(path),
        )
    if not path.is_file():
        return failure(f"Input not found: {path}", exit_code=EXIT_NOT_FOUND, path=str(path))

    target = resolve_path(output_path)
    parent = ensure_parent_directory(target)
    if not parent.success:
        return parent

    keys = [str(k).strip() for k in (key_columns or []) if str(k).strip()]
    lit = sql_string_literal(str(path))
    out_lit = sql_string_literal(str(target))

    try:
        import duckdb
    except ImportError as exc:
        return failure(
            "duckdb package is required for tagged data export.",
            exit_code=EXIT_ERROR,
            error=str(exc),
        )

    try:
        con = duckdb.connect(database=":memory:")
        con.execute("PRAGMA threads=4")
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({lit})").fetchall()]
        if not cols:
            con.close()
            return failure("Parquet has no columns.", exit_code=EXIT_VALIDATION_ERROR, path=str(path))

        unknown = [k for k in keys if k not in cols]
        if unknown:
            con.close()
            return failure(
                f"Unknown key column(s): {unknown}. Available: {cols[:40]}{' …' if len(cols) > 40 else ''}",
                exit_code=EXIT_VALIDATION_ERROR,
                path=str(path),
            )

        part_cols = keys if keys else cols
        part_exprs = ", ".join(normalized_merge_key_sql(c) for c in part_cols)
        key_concat_cols = keys if keys else cols
        key_concat = "concat_ws('|', " + ", ".join(
            f"COALESCE(trim(CAST({_quote_ident(c)} AS VARCHAR)), '')" for c in key_concat_cols
        ) + ")"
        blank_sql = blank_rows_union_sql(cols)
        data_cols = ", ".join(_quote_ident(c) for c in cols)

        sql = f"""
        COPY (
            WITH src AS (
                SELECT *, row_number() OVER () AS __ord FROM read_parquet({lit})
            ),
            m AS (
                SELECT
                    src.*,
                    {key_concat} AS __key__,
                    row_number() OVER (PARTITION BY {part_exprs} ORDER BY __ord) AS __dup_rn
                FROM src
            )
            SELECT
                CASE
                    WHEN ({blank_sql}) THEN 'blank'
                    WHEN __dup_rn > 1 THEN 'duplicate'
                    ELSE ''
                END AS __tag__,
                __key__,
                {data_cols}
            FROM m
        ) TO {out_lit} (FORMAT CSV, HEADER true, DELIMITER ',', QUOTE '"')
        """
        con.execute(sql)
        con.close()
    except Exception as exc:  # noqa: BLE001
        return failure(
            f"Tagged data export failed: {exc}",
            exit_code=EXIT_IO_ERROR,
            error=str(exc),
            path=str(target),
        )

    return success(f"Wrote tagged data report: {target}", path=str(target), format="csv")
