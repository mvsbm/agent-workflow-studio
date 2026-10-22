# Decide by score

Decides each scored pair: your review first; then at least Match at → match, at least Review from → to review (the queue for people), below → different. Raise Match at for fewer wrong merges, lower it for fewer missed ones. Precision first: a match also needs a shared phone, house, town or a stated alias, else it goes to review.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Scored pairs | `RECORD_PAIRS` | From Learn from your reviews. |
| Match at | `FLOAT` | Pairs at least this likely to be the same are merged. Default: `0.5`. |
| Review from | `FLOAT` | Pairs from here up to Match at go to the review queue; below are different. Default: `0.1`. |
| Precision first | `BOOLEAN` | A match also needs a second identifier beside name and birth date. Default: ``. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| decisions | `PAIR_DECISIONS` | Every pair's decision: connect to Cluster guard. |
| queue | `PAIR_DECISIONS` | The review queue: the pairs for people to decide. |
| summary | `STRING` | Counts per decision. |

## Where it runs

`AgentWorkflowDecideByScore` in `contacts_nodes.py` (category `agent_workflow_studio/matching`).
