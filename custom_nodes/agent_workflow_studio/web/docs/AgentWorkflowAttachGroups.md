# Attach group ids

Gives every record its group id from Cluster guard (clusters.csv); records in no group get their own new id.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Records | `TABLE` | All records. |
| Groups | `CUSTOMERS,DICT` | Connect Cluster guard's customers. |
| Id columns | `STRING` | Same as in Find candidate pairs. Default: `system, record_id, role`. |
| Id column | `STRING` | Name of the new column. Default: `customer_id`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| table | `TABLE` | Records with their group id. |

## Where it runs

`AgentWorkflowAttachGroups` in `table_nodes.py` (category `agent_workflow_studio/table`).
