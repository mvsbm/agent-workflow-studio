# Agent Tool

Runs a tool the agent wrote and you approved for this workflow (shown in Versions). Only the approved code runs, in a separate confined process with a time limit.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Tool | `STRING` | Name of an approved tool in this workflow. |
| Text input (optional) | `STRING` | Passed to the tool as inputs['text']. |
| Data input (optional) | `DICT,RECORD_PAIRS,PAIR_DECISIONS,RECORD_CLUSTERS` | Connect a DICT; its keys are passed to the tool as inputs. |
| Input folder (optional) | `STRING` | A folder inside ComfyUI's input directory the tool may read (not change), as TOOL_INPUT_DIR. Empty: no input files. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| result | `DICT` |  |
| text | `STRING` |  |

## Where it runs

`AgentWorkflowAgentTool` in `agent_tool_node.py` (category `agent_workflow_studio/tools`).
