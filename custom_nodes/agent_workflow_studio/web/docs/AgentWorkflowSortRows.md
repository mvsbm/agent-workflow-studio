# Sort rows

Orders the rows by one or more columns: numbers as numbers, text as text, blanks last.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to order. |
| Sort by | `STRING` | Columns to sort by, comma-separated; the first decides, the next breaks ties. |
| Order | `COMBO` | Largest first (descending, Z→A) or smallest first (ascending, A→Z). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` |  |

## Where it runs

`AgentWorkflowSortRows` in `table_calc.py` (category `agent_workflow_studio/table`).
