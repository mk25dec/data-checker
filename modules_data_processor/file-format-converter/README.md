# File Format Converter

## What this is for

Convert one file to Excel, CSV, JSON lines, or Parquet.

## Where the work runs

Almost all **heavy processing** is done by **DuckDB** (in-memory): it reads the input with **`read_csv`**, **`read_parquet`**, **`read_json_auto`**, etc., and writes with **`COPY … TO`** for CSV, JSON, and Parquet. **XLSX input** is read with **Polars** (`read_excel`, **calamine** via **fastexcel**) and registered in DuckDB; **XLSX output** is written with **Polars** `write_excel` (**xlsxwriter**) after DuckDB runs the `SELECT` and returns a Polars frame (via **pyarrow** interop).

**Python** (this package) handles the **CLI**, paths, **`shared_lib`** validation, building the **SQL** string, optional **`WHERE` / `LIMIT`**, errors, and **`SharedResult`** / timing—not the bulk data transform.

**XLSX:** **Reading** uses **Polars** `read_excel(..., engine="calamine", drop_empty_rows=False)` (still avoids dropping blank rows where the reader supports it). **Writing** uses **Polars** `write_excel` (xlsxwriter). **Verification** counts **data** rows via Open XML ``<row>`` elements in ``sheet1.xml`` (readers often drop an all-null last row even when the file on disk is complete). **Several sheets:** repeat **`-s` / `--excel-sheet`**, use a comma-separated list on one **`-s`**, pass **`ALL`** alone for every tab, or omit **`-s`** for the first sheet only. With **more than one** sheet selected, outputs are **`<sanitized_sheet_name>.<ext>`** in **`--output-directory`** or beside the input (**`-O`** without **`-f`**); **`--output-file`** is not allowed in that case.

## Paths and formats

- **Source:** **`-i` / `--input-file`** PATH.
- **Destination:** **`-f` / `--output-file`** sets the output file explicitly (not with **`-d` / `--output-directory`**). Without **`-f`**: either **`-d` DIR** with **`-O` / `--output-format`** writes **`DIR/<stem>.<ext>`** (add **`-T` / `--timestamp-output`** for **`DIR/<stem>_<timestamp>.<ext>`**), or pass **`-O` alone** to write **beside the input** as `<stem>.<ext>` (or `<stem>_converted.<ext>` if that would overwrite the input). **`-O` / `--output-format` is required** whenever **`-f` / `--output-file`** is omitted. **XLSX, multiple sheets:** with **`-s ALL`** or repeated **`-s`**, files are **`DIR/<sheet>.<ext>`** or **`<sheet>.<ext>`** beside the input (sanitized names); do not use **`-f`**.
- **Formats:** `-I` / `--input-format` and `-O` / `--output-format` are **optional** when paths have a known suffix; detection uses **`shared_lib`**. If a suffix is missing or ambiguous, pass `-I` and/or `-O`.
- **Same input/output format:** If both resolve to the same type (e.g. CSV→CSV), the tool **exits without reading or writing** and prints an explanatory message (validation exit code **2**).

## Other short flags

| Flag | Long |
|------|------|
| `-i` | `--input-file` |
| `-f` | `--output-file` |
| `-d` | `--output-directory` |
| `-I` | `--input-format` |
| `-O` | `--output-format` |
| `-w` | `--overwrite` |
| `-q` | `--quiet` |
| `-p` | `--print-sql` |
| `-j` | `--json` — print a single JSON object (`SharedResult`) on stdout (success or failure). |
| `-l` | `--limit` |
| `-T` | `--timestamp-output` — append compact timestamp to the output file name (optional; uses `shared_lib`). |
| `-R` | `--csv-header` / `--no-csv-header` |
| `-c` | `--csv-delimiter` |
| `-E` | `--output-csv-delimiter` |
| `-H` | `--output-header` / `--no-output-header` |
| `-s` | `--excel-sheet` |
| `-x` | `--excel-header` / `--no-excel-header` |
| `-Z` | `--parquet-compression` |
| `-W` | `--where` |

## Shared CLI types (other modules)

Shared rules (**`validate_output_targets_exclusive`**, **`effective_quiet`**, **`CommonIoCliOptions`**) live in **`shared_lib.cli_common`** (no Typer). Typer **`Annotated`** aliases for **`-i`/`--input-file`**, **`-f`/`--output-file`**, **`-d`/`--output-directory`**, **`-w`/`--overwrite`**, **`-j`/`--json`**, **`-T`/`--timestamp-output`** plus **`finish_cli_shared_result`** live in **`shared_lib.cli_typer`**. New tools can import from **`cli_typer`** (and **`cli_common`** if needed) so flags and behavior stay aligned.

## Programmatic API

Conversion returns a **`shared_lib.results.SharedResult`** (same pattern as other shared tools):

```python
from types import SimpleNamespace
from pathlib import Path
from file_format_converter import run_convert, result_to_json

args = SimpleNamespace(
    input_file=Path("in.csv"),
    output_file=None,
    output_directory=None,
    output_format="parquet",
    input_format=None,
    overwrite=False,
    quiet=True,
    print_sql=False,
    json_stdout=False,
    timestamp_output=False,
    csv_header=None,
    csv_delimiter=None,
    output_csv_delimiter=None,
    output_header=True,
    excel_sheet=[],
    excel_header=True,
    parquet_compression="zstd",
    where=None,
    limit=None,
)
result = run_convert(args)
if result.success:
    print(result.data["output_path"], result.data["duration_human"])
payload = result.to_dict()  # dict for chaining / logging
text = result_to_json(result, indent=2)  # JSON string
```

On success, **`result.data`** includes string paths (`output_path` is the first file; **`output_paths`** lists every file when multiple XLSX sheets are exported), `input_format` / `output_format`, `duration_sec`, `duration_human`, `copy_sql` (last sheet’s SQL) and **`copy_sqls`** when multiple, **`output_filename_timestamp`** when **`-T`** was used (stamp string, else `null`). Use **`result_to_json(..., indent=None)`** for one-line JSON.

CLI: **`convert -j`** / **`convert --json`** prints that JSON on stdout and sets the process exit code from **`result.exit_code`**.

## Run with Pixi

```bash
cd /path/to/file-format-converter
pixi install
pixi run help
# Infer formats from extensions (task name is `convert`, not the script name):
pixi run convert -- -i input.csv -f output.parquet
pixi run convert -- -i data.parquet -O csv -w
pixi run convert -- -i data.parquet -f out.csv -w
# Force formats when needed:
pixi run convert -- -i input.bin -I csv -f out.bin -O parquet
# File in a chosen directory (add -T for timestamp in the name):
pixi run convert -- -i input.csv -d /tmp/out -O parquet -w
pixi run convert -- -i input.csv -d /tmp/out -O parquet -T -w
# XLSX: every sheet to CSV beside the workbook (tab names as file stems):
pixi run convert -- -i book.xlsx -s ALL -O csv -w
```

If `pixi run` does not forward flags on your Pixi version, use:  
`pixi run python -m file_format_converter.cli convert -i … -f …`

## Shell tab completion (optional)

The CLI is built with **[Typer](https://typer.tiangolo.com/)** (Click). Install shell completion once:

```bash
file-format-converter --install-completion
# or, when using the module:
python -m file_format_converter.cli --install-completion
```

Follow the printed instructions (shell-specific).

## Supported formats

Input and output: `csv`, `json`, `jsonl`, `ndjson`, `parquet`, `xlsx` (aliases like `pq` → `parquet` come from `shared_lib`).

## Help text

Built with [Typer](https://typer.tiangolo.com/) and **Rich**-formatted `--help`. Options are grouped into panels (**Required**, **Output**, **CSV**, **Excel**, etc.) via `rich_help_panel` so the truly required **`-i`/`--input-file`** line sits under **Required**, not buried in a flat list. Panel **order** follows the **parameter order** in the command (Typer’s rule), not the panel title string.

See `convert --help`; you need **`-f`/`--output-file`** and/or **`-O`/`--output-format`** (if **`-f`** is omitted, **`-O`** is required, unless you use **`-d`/`--output-directory`**, which also requires **`-O`**).

## Notes

XLSX I/O uses **polars**, **fastexcel** (calamine read path), **xlsxwriter** (write path), and **pyarrow** (DuckDB → Polars); no DuckDB `excel` extension is required for `.xlsx`.
