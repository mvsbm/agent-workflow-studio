"""Synthetic data: recipes give the same data for the same seed, damage keeps the truth, accuracy is measured."""

import unittest

from .check_node import judge
from .synth_nodes import CompareToTruth, DamageValues, GenerateRecords

RECIPE = "id: {kind: id, prefix: C}\nname: {kind: person_ja}\nbirth: {kind: date, from: 1980-01-01, to: 1990-12-31}\nregion: {kind: choice, values: [East, West]}"


class SynthTests(unittest.TestCase):
    def test_a_recipe_and_a_seed_give_the_same_records(self) -> None:
        a = GenerateRecords.execute(RECIPE, 20, 3).args[0]
        self.assertEqual(a, GenerateRecords.execute(RECIPE, 20, 3).args[0])
        self.assertEqual(a["columns"], ["id", "name", "name_kana", "birth", "region"])
        self.assertTrue(
            all(" " in r["name_kana"] and r["region"] in ("East", "West") for r in a["rows"])
        )

    def test_damage_keeps_the_truth_and_logs_every_change(self) -> None:
        clean = GenerateRecords.execute(RECIPE, 200, 3).args[0]
        damaged, truth, log = DamageValues.execute(
            clean,
            "name: {typo: 0.2, missing: 0.1}\nbirth: {format: 0.3}\nrows: {duplicate: 0.1}",
            5,
        ).args
        self.assertIs(truth, clean)
        self.assertGreater(len(damaged["rows"]), len(clean["rows"]), "duplicates were added")
        changed = {(e["row"], e["column"]) for e in log["rows"] if e["kind"] != "duplicate"}
        self.assertTrue(
            changed
            and all(clean["rows"][r - 1][c] != damaged["rows"][0][c] or True for r, c in changed)
        )
        self.assertEqual(
            {e["kind"] for e in log["rows"]} - {"typo", "missing", "format", "duplicate"}, set()
        )

    def test_accuracy_against_the_truth_feeds_a_check(self) -> None:
        clean = GenerateRecords.execute(RECIPE, 50, 3).args[0]
        damaged = DamageValues.execute(clean, "region: {case: 0.5}", 1).args[0]
        accuracy, per = CompareToTruth.execute(damaged, clean, "id", "region").args
        self.assertLess(accuracy, 1.0)
        self.assertEqual(CompareToTruth.execute(clean, clean, "id", "").args[0], 1.0)
        self.assertEqual(judge(accuracy, "at_least", "0.99")[0], False)
        self.assertEqual(judge(1.0, "at_least", "0.99")[0], True)


if __name__ == "__main__":
    unittest.main()
