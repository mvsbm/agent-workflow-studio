"""Check: each kind judges the data a stage gives, and says why."""

# Verifies: R14, R16
import unittest

from custom_nodes.agent_workflow_studio.check_node import judge


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

    def test_an_api_output_is_a_file_app_mode_can_show(self) -> None:
        import csv as csv_module
        import tempfile
        from pathlib import Path

        from custom_nodes.agent_workflow_studio.check_node import app_file

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            table = app_file("customers", {"columns": ["id"], "rows": [{"id": "C1"}]}, out)
            self.assertEqual((table["subfolder"], table["type"]), ("app_outputs", "output"))
            self.assertTrue(table["filename"].endswith(".csv"))
            rows = list(csv_module.DictReader(open(out / "app_outputs" / table["filename"])))
            self.assertEqual(rows, [{"id": "C1"}])
            self.assertTrue(app_file("summary", {"f1": 0.9}, out)["filename"].endswith(".json"))
            (out / "nayose").mkdir()
            (out / "nayose" / "result.csv").write_text("a\n1\n")
            written = app_file(
                "customer_file", "nayose/result.csv", out
            )  # shown as the workflow wrote it
            self.assertEqual(
                written, {"filename": "result.csv", "subfolder": "nayose", "type": "output"}
            )
            self.assertTrue(
                app_file("x", "../../etc/passwd", out)["filename"].endswith(".txt")
            )  # never outside
            # A dataset answers with its rows (never the server's path) and shows as its own file.
            from custom_nodes.agent_workflow_studio.check_node import plain

            ref = {"kind": "studio#dataset", "class": "record_clusters", "name": "entities",
                   "uri": str(out / "nayose" / "result.csv"), "format": "csv", "_run_folders": []}  # fmt: skip
            self.assertEqual(
                plain(ref),
                {"name": "entities", "columns": ["a"], "rows": [{"a": "1"}], "total_rows": 1},
            )
            self.assertEqual(app_file("entities", ref, out)["subfolder"], "nayose")


if __name__ == "__main__":
    unittest.main()
