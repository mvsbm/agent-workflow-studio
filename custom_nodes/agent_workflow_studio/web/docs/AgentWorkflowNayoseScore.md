# Nayose · Score

Scores nayose_result.csv (system, record_id, role, customer_id) against the hidden answer key: pairwise and B-cubed precision/recall/F1, recall per kind of damage and per system pair, and what the wrong merges were. Shows totals only, never per-record answers, and only to you: the agent cannot see these results, so it cannot tune itself to the answer key.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Folder | `STRING` | The benchmark folder whose answer key to use (connect an export's folder). Default: `nayose`. |
| Result (optional) | `DICT` | Connect the Agent Tool that wrote nayose_result.csv. |
| Result file | `STRING` | File name of the result in the connected tool's run folders. Default: `nayose_result.csv`. |

## Where it runs

`AgentWorkflowNayoseScore` in `nayose_nodes.py` (category `agent_workflow_studio/nayose benchmark`).
