# Normalize text

Normalizes text columns with the steps you tick: widths (NFKC: full-width ASCII and half-width kana), hiragana → katakana, small kana → large, spaces, upper case.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows to clean. |
| Columns | `STRING` | Comma-separated columns to normalize. |
| Unify widths (NFKC) | `BOOLEAN` | ＡＢＣ１２３ → ABC123, ｶﾀｶﾅ → カタカナ. Default: `True`. |
| Hiragana → katakana | `BOOLEAN` | やまだ → ヤマダ. Default: `True`. |
| Small kana → large | `BOOLEAN` | キョウコ → キヨウコ (as legacy systems store it). Default: `True`. |
| Remove spaces | `BOOLEAN` | 山田 太郎 → 山田太郎 (off: collapse to one space). Default: `True`. |
| Upper case | `BOOLEAN` | Latin letters to upper case. Default: ``. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Rows with the columns normalized. |

## Where it runs

`AgentWorkflowNormalizeText` in `table_nodes.py` (category `agent_workflow_studio/table`).
