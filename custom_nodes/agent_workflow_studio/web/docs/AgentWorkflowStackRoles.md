# Stack roles

Turns column groups into rows: a row with holder (KYK_*), insured (HHK_*) and beneficiary (UKT_*) columns becomes up to three rows with a `role` column and the same field names. Rows whose name fields are all empty (or a non-person value) are left out.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Table | `TABLE` | Rows with one column group per role. |
| Plan | `STRING` | YAML: system, id, keep, roles (role: column prefix), fields (suffix: field), require_any, not_a_person. Default: `system: policies # written into a `system` column id: REC_ID # the row's id, kept as record_id keep: {SHOKEN_NO: policy_ref} # other columns copied to every role row (renamed) roles: {holder: KYK_, insured: HHK_, beneficiary: UKT_} fields: {NM_KJ: name, NM_KN: kana, BIRTH: birth, SEX: sex, ADDR: address, TEL: phone, ZIP: zip} require_any: [name, kana] not_a_person: [法定相続人, 未指定]`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| mentions | `TABLE` | One row per role that names someone. |

## Where it runs

`AgentWorkflowStackRoles` in `table_nodes.py` (category `agent_workflow_studio/table`).
