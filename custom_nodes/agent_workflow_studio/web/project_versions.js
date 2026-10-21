// Versions: every agent edit and user save of a workflow is a git commit in its
// project repository. The sidebar lists them, shows what changed, and restores any
// version as a new commit (history is never rewritten).
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {iconButton, registerWorkflowTab} from './workflow_drawer.js';

const rootGraph = () => app.rootGraph ?? app.graph;
const workflowId = () => rootGraph()?.id;
const base = () => `/agent-workflow-studio/projects/${encodeURIComponent(workflowId())}`;
const CHANGED = 'agent-workflow-studio:versions-changed';

async function request(path, options) {
  const response = await api.fetchApi(base() + path, options);
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'Version history request failed.');
  return body;
}

/** Commit the current canvas; author is 'agent' or 'user'. Never throws. */
export async function commitWorkflow(message, author) {
  if (typeof workflowId() !== 'string') return null;
  try {
    const {sha} = await request('/commits', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({workflow: rootGraph().serialize(), message, author}),
    });
    window.dispatchEvent(new Event(CHANGED));
    return sha;
  } catch (error) {
    console.warn('Agent Workflow Studio: could not record a version', error);
    return null;
  }
}

function el(tag, text, props = {}) {
  const node = Object.assign(document.createElement(tag), props);
  if (text != null) node.textContent = text;
  return node;
}

function describe(diff) {
  const parts = [];
  if (diff.added.length) parts.push(`+${diff.added.length} node${diff.added.length > 1 ? 's' : ''}`);
  if (diff.removed.length) parts.push(`−${diff.removed.length} node${diff.removed.length > 1 ? 's' : ''}`);
  if (diff.changed.length) parts.push(`${diff.changed.length} changed`);
  if (diff.links_added) parts.push(`+${diff.links_added} link${diff.links_added > 1 ? 's' : ''}`);
  if (diff.links_removed) parts.push(`−${diff.links_removed} link${diff.links_removed > 1 ? 's' : ''}`);
  return parts.join(' · ') || 'No node or link changes';
}

function nodeList(title, nodes) {
  if (!nodes.length) return null;
  const list = el('ul', null, {className: 'aw-versions-nodes'});
  // A node inside a stage is named by its stage; its id there is the subgraph's, unreadable.
  for (const node of nodes) list.append(el('li', node.stage ? `${node.title || node.type} in ${node.stage}` : `${node.title || node.type} (#${node.id})`));
  const section = el('div');
  section.append(el('div', title, {className: 'aw-label'}), list);
  return section;
}

function recordSection(record) {
  const section = el('div', null, {className: 'aw-versions-record'});
  const model = [record.model?.provider, record.model?.model].filter(Boolean).join(' · ') || 'Pi defaults';
  section.append(
    el('div', `Run record · ${record.status}`, {className: 'aw-label'}),
    el('p', `${model} · ${record.duration_seconds}s${record.error ? ` · ${record.error}` : ''}`),
  );
  if (record.output) section.append(el('pre', JSON.stringify(record.output, null, 2).slice(0, 2000)));
  const full = el('details');
  full.append(el('summary', 'Full record (prompt, schema, telemetry)'), el('pre', JSON.stringify(record, null, 2)));
  section.append(full);
  return section;
}

async function restore(sha) {
  if (!window.confirm(`Restore version ${sha.slice(0, 8)}? The canvas is replaced, and the restore is recorded as a new version. You can undo it.`)) return;
  const {workflow} = await request(`/restore/${sha}`, {method: 'POST'});
  const graph = rootGraph();
  // Same transaction events as native edits, so one undo reverts the restore.
  app.canvas.emitBeforeChange();
  try {
    graph.configure(workflow);
  } finally {
    app.canvas.emitAfterChange();
  }
  graph.setDirtyCanvas(true, true);
  window.dispatchEvent(new Event(CHANGED));
}

function createPanel() {
  const root = el('section', null, {className: 'aw-versions aw-tab'});
  const status = el('p', null, {className: 'aw-status aw-versions-status', role: 'status'});
  const list = el('ol', null, {className: 'aw-versions-list'});
  const refresh = iconButton('pi pi-refresh', 'Refresh');
  const bar = el('div', null, {className: 'aw-tab-bar'});
  bar.append(status, refresh);
  root.append(bar, list);

  async function load() {
    if (typeof workflowId() !== 'string') {
      status.textContent = 'Open a workflow to see its versions.';
      list.replaceChildren();
      return;
    }
    try {
      const {commits} = await request('/log');
      status.textContent = commits.length
        ? `${commits.length} version${commits.length > 1 ? 's' : ''} of this workflow. Agent edits and saves are recorded automatically.`
        : 'No versions yet: nothing has been saved or changed by the agent since this workflow was opened. Save it (Ctrl/⌘ + S) to record the first version; after that, every save and agent edit is a version you can restore.';
      list.replaceChildren(...commits.map((commit, index) => item(commit, index === 0)));
    } catch (error) {
      status.textContent = error.message;
    }
  }

  function item(commit, latest) {
    const row = el('li', null, {className: `aw-section aw-versions-item aw-versions-${commit.author}`});
    // A section head like a node's in the Parameters tab: who and which layer, then what it did.
    const toggle = el('button', null, {type: 'button', className: 'aw-section-head aw-versions-toggle'});
    toggle.setAttribute('aria-expanded', 'false');
    const meta = el('span', null, {className: 'aw-versions-meta'});
    const when = new Date(commit.time * 1000);
    meta.append(
      el('span', commit.author === 'agent' ? 'Agent' : 'You', {className: 'aw-versions-author'}),
      // Which layer of the workflow it changed: contract, design, a stage's LLD, checks, a run, a release.
      el('span', commit.layer_label ?? '', {className: `aw-versions-layer aw-layer-${commit.layer ?? 'edit'}`}),
      el('time', when.toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}), {dateTime: when.toISOString(), title: when.toLocaleString()}),
    );
    const titles = el('span', null, {className: 'aw-section-title'});
    titles.append(meta, el('strong', commit.message, {className: 'aw-versions-message', title: commit.message}));
    toggle.append(titles, el('i', null, {className: 'pi pi-chevron-down'}));
    const details = el('div', null, {className: 'aw-section-body aw-versions-details', hidden: true});
    row.append(toggle, details);
    toggle.addEventListener('click', async () => {
      const open = details.hidden;
      details.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      if (!open || details.childElementCount) return;
      try {
        const {diff, record, plan_text: planText, workflow, touched = [], code} = await request(`/commits/${commit.sha}`);
        if (touched.length) {
          const stages = el('div', null, {className: 'aw-versions-record'});
          stages.append(el('div', 'Stages it changed', {className: 'aw-label'}));
          const list = el('ul', null, {className: 'aw-versions-nodes'});
          for (const t of touched) {
            const line = el('li', t.stage);
            if (t.serves.length) line.append(el('div', `serves ${t.serves.join(' + ')}`, {className: 'aw-section-sub'}));
            list.append(line);
          }
          stages.append(list);
          details.append(stages);
        }
        // Workflow code this version changed (an approved tool), as a diff of its exact source.
        if (code) {
          const changed = el('div', null, {className: 'aw-versions-record'});
          const lines = el('pre', null, {className: 'aw-versions-code'});
          for (const line of code.split('\n')) lines.append(el('span', `${line}\n`, {className: line.startsWith('+') && !line.startsWith('+++') ? 'aw-code-add' : line.startsWith('-') && !line.startsWith('---') ? 'aw-code-del' : ''}));
          changed.append(el('div', 'Code changed', {className: 'aw-label'}), lines);
          details.append(changed);
        }
        if (planText) {
          const plan = el('div', null, {className: 'aw-versions-record'});
          plan.append(el('div', 'Approved plan', {className: 'aw-label'}), el('pre', planText));
          details.append(plan);
        }
        if (record) details.append(recordSection(record));
        details.append(el('p', describe(diff)));
        for (const section of [nodeList('Added', diff.added), nodeList('Removed', diff.removed), nodeList('Changed', diff.changed)]) {
          if (section) details.append(section);
        }
        const button = el('button', !workflow ? 'No canvas snapshot' : latest ? 'Current version' : 'Restore this version', {
          type: 'button',
          className: 'aw-button',
          disabled: latest || !workflow,
        });
        button.addEventListener('click', () => restore(commit.sha).catch(error => { status.textContent = error.message; }));
        const foot = el('div', null, {className: 'aw-versions-foot'});
        foot.append(el('span', `version ${commit.sha.slice(0, 12)}`), button);
        details.append(foot);
      } catch (error) {
        details.append(el('p', error.message));
      }
    });
    return row;
  }

  refresh.addEventListener('click', load);
  window.addEventListener(CHANGED, load);
  root.load = load;
  return root;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Versions',
  setup() {
    document.head.append(el('link', null, {rel: 'stylesheet', href: new URL('./project_versions.css', import.meta.url).href}));
    // A user save is a version. Subscribing to the workflow store's own action
    // catches Save from the menu, the keyboard shortcut and dialogs alike.
    app.extensionManager.workflow.$onAction(({name, after}) => {
      if (name !== 'saveWorkflow') return;
      after(() => commitWorkflow(`Save ${app.extensionManager.workflow.activeWorkflow?.filename ?? 'workflow'}`, 'user'));
    });
    // Runs can add record versions; refresh when one finishes.
    for (const event of ['execution_success', 'execution_error']) api.addEventListener(event, () => window.dispatchEvent(new Event(CHANGED)));
    let panel;
    registerWorkflowTab({
      order: 2,
      id: 'workflow-versions',
      icon: 'pi pi-history',
      title: 'Versions',
      tooltip: 'Versions · every agent edit and save of this workflow',
      type: 'custom',
      render: container => {
        panel ??= createPanel();
        container.replaceChildren(panel);
        panel.load();
      },
    });
  },
  afterConfigureGraph() {
    window.dispatchEvent(new Event(CHANGED));
  },
});
