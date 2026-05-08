from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    import duckdb  # noqa: F401

    _HAVE_DUCKDB = True
except ImportError:
    _HAVE_DUCKDB = False

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    _HAVE_PYARROW = True
except ImportError:
    _HAVE_PYARROW = False

from shared_lib.parquet_row_audit import (
    export_tagged_data_report_csv,
    run_parquet_row_audit,
    write_parquet_row_audit_report,
)


@unittest.skipUnless(_HAVE_DUCKDB and _HAVE_PYARROW, "duckdb and pyarrow required")
class ParquetRowAuditTests(unittest.TestCase):
    def test_counts_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.parquet"
            table = pa.table(
                {
                    "id": [1, 2, 2, None, None],
                    "name": ["a", "b", "b", "   ", None],
                    "x": [1.0, 2.0, 2.0, None, None],
                }
            )
            pq.write_table(table, str(p))

            r = run_parquet_row_audit(p)
            self.assertTrue(r.success, r.message)
            d = r.data
            self.assertEqual(d["column_count"], 3)
            self.assertEqual(d["row_count"], 5)
            self.assertGreaterEqual(d["blank_rows"], 2)
            self.assertEqual(d["full_row_distinct_count"], 4)
            self.assertEqual(d["full_row_duplicate_surplus_rows"], 1)

            rk = run_parquet_row_audit(p, key_columns=["id"])
            self.assertTrue(rk.success)
            dk = rk.data
            self.assertEqual(dk["unique_key_count"], 3)
            self.assertEqual(dk["duplicate_rows_on_keys"], 2)

    def test_write_report_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.parquet"
            pq.write_table(pa.table({"k": [1, 1]}), str(p))
            r = run_parquet_row_audit(p, key_columns=["k"])
            self.assertTrue(r.success)
            out = Path(tmp) / "rep.csv"
            w = write_parquet_row_audit_report(r.data, out, output_format="csv")
            self.assertTrue(w.success, w.message)
            text = out.read_text(encoding="utf-8")
            self.assertIn("row_count", text)
            self.assertIn("2", text)

    def test_tagged_data_report_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "t.parquet"
            pq.write_table(pa.table({"a": [1, 1], "b": ["x", "y"]}), str(p))
            out = Path(tmp) / "tagged.csv"
            r = export_tagged_data_report_csv(p, out, key_columns=["a"])
            self.assertTrue(r.success, r.message)
            body = out.read_text(encoding="utf-8")
            self.assertIn("__tag__", body)
            self.assertIn("__key__", body)
            self.assertIn("duplicate", body)
