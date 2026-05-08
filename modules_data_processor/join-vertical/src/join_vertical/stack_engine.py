"""
Vertically stack multiple Parquet files with DuckDB (schema alignment, x_source_file / x_id).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

from shared_lib.duckdb_sql import sql_double_quoted_ident as quote_ident
from shared_lib.duckdb_sql import sql_string_literal as quote_literal

TRACKING_COLUMNS = {"x_id", "x_source_file"}
INTERNAL_ORDER_COLUMNS = ["__load_file_idx", "__load_row_idx"]


@dataclass
class SourceSpec:
    file_path: Path
    source_id: str


@dataclass
class SourceLoadStats:
    source_id: str
    status: str
    rows_loaded: int
    columns_loaded: int
    message: str = ""


def normalize_header(name: str, fallback_idx: int) -> str:
    cleaned = re.sub(r"[^\x20-\x7E]+", "", str(name))
    normalized = re.sub(r"\W+", "_", cleaned.lower().strip()).strip("_")
    return normalized or f"col_{fallback_idx}"


def uniquify_headers(headers: Sequence[str]) -> List[str]:
    used: Dict[str, int] = {}
    result: List[str] = []
    for idx, original in enumerate(headers, start=1):
        base = normalize_header(original, idx)
        count = used.get(base, 0)
        candidate = base if count == 0 else f"{base}_{count + 1}"
        used[base] = count + 1

        while candidate in TRACKING_COLUMNS or candidate in INTERNAL_ORDER_COLUMNS:
            used[base] += 1
            candidate = f"{base}_{used[base]}"
        result.append(candidate)
    return result


def is_text_type(dtype: str) -> bool:
    upper = dtype.upper()
    return "CHAR" in upper or "STRING" in upper or upper in {"VARCHAR", "TEXT"}


def normalize_dtype(dtype: str) -> str:
    return dtype.upper().strip()


def parquet_relation_sql(file_path: Path) -> str:
    return f"read_parquet({quote_literal(str(file_path))})"


def discover_parquet_sources(paths: list[Path]) -> List[SourceSpec]:
    return [
        SourceSpec(file_path=p, source_id=p.name)
        for p in paths
        if p.suffix.lower() in {".parquet", ".pq"}
    ]


def get_relation_columns(conn: Any, relation_sql: str) -> List[str]:
    rows = conn.execute(f"DESCRIBE SELECT * FROM {relation_sql} LIMIT 0").fetchall()
    return [row[0] for row in rows]


def table_schema(conn: Any, table_name: str) -> List[tuple[str, str]]:
    rows = conn.execute(f"PRAGMA table_info({quote_literal(table_name)})").fetchall()
    return [(row[1], row[2]) for row in rows]


def create_normalized_select_sql(
    relation_sql: str,
    source_id: str,
    file_idx: int,
    normalized_columns: Sequence[tuple[str, str]],
    type_mode: str,
) -> str:
    select_parts: List[str] = []
    for original, normalized in normalized_columns:
        if type_mode == "string":
            select_parts.append(f"CAST({quote_ident(original)} AS VARCHAR) AS {quote_ident(normalized)}")
        else:
            select_parts.append(f"{quote_ident(original)} AS {quote_ident(normalized)}")

    select_parts.append(f"{quote_literal(source_id)} AS {quote_ident('x_source_file')}")
    select_parts.append(f"{file_idx}::BIGINT AS {quote_ident('__load_file_idx')}")
    select_parts.append("ROW_NUMBER() OVER ()::BIGINT AS " + quote_ident("__load_row_idx"))
    return f"SELECT {', '.join(select_parts)} FROM {relation_sql}"


def reconcile_target_schema(conn: Any, target_table: str, incoming_schema: Dict[str, str]) -> Dict[str, str]:
    current_schema = dict(table_schema(conn, target_table))

    for col, incoming_dtype in incoming_schema.items():
        if col not in current_schema:
            conn.execute(
                f"ALTER TABLE {quote_ident(target_table)} ADD COLUMN {quote_ident(col)} {incoming_dtype}"
            )
            current_schema[col] = incoming_dtype
            continue

        current_dtype = current_schema[col]
        if normalize_dtype(current_dtype) != normalize_dtype(incoming_dtype):
            if normalize_dtype(current_dtype) != "VARCHAR":
                conn.execute(
                    f"ALTER TABLE {quote_ident(target_table)} ALTER COLUMN {quote_ident(col)} "
                    f"TYPE VARCHAR USING CAST({quote_ident(col)} AS VARCHAR)"
                )
                current_schema[col] = "VARCHAR"

    return dict(table_schema(conn, target_table))


def insert_from_temp(conn: Any, temp_table: str, target_table: str) -> None:
    temp_schema = dict(table_schema(conn, temp_table))
    target_schema_items = table_schema(conn, target_table)
    target_schema = dict(target_schema_items)

    select_parts: List[str] = []
    for col, target_dtype in target_schema_items:
        if col not in temp_schema:
            select_parts.append(f"NULL::{target_dtype} AS {quote_ident(col)}")
            continue

        temp_dtype = temp_schema[col]
        if normalize_dtype(temp_dtype) == normalize_dtype(target_dtype):
            select_parts.append(quote_ident(col))
        else:
            select_parts.append(
                f"TRY_CAST({quote_ident(col)} AS {target_schema[col]}) AS {quote_ident(col)}"
            )

    conn.execute(
        f"INSERT INTO {quote_ident(target_table)} "
        f"SELECT {', '.join(select_parts)} FROM {quote_ident(temp_table)}"
    )


def remove_blank_rows(conn: Any, table_name: str) -> None:
    schema = table_schema(conn, table_name)
    data_cols = [
        (name, dtype)
        for name, dtype in schema
        if name not in TRACKING_COLUMNS and name not in set(INTERNAL_ORDER_COLUMNS)
    ]
    if not data_cols:
        return

    predicates: List[str] = []
    for name, dtype in data_cols:
        if is_text_type(dtype):
            predicates.append(f"COALESCE(TRIM(CAST({quote_ident(name)} AS VARCHAR)), '') = ''")
        else:
            predicates.append(f"{quote_ident(name)} IS NULL")

    conn.execute(f"DELETE FROM {quote_ident(table_name)} WHERE {' AND '.join(predicates)}")


def apply_distinct(conn: Any, table_name: str) -> None:
    schema = table_schema(conn, table_name)
    data_cols = [
        name
        for name, _ in schema
        if name not in TRACKING_COLUMNS and name not in set(INTERNAL_ORDER_COLUMNS)
    ]
    if not data_cols:
        return
    partition = ", ".join(quote_ident(c) for c in data_cols)

    conn.execute(
        f"""
        CREATE OR REPLACE TABLE {quote_ident(table_name)} AS
        SELECT * EXCLUDE (rn)
        FROM (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY {partition}
                    ORDER BY {quote_ident('__load_file_idx')}, {quote_ident('__load_row_idx')}
                ) AS rn
            FROM {quote_ident(table_name)}
        )
        WHERE rn = 1
        """
    )


def finalize_table(conn: Any, table_name: str, fill_missing: str, type_mode: str) -> None:
    schema = table_schema(conn, table_name)
    data_cols = [
        (name, dtype)
        for name, dtype in schema
        if name not in TRACKING_COLUMNS and name not in set(INTERNAL_ORDER_COLUMNS)
    ]

    select_parts = [
        "ROW_NUMBER() OVER (ORDER BY "
        + f"{quote_ident('__load_file_idx')}, {quote_ident('__load_row_idx')}"
        + ") AS "
        + quote_ident("x_id"),
        quote_ident("x_source_file"),
    ]

    for name, dtype in data_cols:
        if type_mode == "string" or is_text_type(dtype):
            select_parts.append(
                f"COALESCE(CAST({quote_ident(name)} AS VARCHAR), {quote_literal(fill_missing)}) "
                f"AS {quote_ident(name)}"
            )
        else:
            select_parts.append(f"{quote_ident(name)}")

    conn.execute(
        f"""
        CREATE OR REPLACE TABLE {quote_ident(table_name)} AS
        SELECT {', '.join(select_parts)}
        FROM {quote_ident(table_name)}
        ORDER BY {quote_ident('__load_file_idx')}, {quote_ident('__load_row_idx')}
        """
    )


def write_output(
    conn: Any,
    table_name: str,
    out_path: Path,
    output_format: str,
    output_csv_delim: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = quote_literal(str(out_path))

    if output_format == "parquet":
        conn.execute(f"COPY {quote_ident(table_name)} TO {out} (FORMAT PARQUET, COMPRESSION ZSTD)")
        return

    if output_format == "csv":
        conn.execute(
            f"COPY {quote_ident(table_name)} TO {out} "
            f"(FORMAT CSV, HEADER TRUE, DELIMITER {quote_literal(output_csv_delim)})"
        )
        return

    if output_format == "xlsx":
        try:
            import polars as pl
        except ImportError as exc:
            raise ImportError("polars is required for XLSX output.") from exc
        total_rows = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(table_name)}").fetchone()[0]
        if total_rows > 1_000_000:
            print("Warning: Excel output may exceed row limits (~1,048,576 rows).", flush=True)
        arrow_tbl = conn.execute(f"SELECT * FROM {quote_ident(table_name)}").arrow()
        pl.from_arrow(arrow_tbl).write_excel(str(out_path))
        return

    raise ValueError(f"Unsupported output format: {output_format}")


def load_source_into_temp(
    conn: Any,
    source: SourceSpec,
    file_idx: int,
    *,
    type_mode: str,
) -> tuple[str, int, int]:
    temp_table = f"tmp_source_{file_idx}"
    relation_sql = parquet_relation_sql(source.file_path)

    source_cols = get_relation_columns(conn, relation_sql)
    if not source_cols:
        conn.execute(f"CREATE OR REPLACE TEMP TABLE {quote_ident(temp_table)} AS SELECT 1 WHERE FALSE")
        return temp_table, 0, 0

    normalized = uniquify_headers(source_cols)
    normalized_pairs = list(zip(source_cols, normalized))
    select_sql = create_normalized_select_sql(
        relation_sql=relation_sql,
        source_id=source.source_id,
        file_idx=file_idx,
        normalized_columns=normalized_pairs,
        type_mode=type_mode,
    )

    conn.execute(f"CREATE OR REPLACE TEMP TABLE {quote_ident(temp_table)} AS {select_sql}")
    rows = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(temp_table)}").fetchone()[0]
    cols = len(table_schema(conn, temp_table))
    return temp_table, int(rows or 0), cols


def execute_vertical_stack(
    *,
    input_paths: list[Path],
    output_path: Path,
    fill_missing: str,
    union_type: str,
    on_error: str,
    type_mode: str,
    output_csv_delim: str,
    output_format: str,
    threads: int | None,
) -> dict[str, Any]:
    """
    Stack Parquet files vertically. ``on_error`` is ``fail-fast`` or ``skip-file``.
    ``union_type`` is ``all`` or ``distinct``.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "duckdb is required. Run with Pixi from this package or: pip install duckdb"
        ) from exc

    sources = discover_parquet_sources(input_paths)
    if not sources:
        raise ValueError("No Parquet sources in input list.")

    conn = duckdb.connect(database=":memory:")
    default_threads = max(1, (os.cpu_count() or 4) - 1)
    n_threads = int(threads) if threads is not None and threads > 0 else default_threads
    conn.execute(f"PRAGMA threads={n_threads}")

    source_stats: List[SourceLoadStats] = []
    target_table = "stacked_data"
    first_insert = True

    try:
        for file_idx, source in enumerate(sources, start=1):
            try:
                temp_table, rows, cols = load_source_into_temp(
                    conn, source, file_idx, type_mode=type_mode
                )
                if rows == 0:
                    source_stats.append(
                        SourceLoadStats(
                            source_id=source.source_id,
                            status="skipped",
                            rows_loaded=0,
                            columns_loaded=0,
                            message="empty source",
                        )
                    )
                    conn.execute(f"DROP TABLE IF EXISTS {quote_ident(temp_table)}")
                    continue

                if first_insert:
                    conn.execute(
                        f"CREATE TABLE {quote_ident(target_table)} AS "
                        f"SELECT * FROM {quote_ident(temp_table)}"
                    )
                    first_insert = False
                else:
                    incoming_schema = dict(table_schema(conn, temp_table))
                    reconcile_target_schema(conn, target_table, incoming_schema)
                    insert_from_temp(conn, temp_table, target_table)

                source_stats.append(
                    SourceLoadStats(
                        source_id=source.source_id,
                        status="loaded",
                        rows_loaded=rows,
                        columns_loaded=cols,
                    )
                )
                conn.execute(f"DROP TABLE IF EXISTS {quote_ident(temp_table)}")
            except Exception as exc:  # noqa: BLE001
                source_stats.append(
                    SourceLoadStats(
                        source_id=source.source_id,
                        status="failed",
                        rows_loaded=0,
                        columns_loaded=0,
                        message=str(exc),
                    )
                )
                if on_error == "fail-fast":
                    raise
        if first_insert:
            raise ValueError("No rows were loaded from provided inputs.")

        remove_blank_rows(conn, target_table)
        if union_type == "distinct":
            apply_distinct(conn, target_table)
        finalize_table(conn, target_table, fill_missing, type_mode)
        write_output(conn, target_table, output_path, output_format, output_csv_delim)

        total_rows = int(conn.execute(f"SELECT COUNT(*) FROM {quote_ident(target_table)}").fetchone()[0] or 0)
        col_count = int(
            conn.execute(f"SELECT COUNT(*) FROM pragma_table_info({quote_literal(target_table)})").fetchone()[0]
            or 0
        )
        loaded = sum(1 for s in source_stats if s.status == "loaded")
        skipped = sum(1 for s in source_stats if s.status == "skipped")
        failed = sum(1 for s in source_stats if s.status == "failed")
        return {
            "output_path": str(output_path.resolve()),
            "total_rows": total_rows,
            "column_count": col_count,
            "sources_loaded": loaded,
            "sources_skipped": skipped,
            "sources_failed": failed,
            "source_stats": [
                {
                    "source_id": s.source_id,
                    "status": s.status,
                    "rows_loaded": s.rows_loaded,
                    "columns_loaded": s.columns_loaded,
                    "message": s.message,
                }
                for s in source_stats
            ],
            "duckdb_threads": n_threads,
        }
    finally:
        conn.close()
