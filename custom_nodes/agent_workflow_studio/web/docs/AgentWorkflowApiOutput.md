# API output

Returns the value at a stage's output to callers of the workflow's API, under the promised output's name, and shows it in App mode as a file (CSV for a table, JSON for data, text).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Value | `*` | The stage output the API returns. |
| Name | `STRING` | The promised output's name. |

## Where it runs

`AgentWorkflowApiOutput` in `check_node.py` (category `agent_workflow_studio`).
