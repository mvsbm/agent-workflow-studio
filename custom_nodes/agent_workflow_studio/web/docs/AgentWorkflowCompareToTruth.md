# Compare to truth

How often a result matches the known answers, per column and overall, with examples of misses. Feed its accuracy to a Check (at_least).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Result | `TABLE` | What the workflow produced. |
| Truth | `TABLE` | The known answers. |
| Key | `STRING` | Columns that identify a record in both. Default: `id`. |
| Compare | `STRING` | Columns to compare (empty: all shared). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| accuracy | `FLOAT` |  |
| per column | `TABLE` |  |

## Where it runs

`AgentWorkflowCompareToTruth` in `synth_nodes.py` (category `agent_workflow_studio/table`).
