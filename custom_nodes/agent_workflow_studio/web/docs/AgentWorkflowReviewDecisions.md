# Your review decisions

You, as a step of the cascade: the pairs a stage left unsettled, split by the decisions you made in the Review tab into matched and different (for Cluster guard) and still open.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Unsettled | `RECORD_PAIRS,DICT` | A stage's unsettled branch (Decision rules or LLM suggestions). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| matched | `PAIR_DECISIONS` | Pairs you decided are the same person. |
| different | `PAIR_DECISIONS` | Pairs you decided are different people. |
| open | `RECORD_PAIRS` | Pairs you have not decided yet. |

## Where it runs

`AgentWorkflowReviewDecisions` in `matching_nodes.py` (category `agent_workflow_studio/matching`).
