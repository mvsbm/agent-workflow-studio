# Profile data (EDA)

A first look at a data set: per file rows and columns; per column how full it is, distinct values, value patterns (9 digit, A/a letter, カ katakana, ｶ half-width kana, ひ hiragana, 漢 kanji) and examples. Mixed formats and dirty columns show at a glance. Passes the folder on.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Folder | `STRING` | A folder inside ComfyUI's input directory (connect a data source's folder). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| folder | `STRING` | The same folder, for the next stage. |

## Where it runs

`AgentWorkflowProfileData` in `eda_node.py` (category `agent_workflow_studio`).
