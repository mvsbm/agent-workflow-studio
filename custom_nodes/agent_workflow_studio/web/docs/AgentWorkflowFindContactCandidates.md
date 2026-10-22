# Find contact candidates

Finds the pairs worth comparing: exact keys (birth date with each name part, the full name), a shared phone or house, birth date + town, and names alike in their 2-character pieces. A key shared by more contacts than its cap pairs no one (a family number, a common name).

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Records | `CONTACT_RECORDS` | From Standardize contacts. |
| Exact keys | `BOOLEAN` | Birth date + name parts, full name alone. Default: `True`. |
| Shared phone or house | `BOOLEAN` | Never decides alone: only makes a pair to compare. Default: `True`. |
| Birth date + town | `BOOLEAN` | Finds contacts with no usable name. Default: `True`. |
| Similar names | `BOOLEAN` | Typos and other readings still meet. Default: `True`. |
| Largest exact group | `INT` | Skip exact keys shared by more contacts than this. Default: `50`. |
| Largest shared group | `INT` | Skip phones, houses and towns shared by more contacts than this. Default: `20`. |
| Name similarity | `FLOAT` | How alike names must be (cosine of their 2-character pieces). Default: `0.5`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| candidates | `CANDIDATE_PAIRS` | Pairs to compare. |

## Where it runs

`AgentWorkflowFindContactCandidates` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
