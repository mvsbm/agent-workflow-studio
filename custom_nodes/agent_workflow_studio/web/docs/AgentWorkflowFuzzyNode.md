# Fuzzy · Plan with nodes

Describe a text job; the model plans a chain of allowed installed nodes, which is validated against their schemas and runs as real nodes. No code is generated.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Goal | `STRING` | What to do with the input text, in plain words. |
| Input text | `STRING` | The text to process; connect a text node or type it here. |
| Allowed nodes | `STRING` | Comma-separated node class types the plan may use. Default: `StringConcatenate, StringSubstring, StringTrim, StringReplace, CaseConverter, RegexReplace, RegexExtract`. |
| Pi model config (optional) | `PI_MODEL_CONFIG_V1` | Connect Pi CLI · Model Config to choose the planning model. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| result | `STRING` |  |
| plan | `DICT` |  |

## Where it runs

`AgentWorkflowFuzzyNode` in `fuzzy_node.py` (category `agent_workflow_studio/fuzzy`).
