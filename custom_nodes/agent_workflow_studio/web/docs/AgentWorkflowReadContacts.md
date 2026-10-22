# Read contacts

Reads your contact exports through a column map: which file and column hold the key, the names (kanji, kana, romaji), birth date, gender, the person/company flag, phones and addresses. Phones and addresses may sit in their own files (joined on the key) or in columns of the contacts file. Stops with the column to map when one is missing; counts duplicate keys and unreadable dates.

## Inputs

| Input | Type | What it is |
| --- | --- | --- |
| Folder | `STRING` | The data folder (connect Data folder). |
| Column map | `STRING` | YAML: which column holds what. Only key, birth_date and one name column are required. Default: `# Which column holds what. Files are in the data folder. Only key, birth_date and one name # column are required; leave out what your data does not have. key: CONT_ID # or a list of columns that together identify a contact contacts: contacts.csv surname: {kanji: LAST_NAME_KANJI, kana: LAST_NAME_KANA, romaji: LAST_NAME_ROMAJI} given: {kanji: FIRST_NAME_KANJI, kana: FIRST_NAME_KANA, romaji: FIRST_NAME_ROMAJI} birth_date: BIRTH_DATE # or {column: 生年月日, format: "%Y/%m/%d"} gender: GENDER type: {column: CONT_TYPE, company: ["2"]} # the values that mean a company phones: {file: phones.csv, columns: [PHONE]} # no file: columns of the contacts file addresses: {file: addresses.csv, kanji: [ADDRESS_KANJI]} # several columns: parts of one address`. |

## Outputs

| Output | Type | What it is |
| --- | --- | --- |
| contacts | `CONTACTS` | One contact per key. |

## Where it runs

`AgentWorkflowReadContacts` in `contacts_nodes.py` (category `agent_workflow_studio/contacts`).
