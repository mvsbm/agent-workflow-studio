# Nayose · 2 Insurance activity

Clean facts: policies (auto, fire, medical, injury, liability) with holder, insured and beneficiary; claims by holders and family; premium and payout bank registrations. Update rate: how often a customer's change reaches a policy at all.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| People | `SYNTHETIC_PEOPLE` | From Nayose · 1 People. |
| Update rate | `FLOAT` | How often a move, marriage or new phone is reported to a policy at all (lower = more stale records). Default: `0.5`. |
| Seed | `INT` | Same seed, same data: change it for a different sample. Default: `7`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| facts | `SYNTHETIC_ACTIVITY` | Clean policies, claims and bank registrations. |

## Where it runs

`AgentWorkflowNayoseActivity` in `nayose_nodes.py` (category `agent_workflow_studio/nayose benchmark`).
