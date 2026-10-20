# Compare fields

Compares each candidate pair field by field into plain levels — text: exact / similar (Jaro-Winkler at least the threshold) / different / missing; date: exact / close (day-month swapped, one digit, era mistake) / conflict / missing; same: same / different / missing — and writes pairs.jsonl for Learn weights and Decision rules. Shows how often each level occurs.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Records | `TABLE` | The cleaned records. |
| Pairs | `TABLE` | From Find candidate pairs. |
| Id columns | `STRING` | Same as in Find candidate pairs. Default: `system, record_id, role`. |
| Fields | `STRING` | YAML: field: {method: text \| date \| same, similar: threshold for text, common: with an exact match, adds <field>_freq = common (value shared by at least this many records) or rare}. Default: `kana: {method: text, similar: 0.88, common: 20} name: {method: text, similar: 0.85} birth: {method: date} phone: {method: same} address: {method: text, similar: 0.8}`. |
| Show in Review | `STRING` | Columns shown for each record of a pair. Default: `name, kana, birth, address, phone`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| compared | `TABLE` | Pairs with one level per field. |
| data | `PAIRS` | Run folders with pairs.jsonl, for Learn weights and Decision rules. |

## Where it runs

`AgentWorkflowCompareFields` in `table_nodes.py` (category `agent_workflow_studio/table`).
