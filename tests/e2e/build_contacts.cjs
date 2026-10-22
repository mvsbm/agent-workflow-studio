// Builds the contact-matching templates in an isolated ComfyUI: the measured pipeline (research/contacts_er)
// as visible stages, run, packed into stage cards with their gates, its key settings on the cards and in
// App mode, each published as one command of the exported program:
//   Studio - Contact matching            your exports → entities, review queue        (contacts-nayose)
//   Studio - Contact matching benchmark  generate → match (simulated reviewer) → score (contacts-nayose-benchmark)
//   Studio - Contact data generator      generated contacts with an answer key        (contacts-nayose-generate)
// Usage: scripts/build_templates.sh
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const folder = path.join(__dirname, '..', '..', 'custom_nodes', 'agent_workflow_studio', 'example_workflows');
const PROFILE = process.env.PROFILE || 'company_hard', SIZE = Number(process.env.SIZE || 10000), SEED = Number(process.env.SEED || 7);
const DATA = `contacts_benchmark/${PROFILE}_${SIZE}_${SEED}`;
const MATCHING = [
  ['read', 'Read the exports'], ['standardize', 'Standardize'], ['find_candidates', 'Find candidates'],
  ['compare', 'Compare'], ['learn', 'Learn from your reviews'], ['decide', 'Decide'],
  ['group', 'Group into entities'], ['publish', 'Publish'],
];
const STEP_LIST = {
  production: MATCHING,
  benchmark: [['generate', 'Generate contacts'], ...MATCHING, ['score', 'Score']],
  generator: [['generate', 'Generate contacts']],
};
const STEPS = variant => STEP_LIST[variant].map(([id, title], i) => ({id, title: `${i + 1} · ${title}`}));
const FILES = {
  production: 'Studio - Contact matching.json',
  benchmark: 'Studio - Contact matching benchmark.json',
  generator: 'Studio - Contact data generator.json',
};
// Each gate: an expected count on the default data (company_hard, 10,000 contacts, seed 7), measured, with room.
const at = (output, kind, value) => ({output, kind, value});
const GENERATE = {id: 'generate', purpose: 'Generate CRM-shaped contacts (contacts, phones and addresses on CONT_ID) with realistic dirt, and their answer key.', approach: 'fixed', covers: ['R5'], gives: {folder: 'STRING', answer_key: 'STRING'}, uses: 'Synthetic contacts', why: 'a measurement needs data whose answers are known', pattern: 'Synthetic data with ground truth', gate: {says: 'The data folder is written (with its answer key beside it).', check: {output: 'folder', kind: 'contains', value: 'contacts_benchmark'}}};
const SCORE = {id: 'score', purpose: 'Score the entities against the answer key: precision, recall and F1, totals only.', approach: 'fixed', covers: ['R5'], takes: {entities: 'publish.entities', answer_key: 'generate.answer_key'}, uses: 'Score contacts', why: 'the result is measured, not assumed', pattern: 'Evaluation against ground truth', gate: {says: 'The score is shown, totals only (nothing flows on, so no step can tune itself to the answer key).'}};
const STEP_SPECS = (gates, variant) => variant === 'generator' ? [{...GENERATE, covers: ['R1']}] : [
  ...(variant === 'benchmark' ? [GENERATE] : []),
  {id: 'read', purpose: 'Read your contact exports through the column map: one contact per key, phones and addresses attached.', approach: 'fixed', covers: ['R1', 'R4'], takes: {folder: variant === 'benchmark' ? 'generate.folder' : 'input.exports'}, gives: {contacts: 'CONTACTS'}, uses: 'Read contacts', why: 'every CRM names its columns its own way', pattern: 'Canonical data model', gate: {says: gates.read.says, check: at('contacts', 'rows_at_least', gates.read.value)}},
  {id: 'standardize', purpose: 'Make names, phones and addresses comparable.', approach: 'fixed', covers: ['R1'], takes: {contacts: 'read.contacts'}, gives: {records: 'CONTACT_RECORDS'}, uses: 'Standardize contacts', why: 'the same name is typed many ways', pattern: 'Standardization', gate: {says: gates.standardize.says, check: at('records', 'rows_at_least', gates.standardize.value)}},
  {id: 'find_candidates', purpose: 'Pair the contacts worth comparing.', approach: 'fixed', covers: ['R1', 'R2'], takes: {records: 'standardize.records'}, gives: {candidates: 'CANDIDATE_PAIRS'}, uses: 'Find contact candidates', why: 'comparing every pair is too slow', pattern: 'Blocking', gate: {says: gates.find_candidates.says, check: at('candidates', 'rows_at_least', gates.find_candidates.value)}},
  {id: 'compare', purpose: 'Measure how alike the two contacts of each pair are.', approach: 'fixed', covers: ['R1'], takes: {records: 'standardize.records', candidates: 'find_candidates.candidates'}, gives: {pairs: 'RECORD_PAIRS'}, uses: 'Compare contacts', why: 'a model needs measured evidence', pattern: 'Comparison vector', gate: {says: gates.compare.says, check: at('pairs', 'rows_at_least', gates.compare.value)}},
  {id: 'learn', purpose: 'Score every pair from your reviews, and ask you about the pairs it is least sure of.', approach: 'fixed', covers: ['R3'], takes: variant === 'benchmark' ? {pairs: 'compare.pairs', answer_key_file: 'generate.answer_key'} : {pairs: 'compare.pairs'}, gives: {scored: 'RECORD_PAIRS'}, uses: 'Learn from your reviews', why: 'your data, not ours, decides the weights', pattern: 'Active learning', gate: {says: gates.learn.says, check: at('scored', 'rows_at_least', gates.learn.value)}},
  {id: 'decide', purpose: 'Decide each pair: match, to review, or different.', approach: 'fixed', covers: ['R1', 'R3'], takes: {pairs: 'learn.scored'}, gives: variant === 'production' ? {decisions: 'PAIR_DECISIONS', queue: 'PAIR_DECISIONS'} : {decisions: 'PAIR_DECISIONS'}, uses: 'Decide by score', why: 'the business sets precision against recall', pattern: 'Threshold policy', gate: {says: gates.decide.says, check: at('decisions', 'rows_at_least', gates.decide.value)}},
  {id: 'group', purpose: 'Group matched contacts into entities, refusing merges decided different.', approach: 'fixed', covers: ['R1', 'R2'], takes: {data: 'decide.decisions'}, gives: {clusters: 'RECORD_CLUSTERS'}, uses: 'Cluster guard', why: 'one wrong link must not chain many', pattern: 'Guarded clustering', gate: {says: gates.group.says, check: at('clusters', 'rows_at_least', gates.group.value)}},
  {id: 'publish', purpose: 'Give every contact its entity id.', approach: 'fixed', covers: ['R1'], takes: {records: 'read.contacts', clusters: 'group.clusters'}, gives: {entities: 'RECORD_CLUSTERS'}, uses: 'Entity per contact', why: 'every contact needs an id, merged or not', pattern: 'Publish', gate: {says: gates.publish.says, check: at('entities', 'rows_at_least', gates.publish.value)}},
  ...(variant === 'benchmark' ? [SCORE] : []),
];
// Every variant runs on any data or size: its gates say each stage gave data, never a count of ours.
const ANY_DATA = {
  read: {says: 'The exports are read: at least one contact (10,540 on the benchmark data).', value: 1},
  standardize: {says: 'Every contact read is standardized.', value: 1},
  find_candidates: {says: 'Candidate pairs are found (15,219 on the benchmark data).', value: 1},
  compare: {says: 'Every candidate pair is compared.', value: 1},
  learn: {says: 'Every compared pair gets a score.', value: 1},
  decide: {says: 'Every scored pair is decided.', value: 1},
  group: {says: 'The decided pairs are grouped.', value: 1},
  publish: {says: 'Every contact gets an entity id.', value: 1},
};
const REQUESTS = [
  {id: 'R1', title: 'One entity id per contact', you_said: 'several CONT_IDs can be one person or company; match them with the other columns', we_understood: 'When the exports are read, the workflow gives every contact an entity id, the same id for contacts of the same person or company.', priority: 'must'},
  {id: 'R2', title: 'Never merged on a phone alone', you_said: 'a family shares a phone and an address', we_understood: 'When two contacts share only a phone or an address, the workflow compares them but never merges them on that alone.', priority: 'must'},
  {id: 'R3', title: 'You review uncertain pairs', you_said: 'uncertain pairs go to human review', we_understood: 'When a pair is uncertain, the workflow asks you in the Review tab, and learns from your answers.', priority: 'must'},
  {id: 'R4', title: 'Any export with this shape', you_said: 'the pipeline should work on any data that matches the shape', we_understood: 'When the exports name their columns differently, a column map says which column holds what.', priority: 'should'},
];
const MEASURED = {id: 'R5', title: 'Measured on generated data', you_said: 'we need that data generator', we_understood: 'When the benchmark runs, the workflow generates contacts with an answer key, matches them as Contact matching does with a reviewer who knows the answers, and scores the result.', priority: 'must'};
const GENERATED = {id: 'R1', title: 'Contacts with an answer key', you_said: 'we need that data generator', we_understood: 'When run, the workflow writes CRM-shaped contacts, phones and addresses on CONT_ID with realistic dirt, and the answer key beside them.', priority: 'must'};
const REQUESTS_OF = {production: REQUESTS, benchmark: [...REQUESTS, MEASURED], generator: [GENERATED]};
const DECISIONS = [
  {id: 'D1', decision: 'A model learns the weights from your reviews (logistic regression, active learning).', why: 'it beat fixed rules, Fellegi–Sunter and gradient boosting in every trial', instead_of: 'hand-set points', by: 'agent', pattern: 'Active learning'},
  {id: 'D2', decision: 'No embeddings or reranker.', why: 'neither improved the result at 100,000 contacts (research/contacts_er/RESULTS.md)', instead_of: 'a dense index and a cross-encoder', by: 'agent', principle: 'every part earns its place'},
  {id: 'D3', decision: 'Uncertain pairs and disputed reviews go to you.', why: 'a wrong merge costs more than a question', instead_of: 'auto-deciding everything', by: 'agent', pattern: 'Human in the loop'},
  {id: 'D4', decision: 'Its quality comes from your reviews: about 1,000 make it good. Review a round, run again; a larger round (reviews per round 200) means fewer runs.', why: 'measured on the company_hard 10k synthetic benchmark: no reviews F1 0.54 (precision 0.85, recall 0.39); 1,000 reviews F1 0.96 (precision 0.98, recall 0.93)', instead_of: 'fixed weights tuned on synthetic data, which would not fit yours', by: 'agent', principle: 'learn from the data it runs on'},
];
const DATA_MODEL = {
  contacts: {description: 'One contact per key, as exported, with its phones and addresses', key: ['key']},
  records: {description: 'The contacts standardized for matching'},
  candidates: {description: 'Pairs of contacts worth comparing, and the passes that found them', key: ['left', 'right']},
  pairs: {description: 'Compared pairs: how alike each pair is, measured'},
  scored: {description: 'Each pair with how likely it is the same entity'},
  decisions: {description: 'Each pair decided: match, to review, or different'},
  queue: {description: 'The pairs for people to decide'},
  clusters: {description: 'Groups of matched contacts, and the merges refused'},
  entities: {description: 'Every contact with its entity id', key: ['record'], fields: ['record', 'cluster']},
};
const SAMPLE = {
  'sample/contacts.csv': `CONT_ID,LAST_NAME_KANJI,FIRST_NAME_KANJI,LAST_NAME_KANA,FIRST_NAME_KANA,LAST_NAME_ROMAJI,FIRST_NAME_ROMAJI,BIRTH_DATE,GENDER,CONT_TYPE
EX01,山田,太郎,ヤマダ,タロウ,YAMADA,TAROU,1980-01-02,M,1
EX02,山田,太郎,ﾔﾏﾀﾞ,ﾀﾛｳ,YAMADA,TAROU,1980-01-02,M,1
EX03,髙橋,花子,タカハシ,ハナコ,TAKAHASHI,HANAKO,1975-05-05,F,1
EX04,高橋,花子,タカハシ,ハナコ,TAKAHASHI,HANAKO,1975-05-05,F,1
EX05,佐藤,次郎,サトウ,ジロウ,SATOU,JIROU,1990-03-03,M,1
EX06,佐藤,花子,サトウ,ハナコ,SATOU,HANAKO,1992-07-07,F,1
EX07,株式会社サンプル,,サンプル,,SANPURU,,1999-04-01,,2
EX08,(株)サンプル 東京支店,,カ)サンプル,,KA)SANPURU,,1999-04-01,,2
EX09,鈴木,一郎,スズキ,イチロウ,SUZUKI,ICHIROU,1985-12-12,M,1
EX10,鈴木,一郎,スズキ,イチロウ,SUZUKI,ICHIROU,1960-02-02,M,1
EX11,田中,美咲,タナカ,ミサキ,TANAKA,MISAKI,2000-08-08,F,1
EX12,伊藤,健,イトウ,ケン,ITOU,KEN,1970-10-10,M,1
`,
  'sample/phones.csv': 'CONT_ID,PHONE\nEX01,03-1234-5678\nEX02,0312345678\nEX03,090-1111-2222\nEX04,09011112222\nEX05,045-111-2222\nEX06,045-111-2222\nEX07,03-5555-6666\nEX08,0355556666\nEX09,06-1234-0000\nEX10,06-9876-0000\n',
  'sample/addresses.csv': 'CONT_ID,ADDRESS_KANJI\nEX01,東京都新宿区西新宿2-8-1\nEX02,東京都新宿区西新宿二丁目8番1号\nEX05,神奈川県横浜市港北区日吉1-2-3\nEX06,神奈川県横浜市港北区日吉1-2-3\nEX07,東京都千代田区丸の内1-1-1\nEX08,東京都千代田区丸の内1-1-1\n',
};
// App mode and the API: what a business changes, in its words.
const GEN_KNOBS = [
  ['AgentWorkflowSyntheticContacts', 'profile', 'profile', 'text', 'How dirty. Lifelike and company profiles take names from public lists (downloaded once). All contacts are synthetic.'],
  ['AgentWorkflowSyntheticContacts', 'contacts', 'contacts', 'integer', 'About how many contacts to generate.'],
  ['AgentWorkflowSyntheticContacts', 'seed', 'seed', 'integer', 'The same seed gives the same data.'],
];
const KNOBS_OF = variant => variant === 'production' ? KNOBS : variant === 'benchmark'
  ? {generate: GEN_KNOBS, learn: [['AgentWorkflowLearnFromReviews', 'budget', 'budget', 'integer', 'How many reviews the simulated reviewer makes (about 1,000 for a good model).']]}
  : {generate: GEN_KNOBS};
const KNOBS = {
  read: [['AgentWorkflowReadContacts', 'columns', 'columns', 'text', 'The column map: which column of your exports holds the key, names, birth date, phones and addresses.']],
  learn: [['AgentWorkflowLearnFromReviews', 'batch', 'batch', 'integer', 'How many pairs to ask you about each run.']],
  decide: [
    ['AgentWorkflowDecideByScore', 'match_at', 'match_at', 'number', 'Merge pairs at least this likely to be the same (raise it for fewer wrong merges).'],
    ['AgentWorkflowDecideByScore', 'review_from', 'review_from', 'number', 'Pairs from here up to Match at go to your review queue.'],
    ['AgentWorkflowDecideByScore', 'corroboration', 'corroboration', 'boolean', 'A merge also needs a shared phone, house, town or alias, not only name and birth date.'],
  ],
  group: [['AgentWorkflowClusterGuard', 'max_size', 'max_size', 'integer', 'The most contacts one entity may have.']],
};
const NOTE = {
  benchmark: [
    '# Contact matching benchmark',
    '',
    'Generated CRM contacts (contacts, phones and addresses on CONT_ID) with realistic dirt and an answer key; the same stages as **Studio - Contact matching**, with a reviewer who knows the answers in place of you (1,000 reviews).',
    '',
    '1. Press **Run**. **Score contacts** shows precision, recall and F1 against the answer key, totals only.',
    '2. Change the profile, size, seed or the reviews it makes on the stage cards (or open it as an App). Lifelike and company profiles draw names and companies from public lists, downloaded once: `python -m custom_nodes.agent_workflow_studio.nayose_bench.sources`.',
    '3. The data lands in input/' + DATA + ' (for Studio - Contact matching to read).',
  ],
  generator: [
    '# Contact data generator',
    '',
    'Writes CRM-shaped contacts (contacts.csv, phones.csv, addresses.csv on CONT_ID) with realistic dirt into input/contacts_benchmark/<profile>_<contacts>_<seed>, and the answer key beside them in output/. The same seed gives the same data.',
    '',
    '1. Choose the profile, size and seed, press **Run** (or open it as an App).',
    '2. Lifelike and company profiles draw names, towns and companies from public lists: download them once (`python -m custom_nodes.agent_workflow_studio.nayose_bench.sources`; their licences apply). Every contact is synthetic.',
    '3. Point **Studio - Contact matching** at the folder it wrote; **Studio - Contact matching benchmark** generates and scores in one run.',
  ],
  production: [
    '# Contact matching: one entity id per contact',
    '',
    '1. On **Input · exports**, press **Upload data files** and pick your contacts, phones and addresses CSV files (or choose a folder in ComfyUI\'s input folder). On **1 · Read the exports**, say in the **Column map** which column holds what.',
    '2. Press **Run**. With no reviews yet, pairs are scored by their agreement patterns; the **Review** tab asks you about the pairs the model is least sure of. Decide them, run again: each round it learns from all your reviews. About 1,000 reviews is enough.',
    '3. **Match at** on **6 · Decide** sets how sure a merge must be. The results: **entities** (every contact with its entity id) and **review_queue** (the pairs for people to decide).',
    '4. To try it on generated data: run **Studio - Contact matching benchmark** first (it writes ' + DATA + ').',
    '5. **Export as Python** (Contract tab) turns it into a command-line program for your own machines.',
  ],
};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1700, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') console.error('page', m.type(), m.text().slice(0, 300)); });
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  await page.goto(base);
  await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
  const run = () => page.evaluate(() => new Promise(resolve => {
    const api = window.comfyAPI.api.api;
    api.addEventListener('execution_success', () => resolve('success'), {once: true});
    api.addEventListener('execution_error', e => resolve(`error: ${e.detail?.node_type}: ${e.detail?.exception_message}`), {once: true});
    void window.comfyAPI.app.app.queuePrompt(0, 1);
  }));
  const python = process.env.PYTHON || path.join(require('node:os').homedir(), '.local/share/agent-workflow-studio/ComfyUI/.venv/bin/python');
  const saved = {};
  for (const variant of ['benchmark', 'production', 'generator']) {
    const knobs = KNOBS_OF(variant);
    const examples = {
      production: [{id: 'E1', covers: ['R1', 'R2'], given: {exports: 'sample'}, files: SAMPLE, expect: {entities: 'EX01 EX01, EX02 EX01, EX03 EX03, EX04 EX03, EX05 EX05, EX06 EX06, EX07 EX07, EX08 EX08, EX09 EX09, EX10 EX10'}, note: 'Day 0, no reviews: EX01/02 and EX03/04 merge; a family (EX05/06) and namesakes (EX09/10) stay apart. The company pair EX07/08 merges once reviews teach the model.'}],
      benchmark: [{id: 'E1', covers: ['R1', 'R5'], given: {profile: 'hard', contacts: 600, seed: 5, budget: 200}, expect: {entities: 'record'}, note: 'A small hard set (no downloads needed): every contact gets an entity id and the score is shown.'}],
      generator: [{id: 'E1', covers: ['R1'], given: {profile: 'hard', contacts: 300, seed: 3}, expect: {folder: 'contacts_benchmark/hard_300_3'}, note: 'A small hard set (no downloads needed).'}],
    }[variant];
    const contract = {
      contract: 2,
      title: {production: 'Contact matching', benchmark: 'Contact matching benchmark', generator: 'Contact data generator'}[variant],
      purpose: {
        production: 'Give every contact one entity id: contacts of the same person or company share it, and uncertain pairs go to you.',
        benchmark: 'Measure contact matching on generated contacts with an answer key: the same stages as Contact matching, with a reviewer who knows the answers.',
        generator: 'Generate CRM-shaped contacts with realistic dirt and their answer key, to try or measure contact matching.',
      }[variant],
      requests: REQUESTS_OF[variant],
      examples,
      promises: {
        inputs: [
          ...(variant === 'production' ? [{name: 'exports', kind: 'folder', description: 'The folder with your contact exports (contacts, phones and addresses CSV files).', example: DATA}] : []),
          ...Object.values(knobs).flat().map(([, , name, kind, description]) => ({name, kind, description})),
        ],
        outputs: {
          production: [
            {name: 'entities', kind: 'file', description: 'Every contact with its entity id (record, cluster).', from: 'publish.entities'},
            {name: 'review_queue', kind: 'file', description: 'The pairs for people to decide.', from: 'decide.queue'},
          ],
          benchmark: [{name: 'entities', kind: 'file', description: 'Every generated contact with the entity id the matching gave it.', from: 'publish.entities'}],
          generator: [
            {name: 'folder', kind: 'text', description: 'The data folder written (contacts, phones and addresses CSV files).', from: 'generate.folder'},
            {name: 'answer_key', kind: 'text', description: 'The answer key file: which contacts are one entity.', from: 'generate.answer_key'},
          ],
        }[variant],
      },
      steps: STEP_SPECS(ANY_DATA, variant),
      decisions: variant === 'generator' ? [] : DECISIONS,
      not_now: variant === 'generator' ? [] : ['Embeddings or a reranker (measured: no gain)', 'A registry lookup for merged or renamed companies'],
      open_questions: [],
      data: variant === 'generator' ? {} : DATA_MODEL,
    };
    await page.evaluate(async ({variant, data, profile, size, seed}) => {
      const app = window.comfyAPI.app.app, LiteGraph = window.LiteGraph;
      await app.loadGraphData({id: crypto.randomUUID(), revision: 0, last_node_id: 0, last_link_id: 0, nodes: [], links: [], groups: [], config: {}, extra: {}, version: 0.4});
      const graph = app.graph;
      const add = (type, step, title, widgets = {}) => {
        const node = LiteGraph.createNode(type);
        graph.add(node);
        if (title) node.title = title;
        if (step) node.properties = {...node.properties, aws_contract_step: step};
        for (const [name, value] of Object.entries(widgets)) node.widgets.find(w => w.name === name).value = value;
        return node;
      };
      const slot = (node, name) => node.inputs.findIndex(i => i.name === name || i.widget?.name === name);
      const link = (from, output, to, input) => from.connect(output, to, slot(to, input));
      const answer = (name, from, node, output) => {
        const out = add('AgentWorkflowApiOutput', null, `Output · ${name}`, {name});
        out.properties = {...out.properties, aws_output: name, aws_output_from: from};
        node.connect(output, out, 0);
      };
      let source;
      if (variant === 'production') {
        source = add('AgentWorkflowDataFolder', null, 'Input · exports', {folder: data});
        source.properties = {...source.properties, aws_input: 'exports'};
      } else {
        source = add('AgentWorkflowSyntheticContacts', 'generate', 'Synthetic contacts', {profile, contacts: size, seed});
      }
      if (variant === 'generator') {
        answer('folder', 'generate.folder', source, 0);
        answer('answer_key', 'generate.answer_key', source, 1);
        return;
      }
      const read = add('AgentWorkflowReadContacts', 'read', 'Read contacts');
      link(source, 0, read, 'folder');
      const std = add('AgentWorkflowStandardizeContacts', 'standardize', 'Standardize contacts');
      link(read, 0, std, 'contacts');
      const find = add('AgentWorkflowFindContactCandidates', 'find_candidates', 'Find contact candidates');
      link(std, 0, find, 'records');
      const compare = add('AgentWorkflowCompareContacts', 'compare', 'Compare contacts');
      link(std, 0, compare, 'records');
      link(find, 0, compare, 'candidates');
      const learn = add('AgentWorkflowLearnFromReviews', 'learn', 'Learn from your reviews');
      link(compare, 0, learn, 'pairs');
      if (variant === 'benchmark') link(source, 1, learn, 'answer_key_file');
      const decide = add('AgentWorkflowDecideByScore', 'decide', 'Decide by score');
      link(learn, 0, decide, 'pairs');
      const guard = add('AgentWorkflowClusterGuard', 'group', 'Group into entities (guarded)', {max_size: 12, scores_separate: true});
      link(decide, 0, guard, 'data');
      const entity = add('AgentWorkflowEntityPerContact', 'publish', 'Entity per contact');
      link(read, 0, entity, 'records');
      link(guard, 0, entity, 'clusters');
      answer('entities', 'publish.entities', entity, 0);
      if (variant === 'production') answer('review_queue', 'decide.queue', decide, 1);
      if (variant === 'benchmark') {
        const score = add('AgentWorkflowScoreContacts', 'score', 'Score contacts');
        link(entity, 0, score, 'entities');
        link(source, 1, score, 'answer_key');
      }
    }, {variant, data: DATA, profile: PROFILE, size: SIZE, seed: SEED});
    const first = await run();
    if (first !== 'success') throw Error(`${variant} first run: ${first}`);
    const facts = await page.evaluate(() => Object.fromEntries(window.comfyAPI.app.app.graph.nodes.filter(n => n.properties?.aws_contract_step).map(n => [n.properties.aws_contract_step + ' · ' + n.title, n.properties.aws_headline])));
    console.error(variant, JSON.stringify(facts, null, 1));
    await page.evaluate(async ({steps, contract, knobs, note, variant}) => {
      const app = window.comfyAPI.app.app, LiteGraph = window.LiteGraph;
      const {packIntoStage, addCheck, arrangeGraph, promoteWidget} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const {setSignature} = await import('/extensions/agent_workflow_studio/publish.js');
      for (const {id, title} of steps) {
        const members = app.graph.nodes.filter(n => n.properties?.aws_contract_step === id);
        for (const n of members) delete n.properties.aws_contract_step;
        const step = contract.steps.find(s => s.id === id);
        const stage = packIntoStage(app.graph, members, id, Number(/^\s*(\d+)/.exec(title)[1]), step.gives);
        stage.title = stage.subgraph.name = title;
        if (step.gate.check) addCheck(stage.subgraph, step.gate.check, step.gate.says);
      }
      const inputs = [];
      for (const [id, list] of Object.entries(knobs)) {
        const stage = app.graph.nodes.find(n => n.properties?.aws_stage === id);
        for (const [type, widget, , , description] of list) {
          const node = stage.subgraph.nodes.find(n => n.type === type);
          const label = promoteWidget(stage, node, widget) ?? widget;
          inputs.push({node: stage.id, widget: label, description});
          Object.assign(contract.promises.inputs.find(i => i.name === label), {node: stage.id, widget: label});
        }
      }
      const outputs = contract.promises.outputs.map(p => app.graph.nodes.find(n => n.properties?.aws_output === p.name).id);
      if (variant === 'production') {
        const exports = app.graph.nodes.find(n => n.properties?.aws_input === 'exports');
        Object.assign(contract.promises.inputs.find(i => i.name === 'exports'), {node: exports.id, widget: 'folder'});
        inputs.unshift({node: exports.id, widget: 'folder', description: contract.promises.inputs[0].description});
      }
      setSignature(app.graph, inputs, outputs);
      const md = LiteGraph.createNode('MarkdownNote');
      app.graph.add(md);
      md.title = 'How to use this workflow';
      md.size = [460, 340];
      md.widgets[0].value = note.join('\n');
      arrangeGraph(app.graph);
      app.graph.extra = {...app.graph.extra, aws_contract: contract};
    }, {steps: STEPS(variant), contract, knobs, note: NOTE[variant], variant});
    const second = await run();
    if (second !== 'success') throw Error(`${variant} second run: ${second}`);
    await page.waitForTimeout(3000);
    const gates = await page.evaluate(async () => {
      const {contractEvidence} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      return (await contractEvidence(window.comfyAPI.app.app, window.comfyAPI.api.api)).gate_results;
    });
    console.error(variant, 'gates', JSON.stringify(gates));
    const after = await page.evaluate(() => Object.fromEntries(window.comfyAPI.app.app.graph.nodes.flatMap(s => (s.subgraph?.nodes ?? []).map(n => [s.title + ' · ' + n.title, n.properties?.aws_headline]))));
    console.error(variant, 'after packing', JSON.stringify(after, null, 1));
    const score = await page.evaluate(() => window.comfyAPI.app.app.graph.nodes.find(n => n.type === 'AgentWorkflowScoreContacts')?.properties?.aws_tables?.[0]?.rows ?? null);
    if (score) console.error(variant, 'score', JSON.stringify(score));
    const workflow = await page.evaluate(() => {
      const app = window.comfyAPI.app.app;
      const boxes = app.graph.nodes.map(n => [n.pos[0], n.pos[1] - 30, n.pos[0] + n.size[0], n.pos[1] + n.size[1]]);
      const [x0, y0, x1, y1] = [0, 1, 2, 3].map(i => (i < 2 ? Math.min : Math.max)(...boxes.map(b => b[i])));
      const scale = Math.min(1500 / (x1 - x0 + 80), 900 / (y1 - y0 + 80));
      const graph = app.graph.serialize();
      graph.extra = {...graph.extra, ds: {scale, offset: [140 / scale - x0, 80 / scale - y0]}};
      return graph;
    });
    const target = path.join(folder, FILES[variant]);
    fs.writeFileSync(target, JSON.stringify(workflow, null, 1) + '\n');
    require('node:child_process').execFileSync(python, [path.join(__dirname, 'native_reference.py'), target], {stdio: 'inherit'});
    saved[variant] = {target, gates, score};
  }
  console.log(JSON.stringify({saved, errors}));
  await browser.close();
  if (errors.length) process.exit(1);
})().catch(error => { console.error(error); process.exit(1); });
