# join-vertical

## What this is for

Combine several matching tables into one long table.

## What you need

- **Input:** only `.parquet` / `.pq` files (paths or globs, repeatable `-i`), and/or an `--input-manifest` JSON whose artifact paths point at Parquet files.
- **Output:** one file under **`--output-directory`** — CSV, Parquet, or XLSX (`-O` / `--output-format`).
- Run from this folder with **Pixi** so `duckdb`, `polars`, and `shared_lib` resolve correctly.

## Typical use

```bash
cd modules_data_processor/join-vertical
pixi install

# Required: output folder first, then inputs (same style as file-cleaner)
pixi run join_vertical -- -o /path/to/out -i /path/a.parquet -i /path/b.parquet -O parquet

pixi run help
```

Optional flags: `--union-type distinct`, `--on-error fail-fast`, `--type-mode string`, `--timestamp-output`, `--output-stem my_stack`, `--overwrite`.

Each row keeps **`x_source_file`** (which input it came from) and **`x_id`** (row order in the result). Completely blank data rows are removed before output.

## Layout

- `src/join_vertical/cli.py` — Typer CLI, `SharedResult` / `--json`
- `src/join_vertical/join_core.py` — resolves inputs, validates Parquet-only, builds output path
- `src/join_vertical/stack_engine.py` — DuckDB stack / schema reconcile / write

Shared helpers live in **`shared_lib`** (`sql_string_literal` / `sql_double_quoted_ident`, `resolve_parquet_file_inputs`, `load_manifest_parquet_paths`).
