# Join tables

Adds columns from the right table to each left row whose key matches (left join; first match).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Left | `TABLE` | The rows to keep. |
| Right | `TABLE` | Where to look values up. |
| Left key | `STRING` | Column in the left table. |
| Right key | `STRING` | Column in the right table. |
| Bring columns | `STRING` | Comma-separated right columns to add. |
| Prefix | `STRING` | Put before the added column names. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Left rows with the added columns. |

## Where it runs

`AgentWorkflowJoinTables` in `table_nodes.py` (category `agent_workflow_studio/table`).
