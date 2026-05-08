from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from join_horizontal import __version__
from join_horizontal.join_core import run_join_horizontal

from shared_lib.cli_typer import JsonOpt, effective_quiet, finish_cli_shared_result

_APP_HELP = """\
Join two related tables side-by-side when they share matching keys (Parquet only).

\b• Purpose: One wider table: rows from left and right that belong together (e.g. same ID) get merged side-by-side.
• Input: Two Parquet files (.parquet or .pq) and column name(s) to match on (repeat -k for composite keys).
• Output: Joined result (CSV, Parquet, or Excel) plus stats/state files in the folder you pick (default: left file's folder).
Use the join command: join-horizontal join --help
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"join-horizontal {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="join-horizontal",
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
    """See join-horizontal --help for purpose, input, and output."""


@app.command(
    "join",
    epilog=(
        "Left and right inputs must be Parquet (.parquet / .pq). Join keys: repeat -k, one -k with "
        "comma-separated names (e.g. -k 'row_id,no'), or --merge-on-json. Without -o/--output-directory, "
        "the output directory is the parent of the left file (same idea as file-format-converter). "
        "CSV and Parquet outputs also write *_stats* and join_input_state_left/right (or join_input_state_<ts>_left/right with -T)."
    ),
)
def join_cmd(
    left_file: Annotated[
        Path,
        typer.Option(
            "-L",
            "--left-file",
            resolve_path=True,
            help="Left Parquet file path.",
        ),
    ],
    right_file: Annotated[
        Path,
        typer.Option(
            "-R",
            "--right-file",
            resolve_path=True,
            help="Right Parquet file path.",
        ),
    ],
    merge_on: Annotated[
        list[str],
        typer.Option(
            "-k",
            "--merge-on",
            help="Join key column name(s): repeat -k, or one -k with comma-separated names (e.g. -k 'id,version').",
        ),
    ] = [],
    merge_on_json: Annotated[
        Optional[str],
        typer.Option(
            "--merge-on-json",
            help='JSON array of exact key column names, e.g. \'["row_id","no"]\'. Mutually exclusive with -k.',
        ),
    ] = None,
    output_directory: Annotated[
        Optional[Path],
        typer.Option(
            "-o",
            "--output-directory",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Directory for outputs (created if needed). If omitted, uses the left file's parent folder.",
        ),
    ] = None,
    output_format: Annotated[
        str,
        typer.Option(
            "-O",
            "--output-format",
            help="Primary output format.",
            click_type=click.Choice(["csv", "parquet", "xlsx"], case_sensitive=False),
        ),
    ] = "parquet",
    timestamp_output: Annotated[
        bool,
        typer.Option(
            "-T",
            "--timestamp-output",
            help="Append compact timestamp to the primary output and join_input_state file names.",
        ),
    ] = False,
    overwrite: Annotated[
        bool,
        typer.Option("-w", "--overwrite", help="Replace output files if they already exist."),
    ] = False,
    config_file: Annotated[
        Optional[Path],
        typer.Option(
            "--config-file",
            resolve_path=True,
            help="Optional Parquet column config (file, column) for output projection.",
        ),
    ] = None,
    how: Annotated[
        str,
        typer.Option(
            "--how",
            help="SQL-style join.",
            click_type=click.Choice(["left", "right", "inner", "outer"], case_sensitive=False),
        ),
    ] = "outer",
    dedupe_left_keys: Annotated[
        bool,
        typer.Option(
            "--dedupe-left-keys",
            help=(
                "De-duplicate the left file: remove extra rows where the join key is duplicate or the same "
                "as another row (keeps one row per key, lowest row id first)."
            ),
        ),
    ] = False,
    dedupe_right_keys: Annotated[
        bool,
        typer.Option(
            "--dedupe-right-keys",
            help=(
                "De-duplicate the right file: remove extra rows where the join key is duplicate or the same "
                "as another row (keeps one row per key, lowest row id first)."
            ),
        ),
    ] = False,
    left_name: Annotated[
        Optional[str],
        typer.Option(
            "--left-name",
            help="Label for the left table in stats/config (default: left file stem).",
        ),
    ] = None,
    right_name: Annotated[
        Optional[str],
        typer.Option(
            "--right-name",
            help="Label for the right table in stats/config (default: right file stem).",
        ),
    ] = None,
    large_mode: Annotated[
        bool,
        typer.Option("--large-mode/--no-large-mode", help="Tune DuckDB for larger inputs."),
    ] = False,
    threads: Annotated[
        Optional[int],
        typer.Option("--threads", min=1, help="DuckDB thread count (default: auto)."),
    ] = None,
    temp_directory: Annotated[
        Optional[str],
        typer.Option("--temp-directory", help="DuckDB temp directory."),
    ] = None,
    memory_limit: Annotated[
        Optional[str],
        typer.Option("--memory-limit", help="DuckDB memory limit, e.g. 8GB."),
    ] = None,
    max_runtime_seconds: Annotated[
        Optional[float],
        typer.Option("--max-runtime-seconds", min=0.0, help="Optional wall-clock limit for the join."),
    ] = None,
    debug: Annotated[bool, typer.Option("--debug/--no-debug", help="Verbose engine logging.")] = False,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Suppress success line (ignored with --json).")] = False,
    json_out: JsonOpt = False,
) -> None:
    """\
Combine two Parquet tables into one wider table.

\b• Purpose: Match left/right rows on shared keys; one result with columns from both (inner/left/right/outer).
• Input: Two Parquet files and key column name(s) present in both.
• Output: Joined table plus stats and input-state files in your output directory (see epilog).
"""
    ns = SimpleNamespace(
        left_file=left_file,
        right_file=right_file,
        merge_on=merge_on,
        merge_on_json=merge_on_json,
        output_directory=output_directory,
        output_format=output_format,
        timestamp_output=timestamp_output,
        overwrite=overwrite,
        config_file=config_file,
        how=how,
        left_name=left_name,
        right_name=right_name,
        large_mode=large_mode,
        threads=threads,
        temp_directory=temp_directory,
        memory_limit=memory_limit,
        max_runtime_seconds=max_runtime_seconds,
        debug=debug,
        dedupe_left_keys=dedupe_left_keys,
        dedupe_right_keys=dedupe_right_keys,
    )
    result = run_join_horizontal(ns)
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
