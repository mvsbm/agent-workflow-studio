# Check

A step's gate: checks the data a stage gives (rows at least, non-empty, contains, ...) and shows pass or fail with the evidence. The step counts as verified when its checks pass.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Value | `*` | The stage output to check. |
| Must hold | `COMBO` | What must hold. Default: `non_empty`. |
| Expect | `STRING` | The number or text the check needs (not for non_empty). |
| Says | `STRING` | How we know the step worked, in plain words (from the contract). |

## Where it runs

`AgentWorkflowCheck` in `check_node.py` (category `agent_workflow_studio`).
