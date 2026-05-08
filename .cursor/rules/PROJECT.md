# Astro / `code` repository — guide for AI coders

Use this file when working anywhere under this repo for **project layout and technical conventions**.

**Agent behavior:** `cursor_rules/AGENT_BASE.md` and `.cursor/rules/agent-base.mdc` (always on).

---

## Repository layout

| Area | Path | Role |
|------|------|------|
| **Shared Python utilities** | `shared_lib/` | Small, reusable helpers: paths, validation, `SharedResult`, optional DuckDB ingest, etc. |
| **Data processor modules** | `modules_data_processor/` | Installable tools (e.g. `file-format-converter/`). |
| **Explorer** | `shared_lib_explorer.py` (repo root) | CLI menu to try `shared_lib` utilities. |
| **Legacy** | `shared_lib/old/`, `modules_data_processor/old/` | Historical / reference only — do not extend without an explicit ask. |

**Important naming:** Use **`shared_lib`** (this repo). **`shared_utils`** appears only under **`old/`** legacy trees — do not treat it as the current stack.

---

## `shared_lib` conventions

- **Results:** Prefer `SharedResult` (`success`, `exit_code`, `message`, `error`, `data`) from `shared_lib.results` for library-style APIs so callers get one predictable shape.
- **Validation:** `validate_input_file`, `validate_output_file`, `validate_format`, `check_path`, `ensure_parent_directory`, etc. — compose them rather than reimplementing checks.
- **DuckDB ingest:** `shared_lib/duckdb_create_load.py` — `load_file_into_database()` creates/opens a **file-backed** `.duckdb`, applies **memory / threads / temp_directory** limits, supports CSV **rejects** export. **`duckdb` is optional** at import time; missing package returns a failed `SharedResult`, not a crash of unrelated imports.
- **Heavy deps:** Keep `shared_lib` importable without DuckDB for lightweight callers; DuckDB-heavy paths stay inside functions or optional modules.

---

## `file-format-converter` (example module)

- **Path:** `modules_data_processor/file-format-converter/`
- **CLI:** Subcommand **`convert`** only — DuckDB **` :memory:`** connection to read one supported format and **`COPY`** to another file. It does **not** embed `load-db` / `duckdb_create_load` in the CLI anymore; use **`shared_lib.load_file_into_database`** from Python or a separate script if you need a persistent DB file.
- **Monorepo imports:** `shared_lib` lives at repo root. Pixi / local runs often set `PYTHONPATH` to include repo root (e.g. `src:../..` from the module folder). Match existing `pixi.toml` / docs when adding tasks.

---

## Supported formats (typical)

CSV, JSON, JSONL/NDJSON, Parquet, XLSX — keep lists aligned with `shared_lib.format_registry` / `DUCKDB_LOAD_SUPPORTED_FORMATS` where relevant.

---

## Testing

- **shared_lib:** From repo root: `PYTHONPATH=. python3 -m unittest discover -s shared_lib/tests -v`
- **DuckDB-dependent tests:** May skip if `duckdb` is missing; use the **file-format-converter** Pixi env if you need DuckDB: `pixi run -- python -m unittest ...`

---

## Docs policy

Do not add or expand markdown docs unless the user asks — **except** maintenance of this `cursor_rules/` guide and Cursor rule files when instructed.
