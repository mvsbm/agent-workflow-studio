# Filter rows

Keeps the rows where a column is filled / equals / is one of / matches a pattern (or the opposite).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to filter. |
| Column | `STRING` | The column to test. |
| Keep rows where | `COMBO` | The test. |
| Value | `STRING` | Value, comma-separated list, or regular expression. |
| Invert | `BOOLEAN` | Keep the rows that fail the test instead. Default: ``. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| kept | `TABLE` | The rows that passed. |

## Where it runs

`AgentWorkflowFilterRows` in `table_nodes.py` (category `agent_workflow_studio/table`).
