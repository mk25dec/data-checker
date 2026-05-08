# File cleaner

## What this is for

Clean data after conversion; export Excel, CSV, or data copies.

---

## Where the work runs

Almost all **heavy cleaning** is done by **DuckDB** in-memory over your Parquet (or a **chunked DuckDB view** when you use **``--chunking-mode``** for large files—see **Help** below). **Polars** is used for the optional **meta-cols** step on Parquet (for example adding **`x_ip_id`**) and for **XLSX** outputs: DuckDB results are turned into Polars frames (**pyarrow** / **``.pl()``**) and written with **Polars** **`write_excel`** (**xlsxwriter**). **CSV** and **Parquet** outputs use DuckDB **`COPY`**. That is the same stack direction as the sibling [file format converter](../file-format-converter/): **DuckDB + Polars**, not pandas, for exports and large-table handling.

---

## What files you can use

This tool only works on **Parquet** files. If you have other formats, convert them first (for example with the [file format converter](../file-format-converter/) in this project).

---

## Help and short flags

The CLI is **Typer** (Click). From the **file-cleaner** directory:

- **``pixi run help``** — same as **`pixi run file_cleaner -- --help`** (full **`clean`** command help).
- Pass **`--`** after **`pixi run file_cleaner`** so flags reach the tool (same pattern as [file format converter](../file-format-converter/)).

| Flag | Long |
|------|------|
| `-i` | `--input` |
| `-o` | `--output-directory` |
| `-O` | `--output-format` |
| `-j` | `--json` |
| `-q` | `--quiet` |
| `-d` | `--deduplicate` |
| `-s` | `--strip` |
| `-c` | `--clean-control` |
| `-w` | `--whitespace` |
| `-l` | `--lowercase` |
| `-u` | `--uppercase` |
| `-e` | `--empty-rows` |
| `-t` | `--date-format` |
| `-k` | `--de-duplicate-key` |

**``--chunking-mode``** (`auto` / `on` / `off`) controls whether very large Parquet inputs use a **chunked DuckDB view** (with **`--chunking-size-mb-threshold`** and **`--chunk-size-rows`**): **auto** turns chunking on from the size threshold up; **on** always uses that path (typically **lower peak memory**); **off** keeps a single DuckDB relation over the file. The heavy path stays **DuckDB + Polars**, not a full eager Python dataframe of the whole table.

Shared **`-j` / `--json`**, **`-q` / `--quiet`**, and **`finish_cli_shared_result`** follow **`shared_lib.cli_typer`** like the other data tools in this repo.

---

## How to run it (for people who run commands on the computer)

These examples assume you are **inside the file-cleaner folder** on your machine and your project uses **Pixi** (a helper that installs the right software for this tool).

1. Install what the tool needs (once):

   ```bash
   pixi install
   ```

2. See all available options:

   ```bash
   pixi run help
   ```

   (Or: `pixi run file_cleaner -- --help` — same help text.)

3. Clean some Parquet files (example **beside the input**, same idea as [file format converter](../file-format-converter/) when you omit `--output-directory` there):

   ```bash
   pixi run file_cleaner -- \
     -i /path/to/your/data/file.parquet \
     -O csv
   ```

   Cleaned files, reports, and optional issue lists are written in **the same folder as the first `-i` path** (name pattern `file_cleaner_cleaned_<timestamp>.<ext>`, etc.). If you pass several `-i` paths in **different** directories, everything still goes to the first input’s folder unless you set **`-o`**.

4. Or set an explicit output folder:

   ```bash
   pixi run file_cleaner -- \
     -o /path/to/output/folder \
     -i /path/to/your/data/*.parquet \
     -O csv
   ```

   - **`-i`** — your input Parquet file(s), or a pattern like `*.parquet`.
   - **`-o` / `--output-directory`** — optional; when set, cleaned files go here (created if needed).
   - **`--output-format`** — what kind of file to write (`csv`, `parquet`, or `xlsx`). You can repeat this flag if you want more than one type.

If your setup does not use Pixi, someone on your team can run the same command with `python -m file_cleaner.cli` and the right environment; ask them for the exact line for your computer.

---

## What you get in the output folder

Whether you use **`-o`** or the default directory beside the input, cleaned files are saved with names like **`file_cleaner_cleaned_`** plus a time stamp and the extension you asked for. Depending on settings, you may also get **issue lists** (rows that needed attention) and a **short report** summarizing what ran.

---

## Optional: results as a computer-readable summary

If you add **`--json`**, the tool prints a structured summary on the screen instead of only a short human message. That is mainly useful when another program or script needs to read the outcome automatically.
