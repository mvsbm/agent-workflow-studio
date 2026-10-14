// Stage cards: a subgraph (a packed contract step, or given data) shows on its face what it does,
// its key numbers and its gate status, so the top level of a workflow reads as the data's story.
// Double-click a card to open the stage: the tool, its settings, samples and decision tree.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {contractEvidence} from './pi_canvas.js';
import {stageSummary} from './stage_summary.js';

const CHANGED = 'agent-workflow-studio:versions-changed';
const KINDS = {data: ['#24496b', '#1a3550'], stage: ['#3d4a5c', '#2b3340'], decision: ['#5b3d7a', '#3a2a4f'], code: ['#7a5c14', '#4d3a0e']};
const STATES = {planned: '#5a6b80', built: '#4a6fa5', fuzzy: '#7a52a8', verified: '#2f8a4a', failing: '#b04040'};
const DECIDERS = new Set(['AgentWorkflowLearnWeights', 'AgentWorkflowDecisionRules', 'AgentWorkflowLLMSuggestions', 'AgentWorkflowReviewDecisions', 'AgentWorkflowClusterGuard']);
function kindOf(node) {
  const types = node.subgraph?.nodes?.map(n => n.type) ?? [];
  if (types.some(t => DECIDERS.has(t))) return 'decision';
  if (types.includes('AgentWorkflowAgentTool')) return 'code'; // logic inside agent-written code
  if (node.properties?.aws_given) return 'data';
  return 'stage';
}

function render(node) {
  const card = node.aws_card;
  if (!card) return;
  const {purpose, lines, state} = node.properties?.aws_card ?? {};
  card.replaceChildren();
  if (purpose) card.append(Object.assign(document.createElement('div'), {className: 'aw-card-purpose', textContent: purpose}));
  for (const line of lines ?? []) card.append(Object.assign(document.createElement('div'), {className: 'aw-card-line', textContent: line}));
  if (state) {
    const chip = Object.assign(document.createElement('span'), {className: 'aw-card-chip', textContent: state.toUpperCase()});
    chip.style.background = STATES[state] ?? '#5a6b80';
    card.append(chip);
  }
  if (!card.childNodes.length) card.append(Object.assign(document.createElement('div'), {className: 'aw-card-line', textContent: 'Run the workflow to see what this stage did.'}));
  card.append(Object.assign(document.createElement('div'), {className: 'aw-card-hint', textContent: 'Double-click to open'}));
  const [color, bgcolor] = KINDS[kindOf(node)];
  Object.assign(node, {color, bgcolor});
}

function ensureCard(node) {
  if (node.aws_card || !node.isSubgraphNode?.()) return;
  const card = document.createElement('div');
  card.className = 'aw-card';
  // Display only: never sent to the backend as an input.
  node.addDOMWidget('stage_card', 'aws_stage_card', card, {serialize: false, getMinHeight: () => 140});
  node.aws_card = card;
  if (node.size[0] < 260 || node.size[1] < 200) node.setSize([Math.max(node.size[0], 260), Math.max(node.size[1], 200)]);
  render(node);
}

/** Recompute every card from its inner nodes and, when there is a contract, its step's state. */
async function refresh() {
  const graph = app.rootGraph ?? app.graph;
  const stages = (graph?.nodes ?? []).filter(node => node.isSubgraphNode?.());
  if (!stages.length) return;
  let steps = {};
  try {
    const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/contract`);
    const {contract, gates} = await response.json();
    if (contract) {
      const evidence = await contractEvidence(app, api, gates);
      const status = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/contract/status`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(evidence)}).then(r => r.json());
      steps = Object.fromEntries((status.steps ?? []).map(step => [step.step, step]));
    }
  } catch {
    // No contract or no project yet: cards still show their numbers.
  }
  for (const node of stages) {
    ensureCard(node);
    const step = steps[node.properties?.aws_stage];
    node.properties = {...node.properties, aws_card: {purpose: step?.purpose ?? '', lines: stageSummary(node.subgraph?.nodes ?? []), state: step?.state ?? ''}};
    render(node);
  }
  graph.setDirtyCanvas?.(true, true);
}

app.registerExtension({
  name: 'AgentWorkflowStudio.StageCards',
  setup() {
    const style = document.createElement('style');
    style.textContent = `
      .aw-card { display: flex; flex-direction: column; gap: 5px; font-size: 14px; line-height: 1.3; padding: 2px 4px; overflow: hidden; }
      .aw-card-purpose { opacity: 0.8; font-style: italic; font-size: 12px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
      .aw-card-line { font-variant-numeric: tabular-nums; }
      .aw-card-chip { align-self: flex-start; color: #fff; font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 3px; }
      .aw-card-hint { opacity: 0.45; font-size: 10px; }`;
    document.head.append(style);
    let timer;
    const later = () => { clearTimeout(timer); timer = setTimeout(() => refresh().catch(() => {}), 400); };
    api.addEventListener('execution_success', later);
    window.addEventListener(CHANGED, later);
    this.refresh = later;
  },
  nodeCreated(node) {
    // Subgraph nodes are created on load too; give them their card (filled from saved properties).
    queueMicrotask(() => ensureCard(node));
  },
  afterConfigureGraph() {
    this.refresh?.();
  },
});
