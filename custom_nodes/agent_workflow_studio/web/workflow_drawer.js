// The open workflow's own panels (contract, versions, data, review, customers): registered here and
// shown as tabs in ComfyUI's right panel, in its Workflow overview (workflow_overview.js). When that
// panel cannot be found, they show in this drawer on the right of the canvas instead, titled with
// the workflow and following a switch of workflow. The left sidebar keeps global tools only.
import {app} from '../../scripts/app.js';
import {openInOverview} from './workflow_overview.js';

const tabs = [];
let drawer, strip, body, heading, active, shownFor;

const el = (tag, props = {}, text) => Object.assign(document.createElement(tag), props, text == null ? {} : {textContent: text});
const workflowName = () => {
  const workflow = app.extensionManager?.workflow?.activeWorkflow;
  return workflow?.filename ?? workflow?.path?.split('/').at(-1)?.replace(/\.json$/, '') ?? 'Unsaved workflow';
};
const workflowId = () => (app.rootGraph ?? app.graph)?.id;

export const workflowTabs = () => tabs;

/** A panel of the open workflow: {id, order, title, icon, tooltip, render(container)}. */
export function registerWorkflowTab(tab) {
  tabs.push(tab);
  tabs.sort((a, b) => (a.order ?? 99) - (b.order ?? 99));
  if (drawer) showStrip();
}

function showStrip() {
  strip.replaceChildren(...tabs.map(tab => {
    const button = el('button', {type: 'button', className: `aw-drawer-tab${tab.id === active ? ' active' : ''}`, title: tab.tooltip ?? tab.title});
    button.setAttribute('role', 'tab');
    button.setAttribute('aria-selected', String(tab.id === active));
    button.append(el('i', {className: tab.icon}), el('span', {}, tab.title));
    button.addEventListener('click', () => openWorkflowTab(tab.id));
    return button;
  }));
}

const TOOLTIP = "Open this workflow's overview: contract, versions, data, review, customers";

function place() {
  // Just below the top bar that holds the Workflow button (the canvas itself spans the whole window).
  const anchor = document.querySelector(`[aria-label="${TOOLTIP}"]`)?.getBoundingClientRect();
  drawer.style.top = `${anchor ? anchor.bottom + 12 : 80}px`;
}

function ensure() {
  if (drawer) return;
  drawer = el('aside', {className: 'aw-drawer', hidden: true});
  drawer.setAttribute('aria-label', 'This workflow');
  heading = el('h2');
  const close = el('button', {type: 'button', className: 'aw-drawer-close', title: 'Close'}, '✕');
  close.setAttribute('aria-label', 'Close the workflow drawer');
  close.addEventListener('click', () => { drawer.hidden = true; });
  const header = el('header');
  header.append(heading, close);
  strip = el('nav', {className: 'aw-drawer-tabs'});
  strip.setAttribute('role', 'tablist');
  body = el('div', {className: 'aw-drawer-body'});
  drawer.append(header, strip, body);
  document.body.append(drawer);
  window.addEventListener('resize', () => !drawer.hidden && place());
  // Follow a switch of workflow (tabs, opening another file): retitle and reload the shown panel.
  setInterval(() => { if (!drawer.hidden && workflowId() !== shownFor) show(); }, 1000);
}

function show() {
  const tab = tabs.find(t => t.id === active) ?? tabs[0];
  if (!tab) return;
  active = tab.id;
  shownFor = workflowId();
  heading.textContent = workflowName();
  heading.title = `Panels of this workflow (${shownFor ?? 'not saved'})`;
  showStrip();
  tab.render(body);
}

/** Show one of the workflow's panels: in the native Workflow overview, else in the drawer (also used by tests). */
export async function openWorkflowTab(id) {
  if (await openInOverview(id)) {
    if (drawer) drawer.hidden = true;
    return;
  }
  ensure();
  if (id) active = id;
  drawer.hidden = false;
  place();
  show();
}

export function closeWorkflowDrawer() {
  if (drawer) drawer.hidden = true;
}

app.registerExtension({
  name: 'AgentWorkflowStudio.WorkflowDrawer',
  setup() {
    document.head.append(el('link', {rel: 'stylesheet', href: new URL('./workflow_drawer.css', import.meta.url).href}));
  },
  actionBarButtons: [{
    icon: 'pi pi-briefcase',
    label: 'Workflow',
    tooltip: TOOLTIP,
    onClick: () => { if (drawer && !drawer.hidden) drawer.hidden = true; else openWorkflowTab(); },
  }],
});
