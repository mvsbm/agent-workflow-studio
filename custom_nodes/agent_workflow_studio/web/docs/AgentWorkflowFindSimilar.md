# Find similar records (bi-encoder)

Dense retrieval: turns each record into a vector with a local Japanese embedding model and pairs every record with its nearest others, adding them to the candidate pairs (keys: embedding). Catches records written differently that share no exact key. Runs on this machine.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Records | `TABLE` | One row per record. |
| Pairs (optional) | `TABLE` | Candidate pairs found so far (Find candidate pairs); the similar ones are added. |
| Id columns | `STRING` | Same as in Find candidate pairs. Default: `system, record_id, role`. |
| Text from | `STRING` | Columns joined into each record's text. Default: `name, kana, birth, address`. |
| Nearest per record | `INT` | How many of its most similar records each record is paired with. Default: `5`. |
| Similarity at least | `FLOAT` | Cosine similarity of the two records' vectors. Default: `0.9`. |
| Model | `STRING` | A Hugging Face embedding model; its weights download once, records never leave. Default: `cl-nagoya/ruri-v3-30m`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| pairs | `TABLE` | left, right, keys. |

## Where it runs

`AgentWorkflowFindSimilar` in `semantic_nodes.py` (category `agent_workflow_studio/matching`).
