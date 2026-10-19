"""Check: each kind judges the data a stage gives, and says why."""

import unittest

from .check_node import judge


class CheckTests(unittest.TestCase):
    def test_rows_from_tables_and_branch_records(self) -> None:
        table = {"name": "t", "columns": ["a"], "rows": [{"a": 1}, {"a": 2}]}
        self.assertEqual(judge(table, "rows_at_least", "2"), (True, "2 row(s)"))
        self.assertEqual(judge({"count": 0, "file": "x.csv"}, "non_empty", ""), (False, "0 row(s)"))

    def test_text_kinds(self) -> None:
        self.assertTrue(judge("hello world", "contains", "world")[0])
        self.assertTrue(judge({"text": " done "}, "equals", "done")[0])
        self.assertFalse(judge("abc", "regex", r"^\d+$")[0])

    def test_nothing_is_empty(self) -> None:
        self.assertEqual(judge(None, "non_empty", ""), (False, "nothing"))
        self.assertEqual(judge("output/report.csv", "non_empty", ""), (True, "a value"))


if __name__ == "__main__":
    unittest.main()
