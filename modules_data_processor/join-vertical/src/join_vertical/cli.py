from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from . import __version__
from .join_core import run_join_vertical

from shared_lib.cli_typer import JsonOpt, effective_quiet, finish_cli_shared_result

SCRIPT = "join_vertical"

_APP_HELP = """\
Stack many similar tables into one tall table (Parquet only).

\b• Purpose: Same-shaped files (e.g. monthly exports) into one file for one-off analysis or handoff.
• Input: Several Parquet files (.parquet or .pq): repeat -i, glob, or --input-manifest.
• Output: One stacked CSV/Parquet/Excel under the output directory you name with -o.
Use the stack command: join-vertical stack --help
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"join-vertical {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="join-vertical",
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
    """See join-vertical --help for purpose, input, and output."""


@app.command(
    "stack",
    epilog=(
        "Inputs must be Parquet (.parquet / .pq). One stacked file is written under "
        "--output-directory (see --output-stem and --timestamp-output)."
    ),
)
def stack_cmd(
    output_directory: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output-directory",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Directory for the stacked output file (created if needed).",
        ),
    ],
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
        str,
        typer.Option(
            "-O",
            "--output-format",
            help="Output format for the single stacked file.",
            click_type=click.Choice(["csv", "parquet", "xlsx"], case_sensitive=False),
        ),
    ] = "csv",
    output_stem: Annotated[
        str,
        typer.Option(
            "--output-stem",
            help="Output file base name without extension (default: join_vertical).",
        ),
    ] = SCRIPT,
    timestamp_output: Annotated[
        bool,
        typer.Option(
            "-T",
            "--timestamp-output",
            help="Append compact timestamp to the output file name.",
        ),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("-w", "--overwrite", help="Replace the output file if it already exists."),
    ] = False,
    fill_missing: Annotated[
        str,
        typer.Option(
            "--fill-missing",
            help="Replacement string for NULL text cells in the final projection (default empty).",
        ),
    ] = "",
    union_type: Annotated[
        str,
        typer.Option(
            "--union-type",
            help="all: keep duplicate rows; distinct: dedupe on data columns (order preserved by load indices).",
            click_type=click.Choice(["all", "distinct"], case_sensitive=False),
        ),
    ] = "all",
    on_error: Annotated[
        str,
        typer.Option(
            "--on-error",
            help="Per-source error policy when a file fails to load.",
            click_type=click.Choice(["fail-fast", "skip-file"], case_sensitive=False),
        ),
    ] = "skip-file",
    type_mode: Annotated[
        str,
        typer.Option(
            "--type-mode",
            help="preserve: align types across files; string: cast data columns to VARCHAR before stack.",
            click_type=click.Choice(["preserve", "string"], case_sensitive=False),
        ),
    ] = "preserve",
    output_csv_delim: Annotated[
        str,
        typer.Option(
            "--output-csv-delim",
            help="CSV output delimiter when --output-format=csv.",
        ),
    ] = "|",
    threads: Annotated[
        Optional[int],
        typer.Option(
            "--threads",
            min=1,
            help="DuckDB thread count (default: CPU count minus one).",
        ),
    ] = None,
    allow_empty_match: Annotated[
        bool,
        typer.Option(
            "--allow-empty-match/--no-allow-empty-match",
            help="Allow glob patterns that match zero files.",
        ),
    ] = False,
    max_files: Annotated[
        Optional[int],
        typer.Option("--max-files", min=1, help="Maximum number of resolved input files."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("-q", "--quiet", help="Suppress success line (ignored with --json)."),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """\
Stack many Parquet tables into one file.

\b• Purpose: Append rows from files with the same column layout into one table.
• Input: Multiple Parquet files (paths, globs, or manifest).
• Output: One stacked file under -o/--output-directory (format and stem configurable).
"""
    ns = SimpleNamespace(
        input_patterns=input_pattern,
        input_manifest=input_manifest,
        output_directory=output_directory,
        output_format=output_format,
        output_stem=output_stem,
        timestamp_output=timestamp_output,
        overwrite=overwrite,
        fill_missing=fill_missing,
        union_type=union_type,
        on_error=on_error,
        type_mode=type_mode,
        output_csv_delim=output_csv_delim,
        threads=threads,
        allow_empty_match=allow_empty_match,
        max_files=max_files,
    )
    result = run_join_vertical(ns)
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
