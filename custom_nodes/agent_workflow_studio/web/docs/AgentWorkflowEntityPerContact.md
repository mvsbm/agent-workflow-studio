# Entity per contact

Every contact with its entity id: contacts Cluster guard grouped share their cluster's id; every other contact is its own entity. Writes entities.csv (record, cluster).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Contacts | `CONTACTS` | From Read contacts (every key). |
| Clusters | `RECORD_CLUSTERS` | From Cluster guard. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| entities | `RECORD_CLUSTERS` | One row per contact. |

## Where it runs

`AgentWorkflowEntityPerContact` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
