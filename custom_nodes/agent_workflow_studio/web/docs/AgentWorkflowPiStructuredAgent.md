# Pi CLI · Structured LLM

Send connected prompt text to the configured Pi CLI model. The model fills the output schema through a typed result tool; all action tools are disabled.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Prompt | `STRING` | Connect a ComfyUI text/prompt node here, or type directly and connect downstream. |
| Output schema JSON | `STRING` | JSON Schema for the required object output. Remote $ref is disabled. Default: `{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"],"additionalProperties":false}`. |
| Temperature | `FLOAT` | Retained for workflow compatibility; Pi CLI uses model-level generation settings. Default: ``. |
| Max tokens | `INT` | Advisory output-size target; Pi CLI RPC has no per-request token cap. Default: `2048`. |
| System prompt (optional) | `STRING` | Optional task instructions; private reasoning remains hidden. |
| Pi model config (optional) | `PI_MODEL_CONFIG_V1` | Connect Pi CLI · Model Config to select provider and model explicitly. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| validated_result | `DICT` |  |

## Where it runs

`AgentWorkflowPiStructuredAgent` in `agent_node.py` (category `agent_workflow_studio/llm`).
