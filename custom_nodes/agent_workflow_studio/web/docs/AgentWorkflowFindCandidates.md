# Find candidate pairs

Finds the pairs worth comparing. Exact keys (blocking): rows sharing a value in a key become pairs; groups larger than the limit are skipped (a common surname would pair everyone). Similar values (sparse retrieval): `kana ~ 0.5` pairs rows whose kana share most of their 2-character pieces, so a typo or a cut-off reading still meets. Each pass is listed with its count.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Records | `TABLE` | One row per record (mention). |
| Id columns | `STRING` | Columns that together identify a record. Default: `system, record_id, role`. |
| Blocking keys | `STRING` | One pass per line: a column, columns joined with + (kana+birth), or column ~ similarity (kana ~ 0.5: one typo in five letters still meets). Default: `kana birth phone`. |
| Largest group | `INT` | Skip key values shared by more records than this. Default: `50`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| pairs | `TABLE` | left, right, keys (which passes found the pair). |

## Where it runs

`AgentWorkflowFindCandidates` in `table_nodes.py` (category `agent_workflow_studio/table`).
