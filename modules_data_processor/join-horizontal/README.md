# join-horizontal

## What this is for

Merge two tables side by side using shared reference columns.

## Inputs

- **Left / right:** **`-L` / `--left-file`** and **`-R` / `--right-file`** — each must be **`.parquet`** or **`.pq`**.
- **Keys:** **`-k` / `--merge-on`** — repeat per column for composite keys.
- **Optional column config:** **`--config-file`** — a **Parquet** table with columns **`file`** and **`column`** (same semantics as the legacy Excel config sheet).

## Outputs

- **`-O` / `--output-format`:** `parquet` (default), `csv`, or `xlsx`.
- **Directory:** **`-o` / `--output-directory`** or, if omitted, the **parent folder of the left file** (like file-format-converter beside-input).
- **Name:** outputs use the fixed base name **`join_horizontal`**; optional **`-T` / `--timestamp-output`** appends a timestamp to the primary path and to **`join_input_state`** sidecar names.
- For **CSV / Parquet**, the engine also writes **`*_result*`** and **`*_stats*`** in that format, plus **`join_input_state_left.*`** and **`join_input_state_right.*`** (with **`join_input_state_<ts>_left.*`** when **`-T`** is used).
- For **XLSX**, input state is **`join_input_state.xlsx`** (or **`join_input_state_<ts>.xlsx`** with **`-T`**), one sheet per side.

## Run

```bash
cd modules_data_processor/join-horizontal
pixi install

pixi run join_horizontal -- \
  -L /path/left.parquet \
  -R /path/right.parquet \
  -k id \
  -O parquet

pixi run help
# JSON result on stdout:
pixi run join_horizontal -- -L left.parquet -R right.parquet -k id -j
```

Use **`--`** after **`pixi run …`** so flags reach the CLI.

## Layout

- `src/join_horizontal/cli.py` — Typer CLI, **`--json`**
- `src/join_horizontal/join_core.py` — paths, validation, `SharedResult`
- `src/join_horizontal/engine.py` — DuckDB join pipeline (from legacy `join_clean.py`, Parquet-only I/O)
