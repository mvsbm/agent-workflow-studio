"""Improve loop: measured verdicts and a controller that stops, reverts or escalates."""

import unittest

from .loop import budget_of, next_action, snapshot, verdict


def state(verified, failing=(), steps=4, examples=None, current=True):
    return {
        "verified": list(verified),
        "failing": list(failing),
        "steps": steps,
        "examples": examples,
        "current": current,
    }


class LoopTests(unittest.TestCase):
    def test_snapshot_reads_the_contract_report(self) -> None:
        status = {
            "freshness": "Based on the latest run of this exact graph.",
            "steps": [
                {"step": "a", "state": "verified"},
                {"step": "b", "state": "failing"},
                {"step": "c", "state": "built"},
            ],
        }
        self.assertEqual(
            snapshot(status, {"passed": 1, "total": 2}),
            {"verified": ["a"], "failing": ["b"], "steps": 3, "examples": [1, 2], "current": True},
        )

    def test_verdicts_come_from_measurements(self) -> None:
        self.assertEqual(verdict(state("a", "b"), state("ab")), "improved")
        self.assertEqual(verdict(state("ab"), state("a", "b")), "worse")
        self.assertEqual(verdict(state("a"), state("a")), "same")
        self.assertEqual(verdict(state("a"), state("ab", current=False)), "not measured")

    def test_the_controller_stops_reverts_and_escalates(self) -> None:
        budget = budget_of(None)
        self.assertEqual(next_action([], state("abcd"), budget)["next"], "stop")
        worse = {"n": 1, "verdict": "worse", "before": state("ab"), "version_before": "abc"}
        self.assertEqual(
            next_action([worse], state("a", "b"), budget),
            {"next": "revert", "why": "iteration 1 made things worse", "restore": "abc"},
        )
        self.assertEqual(
            next_action([worse], state("ab"), budget)["next"], "continue", "a restore settles it"
        )
        same = [{"n": i, "verdict": "same", "before": state("a")} for i in (1, 2)]
        self.assertEqual(next_action(same, state("a"), budget)["next"], "escalate")
        spent = [{"n": i, "verdict": "improved", "before": state("")} for i in range(6)]
        self.assertIn("budget", next_action(spent, state("a"), budget)["why"])
        self.assertEqual(budget_of({"loop": {"iterations": 10}})["iterations"], 10)


if __name__ == "__main__":
    unittest.main()
