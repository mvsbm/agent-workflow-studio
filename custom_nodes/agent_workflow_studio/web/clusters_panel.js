// Clusters tab: why are these records one entity? For the latest Cluster guard run (a record_clusters
// dataset): each cluster's records side by side, the chain of decisions that joined them (stage, rule
// or score, reason) and the merges refused into it (look-alikes kept out). It speaks the workflow's
// own word for them (the stage port typed RECORD_CLUSTERS: customers, in nayose). Read-only.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {iconButton, registerWorkflowTab, searchBox, section} from './workflow_drawer.js';
import {servesOf} from './stage_summary.js';
import {currentType} from './standards_core.js';

const PAGE = 40;
const rootGraph = () => app.rootGraph ?? app.graph;
// What this workflow calls its clusters: the name of its stage port of type RECORD_CLUSTERS.
const plural = () => {
  for (const node of rootGraph()?.nodes ?? []) {
    for (const [port, type] of Object.entries(node.properties?.aws_step?.gives ?? {})) if (currentType(type) === 'RECORD_CLUSTERS') return port.replace(/_/g, ' ');
  }
  return 'clusters';
};
const one = () => plural().replace(/s$/, '');

function el(tag, text, props = {}) {
  const node = Object.assign(document.createElement(tag), props);
  if (text != null) node.textContent = text;
  return node;
}

const text = value => (value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value));

// One row per record; a value unlike the cluster's most common one is marked.
function recordTable(records) {
  const fields = [...new Set(records.flatMap(record => Object.keys(record)))].filter(f => f !== 'id');
  const wrap = el('div', null, {className: 'aw-review-scroll'});
  const table = el('table', null, {className: 'aw-table'});
  table.createTHead().insertRow().append(el('th', 'record', {scope: 'col'}), ...fields.map(f => el('th', f, {scope: 'col'})));
  const common = Object.fromEntries(fields.map(field => {
    const counts = new Map();
    for (const record of records) if (text(record[field])) counts.set(text(record[field]), (counts.get(text(record[field])) ?? 0) + 1);
    return [field, [...counts].sort((a, b) => b[1] - a[1])[0]?.[0]];
  }));
  const body = table.createTBody();
  for (const record of records) {
    const row = body.insertRow();
    row.append(el('th', record.id, {scope: 'row'}));
    for (const field of fields) {
      const value = text(record[field]);
      row.append(el('td', value, {className: value && value !== common[field] ? 'aw-diff' : ''}));
    }
  }
  wrap.append(table);
  return wrap;
}

// The decisions, grouped by the stage on the canvas that made them (and the request it serves); each
// line is the pair, then the rule or score band and its evidence.
function steps(title, items, detail) {
  const box = el('div');
  box.append(el('div', `${title} (${items.length})`, {className: 'aw-label'}));
  const byStage = new Map();
  for (const item of items) byStage.set(item.stage ?? '', [...byStage.get(item.stage ?? '') ?? [], item]);
  for (const [stage, group] of byStage) {
    const head = el('div', null, {className: 'aw-cluster-stage'});
    head.append(el('strong', stage || 'Unnamed stage'));
    const serves = servesOf(rootGraph(), stage);
    if (serves) head.append(el('span', serves, {className: 'aw-section-sub'}));
    const list = el('ol', null, {className: 'aw-cluster-steps'});
    for (const item of group) {
      const line = el('li');
      line.append(el('div', `${item.a} ↔ ${item.b}`), el('div', detail(item), {className: 'aw-section-sub'}));
      list.append(line);
    }
    box.append(head, list);
  }
  return box;
}

function clusterCard(id, cluster) {
  const {records, joined_by: joined, kept_apart: kept} = cluster;
  const names = [...new Set(records.map(r => [r.name, r.kana].filter(Boolean).join(' / ')).filter(Boolean))].slice(0, 2).join(' · ');
  const {node, body} = section(`${id} · ${records.length} record${records.length === 1 ? '' : 's'}`,
    {sub: [names, kept.length && `${kept.length} kept apart`].filter(Boolean).join(' · '), open: false});
  node.addEventListener('toggle', () => {
    if (!node.open || body.childElementCount) return;
    body.append(recordTable(records));
    if (records.length > 1) body.append(steps('Joined by', joined, s => [s.branch, s.why].filter(Boolean).join(' · ')));
    else body.append(el('p', `No match joined this record to another: it is a ${one()} on its own.`, {className: 'aw-review-reason'}));
    if (kept.length) body.append(steps('Kept apart', kept, s => `${s.branch ? `${s.branch} said match` : 'Said match'}; refused: ${s.reason}`));
  });
  return node;
}

function createPanel() {
  const root = el('section', null, {className: 'aw-review aw-tab'});
  const refresh = iconButton('pi pi-refresh', 'Refresh');
  const {box, input: search} = searchBox('Search names, ids…', 'Search by name, reading, record or cluster id');
  const bar = el('div', null, {className: 'aw-tab-bar'});
  bar.append(box, refresh);
  const status = el('p', null, {className: 'aw-status aw-review-status', role: 'status'});
  const list = el('div', null, {className: 'aw-review-list'});
  const more = el('button', 'Show more', {type: 'button', className: 'aw-button aw-review-more'});
  root.append(bar, status, list, more);

  let clusters = [], total = 0, timer;

  // The server searches and pages; the tab holds only what it shows.
  async function load(append = false) {
    if (typeof rootGraph()?.id !== 'string') {
      status.textContent = 'Open a workflow to see its clusters.';
      return list.replaceChildren();
    }
    try {
      const params = new URLSearchParams({q: search.value.trim(), offset: append ? clusters.length : 0, limit: PAGE});
      const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(rootGraph().id)}/clusters?${params}`);
      const body = await response.json();
      if (!response.ok) throw Error(body.error ?? `Could not load the ${plural()}.`);
      if (!body.clusters) {
        status.textContent = `No ${plural()} yet: run the workflow. Its Cluster guard step builds them, and this tab shows why records were joined and what was kept apart.`;
        clusters = [];
        return list.replaceChildren();
      }
      const page = Object.entries(body.clusters);
      clusters = append ? [...clusters, ...page] : page;
      total = body.matches;
      // Largest first: big clusters are where a wrong merge hides.
      status.textContent = `${body.total.toLocaleString()} ${body.total === 1 ? one() : plural()} · largest first` + (search.value.trim() ? ` · ${total.toLocaleString()} match` : '');
      status.title = `Latest run: ${body.run}`;
      if (!append) list.replaceChildren();
      list.append(...page.map(([id, c]) => clusterCard(id, c)));
      if (!clusters.length) list.append(el('p', `No ${one()} matches.`, {className: 'aw-empty'}));
      more.hidden = clusters.length >= total;
    } catch (error) {
      status.textContent = error.message;
    }
  }

  refresh.addEventListener('click', () => load());
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => load(), 250); });
  more.addEventListener('click', () => load(true));
  root.load = () => load();
  return root;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Clusters',
  setup() {
    let panel;
    api.addEventListener('execution_success', () => panel?.isConnected && panel.load());
    registerWorkflowTab({
      order: 5,
      id: 'workflow-clusters',
      icon: 'pi pi-id-card',
      title: 'Clusters',
      tooltip: 'Clusters · why records were joined into one entity (a record_clusters dataset), and what was kept apart',
      type: 'custom',
      render: container => {
        panel ??= createPanel();
        container.replaceChildren(panel);
        panel.load();
      },
    });
  },
});
