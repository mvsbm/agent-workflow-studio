"""Decision rules: order, negation, scorecard thresholds, review overrides and spec errors."""

import unittest

from .decision_rules import EXAMPLE, decide, load_spec, run_rules

SPEC = load_spec("""
rules:
  - name: Same bank account
    when: {account: same}
    then: match
  - name: Birth and given name both conflict
    when: {birth: conflict, given: "!exact"}
    then: different
score:
  reading: {exact: 4, similar: 2, different: -3}
  birth: {exact: 5, close: 3, conflict: -6}
decide: {match: 9, review: 4}
""")


class DecisionRulesTests(unittest.TestCase):
    def test_first_rule_that_applies_decides(self) -> None:
        both = {"account": "same", "birth": "conflict", "given": "different"}
        self.assertEqual(decide(both, SPEC)["branch"], "Same bank account")
        result = decide({"birth": "conflict", "given": "similar"}, SPEC)
        self.assertEqual(
            (result["decision"], result["why"]), ("different", "birth=conflict, given=similar")
        )
        # "!exact" does not hold for an exact given name, so the scorecard decides.
        self.assertEqual(
            decide({"birth": "conflict", "given": "exact"}, SPEC)["branch"], "Score < 4"
        )

    def test_a_skipped_rule_is_not_applied(self) -> None:
        spec = load_spec(
            "rules:\n  - {name: Same bank account, when: {account: same}, then: match, skip: true}\n"
            "score: {reading: {exact: 4}}\ndecide: {match: 9, review: 4}\n"
        )
        self.assertEqual(decide({"account": "same"}, spec)["branch"], "Score < 4")
        with self.assertRaisesRegex(ValueError, "skip: true"):
            load_spec("rules:\n  - {name: X, when: {a: b}, then: match, skip: please}\n")

    def test_scorecard_and_thresholds(self) -> None:
        self.assertEqual(decide({"reading": "exact", "birth": "exact"}, SPEC)["decision"], "match")
        middle = decide({"reading": "exact", "birth": "missing"}, SPEC)
        self.assertEqual(
            (middle["decision"], middle["score"], middle["why"]),
            ("review", 4, "+4 reading exact = 4"),
        )
        low = decide({"reading": "different", "birth": "close"}, SPEC)
        self.assertEqual((low["decision"], low["branch"]), ("different", "Score < 4"))

    def test_review_decisions_override_rules(self) -> None:
        row = {"account": "same"}
        self.assertEqual(decide(row, SPEC, "different")["branch"], "Your review")
        self.assertEqual(decide(row, SPEC, "unsure")["branch"], "Same bank account")

    def test_run_counts_branches_and_levels(self) -> None:
        rows = [
            {"id": "p1", "account": "same"},
            {"id": "p2", "reading": "exact", "birth": "exact"},
            {"id": "p3", "reading": "similar", "birth": "close"},
            {"id": "p4", "reading": "exact"},
        ]
        decided, seen = run_rules(rows, SPEC, {"p4": "same", "p3": "different", "p1": "different"})
        self.assertEqual(
            [d["decision"] for d in decided], ["different", "match", "different", "match"]
        )
        self.assertEqual(seen["flow"][0], (("Your review", "match"), 1))
        # What the rules alone would have done with the reviewed pairs, per branch.
        self.assertEqual(seen["agreement"]["Same bank account"]["disagree"], 1)
        self.assertEqual(seen["agreement"]["Score 4 to 9"]["undecided"], 2)  # p3 and p4
        self.assertEqual(seen["agreement"]["Score ≥ 9"]["agree"], 0)
        self.assertEqual(seen["levels"]["reading"]["exact"], 2)

    def test_deterministic_then_probabilistic(self) -> None:
        deterministic = load_spec(
            "rules:\n  - {name: Same bank account, when: {account: same}, then: match}\n"
        )
        rows = [{"id": "p1", "account": "same"}, {"id": "p2", "reading": "exact", "birth": "exact"}]
        first, _ = run_rules(rows, deterministic, {})
        self.assertEqual([d["decision"] for d in first], ["match", "undecided"])
        earlier = {d["id"]: {**d, "score": ""} for d in first}
        second, seen = run_rules(rows, SPEC, {}, earlier)
        self.assertEqual(
            [(d["decision"], d["branch"]) for d in second],
            [("match", "Same bank account"), ("match", "Score ≥ 9")],
        )
        self.assertEqual(seen["flow"][0], (("Decided by an earlier stage", "match"), 1))

    def test_bad_specs_say_what_to_fix(self) -> None:
        load_spec(EXAMPLE)
        for bad, message in (
            ("rules: [{name: x, when: {}, then: match}]", "non-empty `when`"),
            ("rules: [{name: x, when: {a: b}, then: maybe}]", "then"),
            ("score: {a: {x: high}}", "points"),
            ("score: {a: {x: 1}}\ndecide: {match: 1, review: 5}", "review <= match"),
            ("other: 1", "three parts"),
            ("rules: [", "YAML"),
        ):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                load_spec(bad)


if __name__ == "__main__":
    unittest.main()
