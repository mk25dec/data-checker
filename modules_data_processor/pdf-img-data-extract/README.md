# pdf-img-data-extract

## What this is for

Extract text, tables, or searchable OCR PDFs from PDF/image input files.

## Inputs

- One or more files via `-i/--input-file` (repeatable, comma-separated, and glob patterns supported).
- Supported input extensions: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp`.

## Modes and output formats

- `--mode text`: `txt`, `json`, `docx`
- `--mode table`: `csv`, `xlsx`, `json`, `docx` (PDF inputs only)
- `--mode ocrpdf`: `pdf`

Outputs are timestamped and written to `-o/--output-directory` (or beside each input file if omitted).

## Run

```bash
cd modules_data_processor/pdf-img-data-extract
pixi install

pixi run pdf_img_data_extract -- \
  -i "/path/invoice.pdf" \
  --mode text \
  -O json
```

Help:

```bash
pixi run help
```
