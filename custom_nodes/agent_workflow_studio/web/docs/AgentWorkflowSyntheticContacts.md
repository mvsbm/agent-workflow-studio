# Synthetic contacts (benchmark)

Generates CRM-shaped contact exports (contacts, phones, addresses on CONT_ID) with realistic dirt and an answer key, so a matching workflow can be measured. Real profiles use real names, towns and companies once their sources are downloaded.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Profile | `COMBO` | How dirty and how many companies. Default: `company_hard`. |
| Contacts | `INT` | About how many contacts. Default: `10000`. |
| Seed | `INT` | The same seed gives the same data. Default: `7`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| folder | `STRING` | The data folder (for Read contacts). |
| answer key | `STRING` | The answer key file (for the simulated reviewer and Score contacts). |

## Where it runs

`AgentWorkflowSyntheticContacts` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
