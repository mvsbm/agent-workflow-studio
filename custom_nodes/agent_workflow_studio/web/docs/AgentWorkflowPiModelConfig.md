# Pi CLI · Model Config

Select the Pi provider, model, thinking level, and credential source for connected LLM nodes. Credentials are never workflow data.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Provider ID | `STRING` | Provider ID from the Pi model catalog. Use the Pi Credentials menu to browse providers. |
| Model ID | `STRING` | Exact model ID from the Pi model catalog. |
| Thinking level | `COMBO` | Select the model's configured reasoning effort. Default: `medium`. |
| Credential source | `COMBO` | Studio Keychain resolves the selected provider's credential environment variables; Pi-managed uses Pi's own auth configuration. Default: `pi_managed`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| pi_model_config | `PI_MODEL_CONFIG_V1` |  |

## Where it runs

`AgentWorkflowPiModelConfig` in `pi_model_config.py` (category `agent_workflow_studio/llm`).
