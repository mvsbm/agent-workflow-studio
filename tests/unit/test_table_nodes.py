"""Table nodes: each operation does one visible thing, and says what it changed."""

# Verifies: R6, R34

import unittest

from custom_nodes.agent_workflow_studio.table_nodes import (
    compare_dates,
    compare_value,
    jaro_winkler,
    make,
    normalize,
    parse_date,
    report,
    similar_pairs,
)


class TableNodeTests(unittest.TestCase):
    def test_parse_dates_in_every_form_and_names_the_form(self) -> None:
        cases = {
            "S45.03.12": ("1970-03-12", "era letter"),
            "昭和45年3月12日": ("1970-03-12", "era kanji"),
            "平成元年1月8日": ("1989-01-08", "era kanji"),
            "3450312": ("1970-03-12", "era code"),
            "1970/3/12": ("1970-03-12", "year first"),
            "19700312": ("1970-03-12", "compact"),
            "70/03/12": ("1970-03-12", "two-digit"),
            "１９７０－０３－１２": ("1970-03-12", "year first"),
            "S45.02.30": ("", "impossible"),
            "不明": ("", "not a date"),
        }
        for text, (iso, kind) in cases.items():
            with self.subTest(text=text):
                value, form = parse_date(text)
                self.assertEqual(value, iso)
                self.assertIn(kind, form)

    def test_normalize_steps(self) -> None:
        self.assertEqual(normalize("ｽｽﾞｷ　ﾀﾛｳ", True, True, True, True, False), "スズキタロウ")
        self.assertEqual(normalize("きょうこ", True, True, True, True, False), "キヨウコ")
        self.assertEqual(normalize("ＡＢＣ  def", True, False, False, False, True), "ABC DEF")

    def test_comparison_levels(self) -> None:
        self.assertEqual(compare_value("ヤマダタロウ", "ヤマダタロウ", "text", 0.88), "exact")
        self.assertEqual(compare_value("ヤマダタロウ", "ヤマダタロ", "text", 0.88), "similar")
        self.assertEqual(compare_value("ヤマダタロウ", "スズキハナコ", "text", 0.88), "different")
        self.assertEqual(compare_value("", "x", "text", 0.88), "missing")
        self.assertEqual(compare_value("0312345678", "0312345678", "same", 0), "same")
        self.assertEqual(
            compare_dates("1970-03-12", "1970-12-03"), "close"
        )  # day and month swapped
        self.assertEqual(compare_dates("1970-03-12", "2033-03-12"), "close")  # Shōwa read as Heisei
        self.assertEqual(compare_dates("1970-03-12", "1975-08-01"), "conflict")
        self.assertGreater(jaro_winkler("MARTHA", "MARHTA"), 0.96)

    def test_report_shows_what_changed(self) -> None:
        before = make("t", [{"kana": "ｽｽﾞｷ"}, {"kana": "サトウ"}])
        after = make("t", [{"kana": "スズキ"}, {"kana": "サトウ"}])
        tables = report("Normalize text", before, after, {}, ["kana"])
        self.assertEqual(tables["aws_tables"][1]["rows"], [["kana", "ｽｽﾞｷ", "スズキ"]])

    def test_map_parts_of_values(self) -> None:
        from custom_nodes.agent_workflow_studio.table_nodes import MapValues

        t = make("t", [{"kana": "カ)サンワケンセツ"}, {"kana": "サンワコウギヨウ(カ"}])
        out = MapValues.execute(t, "kana", "カ): (カ:", "parts of values").args[0]
        self.assertEqual([r["kana"] for r in out["rows"]], ["サンワケンセツ", "サンワコウギヨウ"])

    def test_similar_values_meet_despite_a_typo_or_a_cut(self) -> None:
        values = [
            ("a", "ヒラベキキ"),
            ("b", "ヒラベキ"),
            ("c", "ヒラヘキキ"),
            ("d", "スズキハナコ"),
        ]
        self.assertEqual(
            similar_pairs(values, 0.5), {("a", "b"), ("a", "c")}
        )  # one typo in five: 0.5

    def test_each_run_writes_its_own_file_like_comfyuis_save_nodes(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from custom_nodes.agent_workflow_studio.nayose_nodes import find_file
        from custom_nodes.agent_workflow_studio.table_nodes import WriteTable

        table = make("orders", [{"id": "1", "region": "north"}], ["id", "region"])
        with (
            tempfile.TemporaryDirectory() as root,
            patch("folder_paths.get_output_directory", return_value=root),
            patch("folder_paths.get_input_directory", return_value=root),
        ):
            first = WriteTable.execute(table, "api/result.csv", "").result[0]
            second = WriteTable.execute(table, "api/result.csv", "").result[0]
            self.assertEqual([first, second], ["api/result_00001_.csv", "api/result_00002_.csv"])
            self.assertTrue((Path(root) / first).is_file(), "an earlier call's file stays")
            # Asked for by its plain name, the newest run's file answers.
            self.assertEqual(find_file("api/result.csv", None).name, "result_00002_.csv")
            with self.assertRaisesRegex(ValueError, "under ComfyUI's output directory"):
                WriteTable.execute(table, "../escape.csv", "")

    def test_follow_a_record_through_the_run(self) -> None:
        from comfy_execution.utils import CurrentNodeContext

        from custom_nodes.agent_workflow_studio import table_nodes

        before = make("t", [{"id": "P-1", "kana": "ｽｽﾞｷ"}, {"id": "P-2", "kana": "サトウ"}])
        after = make("t", [{"id": "P-1", "kana": "スズキ"}])
        for node, table in (("4:1", before), ("4:2", after)):
            with CurrentNodeContext("p", node, None):
                report("step", None, table)
        steps = {s["node"]: s for s in table_nodes.follow("p-2")}
        self.assertEqual((steps["4:1"]["count"], steps["4:2"]["count"]), (1, 0))  # dropped at 4:2
        self.assertEqual(table_nodes.follow("P-1")[-1]["rows"], [{"id": "P-1", "kana": "スズキ"}])


if __name__ == "__main__":
    unittest.main()
