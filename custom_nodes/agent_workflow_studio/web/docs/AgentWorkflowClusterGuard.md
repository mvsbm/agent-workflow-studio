# Cluster guard

Builds clusters from the match decisions, strongest first, and refuses any merge that would join records already decided to be different people (by a rule or by you) or exceed a size cap — so one wrong link cannot chain hundreds of records. Shows the whole decision tree with counts and every blocked merge with its reason. Writes clusters.csv (record, cluster): a record_clusters dataset.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| All decisions (optional) | `PAIR_DECISIONS,DICT` | Decision rules' decisions (all pairs), or connect the branches below instead. |
| Matched 1 (optional) | `PAIR_DECISIONS,DICT` | A stage's matched branch (Decision rules, Your review decisions). |
| Matched 2 (optional) | `PAIR_DECISIONS,DICT` | A stage's matched branch (Decision rules, Your review decisions). |
| Matched 3 (optional) | `PAIR_DECISIONS,DICT` | A stage's matched branch (Decision rules, Your review decisions). |
| Matched 4 (optional) | `PAIR_DECISIONS,DICT` | A stage's matched branch (Decision rules, Your review decisions). |
| Different 1 (optional) | `PAIR_DECISIONS,DICT` | A stage's different branch (Decision rules, Your review decisions). |
| Different 2 (optional) | `PAIR_DECISIONS,DICT` | A stage's different branch (Decision rules, Your review decisions). |
| Different 3 (optional) | `PAIR_DECISIONS,DICT` | A stage's different branch (Decision rules, Your review decisions). |
| Different 4 (optional) | `PAIR_DECISIONS,DICT` | A stage's different branch (Decision rules, Your review decisions). |
| Max records per cluster | `INT` | 0 = no cap. Default: `60`. |
| Scores keep apart (optional) | `BOOLEAN` | A pair the score decided different also keeps its records apart (a learned score is proof enough); off: only rules and your reviews do. Default: ``. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| clusters | `RECORD_CLUSTERS` | Counts and run folders (clusters.csv) for the tool that writes the result. |

## Where it runs

`AgentWorkflowClusterGuard` in `matching_nodes.py` (category `agent_workflow_studio/matching`).
