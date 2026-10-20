# Preview table

Shows the first rows of a CSV, JSONL or JSON file: one an Agent Tool wrote (connect its result and name the file) or one under ComfyUI's input/output folders.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| File | `STRING` | File name in the tool's run folder, or a path under input/ or output/. |
| Data (optional) | `DICT` | Connect an Agent Tool's result. |
| Rows | `INT` | How many rows to show. Default: `20`. |

## Where it runs

`AgentWorkflowPreviewTable` in `nayose_nodes.py` (category `agent_workflow_studio`).
