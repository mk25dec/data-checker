from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from mask_encrypt import __version__
from mask_encrypt.mask_core import DEFAULT_PASSWORD_ENV_VAR, run_mask_encrypt
from shared_lib.cli_token_lists import resolve_comma_or_json_cli_list
from shared_lib.cli_typer import JsonOpt, effective_quiet, finish_cli_shared_result

_APP_HELP = """\
Mask, encrypt, or decrypt sensitive columns from common data files.

\b• Purpose: protect sensitive columns per user key, then export processed CSV outputs.
• Input: one or more files (.csv/.xlsx/.xls/.parquet/.json) and sensitive column names.
• Output: processed CSV files, run report CSV, users CSV, and keys CSV.
Use: mask-encrypt process --help
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mask-encrypt {__version__}")
        raise typer.Exit(0)


app = typer.Typer(name="mask-encrypt", help=_APP_HELP, no_args_is_help=True)


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version and exit."),
    ] = False,
) -> None:
    """See mask-encrypt --help for purpose, input, and output."""


@app.command("process")
def process_cmd(
    input_file: Annotated[
        list[str],
        typer.Option(
            "-i",
            "--input-file",
            help=(
                "Input path(s), directory path(s), or wildcard pattern(s). Repeat -i or comma-separate in one -i."
            ),
        ),
    ],
    sensitive_column: Annotated[
        list[str],
        typer.Option(
            "-s",
            "--sensitive-column",
            help="Sensitive columns: repeat or comma-separate, e.g. -s 'account_no,pan'.",
        ),
    ] = [],
    sensitive_columns_json: Annotated[
        Optional[str],
        typer.Option(
            "--sensitive-columns-json",
            help='JSON array of sensitive columns, e.g. \'["account_no","pan"]\'.',
        ),
    ] = None,
    username: Annotated[str, typer.Option("-u", "--username", help="Username for authentication and key lookup.")]="",
    action: Annotated[
        str,
        typer.Option(
            "--action",
            click_type=click.Choice(["mask", "encrypt", "decrypt"], case_sensitive=False),
            help="Action to apply.",
        ),
    ] = "mask",
    output_directory: Annotated[
        Optional[Path],
        typer.Option("-o", "--output-directory", file_okay=False, dir_okay=True, resolve_path=True),
    ] = None,
    output_file_name: Annotated[str, typer.Option("--output-file-name", help="Output file prefix.")] = "mask_encrypt",
    delimiter: Annotated[str, typer.Option("--delimiter", help="CSV delimiter for input CSV read and output write.")] = ",",
    mask_style: Annotated[
        str,
        typer.Option(
            "--mask-style",
            click_type=click.Choice(["last4", "full", "email"], case_sensitive=False),
            help="Masking style for --action mask.",
        ),
    ] = "last4",
    mask_char: Annotated[str, typer.Option("--mask-char", help="Mask character (first char is used).")] = "*",
    recursive: Annotated[bool, typer.Option("--recursive/--no-recursive", help="Recurse subfolders for directory input.")] = False,
    allow_empty_match: Annotated[
        bool,
        typer.Option("--allow-empty-match/--no-allow-empty-match", help="Allow wildcard patterns with zero matches."),
    ] = False,
    max_files: Annotated[int, typer.Option("--max-files", min=1)] = 5000,
    add_timestamp: Annotated[bool, typer.Option("--add-timestamp/--no-add-timestamp")] = True,
    auth_directory: Annotated[
        Path,
        typer.Option("--auth-directory", resolve_path=True, file_okay=False, dir_okay=True, help="CSV vault folder."),
    ] = Path("vault"),
    password_env_var: Annotated[str, typer.Option("--password-env-var")] = DEFAULT_PASSWORD_ENV_VAR,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Suppress success line (ignored with --json).")] = False,
    json_out: JsonOpt = False,
) -> None:
    sensitive_res = resolve_comma_or_json_cli_list(
        sensitive_column,
        sensitive_columns_json,
        mutual_exclusive=True,
        comma_option_desc="-s/--sensitive-column",
        json_option_desc="--sensitive-columns-json",
        data_key="sensitive_columns",
        non_empty=True,
        empty_error="Pass at least one sensitive column via -s or --sensitive-columns-json.",
        json_label="--sensitive-columns-json",
    )
    if not sensitive_res.success:
        finish_cli_shared_result(sensitive_res, json_out=json_out, quiet=True)

    ns = SimpleNamespace(
        input_file=input_file,
        sensitive_columns=list(sensitive_res.data["sensitive_columns"]),
        username=username,
        action=action,
        output_directory=output_directory,
        output_file_name=output_file_name,
        delimiter=delimiter,
        mask_style=mask_style,
        mask_char=mask_char,
        recursive=recursive,
        allow_empty_match=allow_empty_match,
        max_files=max_files,
        add_timestamp=add_timestamp,
        auth_directory=auth_directory,
        password_env_var=password_env_var,
    )
    result = run_mask_encrypt(ns)
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
