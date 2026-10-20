# Write table

Writes a table as a UTF-8 CSV under ComfyUI's output directory, with the columns you list (in that order). Like ComfyUI's save nodes it never overwrites: each run writes its own numbered file (result_00001_.csv), so every API call's download stays its own.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | The table to write. |
| File | `STRING` | Name under the output directory, e.g. nayose/nayose_result.csv; each run adds a number (nayose_result_00001_.csv). Default: `result.csv`. |
| Columns | `STRING` | Comma-separated; empty = all columns. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| path | `STRING` | Where the file was written (under output/). |

## Where it runs

`AgentWorkflowWriteTable` in `table_nodes.py` (category `agent_workflow_studio/table`).
