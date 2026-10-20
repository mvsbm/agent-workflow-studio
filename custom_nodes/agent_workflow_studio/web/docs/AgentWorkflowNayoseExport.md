# Nayose · 3 Legacy export

One legacy system's export of the same facts, damaged the way that system damages data (mainframe kana and 〓, OCR on paper forms, call-centre homophones, bank-format kana...). Writes the dirty file to ComfyUI's input folder and its answer key under output/nayose_benchmark.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Facts | `NAYOSE_FACTS` | From Nayose · 2 Insurance activity. |
| System | `COMBO` | Which legacy system exports these facts, in its own format and with its own kinds of damage. |
| Damage | `FLOAT` | 0 = clean, 1 = realistic, 3 = terrible Default: `1.0`. |
| Folder | `STRING` | Folder inside ComfyUI's input directory. Default: `nayose`. |
| Seed | `INT` | Same seed, same data: change it for a different sample. Default: `7`. |
| After (optional) | `STRING` | Connect another export's folder to run after it. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| folder | `STRING` | The input folder with the exported files. |

## Where it runs

`AgentWorkflowNayoseExport` in `nayose_nodes.py` (category `agent_workflow_studio/nayose benchmark`).
