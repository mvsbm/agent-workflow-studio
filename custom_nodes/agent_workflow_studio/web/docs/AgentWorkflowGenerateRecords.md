# Generate records (synthetic)

Synthetic records from a readable recipe (id, person_ja, date, choice, number, phone, email, address_ja, text), seeded.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Recipe | `STRING` | One field per line: name: {kind: …}. person_ja also writes <name>_kana. Default: `id: {kind: id, prefix: C} name: {kind: person_ja} birth: {kind: date, from: 1950-01-01, to: 2005-12-31} phone: {kind: phone}`. |
| Rows | `INT` | How many records. Default: `100`. |
| Seed | `INT` | Same seed, same data. Default: `7`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| records | `TABLE` |  |

## Where it runs

`AgentWorkflowGenerateRecords` in `synth_nodes.py` (category `agent_workflow_studio/table`).
