# Split name

Splits a name (reading) into surname and given name, normalized. Values written with a space teach it the surnames; values written without one are split at the longest known surname. Shows how many were split each way. Comparing given names separately catches maiden names (same given name and birth, different surname) and family members (same surname, different given name).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows with a name column. |
| Column | `STRING` | The name or reading column to split. Default: `kana`. |
| Surname seen at least | `INT` | A surname must appear this often with a space to be trusted. Default: `2`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Rows with <column>_surname and <column>_given added. |

## Where it runs

`AgentWorkflowSplitName` in `table_nodes.py` (category `agent_workflow_studio/table`).
