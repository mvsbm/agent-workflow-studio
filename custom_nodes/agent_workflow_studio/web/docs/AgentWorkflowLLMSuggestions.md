# LLM suggestions

Asks a local model (an OpenAI-compatible server on this machine, e.g. llama-server with Gemma) about the pairs Decision rules sent to review, and shows its suggestion and reason next to each pair in the Review tab. It never decides: you do. Shows how often it agrees with your decisions so far.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Decisions | `PAIRS,DECISIONS,DICT` | Connect Decision rules' decisions. |
| Pairs file | `STRING` | The compared pairs, for the records' details. Default: `pairs.jsonl`. |
| Model server | `STRING` | A local OpenAI-compatible server, e.g. llama-server -hf ggml-org/gemma-4-E4B-it-GGUF:Q4_0 --port 8091. Default: `http://127.0.0.1:8091/v1`. |
| Model | `STRING` | Model name the server expects. Default: `gemma-4-E4B-it`. |
| Instructions | `STRING` | What the model is told; edit it to change how it judges. Default: `You review customer records from a Japanese insurer's legacy systems for nayose (deciding whether two records are the same person). The data is dirty: OCR errors, typos, wrong kanji for the same reading, missing fields, half-width or bank-format kana, Japanese era dates, swapped day and month, old addresses, maiden names, and family members who share an address, phone or bank account but are different people. Answer with JSON: {"same": "yes" \| "no" \| "unsure", "reason": "<one short sentence>"}.`. |
| Pairs | `INT` | At most this many review pairs, most uncertain first. Default: `200`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| suggestions | `PAIRS` | Counts and run folders (suggestions.csv) for Cluster guard. |

## Where it runs

`AgentWorkflowLLMSuggestions` in `matching_nodes.py` (category `agent_workflow_studio/matching`).
