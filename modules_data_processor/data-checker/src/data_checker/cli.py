from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from data_checker import __version__
from data_checker.check_core import run_data_check

from shared_lib.cli_typer import JsonOpt, effective_quiet, finish_cli_shared_result
from shared_lib.cli_token_lists import resolve_comma_or_json_cli_list

_APP_HELP = """\
Summarize one Parquet table: row counts, blanks, and duplicate patterns.

\b• Purpose: Produce a small report on how complete and unique your data is before sharing or loading it elsewhere.
• Input: One Parquet file (.parquet or .pq); optional key column(s) for duplicate-on-key stats.
• Output: A metrics file (CSV or Parquet), usually beside the input; optional export of flagged rows.
Use the check command: data-checker check --help
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"data-checker {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="data-checker",
    help=_APP_HELP,
    no_args_is_help=True,
)


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
    """See data-checker --help for purpose, input, and output."""


@app.command(
    "check",
    epilog=(
        "Input must be Parquet (.parquet / .pq). Key columns: repeat -k, comma-separated on one -k, or "
        "--key-columns-json. Metrics: row_count, column_count, blank_rows "
        "(all-null / all-blankish / whitespace-only rows, one count), full-row distinct vs duplicate "
        "surplus, and with -k: unique_key_count vs duplicate_rows_on_keys (rows beyond one per key). "
        "By default the report is written beside the input as <stem>_row_audit.csv "
        "(or .parquet with --report-format parquet). Use -r/--report-file to choose another path. "
        "With -dr/--data-report, also write <stem>_data_report.csv (__tag__, __key__, then data)."
    ),
)
def check_cmd(
    input_file: Annotated[
        Path,
        typer.Option(
            "-i",
            "--input-file",
            resolve_path=True,
            help="Input Parquet file path.",
        ),
    ],
    key_column: Annotated[
        list[str],
        typer.Option(
            "-k",
            "--key-column",
            help="Key column name(s) for duplicate stats: repeat -k or comma-separated (e.g. -k 'id,region').",
        ),
    ] = [],
    key_columns_json: Annotated[
        Optional[str],
        typer.Option(
            "--key-columns-json",
            help='JSON array of key column names, e.g. \'["id","region"]\'. Mutually exclusive with -k.',
        ),
    ] = None,
    report_file: Annotated[
        Optional[Path],
        typer.Option(
            "-r",
            "--report-file",
            resolve_path=True,
            help="Report output path. If omitted, writes <input_stem>_row_audit.csv next to the input "
            "(or .parquet when --report-format parquet).",
        ),
    ] = None,
    report_format: Annotated[
        Optional[str],
        typer.Option(
            "--report-format",
            help="csv or parquet: sets format and default file extension beside the input when -r is omitted.",
            click_type=click.Choice(["csv", "parquet"], case_sensitive=False),
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("-w", "--overwrite", help="Replace the report file if it already exists."),
    ] = False,
    data_report: Annotated[
        bool,
        typer.Option(
            "-dr",
            "--data-report",
            help=(
                "Also write <input_stem>_data_report.csv beside the input: columns __tag__ "
                "(blank | duplicate | empty), __key__ (-k values joined by |, or all columns if no -k), "
                "then all data columns."
            ),
        ),
    ] = False,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Suppress success line (ignored with --json).")] = False,
    json_out: JsonOpt = False,
) -> None:
    """\
Summarize one Parquet table for data quality.

\b• Purpose: Count rows/columns, spot blank-like rows, and see duplicates (full row and, with -k, on keys).
• Input: One Parquet file (.parquet or .pq); optional key columns (-k and/or --key-columns-json).
• Output: A metrics report (CSV or Parquet), usually beside the input unless -r/--report-file.
"""
    key_res = resolve_comma_or_json_cli_list(
        key_column,
        key_columns_json,
        mutual_exclusive=True,
        comma_option_desc="-k/--key-column",
        json_option_desc="--key-columns-json",
        data_key="key_columns",
        non_empty=False,
        json_label="--key-columns-json",
    )
    if not key_res.success:
        finish_cli_shared_result(key_res, json_out=json_out, quiet=True)
    resolved_keys = list(key_res.data["key_columns"])

    ns = SimpleNamespace(
        input_file=input_file,
        key_columns=resolved_keys,
        report_file=report_file,
        report_format=report_format,
        overwrite=overwrite,
        data_report=data_report,
    )
    result = run_data_check(ns)
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
