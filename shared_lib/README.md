# Shared Library

Clean shared utilities for data processor modules and other Python callers.

## Explorer Script

Run the menu:

```bash
python3 shared_lib_explorer.py
```

Useful direct commands:

```bash
python3 shared_lib_explorer.py modules
python3 shared_lib_explorer.py module file_size
python3 shared_lib_explorer.py module filesystem
python3 shared_lib_explorer.py module ids
python3 shared_lib_explorer.py functions file_size
python3 shared_lib_explorer.py show file_size get_path_size
python3 shared_lib_explorer.py size /path/to/file-or-folder
python3 shared_lib_explorer.py check /path/to/file --expected file
python3 shared_lib_explorer.py validate-input /path/to/input.csv --formats csv,parquet
python3 shared_lib_explorer.py uid --kind nanoid --length 12
python3 shared_lib_explorer.py uid --kind uuid
python3 shared_lib_explorer.py latest /path/to/dir --top-n 3 --pattern '*.parquet'
python3 shared_lib_explorer.py latest /path/to/dir --recursive --pattern '*.log'
```

The explorer discovers modules dynamically from `shared_lib`; it does not use
a fixed module list.

## CLI helpers without Typer (`cli_common.py`)

Validation and small rules used by multiple CLIs (no **Typer** dependency):

```python
from shared_lib.cli_common import (
    CommonIoCliOptions,
    effective_quiet,
    validate_output_targets_exclusive,
)
```

## Typer CLI helpers (`cli_typer.py`)

For Typer-based tools that share the same flags (**`-i`/`--input-file`**, **`-f`/`--output-file`**,
**`-d`/`--output-directory`**, **`-w`/`--overwrite`**, **`-j`/`--json`**, **`-T`/`--timestamp-output`**), import reusable
**`Annotated`** aliases and **`finish_cli_shared_result`** (requires **`typer`** in the environment).
**`cli_typer`** re-exports **`validate_output_targets_exclusive`** and **`effective_quiet`** from **`cli_common`**. It also exports **`PANEL_*`** constants (e.g. **`PANEL_REQUIRED`**, **`PANEL_RUN`**) for Typer **`rich_help_panel=`** so `--help` groups options into Rich panels; panel order follows **first occurrence** of each panel in the command signature.

```python
from shared_lib.cli_typer import (
    InputFileOpt,
    OutputFileOpt,
    OutputDirectoryOpt,
    OverwriteOpt,
    JsonOpt,
    TimestampOutputOpt,
    validate_output_targets_exclusive,
    effective_quiet,
    finish_cli_shared_result,
    CommonIoCliOptions,
)

def my_cmd(
    input_file: InputFileOpt,
    output_file: OutputFileOpt = None,
    output_directory: OutputDirectoryOpt = None,
    overwrite: OverwriteOpt = False,
    json_out: JsonOpt = False,
    timestamp_output: TimestampOutputOpt = False,
):
    bad = validate_output_targets_exclusive(output_file, output_directory)
    if bad is not None:
        finish_cli_shared_result(bad, json_out=json_out, quiet=True)
    # ... build SharedResult ...
    finish_cli_shared_result(result, json_out=json_out, quiet=quiet)
```

This module is **not** imported from the top-level `import shared_lib` bundle so that non-CLI
code does not require Typer.

## Public Grouped Modules

Use these when you want one clear place to look:

- `shared_lib.filesystem`: common file and folder tasks.
- `shared_lib.ids`: ID generation.

Example:

```python
from shared_lib.filesystem import check_path, ensure_directory, get_path_size
from shared_lib.ids import generate_uid
```

Focused implementation modules still exist for smaller ownership, such as
`path_utils.py`, `file_size.py`, `atomic_write.py`, `generate_uid.py`, and
`find_latest_file.py` (**`find_latest_files`** / **`find_latest_file`**: newest files by mtime or ctime,
optional glob and **`recursive`** subtree search).

`duckdb_create_load.py` loads supported tabular files into a **persistent** DuckDB database
with `SET memory_limit` / `threads` / `temp_directory`, CSV **rejects** exported to a CSV file,
and an optional `duckdb` install (import fails with a clear `SharedResult` if missing).
On success, `SharedResult.data` includes `input_size_bytes`, `input_size_human`,
`input_physical_lines` (newline count for text inputs when scanned), `columns_loaded`,
`column_names`, and `rows_loaded`.

## Suggestions

Future shared utilities should stay small and wrap standard or proven OSS tools
instead of rewriting heavy logic.

| Need | Shared module idea | Best default | Optional OSS/backend |
|---|---|---|---|
| Time and timestamps | `time_utils.py` | `datetime`, `zoneinfo`, `time` | Done |
| JSON files and parsing | `json_utils.py` | `json` | `orjson` for speed |
| Environment variables | `env_utils.py` | `os.environ` | `python-dotenv`, `pydantic-settings` |
| Running commands | `process_utils.py` | `subprocess` | avoid extra dependency unless needed |
| File hashes | `hash_utils.py` | `hashlib` | `blake3` if speed matters |
| Row counts | `row_count.py` | simple file iteration | DuckDB, Polars, PyArrow |
| CSV helpers | `csv_utils.py` | `csv` | CleverCSV, DuckDB, Polars |
| Error formatting | `error_utils.py` | `traceback`, `logging` | Rich, Sentry SDK |

Recommended order:

1. `time_utils.py` - done
2. `json_utils.py`
3. `env_utils.py`
4. `process_utils.py`
5. `hash_utils.py`
6. `row_count.py`
7. `csv_utils.py`
8. `error_utils.py`

Keep this rule:

```text
app modules -> shared_lib -> standard library or optional OSS backend
```

The app modules should call `shared_lib` only. That keeps the rest of the repo
stable even if an internal backend changes later.
