# Table from tool result

Takes one table out of an Agent Tool's result, so table nodes can use it.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Tool result | `DICT` | An Agent Tool's result. |
| Output | `STRING` | The tool's output name; empty: its only table. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` |  |

## Where it runs

`AgentWorkflowTableFromResult` in `table_nodes.py` (category `agent_workflow_studio/table`).
