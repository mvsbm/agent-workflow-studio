// Contract tab: the workflow's approved contract with live progress. Step states come from
// evidence (the stages on the canvas + their Checks on the latest run of exactly this graph), derived on
// the server by the same transition table the agent's status tool uses.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {contractEvidence} from './pi_canvas.js';

const CHANGED = 'agent-workflow-studio:versions-changed';
const STATE = {
  planned: ['Planned', '#5a6b80'], built: ['Built', '#4a6fa5'], fuzzy: ['Fuzzy · AI-planned', '#7a52a8'],
  verified: ['Verified', '#2f8a4a'], failing: ['Failing', '#b04040'],
};
const rootGraph = () => app.rootGraph ?? app.graph;

function el(tag, text, props = {}) {
  const node = Object.assign(document.createElement(tag), props);
  if (text != null) node.textContent = text;
  return node;
}

async function request(path, options) {
  const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(rootGraph().id)}${path}`, options);
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'Contract request failed.');
  return body;
}

function createPanel() {
  const root = el('section', null, {className: 'aw-contract'});
  const status = el('p', null, {className: 'aw-contract-status', role: 'status'});
  const body = el('div');
  const refresh = el('button', 'Refresh', {type: 'button', className: 'p-button p-component p-button-sm'});
  const header = el('header');
  header.append(el('h2', 'Contract'), refresh);
  root.append(header, status, body);

  async function load() {
    if (typeof rootGraph()?.id !== 'string') {
      status.textContent = 'Open a workflow to see its contract.';
      return body.replaceChildren();
    }
    try {
      const {contract} = await request('/contract');
      if (!contract) {
        status.textContent = 'No contract yet. Ask the agent in the Pi chat to plan this workflow; you approve the contract before anything is built.';
        return body.replaceChildren();
      }
      const evidence = await contractEvidence(app, api);
      const result = await request('/contract/status', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(evidence)});
      status.textContent = `${result.aligned ? '✓ Aligned with the contract' : `⚠ ${result.drift.length} drift(s) from the contract`} · ${result.progress} · ${result.freshness}`;
      const steps = el('ol', null, {className: 'aw-contract-steps'});
      for (const step of result.steps) {
        const [label, color] = STATE[step.state];
        const item = el('li');
        const chip = el('span', label, {className: 'aw-contract-chip'});
        chip.style.background = color;
        item.append(chip, el('strong', step.purpose), el('small', step.next));
        steps.append(item);
      }
      const parts = [];
      if (result.drift.length) {
        const drift = el('ul', null, {className: 'aw-contract-warning'});
        for (const item of result.drift) drift.append(el('li', item));
        parts.push(drift);
      }
      parts.push(steps);
      if (result.todo?.length) {
        const todo = el('details');
        todo.append(el('summary', `To do (${result.todo.length})`), ...result.todo.map(item => el('div', item, {className: 'aw-contract-todo'})));
        parts.push(todo);
      }
      const full = el('details', null, {open: true});
      full.append(el('summary', 'Full contract'), el('pre', result.text));
      parts.push(full);
      body.replaceChildren(...parts);
    } catch (error) {
      status.textContent = error.message;
    }
  }

  refresh.addEventListener('click', load);
  window.addEventListener(CHANGED, load);
  root.load = load;
  return root;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Contract',
  setup() {
    document.head.append(el('link', null, {rel: 'stylesheet', href: new URL('./contract_panel.css', import.meta.url).href}));
    let panel;
    for (const event of ['execution_success', 'execution_error']) api.addEventListener(event, () => panel?.isConnected && panel.load());
    app.extensionManager.sidebarTab.registerSidebarTab({
      id: 'workflow-contract',
      icon: 'pi pi-check-square',
      title: 'Contract',
      tooltip: 'Contract · what this workflow promises, and where each step stands',
      type: 'custom',
      render: container => {
        panel ??= createPanel();
        container.replaceChildren(panel);
        panel.load();
      },
    });
  },
});
