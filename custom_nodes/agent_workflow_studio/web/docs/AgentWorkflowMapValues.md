# Map values

Replaces characters, parts of values or whole values using a visible map (from:to pairs), e.g. variant kanji 髙 → 高, OCR look-alikes in readings 力 → カ, or a company marker ｶ) → nothing (ｶ):).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to map. |
| Columns | `STRING` | Comma-separated columns. |
| Map | `STRING` | from:to pairs separated by spaces or new lines. Default: `髙:高 﨑:崎 邊:辺 邉:辺 齋:斎 齊:斉 𠮷:吉 德:徳 澤:沢 濱:浜 嶋:島 櫻:桜 廣:広 國:国 龍:竜 惠:恵 冨:富`. |
| Replace | `COMBO` | Each character, the whole value, or each place a from-text appears (longest first; an empty to removes it). |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Rows with values mapped. |

## Where it runs

`AgentWorkflowMapValues` in `table_nodes.py` (category `agent_workflow_studio/table`).
