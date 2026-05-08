from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from file_cleaner import __version__
from file_cleaner.clean_core import run_clean

from shared_lib.cli_typer import JsonOpt, effective_quiet, finish_cli_shared_result
from shared_lib.cli_token_lists import parse_comma_separated_cli_tokens

_APP_HELP = """\
Clean and normalize one or more Parquet tables (trim text, dedupe, optional reports).

\b• Purpose: Fix spaces, junk characters, duplicate rows, empty rows so files are safer downstream.
• Input: One or more Parquet files (.parquet or .pq): paths, globs, or JSON manifest.
• Output: Cleaned CSV/Parquet/Excel in the folder you set or beside the first input; optional reports.
Use the clean command: file-cleaner clean --help
"""

app = typer.Typer(
    name="file-cleaner",
    help=_APP_HELP,
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"file-cleaner {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """See file-cleaner --help for purpose, input, and output."""


@app.command(
    "clean",
    epilog=(
        "Input must be Parquet (.parquet / .pq). Without -o/--output-directory, outputs go in the same directory "
        "as the first matched input (like file-format-converter beside-input). With -o, files are "
        "file_cleaner_cleaned_<timestamp>.<ext> plus optional reports."
    ),
)
def clean_cmd(
    output_directory: Annotated[
        Optional[Path],
        typer.Option(
            "-o",
            "--output-directory",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Directory for cleaned outputs and reports (created if needed). "
            "If omitted, uses the parent folder of the first resolved -i/--input path.",
        ),
    ] = None,
    input_pattern: Annotated[
        list[str],
        typer.Option(
            "-i",
            "--input",
            help="Input Parquet path or glob (repeatable, or comma-separated on one -i). Only .parquet / .pq. "
            "Use with --input-manifest or pass at least one -i.",
        ),
    ] = [],
    input_manifest: Annotated[
        Optional[Path],
        typer.Option(
            "--input-manifest",
            help="Optional JSON manifest; only .parquet / .pq artifact paths are used.",
        ),
    ] = None,
    output_format: Annotated[
        list[str],
        typer.Option(
            "-O",
            "--output-format",
            help="Output format (repeatable). Default: csv.",
            click_type=click.Choice(["csv", "parquet", "xlsx"], case_sensitive=False),
        ),
    ] = [],
    delimiter: Annotated[
        Optional[str],
        typer.Option("--delimiter", help="CSV output delimiter when writing CSV."),
    ] = None,
    strict_delimiter: Annotated[
        bool,
        typer.Option("--strict-delimiter/--no-strict-delimiter", help="Reserved for CSV tooling."),
    ] = False,
    meta_cols: Annotated[
        str,
        typer.Option(
            "--meta-cols",
            help="Add x_ip_id / x_id / x_source_file pipeline.",
            click_type=click.Choice(["yes", "no"], case_sensitive=False),
        ),
    ] = "yes",
    deduplicate: Annotated[bool, typer.Option("-d", "--deduplicate", help="Deduplicate rows.")] = False,
    strip: Annotated[bool, typer.Option("-s", "--strip", help="Trim string whitespace.")] = False,
    clean_control: Annotated[
        bool,
        typer.Option("-c", "--clean-control", help="Remove control characters from strings."),
    ] = False,
    whitespace: Annotated[
        bool,
        typer.Option("-w", "--whitespace", help="Collapse runs of whitespace."),
    ] = False,
    lowercase: Annotated[bool, typer.Option("-l", "--lowercase", help="Lowercase strings.")] = False,
    uppercase: Annotated[bool, typer.Option("-u", "--uppercase", help="Uppercase strings.")] = False,
    empty_rows: Annotated[
        bool,
        typer.Option("-e", "--empty-rows", help="Drop completely empty rows."),
    ] = False,
    date_format: Annotated[
        bool,
        typer.Option("-t", "--date-format", help="Format date columns as DD/MM/YYYY."),
    ] = False,
    trim_cols: Annotated[
        Optional[str],
        typer.Option(
            "--trim-cols",
            help="Comma-separated column names. With --strip, only these columns are trimmed; "
            "omit to trim every string column.",
        ),
    ] = None,
    drop_cols: Annotated[
        Optional[str],
        typer.Option(
            "--drop-cols",
            help="Comma-separated columns to remove from the cleaned output.",
        ),
    ] = None,
    nullify_values: Annotated[
        Optional[str],
        typer.Option(
            "--nullify-values",
            help="Comma-separated extra tokens to treat as NULL (case-insensitive), in addition to built-in "
            "tokens like na, n/a, null.",
        ),
    ] = None,
    column_types: Annotated[
        Optional[str],
        typer.Option(
            "--column-types",
            help='Force DuckDB types for columns: comma-separated col:type items (e.g. "amount:decimal,id:bigint").',
        ),
    ] = None,
    dedup_cols: Annotated[
        Optional[str],
        typer.Option(
            "--dedup-cols",
            help="With --deduplicate and without -k/--de-duplicate-key: comma-separated columns that define "
            "duplicate rows (partition key).",
        ),
    ] = None,
    empty_row_cols: Annotated[
        Optional[str],
        typer.Option(
            "--empty-row-cols",
            help="With --empty-rows: comma-separated columns used to decide if a row is empty. "
            "Omit to consider all output columns (except x_ip_id in the predicate).",
        ),
    ] = None,
    date_cols: Annotated[
        Optional[str],
        typer.Option(
            "--date-cols",
            help="Comma-separated columns to include when --date-format rewrites dates to DD/MM/YYYY; "
            "omit to apply to every date-like column.",
        ),
    ] = None,
    date_input_format: Annotated[
        Optional[str],
        typer.Option(
            "--date-input-format",
            help="Strptime pattern for parsing source dates before DD/MM/YYYY output (e.g. %Y-%m-%d). "
            "When set, used instead of TRY_CAST(... AS DATE) for those columns.",
        ),
    ] = None,
    de_duplicate_key: Annotated[
        Optional[str],
        typer.Option(
            "-k",
            "--de-duplicate-key",
            help="With --deduplicate: explicit comma-separated key columns (overrides --dedup-cols default).",
        ),
    ] = None,
    keep_intermediate: Annotated[
        bool,
        typer.Option(
            "--keep-intermediate/--no-keep-intermediate",
            help="When --meta-cols=yes, keep the intermediate file with x_ip_id instead of deleting it.",
        ),
    ] = False,
    intermediate_format: Annotated[
        str,
        typer.Option(
            "--intermediate-format",
            help="File format for the intermediate table when --meta-cols=yes.",
            click_type=click.Choice(["csv", "parquet"], case_sensitive=False),
        ),
    ] = "parquet",
    adaptive_clean: Annotated[
        bool,
        typer.Option(
            "--adaptive-clean/--no-adaptive-clean",
            help="Infer numeric/date-like text columns and normalize where confidence is high.",
        ),
    ] = True,
    strict_mode: Annotated[
        bool,
        typer.Option(
            "--strict-mode/--no-strict-mode",
            help="Fail the run if adaptive parsing leaves invalid values in any column.",
        ),
    ] = False,
    report_format: Annotated[
        str,
        typer.Option(
            "--report-format",
            help="User-facing quality summary: Excel or plain text.",
            click_type=click.Choice(["xlsx", "txt"], case_sensitive=False),
        ),
    ] = "xlsx",
    chunking_mode: Annotated[
        str,
        typer.Option(
            "--chunking-mode",
            help="Large Parquet: auto enables chunked DuckDB view from --chunking-size-mb-threshold MB; "
            "on always chunks; off uses one DuckDB relation. Heavy work stays DuckDB+Polars.",
            click_type=click.Choice(["auto", "on", "off"], case_sensitive=False),
        ),
    ] = "auto",
    chunk_size_rows: Annotated[
        int,
        typer.Option("--chunk-size-rows", min=1, help="Rows per chunk when chunking is enabled."),
    ] = 100_000,
    chunking_size_mb_threshold: Annotated[
        int,
        typer.Option(
            "--chunking-size-mb-threshold",
            min=1,
            help="Input file size (MB) at or above which --chunking-mode=auto enables chunked scan.",
        ),
    ] = 150,
    allow_empty_match: Annotated[
        bool,
        typer.Option(
            "--allow-empty-match/--no-allow-empty-match",
            help="Allow input globs or manifests that resolve to zero files (default: error).",
        ),
    ] = False,
    max_files: Annotated[
        Optional[int],
        typer.Option("--max-files", min=1, help="Cap the number of resolved input files after expansion."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("-q", "--quiet", help="Suppress success line (ignored with --json)."),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """\
Clean Parquet inputs into tidier table files.

\b• Purpose: Trimming, deduping, dropping bad rows, etc.; new files unless you overwrite on purpose.
• Input: Parquet only; repeat -i/--input or use --input-manifest.
• Output: One cleaned file per input under -o or beside the first -i (formats you pick).
"""
    flat_fmt = parse_comma_separated_cli_tokens(output_format) if output_format else []
    formats = list(dict.fromkeys(flat_fmt)) if flat_fmt else ["csv"]
    ns = SimpleNamespace(
        input_patterns=input_pattern,
        input_manifest=input_manifest,
        output_directory=output_directory,
        output_dir=None,
        output_formats=formats,
        delimiter=delimiter,
        strict_delimiter=strict_delimiter,
        meta_cols=meta_cols,
        dedup=deduplicate,
        strip=strip,
        clean_control=clean_control,
        whitespace=whitespace,
        lowercase=lowercase,
        uppercase=uppercase,
        empty_rows=empty_rows,
        date_format=date_format,
        trim_cols=trim_cols,
        drop_cols=drop_cols,
        nullify_values=nullify_values,
        column_types=column_types,
        dedup_cols=dedup_cols,
        empty_row_cols=empty_row_cols,
        date_cols=date_cols,
        date_input_format=date_input_format,
        de_duplicate_key=de_duplicate_key,
        keep_intermediate=keep_intermediate,
        intermediate_format=intermediate_format,
        adaptive_clean=adaptive_clean,
        strict_mode=strict_mode,
        report_format=report_format,
        chunking_mode=chunking_mode,
        chunk_size_rows=chunk_size_rows,
        chunking_size_mb_threshold=chunking_size_mb_threshold,
        allow_empty_match=allow_empty_match,
        max_files=max_files,
    )
    result = run_clean(ns)
    finish_cli_shared_result(result, json_out=json_out, quiet=effective_quiet(quiet=quiet, json_out=json_out))


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv)
        return 0
    except SystemExit as e:
        code = e.code
        if code is None or code is False:
            return 0
        if isinstance(code, int):
            return code
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
