"""Horizontal join engine (DuckDB). Left/right inputs must be Parquet."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

import duckdb
import polars as pl
import pyarrow as pa
import xlsxwriter

from shared_lib import ensure_parent_directory
from shared_lib.parquet_row_audit import (
    blankish_cell_sql,
    blankish_cell_sql_expr,
    dedupe_partition_expressions,
    normalized_merge_key_sql,
)
from shared_lib.naming import apply_output_format_to_path
from shared_lib.time_utils import timestamp_compact_microseconds


def _apply_format(path: str, output_format: Optional[str]) -> str:
    if not output_format:
        return path
    fmt = str(output_format).lower().strip()
    r = apply_output_format_to_path(Path(path), fmt)
    if not r.success:
        raise ValueError(r.message or "apply_output_format_to_path failed")
    return str(r.data["path"])


def _ensure_parent(path: str) -> None:
    r = ensure_parent_directory(Path(path))
    if not r.success:
        raise RuntimeError(r.message or "ensure_parent_directory failed")


def _validate_parquet_path(path: str, label: str) -> None:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    if p.suffix.lower() not in {".parquet", ".pq"}:
        raise ValueError(f"{label} must be Parquet (.parquet / .pq); got {p.suffix!r}")


ID_COL = "row_id"
XLSX_MAX_ROWS = 1_048_576
DEBUG_MODE = False
RUNTIME_LOGGER: Optional[logging.Logger] = None
DEFAULT_OUTPUT_FILE_NAME = "join_horizontal"
KEY_STAGE_PREFIXES = (
    "Starting join pipeline",
    "Inputs ready:",
    "resolved_output_file=",
    "wrote_result=",
    "wrote_stats=",
    "wrote_input_state=",
    "wrote_input_state_left=",
    "wrote_input_state_right=",
    "Completed in ",
)


class JoinPipelineError(Exception):
    """Base exception for join pipeline failures."""


class JoinTimeoutError(JoinPipelineError):
    """Raised when runtime exceeds configured max_runtime_seconds."""


class JoinCancelledError(JoinPipelineError):
    """Raised when caller cancellation callback signals stop."""


@dataclass
class JoinErrorModel:
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class JoinApiResponse:
    ok: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[JoinErrorModel] = None


def log_stage(message: str):
    if not DEBUG_MODE and not any(message.startswith(p) for p in KEY_STAGE_PREFIXES):
        return
    if RUNTIME_LOGGER is not None:
        RUNTIME_LOGGER.info(message)
    else:
        print(f"[join] {message}", flush=True)


def log_debug(message: str):
    if DEBUG_MODE:
        if RUNTIME_LOGGER is not None:
            RUNTIME_LOGGER.debug(message)
        else:
            print(f"[join][debug] {message}", flush=True)


def _check_runtime_guard(
    *,
    t0: float,
    stage: str,
    max_runtime_seconds: Optional[float],
    cancel_check: Optional[Callable[[], bool]],
) -> None:
    if cancel_check is not None and cancel_check():
        raise JoinCancelledError(f"Join cancelled by caller at stage: {stage}")
    if max_runtime_seconds is not None:
        elapsed = time.perf_counter() - t0
        if elapsed > max_runtime_seconds:
            raise JoinTimeoutError(
                f"Join exceeded max runtime ({max_runtime_seconds}s) at stage: {stage} "
                f"(elapsed={elapsed:.3f}s)"
            )


def clean_col_name(col_name: str) -> str:
    return re.sub(r"\s+", " ", str(col_name)).strip()


def safe_name(name: str, max_len: int = 31) -> str:
    return str(name)[:max_len]


def quote_ident(col: str) -> str:
    return '"' + str(col).replace('"', '""') + '"'


def quote_literal(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def build_key_expr(alias: str, merge_on: Sequence[str]) -> str:
    parts = [f"COALESCE(CAST({alias}.{quote_ident(c)} AS VARCHAR), '')" for c in merge_on]
    if len(parts) == 1:
        return parts[0]
    return f"CONCAT_WS('|', {', '.join(parts)})"


def normalized_key_component_expr(alias: str, col: str) -> str:
    """Join keys match on trimmed string form (shared_lib ``normalized_merge_key_sql``)."""
    return normalized_merge_key_sql(col, table_alias=alias)


def normalize_columns(cols: Sequence[str]) -> List[str]:
    cleaned = [clean_col_name(c) for c in cols]
    if "zxz_id" in cleaned and ID_COL not in cleaned:
        cleaned = [ID_COL if c == "zxz_id" else c for c in cleaned]
    seen: Dict[str, int] = {}
    out: List[str] = []
    for c in cleaned:
        if c not in seen:
            seen[c] = 0
            out.append(c)
            continue
        seen[c] += 1
        out.append(f"{c}_{seen[c]}")
    return out


def blankish_expr(col: str) -> str:
    """Same as ``shared_lib.parquet_row_audit.blankish_cell_sql`` (predicate without extra wrapping)."""
    return blankish_cell_sql(col)


def blankish_expr_qualified(alias: str, col: str) -> str:
    return blankish_cell_sql_expr(f"{alias}.{quote_ident(col)}")


def ensure_arrow_table(tbl) -> pa.Table:
    if isinstance(tbl, pa.Table):
        return tbl
    if isinstance(tbl, pa.RecordBatchReader):
        return tbl.read_all()
    if isinstance(tbl, pa.RecordBatch):
        return pa.Table.from_batches([tbl])
    raise TypeError(f"Expected pyarrow Table/RecordBatchReader/RecordBatch, got {type(tbl)}")


@dataclass
class SheetResult:
    name: str
    input_rows: int
    excluded_rows: int
    processed_rows: int
    duplicate_key_detected_rows: int
    duplicate_key_excluded_rows: int
    clean_table: str
    state_table: str
    data_columns: List[str]


def load_table_to_con(
    con: duckdb.DuckDBPyConnection,
    file_path: str,
    table_name: str,
    sheet_name: Optional[str] = None,
) -> List[str]:
    del sheet_name  # Parquet-only CLI; kept for API compatibility with process_sheet.
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in {".parquet", ".pq"}:
        raise ValueError(f"Only Parquet input is supported; got {ext!r} for {file_path}")
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {table_name} AS "
        f"SELECT * FROM parquet_scan({quote_literal(file_path)})"
    )

    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table_name})").fetchall()]
    normalized = normalize_columns(cols)
    select_cols = ", ".join(
        f"{quote_ident(c_old)} AS {quote_ident(c_new)}" for c_old, c_new in zip(cols, normalized)
    )
    con.execute(f"CREATE OR REPLACE TEMP TABLE {table_name} AS SELECT {select_cols} FROM {table_name}")
    return normalized


def process_sheet(
    con: duckdb.DuckDBPyConnection,
    src_table: str,
    sheet_name: str,
    merge_on: List[str],
    *,
    dedupe_duplicate_keys: bool = False,
) -> SheetResult:
    """Prepare rows for join: drop all-blank rows and rows with blank merge keys only.

    When ``dedupe_duplicate_keys`` is true, rows that share the same join key (after the same
    normalization as the join) are de-duplicated: one row per key is kept (lowest ``__id__``).
    """
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({src_table})").fetchall()]
    for m in merge_on:
        if m not in cols:
            raise KeyError(f"Sheet '{sheet_name}' missing merge key column: {m}")

    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {src_table}_base AS "
        f"SELECT row_number() OVER () AS __ord, * FROM {src_table}"
    )
    input_rows = con.execute(f"SELECT COUNT(*) FROM {src_table}_base").fetchone()[0]

    # Parenthesize each per-column expression to avoid OR/AND precedence bugs.
    blank_all = " AND ".join(blankish_expr(c) for c in cols)
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {src_table}_blank AS "
        f"SELECT * FROM {src_table}_base WHERE {blank_all}"
    )
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {src_table}_no_blank AS "
        f"SELECT * FROM {src_table}_base WHERE NOT ({blank_all})"
    )

    invalid_key = " AND ".join(blankish_expr(c) for c in merge_on)
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {src_table}_invalid_keys AS "
        f"SELECT * FROM {src_table}_no_blank WHERE {invalid_key}"
    )
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {src_table}_valid_keys AS "
        f"SELECT * FROM {src_table}_no_blank WHERE NOT ({invalid_key})"
    )

    con.execute(
        f"CREATE OR REPLACE TEMP TABLE {src_table}_clean AS SELECT * FROM {src_table}_valid_keys"
    )

    key_expr = build_key_expr("b", merge_on)
    invalid_key_expr = " AND ".join(blankish_expr_qualified("b", c) for c in merge_on)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {src_table}_state AS
        WITH state_rows AS (
            SELECT
                CASE
                    WHEN bnk.__ord IS NOT NULL OR inv.__ord IS NOT NULL OR ({invalid_key_expr})
                        THEN 'exclude'
                    ELSE 'process'
                END AS "__state__",
                CASE
                    WHEN bnk.__ord IS NOT NULL THEN 'full blank row'
                    WHEN inv.__ord IS NOT NULL OR ({invalid_key_expr}) THEN 'invalid key'
                    ELSE 'process'
                END AS "__reason__",
                {key_expr} AS "__key__",
                b.__ord AS "__ord",
                b.* EXCLUDE (__ord)
            FROM {src_table}_base b
            LEFT JOIN (SELECT DISTINCT __ord FROM {src_table}_blank) bnk USING (__ord)
            LEFT JOIN (SELECT DISTINCT __ord FROM {src_table}_invalid_keys) inv USING (__ord)
        )
        SELECT
            "__state__",
            "__reason__",
            row_number() OVER (ORDER BY "__state__" DESC, "__key__" ASC, "__ord" ASC) AS "__id__",
            "__key__",
            * EXCLUDE ("__state__", "__reason__", "__key__", "__ord")
        FROM state_rows
        ORDER BY "__state__" DESC, "__key__" ASC, "__ord" ASC
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE {src_table}_processing AS
        SELECT * EXCLUDE ("__state__", "__reason__", "__key__")
        FROM {src_table}_state
        WHERE "__state__" = 'process'
        """
    )

    processed_rows = con.execute(f"SELECT COUNT(*) FROM {src_table}_processing").fetchone()[0]
    excluded_rows = input_rows - processed_rows

    dup_detected = 0
    dup_excluded = 0
    processing_tbl = f"{src_table}_processing"
    if dedupe_duplicate_keys and merge_on:
        proc_before = int(processed_rows)
        part_keys = dedupe_partition_expressions(merge_on, table_alias="z")
        dup_detected = int(
            con.execute(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT 1 AS one
                    FROM {processing_tbl} AS z
                    GROUP BY {part_keys}
                    HAVING COUNT(*) > 1
                ) t
                """
            ).fetchone()[0]
        )
        dedup_src = f"{src_table}_processing__dedupe_src"
        con.execute(f"CREATE OR REPLACE TEMP TABLE {dedup_src} AS SELECT * FROM {processing_tbl}")
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE {processing_tbl} AS
            SELECT * EXCLUDE ("__dedup_rn")
            FROM (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY {part_keys}
                        ORDER BY z.{quote_ident("__id__")} ASC
                    ) AS __dedup_rn
                FROM {dedup_src} AS z
            ) ranked
            WHERE __dedup_rn = 1
            """
        )
        con.execute(f"DROP TABLE IF EXISTS {dedup_src}")
        proc_after = int(con.execute(f"SELECT COUNT(*) FROM {processing_tbl}").fetchone()[0])
        dup_excluded = max(0, proc_before - proc_after)
        processed_rows = proc_after

    return SheetResult(
        name=sheet_name,
        input_rows=int(input_rows),
        excluded_rows=int(excluded_rows),
        processed_rows=int(processed_rows),
        duplicate_key_detected_rows=dup_detected,
        duplicate_key_excluded_rows=dup_excluded,
        clean_table=processing_tbl,
        state_table=f"{src_table}_state",
        data_columns=cols,
    )


def read_config(
    config_path: Optional[str],
    config_sheet: Optional[str],
    validate_schema: bool = True,
) -> Optional[pl.DataFrame]:
    del validate_schema  # Pandera removed; columns validated below.
    del config_sheet  # Parquet-only config table.
    if not config_path:
        return None
    ext = os.path.splitext(config_path)[1].lower()
    if ext not in {".parquet", ".pq"}:
        raise ValueError(f"Column config must be Parquet (.parquet / .pq); got {ext!r}")
    df = pl.read_parquet(config_path)

    if df.width < 2:
        raise ValueError("Config file must have at least two columns: file, column")
    if list(df.columns[:2]) != ["file", "column"]:
        df = df.rename({df.columns[0]: "file", df.columns[1]: "column"})
    df = df.select(
        pl.col("file").cast(pl.Utf8).str.strip_chars(),
        pl.col("column").cast(pl.Utf8).str.strip_chars(),
    )
    return df


def get_output_columns(
    result_cols: List[str],
    config_df: Optional[pl.DataFrame],
    left_name: str,
    right_name: str,
    left_data_columns: Sequence[str],
    right_data_columns: Sequence[str],
    merge_on: Sequence[str],
    left_id_col: str,
    left_key_col: str,
    right_id_col: str,
    right_key_col: str,
    suffixes: Tuple[str, str],
) -> List[str]:
    mandatory = ["match_status", left_id_col, left_key_col, right_id_col, right_key_col]
    mandatory_set = set(mandatory)
    result_set = set(result_cols)
    left_data_set = set(left_data_columns)
    right_data_set = set(right_data_columns)
    merge_on_set = set(merge_on)

    def side_of_file(value: str) -> Optional[str]:
        v = clean_col_name(value)
        if v == left_name:
            return "left"
        if v == right_name:
            return "right"
        return None

    def resolve_side_col(base_col: str, side: str) -> Optional[str]:
        if side == "left":
            suffixed = f"{base_col}{suffixes[0]}"
            if suffixed in result_set:
                return suffixed
            # For merge keys, prefer side-specific key columns only.
            if base_col in merge_on_set:
                return None
            if base_col in result_set and base_col in left_data_set and base_col not in right_data_set:
                return base_col
            return None
        if side == "right":
            suffixed = f"{base_col}{suffixes[1]}"
            if suffixed in result_set:
                return suffixed
            # For merge keys, prefer side-specific key columns only.
            if base_col in merge_on_set:
                return None
            if base_col in result_set and base_col in right_data_set and base_col not in left_data_set:
                return base_col
            return None
        return None

    def wildcard_cols(side: str) -> List[str]:
        src_cols = left_data_columns if side == "left" else right_data_columns
        out: List[str] = []
        for src_col in src_cols:
            mapped = resolve_side_col(src_col, side)
            if mapped and mapped not in out and mapped not in mandatory_set:
                out.append(mapped)
        return out

    def parse_exclude_token(token: str) -> Optional[str]:
        if token.startswith("!"):
            c = clean_col_name(token[1:])
            return c or None
        lower = token.lower()
        if lower.startswith("__exclude__:"):
            c = clean_col_name(token[len("__exclude__:") :])
            return c or None
        return None

    def remove_from_output(current: List[str], col: str) -> List[str]:
        return [x for x in current if x != col]

    def remove_for_side(current: List[str], base_col: str, side: str) -> List[str]:
        mapped = resolve_side_col(base_col, side)
        if mapped:
            current = remove_from_output(current, mapped)
        # Also allow removing direct result column names if provided.
        if base_col in result_set:
            current = remove_from_output(current, base_col)
        return current

    if config_df is None:
        return mandatory + [c for c in result_cols if c not in mandatory_set]

    out: List[str] = []
    blocked_cols: set[str] = set()
    rows = config_df.iter_rows(named=True)
    for r in rows:
        s_raw = clean_col_name(r.get("file", ""))
        c = clean_col_name(r.get("column", ""))
        side = side_of_file(s_raw)
        if not side or not c or c in mandatory_set:
            continue

        exclude_base = parse_exclude_token(c)
        if exclude_base:
            mapped = resolve_side_col(exclude_base, side)
            if mapped:
                blocked_cols.add(mapped)
            if exclude_base in result_set:
                blocked_cols.add(exclude_base)
            out = remove_for_side(out, exclude_base, side)
            continue

        if c == "*":
            for wc in wildcard_cols(side):
                if wc in blocked_cols:
                    continue
                out.append(wc)
            continue

        mapped = resolve_side_col(c, side)
        if mapped and mapped not in blocked_cols:
            out.append(mapped)
            continue
        # Fallback: allow direct result column references.
        if c in result_set and c not in blocked_cols:
            out.append(c)

    deduped: List[str] = []
    seen = set()
    for c in out:
        if c in seen or c in mandatory_set:
            continue
        seen.add(c)
        deduped.append(c)
    return mandatory + deduped


def build_merge_sql(
    con: duckdb.DuckDBPyConnection,
    left_table: str,
    right_table: str,
    left_name: str,
    right_name: str,
    merge_on: List[str],
    how: str,
    suffixes: Tuple[str, str],
) -> Tuple[str, List[str]]:
    left_cols = [r[1] for r in con.execute(f"PRAGMA table_info({left_table})").fetchall() if r[1] != "__ord"]
    right_cols = [r[1] for r in con.execute(f"PRAGMA table_info({right_table})").fetchall() if r[1] != "__ord"]
    left_set = set(left_cols)
    right_set = set(right_cols)
    common_non_key = sorted((left_set & right_set) - set(merge_on) - {"__id__"})
    left_only = [c for c in left_cols if c not in right_set]
    right_only = [c for c in right_cols if c not in left_set]

    join_map = {"outer": "FULL OUTER", "left": "LEFT", "right": "RIGHT", "inner": "INNER"}
    on_clause = " AND ".join(
        f"{normalized_key_component_expr('l', k)} = {normalized_key_component_expr('r', k)}"
        for k in merge_on
    )
    first_key = merge_on[0]
    status_expr = (
        f"CASE WHEN l.{quote_ident(first_key)} IS NOT NULL AND r.{quote_ident(first_key)} IS NOT NULL THEN 'match' "
        f"WHEN l.{quote_ident(first_key)} IS NOT NULL THEN {quote_literal('only in ' + left_name)} "
        f"WHEN r.{quote_ident(first_key)} IS NOT NULL THEN {quote_literal('only in ' + right_name)} "
        f"ELSE 'unknown' END AS match_status"
    )
    left_key_col = f"key_{left_name}"
    right_key_col = f"key_{right_name}"
    left_id_col = f"id_{left_name}"
    right_id_col = f"id_{right_name}"
    left_key_expr = f"{build_key_expr('l', merge_on)} AS {quote_ident(left_key_col)}"
    right_key_expr = f"{build_key_expr('r', merge_on)} AS {quote_ident(right_key_col)}"
    left_id_expr = f"l.{quote_ident('__id__')} AS {quote_ident(left_id_col)}"
    right_id_expr = f"r.{quote_ident('__id__')} AS {quote_ident(right_id_col)}"

    select_exprs = [status_expr, left_id_expr, left_key_expr, right_id_expr, right_key_expr]
    out_cols = ["match_status", left_id_col, left_key_col, right_id_col, right_key_col]

    # Coalesced join keys (shared), then all left-side columns together, then all right-side together
    # (after match_status and per-side __id__ / __key__ columns, which stay as today).
    for k in merge_on:
        select_exprs.append(f"COALESCE(l.{quote_ident(k)}, r.{quote_ident(k)}) AS {quote_ident(k)}")
        out_cols.append(k)
    for k in merge_on:
        lk = f"{k}{suffixes[0]}"
        select_exprs.append(f"l.{quote_ident(k)} AS {quote_ident(lk)}")
        out_cols.append(lk)
    for c in left_only:
        select_exprs.append(f"l.{quote_ident(c)} AS {quote_ident(c)}")
        out_cols.append(c)
    for c in common_non_key:
        lc = f"{c}{suffixes[0]}"
        select_exprs.append(f"l.{quote_ident(c)} AS {quote_ident(lc)}")
        out_cols.append(lc)
    for k in merge_on:
        rk = f"{k}{suffixes[1]}"
        select_exprs.append(f"r.{quote_ident(k)} AS {quote_ident(rk)}")
        out_cols.append(rk)
    for c in right_only:
        select_exprs.append(f"r.{quote_ident(c)} AS {quote_ident(c)}")
        out_cols.append(c)
    for c in common_non_key:
        rc = f"{c}{suffixes[1]}"
        select_exprs.append(f"r.{quote_ident(c)} AS {quote_ident(rc)}")
        out_cols.append(rc)

    sql = (
        f"SELECT {', '.join(select_exprs)} "
        f"FROM {left_table} l {join_map[how]} JOIN {right_table} r ON {on_clause}"
    )
    return sql, out_cols


def copy_table_to_csv_or_parquet(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    path: str,
):
    ext = os.path.splitext(path)[1].lower()
    if ext in {".csv", ".txt"}:
        con.execute(
            f"COPY (SELECT * FROM {table_name}) TO {quote_literal(path)} "
            "(FORMAT CSV, HEADER TRUE)"
        )
    elif ext in {".parquet", ".pq"}:
        con.execute(
            f"COPY (SELECT * FROM {table_name}) TO {quote_literal(path)} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    else:
        raise ValueError(f"Unsupported output format for single table: {ext}")


def write_excel(
    output_file: str,
    header_rows: List[List],
    result_tbl,
    stats_tbl,
):
    result_tbl = ensure_arrow_table(result_tbl)
    stats_tbl = ensure_arrow_table(stats_tbl)

    wb = xlsxwriter.Workbook(output_file)
    yellow = wb.add_format({"bg_color": "#FFFF00"})
    green = wb.add_format({"bg_color": "#CCFFCC"})
    blue = wb.add_format({"bg_color": "#4F81BD", "font_color": "white"})

    ws_r = wb.add_worksheet("result")
    for i, row in enumerate(header_rows):
        ws_r.write_row(i, 0, row)
    for ridx, row in enumerate(result_tbl.to_pylist(), start=3):
        ws_r.write_row(ridx, 0, [row.get(c) for c in result_tbl.column_names])
    ws_r.set_row(0, None, yellow)
    ws_r.set_row(2, None, green)

    ws_s = wb.add_worksheet("stats")
    stats_rows = [stats_tbl.column_names] + [list(r.values()) for r in stats_tbl.to_pylist()]
    for i, row in enumerate(stats_rows):
        ws_s.write_row(i, 0, row)
    ws_s.set_row(0, None, blue)

    wb.close()


def build_source_state_table(
    con: duckdb.DuckDBPyConnection,
    src_table: str,
) -> pa.Table:
    sql = f'SELECT * FROM {src_table}_state ORDER BY "__id__" ASC'
    return ensure_arrow_table(con.execute(sql).arrow())


def write_source_state_excel(
    output_file: str,
    left_name: str,
    left_tbl: pa.Table,
    right_name: str,
    right_tbl: pa.Table,
):
    wb = xlsxwriter.Workbook(output_file)
    for sheet_name, tbl in [(left_name, left_tbl), (right_name, right_tbl)]:
        ws = wb.add_worksheet(safe_name(sheet_name))
        ws.write_row(0, 0, tbl.column_names)
        for i, row in enumerate(tbl.to_pylist(), start=1):
            ws.write_row(i, 0, [row.get(c) for c in tbl.column_names])
    wb.close()


def strip_suffix(col: str, suffixes: Tuple[str, str]) -> str:
    if col.endswith(suffixes[0]):
        return col[: -len(suffixes[0])]
    if col.endswith(suffixes[1]):
        return col[: -len(suffixes[1])]
    return col


def display_col_name(
    col: str,
    left_id_col: str,
    left_key_col: str,
    right_id_col: str,
    right_key_col: str,
    suffixes: Tuple[str, str],
) -> str:
    if col in {left_id_col, right_id_col}:
        return "__id__"
    if col in {left_key_col, right_key_col}:
        return "__key__"
    return strip_suffix(col, suffixes)


def default_output_path(
    file1: str,
    file2: str,
    output_file: Optional[str],
    output_file_name: str = DEFAULT_OUTPUT_FILE_NAME,
) -> str:
    if output_file:
        return output_file
    out_dir = os.path.dirname(file1) or "."
    return str(Path(out_dir) / f"{output_file_name}.xlsx")


def join_files(
    file1: str,
    file2: str,
    config_file: Optional[str],
    merge_on: Sequence[str],
    how: str,
    output_file: Optional[str] = None,
    output_file_name: str = DEFAULT_OUTPUT_FILE_NAME,
    output_format: Optional[Literal["xlsx", "csv", "parquet"]] = None,
    file1_sheet: Optional[str] = None,
    file2_sheet: Optional[str] = None,
    config_sheet: Optional[str] = None,
    file1_name: Optional[str] = None,
    file2_name: Optional[str] = None,
    suffixes: Tuple[str, str] = ("_left", "_right"),
    large_mode: bool = False,
    threads: Optional[int] = None,
    temp_directory: Optional[str] = None,
    memory_limit: Optional[str] = None,
    write_ignored: Optional[bool] = None,
    debug: bool = False,
    logger: Optional[logging.Logger] = None,
    max_runtime_seconds: Optional[float] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    timestamp_output: bool = False,
    dedupe_left_keys: bool = False,
    dedupe_right_keys: bool = False,
):
    global DEBUG_MODE, RUNTIME_LOGGER
    DEBUG_MODE = debug
    RUNTIME_LOGGER = logger
    t0 = time.perf_counter()
    output_file = default_output_path(file1, file2, output_file, output_file_name)
    log_stage("Starting join pipeline")
    log_stage("Running preflight checks")
    _check_runtime_guard(
        t0=t0,
        stage="start",
        max_runtime_seconds=max_runtime_seconds,
        cancel_check=cancel_check,
    )
    output_file = _apply_format(output_file, output_format)
    _validate_parquet_path(file1, "file1")
    _validate_parquet_path(file2, "file2")
    if config_file:
        _validate_parquet_path(config_file, "config_file")
    _ensure_parent(output_file)
    if how not in {"left", "right", "inner", "outer"}:
        raise ValueError("how must be one of: left,right,inner,outer")

    merge_on_clean = [clean_col_name(m) for m in merge_on]
    if not merge_on_clean or any(not m for m in merge_on_clean):
        raise ValueError("merge_on must contain at least one non-empty column name")
    if len(set(merge_on_clean)) != len(merge_on_clean):
        raise ValueError("merge_on contains duplicate column names")
    left_name = file1_name or os.path.splitext(os.path.basename(file1))[0]
    right_name = file2_name or os.path.splitext(os.path.basename(file2))[0]
    log_stage(f"Inputs ready: left='{left_name}', right='{right_name}', how='{how}', keys={merge_on_clean}")
    log_stage(f"file1={os.path.abspath(file1)}")
    log_stage(f"file2={os.path.abspath(file2)}")
    log_stage(f"config_file={os.path.abspath(config_file) if config_file else None}")
    log_debug(f"file1_sheet={file1_sheet}, file2_sheet={file2_sheet}, config_sheet={config_sheet}")
    log_stage(f"requested_output_file={os.path.abspath(output_file)}")
    log_debug(f"output_format={output_format}")
    log_debug(f"large_mode={large_mode}, threads={threads}, temp_directory={temp_directory}, memory_limit={memory_limit}")
    _check_runtime_guard(
        t0=t0,
        stage="inputs_ready",
        max_runtime_seconds=max_runtime_seconds,
        cancel_check=cancel_check,
    )

    con = duckdb.connect(database=":memory:")
    con.execute("PRAGMA preserve_insertion_order=false")
    effective_threads = threads if threads is not None else (max(1, (os.cpu_count() or 8) - 1) if large_mode else 8)
    con.execute(f"PRAGMA threads={int(effective_threads)}")
    effective_temp_dir = temp_directory or os.getenv("TMPDIR") or "/tmp"
    con.execute(f"PRAGMA temp_directory={quote_literal(effective_temp_dir)}")
    log_debug(f"duckdb_threads={effective_threads}")
    log_debug(f"duckdb_temp_directory={effective_temp_dir}")
    if memory_limit:
        con.execute(f"PRAGMA memory_limit={quote_literal(memory_limit)}")
        log_debug(f"duckdb_memory_limit={memory_limit}")

    log_stage("Loading source files into DuckDB")
    load_table_to_con(con, file1, "src1", file1_sheet)
    load_table_to_con(con, file2, "src2", file2_sheet)
    log_debug(f"src1_rows={con.execute('SELECT COUNT(*) FROM src1').fetchone()[0]}")
    log_debug(f"src2_rows={con.execute('SELECT COUNT(*) FROM src2').fetchone()[0]}")
    _check_runtime_guard(
        t0=t0,
        stage="loaded_sources",
        max_runtime_seconds=max_runtime_seconds,
        cancel_check=cancel_check,
    )

    log_stage(f"Applying state engine to '{left_name}'")
    left_result = process_sheet(con, "src1", left_name, merge_on_clean, dedupe_duplicate_keys=dedupe_left_keys)
    log_stage(f"Applying state engine to '{right_name}'")
    right_result = process_sheet(con, "src2", right_name, merge_on_clean, dedupe_duplicate_keys=dedupe_right_keys)
    log_debug(
        f"{left_name}: input_rows={left_result.input_rows}, excluded_rows={left_result.excluded_rows}, processed_rows={left_result.processed_rows}"
    )
    log_debug(
        f"{right_name}: input_rows={right_result.input_rows}, excluded_rows={right_result.excluded_rows}, processed_rows={right_result.processed_rows}"
    )
    _check_runtime_guard(
        t0=t0,
        stage="state_engine",
        max_runtime_seconds=max_runtime_seconds,
        cancel_check=cancel_check,
    )

    log_stage("Building and executing join SQL on process rows")
    merge_sql, _ = build_merge_sql(
        con,
        left_result.clean_table,
        right_result.clean_table,
        left_name,
        right_name,
        merge_on_clean,
        how,
        suffixes,
    )
    con.execute(f"CREATE OR REPLACE TEMP TABLE merged AS {merge_sql}")
    result_cols = [r[1] for r in con.execute("PRAGMA table_info(merged)").fetchall()]
    left_data_set = set(left_result.data_columns) | {"__id__"}
    right_data_set = set(right_result.data_columns) | {"__id__"}
    left_only_cols = left_data_set - right_data_set
    right_only_cols = right_data_set - left_data_set
    left_id_col = f"id_{left_name}"
    left_key_col = f"key_{left_name}"
    right_id_col = f"id_{right_name}"
    right_key_col = f"key_{right_name}"

    log_stage("Applying output column configuration")
    config_df = read_config(config_file, config_sheet, validate_schema=not large_mode) if config_file else None
    out_cols = get_output_columns(
        result_cols,
        config_df,
        left_name,
        right_name,
        left_result.data_columns,
        right_result.data_columns,
        merge_on_clean,
        left_id_col,
        left_key_col,
        right_id_col,
        right_key_col,
        suffixes,
    )
    out_cols_sql = ", ".join(quote_ident(c) for c in out_cols if c in result_cols)
    con.execute(f"CREATE OR REPLACE TEMP TABLE final_result AS SELECT {out_cols_sql} FROM merged")
    _check_runtime_guard(
        t0=t0,
        stage="final_result_built",
        max_runtime_seconds=max_runtime_seconds,
        cancel_check=cancel_check,
    )
    log_debug(f"final_output_columns={out_cols}")

    status_counts = dict(
        con.execute("SELECT match_status, COUNT(*) AS c FROM final_result GROUP BY match_status").fetchall()
    )
    log_debug(f"status_counts={status_counts}")
    match_count = int(status_counts.get("match", 0))
    only_left_count = int(status_counts.get(f"only in {left_name}", 0))
    only_right_count = int(status_counts.get(f"only in {right_name}", 0))

    l_proc = left_result.processed_rows
    r_proc = right_result.processed_rows
    if how == "inner":
        # Non-matching keys never appear in an INNER result, so match_status has no
        # "only in …" rows; derive key NO match as residual of processed rows.
        only_left_count = max(0, l_proc - left_result.duplicate_key_excluded_rows - match_count)
        only_right_count = max(0, r_proc - right_result.duplicate_key_excluded_rows - match_count)
    t_proc = l_proc + r_proc
    l_input = left_result.input_rows
    r_input = right_result.input_rows
    t_input = l_input + r_input
    l_excl = left_result.excluded_rows
    r_excl = right_result.excluded_rows
    t_excl = l_excl + r_excl

    l_excl_pct = f"{int(round(l_excl / l_input * 100))}%" if l_input else ""
    r_excl_pct = f"{int(round(r_excl / r_input * 100))}%" if r_input else ""
    t_excl_pct = f"{int(round(t_excl / t_input * 100))}%" if t_input else ""
    l_proc_pct = f"{int(round(l_proc / l_input * 100))}%" if l_input else ""
    r_proc_pct = f"{int(round(r_proc / r_input * 100))}%" if r_input else ""
    t_proc_pct = f"{int(round(t_proc / t_input * 100))}%" if t_input else ""

    l_match_pct = f"{int(round(match_count / l_proc * 100))}%" if l_proc else ""
    r_match_pct = f"{int(round(match_count / r_proc * 100))}%" if r_proc else ""
    l_only_pct = f"{int(round(only_left_count / l_proc * 100))}%" if l_proc else ""
    r_only_pct = f"{int(round(only_right_count / r_proc * 100))}%" if r_proc else ""
    total_match_count = match_count * 2
    total_only_count = only_left_count + only_right_count
    total_match_pct = f"{int(round(total_match_count / t_proc * 100))}%" if t_proc else ""
    total_only_pct = f"{int(round(total_only_count / t_proc * 100))}%" if t_proc else ""

    stats_rows = [
        {
            "Sheet": left_name,
            "total rows inputfile": l_input,
            "excluded rows": l_excl,
            "Rows processed": l_proc,
            "duplicate keys detected": left_result.duplicate_key_detected_rows,
            "duplicate keys excluded": left_result.duplicate_key_excluded_rows,
            "key match": match_count,
            "key NO match": only_left_count,
            "excluded rows %": l_excl_pct,
            "Rows processed %": l_proc_pct,
            "key match %": l_match_pct,
            "key NO match %": l_only_pct,
        },
        {
            "Sheet": right_name,
            "total rows inputfile": r_input,
            "excluded rows": r_excl,
            "Rows processed": r_proc,
            "duplicate keys detected": right_result.duplicate_key_detected_rows,
            "duplicate keys excluded": right_result.duplicate_key_excluded_rows,
            "key match": match_count,
            "key NO match": only_right_count,
            "excluded rows %": r_excl_pct,
            "Rows processed %": r_proc_pct,
            "key match %": r_match_pct,
            "key NO match %": r_only_pct,
        },
        {
            "Sheet": "Total",
            "total rows inputfile": t_input,
            "excluded rows": t_excl,
            "Rows processed": t_proc,
            "duplicate keys detected": (
                left_result.duplicate_key_detected_rows + right_result.duplicate_key_detected_rows
            ),
            "duplicate keys excluded": (
                left_result.duplicate_key_excluded_rows + right_result.duplicate_key_excluded_rows
            ),
            "key match": total_match_count,
            "key NO match": total_only_count,
            "excluded rows %": t_excl_pct,
            "Rows processed %": t_proc_pct,
            "key match %": total_match_pct,
            "key NO match %": total_only_pct,
        },
    ]

    output_file_ts = output_file
    _ensure_parent(output_file_ts)
    log_stage(f"resolved_output_file={os.path.abspath(output_file_ts)}")
    elapsed_sec = time.perf_counter() - t0

    source_row = []
    for c in out_cols:
        if c == "match_status":
            source_row.append("sheet_name >")
        elif c == left_id_col:
            source_row.append(left_name)
        elif c == left_key_col:
            source_row.append(left_name)
        elif c == right_id_col:
            source_row.append(right_name)
        elif c == right_key_col:
            source_row.append(right_name)
        elif c.endswith(suffixes[0]):
            source_row.append(left_name)
        elif c.endswith(suffixes[1]):
            source_row.append(right_name)
        elif c in left_only_cols:
            source_row.append(left_name)
        elif c in right_only_cols:
            source_row.append(right_name)
        elif c in merge_on_clean:
            source_row.append("both")
        else:
            source_row.append("")

    col_row = [display_col_name(c, left_id_col, left_key_col, right_id_col, right_key_col, suffixes) for c in out_cols]
    blank_row = [""] * len(out_cols)
    stats_tbl = pa.Table.from_pylist(stats_rows)

    out_ext = os.path.splitext(output_file_ts)[1].lower()
    if out_ext in {".xlsx"}:
        log_stage("Writing XLSX result and stats")
        result_row_count = con.execute("SELECT COUNT(*) FROM final_result").fetchone()[0]
        if result_row_count + 3 > XLSX_MAX_ROWS:
            raise ValueError(
                f"Result has {result_row_count} data rows, exceeds Excel limit "
                f"{XLSX_MAX_ROWS - 3} for this output layout. "
                "Use .parquet or .csv output instead."
            )
        result_tbl = ensure_arrow_table(con.execute("SELECT * FROM final_result").arrow())
        write_excel(output_file_ts, [source_row, blank_row, col_row], result_tbl, stats_tbl)
    elif out_ext in {".csv", ".parquet", ".pq"}:
        log_stage(f"Writing split {out_ext} outputs (result/stats)")
        base, ext = os.path.splitext(output_file_ts)
        stats_out_tbl = "stats_out"
        con.register("stats_out_arrow", stats_tbl)
        con.execute(f"CREATE OR REPLACE TEMP TABLE {stats_out_tbl} AS SELECT * FROM stats_out_arrow")
        copy_table_to_csv_or_parquet(con, "final_result", f"{base}_result{ext}")
        copy_table_to_csv_or_parquet(con, stats_out_tbl, f"{base}_stats{ext}")
        log_stage(f"wrote_result={os.path.abspath(f'{base}_result{ext}')}")
        log_stage(f"wrote_stats={os.path.abspath(f'{base}_stats{ext}')}")
    else:
        raise ValueError("Output must be .xlsx, .csv, or .parquet")
    _check_runtime_guard(
        t0=t0,
        stage="outputs_written",
        max_runtime_seconds=max_runtime_seconds,
        cancel_check=cancel_check,
    )

    log_stage("Writing input state (per-side)")
    state_dir = os.path.dirname(output_file_ts)
    ts_suffix = f"_{timestamp_compact_microseconds()}" if timestamp_output else ""
    state_base = os.path.join(state_dir, f"join_input_state{ts_suffix}")
    left_state_tbl = build_source_state_table(con, "src1")
    right_state_tbl = build_source_state_table(con, "src2")

    if out_ext in {".xlsx"}:
        input_state_file = f"{state_base}.xlsx"
        _ensure_parent(input_state_file)
        write_source_state_excel(input_state_file, left_name, left_state_tbl, right_name, right_state_tbl)
        input_state_files = [input_state_file]
        log_stage(f"wrote_input_state={os.path.abspath(input_state_file)}")
    elif out_ext in {".csv", ".parquet", ".pq"}:
        _, state_ext = os.path.splitext(output_file_ts)
        state_ext = state_ext.lower()
        if state_ext == ".pq":
            state_ext = ".parquet"
        base_state = state_base
        left_path = f"{base_state}_left{state_ext}"
        right_path = f"{base_state}_right{state_ext}"
        _ensure_parent(left_path)
        con.register("_jh_left_st_arrow", left_state_tbl)
        con.execute("CREATE OR REPLACE TEMP TABLE _jh_left_state AS SELECT * FROM _jh_left_st_arrow")
        copy_table_to_csv_or_parquet(con, "_jh_left_state", left_path)
        con.register("_jh_right_st_arrow", right_state_tbl)
        con.execute("CREATE OR REPLACE TEMP TABLE _jh_right_state AS SELECT * FROM _jh_right_st_arrow")
        copy_table_to_csv_or_parquet(con, "_jh_right_state", right_path)
        input_state_files = [left_path, right_path]
        log_stage(f"wrote_input_state_left={os.path.abspath(left_path)}")
        log_stage(f"wrote_input_state_right={os.path.abspath(right_path)}")
    else:
        raise ValueError("Output must be .xlsx, .csv, or .parquet")

    log_stage(f"Completed in {time.perf_counter() - t0:.3f}s")

    return {
        "execution_time_sec": round(elapsed_sec, 3),
        "output_file": output_file_ts,
        "input_state_file": input_state_files[0],
        "input_state_files": input_state_files,
    }


def join_files_api(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """API-safe wrapper returning structured success/error payload."""
    try:
        result = join_files(*args, **kwargs)
        return asdict(JoinApiResponse(ok=True, result=result, error=None))
    except JoinTimeoutError as e:
        err = JoinErrorModel(code="timeout", message=str(e))
        return asdict(JoinApiResponse(ok=False, result=None, error=err))
    except JoinCancelledError as e:
        err = JoinErrorModel(code="cancelled", message=str(e))
        return asdict(JoinApiResponse(ok=False, result=None, error=err))
    except Exception as e:  # noqa: BLE001
        err = JoinErrorModel(code="runtime_error", message=str(e))
        return asdict(JoinApiResponse(ok=False, result=None, error=err))

