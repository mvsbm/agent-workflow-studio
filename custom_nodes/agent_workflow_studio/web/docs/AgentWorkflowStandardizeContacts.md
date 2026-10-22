# Standardize contacts

Makes contacts comparable: kanji variants folded, kana keys (small kana, ヅ/ヂ, long vowels), phones canonical, addresses parsed to town, house and room against a town registry (renamed municipalities mapped to today's name). Each fix below earned its place in the measurements.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Contacts | `CONTACTS` | From Read contacts. |
| Town registry | `STRING` | TSV (name, current) in the input folder; made once if missing. Empty: no registry. Default: `reference/towns.tsv`. |
| Repair kana OCR | `BOOLEAN` | 八→ハ, 卜→ト, 口→ロ … in kana fields. Default: `True`. |
| Readings from the data | `BOOLEAN` | Fill a missing kana from the reading the data uses for that kanji. Default: `True`. |
| Split whole names | `BOOLEAN` | A whole name in the surname field, split after a known surname. Default: `True`. |
| Shop name as surname | `BOOLEAN` | 藤江不動産 also meets 藤江 (a sole proprietor's shop). Default: `True`. |
| Clean company names | `BOOLEAN` | 御中, departments and contact persons removed; legal form, branch and loanword spelling folded. Default: `True`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| records | `CONTACT_RECORDS` | Standardized records. |

## Where it runs

`AgentWorkflowStandardizeContacts` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
