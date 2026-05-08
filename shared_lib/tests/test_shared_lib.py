from __future__ import annotations

# NOTE: Tests the shared utility contracts and idempotency.

import logging
import os
import tempfile
import time
import unittest
from pathlib import Path

from shared_lib.cli_common import (
    effective_quiet,
    validate_output_targets_exclusive,
)
from shared_lib import (
    EXIT_NOT_FOUND,
    SharedResultError,
    apply_output_format_to_path,
    find_latest_file,
    find_latest_files,
    check_path,
    default_output_beside_input,
    ensure_directory,
    expand_env_vars,
    format_duration,
    get_logger,
    get_directory_size,
    get_file_size,
    get_path_size,
    generate_nanoid,
    generate_uid,
    generate_uuid,
    human_readable_size,
    load_toml_config,
    make_output_file_path,
    make_output_folder,
    merge_settings,
    raise_if_failed,
    parse_iso_timestamp,
    run_stats,
    stop_on_first_failure,
    timestamp_compact,
    timestamp_compact_microseconds,
    timestamp_iso,
    validate_allowed_settings,
    validate_format,
    validate_input_file,
    validate_output_file,
    validate_required_settings,
    write_json_atomic,
    write_text_atomic,
)
from shared_lib.filesystem import check_path as filesystem_check_path
from shared_lib.filesystem import get_path_size as filesystem_get_path_size
from shared_lib.filesystem import write_text_atomic as filesystem_write_text_atomic
from shared_lib.ids import generate_uid as ids_generate_uid


class SharedLibTests(unittest.TestCase):
    def test_check_path_missing_returns_clear_failure(self) -> None:
        result = check_path("/tmp/shared-lib-test-missing-file.txt", expected="file")

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, EXIT_NOT_FOUND)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.data["kind"], "missing")

    def test_ensure_directory_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"

            first = ensure_directory(target)
            second = ensure_directory(target)

            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertTrue(target.is_dir())

    def test_validate_input_file_detects_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "input.csv"
            target.write_text("a,b\n1,2\n", encoding="utf-8")

            result = validate_input_file(target, supported_formats={"csv"})

            self.assertTrue(result.success)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.data["format"], "csv")

    def test_validate_output_file_uses_pluggable_row_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "output.csv"
            target.write_text("a,b\n1,2\n", encoding="utf-8")

            result = validate_output_file(
                target,
                supported_formats={"csv"},
                min_rows=1,
                row_count_reader=lambda path, fmt: 1,
            )

            self.assertTrue(result.success)
            self.assertEqual(result.data["rows"], 1)

    def test_atomic_write_json_creates_parent_and_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "result.json"

            first = write_json_atomic(target, {"success": True})
            second = write_json_atomic(target, {"success": False})

            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertEqual(target.read_text(encoding="utf-8").strip(), '{\n  "success": false\n}')

    def test_write_text_atomic_creates_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "message.txt"

            result = write_text_atomic(target, "done")

            self.assertTrue(result.success)
            self.assertEqual(target.read_text(encoding="utf-8"), "done")

    def test_grouped_filesystem_module_exports_common_file_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "grouped.txt"

            write_result = filesystem_write_text_atomic(target, "done")
            check_result = filesystem_check_path(target, expected="file")
            size_result = filesystem_get_path_size(target)

            self.assertTrue(write_result.success)
            self.assertTrue(check_result.success)
            self.assertTrue(size_result.success)
            self.assertEqual(size_result.data["size_bytes"], 4)

    def test_grouped_ids_module_exports_uid_generation(self) -> None:
        result = ids_generate_uid("nanoid", length=6)

        self.assertTrue(result.success)
        self.assertEqual(result.data["kind"], "nanoid")
        self.assertEqual(len(result.data["uid"]), 6)

    def test_format_validation_accepts_custom_supported_format(self) -> None:
        result = validate_format("xml", supported_formats={"xml"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["format"], "xml")

    def test_apply_output_format_to_path_adds_extension(self) -> None:
        result = apply_output_format_to_path("/tmp/report", "parquet")

        self.assertTrue(result.success)
        self.assertEqual(result.data["path"], "/tmp/report.parquet")

    def test_get_file_size_returns_human_readable_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "size.txt"
            target.write_bytes(b"x" * 2048)

            result = get_file_size(target)

            self.assertTrue(result.success)
            self.assertEqual(result.data["size_bytes"], 2048)
            self.assertEqual(result.data["size_human"], "2 KB")

    def test_get_directory_size_returns_total_file_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"a" * 1024)
            nested = root / "nested"
            nested.mkdir()
            (nested / "b.txt").write_bytes(b"b" * 2048)

            result = get_directory_size(root)

            self.assertTrue(result.success)
            self.assertEqual(result.data["kind"], "directory")
            self.assertEqual(result.data["size_bytes"], 3072)
            self.assertEqual(result.data["size_human"], "3 KB")
            self.assertEqual(result.data["file_count"], 2)
            self.assertEqual(result.data["directory_count"], 1)

    def test_get_path_size_supports_file_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "size.txt"
            target.write_bytes(b"x" * 512)

            file_result = get_path_size(target)
            directory_result = get_path_size(root)

            self.assertTrue(file_result.success)
            self.assertTrue(directory_result.success)
            self.assertEqual(file_result.data["size_bytes"], 512)
            self.assertEqual(directory_result.data["size_bytes"], 512)

    def test_human_readable_size_formats_bytes(self) -> None:
        self.assertEqual(human_readable_size(12), "12 B")
        self.assertEqual(human_readable_size(1024), "1 KB")
        self.assertEqual(human_readable_size(1536), "1.5 KB")
        self.assertEqual(human_readable_size(10 * 1024), "10 KB")
        self.assertEqual(human_readable_size(123 * 1024 * 1024), "123 MB")

    def test_raise_if_failed_raises_shared_result_error(self) -> None:
        result = check_path("/tmp/shared-lib-test-missing-failfast.txt", expected="file")

        with self.assertRaises(SharedResultError) as ctx:
            raise_if_failed(result)

        self.assertIs(ctx.exception.result, result)

    def test_stop_on_first_failure_returns_first_failure(self) -> None:
        success_result = ensure_directory(Path(tempfile.gettempdir()) / "shared-lib-stop-ok")
        failed_result = check_path("/tmp/shared-lib-test-missing-stop.txt", expected="file")

        result = stop_on_first_failure([success_result, failed_result])

        self.assertIs(result, failed_result)

    def test_config_helpers_load_merge_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.toml"
            target.write_text('[main]\ninput = "${INPUT_FILE:-fallback.csv}"\nlimit = 10\n', encoding="utf-8")

            result = load_toml_config(target, section="main", expand_env=True)
            merged = merge_settings({"limit": 5, "debug": False}, result.data["config"], {"debug": True})
            required = validate_required_settings(merged, ["input", "limit"])
            allowed = validate_allowed_settings(merged, ["input", "limit", "debug"])

            self.assertTrue(result.success)
            self.assertEqual(result.data["config"]["input"], "fallback.csv")
            self.assertEqual(merged["limit"], 10)
            self.assertTrue(merged["debug"])
            self.assertTrue(required.success)
            self.assertTrue(allowed.success)
            self.assertEqual(expand_env_vars("${MISSING:-x}", env={}), "x")

    def test_output_path_helpers_create_folder_and_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_output_folder(tmp, run_id="run 1")
            output = make_output_file_path(
                folder.data["output_dir"],
                base_name="My Report",
                output_format="csv",
                timestamp="20260101_010203",
            )

            self.assertTrue(folder.success)
            self.assertTrue(Path(folder.data["output_dir"]).is_dir())
            self.assertTrue(output.success)
            self.assertTrue(output.data["output_path"].endswith("My_Report_20260101_010203.csv"))

    def test_get_logger_is_idempotent_and_can_write_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = get_logger("shared_test_logger", log_dir=tmp, console=False)
            second = get_logger("shared_test_logger", log_dir=tmp, console=False)
            logger = first.data["logger"]

            logger.info("hello")

            self.assertTrue(first.success)
            self.assertTrue(second.success)
            self.assertIsInstance(logger, logging.Logger)
            self.assertEqual(len([h for h in logger.handlers if getattr(h, "_shared_log_path", None)]), 1)
            self.assertTrue(Path(first.data["log_path"]).is_file())

    def test_time_helpers_format_parse_and_summarize(self) -> None:
        parsed = parse_iso_timestamp("2026-05-02T10:20:30+00:00")
        stats = run_stats(0, 65)

        self.assertTrue(parsed.success)
        self.assertEqual(timestamp_compact(parsed.data["datetime"]), "20260502_102030")
        self.assertEqual(timestamp_compact_microseconds(parsed.data["datetime"]), "20260502_102030_000000")
        self.assertEqual(timestamp_iso(parsed.data["datetime"]), "2026-05-02T10:20:30.000000+00:00")
        self.assertEqual(format_duration(0.25), "0.25s")
        self.assertEqual(format_duration(65), "1.08min")
        self.assertEqual(stats["duration_human"], "1.08min")

    def test_cli_common_validate_output_targets_exclusive(self) -> None:
        self.assertIsNone(validate_output_targets_exclusive(None, None))
        self.assertIsNone(validate_output_targets_exclusive(Path("/a/out.csv"), None))
        self.assertIsNone(validate_output_targets_exclusive(None, Path("/tmp")))
        bad = validate_output_targets_exclusive(Path("/a.csv"), Path("/tmp"))
        self.assertIsNotNone(bad)
        assert bad is not None
        self.assertFalse(bad.success)
        self.assertTrue(effective_quiet(quiet=False, json_out=True))
        self.assertFalse(effective_quiet(quiet=False, json_out=False))

    def test_default_output_beside_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.csv"
            p.write_text("x", encoding="utf-8")
            r = default_output_beside_input(p, "parquet", supported_formats=("csv", "parquet", "json"))
            self.assertTrue(r.success)
            self.assertEqual(r.data["output_dir"], str(p.parent.resolve()))
            self.assertEqual(r.data["output_path"], str((p.parent / "a.parquet").resolve()))
            self.assertEqual(r.data["format"], "parquet")
            self.assertFalse(r.data["avoided_input_collision"])
            r2 = default_output_beside_input(p, "csv", supported_formats=("csv", "parquet"))
            self.assertTrue(r2.success)
            self.assertTrue(r2.data["avoided_input_collision"])
            self.assertEqual(r2.data["output_path"], str((p.parent / "a_converted.csv").resolve()))
            bad = default_output_beside_input(Path(tmp) / "b.csv", "pdf", supported_formats=("csv", "parquet"))
            self.assertFalse(bad.success)

    def test_uid_helpers_generate_nanoid_and_uuid(self) -> None:
        nano = generate_nanoid(10)
        uuid_value = generate_uuid()
        generic = generate_uid("uuid")
        invalid = generate_uid("uuid", length=8)
        unsupported = generate_uid("uuid7")

        self.assertTrue(nano.success)
        self.assertEqual(nano.data["kind"], "nanoid")
        self.assertEqual(len(nano.data["uid"]), 10)
        self.assertTrue(uuid_value.success)
        self.assertEqual(uuid_value.data["kind"], "uuid")
        self.assertEqual(uuid_value.data["version"], 7)
        self.assertEqual(uuid_value.data["length"], 36)
        self.assertEqual(len(uuid_value.data["uid"]), 36)
        self.assertIn("backend", uuid_value.data)
        self.assertNotIn("full_uid", uuid_value.data)
        self.assertTrue(generic.success)
        self.assertEqual(generic.data["kind"], "uuid")
        self.assertEqual(len(generic.data["uid"]), 36)
        self.assertFalse(invalid.success)
        self.assertFalse(unsupported.success)

    def test_sql_string_literal_escapes_quotes(self) -> None:
        from shared_lib.duckdb_sql import sql_double_quoted_ident, sql_string_literal

        self.assertEqual(sql_string_literal("a'b"), "'a''b'")
        self.assertEqual(sql_double_quoted_ident('a"b'), '"a""b"')

    def test_resolve_parquet_file_inputs_expands_glob(self) -> None:
        from shared_lib.parquet_input_paths import resolve_parquet_file_inputs

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.parquet"
            target.write_bytes(b"\0")
            found = resolve_parquet_file_inputs(
                [str(Path(tmp) / "*.parquet")],
                allow_empty_match=False,
                max_files=10,
            )
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].resolve(), target.resolve())

    def test_find_latest_files_orders_by_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            base = 1_700_000_000.0
            f_old = d / "a.txt"
            f_mid = d / "b.txt"
            f_new = d / "c.txt"
            f_old.write_text("a", encoding="utf-8")
            f_mid.write_text("b", encoding="utf-8")
            f_new.write_text("c", encoding="utf-8")
            os.utime(f_old, (base, base))
            os.utime(f_mid, (base + 10, base + 10))
            os.utime(f_new, (base + 20, base + 20))

            got = find_latest_files(d, top_n=2, sort_by="mtime")
            self.assertEqual(got, [f_new, f_mid])

    def test_find_latest_files_glob_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.log").write_text("1", encoding="utf-8")
            (d / "y.log").write_text("2", encoding="utf-8")
            (d / "z.csv").write_text("3", encoding="utf-8")
            t = time.time()
            os.utime(d / "x.log", (t, t - 100))
            os.utime(d / "y.log", (t, t))
            os.utime(d / "z.csv", (t, t - 50))

            got = find_latest_files(d, top_n=5, pattern="*.log", sort_by="mtime")
            self.assertEqual([p.name for p in got], ["y.log", "x.log"])

    def test_find_latest_file_returns_none_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_latest_file(tmp, pattern="*.missing"))

    def test_find_latest_files_invalid_top_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                find_latest_files(tmp, top_n=0)

    def test_find_latest_files_missing_directory(self) -> None:
        with self.assertRaises(FileNotFoundError):
            find_latest_files("/tmp/nonexistent-dir-shared-lib-xyz-999", top_n=1)

    def test_find_latest_files_recursive_finds_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "root.txt").write_text("r", encoding="utf-8")
            sub = d / "sub"
            sub.mkdir()
            nested = sub / "nested.txt"
            nested.write_text("n", encoding="utf-8")
            base = 1_700_000_000.0
            os.utime(d / "root.txt", (base, base))
            os.utime(nested, (base + 99, base + 99))

            flat = find_latest_files(d, top_n=5, pattern="*.txt", recursive=False, sort_by="mtime")
            self.assertEqual([p.resolve() for p in flat], [(d / "root.txt").resolve()])

            deep = find_latest_files(d, top_n=5, pattern="*.txt", recursive=True, sort_by="mtime")
            self.assertEqual([p.resolve() for p in deep], [nested.resolve(), (d / "root.txt").resolve()])

    def test_find_latest_files_invalid_sort_by(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                find_latest_files(tmp, top_n=1, sort_by="bogus")  # type: ignore[arg-type]


class DuckdbCreateLoadTests(unittest.TestCase):
    def test_load_invalid_table_name_fails_without_duckdb(self) -> None:
        from shared_lib.duckdb_create_load import load_file_into_database

        result = load_file_into_database(
            "/tmp/shared-lib-duckdb-missing-input-xyz.csv",
            "/tmp/out.duckdb",
            table_name="not-valid",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 2)

    def test_load_missing_input_fails_before_duckdb(self) -> None:
        from shared_lib.duckdb_create_load import load_file_into_database

        result = load_file_into_database(
            "/tmp/shared-lib-duckdb-missing-input-xyz.csv",
            "/tmp/out.duckdb",
            input_format="csv",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 3)

    def test_load_csv_rejects_and_database_when_duckdb_installed(self) -> None:
        try:
            import duckdb  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("duckdb not installed")

        from pathlib import Path

        from shared_lib.duckdb_create_load import DuckdbResourceLimits, load_file_into_database

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inp = base / "rows.csv"
            inp.write_text("a,b\n1,2\nbroken\n3,4\n", encoding="utf-8")
            db = base / "store.duckdb"
            res = load_file_into_database(
                inp,
                db,
                limits=DuckdbResourceLimits(memory_limit="512MB", threads=2),
            )

            self.assertTrue(res.success)
            self.assertEqual(res.data["rows_loaded"], 2)
            self.assertEqual(res.data["columns_loaded"], 2)
            self.assertEqual(len(res.data["column_names"]), 2)
            self.assertGreater(res.data["input_size_bytes"], 0)
            self.assertIsInstance(res.data["input_size_human"], str)
            self.assertIsNotNone(res.data.get("input_physical_lines"))
            self.assertGreaterEqual(res.data["rejects_distinct_lines"], 1)
            self.assertGreaterEqual(res.data["rejects_error_entries"], 1)
            self.assertIsNotNone(res.data.get("rejects_csv_path"))
            rej_path = Path(res.data["rejects_csv_path"])
            self.assertTrue(rej_path.is_file())
            self.assertIn("csv_line", rej_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
