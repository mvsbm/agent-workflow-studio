// Review tab: check what an agent tool matched or grouped. A tool writes review.json in its run
// folder: `pairs` to decide (same / different) and `groups` to check (one person or not). Your
// decisions are versioned as the user's (labels/review.json) and reach later tool runs as
// inputs["review_labels"], so the next run can learn from and be measured against them.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {iconButton, registerWorkflowTab, searchBox, section} from './workflow_drawer.js';
import {servesOf} from './stage_summary.js';

const CHANGED = 'agent-workflow-studio:versions-changed';
const PAGE = 40;
const PAIR_CHOICES = [['same', 'Same'], ['different', 'Different'], ['unsure', 'Not sure']];
const GROUP_CHOICES = [['correct', 'All one person'], ['wrong', 'Has someone else']];
const rootGraph = () => app.rootGraph ?? app.graph;

function el(tag, text, props = {}) {
  const node = Object.assign(document.createElement(tag), props);
  if (text != null) node.textContent = text;
  return node;
}

async function request(path, options) {
  const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(rootGraph().id)}${path}`, options);
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'Review request failed.');
  return body;
}

const text = value => (value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value));

// Records side by side: one column per record, one row per field; differing values are marked.
function compareTable(records, headings) {
  const fields = [...new Set(records.flatMap(record => Object.keys(record)))];
  const table = el('table', null, {className: 'aw-table'});
  const head = table.createTHead().insertRow();
  head.append(el('th', ''), ...headings.map(h => el('th', h, {scope: 'col'})));
  const body = table.createTBody();
  for (const field of fields) {
    const values = records.map(record => text(record[field]));
    const filled = values.filter(Boolean);
    const differs = new Set(filled).size > 1;
    const row = body.insertRow();
    row.append(el('th', field, {scope: 'row'}));
    for (const value of values) row.append(el('td', value, {className: differs && value ? 'aw-diff' : ''}));
  }
  return table;
}

// Many records: one row each, fields as columns (scrolls sideways); values unlike the rest are marked.
function recordTable(records, onOutlier, isOutlier) {
  // Names first; the record id is unique by design, so it goes last and is never marked.
  const fields = [...new Set(records.flatMap(record => Object.keys(record)))].filter(f => f !== 'id');
  if (records.some(record => record.id != null)) fields.push('id');
  const wrap = el('div', null, {className: 'aw-review-scroll'});
  const table = el('table', null, {className: 'aw-table'});
  const head = table.createTHead().insertRow();
  head.append(el('th', 'Not this person', {scope: 'col'}), ...fields.map(f => el('th', f, {scope: 'col'})));
  const common = Object.fromEntries(fields.map(field => {
    const counts = new Map();
    for (const record of records) if (text(record[field])) counts.set(text(record[field]), (counts.get(text(record[field])) ?? 0) + 1);
    return [field, [...counts].sort((a, b) => b[1] - a[1])[0]?.[0]];
  }));
  const body = table.createTBody();
  records.forEach((record, i) => {
    const row = body.insertRow();
    const box = el('input', null, {type: 'checkbox', checked: isOutlier(record, i)});
    box.setAttribute('aria-label', `Record ${i + 1} is not this person`);
    box.addEventListener('change', () => onOutlier(record, i, box.checked));
    const cell = el('td');
    cell.append(box, ` ${i + 1}`);
    row.append(cell);
    for (const field of fields) {
      const value = text(record[field]);
      row.append(el('td', value, {className: field !== 'id' && value && value !== common[field] ? 'aw-diff' : ''}));
    }
  });
  wrap.append(table);
  return wrap;
}

function choices(options, current, onPick, label) {
  const group = el('div', null, {className: 'aw-review-choices', role: 'group'});
  group.setAttribute('aria-label', label);
  for (const [value, name] of options) {
    const button = el('button', name, {type: 'button', className: 'aw-button'});
    button.setAttribute('aria-pressed', String(current === value));
    button.addEventListener('click', () => onPick(current === value ? null : value));
    group.append(button);
  }
  return group;
}

function createPanel() {
  const root = el('section', null, {className: 'aw-review aw-tab'});
  const refresh = iconButton('pi pi-refresh', 'Refresh');
  const {box, input: search} = searchBox('Search names, ids…', 'Search review items');
  const bar = el('div', null, {className: 'aw-tab-bar'});
  bar.append(box, refresh);
  const tabs = el('div', null, {className: 'aw-tab-bar', role: 'group'});
  tabs.setAttribute('aria-label', 'What to review');
  const open = el('label', null, {className: 'aw-review-open'});
  const openOnly = el('input', null, {type: 'checkbox', checked: true});
  open.append(openOnly, 'Undecided only');
  const status = el('p', null, {className: 'aw-status aw-review-status', role: 'status'});
  const list = el('div', null, {className: 'aw-review-list'});
  const more = el('button', 'Show more', {type: 'button', className: 'aw-button aw-review-more'});
  root.append(bar, tabs, status, list, more);

  let review = null, labels = {}, run = '', mode = 'pairs', shown = PAGE, pending = {}, timer = null;

  const items = () => (mode === 'pairs' ? review?.pairs : review?.groups) ?? [];
  const decided = kind => (review?.[kind] ?? []).filter(item => labels[item.id]).length;

  function summary(note = '') {
    const parts = [];
    if (review?.pairs?.length) parts.push(`Pairs: ${decided('pairs')} of ${review.pairs.length} decided`);
    if (review?.groups?.length) parts.push(`Customers: ${decided('groups')} of ${review.groups.length} checked`);
    // A review merged from several decision stages is titled by its sections, shown as they are.
    status.textContent = [review?.sections?.length ? '' : review?.title, ...parts, note].filter(Boolean).join(' · ');
    status.title = `Latest run: ${run}`;
  }

  function decide(id, value) {
    if (value) labels[id] = value; else delete labels[id];
    pending[id] = value;
    summary('Saving…');
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const batch = pending;
      pending = {};
      try {
        const {sha} = await request('/review/labels', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({run, labels: batch})});
        summary(`Saved · version ${sha.slice(0, 8)}`);
        window.dispatchEvent(new Event(CHANGED));
      } catch (error) {
        summary(`Not saved: ${error.message}`);
      }
    }, 500);
  }

  function pairCard(pair) {
    const card = el('article', null, {className: 'aw-review-card'});
    const meta = [pair.id, pair.score != null && `score ${pair.score}`, pair.suggestion && `suggests ${pair.suggestion}`].filter(Boolean).join(' · ');
    card.append(el('div', meta, {className: 'aw-review-meta'}));
    if (pair.branch && !String(pair.reason ?? '').startsWith('Spot check')) card.append(el('div', `Left to you by: ${pair.branch}`, {className: 'aw-review-why'}));
    if (pair.reason) card.append(el('div', pair.reason, {className: 'aw-review-reason'}));
    card.append(compareTable([pair.a ?? {}, pair.b ?? {}], ['A', 'B']));
    card.append(choices(PAIR_CHOICES, labels[pair.id], value => { decide(pair.id, value); render(); }, `Is pair ${pair.id} the same person?`));
    return card;
  }

  function groupCard(group) {
    const records = group.records ?? [];
    const card = el('details', null, {className: 'aw-review-card'});
    const verdict = labels[group.id] ? ` · ${labels[group.id]}` : '';
    card.append(el('summary', `${group.id} · ${records.length} records${group.label ? ` · ${group.label}` : ''}${verdict}`));
    card.addEventListener('toggle', () => {
      if (!card.open || card.querySelector('table')) return;
      if (group.note) card.append(el('div', group.note, {className: 'aw-review-reason'}));
      const recordId = (record, i) => `${group.id}/${record.id ?? i + 1}`;
      card.append(recordTable(records, (record, i, checked) => decide(recordId(record, i), checked ? 'not_this_person' : null),
        (record, i) => labels[recordId(record, i)] === 'not_this_person'));
      card.append(choices(GROUP_CHOICES, labels[group.id], value => { decide(group.id, value); card.querySelector('summary').textContent = `${group.id} · ${records.length} records${group.label ? ` · ${group.label}` : ''}${value ? ` · ${value}` : ''}`; }, `Is ${group.id} one person?`));
    });
    return card;
  }

  function render() {
    tabs.replaceChildren(...[['pairs', 'Candidate pairs'], ['groups', 'Matched customers']].filter(([kind]) => review?.[kind]?.length).map(([kind, name]) => {
      const button = el('button', `${name} · ${review[kind].length.toLocaleString()}`, {type: 'button', className: 'aw-pill'});
      button.setAttribute('aria-pressed', String(mode === kind));
      button.addEventListener('click', () => { mode = kind; shown = PAGE; render(); });
      return button;
    }), open);
    const query = search.value.trim().toLowerCase();
    const visible = items().filter(item => (!openOnly.checked || !labels[item.id]) && (!query || JSON.stringify(item).toLowerCase().includes(query)));
    // One section per decision stage that sent them, titled as on the canvas, with the request it serves.
    const key = stage => stage?.id || stage?.title || '';  // reviews from before stage ids carry only the title
    const groups = new Map();
    for (const item of visible) groups.set(key(item.stage), [...groups.get(key(item.stage)) ?? [], item]);
    let left = shown;
    list.replaceChildren(...[...groups.values()].flatMap(group => {
      if (left <= 0) return [];
      const cards = group.slice(0, left).map(item => (mode === 'pairs' ? pairCard(item) : groupCard(item)));
      left -= cards.length;
      const stage = group[0].stage;
      if (!stage) return cards;
      const {node, body} = section(stage.title || stage.id, {sub: servesOf(rootGraph(), stage), count: group.length});
      node.classList.add('aw-review-stage');
      body.append(...cards);
      return [node];
    }));
    if (!visible.length) list.append(el('p', review ? 'Nothing left to review here.' : '', {className: 'aw-empty'}));
    more.hidden = visible.length <= shown;
    summary();
  }

  async function load() {
    if (typeof rootGraph()?.id !== 'string') {
      status.textContent = 'Open a workflow to review its results.';
      return list.replaceChildren();
    }
    try {
      ({review, labels, run} = await request('/review'));
      labels ??= {};
      if (!review) {
        status.textContent = 'Nothing to review yet: run the workflow. Pairs a step could not decide (Decision rules, Your review decisions, or an agent tool writing review.json) and spot checks of its automatic decisions come here for you to decide.';
        tabs.replaceChildren();
        more.hidden = true;
        return list.replaceChildren();
      }
      if (!review[mode]?.length) mode = review.pairs?.length ? 'pairs' : 'groups';
      render();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  refresh.addEventListener('click', load);
  search.addEventListener('input', () => { shown = PAGE; render(); });
  openOnly.addEventListener('change', () => { shown = PAGE; render(); });
  more.addEventListener('click', () => { shown += PAGE; render(); });
  root.load = load;
  return root;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Review',
  setup() {
    document.head.append(el('link', null, {rel: 'stylesheet', href: new URL('./review_panel.css', import.meta.url).href}));
    let panel;
    api.addEventListener('execution_success', () => panel?.isConnected && panel.load());
    registerWorkflowTab({
      order: 4,
      id: 'workflow-review',
      icon: 'pi pi-users',
      title: 'Review',
      tooltip: 'Review · check matched results and decide uncertain candidates',
      type: 'custom',
      render: container => {
        panel ??= createPanel();
        container.replaceChildren(panel);
        panel.load();
      },
    });
  },
});
