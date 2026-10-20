"""The nayose benchmark is deterministic, its key matches its data, and the scorer is exact."""

import csv
import io
import tempfile
import unittest
from pathlib import Path

from .generate import activity, export, generate, population
from .score import score


def mentions_in_data(data: Path) -> set:
    found = set()
    for name, system, encoding in (
        ("policy_master.csv", "policies", "cp932"),
        ("claims.csv", "claims", "utf-8-sig"),
        ("koza_furikae.csv", "payments", "cp932"),
    ):
        for row in csv.DictReader(io.StringIO((data / name).read_bytes().decode(encoding))):
            found.add((system, row["REC_ID"]))
    return found


class Bench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.stats = generate(cls.root / "a", cls.root / "key", households=150, seed=3)
        generate(cls.root / "b", cls.root / "key_b", households=150, seed=3)
        cls.key = []
        for part in sorted((cls.root / "key").glob("answer_key*.csv")):
            with open(part, encoding="utf-8") as f:
                cls.key += list(csv.DictReader(f))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def write(self, rows):
        path = self.root / "result.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["system", "record_id", "role", "customer_id"])
            writer.writerows(rows)
        return path

    def test_deterministic(self):
        for name in ("policy_master.csv", "claims.csv", "koza_furikae.csv"):
            self.assertEqual(
                (self.root / "a" / name).read_bytes(), (self.root / "b" / name).read_bytes()
            )

    def test_key_points_at_real_rows_and_data_is_dirty(self):
        rows = mentions_in_data(self.root / "a")
        self.assertTrue(all((k["system"], k["record_id"]) in rows for k in self.key))
        for tag in (
            "ocr",
            "old_surname",
            "stale_address",
            "zengin",
            "homophone",
            "kana_only",
            "old_city_name",
        ):
            self.assertIn(tag, self.stats["tags"])

    def test_damage_setting_scales_the_dirt(self):
        facts = activity(population(80, 5), 5)
        clean = sum(bool(k["tags"]) for k in export(facts, "claims", damage=0)[1])
        dirty = sum(bool(k["tags"]) for k in export(facts, "claims", damage=2)[1])
        self.assertLess(clean, dirty)
        rows, _ = export(facts, "claims", damage=0)
        self.assertTrue(all(r["SHOKEN_NO"] for r in rows), "no damage: no lost policy references")

    def test_perfect_result_scores_one(self):
        report = score(
            self.root / "key",
            self.write(
                [(k["system"], k["record_id"], k["role"], k["entity_id"]) for k in self.key]
            ),
        )
        self.assertEqual(report["pairwise"]["f1"], 1.0)
        self.assertEqual(report["bcubed"]["f1"], 1.0)
        self.assertEqual(report["false_merges"], {})

    def test_everyone_alone_has_no_recall_and_one_customer_merges_everything(self):
        alone = score(self.root / "key", self.write([]))
        self.assertEqual(
            (alone["pairwise"]["recall"], alone["pairwise"]["precision"], alone["covered"]),
            (0.0, 1.0, 0.0),
        )
        merged = score(
            self.root / "key",
            self.write([(k["system"], k["record_id"], k["role"], "X") for k in self.key]),
        )
        self.assertEqual(merged["pairwise"]["recall"], 1.0)
        self.assertLess(merged["pairwise"]["precision"], 0.05)

    def test_funnel_says_where_matches_are_lost(self):
        from .score import funnel

        perfect = self.write(
            [(k["system"], k["record_id"], k["role"], k["entity_id"]) for k in self.key]
        )
        mention = lambda k: f"{k['system']}/{k['record_id']}/{k['role']}"  # noqa: E731
        from collections import defaultdict
        from itertools import combinations

        groups = defaultdict(list)
        for k in self.key:
            groups[k["entity_id"]].append(mention(k))
        everyone = [pair for g in groups.values() for pair in combinations(g, 2)]  # all true pairs
        counts = funnel(self.root / "key", perfect, everyone)["counts"]
        self.assertEqual(counts["same customer"], counts["true pairs"])
        lost = funnel(self.root / "key", self.write([]), [])["counts"]
        self.assertEqual(lost["never a candidate"], lost["true pairs"])
        alone = funnel(self.root / "key", self.write([]), everyone)["counts"]
        self.assertEqual(alone["candidate, not merged"], alone["true pairs"])


if __name__ == "__main__":
    unittest.main()
