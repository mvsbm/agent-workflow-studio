# Extract digits

Keeps only the digits of each value (after unifying widths): 03(1234)5678 → 0312345678, 〒１６０-０００１ → 1600001. Values with fewer digits than the minimum become empty.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to clean. |
| Columns | `STRING` | Comma-separated columns. |
| Minimum digits | `INT` | Shorter results become empty (placeholders, fragments). Default: ``. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Rows with digits only. |

## Where it runs

`AgentWorkflowExtractDigits` in `table_nodes.py` (category `agent_workflow_studio/table`).
