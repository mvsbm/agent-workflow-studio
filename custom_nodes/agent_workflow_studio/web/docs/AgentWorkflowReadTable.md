# Read table

Reads a CSV/TSV/JSONL file from a folder in ComfyUI's input directory (UTF-8 or Shift_JIS, detected).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Folder | `STRING` | Folder inside ComfyUI's input directory (connect a data source's folder). |
| File | `STRING` | File name in that folder; empty: the folder's one data file, whatever its name (a new month's export). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | The rows, for the next step. |

## Where it runs

`AgentWorkflowReadTable` in `table_nodes.py` (category `agent_workflow_studio/table`).
