# Damage values (synthetic)

Makes clean records dirty the way real data is, from a readable recipe per column (rates): missing, typo, width, ocr, case, spaces, format, outlier; and `rows: {duplicate: rate}`. Keeps the clean truth and logs every change.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Clean records | `TABLE` | The truth to damage. |
| Damage | `STRING` | column: {kind: rate, …}; rows: {duplicate: rate}. Default: `name: {typo: 0.03, ocr: 0.02, missing: 0.01} birth: {format: 0.2} rows: {duplicate: 0.03}`. |
| Seed | `INT` | Same seed, same damage. Default: `7`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| damaged | `TABLE` |  |
| truth | `TABLE` | The clean records: the answers. |
| log | `TABLE` | Every change: row, column, kind, before, after. |

## Where it runs

`AgentWorkflowDamageValues` in `synth_nodes.py` (category `agent_workflow_studio/table`).
