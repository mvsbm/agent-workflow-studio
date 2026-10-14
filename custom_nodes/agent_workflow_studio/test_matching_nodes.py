"""The matching cascade: EM weights, the cluster guard, the decision tree, local-only LLM calls."""

import json
import math
import random
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from .matching_nodes import (
    ask_local,
    check_local,
    decision_tree,
    explain_customers,
    guard_clusters,
    learn_weights,
    thresholds,
)

TRUTH = {"reading": (0.9, 0.05), "birth": (0.8, 0.02), "tel": (0.5, 0.03), "addr": (0.6, 0.1)}


def pairs_with_known_truth(n=4000, seed=1):
    """10% matches; each field agrees with probability m among matches and u among non-matches;
    15% of values are missing regardless (so missing should be worth about nothing)."""
    rng, rows = random.Random(seed), []
    for i in range(n):
        match, row = rng.random() < 0.1, {"id": f"p{i}"}
        for field, (m, u) in TRUTH.items():
            agree = rng.random() < (m if match else u)
            row[field] = "missing" if rng.random() < 0.15 else "same" if agree else "different"
        rows.append(row)
    return rows


class LearnWeightsTests(unittest.TestCase):
    def test_recovers_the_hidden_structure_from_a_rough_start(self) -> None:
        start = {f: {"same": 1, "different": -1, "missing": 0} for f in TRUTH}
        result = learn_weights(pairs_with_known_truth(), list(TRUTH), {}, start)
        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["lambda"], 0.1, delta=0.025)
        for field, (m, u) in TRUTH.items():
            with self.subTest(field=field):
                self.assertAlmostEqual(result["points"][field]["same"], math.log2(m / u), delta=0.6)
                self.assertEqual(result["points"][field]["missing"], 0)

    def test_missing_is_no_evidence_even_when_it_goes_with_non_matches(self) -> None:
        rows = pairs_with_known_truth()
        for row in rows[::2]:
            if row["reading"] == "different":
                row["reading"] = "missing"  # missing mostly where it would have disagreed
        result = learn_weights(
            rows, list(TRUTH), {}, {f: {"same": 1, "different": -1} for f in TRUTH}
        )
        self.assertEqual(result["points"]["reading"]["missing"], 0)
        self.assertGreater(result["points"]["reading"]["same"], 2)

    def test_reviewed_pairs_are_known_answers(self) -> None:
        rows = [
            {"id": "a", "f": "x"},
            {"id": "b", "f": "x"},
            {"id": "c", "f": "y"},
            {"id": "d", "f": "y"},
        ]
        result = learn_weights(
            rows, ["f"], {"a": "same", "b": "same", "c": "different", "d": "different"}, {"f": {}}
        )
        self.assertEqual(result["reviewed"], 4)
        # Four reviews are little evidence: clearly for x and against y, but softened (≈ ±2.3).
        self.assertGreater(result["points"]["f"]["x"], 2)
        self.assertLess(result["points"]["f"]["y"], -2)

    def test_thresholds_follow_the_target_probabilities(self) -> None:
        self.assertEqual(thresholds(0.5, 0.8, 0.5), {"match": 2.0, "review": 0.0})


def decision(i, left, right, decision, branch="Score ≥ 9", score="10"):
    return {
        "id": i,
        "left": left,
        "right": right,
        "decision": decision,
        "branch": branch,
        "score": score,
        "why": "",
    }


class ClusterGuardTests(unittest.TestCase):
    def test_a_weak_link_cannot_chain_two_people_decided_different(self) -> None:
        decisions = [
            decision("1", "a", "b", "match", score="20"),
            decision("2", "c", "d", "match", score="18"),
            decision("3", "b", "c", "match", score="9"),  # the weak link that would chain a..d
            decision(
                "4", "a", "d", "different", branch="Birth and given name both conflict", score=""
            ),
        ]
        result = guard_clusters(decisions, 0)
        self.assertEqual([b["id"] for b in result["blocked"]], ["3"])
        self.assertIn("decided different", result["blocked"][0]["reason"])
        clusters = result["clusters"]
        self.assertEqual(clusters["a"], clusters["b"])
        self.assertNotEqual(clusters["a"], clusters["c"])

    def test_each_customer_explains_its_joins_and_what_was_kept_out(self) -> None:
        decisions = [
            decision("1", "a", "b", "match", score="20"),
            decision("2", "c", "d", "match", score="18"),
            decision("3", "b", "c", "match", score="9"),
            decision("4", "a", "d", "different", branch="Namesake", score=""),
            decision("5", "a", "b", "match", score="7"),  # already together: not part of the chain
        ]
        guarded = guard_clusters(decisions, 0)
        ids = {root: f"C{i}" for i, root in enumerate(sorted(set(guarded["clusters"].values())), 1)}
        out = explain_customers(guarded, ids, {"a": {"name": "山田 太郎"}})
        ab = out[ids[guarded["clusters"]["a"]]]
        self.assertEqual([r["id"] for r in ab["records"]], ["a", "b"])
        self.assertEqual(ab["records"][0]["name"], "山田 太郎")
        self.assertEqual([(j["a"], j["b"]) for j in ab["joined_by"]], [("a", "b")])
        # The refused weak link shows on both customers it would have joined.
        for customer in out.values():
            self.assertEqual([(k["a"], k["b"]) for k in customer["kept_apart"]], [("b", "c")])

    def test_score_band_differences_are_not_proof_and_size_caps_hold(self) -> None:
        decisions = [
            decision("1", "a", "b", "match"),
            decision("2", "b", "c", "match"),
            decision("3", "a", "c", "different", score="-5"),
        ]
        self.assertEqual(guard_clusters(decisions, 0)["blocked"], [])
        self.assertEqual(
            guard_clusters(decisions, 2)["blocked"][0]["reason"], "customer would exceed 2 records"
        )

    def test_tree_counts_every_pair(self) -> None:
        decisions = [
            decision("1", "a", "b", "match"),
            decision("2", "b", "c", "review", branch="Score 4 to 9", score="6"),
            decision("3", "c", "d", "different", branch="Score < 4", score="1"),
        ]
        tree = decision_tree(
            decisions, {"2": "same"}, {"2": "different"}, guard_clusters(decisions, 0)
        )
        decided = tree["children"][0]["children"]
        self.assertEqual(sum(child["count"] for child in decided), 3)
        band = next(child for child in decided if child["label"] == "Score 4 to 9")
        self.assertEqual([c["label"] for c in band["children"]], ["LLM suggests", "You decided"])
        self.assertEqual(
            band["children"][1]["children"][0],
            {"label": "different", "count": 1, "decision": "different"},
        )


class LocalModelTests(unittest.TestCase):
    def test_customer_data_never_leaves_the_machine(self) -> None:
        for endpoint in (
            "https://api.example.com/v1",
            "http://10.0.0.5:8091/v1",
            "http://127.0.0.1.evil.com/v1",
        ):
            with (
                self.subTest(endpoint=endpoint),
                self.assertRaisesRegex(ValueError, "this machine only"),
            ):
                check_local(endpoint)
        check_local("http://127.0.0.1:8091/v1")

    def test_asks_a_local_server_and_reads_its_verdict(self) -> None:
        seen = []

        class Server(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        {"same": "yes", "reason": "same reading and birth"}
                                    )
                                }
                            }
                        ]
                    }
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            answer = ask_local(
                f"http://127.0.0.1:{server.server_port}/v1",
                "m",
                "judge",
                {"a": {"name": "山田"}, "b": {"name": "ﾔﾏﾀﾞ"}, "reading": "exact"},
            )
        finally:
            server.shutdown()
        self.assertEqual(answer, {"suggestion": "same", "reason": "same reading and birth"})
        self.assertIn('"reading": "exact"', seen[0]["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
