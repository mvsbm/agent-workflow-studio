// The Design tab: the traceability matrix, read in both directions. With nothing selected, each
// request (the contract) with the stages that serve it (the HLD) and what verifies it; with a stage
// selected, its black box and each part of its step with the nodes realizing it (the LLD); with a
// node selected, why it exists, from the request down. It follows the canvas selection.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {contractEvidence} from './pi_canvas.js';
import {registerWorkflowTab} from './workflow_drawer.js';
import {nodePath, requestLabel, stageView} from './stage_summary.js';

const VERDICT = {proven: '✓ proven by its example', 'checked by its steps': '✓ checked by its stages', 'proven earlier': '◐ proven on an older version', failing: '✗ failing', unproven: '○ not proven yet'};
const el = (tag, text, props = {}) => Object.assign(document.createElement(tag), text == null ? {} : {textContent: text}, props);
const root = () => app.rootGraph ?? app.graph;
const requests = () => root()?.extra?.aws_contract?.requests ?? [];
const stageOf = node => node.properties?.aws_step ? node : root()?.nodes.find(n => n.properties?.aws_stage && n.subgraph === node.graph);

function stageBox(stage) {
  const view = stageView(stage, requests()), box = el('section', null, {className: 'aw-design-box'});
  box.append(el('h3', view.title));
  for (const r of view.serves) box.append(el('div', `Serves ${requestLabel(r)}: you said “${r.you_said}”`, {className: 'aw-design-serves'}));
  // The black box as stored on the stage, without the request line shown just above.
  const blackBox = view.description.split('\n').filter(line => !line.startsWith('Serves ')).join('\n');
  if (blackBox) box.append(el('pre', blackBox, {className: 'aw-design-blackbox'}));
  const table = el('table', null, {className: 'aw-design-parts'});
  table.append(el('caption', 'Each part of this step, and the nodes inside that realize it'));
  for (const {part, nodes} of view.parts) {
    const row = el('tr');
    const cell = el('td', nodes.length ? null : '✗ no node realizes this', {className: nodes.length ? '' : 'aw-design-missing'});
    cell.append(...nodes.map(name => el('div', name)));
    row.append(el('th', part), cell);
    table.append(row);
  }
  const gate = el('tr');
  gate.append(el('th', 'verified by'), el('td', view.checks.length ? `${view.checks.join(', ')} — ${view.gate}` : (view.gate ? `you confirm: ${view.gate}` : '✗ nothing verifies it')));
  table.append(gate);
  box.append(table);
  if (view.extra.length) box.append(el('div', `+ not in the design: ${view.extra.join(', ')}`, {className: 'aw-design-extra'}));
  return box;
}

function nodeBox(node, stage) {
  const box = el('section', null, {className: 'aw-design-box'});
  const [serves, inStage, self] = nodePath(node, stage, requests());
  box.append(el('h3', node.title ?? node.type));
  const path = el('dl', null, {className: 'aw-design-path'});
  for (const [term, value] of [['Serves', serves], ['In stage', inStage], ['This node', self]]) path.append(el('dt', term), el('dd', value));
  box.append(path);
  box.append(el('small', `Node type ${node.type}: the Info tab explains what it does.`));
  return box;
}

async function matrix(container) {
  const graph = root(), base = `/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}`;
  const status = await api.fetchApi(`${base}/contract/status`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(await contractEvidence(app, api))}).then(r => r.json());
  const stages = Object.fromEntries(graph.nodes.filter(n => n.properties?.aws_stage).map(n => [n.properties.aws_stage, n]));
  const table = el('table', null, {className: 'aw-design-matrix'});
  table.append(el('caption', 'What you asked for → the stages that serve it → what verifies it. Click a request to see its stages.'));
  const head = el('tr');
  for (const h of ['Request', 'Served by', 'Verified by', 'Status']) head.append(el('th', h));
  table.append(head);
  for (const r of status.trace?.requests ?? []) {
    const row = el('tr', null, {className: 'aw-design-row', title: `You said: “${r.you_said}”`});
    const serving = r.steps.map(s => stages[s.id]).filter(Boolean);
    row.append(el('td', requestLabel(r)), el('td', serving.map(s => s.title).join(', ') || '✗ no stage'),
      el('td', [...r.examples.map(e => `example ${e.id}`), ...serving.length ? [`the Checks of ${serving.length} stage${serving.length > 1 ? 's' : ''}`] : []].join(' · ') || '✗ nothing'),
      el('td', VERDICT[r.verdict] ?? r.verdict));
    // Selecting the stages on the canvas: this tab then shows each of them.
    row.addEventListener('click', () => { app.canvas.deselectAll(); app.canvas.selectNodes(serving); app.canvas.setDirty(true, true); });
    table.append(row);
  }
  container.replaceChildren(table);
}

async function render(container) {
  const selected = Object.values(app.canvas?.selected_nodes ?? {});
  if (!root()?.extra?.aws_contract) return container.replaceChildren(el('p', 'No contract yet: ask the agent to plan this workflow. Its requests, stages and nodes are traced here once they exist.'));
  if (!selected.length) return matrix(container).catch(error => container.replaceChildren(el('p', `Could not read the design: ${error.message}`)));
  const boxes = selected.map(node => {
    const stage = stageOf(node);
    if (!stage) return el('p', `${node.title ?? node.type} is not part of a stage.`);
    return node === stage ? stageBox(stage) : nodeBox(node, stage);
  });
  container.replaceChildren(...boxes);
}

app.registerExtension({
  name: 'AgentWorkflowStudio.DesignPanel',
  setup() {
    const style = document.createElement('style');
    style.textContent = `
      .aw-design-matrix, .aw-design-parts { width: 100%; border-collapse: collapse; font-size: 13px; }
      .aw-design-matrix caption, .aw-design-parts caption { text-align: left; opacity: .7; font-size: 12px; padding: 4px 0; }
      .aw-design-matrix th, .aw-design-matrix td, .aw-design-parts th, .aw-design-parts td { text-align: left; vertical-align: top; padding: 5px 6px; border-bottom: 1px solid var(--border-color, #333); }
      .aw-design-parts th { white-space: nowrap; font-weight: 600; width: 1%; }
      .aw-design-row { cursor: pointer; } .aw-design-row:hover { background: var(--comfy-input-bg, #2a2a2a); }
      .aw-design-box { padding: 8px 4px 14px; } .aw-design-box h3 { margin: 0 0 6px; font-size: 15px; }
      .aw-design-serves { color: #8fb4e3; font-size: 13px; margin-bottom: 4px; }
      .aw-design-blackbox { white-space: pre-wrap; font-family: inherit; font-size: 12px; opacity: .85; margin: 6px 0; }
      .aw-design-missing { color: #e08080; } .aw-design-extra { color: #d9a441; font-size: 12px; margin-top: 6px; }
      .aw-design-path { font-size: 13px; line-height: 1.4; margin: 0 0 8px; } .aw-design-path dt { opacity: .6; font-size: 11px; margin-top: 6px; } .aw-design-path dd { margin: 0; }`;
    document.head.append(style);
    registerWorkflowTab({order: 0, id: 'workflow-design', icon: 'pi pi-share-alt', title: 'Design', selection: true,
      tooltip: 'Design · which request each stage serves, and which part of its stage each node realizes', render});
  },
});
