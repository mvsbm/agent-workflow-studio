// Contract tab: the workflow's approved contract with live progress. Step states come from
// evidence (the stages on the canvas + their Checks on the latest run of exactly this graph), derived on
// the server by the same transition table the agent's status tool uses.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {contractEvidence} from './pi_canvas.js';
import {publishWorkflow, runExamples} from './publish.js';

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

// The workflow as an API: what is published (endpoint, version, a call to copy) and the button to
// publish the current version. The server refuses until the structure and release items hold.
function apiSection() {
  const section = el('section', null, {className: 'aw-contract-api'});
  const info = el('div');
  const name = el('input', null, {type: 'text', placeholder: 'api name, e.g. nayose', className: 'p-inputtext p-component', size: 18});
  name.setAttribute('aria-label', 'API name');
  const publish = el('button', 'Publish as API', {type: 'button', className: 'p-button p-component p-button-sm'});
  const examples = el('button', 'Run examples', {type: 'button', className: 'p-button p-component p-button-sm p-button-text', title: "Run the contract's examples as acceptance tests; publishing needs them to pass"});
  const result = el('p', null, {role: 'status'});
  section.append(el('h3', 'API'), info, name, publish, examples, result);
  examples.addEventListener('click', async () => {
    result.textContent = 'Running the examples…';
    try {
      const done = await runExamples(app, api);
      result.textContent = `Examples: ${done.passed}/${done.total} passed` + done.results.filter(r => !r.passed).map(r => ` · ${r.example}: ${r.misses.join('; ')}`).join('');
    } catch (error) {
      result.textContent = error.message;
    }
  });
  const show = item => {
    const call = `curl -X POST ${location.origin}/agent-workflow-studio/api/${item.name}/run -H 'Content-Type: application/json' -d '{"inputs": {${item.inputs.map(i => `"${i}": …`).join(', ')}}}'`;
    info.replaceChildren(el('div', `Published as ${item.name} · version ${item.version.slice(0, 8)} · ${item.published_at}`),
      el('div', `Inputs: ${item.inputs.join(', ') || 'none'} · outputs: ${item.outputs.join(', ')}`),
      el('code', call, {className: 'aw-contract-call'}),
      Object.assign(el('a', 'OpenAPI description'), {href: `/agent-workflow-studio/api/${item.name}/openapi.json`, target: '_blank'}));
    name.value ||= item.name;
  };
  section.load = async () => {
    const response = await api.fetchApi('/agent-workflow-studio/api');
    const mine = response.ok ? (await response.json()).apis.filter(a => a.workflow_id === rootGraph()?.id) : [];
    if (mine.length) show(mine.at(-1));
    else info.replaceChildren(el('div', 'Not published. Choose its inputs and outputs in App mode (or ask the agent to set the signature), then publish.'));
  };
  publish.addEventListener('click', async () => {
    result.textContent = 'Publishing…';
    try {
      const done = await publishWorkflow(app, api, name.value.trim());
      result.textContent = `Published version ${done.version}: POST ${done.endpoint}` + (done.todo.length ? ` · still to do: ${done.todo.join('; ')}` : '');
      await section.load();
    } catch (error) {
      result.textContent = error.message;
    }
  });
  return section;
}

function createPanel() {
  const root = el('section', null, {className: 'aw-contract'});
  const status = el('p', null, {className: 'aw-contract-status', role: 'status'});
  const body = el('div');
  const refresh = el('button', 'Refresh', {type: 'button', className: 'p-button p-component p-button-sm'});
  const header = el('header');
  header.append(el('h2', 'Contract'), refresh);
  root.append(header, status, body, apiSection());

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

  const apiPart = root.querySelector('.aw-contract-api');
  const loadAll = () => { load(); apiPart.load().catch(() => {}); };
  refresh.addEventListener('click', loadAll);
  window.addEventListener(CHANGED, loadAll);
  root.load = loadAll;
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
