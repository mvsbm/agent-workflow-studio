// Data tab: the workflow's data model (ERD) — the entities agreed in the design (description, key,
// references), the fields actually seen flowing on the last run, and which stage gives and takes
// each one. A declared field the run did not carry is marked; so is data no entity names.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {registerWorkflowTab} from './workflow_drawer.js';
import {observedFields} from './pi_canvas.js';

const rootGraph = () => app.rootGraph ?? app.graph;
const el = (tag, text, props = {}) => Object.assign(document.createElement(tag), {textContent: text ?? '', ...props});

function flows(graph) {
  // entity (port name) -> who gives it (with the fields seen) and who takes it
  const out = {};
  for (const stage of graph.nodes.filter(n => n.properties?.aws_stage)) {
    for (const port of stage.subgraph.outputs ?? []) (out[port.name] ??= {gives: [], takes: []}).gives.push({stage: stage.title, type: port.type, seen: observedFields(stage.subgraph, port.name)});
    // An input numbered for several sources (matched_1, matched_2, …) takes that entity.
    for (const port of stage.subgraph.inputs ?? []) {
      const entity = port.name.replace(/_\d+$/, '');
      const takers = (out[entity] ??= {gives: [], takes: []}).takes;
      if (!takers.includes(stage.title)) takers.push(stage.title);
    }
  }
  return out;
}

function entityCard(name, declared, flow) {
  const card = el('article', null, {className: 'aw-data-entity'});
  const key = declared?.key?.length ? ` · key ${declared.key.join(', ')}` : '';
  card.append(el('strong', `${name}${key}`), el('div', declared?.description ?? 'not named in the design', {className: declared ? 'aw-data-desc' : 'aw-data-warn'}));
  const seen = new Set(flow?.gives.flatMap(g => g.seen ?? []) ?? []);
  const fields = [...new Set([...(declared?.fields ?? []), ...seen])];
  if (fields.length) {
    const list = el('div', null, {className: 'aw-data-fields'});
    for (const field of fields) {
      const ref = declared?.refs?.[field];
      const state = !seen.size ? '' : !seen.has(field) ? ' ✗ not seen' : declared?.fields?.length && !declared.fields.includes(field) ? ' +' : '';
      list.append(el('span', `${field}${ref ? ` → ${ref}` : ''}${state}`, {className: state.includes('✗') ? 'aw-data-warn' : ''}));
    }
    card.append(list);
  }
  if (flow) card.append(el('small', `given by ${flow.gives.map(g => `${g.stage} (${g.type})`).join(', ') || '—'} · taken by ${flow.takes.join(', ') || '—'}`));
  return card;
}

function nodeAt(graph, id) {
  // "12:5" is node 5 inside stage 12 (as the server names nodes inside subgraphs)
  const path = String(id).split(':');
  let node = graph.getNodeById?.(Number(path[0]));
  const stage = node;
  for (const part of path.slice(1)) node = node?.subgraph?.getNodeById?.(Number(part));
  return node && {node, stage: path.length > 1 ? stage : null};
}

/** Follow a record: each step that had it, and only the values that changed since the step
 * before it in the same stage (lanes run side by side, so other stages are not "before"). */
async function followRecord(query, out) {
  const response = await api.fetchApi(`/agent-workflow-studio/follow?q=${encodeURIComponent(query)}`);
  if (!response.ok) return void out.replaceChildren(el('p', 'Could not search the last run.'));
  const steps = (await response.json()).steps.map(s => ({...s, ...nodeAt(rootGraph(), s.node)})).filter(s => s.node?.title);
  if (!steps.some(s => s.count)) return void out.replaceChildren(el('p', steps.length ? `"${query}" is in no step of the last run.` : 'Run the workflow first.'));
  // Stage by stage (lanes run interleaved), in the order each stage first ran.
  const lane = s => s.stage?.id ?? 'top';
  const order = [...new Set(steps.map(lane))];
  steps.sort((a, b) => order.indexOf(lane(a)) - order.indexOf(lane(b)));
  const previous = {}, found = new Set(), seen = {};  // stage -> its step before; stages that had it; record identity -> its last row
  const identity = row => JSON.stringify(Object.values(row).slice(0, 2));  // e.g. system + record id
  const items = [];
  for (const s of steps) {
    const where = s.stage ? `${s.stage.title} › ${s.node.title}` : s.node.title;
    const prior = previous[lane(s)];
    previous[lane(s)] = s;
    if (!s.count) {
      if (!found.has(lane(s))) continue;  // not reached yet in this stage (or written differently until cleaned)
      // A drop only where the rows keep their shape; otherwise the rows are something else (pairs, ids).
      const sameShape = prior.count && prior.columns.every(c => s.columns.includes(c));
      items.push(sameShape
        ? el('li', `${where}: not here (dropped, merged or filtered out)`, {className: 'aw-data-warn'})
        : el('li', `${where}: not found in its rows (${s.columns.slice(0, 4).join(', ')}${s.columns.length > 4 ? ', …' : ''})`, {className: 'aw-data-desc'}));
      continue;
    }
    found.add(lane(s));
    const row = s.rows[0];
    const entries = Object.entries(row);
    const before = seen[identity(row)];
    const shown = before
      ? entries.filter(([k, v]) => String(before[k] ?? '') !== String(v ?? ''))
      : entries.filter(([k], i) => i < 2 || s.matched.includes(k));  // first sight: its ids and where it matched
    const more = before ? 0 : entries.length - shown.length;
    seen[identity(row)] = row;
    if (before && !shown.length) { items.push(el('li', `${s.node.title} · no change`, {className: 'aw-data-desc'})); continue; }
    const item = el('li');
    item.append(el('strong', `${where} · ${s.count.toLocaleString()} ${s.table.includes('pair') ? 'pairs' : s.count === 1 ? 'row' : 'rows'}`));
    item.append(el('div', (shown.length ? shown.map(([k, v]) => `${k}: ${v ?? ''}`).join(' · ') : 'no change') + (more ? ` · +${more} more` : ''), {className: 'aw-data-fields'}));
    items.push(item);
  }
  out.replaceChildren(el('ol', null, {className: 'aw-data-follow'}));
  out.firstChild.append(...items);
}

function createPanel() {
  const root = el('section', null, {className: 'aw-data'});
  const header = el('header');
  const refresh = el('button', 'Refresh', {type: 'button', className: 'p-button p-component p-button-sm'});
  header.append(el('h2', 'Data'), refresh);
  const status = el('p', null, {role: 'status'});
  const list = el('div', null, {className: 'aw-data-list'});
  const follow = el('form', null, {className: 'aw-data-search'});
  const query = el('input', null, {type: 'search', placeholder: 'Follow a record: an id, name or phone', ariaLabel: 'Follow a record through the last run'});
  const trail = el('div');
  follow.append(query, el('button', 'Follow', {type: 'submit', className: 'p-button p-component p-button-sm'}));
  follow.addEventListener('submit', event => { event.preventDefault(); if (query.value.trim()) followRecord(query.value.trim(), trail); });
  root.append(header, follow, trail, status, list);
  root.load = async () => {
    const graph = rootGraph();
    if (typeof graph?.id !== 'string') return void (status.textContent = 'Open a workflow to see its data.');
    const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/contract`);
    const declared = (response.ok ? (await response.json()).contract?.data : null) ?? {};
    const flow = flows(graph);
    // Entities first (as designed), then other data that flows between stages without a name.
    const names = [...Object.keys(declared), ...Object.keys(flow).filter(n => !declared[n] && flow[n].gives.length && flow[n].takes.length)];
    status.textContent = names.length ? `${Object.keys(declared).length} entities in the design · fields as seen on the last run` : 'No data model: the entities passed between steps come from the contract\'s design. Ask the agent in the chat to plan this workflow.';
    list.replaceChildren(...names.map(name => entityCard(name, declared[name], flow[name])));
  };
  refresh.addEventListener('click', () => root.load());
  return root;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Data',
  setup() {
    document.head.append(el('style', `.aw-data { padding: 8px; display: flex; flex-direction: column; gap: 8px; }
      .aw-data header { display: flex; justify-content: space-between; align-items: center; }
      .aw-data-list { display: flex; flex-direction: column; gap: 8px; }
      .aw-data-entity { border: 1px solid var(--border-color, #555); border-radius: 6px; padding: 6px 8px; display: flex; flex-direction: column; gap: 4px; }
      .aw-data-search { display: flex; gap: 6px; } .aw-data-search input { flex: 1; min-width: 0; }
      .aw-data-follow { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
      .aw-data-desc { opacity: 0.85; } .aw-data-warn { color: #d9822b; }
      .aw-data-fields { display: flex; flex-wrap: wrap; gap: 4px 10px; font-family: monospace; font-size: 12px; }`));
    let panel;
    api.addEventListener('execution_success', () => panel?.isConnected && panel.load());
    registerWorkflowTab({
      order: 3,
      id: 'workflow-data', icon: 'pi pi-sitemap', title: 'Data',
      tooltip: 'Data · the entities this workflow passes between its stages (ERD), as designed and as seen',
      type: 'custom',
      render: container => { panel ??= createPanel(); container.replaceChildren(panel); panel.load(); },
    });
  },
});
