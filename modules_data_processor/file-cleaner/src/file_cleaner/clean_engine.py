from __future__ import annotations

import concurrent.futures
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence

from shared_lib.parquet_row_audit import dedupe_partition_expressions, keep_row_if_not_blank_sql_exprs
from shared_lib.time_utils import timestamp_compact


def _load_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "duckdb is required. Run with Pixi from this package or: pip install duckdb"
        ) from exc
    return duckdb


def _load_polars():
    try:
        import polars as pl
    except ImportError as exc:
        raise ImportError(
            "polars is required. Run with Pixi from this package or: pip install polars"
        ) from exc
    return pl


def _duckdb_sql_to_polars(conn: Any, sql: str) -> Any:
    """Run ``sql`` on ``conn`` and return a Polars ``DataFrame`` (``.pl()`` or Arrow)."""
    pl = _load_polars()
    res = conn.execute(sql)
    if hasattr(res, "pl"):
        return res.pl()
    try:
        import pyarrow  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "pyarrow is required for DuckDB → Polars (XLSX export). Install file-cleaner dependencies."
        ) from exc
    return pl.from_arrow(res.fetch_arrow_table())


# Global regex patterns for cleaning steps (kept from original)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1F\x7F]+")
MULTI_SPACE_RE = re.compile(r"\s+")

APP_NAME = "file_cleaner"
DEFAULT_INPUT_MAX_FILES_GUARD = 5000

HUGE_FILE_MODE_THRESHOLD_MB = 1024
HUGE_FILE_MODE_CHUNK_ROWS = 50000
SUPPORTED_INPUT_SUFFIXES = frozenset({".parquet", ".pq"})
SUPPORTED_INPUT_FORMATS = ("parquet",)

def _resolve_input_paths(
    raw_inputs: Sequence[str],
    *,
    allow_empty_match: bool,
    max_files: int,
    include_dirs: bool,
    supported_suffixes: set[str],
) -> list[Path]:
    paths: list[Path] = []
    for raw in raw_inputs:
        text = str(raw or "").strip()
        if not text:
            continue
        expanded = os.path.expandvars(os.path.expanduser(text))
        has_glob = glob.has_magic(expanded)
        matches = [Path(m).resolve() for m in glob.glob(expanded, recursive=True)] if has_glob else []
        if has_glob:
            if not matches and not allow_empty_match:
                raise FileNotFoundError(f"No matches for pattern: {text}")
            paths.extend(matches)
            continue
        p = Path(expanded).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Input path not found: {p}")
        paths.append(p)

    out: list[Path] = []
    seen: set[Path] = set()
    allowed = {str(s).lower() for s in supported_suffixes}
    for p in paths:
        if p.is_dir():
            if include_dirs and p not in seen:
                seen.add(p)
                out.append(p)
            continue
        if p.is_file() and p.suffix.lower() in allowed:
            r = p.resolve()
            if r not in seen:
                seen.add(r)
                out.append(r)
    if len(out) > max_files:
        raise ValueError(f"Resolved input file count {len(out)} exceeds max_files={max_files}.")
    return out


def inject_ip_id_column(input_path: str, outdir: str, delimiter: Optional[str], intermediate_format: str, script_name: str) -> str:
    """
    [POLARS STEP] Create an intermediate copy of input file with x_ip_id column added.
    Input must be Parquet (.parquet / .pq). Returns new intermediate path.
    """
    # ***FIXED: Use script_name instead of input base name (base_name).***
    ts = timestamp_compact()
    
    # Ensure the output directory exists for the intermediate file
    os.makedirs(outdir, exist_ok=True)
    
    # ***FIXED: Changed filename from f"{base_name}_intermediate..." to f"{script_name}_intermediate..."***
    new_path = os.path.join(outdir, f"{script_name}_intermediate_{ts}.{intermediate_format}")
    
    print(f"   ⚙️  Creating intermediate {intermediate_format.upper()} with Polars...")
    
    try:
        ext = os.path.splitext(input_path)[1].lower()
        if ext in (".parquet", ".pq"):
            pl = _load_polars()
            pf = pl.read_parquet(input_path)
        else:
            raise ValueError(
                f"Unsupported input format for indexing (Parquet only): {ext}. "
                "Convert other formats to Parquet before running the cleaner."
            )

        # Add the index column (x_ip_id) using Polars
        # The offset=1 ensures the index starts at 1, matching common row counts.
        pf = pf.with_row_index("x_ip_id", offset=1) 
        
        # Write to requested intermediate format
        if intermediate_format == 'parquet':
            pf.write_parquet(new_path)
        elif intermediate_format == 'csv':
            # Use the input delimiter for the intermediate CSV for consistency
            pf.write_csv(new_path, separator=delimiter or ',')
        
        print(f"   ➕ Created intermediate file with x_ip_id: {new_path}")
        return new_path
    except Exception as e:
        print(f"   ❌ Polars indexing failed: {e}")
        raise


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _csv_delimiter(args: Any) -> str:
    return args.delimiter if args.delimiter else ","


def _split_csv_values(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in str(raw).split(",") if x.strip()]


def _load_manifest_input_paths(manifest_path: str) -> list[str]:
    path = Path(str(manifest_path)).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Input manifest not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to parse input manifest JSON: {path}") from exc
    artifacts = payload.get("artifacts", []) if isinstance(payload, dict) else []
    if not isinstance(artifacts, list):
        raise ValueError(f"Invalid manifest schema (artifacts must be a list): {path}")
    inputs: list[str] = []
    skipped_non_parquet: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        out_file = artifact.get("output_file_abs") or artifact.get("output_file")
        if not out_file:
            continue
        resolved = Path(str(out_file)).expanduser().resolve()
        if resolved.exists() and resolved.is_file():
            if resolved.suffix.lower() in SUPPORTED_INPUT_SUFFIXES:
                inputs.append(str(resolved))
            else:
                skipped_non_parquet.append(str(resolved))
    if skipped_non_parquet:
        preview = ", ".join(skipped_non_parquet[:5])
        more = f" (+{len(skipped_non_parquet) - 5} more)" if len(skipped_non_parquet) > 5 else ""
        print(
            f"Warning: manifest skipped {len(skipped_non_parquet)} non-Parquet path(s) "
            f"(only .parquet / .pq accepted): {preview}{more}",
            file=sys.stderr,
        )
    return sorted(set(inputs))


def _default_null_tokens() -> list[str]:
    return ["na", "n/a", "null", "none", "nil", "unknown", "-", "--"]


def _is_string_type(type_name: str) -> bool:
    t = str(type_name).lower()
    return "varchar" in t or "string" in t or "text" in t


def _normalized_numeric_text_expr(base_expr: str) -> str:
    raw = f"trim(cast({base_expr} AS VARCHAR))"
    cleaned = f"regexp_replace({raw}, '[^0-9,\\.\\-]', '', 'g')"
    return (
        "CASE "
        f"WHEN regexp_matches({cleaned}, '^-?[0-9]{{1,3}}(\\.[0-9]{{3}})+,[0-9]+$') THEN replace(replace({cleaned}, '.', ''), ',', '.') "
        f"WHEN regexp_matches({cleaned}, '^-?[0-9]{{1,3}}(,[0-9]{{3}})+\\.[0-9]+$') THEN replace({cleaned}, ',', '') "
        f"WHEN regexp_matches({cleaned}, '^-?[0-9]+,[0-9]+$') THEN replace({cleaned}, ',', '.') "
        f"ELSE replace({cleaned}, ',', '') END"
    )


def _date_parse_expr(base_expr: str) -> str:
    text_expr = f"trim(cast({base_expr} AS VARCHAR))"
    return (
        f"COALESCE("
        f"TRY_CAST({base_expr} AS TIMESTAMP), "
        f"TRY_STRPTIME({text_expr}, '%Y-%m-%d'), "
        f"TRY_STRPTIME({text_expr}, '%d/%m/%Y'), "
        f"TRY_STRPTIME({text_expr}, '%m/%d/%Y'), "
        f"TRY_STRPTIME({text_expr}, '%Y/%m/%d'), "
        f"TRY_STRPTIME({text_expr}, '%d-%m-%Y'), "
        f"TRY_STRPTIME({text_expr}, '%m-%d-%Y'), "
        f"TRY_STRPTIME({text_expr}, '%Y-%m-%d %H:%M:%S'), "
        f"TRY_STRPTIME({text_expr}, '%d/%m/%Y %H:%M:%S'), "
        f"TRY_STRPTIME({text_expr}, '%m/%d/%Y %H:%M:%S')"
        f")"
    )


def _infer_adaptive_column_kinds(
    conn: Any,
    *,
    table_name: str,
    columns: list[str],
    column_types: dict[str, str],
    sample_rows: int = 5000,
    confidence_threshold: float = 0.6,
) -> dict[str, str]:
    inferred: dict[str, str] = {}
    for col in columns:
        col_type = str(column_types.get(col, "")).lower()
        if not _is_string_type(col_type):
            continue
        col_ref = f'"{col}"'
        sample_sql = f"SELECT {col_ref} AS val FROM {table_name} LIMIT {max(1, int(sample_rows))}"
        non_empty_sql = "count(*) FILTER (WHERE val IS NOT NULL AND trim(cast(val AS VARCHAR)) != '')"
        num_ok_sql = (
            f"count(*) FILTER (WHERE TRY_CAST({_normalized_numeric_text_expr('val')} AS DOUBLE) IS NOT NULL)"
        )
        date_ok_sql = f"count(*) FILTER (WHERE {_date_parse_expr('val')} IS NOT NULL)"
        date_pattern_sql = (
            "count(*) FILTER (WHERE regexp_matches(trim(cast(val AS VARCHAR)), '^[0-9]{1,4}[-/][0-9]{1,2}[-/][0-9]{1,4}'))"
        )
        stats = conn.execute(
            f"SELECT {non_empty_sql} AS non_empty, {num_ok_sql} AS num_ok, {date_ok_sql} AS date_ok, {date_pattern_sql} AS date_pattern FROM ({sample_sql}) s"
        ).fetchone()
        if not stats:
            continue
        non_empty = int(stats[0] or 0)
        if non_empty < 3:
            continue
        num_ratio = float(stats[1] or 0) / non_empty
        date_ratio = float(stats[2] or 0) / non_empty
        date_pattern_ratio = float(stats[3] or 0) / non_empty
        if date_pattern_ratio >= 0.5 and date_ratio >= 0.4:
            inferred[col] = "date"
        elif num_ratio >= confidence_threshold and num_ratio >= date_ratio:
            inferred[col] = "numeric"
        elif date_ratio >= confidence_threshold:
            inferred[col] = "date"
    return inferred


def _collect_adaptive_quality_metrics(
    conn: Any,
    *,
    table_name: str,
    adaptive_kinds: dict[str, str],
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for col, kind in adaptive_kinds.items():
        col_ref = f'"{col}"'
        non_empty_sql = f"count(*) FILTER (WHERE {col_ref} IS NOT NULL AND trim(cast({col_ref} AS VARCHAR)) != '')"
        if kind == "numeric":
            valid_sql = f"count(*) FILTER (WHERE TRY_CAST({_normalized_numeric_text_expr(col_ref)} AS DOUBLE) IS NOT NULL)"
        else:
            valid_sql = f"count(*) FILTER (WHERE {_date_parse_expr(col_ref)} IS NOT NULL)"
        row = conn.execute(
            f"SELECT {non_empty_sql} AS non_empty, {valid_sql} AS valid FROM {table_name}"
        ).fetchone()
        non_empty = int((row[0] if row else 0) or 0)
        valid = int((row[1] if row else 0) or 0)
        invalid = max(0, non_empty - valid)
        ratio = (valid / non_empty) if non_empty else 1.0
        metrics[col] = {
            "kind": kind,
            "non_empty": non_empty,
            "valid": valid,
            "invalid": invalid,
            "valid_ratio": round(ratio, 4),
        }
    return metrics


def _build_adaptive_issues_query(
    *,
    table_name: str,
    adaptive_kinds: dict[str, str],
) -> Optional[str]:
    parts: list[str] = []
    for col, kind in sorted(adaptive_kinds.items()):
        col_ref = f'"{col}"'
        not_empty = f"{col_ref} IS NOT NULL AND trim(cast({col_ref} AS VARCHAR)) != ''"
        if kind == "numeric":
            invalid_cond = f"TRY_CAST({_normalized_numeric_text_expr(col_ref)} AS DOUBLE) IS NULL"
            issue_type = "invalid_numeric"
        elif kind == "date":
            invalid_cond = f"{_date_parse_expr(col_ref)} IS NULL"
            issue_type = "invalid_date"
        else:
            continue
        parts.append(
            f"""
            SELECT
                __row_num AS row_number,
                {_sql_literal(col)} AS column_name,
                {_sql_literal(issue_type)} AS issue_type,
                cast({col_ref} AS VARCHAR) AS original_value
            FROM (
                SELECT row_number() OVER () AS __row_num, * FROM {table_name}
            ) s
            WHERE {not_empty} AND {invalid_cond}
            """
        )
    if not parts:
        return None
    return "\nUNION ALL\n".join(parts)


def _write_user_report(
    *,
    report_payload: dict[str, Any],
    report_path: str,
    report_format: str,
) -> None:
    fmt = str(report_format).lower()
    if fmt == "txt":
        summary = report_payload.get("summary", {})
        adaptive = report_payload.get("adaptive", {})
        lines = [
            f"Input File: {report_payload.get('input_file', '')}",
            f"Timestamp: {report_payload.get('timestamp', '')}",
            "",
            "Summary:",
            f"  Rows Input: {summary.get('rows_input', 0)}",
            f"  Rows Output: {summary.get('rows_output', 0)}",
            f"  Rows Removed: {summary.get('rows_removed', 0)}",
            "",
            "Adaptive:",
            f"  Enabled: {adaptive.get('enabled', False)}",
            f"  Strict Mode: {adaptive.get('strict_mode', False)}",
            "",
            "Column Quality:",
        ]
        q = adaptive.get("quality_metrics", {})
        if isinstance(q, dict):
            for col, metrics in sorted(q.items()):
                if isinstance(metrics, dict):
                    lines.append(
                        f"  - {col}: kind={metrics.get('kind','')}, valid={metrics.get('valid',0)}, "
                        f"invalid={metrics.get('invalid',0)}, valid_ratio={metrics.get('valid_ratio',0)}"
                    )
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return
    if fmt == "xlsx":
        import xlsxwriter

        pl = _load_polars()
        summary = report_payload.get("summary", {})
        adaptive = report_payload.get("adaptive", {})
        summary_rows = [
            {"metric": "input_file", "value": report_payload.get("input_file", "")},
            {"metric": "timestamp", "value": report_payload.get("timestamp", "")},
            {"metric": "rows_input", "value": summary.get("rows_input", 0)},
            {"metric": "rows_output", "value": summary.get("rows_output", 0)},
            {"metric": "rows_removed", "value": summary.get("rows_removed", 0)},
            {"metric": "issues_count", "value": summary.get("issues_count", 0)},
            {"metric": "adaptive_enabled", "value": adaptive.get("enabled", False)},
            {"metric": "strict_mode", "value": adaptive.get("strict_mode", False)},
        ]
        quality_rows: list[dict[str, Any]] = []
        q = adaptive.get("quality_metrics", {})
        if isinstance(q, dict):
            for col, metrics in sorted(q.items()):
                if isinstance(metrics, dict):
                    quality_rows.append(
                        {
                            "column": col,
                            "kind": metrics.get("kind", ""),
                            "non_empty": metrics.get("non_empty", 0),
                            "valid": metrics.get("valid", 0),
                            "invalid": metrics.get("invalid", 0),
                            "valid_ratio": metrics.get("valid_ratio", 0),
                        }
                    )
        qdf = (
            pl.DataFrame(quality_rows)
            if quality_rows
            else pl.DataFrame(
                schema={
                    "column": pl.String,
                    "kind": pl.String,
                    "non_empty": pl.Int64,
                    "valid": pl.Int64,
                    "invalid": pl.Int64,
                    "valid_ratio": pl.Float64,
                }
            )
        )
        wb = xlsxwriter.Workbook(str(report_path))
        try:
            pl.DataFrame(summary_rows).write_excel(wb, worksheet="Summary", include_header=True)
            qdf.write_excel(wb, worksheet="Column_Quality", include_header=True)
        finally:
            wb.close()
        return
    raise ValueError(f"Unsupported report format: {report_format}")


def _normalize_type_name(raw: str) -> str:
    key = str(raw).strip().lower()
    mapping = {
        "int": "BIGINT",
        "integer": "BIGINT",
        "bigint": "BIGINT",
        "float": "DOUBLE",
        "double": "DOUBLE",
        "number": "DOUBLE",
        "decimal": "DECIMAL(38,10)",
        "bool": "BOOLEAN",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "datetime": "TIMESTAMP",
        "timestamp": "TIMESTAMP",
        "str": "VARCHAR",
        "string": "VARCHAR",
        "text": "VARCHAR",
        "varchar": "VARCHAR",
    }
    if key not in mapping:
        raise ValueError(f"Unsupported type alias: {raw}")
    return mapping[key]


def _parse_column_type_overrides(raw: Optional[str]) -> dict[str, str]:
    if not raw:
        return {}
    result: dict[str, str] = {}
    for item in _split_csv_values(raw):
        if ":" not in item:
            raise ValueError(f"Invalid --column-types item: {item}. Use col:type")
        col, typ = item.split(":", 1)
        col_name = col.strip()
        if not col_name:
            raise ValueError(f"Invalid --column-types item: {item}. Empty column name.")
        result[col_name] = _normalize_type_name(typ)
    return result


def _load_raw_data_table(
    conn: Any,
    *,
    input_path: str,
    args: Any,
    source_label: str,
    use_chunked_scan: bool,
) -> None:
    def _replace_temp_source(select_sql: str) -> None:
        for drop_sql in ("DROP TABLE IF EXISTS raw_data", "DROP VIEW IF EXISTS raw_data"):
            try:
                conn.execute(drop_sql)
            except Exception:
                pass
        if use_chunked_scan:
            conn.execute(f"CREATE TEMP VIEW raw_data AS {select_sql}")
        else:
            conn.execute(f"CREATE TEMP TABLE raw_data AS {select_sql}")

    ext = os.path.splitext(input_path)[1].lower()
    print(f"   📥 Loading file from {source_label}: {input_path}")
    if ext in (".parquet", ".pq"):
        _replace_temp_source(f"SELECT * FROM read_parquet({_sql_literal(input_path)})")
        return
    raise ValueError(
        f"Unsupported input type: {ext}. This cleaner accepts only Parquet (.parquet, .pq)."
    )


def _normalize_chunking_mode(raw_mode: Any) -> str:
    mode = str(raw_mode or "auto").strip().lower()
    return mode if mode in {"auto", "on", "off"} else "auto"


def _should_use_chunked_scan(input_path: str, args: Any) -> bool:
    ext = os.path.splitext(input_path)[1].lower()
    if ext not in (".parquet", ".pq"):
        return False
    mode = _normalize_chunking_mode(getattr(args, "chunking_mode", "auto"))
    if mode == "on":
        return True
    if mode == "off":
        return False
    threshold_mb = max(1, int(getattr(args, "chunking_size_mb_threshold", 150) or 150))
    try:
        size_mb = os.path.getsize(input_path) / (1024 * 1024)
    except OSError:
        return False
    return size_mb >= threshold_mb


def _build_clean_select_query(
    conn: Any,
    *,
    table_name: str,
    original_filename: str,
    args: Any,
    source_label: str,
) -> tuple[str, dict[str, Any]]:
    print(f"   🔍 Inferring data types from {source_label}...")
    column_info = conn.execute(f"DESCRIBE {table_name}").fetchall()
    columns = [col[0] for col in column_info]
    column_types = {col[0]: col[1] for col in column_info}

    drop_cols = set(_split_csv_values(args.drop_cols))
    trim_cols = set(_split_csv_values(args.trim_cols))
    dedup_cols_scope = set(_split_csv_values(args.dedup_cols))
    empty_row_cols_scope = set(_split_csv_values(args.empty_row_cols))
    date_cols_scope = set(_split_csv_values(args.date_cols))
    type_overrides = _parse_column_type_overrides(args.column_types)
    nullify_values_raw = _split_csv_values(args.nullify_values)
    nullify_values_norm = sorted(set(_default_null_tokens() + [v.strip().lower() for v in nullify_values_raw if v.strip()]))

    existing_cols = set(columns)
    unknown_drop = sorted(drop_cols - existing_cols)
    unknown_trim = sorted(trim_cols - existing_cols)
    unknown_dedup = sorted(dedup_cols_scope - existing_cols)
    unknown_empty = sorted(empty_row_cols_scope - existing_cols)
    unknown_date = sorted(date_cols_scope - existing_cols)
    unknown_type_cols = sorted(set(type_overrides.keys()) - existing_cols)
    if unknown_drop:
        print(f"   ⚠️ drop-cols ignored (not found): {', '.join(unknown_drop)}")
    if unknown_trim:
        print(f"   ⚠️ trim-cols ignored (not found): {', '.join(unknown_trim)}")
    if unknown_dedup:
        print(f"   ⚠️ dedup-cols ignored (not found): {', '.join(unknown_dedup)}")
    if unknown_empty:
        print(f"   ⚠️ empty-row-cols ignored (not found): {', '.join(unknown_empty)}")
    if unknown_date:
        print(f"   ⚠️ date-cols ignored (not found): {', '.join(unknown_date)}")
    if unknown_type_cols:
        print(f"   ⚠️ column-types ignored (not found): {', '.join(unknown_type_cols)}")

    drop_cols = drop_cols & existing_cols
    trim_cols = trim_cols & existing_cols
    dedup_cols_scope = dedup_cols_scope & existing_cols
    empty_row_cols_scope = empty_row_cols_scope & existing_cols
    date_cols_scope = date_cols_scope & existing_cols
    type_overrides = {k: v for k, v in type_overrides.items() if k in existing_cols}

    output_columns = [c for c in columns if c not in drop_cols]
    if not output_columns:
        raise ValueError("All columns were dropped via --drop-cols. Keep at least one column.")

    cleaned_cols: list[str] = []
    transformed_string_cols: list[str] = []
    date_cols: list[str] = []
    adaptive_clean = bool(getattr(args, "adaptive_clean", True))
    adaptive_kinds: dict[str, str] = {}
    if adaptive_clean:
        adaptive_kinds = _infer_adaptive_column_kinds(
            conn,
            table_name=table_name,
            columns=output_columns,
            column_types=column_types,
        )

    for col in columns:
        if col in drop_cols:
            continue

        source_expr = f'"{col}"'
        typed_expr = source_expr
        if col in type_overrides:
            typed_expr = f"TRY_CAST({source_expr} AS {type_overrides[col]})"
        effective_type = str(type_overrides.get(col, column_types[col])).lower()

        col_expr = f"{typed_expr} AS \"{col}\""
        if "varchar" in effective_type or "string" in effective_type:
            should_transform = (not trim_cols) or (col in trim_cols)
            if should_transform:
                inner = typed_expr
                if args.strip:
                    inner = f"trim({inner})"
                if args.clean_control:
                    inner = f"regexp_replace({inner}, '[\\x00-\\x1F\\x7F]+', '')"
                if args.whitespace:
                    inner = f"regexp_replace({inner}, '\\s+', ' ', 'g')"
                if args.lowercase:
                    inner = f"lower({inner})"
                if args.uppercase:
                    inner = f"upper({inner})"
                if nullify_values_norm:
                    nullify_set = ", ".join(_sql_literal(v) for v in nullify_values_norm)
                    inner = (
                        f"CASE WHEN lower(trim(cast({inner} AS VARCHAR))) IN ({nullify_set}) "
                        f"THEN NULL ELSE {inner} END"
                    )
                adaptive_kind = adaptive_kinds.get(col)
                if adaptive_kind == "numeric":
                    parsed_num = f"TRY_CAST({_normalized_numeric_text_expr(inner)} AS DOUBLE)"
                    inner = (
                        f"CASE WHEN {inner} IS NULL THEN NULL "
                        f"WHEN {parsed_num} IS NOT NULL THEN cast({parsed_num} AS VARCHAR) "
                        f"ELSE {inner} END"
                    )
                elif adaptive_kind == "date":
                    parsed_date = _date_parse_expr(inner)
                    inner = (
                        f"CASE WHEN {inner} IS NULL THEN NULL "
                        f"WHEN {parsed_date} IS NOT NULL THEN strftime({parsed_date}, '%Y-%m-%d') "
                        f"ELSE {inner} END"
                    )
                if args.strip or args.clean_control or args.whitespace or args.lowercase or args.uppercase or nullify_values_norm:
                    col_expr = (
                        f"CASE WHEN {typed_expr} IS NULL OR trim(cast({typed_expr} AS VARCHAR)) = '' "
                        f"THEN NULL ELSE {inner} END AS \"{col}\""
                    )
                    transformed_string_cols.append(col)
                elif adaptive_kind in {"numeric", "date"}:
                    col_expr = f"{inner} AS \"{col}\""
                    transformed_string_cols.append(col)
        elif "date" in effective_type and args.date_format:
            if not date_cols_scope or col in date_cols_scope:
                date_cols.append(col)
                if args.date_input_format:
                    parsed_date_expr = f"TRY_STRPTIME(cast({source_expr} AS VARCHAR), {_sql_literal(args.date_input_format)})"
                else:
                    parsed_date_expr = f"TRY_CAST({typed_expr} AS DATE)"
                col_expr = f"""strftime({parsed_date_expr}, '%d/%m/%Y') AS "{col}" """
        cleaned_cols.append(col_expr)

    if transformed_string_cols:
        steps: list[str] = []
        if args.strip:
            steps.append("trim")
        if args.clean_control:
            steps.append("remove control chars")
        if args.whitespace:
            steps.append("collapse spaces")
        if args.lowercase:
            steps.append("lowercase")
        if args.uppercase:
            steps.append("uppercase")
        if nullify_values_norm:
            steps.append("nullify values")
        if adaptive_clean and adaptive_kinds:
            steps.append("adaptive numeric/date normalize")
        print(f"   ✂️  Applying string cleaning ({', '.join(steps)}) to: {', '.join(transformed_string_cols)}")
    if date_cols:
        print(f"   📅 Formatting date columns to DD/MM/YYYY: {', '.join(date_cols)}")
    if drop_cols:
        print(f"   🗑️  Dropping columns: {', '.join(sorted(drop_cols))}")
    if type_overrides:
        typed_msg = ", ".join(f"{col}:{typ}" for col, typ in sorted(type_overrides.items()))
        print(f"   🧾 Applying type overrides: {typed_msg}")
    if adaptive_clean and adaptive_kinds:
        adaptive_summary = ", ".join(f"{c}:{k}" for c, k in sorted(adaptive_kinds.items()))
        print(f"   🧠 Adaptive detection: {adaptive_summary}")

    where_clause = "TRUE"
    if args.empty_rows:
        # Same blank-row definition as shared_lib data_checker (blank_rows_union on per-column exprs).
        br_exprs: list[str] = []
        empties_target_cols = [c for c in output_columns if (not empty_row_cols_scope or c in empty_row_cols_scope)]
        for col in empties_target_cols:
            if col == "x_ip_id":
                continue
            base_expr = f'"{col}"'
            if col in type_overrides:
                base_expr = f"TRY_CAST({base_expr} AS {type_overrides[col]})"
            br_exprs.append(base_expr)
        if br_exprs:
            where_clause = keep_row_if_not_blank_sql_exprs(br_exprs)
            print("   🧹 Removing blank rows (shared_lib keep_row_if_not_blank_sql_exprs)")

    dedup_clause = ""
    if args.dedup:
        if args.de_duplicate_key:
            dedup_key = [col.strip() for col in args.de_duplicate_key.split(",") if col.strip()]
            missing_dedup = [c for c in dedup_key if c not in output_columns]
            if missing_dedup:
                raise ValueError(
                    "de-duplicate key column(s) missing after drop/selection: " + ", ".join(missing_dedup)
                )
            print(f"   🔁 Deduplicating using key columns: {', '.join(dedup_key)}")
        elif dedup_cols_scope:
            dedup_key = [c for c in output_columns if c in dedup_cols_scope]
            if not dedup_key:
                raise ValueError("No valid columns left in --dedup-cols after selection/drop.")
            print(f"   🔁 Deduplicating using dedup-cols: {', '.join(dedup_key)}")
        else:
            dedup_key = [col for col in output_columns if col.lower() not in ("x_ip_id",)]
            print(f"   🔁 Removing exact duplicate rows (excluding x_ip_id): {', '.join(dedup_key)}")
        dedup_partition = dedupe_partition_expressions(dedup_key)
        order_col = '"x_ip_id"' if "x_ip_id" in columns else ", ".join(f'"{c}"' for c in dedup_key)
        dedup_clause = (
            f"SELECT * FROM cleaned_base "
            f"QUALIFY row_number() OVER (PARTITION BY {dedup_partition} ORDER BY {order_col}) = 1"
        )

    metadata_prefix = ""
    if args.meta_cols == "yes":
        metadata_prefix = (
            f"row_number() OVER () AS x_id, {_sql_literal(original_filename)} AS x_source_file, "
        )
        print("   ➕ Adding final metadata columns: x_id, x_source_file")

    cleaned_projection = ", ".join(cleaned_cols)
    final_source = dedup_clause if dedup_clause else "SELECT * FROM cleaned_base"
    query = f"""
        WITH cleaned_base AS (
            SELECT {cleaned_projection}
            FROM {table_name}
            WHERE {where_clause}
        ),
        final_rows AS (
            {final_source}
        )
        SELECT {metadata_prefix} final_rows.*
        FROM final_rows
    """
    return query, {
        "adaptive_kinds": adaptive_kinds,
        "null_tokens": nullify_values_norm,
    }


def _export_clean_table(
    conn: Any,
    *,
    table_name: str,
    output_path: str,
    output_format: str,
    args: Any,
    source_sql: Optional[str] = None,
) -> bool:
    try:
        select_sql = source_sql or f"SELECT * FROM {table_name}"
        if output_format == "csv":
            delim = _csv_delimiter(args)
            conn.execute(
                f"COPY ({select_sql}) TO {_sql_literal(output_path)} "
                f"(FORMAT CSV, HEADER TRUE, DELIMITER {_sql_literal(delim)})"
            )
        elif output_format == "parquet":
            conn.execute(f"COPY ({select_sql}) TO {_sql_literal(output_path)} (FORMAT PARQUET)")
        elif output_format == "xlsx":
            df = _duckdb_sql_to_polars(conn, select_sql)
            df.write_excel(str(output_path), worksheet="Sheet1", include_header=True)
        else:
            raise ValueError(f"Unsupported output format: {output_format}")
        print(f"   💾 Writing output to: {output_path}")
        return os.path.exists(output_path)
    except Exception as e:  # noqa: BLE001
        print(f"   ❌ {output_format.upper()} export failed: {e}")
        return False


def process_single_file(input_path: str, args: Any, script_name: str) -> dict:
    """
    Coordinates the processing of a single input file, handling intermediate files and output formats.
    """
    ts = timestamp_compact()
    
    if args.output_dir in (None, ""):
        outdir = str((Path(input_path).parent / "cleaned_output").resolve())
    else:
        outdir = str(Path(str(args.output_dir)).expanduser().resolve())
    
    ext = os.path.splitext(input_path)[1].lower()

    results = {}
    actual_input_path = input_path
    intermediate_path = None

    try:
        # --- PHASE 1: Indexing and Intermediate File Creation (Hybrid Polars Step) ---
        if args.meta_cols == 'yes':
            # ***FIXED: Pass script_name to the intermediate file generator.***
            intermediate_path = inject_ip_id_column(input_path, outdir, args.delimiter, args.intermediate_format, script_name)
            actual_input_path = intermediate_path
            ext = os.path.splitext(intermediate_path)[1].lower()

            if args.keep_intermediate:
                print(f"   ℹ️  Intermediate file kept at: {intermediate_path}")

        # --- PHASE 2: Single-pass DuckDB clean + vectorized multi-format export ---
        source_label = "Intermediate file" if args.meta_cols == "yes" else "Parquet input"
        original_filename = os.path.basename(input_path)
        use_chunked_scan = _should_use_chunked_scan(actual_input_path, args)
        if use_chunked_scan:
            print(
                f"   ⚙️  Chunked scan enabled (mode={_normalize_chunking_mode(args.chunking_mode)}, "
                f"threshold_mb={int(args.chunking_size_mb_threshold)})"
            )

        with _load_duckdb().connect() as conn:
            _load_raw_data_table(
                conn,
                input_path=actual_input_path,
                args=args,
                source_label=source_label,
                use_chunked_scan=use_chunked_scan,
            )
            clean_query, plan_meta = _build_clean_select_query(
                conn,
                table_name="raw_data",
                original_filename=original_filename,
                args=args,
                source_label=source_label,
            )
            raw_count = int(conn.execute("SELECT count(*) FROM raw_data").fetchone()[0] or 0)
            cleaned_count = int(conn.execute(f"SELECT count(*) FROM ({clean_query}) q").fetchone()[0] or 0)
            removed_rows = max(0, raw_count - cleaned_count)
            adaptive_kinds: dict[str, str] = dict(plan_meta.get("adaptive_kinds", {}))
            quality_metrics = _collect_adaptive_quality_metrics(
                conn,
                table_name="raw_data",
                adaptive_kinds=adaptive_kinds,
            )
            issues_query = _build_adaptive_issues_query(
                table_name="raw_data",
                adaptive_kinds=adaptive_kinds,
            )
            issues_count = 0
            issues_csv_path: Optional[str] = None
            issues_xlsx_path: Optional[str] = None
            if issues_query:
                issues_count = int(conn.execute(f"SELECT count(*) FROM ({issues_query}) issues").fetchone()[0] or 0)
                if issues_count > 0:
                    issues_csv_path = os.path.join(outdir, f"{script_name}_issues_{ts}.csv")
                    conn.execute(
                        f"COPY ({issues_query}) TO {_sql_literal(issues_csv_path)} "
                        f"(FORMAT CSV, HEADER TRUE, DELIMITER ',')"
                    )
                    issues_xlsx_path = os.path.join(outdir, f"{script_name}_issues_{ts}.xlsx")
                    _duckdb_sql_to_polars(conn, issues_query).write_excel(
                        issues_xlsx_path,
                        worksheet="Sheet1",
                        include_header=True,
                    )

            # Keep user format order and remove duplicates.
            output_formats = list(dict.fromkeys(args.output_formats))
            source_sql: Optional[str] = None
            table_name = "cleaned_output"
            strict_bad_cols = [c for c, m in quality_metrics.items() if int(m.get("invalid", 0)) > 0]
            strict_error: Optional[str] = None
            if bool(getattr(args, "strict_mode", False)) and strict_bad_cols:
                strict_error = (
                    "strict_mode failed due to invalid adaptive values in columns: "
                    + ", ".join(sorted(strict_bad_cols))
                )
            else:
                if use_chunked_scan:
                    source_sql = clean_query
                else:
                    conn.execute(f"CREATE TEMP TABLE cleaned_output AS {clean_query}")
                for fmt in output_formats:
                    output_filename = f"{script_name}_cleaned_{ts}.{fmt}"
                    output_path = os.path.join(outdir, output_filename)
                    os.makedirs(outdir, exist_ok=True)
                    success = _export_clean_table(
                        conn,
                        table_name=table_name,
                        output_path=output_path,
                        output_format=fmt,
                        args=args,
                        source_sql=source_sql,
                    )
                    if success:
                        results[fmt] = output_path
                    else:
                        results[fmt] = f"Error: {fmt} processing failed"

            report_payload = {
                "input_file": input_path,
                "timestamp": ts,
                "summary": {
                    "rows_input": raw_count,
                    "rows_output": cleaned_count,
                    "rows_removed": removed_rows,
                    "issues_count": issues_count,
                },
                "adaptive": {
                    "enabled": bool(getattr(args, "adaptive_clean", True)),
                    "strict_mode": bool(getattr(args, "strict_mode", False)),
                    "detected_columns": adaptive_kinds,
                    "quality_metrics": quality_metrics,
                },
                "null_standardization_tokens": list(plan_meta.get("null_tokens", [])),
                "outputs": {k: v for k, v in results.items() if isinstance(v, str) and not v.startswith("Error:")},
                "issues_files": {
                    "csv": issues_csv_path,
                    "xlsx": issues_xlsx_path,
                },
            }
            report_fmt = str(getattr(args, "report_format", "xlsx")).lower()
            user_report_file = os.path.join(outdir, f"{script_name}_report_{ts}.{report_fmt}")
            _write_user_report(
                report_payload=report_payload,
                report_path=user_report_file,
                report_format=report_fmt,
            )
            results["report"] = user_report_file
            if issues_csv_path:
                results["issues_csv"] = issues_csv_path
            if issues_xlsx_path:
                results["issues_xlsx"] = issues_xlsx_path
            if strict_error:
                results["error"] = strict_error
                return results

    except Exception as e:
        results['error'] = str(e)
    finally:
        # --- PHASE 3: Cleanup ---
        if intermediate_path and os.path.exists(intermediate_path) and not args.keep_intermediate: 
            try:
                os.remove(intermediate_path)
                print(f"   🗑️  Cleaned up intermediate file: {intermediate_path}")
            except OSError as e:
                print(f"   ⚠️ Could not remove intermediate file: {e}")
            
    return results


