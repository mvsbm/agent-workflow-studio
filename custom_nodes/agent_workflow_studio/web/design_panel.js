// The Design tab: the traceability matrix, read in both directions. With nothing selected, each
// request (the contract) with the stages that serve it (the HLD) and what verifies it; with a stage
// selected, its black box and each part of its step with the nodes realizing it (the LLD); with a
// node selected, why it exists, from the request down. It follows the canvas selection.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {contractEvidence} from './pi_canvas.js';
import {registerWorkflowTab} from './workflow_drawer.js';
import {TOOL_NODE, nodePath, requestLabel, stageView} from './stage_summary.js';

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
  if (node.type === TOOL_NODE) codeOf(node, box).catch(error => box.append(el('p', `Could not read the tool: ${error.message}`)));
  else box.append(el('small', `Node type ${node.type}: the Info tab explains what it does.`));
  return box;
}

// Workflow code: the tool this node runs, as approved: why it is code, its typed ports, its tests
// (with their results in the latest Run checks) and its exact source.
async function codeOf(node, box) {
  const name = node.widgets?.find(w => w.name === 'tool')?.value;
  if (!name) return box.append(el('small', 'Workflow code: no tool set on this node yet.'));
  const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(root().id)}/tools/${encodeURIComponent(name)}`);
  const tool = await response.json();
  if (!response.ok) return box.append(el('p', `Tool ${name}: ${tool.error}`, {className: 'aw-design-missing'}));
  const {manifest: m, approved, tests} = tool;
  const when = approved ? ` · approved in version ${approved.sha.slice(0, 8)}, ${new Date(approved.time * 1000).toLocaleDateString()}` : '';
  box.append(el('div', `Workflow code · tool ${name} · runs sandboxed${when}`, {className: 'aw-design-code-tier'}));
  if (m.description) box.append(el('p', m.description, {className: 'aw-design-code-text'}));
  if (m.why_not_a_node) box.append(el('p', `Why code, not a node: ${m.why_not_a_node}`, {className: 'aw-design-code-text'}));
  const ports = el('table', null, {className: 'aw-design-parts'});
  for (const [side, list] of [['takes', m.inputs], ['gives', m.outputs]]) {
    const row = el('tr');
    row.append(el('th', side), el('td', (list ?? []).map(p => `${p.name} (${p.kind})`).join(', ') || 'nothing'));
    ports.append(row);
  }
  const verdict = !m.examples?.length ? '✗ no examples: propose it again with examples'
    : !tests ? `${m.examples.length} example(s), not run yet: Run checks runs them`
    : tests.passed ? `✓ ${m.examples.length} example(s) pass` : `✗ ${tests.problems.join('; ')}`;
  const row = el('tr');
  row.append(el('th', 'tested by'), el('td', verdict, {className: tests && !tests.passed || !m.examples?.length ? 'aw-design-missing' : ''}));
  ports.append(row);
  box.append(ports);
  const source = el('details', null, {className: 'aw-design-code'});
  source.append(el('summary', `Source · ${tool.code.split('\n').length} lines · sha256 ${m.sha256.slice(0, 12)}`), el('pre', tool.code));
  // Graduation: proven workflow code becomes a studio node, reviewed by a person (graduation.py).
  const graduate = el('button', 'Graduation brief', {type: 'button', className: 'aw-button', title: 'Download the brief for turning this tool into a reviewed studio node: its interface, its tests and its code'});
  graduate.addEventListener('click', async () => {
    const {brief} = await (await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(root().id)}/tools/${encodeURIComponent(name)}/graduation`)).json();
    const link = Object.assign(document.createElement('a'), {href: URL.createObjectURL(new Blob([brief], {type: 'text/markdown'})), download: `graduate-${name}.md`});
    link.click();
    URL.revokeObjectURL(link.href);
  });
  box.append(source, graduate);
}

async function matrix(container, ticket) {
  const graph = root(), base = `/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}`;
  const status = await api.fetchApi(`${base}/contract/status`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(await contractEvidence(app, api))}).then(r => r.json());
  const stages = Object.fromEntries(graph.nodes.filter(n => n.properties?.aws_stage).map(n => [n.properties.aws_stage, n]));
  // One row per request, like a node's section in the Parameters tab: the request and its status,
  // then the stages serving it and what verifies it (labelled by the stylesheet).
  const list = el('div', null, {className: 'aw-design-matrix'});
  for (const r of status.trace?.requests ?? []) {
    const row = el('div', null, {className: 'aw-design-row', title: `You said: “${r.you_said}”`, tabIndex: 0, role: 'button'});
    const serving = r.steps.map(s => stages[s.id]).filter(Boolean);
    row.append(el('strong', requestLabel(r)), el('div', serving.map(s => s.title).join(', ') || '✗ no stage', {className: 'aw-design-served'}),
      el('div', [...r.examples.map(e => `example ${e.id}`), ...serving.length ? [`the Checks of ${serving.length} stage${serving.length > 1 ? 's' : ''}`] : []].join(' · ') || '✗ nothing', {className: 'aw-design-verified'}),
      el('span', VERDICT[r.verdict] ?? r.verdict, {className: `aw-design-verdict aw-verdict-${String(r.verdict).replace(/\W+/g, '-')}`}));
    // Selecting the stages on the canvas: this tab then shows each of them.
    const select = () => { app.canvas.deselectAll(); app.canvas.selectNodes(serving); app.canvas.setDirty(true, true); };
    row.addEventListener('click', select);
    row.addEventListener('keydown', event => (event.key === 'Enter' || event.key === ' ') && (event.preventDefault(), select()));
    list.append(row);
  }
  const intro = el('p', 'What you asked for, the stages that serve it and what verifies it. Select a request to see its stages.', {className: 'aw-status'});
  // Only if nothing newer was shown meanwhile (a selection renders while this waits on the server).
  if (container.awTicket === ticket) container.replaceChildren(intro, list);
}

async function render(container) {
  const ticket = (container.awTicket = (container.awTicket ?? 0) + 1);
  const selected = Object.values(app.canvas?.selected_nodes ?? {});
  if (!root()?.extra?.aws_contract) return container.replaceChildren(el('p', 'No contract yet: ask the agent to plan this workflow. Its requests, stages and nodes are traced here once they exist.'));
  if (!selected.length) return matrix(container, ticket).catch(error => container.replaceChildren(el('p', `Could not read the design: ${error.message}`)));
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
      .aw-design-parts { width: 100%; border-collapse: collapse; font-size: 13px; }
      .aw-design-parts caption { text-align: left; color: var(--muted-foreground, #8a8a8a); font-size: 12px; padding: 4px 0; }
      .aw-design-parts th, .aw-design-parts td { text-align: left; vertical-align: top; padding: 5px 6px; border-bottom: 1px solid var(--interface-stroke, #505053); }
      .aw-design-parts th { white-space: nowrap; font-weight: 600; width: 1%; }
      .aw-design-matrix { display: flex; flex-direction: column; font-size: 14px; }
      .aw-design-row { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; padding: 10px 12px 10px 16px; border-top: 1px solid var(--interface-stroke, #505053); cursor: pointer; }
      .aw-design-row:hover { background: var(--secondary-background-hover, #3c3d42); }
      .aw-design-row:focus-visible { outline: 1px solid var(--primary-background, #0b8ce9); outline-offset: -1px; }
      .aw-design-row > strong { font-weight: 600; }
      .aw-design-served, .aw-design-verified { grid-column: 1; font-size: 12px; color: var(--muted-foreground, #8a8a8a); }
      .aw-design-served::before { content: 'Served by '; } .aw-design-verified::before { content: 'Verified by '; }
      .aw-design-verdict { grid-column: 2; grid-row: 1; font-size: 12px; white-space: nowrap; }
      .aw-verdict-failing { color: color-mix(in srgb, #d04545 70%, var(--base-foreground, #fff)); }
      .aw-verdict-proven, .aw-verdict-checked-by-its-steps { color: color-mix(in srgb, #2f9e5a 70%, var(--base-foreground, #fff)); }
      .aw-design-box { padding: 8px 16px 14px; border-top: 1px solid var(--interface-stroke, #505053); } .aw-design-box h3 { margin: 0 0 6px; font-size: 14px; font-weight: 600; }
      .aw-design-serves { color: color-mix(in srgb, #3b82d6 70%, var(--base-foreground, #fff)); font-size: 13px; margin-bottom: 4px; }
      .aw-design-blackbox { white-space: pre-wrap; font-family: inherit; font-size: 12px; opacity: .85; margin: 6px 0; }
      .aw-design-missing { color: color-mix(in srgb, #d04545 70%, var(--base-foreground, #fff)); } .aw-design-extra { color: color-mix(in srgb, #c98a1a 70%, var(--base-foreground, #fff)); font-size: 12px; margin-top: 6px; }
      .aw-design-code-tier { font-size: 12px; font-weight: 600; color: color-mix(in srgb, #7a52c8 70%, var(--base-foreground, #fff)); margin: 4px 0; }
      .aw-design-code-text { font-size: 13px; margin: 4px 0; }
      .aw-design-code summary { cursor: pointer; font-size: 12px; color: var(--muted-foreground, #8a8a8a); margin-top: 8px; }
      .aw-design-code pre { margin: 6px 0 0; max-height: 360px; overflow: auto; font-size: 12px; padding: 8px; border-radius: 8px; background: var(--secondary-background, #313235); }
      .aw-design-path { font-size: 13px; line-height: 1.4; margin: 0 0 8px; } .aw-design-path dt { opacity: .6; font-size: 11px; margin-top: 6px; } .aw-design-path dd { margin: 0; }`;
    document.head.append(style);
    registerWorkflowTab({order: 0, id: 'workflow-design', icon: 'pi pi-share-alt', title: 'Design', selection: true,
      tooltip: 'Design · which request each stage serves, and which part of its stage each node realizes', render});
  },
});
