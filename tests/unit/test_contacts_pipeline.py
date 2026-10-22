"""Contact matching's core steps (the functions the studio's nodes and an exported CLI both call):
any layout of the same exports reads the same through the column map, a missing column is named,
and the whole pipeline on generated contacts finds most of the true groups.

Verifies: R7, R33
"""

import csv
import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.core import contacts as C
from custom_nodes.agent_workflow_studio.core import steps
from custom_nodes.agent_workflow_studio.core.candidates import find_pairs
from custom_nodes.agent_workflow_studio.core.context import Context
from custom_nodes.agent_workflow_studio.core.features import FEATURES, features
from custom_nodes.agent_workflow_studio.nayose_bench.contacts import generate_contacts
from custom_nodes.agent_workflow_studio.nayose_bench.score import score


def write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([header, *rows])


def step_ctx(root: Path, name: str, labels: dict | None = None) -> Context:
    run = root / "runs" / f"{name}-1"
    run.mkdir(parents=True, exist_ok=True)
    return Context(input_dir=root / "input", run_dir=run, labels=labels or {})


class ColumnMapTests(unittest.TestCase):
    def test_two_layouts_of_the_same_contacts_read_the_same(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Our shape: three tables on CONT_ID.
            write(root / "input/a/contacts.csv", ["CONT_ID", "LAST_NAME_KANJI", "FIRST_NAME_KANJI", "LAST_NAME_KANA", "FIRST_NAME_KANA", "BIRTH_DATE", "GENDER", "CONT_TYPE"],
                  [["1", "山田", "太郎", "ヤマダ", "タロウ", "1980-01-02", "M", "1"], ["2", "株式会社サンプル", "", "サンプル", "", "1999-04-01", "", "2"]])  # fmt: skip
            write(
                root / "input/a/phones.csv",
                ["CONT_ID", "PHONE"],
                [["1", "03-1234-5678"], ["1", "090-1111-2222"]],
            )
            write(
                root / "input/a/addresses.csv",
                ["CONT_ID", "ADDRESS_KANJI"],
                [["2", "東京都新宿区西新宿2-8-1"]],
            )
            # Another CRM: other names, a two-column key, slashed dates, phones as columns,
            # the address split into parts, company type as "法人".
            write(root / "input/b/顧客.csv", ["SYS", "ID", "姓", "名", "セイ", "メイ", "生年月日", "性別", "区分", "TEL1", "TEL2", "都道府県", "住所"],
                  [["X", "1", "山田", "太郎", "ヤマダ", "タロウ", "1980/01/02", "男", "個人", "03-1234-5678", "090-1111-2222", "", ""],
                   ["X", "2", "株式会社サンプル", "", "サンプル", "", "1999/04/01", "", "法人", "", "", "東京都", "新宿区西新宿2-8-1"]])  # fmt: skip
            other = """
key: [SYS, ID]
contacts: 顧客.csv
surname: {kanji: 姓, kana: セイ}
given: {kanji: 名, kana: メイ}
birth_date: {column: 生年月日, format: "%Y/%m/%d"}
gender: {column: 性別, codes: {M: [男], F: [女]}}
type: {column: 区分, company: [法人], person: [個人]}
phones: {columns: [TEL1, TEL2]}
addresses: {kanji: [都道府県, 住所]}
"""
            ours = C.COLUMNS.replace(", romaji: LAST_NAME_ROMAJI", "").replace(
                ", romaji: FIRST_NAME_ROMAJI", ""
            )
            a, stats_a = C.read_contacts(step_ctx(root, "a"), "a", ours)
            b, stats_b = C.read_contacts(step_ctx(root, "b"), "b", other)
            strip = lambda rows: [{k: v for k, v in r.items() if k != "key"} for r in rows]  # noqa: E731
            self.assertEqual(strip(a), strip(b))
            self.assertEqual([r["key"] for r in b], ["X|1", "X|2"])
            self.assertEqual(stats_a["contacts"], stats_b["contacts"])
            self.assertEqual(a[1]["flag"], "2")
            self.assertEqual(
                C.standardize(a, {})[1].core_k, C.standardize(b, {})[1].core_k
            )  # the same company core either way

    def test_shift_jis_exports_and_reviews_saved_from_excel_are_read(self) -> None:
        from custom_nodes.agent_workflow_studio.core.runner import read_reviews

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input/sjis").mkdir(parents=True)
            text = "CONT_ID,姓,生年月日\n1,山田,1980-01-02\n2,髙橋,1975-05-05\n"
            (root / "input/sjis/顧客.csv").write_bytes(text.encode("cp932"))
            rows, _ = C.read_contacts(
                step_ctx(root, "a"),
                "sjis",
                "key: CONT_ID\ncontacts: 顧客.csv\nsurname: {kanji: 姓}\nbirth_date: 生年月日\n",
            )
            self.assertEqual([r["sur_kanji"] for r in rows], ["山田", "髙橋"])
            (root / "reviews.csv").write_bytes(
                "id,decision,a_name\n1|2,different,山田\n".encode("cp932")
            )
            self.assertEqual(read_reviews(root / "reviews.csv"), {"1|2": "different"})

    def test_a_missing_column_is_named_with_what_to_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "input/a/contacts.csv", ["CONT_ID", "姓"], [["1", "山田"]])
            with self.assertRaises(ValueError) as caught:
                C.read_contacts(step_ctx(root, "a"), "a", "")
            self.assertIn("BIRTH_DATE (birth_date) is not in contacts.csv", str(caught.exception))
            self.assertIn("Map birth_date in the column map (or remove it", str(caught.exception))


class StandardizeTests(unittest.TestCase):
    def test_a_guessed_unvoiced_reading_meets_but_different_sounds_do_not(self) -> None:
        def meet(a: str, b: str) -> bool:
            raw = [{"key": k, "sur_kana": s, "giv_kana": "ハナコ"} for k, s in (("1", a), ("2", b))]
            return find_pairs(C.standardize(raw, {}), passes=("exact",))[0] == [(0, 1)]

        self.assertTrue(meet("ナカヅカ", "ナカツカ"))  # 中塚: a clerk's unvoiced guess
        self.assertTrue(meet("ナカヅカ", "ナカズカ"))  # one sound, two spellings
        self.assertTrue(meet("ヤマザキ", "ヤマサキ"))
        self.assertFalse(meet("マツダ", "マスダ"))  # 松田 and 増田 are different names

    def test_a_mistyped_birth_year_is_no_outlier(self) -> None:
        raw = [
            {"key": "1", "sur_kana": "ヤマダ", "birth_date": d}
            for d in ("1067-03-03", "1967-03-03")
        ]
        records = C.standardize(raw, {})
        X = features(records, [(0, 1)], C.frequencies(records))
        self.assertEqual(X[0][FEATURES.index("dob_year_diff")], 50)  # as far apart as unknown


class PipelineTests(unittest.TestCase):
    def test_generated_contacts_are_matched_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generate_contacts(root / "input/bench", root / "key", 1500, seed=3, profile="hard")
            key = root / "key" / "answer_key_contacts.csv"
            (raw,) = steps.read_contacts(step_ctx(root, "read_contacts"), "bench", "")
            (records,) = steps.standardize_contacts(step_ctx(root, "standardize_contacts"), raw, "", True, True, True, True, True)  # fmt: skip
            (candidates,) = steps.find_contact_candidates(step_ctx(root, "find"), records, True, True, True, True, 50, 20, 0.5)  # fmt: skip
            (pairs,) = steps.compare_contacts(step_ctx(root, "compare"), records, candidates)
            learn = step_ctx(root, "learn_from_reviews")
            scored, summary = steps.learn_from_reviews(learn, pairs, 50, True, str(key), 300, 0)
            self.assertIn("simulated reviewer:", summary)  # whole batches: at least the budget
            decisions, queue, _ = steps.decide_pairs(
                step_ctx(root, "decide"), scored, 0.5, 0.1, False
            )
            (clusters,) = steps.cluster_guard(step_ctx(root, "cluster_guard"), decisions, 12, True)
            (entities,) = steps.entity_per_contact(step_ctx(root, "entities"), raw, clusters)
            rows = steps.read_csv(entities)
            self.assertEqual(len(rows), len(C.load_jsonl(Path(raw))))  # every contact, once
            result = root / "result.csv"
            steps.write_csv(
                result,
                ({"system": "contacts", "record_id": r["record"], "role": "contact", "customer_id": r["cluster"]} for r in rows),
                ["system", "record_id", "role", "customer_id"],
            )  # fmt: skip
            report = score(key.parent, result)
            self.assertGreater(report["pairwise"]["f1"], 0.8)
            self.assertGreater(report["pairwise"]["precision"], 0.85)

    def test_your_reviews_train_the_model_and_it_asks_about_the_next_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generate_contacts(root / "input/bench", root / "key", 600, seed=5, profile="base")
            (raw,) = steps.read_contacts(step_ctx(root, "read"), "bench", "")
            (records,) = steps.standardize_contacts(
                step_ctx(root, "std"), raw, "", True, True, True, True, True
            )
            (candidates,) = steps.find_contact_candidates(step_ctx(root, "find"), records, True, True, True, True, 50, 20, 0.5)  # fmt: skip
            (pairs,) = steps.compare_contacts(step_ctx(root, "compare"), records, candidates)
            day0 = step_ctx(root, "learn_from_reviews-day0")
            steps.learn_from_reviews(day0, pairs, 40, True)
            asked = [
                p["id"]
                for p in __import__("json").loads((day0.run_dir / "review.json").read_text())[
                    "pairs"
                ]
            ]
            self.assertGreaterEqual(len(asked), 40)
            truth = {
                r["record_id"]: r["entity_id"]
                for r in steps.read_csv(root / "key/answer_key_contacts.csv")
            }
            labels = {
                i: "same" if truth[i.split("|")[0]] == truth[i.split("|")[1]] else "different"
                for i in asked
            }
            later = step_ctx(root, "learn_from_reviews-day1", labels)
            scored, summary = steps.learn_from_reviews(later, pairs, 40, True)
            self.assertIn(f"learned from {len(labels)} of your reviews", summary)
            again = __import__("json").loads((later.run_dir / "review.json").read_text())["pairs"]
            self.assertTrue(
                again and not {p["id"] for p in again if p["branch"] == "Least sure"} & set(labels)
            )
            reviewed = {r["id"]: r for r in C.load_jsonl(Path(scored)) if r["label"]}
            self.assertEqual(len(reviewed), len(labels))
            self.assertTrue(
                all(r["score"] == (1.0 if r["label"] == "same" else 0.0) for r in reviewed.values())
            )


if __name__ == "__main__":
    unittest.main()
