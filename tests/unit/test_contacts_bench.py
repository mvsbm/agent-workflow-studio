"""The contacts benchmark (CRM shape: contacts, phones, addresses joined on CONT_ID) is
deterministic, its tables and answer key agree, and the scorer gives the truth a perfect score."""

# Verifies: R33
import csv
import datetime as dt
import random
import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.nayose_bench.contacts import (
    PROFILES,
    generate_contacts,
    loanword,
    romaji,
)
from custom_nodes.agent_workflow_studio.nayose_bench.score import score


def rows(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


class ContactsBenchTests(unittest.TestCase):
    def test_tables_and_key_agree_and_the_truth_scores_perfectly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stats = generate_contacts(root / "data", root / "key", 1500, seed=3, profile="hard")
            contacts = rows(root / "data" / "contacts.csv")
            ids = {r["CONT_ID"] for r in contacts}
            self.assertEqual(len(ids), len(contacts), "CONT_ID is unique")
            self.assertEqual(stats["contacts"], len(contacts))
            for name in ("phones.csv", "addresses.csv"):
                self.assertTrue({r["CONT_ID"] for r in rows(root / "data" / name)} <= ids)
            for r in contacts:  # people only in this profile: the birth date is always a date
                dt.date.fromisoformat(r["BIRTH_DATE"])
                self.assertIn(r["CONT_TYPE"], ("1", "2"))
            key = rows(root / "key" / "answer_key_contacts.csv")
            self.assertEqual({k["record_id"] for k in key}, ids)
            self.assertGreater(len({k["entity_id"] for k in key}), len(key) // 4)
            truth = root / "truth.csv"
            with open(truth, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["system", "record_id", "role", "customer_id"])
                w.writerows([k["system"], k["record_id"], k["role"], k["entity_id"]] for k in key)
            report = score(root / "key", truth)
            self.assertEqual(report["pairwise"]["f1"], 1.0)
            self.assertEqual(report["bcubed"]["f1"], 1.0)

    def test_the_same_seed_gives_the_same_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run in ("a", "b"):
                generate_contacts(
                    root / run / "data", root / run / "key", 800, seed=5, profile="wild"
                )
            for name in ("contacts.csv", "phones.csv", "addresses.csv"):
                self.assertEqual(
                    (root / "a" / "data" / name).read_bytes(),
                    (root / "b" / "data" / name).read_bytes(),
                )

    def test_romaji_is_generated_from_the_kana_and_loanwords_vary(self) -> None:
        self.assertEqual(romaji("サトウ タロウ"), "SATOU TAROU")
        self.assertEqual(romaji("ｷｯﾁｮｳ"), "KITCHOU")
        self.assertEqual(romaji("じゅんいち"), "JUNICHI")
        variants = {loanword(random.Random(s), "コンピューター・サービス") for s in range(40)}
        self.assertIn("コンピューター・サービス".replace("・", ""), variants)
        self.assertGreater(len(variants), 1)
        self.assertTrue({"real", "company", "company_hard"} <= set(PROFILES))


if __name__ == "__main__":
    unittest.main()
