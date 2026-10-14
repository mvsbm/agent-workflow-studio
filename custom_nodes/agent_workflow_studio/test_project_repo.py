"""Per-workflow git history: commits, diffs, restore, and git isolation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from .project_repo import ProjectRepo, node_diff

WORKFLOW_ID = "5d9a308c-81ae-4aab-a0bd-5145aa83e56e"


def graph(*nodes, links=()):
    return {
        "nodes": [{"id": i, "type": t, "widgets_values": w} for i, t, w in nodes],
        "links": list(links),
    }


class ProjectRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = ProjectRepo(self.root, WORKFLOW_ID)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_commit_log_show_and_unchanged_snapshot(self) -> None:
        one = graph((1, "PrimitiveString", ["hi"]))
        first = self.repo.commit(one, "Agent: add source", "agent")
        self.assertIsNone(self.repo.commit(one, "same again", "user"))
        second = self.repo.commit(graph((1, "PrimitiveString", ["bye"])), "Save", "user")
        log = self.repo.log()
        self.assertEqual([e["sha"] for e in log], [second, first])
        self.assertEqual([e["author"] for e in log], ["user", "agent"])
        self.assertEqual(log[0]["parent"], first)
        self.assertEqual(log[1]["message"], "Agent: add source")
        self.assertEqual(self.repo.show(first), one)
        self.assertTrue((self.root / f"{self.repo.id}.git").is_dir())
        self.assertFalse((self.repo.tree / ".git").exists())

    def test_diff_reports_nodes_and_links(self) -> None:
        self.repo.commit(graph((1, "A", []), (2, "B", ["x"])), "one", "agent")
        sha = self.repo.commit(
            graph((2, "B", ["y"]), (3, "C", []), links=[[1, 2, 0, 3, 0, "STRING"]]), "two", "agent"
        )
        diff = self.repo.diff(sha)
        self.assertEqual([n["id"] for n in diff["added"]], [3])
        self.assertEqual([n["id"] for n in diff["removed"]], [1])
        self.assertEqual([n["id"] for n in diff["changed"]], [2])
        self.assertEqual((diff["links_added"], diff["links_removed"]), (1, 0))
        self.assertEqual(node_diff({}, {})["added"], [])

    def test_restore_is_a_new_commit(self) -> None:
        first = self.repo.commit(graph((1, "A", [])), "one", "agent")
        self.repo.commit(graph((1, "A", []), (2, "B", [])), "two", "agent")
        sha, workflow = self.repo.restore(first)
        self.assertEqual(workflow, graph((1, "A", [])))
        log = self.repo.log()
        self.assertEqual(len(log), 3)
        self.assertEqual(log[0]["sha"], sha)
        self.assertEqual(log[0]["message"], f"Restore {first[:8]}")

    def test_hooks_never_run(self) -> None:
        self.repo.commit(graph((1, "A", [])), "one", "agent")
        marker = self.root / "hook-ran"
        hook = self.root / f"{self.repo.id}.git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
        hook.chmod(0o755)
        self.repo.commit(graph((1, "B", [])), "two", "agent")
        self.assertFalse(marker.exists())

    def test_contract_versions_and_conversion_of_old_plans(self) -> None:
        from .test_workflow_contract import CONTRACT

        sha = self.repo.save_contract(CONTRACT)
        self.assertIsNone(self.repo.show(sha))  # a contract version has no canvas snapshot
        self.assertEqual(self.repo.contract_at(sha), CONTRACT)
        self.assertEqual(self.repo.load_contract(), CONTRACT)
        self.assertEqual(self.repo.diff(sha)["added"], [])
        with self.assertRaisesRegex(ValueError, "no canvas snapshot"):
            self.repo.restore(sha)
        self.assertTrue(
            (self.repo.tree / "contract.yaml").read_text().startswith("# yaml-language-server")
        )
        built = self.repo.commit(graph((1, "A", [])), "build", "agent")
        self.assertIsNone(self.repo.contract_at(built))

    def test_loads_a_plan_approved_before_contracts(self) -> None:
        old = ProjectRepo(self.root, "older-workflow")
        old.tree.mkdir(parents=True)
        plan = {
            "goal": "Say hi.",
            "inputs": [],
            "outputs": [{"name": "t", "description": "d", "type": "text"}],
            "steps": [
                {
                    "id": "s",
                    "purpose": "p",
                    "approach": "deterministic",
                    "success_gate": {"description": "ok"},
                }
            ],
            "assumptions": [],
            "decisions": [],
            "open_questions": [],
        }
        (old.tree / "spec.json").write_text(json.dumps(plan))
        self.assertEqual(old.load_contract()["purpose"], "Say hi.")
        old._ensure()
        old._git("add", "spec.json")
        old._git("commit", "--quiet", "-m", "old plan")
        from .test_workflow_contract import CONTRACT

        old.save_contract(CONTRACT)
        self.assertFalse(
            (old.tree / "spec.json").exists(), "the old plan is replaced, not kept beside it"
        )

    def test_review_decisions_merge_clear_and_are_the_users(self) -> None:
        self.repo.save_labels({"p1": "same", "p2": "different"}, "match-1")
        self.repo.save_labels({"p1": None, "C7": "wrong"}, "match-2")
        self.assertEqual(self.repo.load_labels(), {"p2": "different", "C7": "wrong"})
        top = self.repo.log()[0]
        self.assertEqual(
            (top["author"], top["message"]), ("user", "Review · 2 decisions (1 different, 1 wrong)")
        )
        for bad in (None, {"p": "maybe"}, {"p": 1}):
            with self.subTest(labels=bad), self.assertRaises(ValueError):
                self.repo.save_labels(bad, "r")

    def test_rejects_bad_input(self) -> None:
        for bad in (None, {}, {"nodes": "x"}):
            with self.subTest(workflow=bad), self.assertRaises(ValueError):
                self.repo.commit(bad, "m", "agent")
        with self.assertRaises(ValueError):
            self.repo.commit(graph(), "m", "someone")
        for sha in ("HEAD", "../x", "a" * 39):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                self.repo.show(sha)
        with self.assertRaises(ValueError):
            ProjectRepo(self.root, "bad\x00id")
        self.assertEqual(ProjectRepo(self.root, "never-used").log(), [])


if __name__ == "__main__":
    unittest.main()
