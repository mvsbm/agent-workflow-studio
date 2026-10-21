"""Per-workflow git history: commits, diffs, restore, and git isolation."""

# Verifies: R8

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from custom_nodes.agent_workflow_studio.project_repo import ProjectRepo, node_diff

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

    def test_a_tool_shows_its_spec_code_approval_tests_and_diff(self) -> None:
        from custom_nodes.agent_workflow_studio.agent_tools import code_hash

        code = "def run(i):\n    return {'n': 1}\n"
        manifest = {
            "version": 2,
            "name": "count",
            "outputs": [{"name": "n", "kind": "INT"}],
            "sha256": code_hash(code),
        }
        sha = self.repo.save_tool("count", code, manifest)
        self.repo.add_record(
            {
                "kind": "pipeline",
                "tools": [{"tool": "count", "passed": True, "problems": [], "examples": 1}],
            },
            "Checks · ✓ tool tests",
            {"nodes": []},
        )
        detail = self.repo.tool_detail("count")
        self.assertEqual((detail["code"], detail["approved"]["sha"]), (code, sha))
        self.assertTrue(detail["tests"]["passed"])
        self.assertIn("+def run(i):", self.repo.code_diff(sha))  # the version's code change
        self.assertEqual(
            self.repo.code_diff(self.repo.log()[0]["sha"]), ""
        )  # a record changes no code
        with self.assertRaisesRegex(ValueError, "unknown tool"):
            self.repo.tool_detail("../x")
        from custom_nodes.agent_workflow_studio.graduation import brief

        text = brief(
            "count",
            code,
            {**manifest, "examples": [{"inputs": {}, "expect": {"n": 1}}]},
            WORKFLOW_ID,
        )
        self.assertIn("| n | INT | io.Int |", text)  # the node keeps the tool's interface
        self.assertIn('- inputs `{}` → expect `{"n": 1}`', text)  # its tests carry over
        self.assertTrue(text.rstrip().endswith("return {'n': 1}\n```"))

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

    def test_diff_sees_inside_stages(self) -> None:
        def staged(*inner, links=()):
            definition = {
                "id": "s1",
                "name": "1 · Clean",
                "nodes": [{"id": i, "type": t} for i, t in inner],
                "links": [
                    {"origin_id": a, "origin_slot": 0, "target_id": b, "target_slot": 0}
                    for a, b in links
                ],
            }
            return {"nodes": [{"id": 9, "type": "s1"}], "definitions": {"subgraphs": [definition]}}

        diff = node_diff(
            staged((1, "AgentWorkflowStub")), staged((2, "Normalize"), (3, "Check"), links=[(2, 3)])
        )
        self.assertEqual(
            [(n["id"], n["stage"]) for n in diff["added"]],
            [("s1:2", "1 · Clean"), ("s1:3", "1 · Clean")],
        )
        self.assertEqual([n["type"] for n in diff["removed"]], ["AgentWorkflowStub"])
        self.assertEqual(diff["links_added"], 1)

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

    def test_the_contract_lives_in_the_workflow_and_versions_show_when_it_changed(self) -> None:
        from custom_nodes.agent_workflow_studio.native_layers import embed

        from .test_native_layers import workflow
        from .test_workflow_contract import CONTRACT

        approved = embed(CONTRACT, workflow())
        sha = self.repo.commit_contract(approved, f"Contract approved: {CONTRACT['purpose']}")
        top = self.repo.log()[0]
        self.assertEqual(
            (top["author"], top["message"]), ("user", f"Contract approved: {CONTRACT['purpose']}")
        )
        self.assertEqual(self.repo.load_contract(), CONTRACT)
        self.assertEqual(self.repo.contract_at(sha), CONTRACT)
        self.assertFalse((self.repo.tree / "contract.yaml").exists())  # one source: the workflow
        moved = {**approved, "extra": {**approved["extra"], "ds": {"scale": 2}}}
        later = self.repo.commit(moved, "pan", "user")
        self.assertIsNone(self.repo.contract_at(later))  # the contract did not change here
        amended = embed({**CONTRACT, "purpose": "Label reviews, now in two languages."}, moved)
        changed = self.repo.commit(amended, "amend", "agent")
        self.assertEqual(
            self.repo.contract_at(changed)["purpose"], "Label reviews, now in two languages."
        )
        self.repo.restore(sha)  # an approval is a full snapshot: it can be restored
        self.assertEqual(self.repo.load_contract(), CONTRACT)

    def test_a_template_brings_its_contract_once(self) -> None:
        from custom_nodes.agent_workflow_studio.native_layers import embed

        from .test_native_layers import workflow
        from .test_workflow_contract import CONTRACT

        self.assertTrue(self.repo.adopt_template(embed(CONTRACT, workflow())))
        self.assertEqual(self.repo.load_contract(), CONTRACT)
        first = self.repo.log()
        self.assertEqual((len(first), first[0]["author"]), (1, "agent"))
        self.assertIn("contract shipped with the workflow", first[0]["message"])
        # Never over what happened since: a second open, or a workflow with history, keeps its own.
        other = embed({**CONTRACT, "purpose": "other"}, workflow())
        self.assertFalse(self.repo.adopt_template(other))
        self.assertEqual(self.repo.load_contract(), CONTRACT)

    def test_each_stage_that_asks_has_its_newest_review_shown(self) -> None:
        import json
        import os
        import tempfile

        from custom_nodes.agent_workflow_studio.project_repo import merged_review

        with tempfile.TemporaryDirectory() as tmp:

            def write(folder, stage, pair, age):
                path = Path(tmp) / folder / "review.json"
                path.parent.mkdir()
                path.write_text(
                    json.dumps({"stage": stage, "title": stage["title"], "pairs": [{"id": pair}]})
                )
                os.utime(path, (age, age))
                return path

            rules = {"id": "decide_rules", "title": "6 · Decide: certain rules"}
            score = {"id": "decide_score", "title": "7 · Decide: learned score"}
            found = [
                write("decision_rules-1", score, "old", 100),
                write("decision_rules-2", rules, "r1", 200),
                write("decision_rules-3", score, "s1", 300),
            ]
            merged = merged_review(found)
        self.assertEqual(
            [p["id"] for p in merged["pairs"]], ["r1", "s1"]
        )  # the old run is replaced
        self.assertEqual(merged["pairs"][1]["stage"], score)
        self.assertEqual([s["pairs"] for s in merged["sections"]], [1, 1])

    def test_a_version_says_its_layer_and_the_requests_it_touched(self) -> None:
        from custom_nodes.agent_workflow_studio.native_layers import embed
        from custom_nodes.agent_workflow_studio.project_repo import layer_of, touched

        from .test_native_layers import workflow
        from .test_workflow_contract import CONTRACT

        self.assertEqual(layer_of("Design approved: 2 steps", "user")["layer"], "design")
        self.assertEqual(layer_of("Build classify from its LLD", "agent")["layer"], "lld")
        self.assertEqual(layer_of("Checks · ✓ design", "agent")["layer_label"], "Checks")
        stored = embed(CONTRACT, workflow())
        diff = {"added": [{"id": "sg-a:1", "stage": "1 · Classify"}], "removed": [], "changed": []}
        self.assertEqual(
            touched(diff, stored),
            [{"stage": "1 · Classify", "step": "classify", "serves": ["R1 · Label each review"]}],
        )
        diff["changed"] = [{"stage": "10 · Publish"}, {"stage": "2 · Report"}]
        self.assertEqual(
            [t["stage"] for t in touched(diff, stored)],
            ["1 · Classify", "2 · Report", "10 · Publish"],
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
