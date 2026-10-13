"""Per-workflow git history: commits, diffs, restore, and git isolation."""

from __future__ import annotations

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
