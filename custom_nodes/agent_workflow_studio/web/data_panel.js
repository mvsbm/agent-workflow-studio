// Data tab: the workflow's data model (ERD) — the entities agreed in the design (description, key,
// references), the fields actually seen flowing on the last run, and which stage gives and takes
// each one. A declared field the run did not carry is marked; so is data no entity names.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {observedFields} from './pi_canvas.js';

const rootGraph = () => app.rootGraph ?? app.graph;
const el = (tag, text, props = {}) => Object.assign(document.createElement(tag), {textContent: text ?? '', ...props});

function flows(graph) {
  // entity (port name) -> who gives it (with the fields seen) and who takes it
  const out = {};
  for (const stage of graph.nodes.filter(n => n.properties?.aws_stage)) {
    for (const port of stage.subgraph.outputs ?? []) (out[port.name] ??= {gives: [], takes: []}).gives.push({stage: stage.title, type: port.type, seen: observedFields(stage.subgraph, port.name)});
    for (const port of stage.subgraph.inputs ?? []) (out[port.name] ??= {gives: [], takes: []}).takes.push(stage.title);
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

function createPanel() {
  const root = el('section', null, {className: 'aw-data'});
  const header = el('header');
  const refresh = el('button', 'Refresh', {type: 'button', className: 'p-button p-component p-button-sm'});
  header.append(el('h2', 'Data'), refresh);
  const status = el('p', null, {role: 'status'});
  const list = el('div', null, {className: 'aw-data-list'});
  root.append(header, status, list);
  root.load = async () => {
    const graph = rootGraph();
    if (typeof graph?.id !== 'string') return void (status.textContent = 'Open a workflow to see its data.');
    const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/contract`);
    const declared = (response.ok ? (await response.json()).contract?.data : null) ?? {};
    const flow = flows(graph);
    // Entities first (as designed), then other data that flows between stages without a name.
    const names = [...Object.keys(declared), ...Object.keys(flow).filter(n => !declared[n] && flow[n].gives.length && flow[n].takes.length)];
    status.textContent = names.length ? `${Object.keys(declared).length} entities in the design · fields as seen on the last run` : 'No data model yet: it comes from the design the agent proposes.';
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
      .aw-data-desc { opacity: 0.85; } .aw-data-warn { color: #d9822b; }
      .aw-data-fields { display: flex; flex-wrap: wrap; gap: 4px 10px; font-family: monospace; font-size: 12px; }`));
    let panel;
    api.addEventListener('execution_success', () => panel?.isConnected && panel.load());
    app.extensionManager.sidebarTab.registerSidebarTab({
      id: 'workflow-data', icon: 'pi pi-sitemap', title: 'Data',
      tooltip: 'Data · the entities this workflow passes between its stages (ERD), as designed and as seen',
      type: 'custom',
      render: container => { panel ??= createPanel(); container.replaceChildren(panel); panel.load(); },
    });
  },
});
