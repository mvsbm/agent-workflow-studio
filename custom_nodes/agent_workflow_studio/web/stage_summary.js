// What a stage did, in a few lines read from its inner nodes' last run (no DOM, testable).
const number = text => Number(String(text).replace(/[^0-9.]/g, '')).toLocaleString();

/** The few numbers that tell what a stage did, read from its inner nodes' last run. */
export function stageSummary(inner) {
  const lines = [];
  // A graph of table steps: rows into its first step and out of its last (left to right), and how many steps.
  const steps = inner.filter(n => (n.properties?.aws_tables?.[0]?.rows ?? []).some(r => r[0] === 'rows out')).sort((a, b) => (a.pos?.[0] ?? 0) - (b.pos?.[0] ?? 0));
  if (steps.length > 1) {
    const fact = (node, key) => node.properties.aws_tables[0].rows.find(r => r[0] === key)?.[1];
    // Rows read by the stage's sources (steps with no rows in), or rows into its first step.
    const sources = steps.filter(n => fact(n, 'rows in') === '—');
    const total = sources.reduce((sum, n) => sum + Number(String(fact(n, 'rows out')).replace(/,/g, '')), 0);
    const into = sources.length ? total.toLocaleString() : fact(steps[0], 'rows in');
    lines.push(`${steps.length} steps · ${into} rows → ${fact(steps.at(-1), 'rows out')}`);
  }
  for (const node of inner) {
    const tables = node.properties?.aws_tables ?? [];
    const flow = node.properties?.aws_flow?.[0];
    const tree = node.properties?.aws_tree?.[0];
    const io = tables.find(t => t.title === 'Data in → out');
    if (io) {
      const side = dir => io.rows.filter(r => r[0] === dir).map(r => `${r[1]} ${r[2]}`).join(', ');
      if (side('in') || side('out')) lines.push(`${side('in') || '—'} → ${side('out') || '—'}`);
    } else if (flow) {
      const sum = {};
      for (const branch of flow.branches) sum[branch.decision] = (sum[branch.decision] ?? 0) + branch.count;
      lines.push(['match', 'review', 'different', 'undecided'].filter(d => sum[d]).map(d => `${d} ${sum[d].toLocaleString()}`).join(' · '));
    } else if (tree) {
      // "Clusters: N" ("Customers: N" in runs from before the type standard).
      const customers = tree.children?.find(c => /^(Clusters|Customers):/.test(c.label));
      const merges = tree.children?.find(c => c.label === 'Merges');
      if (customers) lines.push(customers.label);
      const blocked = merges?.children?.find(c => c.label.startsWith('blocked'));
      if (blocked) lines.push(`${blocked.count.toLocaleString()} merge${blocked.count === 1 ? '' : 's'} blocked`);
    } else if (node.type === 'AgentWorkflowProfileData' && tables[0]?.title === 'Files') {
      const mixed = tables[0].rows.reduce((sum, r) => sum + Number(r[3] || 0), 0);
      lines.push(`${tables[0].rows.length} files profiled · ${mixed} mixed-format columns`);
    } else if (node.type === 'AgentWorkflowReviewDecisions' && tables[0]?.rows) {
      const get = key => tables[0].rows.find(r => r[0] === key)?.[1] ?? '0';
      lines.push(`you: ${get('you decided: same')} same · ${get('you decided: different')} different · ${String(get('still open')).split(' ')[0]} open`);
    } else if (node.type === 'AgentWorkflowLearnWeights') {
      const share = tables[0]?.rows?.find(r => r[0].startsWith('share that match'));
      if (share) lines.push(`learned: ${share[1]} of pairs match`);
    } else if (tables[0]?.rows?.length && node.type?.startsWith('AgentWorkflowNayose')) {
      const rows = tables[0].rows.find(r => ['rows', 'people'].includes(r[0]));
      const file = tables[0].rows.find(r => r[0] === 'file');
      if (rows) lines.push(`${file ? file[1].split('/').pop() : rows[0]} ${number(rows[1])}`);
    }
  }
  return lines.filter(Boolean).slice(0, 4);
}


/** "takes a · does · gives b, c": the step's parts in reading order, ports of a kind together. */
export function partsText(tags) {
  const ports = kind => tags.filter(t => t.startsWith(`${kind} `)).map(t => t.slice(kind.length + 1));
  const rest = tags.filter(t => !/^(takes|gives) /.test(t) && t !== 'does');
  return [ports('takes').length && `takes ${ports('takes').join(', ')}`, tags.includes('does') && 'does',
    ports('gives').length && `gives ${ports('gives').join(', ')}`, ...rest].filter(Boolean).join(' · ');
}

const SCAFFOLD_TYPES = ['AgentWorkflowStub', 'AgentWorkflowCheck', 'AgentWorkflowApiOutput'];
/** A request as it is shown everywhere: its id with its title, never the id alone (IEEE 29148). */
// Contracts from before titles: the same fallback as workflow_contract.title_of.
const fallbackTitle = text => { const t = text.replace(/^When [^,]*, (the workflow )?/, '').replace(/\.$/, ''); return t.charAt(0).toUpperCase() + t.slice(1, 40); };
export const requestLabel = r => `${r.id} · ${r.title || fallbackTitle(r.we_understood ?? '')}`;

const working = stage => (stage.subgraph?.nodes ?? []).filter(n => !SCAFFOLD_TYPES.includes(n.type)).sort((a, b) => a.pos[0] - b.pos[0] || a.pos[1] - b.pos[1]);
/** A node's number inside its stage, in reading order (2.3: stage 2, third node). */
export const numberOf = (node, stage) => `${stage.properties?.aws_stage_order ?? '?'}.${working(stage).indexOf(node) + 1}`;

/** What a node's design badges say. A stage serves requests; a node inside a stage gets its number in
 *  reading order (2.3: stage 2, third node, as in a levelled data-flow diagram) and the part of its
 *  step it realizes; a Check verifies; a node outside the design says so. */
// Workflow code: an Agent Tool node runs a tool the agent wrote and the user approved for this workflow.
export const TOOL_NODE = 'AgentWorkflowAgentTool';
const toolOf = node => node.widgets?.find(w => w.name === 'tool')?.value ?? node.widgets_values?.[0] ?? '';

export function designLabels(node, stage, requests = []) {
  const props = node.properties ?? {};
  if (props.aws_step) {
    const serves = (props.aws_step.covers ?? []).map(id => {
      const request = requests.find(r => r.id === id);
      return {text: `serves ${request ? requestLabel(request) : id}`, kind: 'request'};
    });
    // A stage with workflow code says so on its card: code is the exception, so it shows.
    return (node.subgraph?.nodes ?? []).some(n => n.type === TOOL_NODE) ? [...serves, {text: 'workflow code', kind: 'code'}] : serves;
  }
  if (!stage) return [];
  if (node.type === 'AgentWorkflowCheck') return [{text: 'verifies the stage', kind: 'gate'}];
  if (SCAFFOLD_TYPES.includes(node.type)) return [];
  const number = numberOf(node, stage), tags = [].concat(props.aws_realizes ?? []);
  const part = tags.length ? {text: `${number} · ${partsText(tags)}`, kind: 'part'} : {text: `${number} · + extra, not in the design`, kind: 'extra'};
  return node.type === TOOL_NODE ? [part, {text: `workflow code · ${toolOf(node) || 'no tool set'}`, kind: 'code'}] : [part];
}

/** A stage from the outside and the inside (arc42 black box / white box): what its step says, each
 *  part of it with the nodes realizing it (✗ when none), its Checks, and nodes not in the design. */
export function stageView(stage, requests = []) {
  const step = stage.properties?.aws_step ?? {}, nodes = working(stage);
  const label = n => `${numberOf(n, stage)} ${n.title ?? n.type}`;
  const tagsOf = n => [].concat(n.properties?.aws_realizes ?? []);
  const parts = [...Object.keys(step.takes ?? {}).map(p => `takes ${p}`), 'does', ...Object.keys(step.gives ?? {}).map(p => `gives ${p}`)];
  return {
    title: stage.title,
    serves: (step.covers ?? []).map(id => requests.find(r => r.id === id)).filter(Boolean),
    description: stage.subgraph?.description ?? '',
    parts: parts.map(part => ({part, nodes: nodes.filter(n => tagsOf(n).includes(part)).map(label)})),
    checks: (stage.subgraph?.nodes ?? []).filter(n => n.type === 'AgentWorkflowCheck').map(n => n.title ?? 'Check'),
    gate: step.gate?.says ?? '',
    extra: nodes.filter(n => !tagsOf(n).length).map(label),
  };
}

/** Why a node exists, from the request down: [the requests its stage serves, the stage, the node
 *  with the part it realizes], e.g. ["R3 · Own cleaning lane", "2 · Clean policies", "2.3 Split name · does"]. */
export function nodePath(node, stage, requests = []) {
  const served = (stage.properties?.aws_step?.covers ?? []).map(id => requests.find(r => r.id === id)).filter(Boolean).map(requestLabel);
  const tags = [].concat(node.properties?.aws_realizes ?? []);
  return [served.join(' + ') || 'no request', stage.title, `${numberOf(node, stage)} ${node.title ?? node.type} · ${tags.length ? partsText(tags) : 'not in the design'}`];
}

/** "serves R3 · Own cleaning lane" for a stage of the workflow, found by its step id or its card title
 *  (how decisions, reviews and versions name it); '' when it is not a contract stage. */
export function servesOf(graph, stage, requests = graph?.extra?.aws_contract?.requests ?? []) {
  const key = typeof stage === 'string' ? stage : stage?.id ?? stage?.title;
  const node = (graph?.nodes ?? []).find(n => n.properties?.aws_stage && (n.properties.aws_stage === key || n.title === key || n.title === stage?.title));
  const served = (node?.properties?.aws_step?.covers ?? []).map(id => requests.find(r => r.id === id)).filter(Boolean);
  return served.length ? `serves ${served.map(requestLabel).join(' + ')}` : '';
}
