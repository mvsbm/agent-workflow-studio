# Data folder

A folder of data files in ComfyUI's input folder, for the stages that read it. Choose one, or upload files into a new one with the button (in App mode too); an API call names the folder it uploaded with POST /upload/image.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Folder | `COMBO` | A folder inside ComfyUI's input folder. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| folder | `STRING` | The folder's name. |

## Where it runs

`AgentWorkflowDataFolder` in `check_node.py` (category `agent_workflow_studio`).
