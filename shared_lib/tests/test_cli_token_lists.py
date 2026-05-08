from __future__ import annotations

import unittest

from shared_lib.cli_token_lists import (
    parse_comma_separated_cli_tokens,
    parse_json_array_of_strings,
    resolve_comma_or_json_cli_list,
)
from shared_lib.results import EXIT_VALIDATION_ERROR


class TestParseCommaSeparatedCliTokens(unittest.TestCase):
    def test_repeat_and_comma(self) -> None:
        self.assertEqual(parse_comma_separated_cli_tokens(["a,b", "c"]), ["a", "b", "c"])

    def test_dedupe_order(self) -> None:
        self.assertEqual(parse_comma_separated_cli_tokens(["a", "b", "a"]), ["a", "b"])

    def test_strip(self) -> None:
        self.assertEqual(parse_comma_separated_cli_tokens([" x ", "y, z"]), ["x", "y", "z"])


class TestParseJsonArrayOfStrings(unittest.TestCase):
    def test_ok(self) -> None:
        r = parse_json_array_of_strings('["a","b"]', label="L")
        self.assertTrue(r.success)
        self.assertEqual(r.data["items"], ["a", "b"])

    def test_invalid_json(self) -> None:
        r = parse_json_array_of_strings("{", label="L")
        self.assertFalse(r.success)
        self.assertEqual(r.exit_code, EXIT_VALIDATION_ERROR)

    def test_not_list(self) -> None:
        r = parse_json_array_of_strings('"x"', label="L")
        self.assertFalse(r.success)


class TestResolveCommaOrJsonCliList(unittest.TestCase):
    def test_comma_only(self) -> None:
        r = resolve_comma_or_json_cli_list(["a,b"], None, data_key="columns")
        self.assertTrue(r.success)
        self.assertEqual(r.data["columns"], ["a", "b"])

    def test_json_only(self) -> None:
        r = resolve_comma_or_json_cli_list([], '["x","y"]', data_key="k")
        self.assertTrue(r.success)
        self.assertEqual(r.data["k"], ["x", "y"])

    def test_mutual_exclusive_conflict(self) -> None:
        r = resolve_comma_or_json_cli_list(["a"], '["b"]', mutual_exclusive=True)
        self.assertFalse(r.success)
        self.assertIn("not both", r.message)

    def test_merge_when_not_exclusive(self) -> None:
        r = resolve_comma_or_json_cli_list(
            ["c"],
            '["a","b"]',
            mutual_exclusive=False,
            data_key="items",
        )
        self.assertTrue(r.success)
        self.assertEqual(r.data["items"], ["a", "b", "c"])

    def test_empty(self) -> None:
        r = resolve_comma_or_json_cli_list([], None)
        self.assertFalse(r.success)

    def test_empty_allowed(self) -> None:
        r = resolve_comma_or_json_cli_list([], None, non_empty=False)
        self.assertTrue(r.success)
        self.assertEqual(r.data["items"], [])


if __name__ == "__main__":
    unittest.main()
