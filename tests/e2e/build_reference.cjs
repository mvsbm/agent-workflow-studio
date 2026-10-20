// Builds workflows/Studio - Nayose reference (no code).json in an isolated ComfyUI: the nayose
// benchmark solved only with visible nodes (no agent code), one stage per standard step, run, scored
// and packed into stage cards. Usage: scripts/build_templates.sh (after the benchmark template).
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const root = path.join(__dirname, '..', '..', 'workflows');
const template = JSON.parse(fs.readFileSync(path.join(root, 'Studio - Nayose benchmark.json'), 'utf8'));
const target = path.join(root, 'Studio - Nayose reference (no code).json');
// VARIANT=deterministic: only the certain rules decide; VARIANT=patterns: a classic match-pattern grid
// (no learning). Both leave unsettled pairs unmerged and print the score instead of saving the template.
const VARIANT = process.env.VARIANT || '';
const PATTERNS = `# A classic deterministic match-pattern grid (Trillium-style): fixed comparisons, no learning.
rules:
  - {name: Different sex, when: {sex: different}, then: different}
  - {name: Conflicting birth, when: {birth: conflict}, then: different}
  - {name: Reading and birth exact, when: {kana: exact, birth: exact}, then: match}
  - {name: Name and birth exact, when: {name: exact, birth: exact}, then: match}
  - {name: Reading exact and same phone, when: {kana: exact, phone: same}, then: match}
  - {name: Reading exact and same policy, when: {kana: exact, policy_ref: same}, then: match}
  - {name: Reading exact and address exact, when: {kana: exact, address: exact}, then: match}
  - {name: Reading similar, birth exact, address similar, when: {kana: similar, birth: exact, address: [exact, similar]}, then: match}
  - {name: Reading exact, birth close, same phone, when: {kana: exact, birth: close, phone: same}, then: match}
`;

// The standard nayose pipeline, one verb per stage: clean each source, enrich, find candidates,
// compare, decide (certain rules, then a learned score), you verify, group, publish.
// Numbered in order; the clean lanes run side by side and share a number. The reranker stage only
// exists with RERANK=1, so numbers follow the stages that are there.
const STEPS = [
  ['profile_data', 'Profile'],
  ['clean_policies', 'Clean policies'], ['clean_claims', 'Clean claims'], ['clean_payments', 'Clean payments'],
  ['enrich', 'Enrich'], ['find_candidates', 'Find candidates'], ['compare', 'Compare'],
  ['decide_rules', 'Decide: certain rules'], ['decide_score', 'Decide: learned score'],
  ['decide_rerank', 'Decide: reranker (hard pairs)'],
  ['you_verify', 'You verify'], ['group_customers', 'Group into customers'], ['publish', 'Publish'],
].filter(([id]) => id !== 'decide_rerank' || process.env.RERANK === '1')
  .map(([id, name], i, all) => ({id, number: all.slice(0, i + 1).filter(([other], j) => !(other.startsWith('clean_') && j > 0 && all[j - 1][0].startsWith('clean_'))).length, name}))
  .map(({id, number, name}) => ({id, title: `${number} · ${name}`}));
// The reference's approved contract. After saving, native_reference.py stores it natively (intent in
// extra.aws_contract, each step on its stage) and tags each stage's nodes with what they realize. The Contract
// view compares it with what is built and proven. Each gate's check is an expected count on the
// default data (seed 7, measured), with room for other seeds; a person-step has no check (you decide).
const at = (output, kind, value) => ({output, kind, value});
const CONTRACT = {
  contract: 2,
  purpose: 'Give every person mention in three legacy insurance systems one customer id, with uncertain pairs decided by you.',
  requests: [
    {id: 'R1', you_said: 'find whether customers are the same person across the policy, claims and payment systems', we_understood: 'When the exports are read, the workflow gives every person mention a customer id, the same id for the same person.', priority: 'must'},
    {id: 'R2', you_said: 'a person checks what the machine is unsure of', we_understood: 'When a pair is uncertain, the workflow sends it to you in the Review tab instead of guessing.', priority: 'should'},
    {id: 'R3', you_said: 'each system is dirty in its own way', we_understood: 'When records are cleaned, each system gets its own cleaning lane.', priority: 'should'},
  ],
  examples: [{id: 'E1', covers: ['R1'], given: {seed: 7}, expect: {customer_file: 'nayose_result.csv'}, note: 'The default data: the run publishes one customer id per mention.'}],
  promises: {
    inputs: [{name: 'seed', kind: 'integer', example: 7, widget: 'seed'}],  // node: the given Synthetic data (set in the page)
    outputs: [{name: 'customer_file', kind: 'file', from: 'publish.path'}],
  },
  steps: [
    {id: 'profile_data', purpose: 'Look at the three exports before cleaning.', approach: 'fixed', covers: ['R3'], takes: {folder: 'STRING'}, gives: {folder: 'STRING'}, uses: 'Profile data (EDA)', why: 'each column’s dirt decides its cleaning', pattern: 'Profile before transform', gate: {says: 'The synthetic exports were profiled.', check: at('folder', 'contains', 'nayose_bench')}},
    {id: 'clean_policies', purpose: 'Clean the policy system’s mentions in its own lane.', approach: 'fixed', covers: ['R3'], takes: {folder: 'profile_data.folder'}, gives: {table: 'TABLE'}, uses: 'Read table, Stack roles, Normalize text, Parse dates', why: 'it stores three roles side by side', pattern: 'Pipes and filters', gate: {says: 'At least 4,500 policy mentions come out (5,480 on the default data).', check: at('table', 'rows_at_least', 4500)}},
    {id: 'clean_claims', purpose: 'Clean the claims system’s mentions in its own lane.', approach: 'fixed', covers: ['R3'], takes: {folder: 'profile_data.folder'}, gives: {table: 'TABLE'}, uses: 'Read table, Stack roles, Normalize text, Parse dates', why: 'clerks type variant kanji', pattern: 'Pipes and filters', gate: {says: 'At least 2,200 claim mentions come out (2,699 on the default data).', check: at('table', 'rows_at_least', 2200)}},
    {id: 'clean_payments', purpose: 'Clean the payment system’s mentions in its own lane.', approach: 'fixed', covers: ['R3'], takes: {folder: 'profile_data.folder'}, gives: {table: 'TABLE'}, uses: 'Read table, Stack roles, Normalize text', why: 'it holds only bank kana', pattern: 'Pipes and filters', gate: {says: 'At least 4,500 payment mentions come out (5,396 on the default data).', check: at('table', 'rows_at_least', 4500)}},
    {id: 'enrich', purpose: 'Join the lanes and fix OCR look-alikes and company marks.', approach: 'fixed', covers: ['R1'], takes: {a: 'clean_policies.table', b: 'clean_claims.table', c: 'clean_payments.table'}, gives: {table: 'TABLE'}, uses: 'Concatenate tables, Map values', why: 'the same name must read the same way', pattern: 'Content enricher', gate: {says: 'All mentions go on: at least 11,500 (13,575 on the default data).', check: at('table', 'rows_at_least', 11500)}},
    {id: 'find_candidates', purpose: 'Pair mentions that share a key.', approach: 'fixed', covers: ['R1'], takes: {table: 'enrich.table'}, gives: {pairs: 'TABLE'}, uses: 'Find candidate pairs (blocking keys)', why: 'comparing every pair is too slow', pattern: 'Blocking', gate: {says: 'At least 45,000 candidate pairs (about 58,000 on the default data).', check: at('pairs', 'rows_at_least', 45000)}},
    {id: 'compare', purpose: 'Compare each pair field by field.', approach: 'fixed', covers: ['R1'], takes: {records: 'enrich.table', pairs: 'find_candidates.pairs'}, gives: {data: 'PAIRS'}, uses: 'Compare fields', why: 'decisions need plain levels', pattern: 'Comparison vector', gate: {says: 'At least 45,000 pairs compared (57,624 on the default data).', check: at('data', 'rows_at_least', 45000)}},
    {id: 'decide_rules', purpose: 'Settle the certain pairs by rule.', approach: 'fixed', covers: ['R1'], takes: {data: 'compare.data'}, gives: {matched: 'DECISIONS', different: 'DECISIONS', unsettled: 'PAIRS'}, uses: 'Decision rules', why: 'sure cases need no score', pattern: 'Rules cascade', gate: {says: 'At least 7,000 certain matches (9,857 on the default data).', check: at('matched', 'rows_at_least', 7000)}},
    {id: 'decide_score', purpose: 'Score the rest with learned points.', approach: 'fixed', covers: ['R1'], takes: {data: 'decide_rules.unsettled'}, gives: {matched: 'DECISIONS', different: 'DECISIONS', unsettled: 'PAIRS'}, uses: 'Learn weights, Decision rules', why: 'each field’s evidence differs', pattern: 'Fellegi–Sunter', gate: {says: 'At least 30,000 scored matches (38,520 on the default data).', check: at('matched', 'rows_at_least', 30000)}},
    {id: 'you_verify', purpose: 'You decide the pairs the score is unsure of.', approach: 'fixed', person: true, covers: ['R2'], takes: {data: 'decide_score.unsettled'}, gives: {matched: 'DECISIONS', different: 'DECISIONS'}, uses: 'Your review decisions', why: 'a person beats a guess', pattern: 'Human in the loop', gate: {says: 'The unsure pairs reached you in the Review tab.'}},
    {id: 'group_customers', purpose: 'Group matches into customers, refusing bad merges.', approach: 'fixed', covers: ['R1'], takes: {matched_1: 'decide_rules.matched', matched_2: 'decide_score.matched', matched_3: 'you_verify.matched', different_1: 'decide_rules.different', different_2: 'decide_score.different', different_3: 'you_verify.different'}, gives: {customers: 'CUSTOMERS'}, uses: 'Cluster guard', why: 'one wrong link must not chain many', pattern: 'Guarded clustering', gate: {says: 'Nearly every mention is in a customer: at least 11,500 (13,411 on the default data).', check: at('customers', 'rows_at_least', 11500)}},
    {id: 'publish', purpose: 'Write one customer id per mention.', approach: 'fixed', covers: ['R1'], takes: {records: 'enrich.table', groups: 'group_customers.customers'}, gives: {path: 'STRING'}, uses: 'Attach group ids, Write table', why: 'the systems need one shared id', pattern: 'Publish', gate: {says: 'The result file is written.', check: at('path', 'contains', 'nayose_result.csv')}},
  ],
  decisions: [
    {id: 'D1', decision: 'No code: every step is a visible node.', why: 'you can read and audit each step', instead_of: 'one matching script', by: 'agent', principle: 'transparency'},
    {id: 'D2', decision: 'Uncertain pairs go to you, not to a model.', why: 'a wrong merge costs more than a question', instead_of: 'auto-deciding with a reranker', by: 'agent', pattern: 'Human in the loop'},
  ],
  not_now: ['Real customer exports (this reference runs on synthetic data)', 'A neural reranker or embeddings deciding pairs'],
  open_questions: [],
  data: {
    table: {description: 'One row per person mention, cleaned', key: ['system', 'record_id', 'role'], fields: ['system', 'record_id', 'role', 'name', 'kana']},
    pairs: {description: 'Candidate pairs of mentions and the keys that paired them', key: ['left', 'right']},
    data: {description: 'Compared pairs: one level per field (exact, similar, different, missing)'},
    matched: {description: 'Pairs decided to be the same person, with the rule or score that decided'},
    different: {description: 'Pairs decided to be different people (they are never merged)'},
    unsettled: {description: 'Pairs a stage could not decide, passed on to the next'},
    customers: {description: 'Customers: groups of mentions, and the merges that were refused'},
  },
};

// Known ways Japanese records go wrong, as visible maps (real conventions, not this data's quirks).
const OCR_IN_KANA = '力:カ 工:エ 八:ハ 口:ロ 二:ニ 卜:ト 夕:タ 一:ー';
const COMPANY_KANA = 'カ): (カ: ユ): (ユ: カブシキガイシヤ: ユウゲンガイシヤ:';
const COMPANY_NAME = '株式会社: 有限会社: (株): (有):';

const PLANS = {
  policies: `system: policies\nid: REC_ID\nkeep: {SHOKEN_NO: policy_ref}\nroles: {holder: KYK_, insured: HHK_, beneficiary: UKT_}\nfields: {NM_KJ: name, NM_KN: kana, BIRTH: birth, SEX: sex, ADDR: address, TEL: phone, ZIP: zip}\nrequire_any: [name, kana]\nnot_a_person: [法定相続人, 未指定]\n`,
  claims: `system: claims\nid: REC_ID\nkeep: {SHOKEN_NO: policy_ref}\nroles: {claimant: SEIKYU_, insured: HHK_}\nfields: {NM: name, KANA: kana, BIRTH: birth, SEX: sex, ADDR: address, TEL: phone, ZIP: zip}\nrequire_any: [name, kana]\n`,
  payments: `system: payments\nid: REC_ID\nkeep: {KANRI_NO: policy_ref, KOZA_NO: account}\nroles: {account_holder: KOZA_}\nfields: {MEIGI: kana}\nrequire_any: [kana]\n`,
};
const DETERMINISTIC = `# Certainties only; everything else is passed on to probabilistic matching.
rules:
  - name: Different sex
    when: {sex: different}
    then: different
  - name: Family member sharing a phone (different given name)
    when: {kana_given: different, phone: same}
    then: different
  - name: Family member at the same address (different given name)
    when: {kana_given: different, address: [exact, similar]}
    then: different
  - name: Same name, conflicting birth date (a namesake)
    when: {birth: conflict}
    then: different
  - name: Maiden name (same given name and birth, surname changed)
    when: {kana_given: exact, birth: exact, kana_surname: different}
    then: match
  - name: Same policy and same reading
    when: {policy_ref: same, kana: exact}
    then: match
  - name: Same phone and same reading
    when: {phone: same, kana: exact}
    then: match
`;
const STARTING = `# Rough starting points; Learn weights replaces them with points learned from the pairs.
rules: []
score:
  kana_surname: {exact: 1, similar: 0.5, different: -1, missing: 0}
  kana_given: {exact: 2, similar: 1, different: -2, missing: 0}
  kana_freq: {rare: 1, common: 0, n/a: 0}
  name: {exact: 2, similar: 1, different: -1, missing: 0}
  birth: {exact: 2, close: 1, conflict: -2, missing: 0}
  phone: {same: 2, different: -1, missing: 0}
  address: {exact: 1, similar: 1, different: -1, missing: 0}
  policy_ref: {same: 1, different: 0, missing: 0}
decide: {match: 4, review: 2}
`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1700, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  await page.goto(base);
  await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
  const run = () => page.evaluate(() => new Promise(resolve => {
    const api = window.comfyAPI.api.api;
    api.addEventListener('execution_success', () => resolve('success'), {once: true});
    api.addEventListener('execution_error', e => resolve(`error: ${e.detail?.node_type}: ${e.detail?.exception_message}`), {once: true});
    void window.comfyAPI.app.app.queuePrompt(0, 1);
  }));
  await page.evaluate(async ({template, plans, deterministic, starting, variant, patternRules, seed, ocrInKana, companyKana, companyName, keys, dense, semantic, rerankHigh, rerankLow, reranker, embedder}) => {
    // Pairs only the embeddings found differ from what the points were learned on: the reranker decides them.
    const DENSE_TO_RERANKER = 'rules:\n  - {name: Only the embeddings found them, when: {found_by: embedding}, then: review}\n';
    // Pairs only the embeddings found are too often look-alikes to merge unseen: you confirm them.
    const RERANK_RULES = 'rules:\n  - {name: Reranker sure they differ, when: {rerank: low}, then: different}\n  - {name: Only the embeddings found them (you confirm), when: {found_by: embedding}, then: review}\n  - {name: Reranker sure they are the same, when: {rerank: high}, then: match}\n';
    const app = window.comfyAPI.app.app, LiteGraph = window.LiteGraph;
    template.id = crypto.randomUUID();
    await app.loadGraphData(template);
    const graph = app.graph;
    const find = test => graph.nodes.find(test);
    const synthetic = find(n => n.isSubgraphNode?.()), score = find(n => n.type === 'AgentWorkflowNayoseScore');
    if (seed) synthetic.widgets.find(w => w.name === 'seed').value = seed; // held-out data for validation
    const add = (type, step, title, widgets = {}) => {
      const node = LiteGraph.createNode(type);
      graph.add(node);
      node.title = title;
      node.properties = {...node.properties, aws_contract_step: step};
      for (const [name, value] of Object.entries(widgets)) node.widgets.find(w => w.name === name).value = value;
      return node;
    };
    const slot = (node, name) => node.inputs.findIndex(i => i.name === name || i.widget?.name === name);
    const link = (from, output, to, input) => from.connect(output, to, slot(to, input));
    const profile = add('AgentWorkflowProfileData', 'profile_data', 'Profile the exports');
    link(synthetic, 0, profile, 'folder');
    // One lane per source system: each has its own kind of dirt, cleaned on its own.
    const lane = (step, system, file, steps) => {
      let last = add('AgentWorkflowReadTable', step, `Read ${file}`, {file});
      link(profile, 0, last, 'folder');
      for (const [type, title, widgets] of steps) {
        const node = add(type, step, title, widgets);
        link(last, 0, node, 'table');
        last = node;
      }
      return last;
    };
    const policies = lane('clean_policies', 'policies', 'policy_master.csv', [
      ['AgentWorkflowStackRoles', 'One row per person (holder, insured, beneficiary)', {plan: plans.policies}],
      ['AgentWorkflowSplitName', 'Reading into surname and given name', {column: 'kana'}],  // before spaces go
      ['AgentWorkflowNormalizeText', 'Mainframe kana and names to standard', {columns: 'name, kana'}],
      ['AgentWorkflowMapValues', 'Variant kanji (〓-free) to standard', {columns: 'name'}],
      ['AgentWorkflowMapValues', 'Sex codes 1/2 to M/F', {columns: 'sex', map: '1:M 2:F', mode: 'whole values'}],
      ['AgentWorkflowParseDates', 'Era dates (S45.03.12, 3450312) to YYYY-MM-DD', {columns: 'birth'}],
      ['AgentWorkflowExtractDigits', 'Phones and postcodes to digits', {columns: 'phone, zip', min_digits: 6}],
    ]);
    const claims = lane('clean_claims', 'claims', 'claims.csv', [
      ['AgentWorkflowStackRoles', 'One row per person (claimant, insured)', {plan: plans.claims}],
      ['AgentWorkflowSplitName', 'Reading into surname and given name', {column: 'kana'}],
      ['AgentWorkflowNormalizeText', 'Heard names and hiragana to standard', {columns: 'name, kana'}],
      ['AgentWorkflowMapValues', 'Variant kanji to standard', {columns: 'name'}],
      ['AgentWorkflowMapValues', 'Sex 男/女 to M/F', {columns: 'sex', map: '男:M 女:F', mode: 'whole values'}],
      ['AgentWorkflowParseDates', 'Dates to YYYY-MM-DD', {columns: 'birth'}],
      ['AgentWorkflowExtractDigits', 'Phones and postcodes to digits', {columns: 'phone, zip', min_digits: 6}],
    ]);
    const payments = lane('clean_payments', 'payments', 'koza_furikae.csv', [
      ['AgentWorkflowStackRoles', 'One row per account holder', {plan: plans.payments}],
      ['AgentWorkflowSplitName', 'Account name into surname and given name', {column: 'kana'}],
      ['AgentWorkflowNormalizeText', 'Bank-format kana to standard', {columns: 'kana'}],
    ]);
    // Enrich: one table of all mentions, with what every source needs (fixed readings, name parts).
    const all = add('AgentWorkflowConcatenate', 'enrich', 'All mentions (3 systems)');
    [policies, claims, payments].forEach((lane, i) => link(lane, 0, all, 'abc'[i]));
    let merged = all;
    for (const [type, title, widgets] of [
      ['AgentWorkflowMapValues', 'OCR look-alikes in readings (力 → カ)', {columns: 'kana, kana_surname, kana_given', map: ocrInKana, mode: 'characters'}],
      ['AgentWorkflowMapValues', 'Company marks out of readings (ｶ) → —)', {columns: 'kana', map: companyKana, mode: 'parts of values'}],
      ['AgentWorkflowMapValues', 'Company forms out of names (株式会社 → —)', {columns: 'name', map: companyName, mode: 'parts of values'}],
    ]) {
      const node = add(type, 'enrich', title, widgets);
      link(merged, 0, node, 'table');
      merged = node;
    }
    const candidates = add('AgentWorkflowFindCandidates', 'find_candidates', 'Candidate pairs (keys)', {keys: keys || 'kana\npolicy_ref\nphone\nname\naccount\nkana_given+birth', max_group: 40});
    link(merged, 0, candidates, 'table');
    let found = candidates;
    if (dense) {  // dense retrieval: each mention's nearest others by a local embedding model
      found = add('AgentWorkflowFindSimilar', 'find_candidates', 'Similar mentions (bi-encoder)', {columns: 'name, kana, birth, address', ...(embedder && {model: embedder, top: 10, least: 0.8})});
      link(merged, 0, found, 'records');
      link(candidates, 0, found, 'pairs');
    }
    const compare = add('AgentWorkflowCompareFields', 'compare', 'Compare fields', {fields: 'kana: {method: text, similar: 0.88, common: 20}\nkana_surname: {method: text, similar: 0.9}\nkana_given: {method: text, similar: 0.9}\nname: {method: text, similar: 0.85}\nbirth: {method: date}\nphone: {method: same}\naddress: {method: text, similar: 0.8}\npolicy_ref: {method: same}\nsex: {method: same}\n', show: 'name, kana, birth, address, phone, policy_ref'});
    link(merged, 0, compare, 'records');
    link(found, 0, compare, 'pairs');
    const certain = add('AgentWorkflowDecisionRules', 'decide_rules', 'Certain matches and non-matches', {rules: variant === 'patterns' ? patternRules : deterministic});
    link(compare, 1, certain, 'data');
    const learn = add('AgentWorkflowLearnWeights', 'decide_score', 'Learn the points', {rules: dense && semantic ? starting.replace('rules: []\n', DENSE_TO_RERANKER) : starting});
    link(certain, 4, learn, 'data'); // only the pairs the certain rules left unsettled go on
    const scored = add('AgentWorkflowDecisionRules', 'decide_score', 'Score the unsettled pairs');
    link(learn, 0, scored, 'rules');
    link(learn, 1, scored, 'data');  // Learn weights passes the unsettled subset on
    // The expensive model only where the cheap one is unsure: a local reranker reads both records of
    // each hard pair together; its confident answers decide, the rest goes to you.
    let hard = scored;
    const reranked = semantic && add('AgentWorkflowRerankPairs', 'decide_rerank', 'Rerank the hard pairs (cross-encoder)', {high: rerankHigh, low: rerankLow, ...(reranker && {model: reranker})});
    const byReranker = semantic && add('AgentWorkflowDecisionRules', 'decide_rerank', 'Decide by the reranker', {rules: RERANK_RULES});
    if (semantic) {
      link(scored, 4, reranked, 'data');
      link(reranked, 0, byReranker, 'data');
      hard = byReranker;
    }
    const yours = add('AgentWorkflowReviewDecisions', 'you_verify', 'Your decisions on uncertain pairs');
    link(hard, 4, yours, 'data');
    const guard = add('AgentWorkflowClusterGuard', 'group_customers', 'Build customers (guarded)');
    // Decided pairs leave the cascade at every stage and meet here.
    link(certain, 2, guard, 'matched_1'); link(certain, 3, guard, 'different_1');
    if (!variant || variant === 'cascade') {
      link(scored, 2, guard, 'matched_2'); link(scored, 3, guard, 'different_2');
      link(yours, 0, guard, 'matched_3'); link(yours, 1, guard, 'different_3');
      if (semantic) { link(byReranker, 2, guard, 'matched_4'); link(byReranker, 3, guard, 'different_4'); }
    }
    const attach = add('AgentWorkflowAttachGroups', 'publish', 'Customer id for every mention');
    link(merged, 0, attach, 'records');
    link(guard, 0, attach, 'groups');
    const write = add('AgentWorkflowWriteTable', 'publish', 'Write nayose_result.csv', {file: 'nayose_reference/nayose_result.csv', columns: 'system, record_id, role, customer_id'});
    link(attach, 0, write, 'table');
    link(write, 0, score, 'result_file');
  }, {template, plans: PLANS, deterministic: DETERMINISTIC, starting: STARTING, variant: VARIANT, patternRules: PATTERNS, seed: Number(process.env.SEED || 0), ocrInKana: OCR_IN_KANA, companyKana: COMPANY_KANA, companyName: COMPANY_NAME, keys: (process.env.KEYS || '').replaceAll('\\n', '\n'), dense: process.env.DENSE === '1', semantic: process.env.RERANK === '1', rerankHigh: Number(process.env.HIGH || 0.9), rerankLow: Number(process.env.LOW || 0.02), reranker: process.env.RERANKER || '', embedder: process.env.EMBEDDER || ''});
  const first = await run();
  if (first !== 'success') throw Error(`first run: ${first}`);
  if (VARIANT) {
    const rows = await page.evaluate(() => window.comfyAPI.app.app.graph.nodes.find(n => n.type === 'AgentWorkflowNayoseScore').properties.aws_tables);
    const heads = await page.evaluate(() => window.comfyAPI.app.app.graph.nodes.filter(n => ['AgentWorkflowRerankPairs', 'AgentWorkflowFindSimilar', 'AgentWorkflowClusterGuard'].includes(n.type) || n.title === 'Decide by the reranker').map(n => `${n.title}: ${n.properties?.aws_headline ?? ''} | inputs ${n.inputs.filter(i => i.link != null).map(i => i.name).join(',')}`));
    console.error(heads.join('\n'));
    console.log(JSON.stringify({variant: VARIANT, score: rows[0].rows, by_damage: rows[1].rows.slice(0, 12), wrong_merges: rows[3]?.rows}));
    await browser.close();
    return;
  }
  const summary = await page.evaluate(async ({steps, contract}) => {
    const app = window.comfyAPI.app.app;
    const {packIntoStage, addCheck, arrangeGraph} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
    for (const {id, title} of steps) {
      const members = app.graph.nodes.filter(n => n.properties?.aws_contract_step === id);
      if (!members.length) continue;
      for (const n of members) delete n.properties.aws_contract_step; // the stage now says which step
      const stage = packIntoStage(app.graph, members, id, Number(/^\s*(\d+)/.exec(title)[1]));
      stage.title = stage.subgraph.name = title;  // the pipeline's own words (Decide: certain rules)
      // The contract's step: its agreed Check goes in the stage.
      const step = contract.steps.find(s => s.id === id);
      if (step.gate.check) addCheck(stage.subgraph, step.gate.check, step.gate.says);
    }
    arrangeGraph(app.graph);
    // Its API: a seed for the synthetic data in, the published customer ids out.
    const {setSignature} = await import('/extensions/agent_workflow_studio/publish.js');
    const synthetic = app.graph.nodes.find(n => n.isSubgraphNode?.() && !n.properties?.aws_stage);
    const published = app.graph.nodes.find(n => n.properties?.aws_stage === 'publish');
    // The promised output's top-level output node, wired from the stage that delivers it: App mode shows it.
    const answer = window.LiteGraph.createNode('AgentWorkflowApiOutput');
    app.graph.add(answer);
    answer.title = 'Output · customer_file';
    answer.properties = {...answer.properties, aws_output: 'customer_file', aws_output_from: 'publish.path'};
    answer.widgets.find(w => w.name === 'name').value = 'customer_file';
    published.connect(0, answer, 0);
    setSignature(app.graph, [{node: synthetic.id, widget: 'seed'}], [answer.id]);
    // Shipped with the workflow: on first open it becomes the workflow's approved contract.
    contract.promises.inputs[0].node = synthetic.id;
    app.graph.extra = {...app.graph.extra, aws_contract: contract};
    const score = app.graph.nodes.find(n => n.type === 'AgentWorkflowNayoseScore');
    return score.properties.aws_tables?.[0]?.rows ?? [];
  }, {steps: STEPS, contract: CONTRACT});
  const second = await run(); // fill the stage cards from a run of the packed graph
  await page.waitForTimeout(3000);
  // Stamp what that run proved (each stage's Checks on exactly this graph), so it opens verified.
  const gates = await page.evaluate(async () => {
    const {contractEvidence} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
    return (await contractEvidence(window.comfyAPI.app.app, window.comfyAPI.api.api)).gate_results;
  });
  console.error('gates', JSON.stringify(gates));
  const workflow = await page.evaluate(() => {
    const app = window.comfyAPI.app.app;
    const boxes = app.graph.nodes.map(n => [n.pos[0], n.pos[1] - 30, n.pos[0] + n.size[0], n.pos[1] + n.size[1]]);
    const [x0, y0, x1, y1] = [0, 1, 2, 3].map(i => (i < 2 ? Math.min : Math.max)(...boxes.map(b => b[i])));
    const scale = Math.min(1500 / (x1 - x0 + 80), 900 / (y1 - y0 + 80));
    const graph = app.graph.serialize();
    graph.extra = {...graph.extra, ds: {scale, offset: [140 / scale - x0, 80 / scale - y0]}};
    return graph;
  });
  fs.writeFileSync(target, JSON.stringify(workflow, null, 1) + '\n');
  // Native layers: intent in extra, each step on its stage (+ its sentence), nodes tagged with what they realize.
  const python = process.env.PYTHON || path.join(require('node:os').homedir(), '.local/share/agent-workflow-studio/ComfyUI/.venv/bin/python');
  require('node:child_process').execFileSync(python, [path.join(__dirname, 'native_reference.py'), target], {stdio: 'inherit'});
  console.log(JSON.stringify({second, score: summary, errors, saved: target}));
  await browser.close();
  if (errors.length) process.exit(1);
})().catch(error => { console.error(error); process.exit(1); });
