# Remove duplicates

Keeps the first row for each key (e.g. order_id); later rows with the same key are dropped.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to deduplicate. |
| Key columns | `STRING` | Columns that identify a row, comma-separated (empty: all). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` |  |

## Where it runs

`AgentWorkflowRemoveDuplicates` in `table_calc.py` (category `agent_workflow_studio/table`).
