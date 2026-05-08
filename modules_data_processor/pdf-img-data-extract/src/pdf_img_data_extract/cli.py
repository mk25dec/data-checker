from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Optional

import click
import typer

from pdf_img_data_extract import __version__
from pdf_img_data_extract.extract_core import run_extract
from shared_lib.cli_typer import JsonOpt, effective_quiet, finish_cli_shared_result

_APP_HELP = """\
Extract text, tables, or OCR PDF output from PDF/image files.

\b• Purpose: pull text or tables from PDFs/images, or generate searchable OCR PDFs.
• Input: one or more files via -i/--input-file (supports glob patterns).
• Output: timestamped files in the selected format (text/table/ocrpdf modes).
Use: pdf-img-data-extract extract --help
"""


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"pdf-img-data-extract {__version__}")
        raise typer.Exit(0)


app = typer.Typer(
    name="pdf-img-data-extract",
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
    """See pdf-img-data-extract --help for purpose, input, and output."""


@app.command("extract")
def extract_cmd(
    input_file: Annotated[
        list[str],
        typer.Option(
            "-i",
            "--input-file",
            help=(
                "Input file path(s) or glob pattern(s). Repeat -i or comma-separate in one -i, "
                "for example: -i a.pdf -i b.png or -i 'a.pdf,b.png'."
            ),
        ),
    ],
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help="Extraction mode.",
            click_type=click.Choice(["text", "table", "ocrpdf"], case_sensitive=False),
        ),
    ] = "text",
    output_format: Annotated[
        str,
        typer.Option(
            "-O",
            "--output-format",
            "--format",
            help="Output format (depends on mode).",
            click_type=click.Choice(["txt", "json", "docx", "csv", "xlsx", "pdf"], case_sensitive=False),
        ),
    ] = "json",
    output_directory: Annotated[
        Optional[Path],
        typer.Option(
            "-o",
            "--output-directory",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Output folder (default: folder of each input file).",
        ),
    ] = None,
    ocr: Annotated[
        str,
        typer.Option(
            "--ocr",
            help="OCR behavior for PDF text extraction.",
            click_type=click.Choice(["auto", "always", "never"], case_sensitive=False),
        ),
    ] = "auto",
    ocr_threshold: Annotated[int, typer.Option("--ocr-threshold", min=0)] = 80,
    image_ocr_engine: Annotated[
        str,
        typer.Option(
            "--image-ocr-engine",
            help="OCR engine for image input.",
            click_type=click.Choice(["auto", "tesseract", "easyocr"], case_sensitive=False),
        ),
    ] = "auto",
    lang: Annotated[str, typer.Option("--lang", help="OCR language code(s), e.g. eng or eng+deu.")] = "eng",
    table_engine: Annotated[
        str,
        typer.Option(
            "--table-engine",
            help="Table extraction engine for PDF input.",
            click_type=click.Choice(["auto", "camelot", "pdfplumber"], case_sensitive=False),
        ),
    ] = "auto",
    ocr_pdf_quality: Annotated[
        str,
        typer.Option(
            "--ocr-pdf-quality",
            help="Quality preset for searchable OCR PDF output.",
            click_type=click.Choice(["premium", "balanced", "fast"], case_sensitive=False),
        ),
    ] = "premium",
    ocr_pdf_output_type: Annotated[
        str,
        typer.Option(
            "--ocr-pdf-output-type",
            help="OCR output PDF type.",
            click_type=click.Choice(["pdf", "pdfa", "pdfa-1", "pdfa-2", "pdfa-3"], case_sensitive=False),
        ),
    ] = "pdfa",
    image_pdf_dpi: Annotated[int, typer.Option("--image-pdf-dpi", min=150, max=1200)] = 450,
    allow_empty_match: Annotated[
        bool,
        typer.Option(
            "--allow-empty-match/--no-allow-empty-match",
            help="Allow wildcard patterns with zero matches.",
        ),
    ] = False,
    max_files: Annotated[int, typer.Option("--max-files", min=1, help="Maximum resolved input file count.")] = 5000,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Suppress success line (ignored with --json).")] = False,
    json_out: JsonOpt = False,
) -> None:
    """Extract text/tables/OCR PDF outputs from PDF and image inputs."""
    ns = SimpleNamespace(
        input_file=input_file,
        mode=mode,
        output_format=output_format,
        output_directory=output_directory,
        ocr=ocr,
        ocr_threshold=ocr_threshold,
        image_ocr_engine=image_ocr_engine,
        lang=lang,
        table_engine=table_engine,
        ocr_pdf_quality=ocr_pdf_quality,
        ocr_pdf_output_type=ocr_pdf_output_type,
        image_pdf_dpi=image_pdf_dpi,
        allow_empty_match=allow_empty_match,
        max_files=max_files,
    )
    result = run_extract(ns)
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
