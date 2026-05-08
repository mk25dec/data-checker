from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from column_selector import __version__
from column_selector.select_core import SUPPORTED_OUTPUT_FORMATS, run_select

from shared_lib.cli_typer import (
    InputFileOpt,
    JsonOpt,
    OutputDirectoryOpt,
    OutputFileOpt,
    OverwriteOpt,
    PANEL_APP,
    PANEL_OUTPUT,
    PANEL_REQUIRED,
    PANEL_RUN,
    TimestampOutputOpt,
    effective_quiet,
    finish_cli_shared_result,
    validate_output_targets_exclusive,
)

_FMT_OUT = sorted(SUPPORTED_OUTPUT_FORMATS)

_APP_HELP = """\
Pick which columns to keep from one table file; write a smaller Parquet or CSV.

\b• Purpose: Make a new file that only has the columns you name; the rest are not copied (your original file stays unchanged).
• Input: One Parquet file (.parquet or .pq) and the column names you list (-c or --columns-json).
• Output: A new Parquet or CSV with only those columns; by default it saves beside the input with _selectedColumns before the extension (Parquet unless you change it).
Use the select command: column-selector select --help
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"column-selector {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="column-selector",
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
            rich_help_panel=PANEL_APP,
        ),
    ] = False,
) -> None:
    """See column-selector --help for purpose, input, and output."""


@app.command(
    "select",
    epilog=(
        "Columns: use -c 'col1,col2' (quote the whole token so names with spaces work, e.g. -c 'id,home address'). "
        "If a name contains a comma, use --columns-json instead. "
        "Input must be Parquet (.parquet / .pq). If you omit --output-file and --output-directory, the output "
        "is written in the same directory as the input (like file-format-converter): "
        "<filename_stem>_selectedColumns.<ext> when that avoids overwriting the input. "
        "--output-format defaults to parquet; with --output-file, the suffix implies the format when -O is omitted "
        f"(.csv / .parquet / .pq). Formats: {', '.join(_FMT_OUT)}."
    ),
)
def select_cmd(
    input_file: InputFileOpt,
    column: Annotated[
        list[str],
        typer.Option(
            "-c",
            "--column",
            help=(
                "Column name(s) to keep. Comma-separated list and/or repeat -c; quote for shell-safe spaces "
                "(e.g. -c 'row_id,home address'). Mutually exclusive with --columns-json."
            ),
            rich_help_panel=PANEL_REQUIRED,
        ),
    ] = [],
    columns_json: Annotated[
        Optional[str],
        typer.Option(
            "--columns-json",
            help=(
                "JSON array of exact column names, e.g. '[\"row_id\",\"home, full name\"]'. "
                "Use when a name contains a comma. Mutually exclusive with -c."
            ),
            rich_help_panel=PANEL_REQUIRED,
        ),
    ] = None,
    output_file: OutputFileOpt = None,
    output_directory: OutputDirectoryOpt = None,
    output_format: Annotated[
        Optional[str],
        typer.Option(
            "-O",
            "--output-format",
            help="Output format (default: parquet). With --output-file, optional if the path suffix is .csv, "
            ".parquet, or .pq.",
            click_type=click.Choice(_FMT_OUT, case_sensitive=False),
            rich_help_panel=PANEL_OUTPUT,
        ),
    ] = None,
    overwrite: OverwriteOpt = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "-q",
            "--quiet",
            help="Suppress success line (ignored with --json).",
            rich_help_panel=PANEL_RUN,
        ),
    ] = False,
    json_out: JsonOpt = False,
    timestamp_output: TimestampOutputOpt = False,
) -> None:
    """\
Choose a subset of columns from one data file.

\b• Purpose: Build a new file with only the columns you care about; the original file is not changed.
• Input: One Parquet file (.parquet or .pq) and column names via -c or --columns-json.
• Output: A new Parquet or CSV; default path is beside the input with _selectedColumns before the extension (see options).
"""
    bad = validate_output_targets_exclusive(output_file, output_directory)
    if bad is not None:
        finish_cli_shared_result(bad, json_out=json_out, quiet=True)

    ns = SimpleNamespace(
        input_file=input_file,
        column=list(column) if column else [],
        columns_json=columns_json,
        output_file=output_file,
        output_directory=output_directory,
        output_format=output_format,
        overwrite=overwrite,
        timestamp_output=timestamp_output,
    )
    result = run_select(ns)
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
