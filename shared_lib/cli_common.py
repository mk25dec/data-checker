"""CLI-related helpers that do not depend on Typer (safe for any ``import shared_lib`` test)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .results import EXIT_VALIDATION_ERROR, SharedResult, failure


@dataclass(frozen=True, slots=True)
class CommonIoCliOptions:
    """Shared I/O flags for CLIs (file path, output targets, overwrite, JSON, timestamp)."""

    input_file: Path
    output_file: Optional[Path] = None
    output_directory: Optional[Path] = None
    overwrite: bool = False
    json_out: bool = False
    timestamp_output: bool = False


def validate_output_targets_exclusive(
    output_file: Optional[Path],
    output_directory: Optional[Path],
) -> SharedResult | None:
    """Return a ``SharedResult`` failure if both output file and output directory are set; else ``None``."""
    if output_file is not None and output_directory is not None:
        return failure(
            "Use either --output-file or --output-directory, not both.",
            exit_code=EXIT_VALIDATION_ERROR,
        )
    return None


def effective_quiet(*, quiet: bool, json_out: bool) -> bool:
    """Typical rule: JSON machine mode implies no human success line."""
    return quiet or json_out
