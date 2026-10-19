// Native graph APIs, following Comfy-Org/ComfyUI_frontend's app.ts/API queue path.
// Kept independent of DOM/Comfy imports for small runnable regression checks.
import {analyzeGraph, findConverterPaths} from './graph_analysis.js';
import {setSignature} from './publish.js';
// Widgets that may hold secrets: hidden from the agent and never set by it. Names like
// max_tokens or credential_source are ordinary settings, not secrets.
const secret = /(?:password|passwd|api.?key|secret|authorization|bearer|(?:^|_)token$)/i;
const nodes = graph => graph.nodes ?? graph._nodes ?? [];
// The current frontend keeps links in a Map; older graphs used a plain object.
const links = graph => graph.links instanceof Map ? [...graph.links.values()] : Object.values(graph.links ?? {});
// Every node, including those packed inside subgraphs, with the id a run reports for it ("outer:inner").
export function deepNodes(graph, prefix = '') {
  return nodes(graph).flatMap(node => [
    {node, runId: `${prefix}${node.id}`},
    ...(node.isSubgraphNode?.() && node.subgraph ? deepNodes(node.subgraph, `${prefix}${node.id}:`) : []),
  ]);
}

/** Node definitions plus every subgraph that contains an output node, which therefore runs. */
export function withStageOutputs(graph, defs) {
  const extra = {};
  for (const node of nodes(graph)) {
    if (node.isSubgraphNode?.() && deepNodes(node.subgraph).some(({node: inner}) => defs[inner.type]?.output_node)) {
      extra[node.type] = {...(defs[node.type] ?? {}), output_node: true};
    }
  }
  return {...defs, ...extra};
}

// A contract step is a stage: a subgraph whose ports are the step's typed inputs and outputs.
// The skeleton (one stage per step, wired, a Stub inside, a Check per gate) is built on approval.
const SCAFFOLD = new Set(['AgentWorkflowStub', 'AgentWorkflowCheck']);
const humanize = id => id.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
export const stageNode = (graph, step) => nodes(graph).find(node => node.properties?.aws_stage === step);

/** Pack nodes into one stage subgraph for a contract step, titled "N · Step name". */
export function packIntoStage(graph, members, step, number) {
  const before = new Set(nodes(graph));
  graph.convertToSubgraph(new Set(members));
  const stage = nodes(graph).find(node => !before.has(node) && node.isSubgraphNode?.());
  if (!stage) throw Error(`Could not make a stage for step ${step}.`);
  stage.title = stage.subgraph.name = `${number} · ${humanize(step)}`;
  stage.properties = {...stage.properties, aws_stage: step, aws_stage_order: number};
  stage.setSize([260, 200]); // a stage card's size, so the layout right after packing is final
  return stage;
}

/** Each step's number: one more than the deepest step it takes from, so independent steps (one
 *  lane per source system) share a number and a column. Steps declaring no inputs run in order. */
export function stageNumbers(steps) {
  const number = {};
  steps.forEach((step, i) => {
    const takes = Object.values(step.takes ?? {});
    const from = takes.filter(source => source.includes('.')).map(source => number[source.split('.')[0]] ?? 0);
    number[step.id] = from.length ? Math.max(...from) + 1 : takes.length ? 1 : (i ? number[steps[i - 1].id] + 1 : 1);
  });
  return number;
}

const portType = (steps, source) => {
  if (!source.includes('.')) return source;
  const [step, port] = source.split('.');
  return steps.find(s => s.id === step)?.gives?.[port] ?? '*';
};

/** The node feeding a stage's output port, from inside the stage. */
function feederOf(subgraph, port) {
  const index = subgraph.outputs.findIndex(output => output.name === port);
  const id = subgraph.outputs[index]?.linkIds?.[0];
  const link = id == null ? null : subgraph.links.get?.(id) ?? subgraph.links[id];
  return link ? {node: subgraph.getNodeById(link.origin_id), slot: link.origin_slot} : null;
}

/** Point every Check watching an output port at what now feeds that port. */
function rewatch(subgraph, port) {
  const feeder = feederOf(subgraph, port);
  if (!feeder?.node) return;
  for (const check of nodes(subgraph).filter(n => n.type === 'AgentWorkflowCheck' && n.properties?.aws_watches === port)) feeder.node.connect(feeder.slot, check, 0);
}

const linkIn = (graph, id) => (id == null ? null : graph.links?.get?.(id) ?? graph.links?.[id]);

/** Scaffolding (a Stub's wires, a Check's input) is replaced freely; the user's own wires are not. */
function scaffoldLink(graph, target, slot) {
  const link = linkIn(graph, target.inputs?.[slot]?.link);
  return target.type === 'AgentWorkflowCheck' || graph.getNodeById?.(link?.origin_id)?.type === 'AgentWorkflowStub';
}

/** A Stub that no longer feeds any of the stage's outputs has been replaced: drop it, and let the
 *  Checks follow what feeds their outputs now. */
function dropReplacedStubs(subgraph) {
  for (const stub of nodes(subgraph).filter(n => n.type === 'AgentWorkflowStub')) {
    const feeds = (stub.outputs?.[0]?.links ?? []).map(id => linkIn(subgraph, id)).some(link => link && link.target_id === subgraph.outputNode?.id);
    if (!feeds) subgraph.remove(stub);
  }
  for (const output of subgraph.outputs ?? []) rewatch(subgraph, output.name);
}

export function addCheck(subgraph, check, says) {
  const node = globalThis.LiteGraph.createNode('AgentWorkflowCheck');
  subgraph.add(node);
  node.title = `Check · ${check.output}`;
  node.properties = {...node.properties, aws_watches: check.output};
  const set = (name, value) => { const widget = node.widgets?.find(w => w.name === name); if (widget && value != null) widget.value = String(value); };
  set('kind', check.kind);
  set('expect', check.value ?? '');
  set('says', says);
  rewatch(subgraph, check.output);
}

/** Build (or extend) the canvas skeleton for the approved steps: a stage per step with typed ports,
 *  wired from the steps it takes from, a Stub passing data through until it is built, and a Check
 *  for each gate. Existing stages are kept; a stage whose step is gone is marked, never deleted. */
export function buildSkeleton(graph, steps) {
  const created = [], notes = [], numbers = stageNumbers(steps);
  for (const step of steps) {
    let stage = stageNode(graph, step.id);
    // Existing work the step takes over (the user's nodes from before the contract), wiring kept.
    const adopted = (step.adopts ?? []).map(id => graph.getNodeById(id)).filter(node => node && !node.properties?.aws_stage && !node.properties?.aws_given);
    if (!stage && adopted.length) {
      stage = packIntoStage(graph, adopted, step.id, numbers[step.id]);
      if ((step.adopts ?? []).length > adopted.length) notes.push(`${stage.title}: some adopted nodes were not found at the top level.`);
    } else if (!stage) {
      const stub = globalThis.LiteGraph.createNode('AgentWorkflowStub');
      graph.add(stub);
      stage = packIntoStage(graph, [stub], step.id, numbers[step.id]);
      created.push(step.id);
    }
    stage.title = stage.subgraph.name = `${numbers[step.id]} · ${humanize(step.id)}`;
    stage.properties = {...stage.properties, aws_stage_order: numbers[step.id], aws_orphaned: false};
    const sg = stage.subgraph;
    const ensure = (ports, name, type, add) => {
      const port = ports.find(p => p.name === name);
      if (!port) add(name, type);
      else if (port.type !== type) notes.push(`${stage.title}: ${name} is ${port.type}, the contract now says ${type}; rewire or change the stage.`);
    };
    for (const [name, source] of Object.entries(step.takes ?? {})) ensure(sg.inputs, name, portType(steps, source), (n, t) => sg.addInput(n, t));
    for (const [name, type] of Object.entries(step.gives ?? {})) ensure(sg.outputs, name, type, (n, t) => sg.addOutput(n, t));
    const stub = created.includes(step.id) && nodes(sg).find(n => n.type === 'AgentWorkflowStub');
    if (stub && (!sg.inputs.length || !sg.outputs.length)) sg.remove(stub); // nothing to pass through: the stage starts empty
    else if (stub) {
      sg.inputNode.slots[0].connect(stub.inputs[0], stub);
      // The stub passes its input on: it can stand in for every output of the same type.
      sg.outputs.forEach((output, i) => { if (output.type === sg.inputs[0].type || output.type === '*') sg.outputNode.slots[i].connect(stub.outputs[0], stub); });
    }
    const check = step.gate?.check;
    if (check?.output && !nodes(sg).some(n => n.type === 'AgentWorkflowCheck' && n.properties?.aws_watches === check.output)) addCheck(sg, check, step.gate.says);
  }
  for (const step of steps) {
    for (const [name, source] of Object.entries(step.takes ?? {})) {
      if (!source.includes('.')) continue;
      const [from, port] = source.split('.'), a = stageNode(graph, from), b = stageNode(graph, step.id);
      const out = a?.outputs?.findIndex(o => o.name === port), into = b?.inputs?.findIndex(i => i.name === name);
      if (out >= 0 && into >= 0 && b.inputs[into].link == null) a.connect(out, b, into);
    }
  }
  for (const node of nodes(graph)) {
    if (node.properties?.aws_stage && !steps.some(s => s.id === node.properties.aws_stage)) {
      node.properties = {...node.properties, aws_orphaned: true};
      notes.push(`${node.title} is no longer a contract step: move what you need out of it, then remove it.`);
    }
  }
  arrangeGraph(graph);
  // What is already in place, so the agent builds only what is missing.
  const stages = steps.map(step => {
    const stage = stageNode(graph, step.id), sg = stage.subgraph;
    return {step: step.id, title: stage.title,
      takes: sg.inputs.map((p, i) => `${p.name}: ${p.type}${stage.inputs[i]?.link != null ? ' (wired)' : ' (connect at the top level)'}`),
      gives: sg.outputs.map(p => `${p.name}: ${p.type}`),
      inside: nodes(sg).map(n => (n.type === 'AgentWorkflowCheck' ? `Check on ${n.properties?.aws_watches}` : n.type === 'AgentWorkflowStub' ? 'Stub (input → outputs)' : n.title))};
  });
  return {created, notes, stages, how: 'Build each stage in one batch: add its nodes, wire them from "input" ports to "output" ports. Stages are already wired to each other; a Check follows its output and a Stub disappears once replaced.'};
}

const inputSlot = (node, name) => typeof name === 'number' ? name : node.inputs?.findIndex(input => input.name === name || input.widget?.name === name);

/** A tidy layout, positions only: data flows left to right in columns by depth; a node that
 *  feeds nothing and reads one node (a preview) sits under that node; notes go above. */
export function arrangeGraph(graph, {gapX = 200, gapY = 110} = {}) { // gapX leaves room for wire labels
  const isNote = node => /Note$/.test(node.type ?? '');
  const flow = nodes(graph).filter(node => !isNote(node));
  if (!flow.length) return;
  // Lay out by the height a node really needs (tables inside grow it), not a stale saved size.
  for (const node of flow) {
    const needed = node.computeSize?.()?.[1];
    if (Number.isFinite(needed) && needed > node.size[1]) node.size = [node.size[0], needed];
  }
  const ids = new Set(flow.map(node => node.id));
  const parents = new Map(flow.map(node => [node.id, []])), children = new Map(flow.map(node => [node.id, []]));
  for (const link of links(graph)) {
    if (ids.has(link.origin_id) && ids.has(link.target_id)) {
      parents.get(link.target_id).push(link.origin_id);
      children.get(link.origin_id).push(link.target_id);
    }
  }
  const under = new Map(), attached = new Set();
  for (const node of flow) {
    const from = [...new Set(parents.get(node.id))];
    const parent = from.length === 1 ? graph.getNodeById?.(from[0]) : null;
    // A preview hangs under what it shows; given nodes and stage cards keep their own place, except a
    // given preview of a given source (the data folder under the generator).
    const hangs = !node.properties?.aws_given || !!parent?.properties?.aws_given;
    if (!children.get(node.id).length && from.length === 1 && hangs && !node.isSubgraphNode?.()) {
      under.set(from[0], [...(under.get(from[0]) ?? []), node]);
      attached.add(node.id);
    }
  }
  const depth = new Map();
  const visit = (id, path = new Set()) => {
    if (depth.has(id)) return depth.get(id);
    if (path.has(id)) return 0; // a feedback loop: break it here
    path.add(id);
    const level = Math.max(-1, ...parents.get(id).map(parent => visit(parent, path))) + 1;
    path.delete(id);
    depth.set(id, level);
    return level;
  };
  const main = flow.filter(node => !attached.has(node.id));
  for (const node of main) visit(node.id);
  const columns = [];
  const stages = main.filter(node => node.properties?.aws_stage).sort((a, b) => (a.properties.aws_stage_order ?? 0) - (b.properties.aws_stage_order ?? 0));
  if (stages.length) {
    // Organized into stages: given sources, then one column per stage in step order, then the rest.
    const rest = main.filter(node => !stages.includes(node));
    const sources = rest.filter(node => node.properties?.aws_given && !parents.get(node.id).length);
    // Stages sharing a step number are parallel lanes (e.g. one per source system): one column.
    const lanes = [];
    for (const stage of stages) {
      const last = lanes.at(-1);
      if (last && last[0].properties.aws_stage_order === stage.properties.aws_stage_order) last.push(stage);
      else lanes.push([stage]);
    }
    columns.push(...(sources.length ? [sources] : []), ...lanes);
    const after = rest.filter(node => !sources.includes(node));
    if (after.length) columns.push(after);
  } else {
    for (const node of main) (columns[depth.get(node.id)] ??= []).push(node);
  }
  const x0 = Math.min(...flow.map(node => node.pos[0])), y0 = Math.min(...flow.map(node => node.pos[1]));
  const placedY = new Map();
  const inputY = node => {
    const ys = parents.get(node.id).map(id => placedY.get(id)).filter(Number.isFinite);
    return ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : node.pos[1];
  };
  let x = x0;
  for (const column of columns.filter(Boolean)) {
    column.sort((a, b) => !!b.properties?.aws_given - !!a.properties?.aws_given || inputY(a) - inputY(b));
    let top = y0, width = 0;
    for (const node of column) {
      for (const member of [node, ...(under.get(node.id) ?? [])]) {
        member.pos = [x, top];
        placedY.set(member.id, top);
        top += member.size[1] + gapY;
        width = Math.max(width, member.size[0]);
      }
    }
    x += width + gapX;
  }
  let noteX = x0;
  for (const note of nodes(graph).filter(isNote)) {
    note.pos = [noteX, y0 - note.size[1] - gapY];
    noteX += note.size[0] + gapX;
  }
  // A subgraph's own connectors: what comes in on the left of its first column, what goes out on the
  // right of its last, level with the top row.
  const place = (boundary, at) => { if (boundary?.pos) { boundary.pos[0] = at[0]; boundary.pos[1] = at[1]; } };
  place(graph.inputNode, [x0 - 260, y0]);
  place(graph.outputNode, [x - gapX + 80, y0]);
  // Organize inside every stage too.
  for (const node of flow) if (node.isSubgraphNode?.() && node.subgraph) arrangeGraph(node.subgraph, {gapX, gapY});
}

/** Each step's gate from the Check nodes inside its stage, on one run's outputs (keyed "stage:…:node"):
 *  passed when all pass; needs_user_review when the stage has no Check. */
export function gateResults(record, graph) {
  return nodes(graph).filter(node => node.properties?.aws_stage).map(stage => {
    const checks = deepNodes(stage.subgraph, `${stage.id}:`).filter(({node}) => node.type === 'AgentWorkflowCheck').map(({node, runId}) => ({title: node.title, result: record?.outputs?.[runId]?.aws_check?.[0]}));
    const status = !checks.length ? 'needs_user_review' : checks.some(c => !c.result) ? 'no_output' : checks.every(c => c.result.passed) ? 'passed' : 'failed';
    return {step: stage.properties.aws_stage, status, checks: checks.map(c => ({title: c.title, passed: c.result?.passed ?? null, evidence: c.result?.evidence ?? 'did not run'}))};
  });
}

/** Run outputs small enough for the agent: long texts shortened, long lists sampled, marked as such. */
export function compactOutputs(outputs, {maxText = 4000, maxItems = 20, budget = 400000} = {}) {
  const shrink = value => {
    if (typeof value === 'string') return value.length > maxText ? `${value.slice(0, maxText)}… [${value.length - maxText} more characters]` : value;
    if (Array.isArray(value)) return value.length > maxItems ? [...value.slice(0, maxItems).map(shrink), `… [${value.length - maxItems} more items]`] : value.map(shrink);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, shrink(item)]));
    return value;
  };
  const compact = shrink(outputs ?? {});
  return JSON.stringify(compact).length > budget && maxText > 500 ? compactOutputs(outputs, {maxText: 500, maxItems: 5, budget: Infinity}) : compact;
}

// Scoring against a hidden answer key is for the user only: the agent must never tune to it.
const USER_ONLY = new Set(['AgentWorkflowNayoseScore']);
export function agentOutputs(record) {
  return Object.fromEntries(Object.entries(record.outputs ?? {}).filter(([id]) => !USER_ONLY.has(record.prompt?.[2]?.[id]?.class_type)));
}

/** The most recent run of this workflow in /history, and whether it ran exactly the current graph. */
export function latestMatchingRun(history, workflowId, executable) {
  const runs = Object.values(history ?? {}).filter(run => run.prompt?.[3]?.extra_pnginfo?.workflow?.id === workflowId);
  const run = runs.at(-1);
  return run ? {run, current: JSON.stringify(run.prompt[2]) === executable} : null;
}

/** Evidence for deriving contract step states: the stages and the nodes inside them (a node's step
 *  is the stage it sits in), the saved workflow (for the architecture rules) and, when the latest run
 *  of this workflow ran exactly the current graph, each step's Check results on that run. */
export async function contractEvidence(app, api) {
  const graph = app.rootGraph ?? app.graph;
  const stages = nodes(graph).filter(node => node.properties?.aws_stage);
  const evidence = {
    nodes: [
      ...nodes(graph).filter(node => !node.properties?.aws_stage).map(node => ({id: String(node.id), title: node.title, class_type: node.type, step: null, given: !!node.properties?.aws_given, note: /Note$/.test(node.type ?? '')})),
      ...stages.flatMap(stage => deepNodes(stage.subgraph, `${stage.id}:`).filter(({node}) => !node.isSubgraphNode?.()).map(({node, runId}) => ({id: runId, title: node.title, class_type: node.type, step: stage.properties.aws_stage, scaffold: SCAFFOLD.has(node.type), fuzzy: node.type === 'AgentWorkflowFuzzyNode'}))),
    ],
    stages: stages.map(stage => ({step: stage.properties.aws_stage, title: stage.title, orphaned: !!stage.properties.aws_orphaned,
      takes: (stage.subgraph.inputs ?? []).map(p => [p.name, p.type]), gives: (stage.subgraph.outputs ?? []).map(p => [p.name, p.type])})),
    workflow: graph.serialize(),
    gate_results: {}, run: null,
  };
  const defs = await api.getNodeDefs();
  const analysis = analyzeGraph(graph.serialize(), withStageOutputs(graph, defs));
  // "No output node" is judged by the gates themselves: on an empty canvas it is progress, not drift.
  evidence.analysis = {outputs: analysis.outputs.map(o => o.title), problems: analysis.problems.filter(p => p.kind !== 'no_output_node').map(p => p.message)};
  const history = await api.fetchApi('/history?max_items=64').then(r => (r.ok ? r.json() : {}));
  const match = latestMatchingRun(history, graph.id, JSON.stringify((await app.graphToPrompt()).output));
  if (match) {
    evidence.run = {prompt_id: match.run.prompt[1], current: match.current};
    if (match.current && match.run.status?.completed) for (const gate of gateResults(match.run, graph)) evidence.gate_results[gate.step] = gate.status;
  }
  return evidence;
}

export function createCanvasBridge(app, api, {confirm, autoApproveRun = () => false} = {}) {
  let definitions, latestPrompt, submitted = false;
  // Node keys stay valid for the whole chat, so later batches can reference earlier nodes.
  const keys = Object.create(null);
  const catalog = async () => definitions ??= await api.getNodeDefs();
  // Wait (up to a minute) for this chat's run to finish, so the agent need not poll.
  const waitForRun = async () => {
    for (let waited = 0; latestPrompt && waited < 60000; waited += 1000) {
      const finished = await api.fetchApi(`/history/${encodeURIComponent(latestPrompt)}`).then(r => (r.ok ? r.json() : {}));
      if (finished[latestPrompt]) return;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  };
  // Where an operation works: inside its step's stage, or (without "stage") the top level, where only
  // stages and the user's given nodes live. Keys remember their stage.
  const scope = stage => {
    if (stage == null) return app.graph;
    if (typeof stage !== 'string') throw Error('"stage" is a contract step id.');
    const node = stageNode(app.graph, stage);
    if (!node) throw Error(`No stage for step "${stage}". Stages come from the approved contract; to add a step, propose a revised contract.`);
    return node.subgraph;
  };
  const resolve = (ref, op, added) => {
    const graph = scope(op.stage), key = added[ref] ?? keys[ref];
    if (key) {
      if ((key.stage ?? null) !== (op.stage ?? null)) throw Error(`Node "${ref}" is ${key.stage ? `in stage "${key.stage}"` : 'at the top level'}: give "stage": ${JSON.stringify(key.stage ?? null)}.`);
      return graph.getNodeById(key.id);
    }
    return (op.stage == null && stageNode(graph, ref)) || graph.getNodeById(ref);
  };
  const outputSlot = (node, name) => typeof name === 'number' ? name : node.outputs?.findIndex(output => output.name === name);
  const notFound = ref => Error(`Node not found: ${ref}. Read the workflow: nodes inside a stage are listed under it; give that "stage".`);

  async function edit(operations) {
    if (!Array.isArray(operations) || !operations.length || operations.length > 32) throw Error('Use 1–32 edit operations.');
    const graph = app.graph;
    const defs = await catalog();
    if (app.graph !== graph) throw Error('The active workflow changed; inspect it and retry.');
    const occupied = op => {
      try {
        if (op.to === 'output' || op.from === 'input') return false;
        const to = resolve(op.to, op, {}), slot = inputSlot(to, op.input), where = scope(op.stage);
        const from = resolve(op.from, op, {}), existing = linkIn(where, to?.inputs?.[slot]?.link);
        if (!existing || (existing.origin_id === from?.id && existing.origin_slot === outputSlot(from, op.output))) return false;
        return !scaffoldLink(where, to, slot);
      } catch { return false; }
    };
    // Removing a Stub is replacing scaffolding, not the user's work: no confirmation.
    const scaffold = op => { try { return resolve(op.node, op, {})?.type === 'AgentWorkflowStub'; } catch { return false; } };
    const destructive = operations.some(op => (op?.op === 'remove' && !scaffold(op)) || op?.op === 'disconnect' || (op?.op === 'connect' && occupied(op)));
    const beforeApproval = JSON.stringify(app.graph.serialize());
    if (destructive) {
      if (!await confirm?.('Change existing connections or remove nodes?', JSON.stringify(operations,null,2))) throw Error('Destructive canvas edits were not authorized.');
      if (app.graph !== graph || beforeApproval !== JSON.stringify(app.graph.serialize())) throw Error('Canvas changed during approval; inspect it and retry.');
    }
    const snapshot = structuredClone(app.graph.serialize()), added = Object.create(null);
    // Use the same transaction events as native paste/multi-node commands.
    app.canvas.emitBeforeChange();
    try {
      for (const op of operations) {
        if (!op || typeof op !== 'object') throw Error('Invalid edit operation.');
        if (op.step != null) throw Error('Nodes belong to the stage they are in: use "stage": "<step id>" instead of "step".');
        const where = scope(op.stage);
        if (op.op === 'add') {
          if (op.stage == null) throw Error('Add nodes inside a stage: give "stage": "<step id>". The top level holds only the stages and your given nodes.');
          if (typeof op.key !== 'string' || !op.key || Object.hasOwn(added,op.key)) throw Error('New nodes need unique nonempty keys.');
          if (Object.hasOwn(keys,op.key)) throw Error(`Key "${op.key}" already names a node in this chat; reference it, or use a new key.`);
          if (!Object.hasOwn(defs,op.class_type)) throw Error(`Node is not installed: ${op.class_type}`);
          const node = globalThis.LiteGraph.createNode(op.class_type);
          if (!node) throw Error(`Cannot create native node: ${op.class_type}`);
          if (op.title != null) {if (typeof op.title !== 'string' || op.title.length > 200) throw Error('Invalid node title.'); node.title = op.title;}
          const right = nodes(where).reduce((x,n) => Math.max(x,n.pos[0]+n.size[0]),0);
          const position = op.position ?? [right+80,80];
          if (!Array.isArray(position) || position.length !== 2 || !position.every(Number.isFinite)) throw Error('Invalid node position.');
          node.pos = position;
          where.add(node);
          added[op.key] = {stage: op.stage, id: node.id};
        } else if (op.op === 'move' || op.op === 'title') {
          const node = resolve(op.node, op, added);
          if (!node) throw notFound(op.node);
          if (op.op === 'move') {
            if (!Array.isArray(op.position)||op.position.length!==2||!op.position.every(Number.isFinite)) throw Error('Invalid node position.');
            node.pos=op.position;
          } else {
            if (typeof op.title!=='string'||op.title.length>200) throw Error('Invalid node title.');
            node.title=op.title;
          }
        } else if (op.op === 'widget') {
          const node = resolve(op.node, op, added), widget = node?.widgets?.find(w => w.name === op.name);
          if (!widget || secret.test(op.name)) throw Error(`Widget unavailable or protected: ${op.name}`);
          const spec = defs[node.type]?.input, schema = spec?.required?.[op.name] ?? spec?.optional?.[op.name];
          const type = schema?.[0], options = schema?.[1] ?? {};
          const choices=Array.isArray(type)?type:type==='COMBO'?options.options:undefined;
          if (Array.isArray(choices) && !choices.includes(op.value)) throw Error(`Invalid native choice for ${op.name}`);
          if (['INT','FLOAT'].includes(type) && (!Number.isFinite(op.value) || (type === 'INT' && !Number.isInteger(op.value)) || (options.min != null && op.value < options.min) || (options.max != null && op.value > options.max))) throw Error(`Invalid native number for ${op.name}`);
          if (type === 'BOOLEAN' && typeof op.value !== 'boolean') throw Error(`Expected boolean: ${op.name}`);
          if (type === 'STRING' && (typeof op.value !== 'string' || op.value.length > 65536)) throw Error(`Expected bounded text: ${op.name}`);
          if (!schema || !['string','number','boolean'].includes(typeof op.value)) throw Error(`Unsupported widget value: ${op.name}`);
          widget.value = op.value;
          widget.callback?.(op.value,app.canvas,node);
        } else if (op.op === 'connect') {
          // Inside a stage, "input" is the stage's input ports and "output" its output ports, by name.
          if (op.stage != null && op.from === 'input') {
            const to = resolve(op.to, op, added), port = where.inputs.findIndex(p => p.name === op.output), slot = inputSlot(to, op.input);
            if (port < 0) throw Error(`Stage ${op.stage} has no input port ${op.output}; its ports: ${where.inputs.map(p => p.name).join(', ') || 'none'}.`);
            if (!to?.inputs?.[slot]) throw Error('Invalid connection target.');
            if (to.inputs[slot].link != null && !destructive && !scaffoldLink(where, to, slot)) throw Error('Replacing an occupied input needs fresh approval.');
            if (!where.inputNode.slots[port].connect(to.inputs[slot], to)) throw Error('Native port types do not connect.');
          } else if (op.stage != null && op.to === 'output') {
            const from = resolve(op.from, op, added), port = where.outputs.findIndex(p => p.name === op.input), slot = outputSlot(from, op.output);
            if (port < 0) throw Error(`Stage ${op.stage} has no output port ${op.input}; its ports: ${where.outputs.map(p => p.name).join(', ') || 'none'}.`);
            if (!from?.outputs?.[slot]) throw Error('Invalid connection source.');
            if (!where.outputNode.slots[port].connect(from.outputs[slot], from)) throw Error('Native port types do not connect.');
            dropReplacedStubs(where); // the stage's Checks follow what now feeds the port
          } else {
            const from = resolve(op.from, op, added), to = resolve(op.to, op, added), out = from && outputSlot(from, op.output);
            if (!from || !to || !Number.isInteger(out) || !from.outputs?.[out]) throw Error('Invalid connection source.');
            const slot = inputSlot(to,op.input);
            if (!Number.isInteger(slot) || slot < 0 || !to.inputs?.[slot]) throw Error('Invalid connection target.');
            const existing = linkIn(where, to.inputs[slot].link);
            if (existing && existing.origin_id === from.id && existing.origin_slot === out) continue; // already wired
            if (existing && !destructive && !scaffoldLink(where, to, slot)) throw Error('Replacing an occupied input needs fresh approval.');
            if (!from.connect(out,to,slot)) throw Error('Native port types do not connect.');
          }
        } else if (op.op === 'remove' || op.op === 'disconnect') {
          const node = resolve(op.node, op, added);
          if (!node) throw notFound(op.node);
          if (op.op === 'remove') {
            if (node.properties?.aws_stage && !node.properties.aws_orphaned) throw Error('A stage is a contract step: to drop it, propose a revised contract without that step.');
            where.remove(node);
          } else {
            const slot = inputSlot(node,op.input);
            if (!Number.isInteger(slot) || slot < 0 || !node.inputs?.[slot]) throw Error('Input not found.');
            node.disconnectInput(slot);
          }
        } else if (op.op === 'signature') {
          // The workflow's API: inputs are widgets on top-level nodes, outputs are top-level stages.
          if (op.stage != null) throw Error('The signature is set at the top level (no "stage").');
          const inputs = (op.inputs ?? []).map(({node: ref, widget}) => {
            const node = resolve(ref, op, added);
            if (!node || node.properties?.aws_stage) throw Error(`An API input is a widget on a top-level node (such as a given data folder), not a stage: ${ref}.`);
            if (!node.widgets?.some(w => w.name === widget)) throw Error(`${node.title} has no widget ${widget}.`);
            return {node: node.id, widget};
          });
          const outputs = (op.outputs ?? []).map(ref => { const node = resolve(ref, op, added); if (!node) throw notFound(ref); return node.id; });
          if (!outputs.length) throw Error('Name at least one output: the stage (step id) whose results the API returns.');
          setSignature(app.graph, inputs, outputs);
        } else if (op.op !== 'arrange') throw Error(`Unsupported operation: ${op.op}`);
      }
      Object.assign(keys, added);
      // Keep the canvas readable without the agent computing coordinates: re-lay out after
      // structural edits (and on request); the whole batch is still one undo step.
      if (operations.some(op => ['add', 'connect', 'remove', 'arrange'].includes(op.op))) arrangeGraph(app.graph);
      app.graph.setDirtyCanvas(true,true);
      return {added: Object.fromEntries(Object.entries(added).map(([key, {id}]) => [key, id])), message:'Applied native canvas edits. This tool did not submit a run; native Auto Queue settings still apply.'};
    } catch (error) {
      // Restore the current graph, not a new workflow/tab or root graph.
      app.graph.configure(snapshot);
      throw Error(`Edits rolled back: ${error.message}`);
    } finally {
      app.canvas.emitAfterChange();
    }
  }

  // One node as the agent reads it; a stage also lists its ports and the nodes and wires inside.
  const describe = node => ({id:node.id,class_type:node.type,title:node.title,mode:node.mode,
    ...(node.properties?.aws_given ? {given:true} : {}),
    inputs:(node.inputs??[]).map((input,index) => ({index,name:input.name,type:input.type,link:input.link})),
    outputs:(node.outputs??[]).map((output,index) => ({index,name:output.name,type:output.type})),
    widgets:(node.widgets??[]).filter(w => !['button','aws_table_view','aws_plan_view','aws_stage_card'].includes(w.type)).map(w => ({name:w.name,value:secret.test(w.name)?'[redacted]':(['string','number','boolean'].includes(typeof w.value)?w.value:null)})),
    ...(node.properties?.aws_stage ? {stage:{step:node.properties.aws_stage, ...(node.properties.aws_orphaned ? {orphaned:true} : {}),
      nodes:nodes(node.subgraph).map(describe), links:links(node.subgraph).map(link => ({from:link.origin_id,output:link.origin_slot,to:link.target_id,input:link.target_slot}))}}
      : node.isSubgraphNode?.() ? {subgraph:{name:node.subgraph?.name,nodes:nodes(node.subgraph ?? {}).map(inner => ({id:inner.id,class_type:inner.type,title:inner.title}))}} : {}),
  });

  async function handle(action, args={}) {
    if (action === 'read') return {
      selected: Object.values(app.canvas.selected_nodes ?? {}).map(node => node.id),
      nodes: nodes(app.graph).map(describe),
      links: links(app.graph).map(link => ({from:link.origin_id,output:link.origin_slot,to:link.target_id,input:link.target_slot})),
    };
    if (action === 'catalog') {
      const defs = await catalog();
      if (args.class_types) {
        if (!Array.isArray(args.class_types) || args.class_types.length > 20) throw Error('Request at most 20 node schemas.');
        return {schemas:Object.fromEntries(args.class_types.map(name => {if (!Object.hasOwn(defs,name)) throw Error(`Node is not installed: ${name}`);return [name,defs[name]];}))};
      }
      const search = (text, limit) => {
        const query = String(text??'').toLowerCase();
        const matches = Object.entries(defs).filter(([name,d]) => `${name} ${d.display_name??''} ${d.category??''} ${d.description??''} ${JSON.stringify([d.search_aliases,d.input,d.output,d.output_name])}`.toLowerCase().includes(query));
        return {total:matches.length,nodes:matches.slice(0,limit).map(([name,d]) => ({class_type:name,title:d.display_name??name,category:d.category,description:d.description}))};
      };
      // Several searches in one call save agent turns.
      if (Array.isArray(args.queries)) return {results:Object.fromEntries(args.queries.slice(0,8).map(q => [q, search(q, 20)]))};
      return search(args.query, 60);
    }
    if (action === 'edit') return edit(args.operations);
    if (action === 'harden') {
      // Freezing a fuzzy step's latest plan into deterministic nodes is the user's call.
      const node = resolve(args.node, {stage: args.stage}, {});
      if (node?.type !== 'AgentWorkflowFuzzyNode') throw Error('Name a Fuzzy node and the stage it is in.');
      if (!node.properties?.aws_plan) throw Error('Run the workflow first so the model plans this step.');
      if (!await confirm?.('Harden this step?', `Freeze the latest plan of “${node.title}” into fixed nodes. Later runs call no model; undo or Versions restore it.`)) throw Error('The user did not approve hardening.');
      const {hardenStep} = await import('./fuzzy_steps.js'); // a browser-only module: loaded when needed
      const step = await hardenStep(node);
      return {hardened: true, nodes: step ? nodes(step.subgraph).map(n => n.type) : [], message: 'Hardened: this step no longer calls the model. Run again to verify it.'};
    }
    if (action === 'skeleton') {
      if (!Array.isArray(args.steps)) throw Error('Give the approved steps.');
      app.canvas.emitBeforeChange();
      try { const result = buildSkeleton(app.graph, args.steps); app.graph.setDirtyCanvas(true, true); return result; } finally { app.canvas.emitAfterChange(); }
    }
    if (action === 'run') {
      // Compare what would execute, not layout: node sizes can settle after a render.
      const executable = async () => JSON.stringify((await app.graphToPrompt()).output);
      const before = await executable();
      if (!autoApproveRun() && !await confirm?.('Run the current ComfyUI workflow?', String(args.reason??''))) throw Error('Workflow execution was not authorized.');
      const queue = await api.getQueue();
      if (before !== await executable()) throw Error('Workflow changed before submission; inspect it again before running.');
      if (queue.Running?.length || queue.Pending?.length || app.processingQueue) throw Error('Native queue is busy; wait before starting a chat-owned run.');
      latestPrompt = null; submitted = false;
      // app.queuePrompt does not return the server's prompt_id, so record what
      // api.queuePrompt returns while (and only while) this native Run is in flight.
      const ids = [], queuePrompt = api.queuePrompt;
      api.queuePrompt = async (...args) => {const response = await queuePrompt.apply(api,args); if (response?.prompt_id) ids.push(response.prompt_id); return response;};
      let accepted;
      try { accepted = await app.queuePrompt(0,1); } finally { api.queuePrompt = queuePrompt; }
      if (accepted === false || !ids.length) throw Error('Native ComfyUI validation rejected the workflow. Inspect execution_results and the native error panel.');
      if (ids.length !== 1) throw Error(`Expected one native submission but saw ${ids.length}; inspect the queue before continuing.`);
      latestPrompt = ids[0]; submitted = true;
      return {submitted:true,prompt_id:latestPrompt,message:'Submitted through native Run; inspect execution_results before claiming success.'};
    }
    if (action === 'analyze') return analyzeGraph((app.rootGraph ?? app.graph).serialize(), withStageOutputs(app.rootGraph ?? app.graph, await catalog()));
    if (action === 'converters') {
      if (typeof args.from_type !== 'string' || typeof args.to_type !== 'string') throw Error('Give from_type and to_type.');
      return {paths: findConverterPaths(await catalog(), args.from_type, args.to_type)};
    }
    if (action === 'contract_approve') return {approved: await confirm?.(String(args.title ?? 'Approve this workflow contract?'), String(args.text ?? '')) === true};
    if (action === 'tool_approve') return {approved: await confirm?.(String(args.title ?? 'Approve this tool?'), String(args.text ?? '')) === true};
    if (action === 'contract_evidence') return contractEvidence(app, api);
    if (action === 'gates') {
      if (!latestPrompt) throw Error('Run the workflow first; gates are checked against the latest run.');
      await waitForRun();
      const response = await api.fetchApi(`/history/${encodeURIComponent(latestPrompt)}`);
      if (!response.ok) throw Error('Cannot read native execution history.');
      const record = (await response.json())[latestPrompt];
      if (!record?.status?.completed) throw Error('The latest run has not completed yet.');
      return {prompt_id: latestPrompt, gates: gateResults(record, app.graph)};
    }
    if (action === 'results') {
      await waitForRun();
      const queue = await api.getQueue();
      const result = {submitted,prompt_id:latestPrompt??null,queue,validation_errors:app.extensionManager?.lastNodeErrors??null,execution_error:app.extensionManager?.lastExecutionError??null};
      if (latestPrompt) {
        const response = await api.fetchApi(`/history/${encodeURIComponent(latestPrompt)}`);
        if (!response.ok) throw Error('Cannot read native execution history.');
        const record = (await response.json())[latestPrompt];
        if (record) {
          result.status=record.status; result.outputs=compactOutputs(agentOutputs(record));
          result.image_previews=[];
          for (const output of Object.values(agentOutputs(record))) {
            for (const image of output.images??[]) {
              if (result.image_previews.length >= 2) break;
              const params = new URLSearchParams({filename:image.filename,subfolder:image.subfolder??'',type:image.type??'output'});
              const preview = await api.fetchApi(`/view?${params}`);
              const blob = await preview.blob();
              if (!preview.ok || !['image/png','image/jpeg','image/webp'].includes(blob.type) || blob.size > 262144) continue;
              const bytes = new Uint8Array(await blob.arrayBuffer());
              let binary='';for (let i=0;i<bytes.length;i+=8192) binary += String.fromCharCode(...bytes.subarray(i,i+8192));
              result.image_previews.push({mimeType:blob.type,data:btoa(binary)});
            }
          }
        }
      }
      return result;
    }
    throw Error('Unsupported native canvas action.');
  }
  return {handle,dispose:()=>{}};
}
