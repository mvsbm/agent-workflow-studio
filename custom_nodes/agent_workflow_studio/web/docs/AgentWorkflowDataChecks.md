# Data checks

Counts the rows (or, for is duplicate, the values) that break each readable rule, with examples: e.g. missing_phone: phone is empty · invalid_email: email not matches ^[^@\s]+@[^@\s]+\.[^@\s]+$ · duplicate_id: customer_id is duplicate.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to check. |
| Checks | `STRING` | One per line: name: column test [value]; tests is empty, is filled, equals, is one of, matches, not matches, is duplicate. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| counts | `TABLE` | One row per check: check, count, examples. |

## Where it runs

`AgentWorkflowDataChecks` in `table_calc.py` (category `agent_workflow_studio/table`).
