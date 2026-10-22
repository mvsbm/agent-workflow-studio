# Score contacts (benchmark)

Scores entities.csv against the answer key: pairwise and B-cubed precision, recall and F1, recall per kind of dirt, and the wrong merges. Totals only, to you: nothing flows on, so no step can tune itself to the answer key.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Entities | `RECORD_CLUSTERS` | From Entity per contact. |
| Answer key | `STRING` | From Synthetic contacts. |

## Where it runs

`AgentWorkflowScoreContacts` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
