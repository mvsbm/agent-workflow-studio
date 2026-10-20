# Summarize (group by)

One row per group with totals, counts, means, min/max or distinct counts, e.g. revenue per region and month.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to summarize. |
| Group by | `STRING` | Columns, comma-separated (empty: one row for all). |
| Aggregations | `STRING` | One per line: name: function(column); functions sum, mean, min, max, count, count_distinct. Default: `rows: count()`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| summary | `TABLE` |  |

## Where it runs

`AgentWorkflowSummarize` in `table_calc.py` (category `agent_workflow_studio/table`).
