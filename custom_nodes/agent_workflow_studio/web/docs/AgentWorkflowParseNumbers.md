# Parse numbers

Turns text like ¥1,200, 3,500円 or ３．５ into numbers; a blank can mean a default (e.g. quantity 1).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to convert. |
| Columns | `STRING` | Columns to convert, comma-separated. |
| Blank means | `STRING` | A number to use for blank cells (empty: leave them blank). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` |  |

## Where it runs

`AgentWorkflowParseNumbers` in `table_calc.py` (category `agent_workflow_studio/table`).
