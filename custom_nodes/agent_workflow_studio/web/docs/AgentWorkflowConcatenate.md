# Concatenate tables

Puts the rows of up to four tables one after another; columns are the union of all.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| A | `TABLE` | A table. |
| B (optional) | `TABLE` | A table. |
| C (optional) | `TABLE` | A table. |
| D (optional) | `TABLE` | A table. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | All rows. |

## Where it runs

`AgentWorkflowConcatenate` in `table_nodes.py` (category `agent_workflow_studio/table`).
