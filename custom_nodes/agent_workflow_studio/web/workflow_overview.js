// The open workflow's panels (design, contract, versions, data, review, customers) as tabs in ComfyUI's
// own right panel, in its Workflow overview: what it shows when nothing is selected. Tabs marked
// `selection` (Design) also show beside the native tabs of a selected node, and follow the selection. ComfyUI has no API
// for that panel's tabs, so ours attach to it by its test ids (the frontend is pinned, and a browser
// test fails if they change); when the panel cannot be found, the Workflow drawer shows them instead.
import {app} from '../../scripts/app.js';
import {workflowTabs} from './workflow_drawer.js';

const FLAG = 'agent-workflow-studio:overview';  // 'drawer': always use the drawer (an escape hatch)
let active = null, shownFor = null, shownIn = null, scheduled = false;

const el = (tag, props = {}, text) => Object.assign(document.createElement(tag), props, text == null ? {} : {textContent: text});
// What a tab shows depends on the workflow and, for selection tabs, on what is selected.
const graphId = () => `${(app.rootGraph ?? app.graph)?.id}|${app.canvas?.graph?.id}:${Object.keys(app.canvas?.selected_nodes ?? {}).join(',')}`;
const panel = () => document.querySelector('[data-testid="properties-panel"]');
// Only the Workflow overview (nothing selected) has the Nodes tab.
const inOverview = root => Boolean(root?.querySelector('[data-testid="panel-tab-nodes"]'));
export const overviewDisabled = () => localStorage.getItem(FLAG) === 'drawer';

const shownTabs = root => (inOverview(root) ? workflowTabs() : workflowTabs().filter(t => t.selection));

function render(host) {
  const tab = workflowTabs().find(t => t.id === active);
  if (!tab) return;
  shownFor = graphId();
  shownIn = host;
  tab.render(host);
}

function select(id) {
  active = id;
  shownFor = null;
  attach();
}

function attach() {
  scheduled = false;
  const root = panel();
  if (!root) return;
  const section = root.firstElementChild, nativeNav = section?.querySelector('nav');
  let row = root.querySelector('[data-testid="aw-overview-tabs"]');
  let host = root.querySelector('[data-testid="aw-overview-content"]');
  const native = [...root.children].find(child => child !== section && child !== host);
  const tabs = shownTabs(root), current = tabs.some(t => t.id === active) ? active : null;
  const show = !overviewDisabled() && nativeNav && tabs.length;
  if (!show) {
    // We are off, or the panel has no place for our tabs: the native panel as it is.
    if (row) row.hidden = true;
    if (host) host.hidden = true;
    if (native) native.style.display = '';
    root.classList.remove('aw-overview-on');
    return;
  }
  if (!row) {
    row = el('div', {className: 'aw-overview-tabs'});
    row.dataset.testid = 'aw-overview-tabs';
    row.setAttribute('role', 'tablist');
    row.setAttribute('aria-label', 'This workflow');
    nativeNav.after(row);
  }
  if (!nativeNav.dataset.awHooked) {
    // Choosing a native tab (Parameters, Nodes, Settings) brings the native content back.
    nativeNav.dataset.awHooked = '1';
    nativeNav.addEventListener('click', () => { if (active) select(null); }, true);
  }
  if (!host) {
    host = el('div', {className: 'aw-overview-content'});
    host.dataset.testid = 'aw-overview-content';
    root.append(host);
  }
  row.hidden = false;
  const look = nativeNav.querySelector('[data-testid^="panel-tab-"]')?.className ?? '';
  const painted = `${current}|${tabs.map(t => t.id).join(',')}`;
  if (row.dataset.painted !== painted) {  // repaint only on change: painting is itself a DOM change
    row.dataset.painted = painted;
    row.replaceChildren(...tabs.map(tab => {
      const button = el('button', {type: 'button', className: `${look} aw-overview-tab`, title: tab.tooltip ?? tab.title}, tab.title);
      button.dataset.testid = `aw-tab-${tab.id}`;
      button.dataset.state = tab.id === current ? 'active' : 'inactive';
      button.setAttribute('role', 'tab');
      button.setAttribute('aria-selected', String(tab.id === current));
      button.addEventListener('click', () => select(tab.id));
      return button;
    }));
  }
  root.classList.toggle('aw-overview-on', Boolean(current));
  if (native) native.style.display = current ? 'none' : '';
  host.hidden = !current;
  // Again when the workflow or the selection changed, or ComfyUI rebuilt its panel (a fresh, empty host).
  if (current && (shownFor !== graphId() || shownIn !== host)) render(host);
}

/** Open the native panel in its Workflow overview on one of our tabs; false when it is not there. */
export async function openInOverview(id) {
  if (overviewDisabled()) return false;
  app.canvas?.deselectAll?.();
  if (!panel()) {
    await app.extensionManager?.setting?.set?.('Comfy.RightSidePanel.IsOpen', true);
    if (!panel()) document.querySelector('[aria-label="Toggle properties panel"]')?.click();
  }
  for (let i = 0; i < 40 && !inOverview(panel()); i++) await new Promise(resolve => setTimeout(resolve, 50));
  if (!inOverview(panel())) return false;
  select(id ?? active ?? workflowTabs()[0]?.id);
  return true;
}

new MutationObserver(() => {
  if (!scheduled) { scheduled = true; requestAnimationFrame(attach); }
}).observe(document.body, {childList: true, subtree: true});
// Another workflow opened: show the shown tab for it.
setInterval(() => { if (active && graphId() !== shownFor) attach(); }, 1000);
