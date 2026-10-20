# Decision rules

Decides each compared pair with rules and a points scorecard you can read and edit (YAML). Shows the decision flow with counts, writes decisions.csv with every pair's reason, and sends uncertain pairs to the Review tab; your Review decisions override the rules.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Pairs (optional) | `PAIRS,DICT` | Connect the Agent Tool that wrote the compared pairs (or give a path under output/). |
| Pairs file | `STRING` | JSONL or CSV in that tool's run folder: id (or left/right) plus one level per field. Default: `pairs.jsonl`. |
| Rules | `STRING` | rules (first that applies decides), score (points per level), decide (thresholds). Default: `# Checked top to bottom: the first rule that applies decides the pair. rules: - name: Same bank account when: {account: same} then: match # Points per field level for pairs no rule decided; the total decides. score: reading: {exact: 4, similar: 2, different: -3} birth: {exact: 5, close: 3, missing: 0, conflict: -6} decide: {match: 9, review: 4}`. |
| Review limit | `INT` | At most this many uncertain pairs go to the Review tab. Default: `2000`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| decisions | `DECISIONS` | Counts, plus run folders so the next tool can read decisions.csv. |
| summary | `STRING` | The decision flow as text. |
| matched | `DECISIONS` | Pairs decided to be the same: connect to Cluster guard. |
| different | `DECISIONS` | Pairs decided to be different: connect to Cluster guard. |
| unsettled | `PAIRS` | Pairs not decided here (undecided or to review): connect to the next stage. |

## Where it runs

`AgentWorkflowDecisionRules` in `decision_rules.py` (category `agent_workflow_studio`).
