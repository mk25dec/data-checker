"""Build clean_engine.py from old file_cleaner.py (no argparse main, no shared_utils)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_OLD = ROOT.parent / "old/file-cleaner/src/file_cleaner/file_cleaner.py"
DST = ROOT / "src/file_cleaner/clean_engine.py"

NEW_TOP = '''from __future__ import annotations

import concurrent.futures
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence

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

'''

CONST_BLOCK = (
    "# Global regex patterns for cleaning steps (kept from original)\n"
    r'CONTROL_CHAR_RE = re.compile(r"[\x00-\x1F\x7F]+")' "\n"
    r'MULTI_SPACE_RE = re.compile(r"\s+")' "\n"
    "\n"
    'APP_NAME = "file_cleaner"\n'
    "DEFAULT_INPUT_MAX_FILES_GUARD = 5000\n"
    "\n"
    "HUGE_FILE_MODE_THRESHOLD_MB = 1024\n"
    "HUGE_FILE_MODE_CHUNK_ROWS = 50000\n"
    'SUPPORTED_INPUT_SUFFIXES = frozenset({".parquet", ".pq"})\n'
    'SUPPORTED_INPUT_FORMATS = ("parquet",)\n'
    "\n"
)


def main() -> None:
    lines = SRC_OLD.read_text(encoding="utf-8").splitlines(True)

    def find_prefix(prefix: str) -> int:
        for i, ln in enumerate(lines):
            if ln.startswith(prefix):
                return i
        raise SystemExit(f"missing {prefix!r}")

    i_extract = find_prefix("def _extract_config_section")
    i_sql = find_prefix("def _sql_literal")
    i_main = find_prefix("def main(")

    body = "".join(lines[:i_extract] + lines[i_sql:i_main])
    n0 = body.find("def _normalize_inputs_with_converter")
    n1 = body.find("def _default_null_tokens")
    if n0 != -1 and n1 != -1 and n0 < n1:
        body = body[:n0] + body[n1:]

    fr = body.find("def _resolve_input_paths")
    if fr == -1:
        raise SystemExit("def _resolve_input_paths not found")
    body = body[fr:]

    text = NEW_TOP + CONST_BLOCK + body

    text = text.replace("timestamp_filename()", "timestamp_compact()")
    text = text.replace("argparse.Namespace", "Any")
    text = text.replace(
        """    if args.output_dir in (None, ""):
        outdir = resolve_output_dir(
            configured_output_dir=None,
            input_files=[input_path],
            app_name="file_cleaner",
        )
    else:
        outdir_raw = Path(str(args.output_dir)).expanduser()
        outdir = str((CODE_ROOT / outdir_raw).resolve()) if not outdir_raw.is_absolute() else str(outdir_raw.resolve())""",
        """    if args.output_dir in (None, ""):
        outdir = str((Path(input_path).parent / "cleaned_output").resolve())
    else:
        outdir = str(Path(str(args.output_dir)).expanduser().resolve())""",
    )
    text = text.replace("with duckdb.connect() as conn:", "with _load_duckdb().connect() as conn:")
    text = text.replace(
        "        ext = os.path.splitext(input_path)[1].lower()\n"
        '        if ext in (".parquet", ".pq"):\n'
        "            pf = pl.read_parquet",
        "        ext = os.path.splitext(input_path)[1].lower()\n"
        '        if ext in (".parquet", ".pq"):\n'
        "            pl = _load_polars()\n"
        "            pf = pl.read_parquet",
    )
    text = text.replace("conn: duckdb.DuckDBPyConnection", "conn: Any")

    # XLSX: Polars + xlsxwriter (no pandas)
    text = text.replace(
        '    if fmt == "xlsx":\n'
        '        if "pandas" not in sys.modules:\n'
        '            raise ImportError("pandas and xlsxwriter are required for XLSX report output.")\n'
        '        summary = report_payload.get("summary", {})\n',
        '    if fmt == "xlsx":\n'
        '        import xlsxwriter\n'
        '\n'
        '        pl = _load_polars()\n'
        '        summary = report_payload.get("summary", {})\n',
    )
    text = text.replace(
        '        with pd.ExcelWriter(report_path, engine="xlsxwriter") as writer:\n'
        '            pd.DataFrame(summary_rows).to_excel(writer, sheet_name="Summary", index=False)\n'
        '            pd.DataFrame(quality_rows).to_excel(writer, sheet_name="Column_Quality", index=False)\n',
        '        qdf = (\n'
        '            pl.DataFrame(quality_rows)\n'
        '            if quality_rows\n'
        '            else pl.DataFrame(\n'
        '                schema={\n'
        '                    "column": pl.String,\n'
        '                    "kind": pl.String,\n'
        '                    "non_empty": pl.Int64,\n'
        '                    "valid": pl.Int64,\n'
        '                    "invalid": pl.Int64,\n'
        '                    "valid_ratio": pl.Float64,\n'
        '                }\n'
        '            )\n'
        '        )\n'
        '        wb = xlsxwriter.Workbook(str(report_path))\n'
        '        try:\n'
        '            pl.DataFrame(summary_rows).write_excel(wb, worksheet="Summary", include_header=True)\n'
        '            qdf.write_excel(wb, worksheet="Column_Quality", include_header=True)\n'
        '        finally:\n'
        '            wb.close()\n',
    )
    text = text.replace(
        '        elif output_format == "xlsx":\n'
        '            if "pandas" not in sys.modules:\n'
        '                raise ImportError("pandas and xlsxwriter are required for XLSX output.")\n'
        '            df = conn.execute(select_sql).fetchdf()\n'
        '            df.to_excel(output_path, index=False, engine="xlsxwriter")\n',
        '        elif output_format == "xlsx":\n'
        '            df = _duckdb_sql_to_polars(conn, select_sql)\n'
        '            df.write_excel(str(output_path), worksheet="Sheet1", include_header=True)\n',
    )
    text = text.replace(
        '                    if "pandas" in sys.modules:\n'
        '                        issues_xlsx_path = os.path.join(outdir, f"{script_name}_issues_{ts}.xlsx")\n'
        '                        conn.execute(issues_query).fetchdf().to_excel(issues_xlsx_path, index=False, engine="xlsxwriter")\n',
        '                    issues_xlsx_path = os.path.join(outdir, f"{script_name}_issues_{ts}.xlsx")\n'
        '                    _duckdb_sql_to_polars(conn, issues_query).write_excel(\n'
        '                        issues_xlsx_path,\n'
        '                        worksheet="Sheet1",\n'
        '                        include_header=True,\n'
        '                    )\n',
    )

    if "def _load_optional_toml_root" in text:
        start = text.index("def _load_optional_toml_root")
        rest = text[start:]
        m = rest.find("\ndef ", 1)
        if m == -1:
            raise SystemExit("could not end-slice _load_optional")
        end = start + m
        text = text[:start] + text[end:]

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(text, encoding="utf-8")
    print("Wrote", DST, "lines", text.count("\n") + 1)


if __name__ == "__main__":
    main()
