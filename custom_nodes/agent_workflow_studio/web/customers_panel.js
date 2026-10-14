// Customers tab: why are these records one customer? For the latest Cluster guard run: each
// customer's records side by side, the chain of decisions that joined them (stage, rule or score,
// reason) and the merges refused into it (look-alikes kept out). Read-only; Review is for deciding.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';

const PAGE = 40;
const rootGraph = () => app.rootGraph ?? app.graph;

function el(tag, text, props = {}) {
  const node = Object.assign(document.createElement(tag), props);
  if (text != null) node.textContent = text;
  return node;
}

const text = value => (value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value));

// One row per record; a value unlike the customer's most common one is marked.
function recordTable(records) {
  const fields = [...new Set(records.flatMap(record => Object.keys(record)))].filter(f => f !== 'id');
  const wrap = el('div', null, {className: 'aw-review-scroll'});
  const table = el('table', null, {className: 'aw-review-table'});
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
      row.append(el('td', value, {className: value && value !== common[field] ? 'aw-review-diff' : ''}));
    }
  }
  wrap.append(table);
  return wrap;
}

function steps(title, items, line) {
  const section = el('section');
  section.append(el('strong', `${title} (${items.length})`));
  const list = el('ol', null, {className: 'aw-customer-steps'});
  for (const item of items) list.append(el('li', line(item)));
  section.append(list);
  return section;
}

const decidedBy = step => [step.stage, step.branch].filter(Boolean).join(': ');

function customerCard(id, customer) {
  const {records, joined_by: joined, kept_apart: kept} = customer;
  const card = el('details', null, {className: 'aw-review-card'});
  const names = [...new Set(records.map(r => [r.name, r.kana].filter(Boolean).join(' / ')).filter(Boolean))].slice(0, 2).join(' · ');
  card.append(el('summary', `${id} · ${records.length} record${records.length === 1 ? '' : 's'}${names ? ` · ${names}` : ''}${kept.length ? ` · ${kept.length} kept apart` : ''}`));
  card.addEventListener('toggle', () => {
    if (!card.open || card.querySelector('table')) return;
    card.append(recordTable(records));
    if (records.length > 1) card.append(steps('Joined by', joined, s => `${s.a} ↔ ${s.b} — ${decidedBy(s)}${s.why ? ` (${s.why})` : ''}`));
    else card.append(el('p', 'No match joined this record to another: it is a customer on its own.', {className: 'aw-review-reason'}));
    if (kept.length) card.append(steps('Kept apart', kept, s => `${s.a} ↔ ${s.b} — ${decidedBy(s)} said match, refused: ${s.reason}`));
  });
  return card;
}

function createPanel() {
  const root = el('section', null, {className: 'aw-review'});
  const refresh = el('button', 'Refresh', {type: 'button', className: 'p-button p-component p-button-sm'});
  const header = el('header');
  header.append(el('h2', 'Customers'), refresh);
  const status = el('p', null, {className: 'aw-review-status', role: 'status'});
  const search = el('input', null, {type: 'search', placeholder: 'Search names, readings, record or customer ids…', className: 'p-inputtext p-component'});
  search.setAttribute('aria-label', 'Search customers');
  const tools = el('div', null, {className: 'aw-review-tools'});
  tools.append(search);
  const list = el('div', null, {className: 'aw-review-list'});
  const more = el('button', 'Show more', {type: 'button', className: 'p-button p-component p-button-sm p-button-text'});
  root.append(header, status, tools, list, more);

  let customers = [], shown = PAGE;

  function render() {
    const query = search.value.trim().toLowerCase();
    const visible = query ? customers.filter(([id, c]) => id.toLowerCase().includes(query) || JSON.stringify(c.records).toLowerCase().includes(query)) : customers;
    list.replaceChildren(...visible.slice(0, shown).map(([id, c]) => customerCard(id, c)));
    if (!visible.length) list.append(el('p', customers.length ? 'No customer matches.' : '', {className: 'aw-review-empty'}));
    more.hidden = visible.length <= shown;
  }

  async function load() {
    if (typeof rootGraph()?.id !== 'string') {
      status.textContent = 'Open a workflow to see its customers.';
      return list.replaceChildren();
    }
    try {
      const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(rootGraph().id)}/customers`);
      const body = await response.json();
      if (!response.ok) throw Error(body.error ?? 'Could not load customers.');
      // Largest first: big customers are where a wrong merge hides.
      customers = Object.entries(body.customers ?? {}).sort((a, b) => b[1].records.length - a[1].records.length);
      status.textContent = body.customers
        ? `${customers.length.toLocaleString()} customers · run ${body.run} · largest first`
        : 'No customers yet. Run a workflow with a Cluster guard node.';
      render();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  refresh.addEventListener('click', load);
  search.addEventListener('input', () => { shown = PAGE; render(); });
  more.addEventListener('click', () => { shown += PAGE; render(); });
  root.load = load;
  return root;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Customers',
  setup() {
    const style = el('style', '.aw-customer-steps { margin: 4px 0 8px; padding-left: 20px; font-size: 12px; display: flex; flex-direction: column; gap: 3px; }');
    document.head.append(style);
    let panel;
    api.addEventListener('execution_success', () => panel?.isConnected && panel.load());
    app.extensionManager.sidebarTab.registerSidebarTab({
      id: 'workflow-customers',
      icon: 'pi pi-id-card',
      title: 'Customers',
      tooltip: 'Customers · why records were joined into one customer, and what was kept apart',
      type: 'custom',
      render: container => {
        panel ??= createPanel();
        container.replaceChildren(panel);
        panel.load();
      },
    });
  },
});
