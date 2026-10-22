// Contract tab: the workflow's approved contract with live progress. Step states come from
// evidence (the stages on the canvas + their Checks on the latest run of exactly this graph), derived on
// the server by the same transition table the agent's status tool uses.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {iconButton, registerWorkflowTab, section} from './workflow_drawer.js';
import {contractEvidence} from './pi_canvas.js';
import {publishWorkflow, rollbackApi, runChecks} from './publish.js';
import {designPage} from './design_page.js';

const CHANGED = 'agent-workflow-studio:versions-changed';
const STATE = {
  planned: ['Planned', '#5a6b80'], built: ['Built', '#4a6fa5'], fuzzy: ['Fuzzy · AI-planned', '#7a52a8'],
  verified: ['Verified', '#2f8a4a'], failing: ['Failing', '#b04040'],
};
const rootGraph = () => app.rootGraph ?? app.graph;
// Gaps by what they mean for the user, most serious first (progress.py names the severities).
const GAPS = {
  blocking: ['Broken', '#b04040'], unproven: ['Not proven yet', '#a8771c'], yours: ['Waiting for you', '#3f72b5'],
  design: ['Differs from the design', '#7a52a8'], deferred: ['Agreed not now', '#5a6b80'],
};
const MARK = {passed: '✓ passed', failed: '✗ failed', 'passed earlier': 'passed on an older version', 'failed earlier': 'failed on an older version', 'not run': 'not run yet'};
const VERDICT = {proven: ['Proven', '#2f8a4a'], 'checked by its steps': ['Checked by its steps (no example)', '#4a7f5a'], 'proven earlier': ['Proven on an older version', '#a8771c'], failing: ['Failing', '#b04040'], unproven: ['Not proven', '#a8771c']};

function chip(label, color) {
  const node = el('span', label, {className: 'aw-contract-chip'});
  node.style.background = color;
  return node;
}

// Plan vs built vs proven, every gap named (progress.py): nothing here is the agent's own claim.
function gapsSection(trace) {
  const total = trace.gaps.length;
  const {node, body: gaps} = section(total ? `Gaps · ${total}` : 'No gaps',
    {sub: total ? Object.entries(trace.gap_counts).filter(([, n]) => n).map(([k, n]) => `${GAPS[k][0].toLowerCase()} ${n}`).join(' · ') : 'everything agreed is built and proven on this version'});
  node.classList.add('aw-contract-gaps');
  for (const [severity, [label, color]] of Object.entries(GAPS)) {
    const mine = trace.gaps.filter(g => g.severity === severity);
    if (!mine.length) continue;
    const group = el('div', null, {className: 'aw-contract-gap-group'});
    group.append(chip(label, color));
    const list = el('ul');
    for (const g of mine) list.append(el('li', `${g.where}: ${g.text}`));
    group.append(list);
    gaps.append(group);
  }
  return node;
}

function requestsSection(trace) {
  const {node, body} = section('Your requests → proof', {sub: 'each request, what serves it and what proves it'});
  const list = el('ul', null, {className: 'aw-contract-requests'});
  for (const r of trace.requests) {
    const item = el('li');
    const head = el('div');
    head.append(chip(...VERDICT[r.verdict]), el('strong', ` ${r.id} · ${r.title ?? ''}`), el('small', ` ${r.priority ?? ''}`));
    item.append(head, el('div', r.we_understood ?? r.you_said ?? ''));
    if (r.you_said) item.append(el('small', `You said: “${r.you_said}”`));
    item.append(el('small', `Served by: ${r.steps.map(s => `${s.id} (${s.state ?? '?'})`).join(', ') || 'no step'} · Examples: ${r.examples.map(e => `${e.id} ${MARK[e.status]}`).join(', ') || 'none'} (they verify it)`));
    list.append(item);
  }
  body.append(list);
  return node;
}

// One step: what was agreed (contract + design), what is built, what the last run proved.
function stepColumns(step, row) {
  const columns = el('div', null, {className: 'aw-contract-columns'});
  const column = (title, lines) => {
    const box = el('div');
    box.append(el('h4', title));
    for (const [label, value] of lines) if (value != null && value !== '' && !(Array.isArray(value) && !value.length)) box.append(el('div', `${label}: ${Array.isArray(value) ? value.join(', ') : value}`));
    return box;
  };
  const ports = side => Object.entries(row.agreed[side] ?? {}).map(([k, v]) => `${k} ${v}`);
  columns.append(
    column('Agreed', [['Does', row.agreed.purpose], ['Takes', ports('takes')], ['Gives', ports('gives')], ['How', row.agreed.uses], ['Why', row.agreed.why], ['Pattern', row.agreed.pattern], ['Done when', row.agreed.done_when], ['Agreed check', row.agreed.check], ['Designed nodes', row.agreed.nodes]]),
    column('Built', [['Nodes', row.built.nodes.length ? row.built.nodes : 'none yet'], ['Checks', row.built.checks.length ? row.built.checks : 'none'], ['Not in the design', row.built.not_designed], ['AI-planned nodes', row.built.fuzzy || null]]),
    column('Proven', [['State', step.state], ['Check matches the agreement', row.proven.check_matches_agreement ? 'yes' : 'no'], ...row.proven.checks.map(c => [c.title, `${c.passed === true ? '✓' : c.passed === false ? '✗' : '–'} ${c.evidence}`])]),
  );
  return columns;
}

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

// What the Export as Python button asks the agent (it uses comfy_export_check, then comfy_export_python).
const EXPORT_REQUEST = 'Export this workflow as a Python program I can run without ComfyUI: check it is ready (published, every step exportable), tell me what blocks it, then export it, test it and give me the package.';

// The workflow as an API: what is published (endpoint, version, a call to copy) and the button to
// publish the current version. The server refuses until the structure and release items hold.
function apiSection() {
  const {node: part, body: apiBody} = section('API', {sub: 'this workflow called over HTTP, at a fixed version'});
  part.classList.add('aw-contract-api');
  const info = el('div', null, {className: 'aw-contract-api-info'});
  const name = el('input', null, {type: 'text', placeholder: 'API name, e.g. nayose', className: 'aw-field'});
  name.setAttribute('aria-label', 'API name');
  const publish = el('button', 'Publish as API', {type: 'button', className: 'aw-button aw-button-primary'});
  const examples = el('button', 'Run checks', {type: 'button', className: 'aw-button', title: "This workflow's CI: its design, every stage's Checks, the contract's examples and no regression since the last release, on exactly this graph. Publishing needs them all to pass."});
  const back = el('button', 'Roll back', {type: 'button', className: 'aw-button', hidden: true});
  const exportPython = el('button', 'Export as Python', {type: 'button', className: 'aw-button', title: 'The published release as a Python program with a command line, without ComfyUI: the agent checks it is ready, exports it, tests it in a fresh environment and gives you the package.'});
  const result = el('p', null, {role: 'status', className: 'aw-contract-result'});
  // The outer loop: run the published workflow on a schedule; each run's Checks become a version.
  const every = el('input', null, {type: 'number', min: '0', step: '5', placeholder: 'minutes', className: 'aw-field', style: 'width: 7em; flex: none'});
  every.setAttribute('aria-label', 'Watch every N minutes (0 stops)');
  const watch = el('button', 'Watch', {type: 'button', className: 'aw-button', title: 'Run the published API on a schedule and record whether its Checks pass'});
  const row = (...items) => { const line = el('div', null, {className: 'aw-contract-row'}); line.append(...items); return line; };
  apiBody.append(info, row(name, examples, publish), row(back, exportPython, every, watch), result);
  watch.addEventListener('click', async () => {
    try {
      const response = await api.fetchApi(`/agent-workflow-studio/api/${encodeURIComponent(name.value.trim())}/watch`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({every_minutes: Number(every.value || 0)})});
      const body = await response.json();
      if (!response.ok) throw Error(body.error ?? 'Could not set the watch.');
      result.textContent = body.watch ? `Watching every ${body.watch.every_minutes} min.` : 'Watch stopped.';
      await part.load();
    } catch (error) {
      result.textContent = error.message;
    }
  });
  examples.addEventListener('click', async () => {
    result.textContent = 'Running the checks…';
    try {
      const done = await runChecks(app, api);
      // What publishing this version would change for callers of the last release.
      const change = done.compatibility, breaking = change?.breaking ?? [], additive = change?.additive ?? [];
      const forCallers = breaking.length ? ` · breaking for callers (next release is major): ${breaking.join('; ')}` : additive.length ? ` · for callers: ${additive.join('; ')}` : '';
      result.textContent = `Checks: ${done.summary}` + done.stages.filter(s => s.status !== 'passed').map(s => ` · ${s.label}: ${s.detail.slice(0, 3).join('; ')}`).join('') + forCallers;
    } catch (error) {
      result.textContent = error.message;
    }
  });
  const show = item => {
    // Data inputs are uploaded first with ComfyUI's own upload (claim check), then named in the call.
    const data = item.data_inputs ?? {};
    const uploads = Object.keys(data).map(name => `curl -F image=@your_file.csv -F type=input -F subfolder=${name}_1 ${location.origin}/upload/image\n`).join('');
    const value = name => (data[name] ? `"${name}_1"` : '…');
    const call = `${uploads}curl -X POST ${location.origin}/agent-workflow-studio/api/${item.name}/run -H 'Content-Type: application/json' -d '{"inputs": {${item.inputs.map(i => `"${i}": ${value(i)}`).join(', ')}}}'`;
    // Earlier releases can be returned to (callers pinned to name@x.y.z keep theirs).
    const earlier = (item.versions ?? []).filter(v => v !== item.semver);
    back.hidden = !earlier.length;
    back.textContent = `Roll back to ${earlier.at(-1) ?? ''}`;
    back.onclick = async () => {
      try { await rollbackApi(api, item.name, earlier.at(-1)); result.textContent = `${item.name} now serves ${earlier.at(-1)}.`; await part.load(); } catch (error) { result.textContent = error.message; }
    };
    info.replaceChildren(el('div', `Published as ${item.name} · ${item.semver ?? ''} (${item.version.slice(0, 8)}) · ${item.published_at}`),
      ...(item.notes ? [el('div', item.notes)] : []),
      el('div', `Inputs: ${item.inputs.join(', ') || 'none'} · outputs: ${item.outputs.join(', ')}`),
      el('div', item.watch ? `Watched every ${item.watch.every_minutes} min · last: ${item.watch.last ? `${item.watch.last.passing ? '✓ passing' : '✗ FAILING'} (checks ${item.watch.last.checks}) at ${item.watch.last.at}` : 'not run yet'}` : 'Not watched.'),
      el('code', call, {className: 'aw-contract-call'}),
      Object.assign(el('a', 'OpenAPI description'), {href: `/agent-workflow-studio/api/${item.name}/openapi.json`, target: '_blank'}));
    name.value ||= item.name;
  };
  part.load = async () => {
    const response = await api.fetchApi('/agent-workflow-studio/api');
    const mine = response.ok ? (await response.json()).apis.filter(a => a.workflow_id === rootGraph()?.id) : [];
    if (mine.length) show(mine.at(-1));
    else info.replaceChildren(el('div', 'Not published. Choose its inputs and outputs in App mode (or ask the agent to set the signature), then publish.'));
  };
  // Export as Python: the agent checks the release, exports it, tests the program and hands it over.
  exportPython.addEventListener('click', () => window.dispatchEvent(new CustomEvent('aws:ask-agent', {detail: {text: EXPORT_REQUEST}})));
  publish.addEventListener('click', async () => {
    result.textContent = 'Publishing…';
    try {
      const done = await publishWorkflow(app, api, name.value.trim());
      result.textContent = `Published ${done.semver} (${done.version}): ${done.notes} POST ${done.endpoint}` + (done.todo.length ? ` · still to do: ${done.todo.join('; ')}` : '');
      await part.load();
    } catch (error) {
      result.textContent = error.message;
    }
  });
  return part;
}

function createPanel() {
  const root = el('section', null, {className: 'aw-contract aw-tab'});
  const status = el('p', null, {className: 'aw-status aw-contract-status', role: 'status'});
  const body = el('div', null, {className: 'aw-contract-body'});
  const refresh = iconButton('pi pi-refresh', 'Refresh');
  // One shareable page generated from the workflow: design, data, stages, decisions, status, loop, API.
  const exportDesign = iconButton('pi pi-download', 'Export design');
  exportDesign.addEventListener('click', async () => {
    const text = await designPage(app, api);
    const link = Object.assign(document.createElement('a'), {href: URL.createObjectURL(new Blob([text], {type: 'text/markdown'})), download: 'design.md'});
    link.click();
    URL.revokeObjectURL(link.href);
  });
  const bar = el('div', null, {className: 'aw-tab-bar'});
  bar.append(status, exportDesign, refresh);
  root.append(bar, body, apiSection());

  async function load() {
    if (typeof rootGraph()?.id !== 'string') {
      status.textContent = 'Open a workflow to see its contract.';
      return body.replaceChildren();
    }
    try {
      const {contract} = await request('/contract');
      if (!contract) {
        status.textContent = 'No contract: this workflow was built without one, so there is nothing to check it against. Ask the agent in the chat to plan it: it proposes a contract (what you asked for, examples, steps, how each is checked) and you approve it.';
        return body.replaceChildren();
      }
      const evidence = await contractEvidence(app, api);
      const result = await request('/contract/status', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(evidence)});
      status.textContent = `${result.aligned ? '✓ Aligned with the contract' : `⚠ ${result.drift.length} drift(s) from the contract`} · ${result.freshness}`;
      const trace = result.trace;
      const rows = Object.fromEntries(trace.steps.map(r => [r.id, r]));
      const steps = el('ol', null, {className: 'aw-contract-steps'});
      for (const step of result.steps) {
        const item = el('li'), more = el('details'), head = el('summary');
        head.append(chip(...STATE[step.state]), el('strong', step.purpose), el('small', step.next));
        more.append(head, stepColumns(step, rows[step.step]));
        item.append(more);
        steps.append(item);
      }
      // Drift is listed among the gaps (Broken), with everything else that is not as agreed.
      const parts = [el('p', trace.summary, {className: 'aw-contract-summary'}), gapsSection(trace), requestsSection(trace)];
      const stepsBox = section(`Steps · ${result.progress}`, {sub: 'open one to compare agreed, built and proven'});
      stepsBox.body.append(steps);
      parts.push(stepsBox.node);
      if (result.todo?.length) {
        const todo = section(`To do · ${result.todo.length}`, {open: false});
        todo.body.append(...result.todo.map(item => el('div', item, {className: 'aw-contract-todo'})));
        parts.push(todo.node);
      }
      // The improve loop: each iteration's hypothesis, change and measured verdict (a version each).
      const {journal = [], budget, outside = []} = await request('/loop').catch(() => ({}));
      if (journal.length) {
        const {node: loopNode, body: loop} = section(`Improve loop · ${journal.length}/${budget?.iterations ?? '?'} iterations`);
        for (const j of journal) {
          const mark = {improved: '▲', worse: '▼', same: '■'}[j.verdict] ?? '·';
          const after = j.after ? ` · verified ${j.after.verified.length}/${j.after.steps}` + (j.after.examples ? ` · examples ${j.after.examples[0]}/${j.after.examples[1]}` : '') : '';
          loop.append(el('div', `${mark} #${j.n} ${j.verdict}: ${j.hypothesis} — ${j.change}${after}`, {className: 'aw-contract-todo'}));
        }
        // Changes since the last iteration that no iteration measured.
        if (outside.length) loop.append(el('div', `⚠ ${outside.length} change(s) since #${journal.at(-1).n} made outside the loop, not measured: ${outside.join('; ')}`, {className: 'aw-contract-warning'}));
        parts.push(loopNode);
      }
      const full = section('Full contract', {open: false});
      full.body.append(el('pre', result.text));
      parts.push(full.node);
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
  // A workflow opened with its contract inside (a template): on first open it becomes its own first
  // version (the server does this once, only while the workflow has no history of its own).
  async afterConfigureGraph() {
    const graph = rootGraph(), shipped = graph?.extra?.aws_contract;
    if (!shipped || typeof graph.id !== 'string') return;
    const done = await request('/template', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({workflow: graph.serialize()})}).catch(() => null);
    if (done?.adopted) window.dispatchEvent(new Event(CHANGED));
  },
  setup() {
    document.head.append(el('link', null, {rel: 'stylesheet', href: new URL('./contract_panel.css', import.meta.url).href}));
    let panel;
    for (const event of ['execution_success', 'execution_error']) api.addEventListener(event, () => panel?.isConnected && panel.load());
    registerWorkflowTab({
      order: 1,
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
