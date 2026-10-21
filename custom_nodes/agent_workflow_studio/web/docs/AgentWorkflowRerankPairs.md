# Rerank pairs (cross-encoder)

Reads both records of each pair together with a local Japanese reranker and adds a comparison field `rerank`: high / medium / low (and its score). By default only the pairs an earlier stage left unsettled are scored (about 40 pairs a second); the others get n/a. Learn weights then weighs it like any field. Runs on this machine.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Pairs | `RECORD_PAIRS,DICT` | Compare fields' data, or a stage's unsettled branch (only those are scored). |
| Text from | `STRING` | Each record's columns (as shown in Review) joined into its text. Default: `name, kana, birth, address, phone`. |
| High from | `FLOAT` | Score from which a pair is high (in our test, half of the true pairs scored above 0.5). Default: `0.5`. |
| Low below | `FLOAT` | Score below which a pair is low. Default: `0.05`. |
| Model | `STRING` | A Hugging Face cross-encoder; its weights download once, records never leave. Default: `cl-nagoya/ruri-v3-reranker-310m`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| data | `RECORD_PAIRS` | The pairs with `rerank`, for Learn weights and Decision rules. |

## Where it runs

`AgentWorkflowRerankPairs` in `semantic_nodes.py` (category `agent_workflow_studio/matching`).
