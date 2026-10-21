# Learn weights

Learns each field level's points from the compared pairs (Fellegi–Sunter with EM; your Review decisions count as known answers) and writes them into the Decision rules YAML, with match and review thresholds set from target probabilities. Shows the learned weights next to the old ones.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Pairs (optional) | `RECORD_PAIRS,DICT` | Connect the tool that wrote the compared pairs. |
| Pairs file | `STRING` | One line per pair with a level per field. Default: `pairs.jsonl`. |
| Starting rules | `STRING` | Decision rules YAML: hard rules are kept, its score fields are re-learned and its points are the starting guess. |
| Match at | `FLOAT` | Pairs at least this likely to be the same go to match. Default: `0.95`. |
| Review from | `FLOAT` | Pairs between this and Match at go to review. Default: `0.3`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| rules | `STRING` | The YAML with learned points; connect to Decision rules' Rules. |
| data | `RECORD_PAIRS` | Passes the pairs on (run folders) to Decision rules. |

## Where it runs

`AgentWorkflowLearnWeights` in `matching_nodes.py` (category `agent_workflow_studio/matching`).
