# Compare contacts

Measures how alike the two contacts of each pair are: names in both scripts, birth date (and how many contacts share it), phones and houses (and how common they are), addresses, the company name core, the person/company flag. Writes pairs.jsonl for Learn from your reviews.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Records | `CONTACT_RECORDS` | From Standardize contacts. |
| Candidates | `CANDIDATE_PAIRS` | From Find contact candidates. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| pairs | `RECORD_PAIRS` | Compared pairs. |

## Where it runs

`AgentWorkflowCompareContacts` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
