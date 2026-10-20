"""Profile data: patterns make mixed formats visible; columns report fill and distinct values."""

# Verifies: R6
import unittest

from custom_nodes.agent_workflow_studio.eda_node import pattern, profile_rows


class ProfileTests(unittest.TestCase):
    def test_patterns_show_the_shape_of_values(self) -> None:
        self.assertEqual(pattern("S45.03.12"), "A9+.9+.9+")
        self.assertEqual(pattern("1970/03/12"), "9+/9+/9+")
        self.assertEqual(pattern("ﾔﾏﾀﾞ ﾀﾛｳ"), "ｶ+␣ｶ+")
        self.assertEqual(pattern("山田　太郎"), "漢+␣漢+")
        self.assertEqual(pattern("やまだ"), "ひ+")

    def test_columns_report_fill_distinct_and_mixed_formats(self) -> None:
        rows = [
            {"birth": "S45.03.12", "name": "山田"},
            {"birth": "1970/03/12", "name": ""},
            {"birth": "3450312", "name": "山田"},
        ]
        birth, name = profile_rows(rows)
        self.assertEqual(
            (birth["filled"], birth["distinct"], birth["pattern count"]), ("100%", "3", 3)
        )
        self.assertIn("A9+.9+.9+ (33%)", birth["patterns"])
        self.assertEqual((name["filled"], name["distinct"]), ("67%", "1"))


if __name__ == "__main__":
    unittest.main()
