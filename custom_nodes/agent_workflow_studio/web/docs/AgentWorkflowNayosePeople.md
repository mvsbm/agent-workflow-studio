# Nayose · 1 People

Synthetic Japanese households with life histories: moves, marriages (surname changes), children, phones, bank accounts; plus look-alike strangers and small companies.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Households | `INT` | How many households to simulate (about 2.3 people each). Default: `1200`. |
| Seed | `INT` | Same seed, same data: change it for a different sample. Default: `7`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| people | `NAYOSE_PEOPLE` | Simulated people and companies with their life histories. |

## Where it runs

`AgentWorkflowNayosePeople` in `nayose_nodes.py` (category `agent_workflow_studio/nayose benchmark`).
