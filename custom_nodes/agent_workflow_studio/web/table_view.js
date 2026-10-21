// Tables inside nodes: Nayose benchmark stages and Preview table show their data as small tables
// (ui.aws_tables), kept in node properties so they survive reloads and appear in Versions.
import {app} from '../../scripts/app.js';
import {quietly} from './pi_canvas.js';

const TYPES = new Set(['AgentWorkflowSplitName', 'AgentWorkflowReviewDecisions', 'AgentWorkflowReadTable', 'AgentWorkflowWriteTable', 'AgentWorkflowStackRoles', 'AgentWorkflowConcatenate', 'AgentWorkflowFilterRows', 'AgentWorkflowJoinTables', 'AgentWorkflowNormalizeText', 'AgentWorkflowMapValues', 'AgentWorkflowParseDates', 'AgentWorkflowExtractDigits', 'AgentWorkflowFindCandidates', 'AgentWorkflowCompareFields', 'AgentWorkflowAttachGroups', 'AgentWorkflowParseNumbers', 'AgentWorkflowRemoveDuplicates', 'AgentWorkflowComputeColumn', 'AgentWorkflowSummarize', 'AgentWorkflowDataChecks', 'AgentWorkflowGenerateRecords', 'AgentWorkflowDamageValues', 'AgentWorkflowCompareToTruth', 'AgentWorkflowCheck', 'AgentWorkflowStub', 'AgentWorkflowAgentTool', 'AgentWorkflowDecisionRules', 'AgentWorkflowProfileData', 'AgentWorkflowLearnWeights', 'AgentWorkflowLLMSuggestions', 'AgentWorkflowClusterGuard', 'AgentWorkflowNayosePeople', 'AgentWorkflowNayoseActivity', 'AgentWorkflowNayoseExport', 'AgentWorkflowNayoseScore', 'AgentWorkflowPreviewTable']);

// Decisions are the logic the user came to read: their views get more room.
const viewHeight = node => (['AgentWorkflowDecisionRules', 'AgentWorkflowClusterGuard', 'AgentWorkflowLearnWeights'].includes(node.type) ? 480 : 300);
const COLORS = {match: '#2f8a4a', review: '#b8860b', different: '#5a6b80', undecided: '#4a6fa5'};

// The decision flow: every branch with its decision and how many pairs took it.
function flowView({total, branches}) {
  const section = document.createElement('section');
  const heading = document.createElement('strong');
  heading.textContent = `Decision flow · ${total.toLocaleString()} pairs (first branch that applies decides)`;
  const list = document.createElement('ol');
  list.className = 'aw-flow';
  for (const {label, decision, count, detail} of branches) {
    const item = document.createElement('li');
    const bar = document.createElement('span');
    bar.className = 'aw-flow-bar';
    bar.style.width = `${Math.max(1, (count / Math.max(1, total)) * 100)}%`;
    bar.style.background = COLORS[decision];
    const chip = Object.assign(document.createElement('span'), {className: 'aw-flow-chip', textContent: decision.toUpperCase()});
    chip.style.background = COLORS[decision];
    item.append(Object.assign(document.createElement('span'), {textContent: label}), chip,
      Object.assign(document.createElement('span'), {textContent: count.toLocaleString(), className: 'aw-flow-count'}),
      ...(detail ? [Object.assign(document.createElement('small'), {textContent: detail, className: 'aw-flow-detail'})] : []), bar);
    list.append(item);
  }
  section.append(heading, list);
  return section;
}

// The whole decision tree: each branch with its count, a bar against its parent, nested.
function treeView(root) {
  const build = (node, parentCount) => {
    const item = document.createElement('li');
    const line = document.createElement('div');
    line.className = 'aw-tree-line';
    const share = parentCount ? ` (${Math.round((node.count / parentCount) * 100)}%)` : '';
    line.append(Object.assign(document.createElement('span'), {textContent: node.label}),
      Object.assign(document.createElement('span'), {className: 'aw-flow-count', textContent: `${node.count.toLocaleString()}${share}`}));
    if (node.decision) {
      const chip = Object.assign(document.createElement('span'), {className: 'aw-flow-chip', textContent: node.decision.toUpperCase()});
      chip.style.background = COLORS[node.decision] ?? '#5a6b80';
      line.append(chip);
    }
    const bar = Object.assign(document.createElement('span'), {className: 'aw-flow-bar'});
    bar.style.width = `${Math.max(1, (node.count / Math.max(1, parentCount || node.count)) * 100)}%`;
    bar.style.background = COLORS[node.decision] ?? '#4a6fa5';
    item.append(line, bar);
    if (node.children?.length) {
      const list = document.createElement('ul');
      list.className = 'aw-tree';
      for (const child of node.children) list.append(build(child, node.count));
      item.append(list);
    }
    return item;
  };
  const section = document.createElement('section');
  const heading = Object.assign(document.createElement('strong'), {textContent: 'Decision tree · how every candidate pair was decided'});
  const list = document.createElement('ul');
  list.className = 'aw-tree aw-tree-root';
  list.append(build(root, 0));
  section.append(heading, list);
  return section;
}

function render(container, tables, flow, tree) {
  container.replaceChildren(...(tree ?? []).map(treeView), ...(flow ?? []).map(flowView), ...(tables ?? []).map(({title, columns, rows}) => {
    const section = document.createElement('section');
    const heading = document.createElement('strong');
    heading.textContent = title;
    const table = document.createElement('table');
    const head = table.createTHead().insertRow();
    for (const column of columns) head.append(Object.assign(document.createElement('th'), {textContent: column, scope: 'col'}));
    const body = table.createTBody();
    for (const row of rows) {
      const tr = body.insertRow();
      for (const cell of row) tr.insertCell().textContent = cell;
    }
    section.append(heading, table);
    return section;
  }));
  if (!tables?.length && !flow?.length && !tree?.length) container.textContent = 'Run the workflow to see this stage\'s data.';
}

function ensureView(node) {
  let widget = node.widgets?.find(w => w.name === 'table_view');
  if (widget) return widget.element;
  const container = document.createElement('div');
  container.className = 'aw-table-view';
  // Display only: never sent to the backend as an input.
  // The node reserves exactly the height the view uses (it scrolls inside), so nothing spills over.
  const height = viewHeight(node);
  container.style.height = container.style.maxHeight = `${height}px`;
  widget = node.addDOMWidget('table_view', 'aws_table_view', container, {serialize: false, getMinHeight: () => height, getMaxHeight: () => height});
  widget.element = container;
  render(container, node.properties?.aws_tables, node.properties?.aws_flow, node.properties?.aws_tree);
  fit(node);
  return container;
}

// Fit the node to its widgets plus the view, wide enough to read its sentences (also after a saved
// workflow restores an older size); the automatic layout makes room around it.
function fit(node) {
  const width = Math.max(node.size[0], node.type === 'AgentWorkflowAgentTool' ? 480 : 560);
  node.setSize([width, Math.max(node.size[1], node.computeSize?.()[1] ?? 0)]);
}

app.registerExtension({
  name: 'AgentWorkflowStudio.TableView',
  setup() {
    const style = document.createElement('style');
    style.textContent = `
      .aw-table-view { overflow: auto; font-size: 11px; display: flex; flex-direction: column; gap: 8px; }
      .aw-table-view table { border-collapse: collapse; width: max-content; }
      .aw-table-view th, .aw-table-view td { border: 1px solid var(--border-color, #555); padding: 1px 4px; white-space: nowrap; text-align: left; }
      .aw-table-view th { position: sticky; top: 0; background: var(--comfy-menu-bg, #333); }
      .aw-flow { margin: 4px 0 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; }
      .aw-flow li { display: grid; grid-template-columns: 1fr auto auto; gap: 2px 8px; align-items: center; }
      .aw-flow-bar { grid-column: 1 / -1; height: 4px; border-radius: 2px; }
      .aw-flow-chip { color: #fff; font-size: 10px; font-weight: 600; padding: 0 5px; border-radius: 3px; }
      .aw-flow-count { font-variant-numeric: tabular-nums; }
      .aw-flow-detail { grid-column: 1 / -1; opacity: 0.7; white-space: normal; }
      .aw-tree { list-style: none; margin: 2px 0 0; padding-left: 14px; border-left: 1px solid var(--border-color, #555); display: flex; flex-direction: column; gap: 3px; }
      .aw-tree-root { border-left: none; padding-left: 0; }
      .aw-tree-line { display: flex; gap: 8px; align-items: center; }
      .aw-tree-line > span:first-child { flex: 1; }`;
    document.head.append(style);
  },
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (!TYPES.has(nodeData.name)) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      created?.apply(this, arguments);
      // Code written by the agent is the one kind of step whose logic is not on the canvas: mark it.
      if (nodeData.name === 'AgentWorkflowAgentTool') Object.assign(this, {color: '#7a5c14', bgcolor: '#4d3a0e'});
      ensureView(this);
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      configured?.apply(this, arguments);
      render(ensureView(this), this.properties?.aws_tables, this.properties?.aws_flow, this.properties?.aws_tree);
      fit(this);
    };
    const executed = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (output) {
      executed?.apply(this, arguments);
      // Rules that came in over a link: show them in the (read-only) Rules box instead of stale text.
      const rules = output?.aws_rules?.[0], box = this.widgets?.find(w => w.name === 'rules');
      if (rules && box && box.value !== rules) box.value = rules;
      // For glance.js: wire labels and the zoomed-out line (else the first line of the node's text).
      const line = [].concat(output?.aws_headline ?? output?.text?.[0]?.split('\n')[0] ?? [])[0];
      // What the run showed is kept on the node (it survives a reload) without becoming an undo step.
      if (output?.aws_wires || line) quietly(app, () => { this.properties = {...this.properties, aws_wires: output?.aws_wires, aws_headline: line?.slice(0, 600)}; });
      // The datasets it gave, by output slot (class, name, file, rows): the Data tab lists them.
      if (output?.aws_datasets) quietly(app, () => { this.properties = {...this.properties, aws_datasets: output.aws_datasets}; });
      if (!output?.aws_tables && !output?.aws_flow && !output?.aws_tree) return;
      quietly(app, () => { this.properties = {...this.properties, aws_tables: output.aws_tables, aws_flow: output.aws_flow, aws_tree: output.aws_tree}; });
      render(ensureView(this), output.aws_tables, output.aws_flow, output.aws_tree);
    };
  },
});
