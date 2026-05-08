from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, List, Optional

import click
import typer

from file_format_converter.convert_core import (
    SUPPORTED_INPUT_FORMATS,
    SUPPORTED_OUTPUT_FORMATS,
    run_convert,
)

from file_format_converter import __version__

from shared_lib.cli_typer import (
    InputFileOpt,
    JsonOpt,
    OutputDirectoryOpt,
    OutputFileOpt,
    OverwriteOpt,
    PANEL_APP,
    PANEL_CSV,
    PANEL_EXCEL,
    PANEL_FILTER,
    PANEL_INPUT_FMT,
    PANEL_OUTPUT,
    PANEL_PARQUET,
    PANEL_RUN,
    TimestampOutputOpt,
    effective_quiet,
    finish_cli_shared_result,
    validate_output_targets_exclusive,
)

_FMT_IN = sorted(SUPPORTED_INPUT_FORMATS)
_FMT_OUT = sorted(SUPPORTED_OUTPUT_FORMATS)

_APP_HELP = """\
Convert one table file from one layout to another (spreadsheet, CSV, Parquet, and more).

\b• Purpose: Change file type or shape so another program can open the data, or re-save with optional row filters.
• Input: One tabular file (supported types are listed on convert --help).
• Output: A new file in the format you choose; set the path or write beside the input with a safe name.
Use the convert command: file-format-converter convert --help
"""


def _limit_callback(value: Optional[int]) -> Optional[int]:
    if value is not None and value < 1:
        raise typer.BadParameter("must be at least 1")
    return value


def _one_char_callback(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if len(value) != 1:
        raise typer.BadParameter("must be exactly one character")
    return value


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"file-format-converter {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="file-format-converter",
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
    """See file-format-converter --help for purpose, input, and output."""


@app.command(
    "convert",
    epilog=(
        f"Formats: {', '.join(_FMT_IN)}. "
        "XLSX: repeat -s/--excel-sheet, comma-separated names, or ALL for every sheet; multiple sheets write <sheet>.<ext> (no -f). "
        "Same input and output format (e.g. CSV to CSV) is rejected with an explanatory message. "
        "Without -f/--output-file and without -d/--output-directory: output is beside the input; "
        "omit -O to use a default target type by input (e.g. CSV → Parquet), or pass -O explicitly. "
        "Or use -d DIR with -O for DIR/<stem>.<ext>; add -T for a timestamp in the name."
    ),
)
def convert_cmd(
    input_file: InputFileOpt,
    input_format: Annotated[
        Optional[str],
        typer.Option(
            "-I",
            "--input-format",
            help="Input format when not clear from the path.",
            click_type=click.Choice(_FMT_IN, case_sensitive=False),
            rich_help_panel=PANEL_INPUT_FMT,
        ),
    ] = None,
    output_file: OutputFileOpt = None,
    output_directory: OutputDirectoryOpt = None,
    output_format: Annotated[
        Optional[str],
        typer.Option(
            "-O",
            "--output-format",
            help=(
                "Output format. With --output-file: optional if the path suffix is enough. "
                "With --output-directory: required. "
                "With neither: optional; beside-input default by input type (e.g. csv→parquet, parquet→csv, xlsx→parquet)."
            ),
            click_type=click.Choice(_FMT_OUT, case_sensitive=False),
            rich_help_panel=PANEL_OUTPUT,
        ),
    ] = None,
    csv_header: Annotated[
        Optional[bool],
        typer.Option(
            "-R",
            "--csv-header/--no-csv-header",
            help="CSV input header row (default autodetect).",
            rich_help_panel=PANEL_CSV,
        ),
    ] = None,
    csv_delimiter: Annotated[
        Optional[str],
        typer.Option(
            "-c",
            "--csv-delimiter",
            metavar="C",
            help="CSV input delimiter (default autodetect).",
            callback=_one_char_callback,
            rich_help_panel=PANEL_CSV,
        ),
    ] = None,
    output_csv_delimiter: Annotated[
        Optional[str],
        typer.Option(
            "-E",
            "--output-csv-delimiter",
            metavar="C",
            help="CSV output delimiter.",
            callback=_one_char_callback,
            rich_help_panel=PANEL_CSV,
        ),
    ] = None,
    output_header: Annotated[
        bool,
        typer.Option(
            "-H",
            "--output-header/--no-output-header",
            help="Header row on CSV/XLSX output.",
            rich_help_panel=PANEL_CSV,
        ),
    ] = True,
    excel_sheet: Annotated[
        Optional[List[str]],
        typer.Option(
            "-s",
            "--excel-sheet",
            metavar="NAME|INDEX|ALL",
            help=(
                "XLSX only: 0-based sheet index, exact tab name, comma-separated names, "
                "repeat -s for several sheets, or ALL for every sheet. Omit for the first sheet only. "
                "More than one sheet writes <sheetname>.<ext> (use -d or -O without -f). "
                "If a tab name contains a comma, use --excel-sheet-json instead."
            ),
            rich_help_panel=PANEL_EXCEL,
        ),
    ] = None,
    excel_sheet_json: Annotated[
        Optional[str],
        typer.Option(
            "--excel-sheet-json",
            help=(
                "XLSX only: JSON array of sheet names or indices, e.g. '[\"Sheet1\",\"Q1, Summary\"]'. "
                "Mutually exclusive with -s/--excel-sheet."
            ),
            rich_help_panel=PANEL_EXCEL,
        ),
    ] = None,
    excel_header: Annotated[
        bool,
        typer.Option(
            "-x",
            "--excel-header/--no-excel-header",
            help="XLSX input has header row.",
            rich_help_panel=PANEL_EXCEL,
        ),
    ] = True,
    parquet_compression: Annotated[
        str,
        typer.Option(
            "-Z",
            "--parquet-compression",
            help="Parquet compression.",
            click_type=click.Choice(["uncompressed", "snappy", "gzip", "zstd"], case_sensitive=False),
            rich_help_panel=PANEL_PARQUET,
        ),
    ] = "zstd",
    where: Annotated[
        Optional[str],
        typer.Option(
            "-W",
            "--where",
            metavar="EXPR",
            help="SQL filter (no WHERE keyword).",
            rich_help_panel=PANEL_FILTER,
        ),
    ] = None,
    limit: Annotated[
        Optional[int],
        typer.Option(
            "-l",
            "--limit",
            metavar="N",
            help="Row limit after filter.",
            callback=_limit_callback,
            rich_help_panel=PANEL_FILTER,
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
    print_sql: Annotated[
        bool,
        typer.Option(
            "-p",
            "--print-sql",
            help="Print COPY SQL to stdout before running.",
            rich_help_panel=PANEL_RUN,
        ),
    ] = False,
    json_out: JsonOpt = False,
    timestamp_output: TimestampOutputOpt = False,
) -> None:
    """\
Convert one table file to another format (or filter rows and re-save).

\b• Purpose: New file for another tool; change type (e.g. Excel to CSV), optional filters; original unchanged.
• Input: One data file (supported types in epilog below).
• Output: One new file (or one per multi-sheet Excel); use -f, or -d plus -O, or omit both to write beside input (default format if -O omitted).
"""
    bad = validate_output_targets_exclusive(output_file, output_directory)
    if bad is not None:
        finish_cli_shared_result(bad, json_out=json_out, quiet=True)

    ns = SimpleNamespace(
        input_file=input_file,
        input_format=input_format,
        output_file=output_file,
        output_directory=output_directory,
        output_format=output_format,
        overwrite=overwrite,
        quiet=effective_quiet(quiet=quiet, json_out=json_out),
        json_stdout=json_out,
        timestamp_output=timestamp_output,
        print_sql=print_sql,
        csv_header=csv_header,
        csv_delimiter=csv_delimiter,
        output_csv_delimiter=output_csv_delimiter,
        output_header=output_header,
        excel_sheet=list(excel_sheet) if excel_sheet else [],
        excel_sheet_json=excel_sheet_json,
        excel_header=excel_header,
        parquet_compression=parquet_compression,
        where=where,
        limit=limit,
    )
    result = run_convert(ns)
    finish_cli_shared_result(result, json_out=json_out, quiet=quiet)


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
