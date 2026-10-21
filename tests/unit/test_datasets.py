"""Datasets pass by reference: a step reads exactly the file it was given, never the newest by name."""

# Verifies: R11
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from custom_nodes.agent_workflow_studio.datasets import (
    dataset_file,
    lineage_file,
    ref,
    subset_ids,
)
from custom_nodes.agent_workflow_studio.standards import value_problems


class DatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name) / "output"
        self.patches = [
            patch("folder_paths.get_output_directory", return_value=str(self.output)),
            patch(
                "folder_paths.get_input_directory", return_value=str(Path(self.temp.name) / "input")
            ),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def run_folder(self, name: str, files: dict) -> Path:
        folder = self.output / "agent_tools" / "w" / name
        folder.mkdir(parents=True)
        for file, text in files.items():
            (folder / file).write_text(text, encoding="utf-8")
        return folder

    def test_a_step_reads_exactly_the_dataset_it_was_given(self) -> None:
        first = self.run_folder("compare-1", {"pairs.jsonl": '{"id": "p1"}\n'})
        later = self.run_folder("compare-2", {"pairs.jsonl": '{"id": "p9"}\n'})  # newer, same name
        given = ref(
            "record_pairs", "pairs", first / "pairs.jsonl", 1, run_folders=[str(first), str(later)]
        )
        self.assertEqual(dataset_file(given), (first / "pairs.jsonl").resolve())  # not the newest
        self.assertEqual(value_problems("RECORD_PAIRS", given), [])
        self.assertEqual(given["format"], "jsonl")

    def test_a_branch_is_the_pairs_it_was_given_limited_to_its_subset(self) -> None:
        compare = self.run_folder("compare-1", {"pairs.jsonl": '{"id": "p1"}\n{"id": "p2"}\n'})
        rules = self.run_folder("rules-1", {"unsettled.csv": "id,decision\np2,review\n"})
        pairs = ref("record_pairs", "pairs", compare / "pairs.jsonl", 2, run_folders=[str(compare)])
        unsettled = ref(
            "record_pairs",
            "unsettled",
            compare / "pairs.jsonl",
            1,
            subset=rules / "unsettled.csv",
            upstream=pairs,
            run_folders=[str(compare), str(rules)],
        )
        self.assertEqual(dataset_file(unsettled), (compare / "pairs.jsonl").resolve())
        self.assertEqual(subset_ids(unsettled), {"p2"})
        self.assertEqual(unsettled["inputs"], [pairs["uri"]])  # lineage
        self.assertIsNone(subset_ids(pairs))

    def test_lineage_files_and_values_from_before_references(self) -> None:
        compare = self.run_folder("compare-1", {"pairs.jsonl": '{"id": "p1"}\n'})
        rules = self.run_folder("rules-1", {"matched.csv": "id\np1\n"})
        matched = ref(
            "pair_decisions",
            "matched",
            rules / "matched.csv",
            1,
            run_folders=[str(compare), str(rules)],
        )
        # The pairs behind a decisions dataset: found in its lineage.
        self.assertEqual(lineage_file(matched, "record_pairs", "pairs.jsonl").name, "pairs.jsonl")
        old = {
            "_run_folders": [str(rules)],
            "file": "matched.csv",
        }  # a value from before references
        self.assertEqual(dataset_file(old), (rules / "matched.csv").resolve())
        self.assertIn(
            "should be a pair_decisions dataset reference", value_problems("PAIR_DECISIONS", old)[0]
        )
        (rules / "matched.csv").unlink()
        with self.assertRaisesRegex(ValueError, "dataset 'matched' is gone"):
            dataset_file(matched)

    def test_only_registered_file_classes_are_referenced(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a registered file dataset class"):
            ref("synthetic_people", "people", Path(self.temp.name) / "x.json")
        with self.assertRaisesRegex(ValueError, "not a registered file dataset class"):
            ref("customers", "c", Path(self.temp.name) / "x.csv")


if __name__ == "__main__":
    unittest.main()
