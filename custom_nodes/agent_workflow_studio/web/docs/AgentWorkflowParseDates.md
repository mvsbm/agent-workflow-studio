# Parse dates

Turns dates in any of these forms into YYYY-MM-DD: Japanese era letters (S45.03.12), era kanji (昭和45年3月12日, 元年), era codes (3450312), 1970/03/12, 19700312, 70/03/12. Shows how many values had each format.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows with date columns. |
| Columns | `STRING` | Comma-separated date columns. |
| Two-digit year pivot | `INT` | 00–pivot → 2000s, above → 1900s. Default: `30`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Rows with ISO dates (unparseable values left empty). |

## Where it runs

`AgentWorkflowParseDates` in `table_nodes.py` (category `agent_workflow_studio/table`).
