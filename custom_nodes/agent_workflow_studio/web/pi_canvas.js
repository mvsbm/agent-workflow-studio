// Native graph APIs, following Comfy-Org/ComfyUI_frontend's app.ts/API queue path.
// Kept independent of DOM/Comfy imports for small runnable regression checks.
import {analyzeGraph, findConverterPaths} from './graph_analysis.js';
import {outputNode, refreshApp, setSignature} from './publish.js';
import {typeOfKind} from './standards_core.js';
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
const SCAFFOLD = new Set(['AgentWorkflowStub', 'AgentWorkflowCheck', 'AgentWorkflowApiOutput']);
const humanize = id => id.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
/** Display state kept in the workflow (stage cards, run outputs shown on nodes, the verification) is
 *  not a change the user made: write it without adding an undo step, by folding it into ComfyUI's
 *  tracked state. (User edits are recorded on mouse-up and agent edits as they apply, so nothing of
 *  theirs is waiting here; what may be is ComfyUI's own re-layout, which is not a step either.) */
export function quietly(app, write) {
  const tracker = app.extensionManager?.workflow?.activeWorkflow?.changeTracker;
  write();
  if (tracker?.activeState && app.rootGraph) tracker.activeState = JSON.parse(JSON.stringify(app.rootGraph.serialize()));
}

/** The studio's scope for the agent: data and text work. Our own nodes, and ComfyUI's text and
 *  utility nodes that carry no image, video, audio or model data. The rest stays installed (a person
 *  can still add it by hand); the agent neither finds nor adds it. */
const MEDIA = /^(IMAGE|LATENT|MASK|MODEL|VAE|CLIP|CLIP_VISION|CLIP_VISION_OUTPUT|CONDITIONING|CONTROL_NET|STYLE_MODEL|UPSCALE_MODEL|VIDEO|AUDIO|BOUNDING_BOX|NOISE|SAMPLER|SIGMAS|GUIDER|MESH|VOXEL)$/;
export function inStudioScope(def) {
  // The studio's own pack (exactly it or a module of it; not another pack whose name only starts so).
  if (/^custom_nodes\.agent_workflow_studio(\.|$)/.test(String(def?.python_module ?? ''))) return true;
  if (!/^(text|utilities)(\/|$)/.test(def?.category ?? '')) return false;
  const types = [...Object.values({...def.input?.required, ...def.input?.optional}).map(spec => spec?.[0]), ...(def.output ?? [])];
  return !types.some(type => typeof type === 'string' && MEDIA.test(type));
}
const outOfScope = name => Error(`${name} is outside this studio's scope (data and text work), so you cannot add it; a person can add it by hand.`);

/** Promote an inner node's setting to its stage card, the way ComfyUI promotes a widget: a stage
 *  input wired to that widget (the card then shows and sets it; the run uses the card's value). */
export function promoteWidget(stage, node, name) {
  const sg = stage.subgraph, input = node.inputs?.find(i => i.widget?.name === name);
  if (!input) throw Error(`${node.title ?? node.type} has no setting "${name}" to put on the stage card.`);
  if (node.isInputConnected(node.inputs.indexOf(input))) return null;  // already wired (or promoted)
  const label = sg.inputs.some(i => i.name === name) ? `${name}_${node.id}` : name;
  sg.addInput(label, input.type);
  // The card shows the setting as the node does ("Match at"), not its internal name.
  const shown = input.label || input.localized_name || node.widgets?.find(w => w.name === name)?.label;
  if (shown && shown !== label) sg.inputs.at(-1).label = shown;
  if (!sg.inputNode.slots.at(-1).connect(input, node)) throw Error(`Could not put ${name} on the stage card.`);
  // The card's own input and widget carry the shown name too: App mode titles an input by it.
  if (shown && shown !== label) {
    for (const item of [stage.inputs?.find(i => i.name === label), stage.widgets?.find(w => w.name === label)]) if (item) item.label = shown;
  }
  return label;
}

export const stageNode = (graph, step) => nodes(graph).find(node => node.properties?.aws_stage === step);

/** Pack nodes into one stage subgraph for a contract step, titled "N · Step name". */
export function packIntoStage(graph, members, step, number, gives = {}) {
  const before = new Set(nodes(graph));
  graph.convertToSubgraph(new Set(members));
  const stage = nodes(graph).find(node => !before.has(node) && node.isSubgraphNode?.());
  if (!stage) throw Error(`Could not make a stage for step ${step}.`);
  // ComfyUI names a new stage's ports after the inner outputs; the design names them (a dataset's
  // name in this workflow, e.g. customers for Cluster guard's clusters): each output whose type
  // matches exactly one declared port takes that port's name.
  stage.subgraph.outputs.forEach((output, i) => {
    const named = Object.entries(gives).filter(([, type]) => type === output.type);
    if (named.length !== 1 || output.name === named[0][0]) return;
    output.name = named[0][0];
    output.label = undefined;
    if (stage.outputs?.[i]) Object.assign(stage.outputs[i], {name: named[0][0], label: undefined});
  });
  stage.title = stage.subgraph.name = `${number} · ${humanize(step)}`;
  stage.properties = {...stage.properties, aws_stage: step, aws_stage_order: number};
  stage.setSize([320, 240]); // a stage card's size (room for its design sentence), so the layout right after packing is final
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

// A promised input's kind as a socket type, and the node that holds it at the top level.
// A data folder (claim check) gets the Data folder node: a folder list and an Upload button, also in App mode.
// A promised input's socket and its App-mode input node, from the type standard (standards.js).
// The widget an input node takes its value in.
const valueWidget = node => (node.type === 'AgentWorkflowDataFolder' ? 'folder' : 'value');
const inputKind = kind => {
  const type = typeOfKind(String(kind).toLowerCase());
  return [type?.socket ?? 'STRING', type?.app_input ?? 'PrimitiveString'];
};

const portType = (steps, source, promises) => {
  if (source.startsWith('input.')) return inputKind(promises?.inputs?.find(i => i.name === source.slice(6))?.kind)[0];
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

/** The columns last seen flowing out of a stage's output port (from its feeding node's sample table). */
export function observedFields(subgraph, port) {
  const tables = feederOf(subgraph, port)?.node?.properties?.aws_tables ?? [];
  const sample = [...tables].reverse().find(t => / · sample$/.test(t.title ?? ''));
  return sample?.columns ?? null;
}

/** The dataset a stage's output port carried on the last run (class, name, file, rows), if any. */
export function outputDataset(subgraph, port) {
  const feeder = feederOf(subgraph, port);
  return (feeder?.node?.properties?.aws_datasets ?? []).find(([slot]) => slot === feeder.slot)?.[1] ?? null;
}

/** Point every Check watching an output port at what now feeds that port. */
function rewatch(subgraph, port) {
  const feeder = feederOf(subgraph, port);
  if (!feeder?.node) return;
  for (const watcher of nodes(subgraph).filter(n => SCAFFOLD.has(n.type) && n.type !== 'AgentWorkflowStub' && n.properties?.aws_watches === port)) feeder.node.connect(feeder.slot, watcher, 0);
}

const linkIn = (graph, id) => (id == null ? null : graph.links?.get?.(id) ?? graph.links?.[id]);
// The link into a node's input slot. A stage (SubgraphNode) overrides getInputLink with the link
// inside it, so the base LGraphNode one is called: the wire into the card on this graph.
const inputLink = (node, slot) => (node?.inputs?.[slot] ? window.LiteGraph.LGraphNode.prototype.getInputLink.call(node, slot) : null);

// Plain copies: a link's fields are getters, which a spread ({...link}) would not copy.
const allLinks = graph => [...(graph.links?.values?.() ?? Object.values(graph.links ?? {}))].filter(Boolean)
  .map(l => ({origin_id: String(l.origin_id), origin_slot: l.origin_slot, target_id: String(l.target_id), target_slot: l.target_slot}));

/** Move the user's top-level nodes into a built stage, keeping their ids and wiring: links among them
 *  stay, a link into or out of the stage becomes a link inside it. Refused (null, nothing changed)
 *  when they are wired to anything else at the top level; returns the wires it could not remake. */
export function adoptIntoStage(graph, stage, adopted) {
  // Ids compare as text: nodes and links do not always agree on number vs string.
  const sg = stage.subgraph, ids = new Set(adopted.map(n => String(n.id))), key = String;
  const touching = allLinks(graph).filter(l => ids.has(l.origin_id) || ids.has(l.target_id));
  if (!touching.every(l => [l.origin_id, l.target_id].every(id => ids.has(id) || id === key(stage.id)))) return null;
  // Where the stage's input ports lead inside, read before anything moves.
  const inside = allLinks(sg).filter(l => l.origin_id === key(sg.inputNode?.id));
  const clones = new Map();
  for (const node of adopted) {
    const data = node.serialize();
    graph.remove(node);
    const clone = globalThis.LiteGraph.createNode(node.type);
    clone.id = node.id;  // a contract adopts nodes by id
    sg.add(clone);
    Object.assign(clone, {title: data.title, pos: [...data.pos]});
    clone.properties = {...clone.properties, ...data.properties};
    (data.widgets_values ?? []).forEach((value, i) => { if (clone.widgets?.[i]) clone.widgets[i].value = value; });
    clones.set(key(node.id), clone);
  }
  const unwired = [];
  const wire = (from, slot, to, input) => { if (!from || !to || !from.connect(slot, to, input)) unwired.push(`${from?.title ?? '?'} → ${to?.title ?? '?'}`); };
  for (const l of touching) {
    const from = clones.get(l.origin_id), to = clones.get(l.target_id);
    if (from && to) wire(from, l.origin_slot, to, l.target_slot);
    else if (from) for (const t of inside.filter(i => i.origin_slot === l.target_slot)) wire(from, l.origin_slot, sg.getNodeById(t.target_id), t.target_slot);
    else {
      const feeder = feederOf(sg, sg.outputs[l.origin_slot]?.name);
      wire(feeder?.node, feeder?.slot, to, l.target_slot);
    }
  }
  return unwired;
}

/** Scaffolding (a Stub's wires, a Check's input) is replaced freely; the user's own wires are not. */
function scaffoldLink(graph, target, slot) {
  const link = inputLink(target, slot);
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
export function buildSkeleton(graph, steps, {promises, design, intent, meta} = {}) {
  const created = [], notes = [];
  // Titles come from the approved design (the HLD) when given; numbering mirrors it otherwise.
  const numbers = design ? Object.fromEntries(design.map(d => [d.id, Number(/^(\d+)/.exec(d.title)?.[1] ?? 1)])) : stageNumbers(steps);
  const sentence = Object.fromEntries((design ?? []).map(d => [d.id, d.sentence]));
  const blackBox = Object.fromEntries((design ?? []).map(d => [d.id, d.description ?? d.sentence]));
  for (const step of steps) {
    let stage = stageNode(graph, step.id);
    // Existing work the step takes over (the user's nodes from before the contract), wiring kept.
    const adopted = (step.adopts ?? []).map(id => graph.getNodeById(id)).filter(node => node && !node.properties?.aws_stage && !node.properties?.aws_given);
    if (!stage && adopted.length) {
      stage = packIntoStage(graph, adopted, step.id, numbers[step.id], step.gives);
      if ((step.adopts ?? []).length > adopted.length) notes.push(`${stage.title}: some adopted nodes were not found at the top level.`);
    } else if (stage && adopted.length) {
      const titles = adopted.map(n => n.title).join(', ');
      const unwired = adoptIntoStage(graph, stage, adopted);
      if (unwired?.length) notes.push(`${stage.title}: took over ${titles}; could not rewire ${unwired.join(', ')} (types differ): connect them inside the stage.`);
      else if (unwired) notes.push(`${stage.title}: took over ${titles}, wiring kept.`);
      else notes.push(`${stage.title}: could not take over ${titles}: they are wired to nodes outside this stage. Rewire or adopt those too.`);
    } else if (!stage) {
      const stub = globalThis.LiteGraph.createNode('AgentWorkflowStub');
      graph.add(stub);
      stage = packIntoStage(graph, [stub], step.id, numbers[step.id]);
      created.push(step.id);
    }
    stage.title = stage.subgraph.name = `${numbers[step.id]} · ${humanize(step.id)}`;
    // A built stage whose design sentence changed is stale until its Checks pass again.
    const before = stage.properties?.aws_design, now = sentence[step.id];
    const stale = Boolean(stage.properties?.aws_stale) || (!created.includes(step.id) && before && now && before !== now);
    if (stale && !stage.properties?.aws_stale) notes.push(`${stage.title}: its design changed; recheck what is inside it.`);
    // The HLD lives on the stage itself: its step, and its sentence as the subgraph's native description.
    stage.properties = {...stage.properties, aws_stage_order: numbers[step.id], aws_orphaned: false, ...(now ? {aws_design: now} : {}), aws_stale: stale, aws_step: step};
    if (now) stage.subgraph.description = blackBox[step.id];
    const sg = stage.subgraph;
    const ensure = (ports, name, type, add) => {
      const port = ports.find(p => p.name === name);
      if (!port) add(name, type);
      else if (port.type !== type) notes.push(`${stage.title}: ${name} is ${port.type}, the contract now says ${type}; rewire or change the stage.`);
    };
    for (const [name, source] of Object.entries(step.takes ?? {})) ensure(sg.inputs, name, portType(steps, source, promises), (n, t) => sg.addInput(n, t));
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
  // The API's outputs (the contract's promised outputs): a top-level output node per promise, wired from
  // the stage that delivers it. App mode shows them; the API returns them by the promised name.
  for (const promised of (promises?.outputs ?? []).filter(o => o.from)) {
    const [step, port] = promised.from.split('.'), stage = stageNode(graph, step);
    const slot = stage?.outputs?.findIndex(o => o.name === port);
    if (slot == null || slot < 0) continue;
    let node = nodes(graph).find(n => n.properties?.aws_output === promised.name);
    if (!node) {
      node = globalThis.LiteGraph.createNode('AgentWorkflowApiOutput');
      graph.add(node);
      node.title = `Output · ${promised.name}`;
      const name = node.widgets?.find(w => w.name === 'name');
      if (name) name.value = promised.name;
    }
    node.properties = {...node.properties, aws_output: promised.name, aws_output_from: promised.from};
    if (node.inputs?.[0]?.link == null) stage.connect(slot, node, 0);
  }
  // The API's inputs (the contract's promised inputs) as top-level nodes the stages take from.
  const inputs = {};
  for (const input of promises?.inputs ?? []) {
    // An input may be an existing top-level node (the user's given data), adopted rather than duplicated.
    const adopted = input.node != null && graph.getNodeById(input.node);
    const widget = input.widget ?? 'value', spec = adopted?.constructor?.nodeData?.input ?? {};
    // Only a widget the node takes as an input can be set by an API call (not one that only shows a value).
    if (adopted && !(widget in {...spec.required, ...spec.optional})) {
      notes.push(`Input ${input.name}: ${adopted.title} (node ${adopted.id}) has no input widget "${widget}" an API call could set; amend the contract to adopt the node that holds the value (its widget, e.g. a folder or seed), or drop node/widget to get a new input node.`);
      inputs[input.name] = adopted;  // the data still flows from it; it is just not part of the API
      continue;
    }
    if (adopted && !adopted.properties?.aws_stage) adopted.properties = {...adopted.properties, aws_input: input.name, aws_input_widget: widget};
    let node = nodes(graph).find(n => n.properties?.aws_input === input.name);
    if (!node) {
      node = globalThis.LiteGraph.createNode(inputKind(input.kind)[1]);
      graph.add(node);
      node.title = `Input · ${input.name}`;
      node.properties = {...node.properties, aws_input: input.name};
      const widget = node.widgets?.find(w => w.name === valueWidget(node));
      if (widget && input.example != null) widget.value = input.example;
    }
    inputs[input.name] = node;
  }
  for (const step of steps) {
    for (const [name, source] of Object.entries(step.takes ?? {})) {
      if (source.startsWith('input.')) {
        const node = inputs[source.slice(6)], b = stageNode(graph, step.id), into = b?.inputs?.findIndex(i => i.name === name);
        // The output that fits the stage's port (an adopted node may have several).
        const out = Math.max(0, node?.outputs?.findIndex(o => o.type === b?.inputs?.[into]?.type) ?? 0);
        if (node && into >= 0 && b.inputs[into].link == null) node.connect(out, b, into);
        continue;
      }
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
  // The signature (App mode): the promised inputs in, the stages delivering promised outputs out.
  // Each promise's output node, so App mode shows the result natively.
  const outputs = [...new Set((promises?.outputs ?? []).filter(o => o.from).map(o => outputNode(graph, o.from)?.id).filter(id => id != null))];
  const settable = node => node.properties?.aws_input_widget ?? valueWidget(node);
  // A Data folder's Upload button goes to App mode too (it is UI only: not part of the API).
  // Labelled in the contract's words (its description, else its name), so App mode reads as the user's.
  const label = name => { const p = (promises?.inputs ?? []).find(i => i.name === name), text = p?.description || name.replace(/_/g, ' '); return text.charAt(0).toUpperCase() + text.slice(1); };
  const appInputs = Object.entries(inputs).filter(([, node]) => node.widgets?.some(w => w.name === settable(node))).flatMap(([name, node]) =>
    [{node: node.id, widget: settable(node), description: label(name)}, ...(node.widgets.some(w => w.name === 'upload') ? [{node: node.id, widget: 'upload', description: `Upload: ${label(name).charAt(0).toLowerCase()}${label(name).slice(1)}`}] : [])]);
  if (outputs.length) setSignature(graph, appInputs, outputs);
  // The contract's intent and the design's shared parts (data entities, decisions, step order)
  // are stored in the workflow itself: the workflow is the single source of truth.
  if (intent) graph.extra = {...graph.extra, aws_contract: intent, ...(meta ? {aws_design: meta} : {})};
  arrangeGraph(graph);
  // What is already in place, so the agent builds only what is missing.
  const stages = steps.map(step => {
    const stage = stageNode(graph, step.id), sg = stage.subgraph;
    return {step: step.id, title: stage.title, design: stage.properties.aws_design,
      takes: sg.inputs.map((p, i) => `${p.name}: ${p.type}${stage.isInputConnected(i) ? ' (wired)' : ' (connect at the top level)'}`),
      gives: sg.outputs.map(p => `${p.name}: ${p.type}`),
      inside: nodes(sg).map(n => (n.type === 'AgentWorkflowCheck' ? `Check on ${n.properties?.aws_watches}` : n.type === 'AgentWorkflowStub' ? 'Stub (input → outputs)' : n.title))};
  });
  return {created, notes, stages, how: 'Build each stage in one batch: add its nodes, wire them from "input" ports to "output" ports. Stages are already wired to each other; a Check follows its output and a Stub disappears once replaced.'};
}

const inputSlot = (node, name) => typeof name === 'number' ? name : node.inputs?.findIndex(input => input.name === name || input.widget?.name === name);

/** A tidy layout, positions only: data flows left to right in columns by depth; a node that
 *  feeds nothing and reads one node (a preview) sits under that node; notes go above. */
/** An unsaved workflow, once its contract is approved, is saved under the contract's title the native
 *  way (the workflow store's rename and save: its tab, the Workflows sidebar and App mode show it).
 *  A name already taken gets " (2)". Returns the path, or null when it was already saved. */
export async function nameWorkflow(app, api, title) {
  const store = app.extensionManager?.workflow, workflow = store?.activeWorkflow;
  const base = String(title ?? '').replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60);
  if (!workflow?.isTemporary || !base || !store.renameWorkflow || !store.saveWorkflow) return null;
  const taken = async path => store.getWorkflowByPath?.(path) || (await api.fetchApi(`/userdata/${encodeURIComponent(path)}`)).ok;
  let path = `workflows/${base}.json`;
  for (let n = 2; await taken(path) && n < 100; n++) path = `workflows/${base} (${n}).json`;
  await store.renameWorkflow(workflow, path);
  await store.saveWorkflow(workflow);
  return path;
}

/** After the agent changed the workflow's shape, show all of it: ComfyUI's own animated fit, into the
 *  part of the canvas no panel covers (the chat on the left, the toolbar, the properties panel), and
 *  never zoomed in past 100%. Not when the user is inside a stage (their view is theirs). */
export function frameWorkflow(app) {
  (globalThis.requestAnimationFrame ?? setTimeout)(() => {
    const canvas = app.canvas, items = app.rootGraph?.nodes ?? [];
    if (canvas?.graph !== app.rootGraph || !items.length || !canvas.ds?.animateToBounds) return;
    const box = canvas.canvas.getBoundingClientRect(), rect = selector => document.querySelector(selector)?.getBoundingClientRect();
    const left = Math.max(0, (rect('.aw-pi-chat')?.right ?? box.left) - box.left) + 24;
    const top = Math.max(0, (rect('[aria-label="Toggle properties panel"]')?.bottom ?? box.top + 80) - box.top) + 24;
    const right = (rect('[data-testid="properties-panel"]')?.left ?? box.right) - box.left - 24;
    const bottom = box.height - 24;
    const title = globalThis.LiteGraph?.NODE_TITLE_HEIGHT ?? 30;  // a node drawn yet or not: its position and size
    const bounds = items.map(n => [n.pos[0], n.pos[1] - title, n.size[0], n.size[1] + title]).reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[0] + b[2]), Math.max(a[3], b[1] + b[3])], [Infinity, Infinity, -Infinity, -Infinity]);  // [left, top, right, bottom]
    const [x, y, w, h] = [bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1]];
    const width = right - left, height = bottom - top;
    if (width <= 0 || height <= 0) return;
    // animateToBounds zooms to `zoom` of the viewport: at most what shows the workflow at 100%.
    const zoom = Math.min(0.95, 1 / Math.min(width / Math.max(w, 300), height / Math.max(h, 300)));
    canvas.ds.animateToBounds([x, y, w, h], () => canvas.setDirty(true, true), {viewport: [left, top, width, height], zoom});
  });
}

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
    const sources = rest.filter(node => (node.properties?.aws_given || node.properties?.aws_input) && !parents.get(node.id).length);
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
    column.sort((a, b) => !!b.properties?.aws_input - !!a.properties?.aws_input || !!b.properties?.aws_given - !!a.properties?.aws_given || inputY(a) - inputY(b));
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
    const inner = deepNodes(stage.subgraph, `${stage.id}:`);
    let checks = inner.filter(({node}) => node.type === 'AgentWorkflowCheck').map(({node, runId}) => ({title: node.title, result: record?.outputs?.[runId]?.aws_check?.[0]}));
    // A step a person does (Your review decisions): its part is done when the queue reached the person.
    const human = inner.filter(({node}) => node.type === 'AgentWorkflowReviewDecisions').map(({node, runId}) => ({node, done: record?.outputs?.[runId]?.aws_human?.[0]}));
    if (!checks.length && human.length) checks = human.map(({node, done}) => ({title: node.title, result: done && {passed: true, evidence: `the review queue reached you · ${done.decided.toLocaleString()} decided · ${done.open.toLocaleString()} open${done.open ? ' (your turn: Review tab)' : ''}`}}));
    const status = !checks.length ? 'needs_user_review' : checks.some(c => !c.result) ? 'no_output' : checks.every(c => c.result.passed) ? 'passed' : 'failed';
    const open = human.reduce((n, h) => n + (h.done?.open ?? 0), 0);
    return {step: stage.properties.aws_stage, status, ...(open ? {open} : {}), checks: checks.map(c => ({title: c.title, passed: c.result?.passed ?? null, evidence: c.result?.evidence ?? 'did not run'}))};
  });
}

/** What each stage's nodes said on a run, by title: the numbers to judge it by (counts, rates,
 *  sizes), readable without node ids. Scoring against a hidden answer key is left out. */
export function stageSummaries(record, graph) {
  const said = out => String(out?.aws_headline?.[0] ?? out?.text?.[0] ?? '').replace(/\s+/g, ' ').slice(0, 300);
  return nodes(graph).filter(node => node.properties?.aws_stage).map(stage => ({
    stage: stage.title,
    nodes: deepNodes(stage.subgraph, `${stage.id}:`)
      .filter(({node, runId}) => !node.isSubgraphNode?.() && record?.outputs?.[runId] && !USER_ONLY.has(node.type))
      .map(({node, runId}) => ({node: node.title, said: said(record.outputs[runId])}))
      .filter(item => item.said),
  }));
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
  // The latest run of exactly this graph counts, even when later runs (its examples, through the
  // API with other inputs) ran something else; without one, the latest run, which is not current.
  const exact = runs.findLast(run => JSON.stringify(run.prompt[2]) === executable);
  return exact ? {run: exact, current: true} : runs.length ? {run: runs.at(-1), current: false} : null;
}

async function digest(text) {
  const bytes = new TextEncoder().encode(text);
  const hash = await globalThis.crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(hash)].slice(0, 12).map(b => b.toString(16).padStart(2, '0')).join('');
}

/** Evidence for deriving contract step states: the stages and the nodes inside them (a node's step
 *  is the stage it sits in), the saved workflow (for the architecture rules) and, when the latest run
 *  of this workflow ran exactly the current graph, each step's Check results on that run. */
// "2026-10-20T12:50" on the user's clock (toISOString alone would show UTC).
const localTime = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export async function contractEvidence(app, api) {
  const graph = app.rootGraph ?? app.graph;
  const stages = nodes(graph).filter(node => node.properties?.aws_stage);
  const evidence = {
    nodes: [
      ...nodes(graph).filter(node => !node.properties?.aws_stage).map(node => ({id: String(node.id), title: node.title, class_type: node.type, step: null, given: !!(node.properties?.aws_given || node.properties?.aws_input || node.properties?.aws_output), note: /Note$/.test(node.type ?? '')})),
      ...stages.flatMap(stage => deepNodes(stage.subgraph, `${stage.id}:`).filter(({node}) => !node.isSubgraphNode?.()).map(({node, runId}) => ({id: runId, title: node.title, class_type: node.type, display: globalThis.LiteGraph?.registered_node_types?.[node.type]?.title, realizes: node.properties?.aws_realizes ?? null, step: stage.properties.aws_stage, scaffold: SCAFFOLD.has(node.type), fuzzy: node.type === 'AgentWorkflowFuzzyNode'}))),
    ],
    stages: stages.map(stage => ({step: stage.properties.aws_stage, title: stage.title, orphaned: !!stage.properties.aws_orphaned, stale: !!stage.properties.aws_stale,
      takes: (stage.subgraph.inputs ?? []).map(p => [p.name, p.type]), gives: (stage.subgraph.outputs ?? []).map(p => [p.name, p.type, observedFields(stage.subgraph, p.name)]),
      // What its Checks actually test, to compare with the check its gate agreed to.
      checks: stage.subgraph.nodes.filter(n => n.type === 'AgentWorkflowCheck').map(n => ({watches: n.properties?.aws_watches ?? null, kind: n.widgets?.find(w => w.name === 'kind')?.value, expect: n.widgets?.find(w => w.name === 'expect')?.value ?? ''}))})),
    workflow: graph.serialize(),
    gate_results: {}, gate_checks: {}, open: {}, run: null,
  };
  const defs = await api.getNodeDefs();
  const analysis = analyzeGraph(graph.serialize(), withStageOutputs(graph, defs));
  // "No output node" is judged by the gates themselves: on an empty canvas it is progress, not drift.
  evidence.analysis = {outputs: analysis.outputs.map(o => o.title), problems: analysis.problems.filter(p => p.kind !== 'no_output_node').map(p => p.message)};
  const history = await api.fetchApi('/history?max_items=64').then(r => (r.ok ? r.json() : {}));
  const {output: prompt} = await app.graphToPrompt();
  const executable = JSON.stringify(prompt);
  evidence.prompt = prompt;  // examples' results count only for exactly this graph
  const match = latestMatchingRun(history, graph.id, executable);
  const fingerprint = await digest(executable);
  if (match) {
    evidence.run = {prompt_id: match.run.prompt[1], current: match.current};
    if (match.current && match.run.status?.completed) {
      for (const gate of gateResults(match.run, graph)) {
        evidence.gate_results[gate.step] = gate.status;
        evidence.gate_checks[gate.step] = gate.checks;
        if (gate.open) evidence.open[gate.step] = gate.open;  // pairs waiting for the user's review
      }
      // Kept with the workflow, so the status survives a restart (ComfyUI's history does not). Written
      // only when it changed: reading the status must not become a change in the user's undo history.
      const verified = {fingerprint, gates: evidence.gate_results, checks: evidence.gate_checks, open: evidence.open}, {at, ...before} = graph.extra?.aws_verified ?? {};
      if (JSON.stringify(before) !== JSON.stringify(verified)) quietly(app, () => { graph.extra = {...graph.extra, aws_verified: {...verified, at: localTime()}}; });
    }
  } else if (graph.extra?.aws_verified?.fingerprint === fingerprint) {
    // No run in this session yet, but exactly this graph was verified before.
    evidence.run = {prompt_id: null, current: true, at: graph.extra.aws_verified.at};
    evidence.gate_results = {...graph.extra.aws_verified.gates};
    evidence.open = {...graph.extra.aws_verified.open};
    evidence.gate_checks = {...graph.extra.aws_verified.checks};
  }
  return evidence;
}

export function createCanvasBridge(app, api, {confirm, autoApproveRun = () => false} = {}) {
  let definitions, latestPrompt, submitted = false;
  // Node keys stay valid for the whole chat, so later batches can reference earlier nodes.
  const keys = Object.create(null);
  // Node definitions, re-read when older than a few seconds: a folder or file uploaded since (a choice
  // in a list such as the Data folder's) is then a valid choice for the agent too.
  let definitionsRead = 0;
  const catalog = async () => {
    if (!definitions || Date.now() - definitionsRead > 5000) { definitions = await api.getNodeDefs(); definitionsRead = Date.now(); }
    return definitions;
  };
  const agentCatalog = async () => Object.fromEntries(Object.entries(await catalog()).filter(([, def]) => inStudioScope(def)));
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

  // The approved design's wiring (from the contract): top-level edits that contradict it are refused,
  // so the canvas cannot drift from the design; changing the design is an amendment the user approves.
  function declaredFor(design, stage, port) {
    return [...design.links.filter(([, , s, p]) => s === stage && p === port).map(([a, b]) => `${a}.${b}`),
      ...design.inputs.filter(([, s, p]) => s === stage && p === port).map(([n]) => `input.${n}`)];
  }
  function guard(design, from, out, to, slot) {
    if (!design || !to?.properties?.aws_stage) return;
    const port = to.inputs[slot]?.name, declared = declaredFor(design, to.properties.aws_stage, port);
    const source = from.properties?.aws_stage ? `${from.properties.aws_stage}.${from.outputs[out]?.name}` : from.properties?.aws_input ? `input.${from.properties.aws_input}` : null;
    if (!source && !declared.length) return; // the user's given data into a port the design leaves open
    if (!declared.includes(source)) throw Error(`Not in the approved design: ${source ?? from.title} → ${to.title} (${port}). The design wires ${declared.join(', ') || 'nothing'} there. To change the design, propose an amended contract.`);
  }

  async function edit(operations, design, {limit = 32} = {}) {
    if (!Array.isArray(operations) || !operations.length || operations.length > limit) throw Error(`Use 1–${limit} edit operations.`);
    const graph = app.graph;
    const defs = await catalog();
    if (app.graph !== graph) throw Error('The active workflow changed; inspect it and retry.');
    const occupied = op => {
      try {
        if (op.to === 'output' || op.from === 'input') return false;
        const to = resolve(op.to, op, {}), slot = inputSlot(to, op.input), where = scope(op.stage);
        const from = resolve(op.from, op, {}), existing = inputLink(to, slot);
        if (!existing || (existing.origin_id === from?.id && existing.origin_slot === outputSlot(from, op.output))) return false;
        return !scaffoldLink(where, to, slot);
      } catch { return false; }
    };
    // Removing a Stub, or a node a stage's previous LLD built, is replacing the agent's own work: no confirmation.
    const scaffold = op => { try { const node = resolve(op.node, op, {}); return node?.type === 'AgentWorkflowStub' || !!node?.properties?.aws_lld_key; } catch { return false; } };
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
          if (!inStudioScope(defs[op.class_type])) throw outOfScope(op.class_type);
          const node = globalThis.LiteGraph.createNode(op.class_type);
          if (!node) throw Error(`Cannot create native node: ${op.class_type}`);
          if (op.title != null) {if (typeof op.title !== 'string' || op.title.length > 200) throw Error('Invalid node title.'); node.title = op.title;}
          const right = nodes(where).reduce((x,n) => Math.max(x,n.pos[0]+n.size[0]),0);
          const position = op.position ?? [right+80,80];
          if (!Array.isArray(position) || position.length !== 2 || !position.every(Number.isFinite)) throw Error('Invalid node position.');
          node.pos = position;
          // Built from a step's LLD: which LLD node it is, and which HLD clauses it realizes.
          if (op.lld) node.properties = {...node.properties, aws_lld_key: op.lld.key, aws_realizes: op.lld.realizes};
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
            if (to.isInputConnected(slot) && !destructive && !scaffoldLink(where, to, slot)) throw Error('Replacing an occupied input needs fresh approval.');
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
            if (op.stage == null) guard(design, from, out, to, slot);
            const existing = inputLink(to, slot);
            if (existing && existing.origin_id === from.id && existing.origin_slot === out) continue; // already wired
            if (existing && !destructive && !scaffoldLink(where, to, slot)) throw Error('Replacing an occupied input needs fresh approval.');
            if (!from.connect(out,to,slot)) throw Error('Native port types do not connect.');
          }
        } else if (op.op === 'remove' || op.op === 'disconnect') {
          const node = resolve(op.node, op, added);
          if (!node) throw notFound(op.node);
          if (op.op === 'remove') {
            if (node.properties?.aws_stage && !node.properties.aws_orphaned) throw Error('A stage is a contract step: to drop it, propose a revised contract without that step.');
            if (node.properties?.aws_input) throw Error("An API input comes from the contract's promised inputs: to drop it, propose an amended contract.");
            where.remove(node);
          } else {
            const slot = inputSlot(node,op.input);
            if (!Number.isInteger(slot) || slot < 0 || !node.inputs?.[slot]) throw Error('Input not found.');
            if (design && op.stage == null && node.properties?.aws_stage && declaredFor(design, node.properties.aws_stage, node.inputs[slot].name).length) throw Error(`The approved design wires ${node.title} (${node.inputs[slot].name}); to change it, propose an amended contract.`);
            node.disconnectInput(slot);
          }
        } else if (op.op === 'signature') {
          // The workflow's API: inputs are widgets on top-level nodes, outputs are stages (each answered
          // by its API output node, which App mode shows).
          if (op.stage != null) throw Error('The signature is set at the top level (no "stage").');
          const inputs = (op.inputs ?? []).map(({node: ref, widget}) => {
            const node = resolve(ref, op, added);
            if (!node || node.properties?.aws_stage) throw Error(`An API input is a widget on a top-level node (such as a given data folder), not a stage: ${ref}.`);
            if (!node.widgets?.some(w => w.name === widget)) throw Error(`${node.title} has no widget ${widget}.`);
            return {node: node.id, widget};
          });
          const outputs = (op.outputs ?? []).map(ref => { const node = resolve(ref, op, added); if (!node) throw notFound(ref); return node.properties?.aws_stage ? (outputNode(app.graph, `${node.properties.aws_stage}.`)?.id ?? node.id) : node.id; });
          if (!outputs.length) throw Error('Name at least one output: the stage (step id) whose results the API returns.');
          setSignature(app.graph, inputs, outputs);
        } else if (op.op === 'unpromote') {
          const input = where.inputs?.find(i => i.name === op.input);
          if (input) where.removeInput(input);
        } else if (op.op === 'promote') {
          if (op.stage == null) throw Error('Promote a setting inside a stage: give "stage".');
          const node = resolve(op.node, op, added);
          if (!node) throw notFound(op.node);
          promoteWidget(stageNode(app.graph, op.stage), node, op.widget);
        } else if (op.op !== 'arrange') throw Error(`Unsupported operation: ${op.op}`);
      }
      Object.assign(keys, added);
      // Keep the canvas readable without the agent computing coordinates: re-lay out after
      // structural edits (and on request); the whole batch is still one undo step.
      if (operations.some(op => ['add', 'connect', 'remove', 'arrange'].includes(op.op))) { arrangeGraph(app.graph); frameWorkflow(app); }
      app.graph.setDirtyCanvas(true,true);
      return {added: Object.fromEntries(Object.entries(added).map(([key, {id}]) => [key, id])), message:'Applied native canvas edits. This tool did not submit a run; native Auto Queue settings still apply.'};
    } catch (error) {
      // Restore the current graph, not a new workflow/tab or root graph.
      app.graph.configure(snapshot);
      throw Error(`Edits rolled back: ${error.message}`);
    } finally {
      app.canvas.emitAfterChange();
      if (app.graph.extra?.linearData) refreshApp();  // a signature set inside the batch reaches App mode
    }
  }

  /** Compile a step's LLD (checked by the server against its HLD) into its stage: the stage's nodes
   *  are replaced by the spec's, in one undo step, through the same checked operations as edits. */
  async function build({step, nodes: spec, wires}) {
    const stage = stageNode(app.graph, step);
    if (!stage) throw Error(`No stage for step ${step}; the design's stages come from comfy_propose_design.`);
    const stamp = Date.now().toString(36), keyOf = key => `${step}~${key}~${stamp}`;
    const socket = name => (/^\d+$/.test(name) ? Number(name) : name);
    const operations = [
      // The previous build goes (its promoted settings too); hand-made nodes only after the user confirms.
      ...stage.subgraph.inputs.filter(i => !(i.name in (stage.properties?.aws_step?.takes ?? {}))).map(i => ({op: 'unpromote', stage: step, input: i.name})),
      ...nodes(stage.subgraph).filter(n => !SCAFFOLD.has(n.type)).map(n => ({op: 'remove', stage: step, node: n.id})),
      ...Object.entries(spec).map(([key, node]) => ({op: 'add', stage: step, key: keyOf(key), class_type: node.type, ...(node.title ? {title: node.title} : {}), lld: {key, realizes: node.realizes}})),
      ...Object.entries(spec).flatMap(([key, node]) => Object.entries(node.set ?? {}).map(([name, value]) => ({op: 'widget', stage: step, node: keyOf(key), name, value}))),
      ...wires.map(({from, to}) => {
        const [a, out] = from.split('.'), [b, into] = to.split('.');
        if (a === 'input') return {op: 'connect', stage: step, from: 'input', output: out, to: keyOf(b), input: into};
        if (b === 'output') return {op: 'connect', stage: step, from: keyOf(a), output: socket(out), to: 'output', input: into};
        return {op: 'connect', stage: step, from: keyOf(a), output: socket(out), to: keyOf(b), input: into};
      }),
      // Its key settings on the stage card (promoted, like ComfyUI's own promote widget).
      ...Object.entries(spec).flatMap(([key, node]) => (node.promote ?? []).map(widget => ({op: 'promote', stage: step, node: keyOf(key), widget}))),
    ];
    if (operations.length > 160) throw Error('This LLD is too large for one stage; split the step in the design.');
    const done = await edit(operations, null, {limit: 160});
    return {built: step, nodes: Object.fromEntries(Object.entries(done.added).map(([key, id]) => [key.split('~')[1], id])), message: `Built ${stage.title} from its LLD.`};
  }

  // One node as the agent reads it; a stage also lists its ports and the nodes and wires inside.
  const describe = node => ({id:node.id,class_type:node.type,title:node.title,mode:node.mode,
    ...(node.properties?.aws_given ? {given:true} : {}),
    inputs:(node.inputs??[]).map((input,index) => ({index,name:input.name,type:input.type,link:inputLink(node, index)?.id ?? null})),
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
      const defs = await agentCatalog(), all = await catalog();
      if (args.class_types) {
        if (!Array.isArray(args.class_types) || args.class_types.length > 20) throw Error('Request at most 20 node schemas.');
        return {schemas:Object.fromEntries(args.class_types.map(name => {if (!Object.hasOwn(all,name)) throw Error(`Node is not installed: ${name}`); if (!Object.hasOwn(defs,name)) throw outOfScope(name); return [name,defs[name]];}))};
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
    if (action === 'edit') return edit(args.operations, args.design);
    if (action === 'build') return build(args);
    if (action === 'intent') {
      // The approved contract (intent) is stored in the workflow itself.
      app.graph.extra = {...app.graph.extra, aws_contract: args.intent};
      app.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
      return {stored: true, saved_as: await nameWorkflow(app, api, args.intent?.title)};
    }
    if (action === 'examples') {
      // The agent runs the whole CI (examples, then the pipeline); it never publishes.
      const {runChecks} = await import('./publish.js');
      return runChecks(app, api, 'agent');
    }
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
      try { const result = buildSkeleton(app.graph, args.steps, {promises: args.promises, design: args.design, intent: args.intent, meta: args.meta}); app.graph.setDirtyCanvas(true, true); frameWorkflow(app); return result; } finally { app.canvas.emitAfterChange(); refreshApp(); }
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
      return {paths: findConverterPaths(await agentCatalog(), args.from_type, args.to_type)};
    }
    if (action === 'contract_approve') return {approved: await confirm?.(String(args.title ?? 'Approve this workflow contract?'), String(args.text ?? ''), String(args.summary ?? '')) === true};
    if (action === 'tool_approve') return {approved: await confirm?.(String(args.title ?? 'Approve this tool?'), String(args.text ?? ''), String(args.summary ?? '')) === true};
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
          // Each stage's numbers by node title first; the raw outputs (by node id) follow.
          result.status=record.status; result.stages=stageSummaries(record, app.graph); result.outputs=compactOutputs(agentOutputs(record));
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
