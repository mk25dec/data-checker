"""
Reusable Typer ``Annotated`` option groups for data-processor CLIs.

Requires ``typer``. Import from here for Typer commands; use :mod:`shared_lib.cli_common` for
helpers that must work without Typer (tests, non-CLI callers).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

from typer import Option

from .cli_common import (
    CommonIoCliOptions,
    effective_quiet,
    validate_output_targets_exclusive,
)
from .results import SharedResult

# Rich help panel titles for Typer/Rich ``--help``. Panel order follows **first occurrence** of each
# panel in the command's parameter list (not alphabetical). Keep related options contiguous.
PANEL_APP = "App"
PANEL_REQUIRED = "Required"
PANEL_INPUT_FMT = "Input format"
PANEL_OUTPUT = "Output"
PANEL_CSV = "CSV"
PANEL_EXCEL = "Excel"
PANEL_PARQUET = "Parquet"
PANEL_FILTER = "Filter"
PANEL_RUN = "Run control"

# Re-export for convenience
__all__ = [
    "CommonIoCliOptions",
    "InputFileOpt",
    "JsonOpt",
    "OutputDirectoryOpt",
    "OutputFileOpt",
    "OverwriteOpt",
    "PANEL_APP",
    "PANEL_CSV",
    "PANEL_EXCEL",
    "PANEL_FILTER",
    "PANEL_INPUT_FMT",
    "PANEL_OUTPUT",
    "PANEL_PARQUET",
    "PANEL_REQUIRED",
    "PANEL_RUN",
    "TimestampOutputOpt",
    "effective_quiet",
    "finish_cli_shared_result",
    "validate_output_targets_exclusive",
]

InputFileOpt = Annotated[
    Path,
    Option(
        ...,
        "-i",
        "--input-file",
        help="Input file path.",
        rich_help_panel=PANEL_REQUIRED,
    ),
]

OutputFileOpt = Annotated[
    Optional[Path],
    Option(
        "-f",
        "--output-file",
        help="Output file path. Mutually exclusive with --output-directory.",
        rich_help_panel=PANEL_OUTPUT,
    ),
]

OutputDirectoryOpt = Annotated[
    Optional[Path],
    Option(
        "-d",
        "--output-directory",
        metavar="DIR",
        help="Output directory. Mutually exclusive with --output-file.",
        rich_help_panel=PANEL_OUTPUT,
    ),
]

OverwriteOpt = Annotated[
    bool,
    Option(
        "--overwrite",
        "-w",
        help="Overwrite output if it exists.",
        rich_help_panel=PANEL_RUN,
    ),
]

JsonOpt = Annotated[
    bool,
    Option(
        "-j",
        "--json",
        help="Print SharedResult as JSON on stdout (success or failure).",
        rich_help_panel=PANEL_RUN,
    ),
]

TimestampOutputOpt = Annotated[
    bool,
    Option(
        "--timestamp-output",
        "-T",
        help="Append compact timestamp to the output file name (shared_lib naming).",
        rich_help_panel=PANEL_RUN,
    ),
]


def finish_cli_shared_result(
    result: SharedResult,
    *,
    json_out: bool,
    quiet: bool,
) -> None:
    """
    Print CLI outcome from a ``SharedResult`` and exit via ``typer.Exit`` (exit code from ``result``).

    On success without ``--json`` and with ``quiet`` false, prints ``result.message`` when non-empty.
    """
    import typer

    if json_out:
        print(json.dumps(result.to_dict(), indent=None))
        raise typer.Exit(result.exit_code)
    if not result.success:
        print(f"error: {result.error or result.message}", file=sys.stderr)
        raise typer.Exit(result.exit_code)
    if not quiet and result.message:
        print(result.message)
