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

const STEPS = [
  ['profile_data', '1 · Profile data'],
  ['policy_system', '2 · Policy system'], ['claims_system', '2 · Claims system'], ['payment_system', '2 · Payment system'],
  ['find_candidates', '3 · Find candidates'], ['deterministic_matching', '4 · Deterministic matching'],
  ['probabilistic_matching', '5 · Probabilistic matching'], ['your_review', '6 · Your review'],
  ['consolidate_customers', '7 · Consolidate customers'], ['publish_results', '8 · Publish results'],
].map(([id, title]) => ({id, title}));

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
  await page.evaluate(async ({template, plans, deterministic, starting, variant, patternRules, seed}) => {
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
    const profile = add('AgentWorkflowProfileData', 'profile_data', '1 · Profile the exports');
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
    const policies = lane('policy_system', 'policies', 'policy_master.csv', [
      ['AgentWorkflowStackRoles', 'One row per person (holder, insured, beneficiary)', {plan: plans.policies}],
      ['AgentWorkflowSplitName', 'Reading into surname and given name', {column: 'kana'}],
      ['AgentWorkflowNormalizeText', 'Mainframe kana and names to standard', {columns: 'name, kana'}],
      ['AgentWorkflowMapValues', 'Variant kanji (〓-free) to standard', {columns: 'name'}],
      ['AgentWorkflowMapValues', 'Sex codes 1/2 to M/F', {columns: 'sex', map: '1:M 2:F', mode: 'whole values'}],
      ['AgentWorkflowParseDates', 'Era dates (S45.03.12, 3450312) to YYYY-MM-DD', {columns: 'birth'}],
      ['AgentWorkflowExtractDigits', 'Phones and postcodes to digits', {columns: 'phone, zip', min_digits: 6}],
    ]);
    const claims = lane('claims_system', 'claims', 'claims.csv', [
      ['AgentWorkflowStackRoles', 'One row per person (claimant, insured)', {plan: plans.claims}],
      ['AgentWorkflowSplitName', 'Reading into surname and given name', {column: 'kana'}],
      ['AgentWorkflowNormalizeText', 'Heard names and hiragana to standard', {columns: 'name, kana'}],
      ['AgentWorkflowMapValues', 'Variant kanji to standard', {columns: 'name'}],
      ['AgentWorkflowMapValues', 'Sex 男/女 to M/F', {columns: 'sex', map: '男:M 女:F', mode: 'whole values'}],
      ['AgentWorkflowParseDates', 'Dates to YYYY-MM-DD', {columns: 'birth'}],
      ['AgentWorkflowExtractDigits', 'Phones and postcodes to digits', {columns: 'phone, zip', min_digits: 6}],
    ]);
    const payments = lane('payment_system', 'payments', 'koza_furikae.csv', [
      ['AgentWorkflowStackRoles', 'One row per account holder', {plan: plans.payments}],
      ['AgentWorkflowSplitName', 'Account name into surname and given name', {column: 'kana'}],
      ['AgentWorkflowNormalizeText', 'Bank-format kana to standard', {columns: 'kana'}],
    ]);
    const merged = add('AgentWorkflowConcatenate', 'find_candidates', 'All mentions (3 systems)');
    [policies, claims, payments].forEach((lane, i) => link(lane, 0, merged, 'abc'[i]));
    const candidates = add('AgentWorkflowFindCandidates', 'find_candidates', 'Candidate pairs (blocking)', {keys: 'kana\npolicy_ref\nphone\nname\naccount\nkana_given+birth', max_group: 40});
    link(merged, 0, candidates, 'table');
    const compare = add('AgentWorkflowCompareFields', 'find_candidates', 'Compare fields', {fields: 'kana: {method: text, similar: 0.88, common: 20}\nkana_surname: {method: text, similar: 0.9}\nkana_given: {method: text, similar: 0.9}\nname: {method: text, similar: 0.85}\nbirth: {method: date}\nphone: {method: same}\naddress: {method: text, similar: 0.8}\npolicy_ref: {method: same}\nsex: {method: same}\n', show: 'name, kana, birth, address, phone, policy_ref'});
    link(merged, 0, compare, 'records');
    link(candidates, 0, compare, 'pairs');
    const certain = add('AgentWorkflowDecisionRules', 'deterministic_matching', 'Certain matches and non-matches', {rules: variant === 'patterns' ? patternRules : deterministic});
    link(compare, 1, certain, 'data');
    const learn = add('AgentWorkflowLearnWeights', 'probabilistic_matching', 'Learn the points', {rules: starting});
    link(certain, 4, learn, 'data'); // only the pairs the certain rules left unsettled go on
    const scored = add('AgentWorkflowDecisionRules', 'probabilistic_matching', 'Score the unsettled pairs');
    link(learn, 0, scored, 'rules');
    link(learn, 1, scored, 'data');
    const yours = add('AgentWorkflowReviewDecisions', 'your_review', 'Your decisions on uncertain pairs');
    link(scored, 4, yours, 'data');
    const guard = add('AgentWorkflowClusterGuard', 'consolidate_customers', 'Build customers (guarded)');
    // Decided pairs leave the cascade at every stage and meet here.
    link(certain, 2, guard, 'matched_1'); link(certain, 3, guard, 'different_1');
    if (!variant || variant === 'cascade') {
      link(scored, 2, guard, 'matched_2'); link(scored, 3, guard, 'different_2');
      link(yours, 0, guard, 'matched_3'); link(yours, 1, guard, 'different_3');
    }
    const attach = add('AgentWorkflowAttachGroups', 'publish_results', 'Customer id for every mention');
    link(merged, 0, attach, 'records');
    link(guard, 0, attach, 'groups');
    const write = add('AgentWorkflowWriteTable', 'publish_results', 'Write nayose_result.csv', {file: 'nayose_reference/nayose_result.csv', columns: 'system, record_id, role, customer_id'});
    link(attach, 0, write, 'table');
    link(write, 0, score, 'result_file');
  }, {template, plans: PLANS, deterministic: DETERMINISTIC, starting: STARTING, variant: VARIANT, patternRules: PATTERNS, seed: Number(process.env.SEED || 0)});
  const first = await run();
  if (first !== 'success') throw Error(`first run: ${first}`);
  if (VARIANT) {
    const rows = await page.evaluate(() => window.comfyAPI.app.app.graph.nodes.find(n => n.type === 'AgentWorkflowNayoseScore').properties.aws_tables);
    console.log(JSON.stringify({variant: VARIANT, score: rows[0].rows, by_damage: rows[1].rows.slice(0, 12), wrong_merges: rows[3]?.rows}));
    await browser.close();
    return;
  }
  const summary = await page.evaluate(async steps => {
    const app = window.comfyAPI.app.app;
    const {packIntoStage, addCheck, arrangeGraph} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
    for (const {id, title} of steps) {
      const members = app.graph.nodes.filter(n => n.properties?.aws_contract_step === id);
      if (!members.length) continue;
      for (const n of members) delete n.properties.aws_contract_step; // the stage now says which step
      const stage = packIntoStage(app.graph, members, id, Number(/^\s*(\d+)/.exec(title)[1]));
      // Each stage proves itself on the data it gives: a Check on its first output.
      const out = stage.subgraph.outputs[0];
      if (out) addCheck(stage.subgraph, {output: out.name, kind: out.type === 'STRING' ? 'non_empty' : 'rows_at_least', value: 1}, `${stage.title} gives data`);
    }
    arrangeGraph(app.graph);
    const score = app.graph.nodes.find(n => n.type === 'AgentWorkflowNayoseScore');
    return score.properties.aws_tables?.[0]?.rows ?? [];
  }, STEPS);
  const second = await run(); // fill the stage cards from a run of the packed graph
  await page.waitForTimeout(3000);
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
  console.log(JSON.stringify({second, score: summary, errors, saved: target}));
  await browser.close();
  if (errors.length) process.exit(1);
})().catch(error => { console.error(error); process.exit(1); });
