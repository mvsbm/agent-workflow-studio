# Compute column

Adds (or replaces) a column computed per row, e.g. revenue = quantity * unit_price or month = left(date, 7). Allowed: + - * /, columns, numbers, text and round, abs, min, max, left, right, lower, upper, text, number, length.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to compute on. |
| New column | `STRING` | The column to write. |
| Expression | `STRING` | e.g. quantity * unit_price |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` |  |

## Where it runs

`AgentWorkflowComputeColumn` in `table_calc.py` (category `agent_workflow_studio/table`).
