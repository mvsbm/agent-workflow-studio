"""Semantic matching: nearest neighbours and rerank levels (the models themselves are not loaded here)."""

import unittest

import torch

from custom_nodes.agent_workflow_studio.semantic_nodes import level, nearest


class SemanticTests(unittest.TestCase):
    def test_nearest_pairs_each_record_with_its_closest_similar_ones(self) -> None:
        v = torch.nn.functional.normalize(torch.tensor([[1.0, 0], [0.99, 0.1], [0, 1.0]]), dim=-1)
        pairs = {(i, j) for i, j, _ in nearest(v, top=1, least=0.9)}
        self.assertEqual(pairs, {(0, 1), (1, 0)})  # the third is far from both

    def test_rerank_levels(self) -> None:
        self.assertEqual([level(s, 0.5, 0.05) for s in (0.9, 0.2, 0.01)], ["high", "medium", "low"])


if __name__ == "__main__":
    unittest.main()
