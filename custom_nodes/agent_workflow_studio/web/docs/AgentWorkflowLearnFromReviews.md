# Learn from your reviews

Scores every pair from your Review decisions: a model (logistic regression) learns from the pairs you decided, then asks you about the pairs it is least sure of, a batch at a time (active learning). Reviews a model trained without them strongly disputes come back for a second look. Before any review, pairs are ordered by their agreement patterns. About 1,000 reviews is enough.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Pairs | `RECORD_PAIRS` | From Compare contacts. |
| Reviews per round | `INT` | How many pairs to ask you about each run. Default: `50`. |
| Check my reviews | `BOOLEAN` | Ask again about reviews the model strongly disputes. Default: `True`. |
| Answer key (benchmark) (optional) | `STRING` | Benchmark only: a reviewer who knows the answers reviews instead of you. |
| Simulated reviews (optional) | `INT` | Benchmark only: how many reviews the simulated reviewer makes. Default: `1000`. |
| Seed (optional) | `INT` | Which random pairs the first round adds. Default: ``. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| scored | `RECORD_PAIRS` | Each pair with how likely it is the same. |
| summary | `STRING` | What the model learned from, and what to review next. |

## Where it runs

`AgentWorkflowLearnFromReviews` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
