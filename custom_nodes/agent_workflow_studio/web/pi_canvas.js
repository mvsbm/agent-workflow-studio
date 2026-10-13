// Native graph APIs, following Comfy-Org/ComfyUI_frontend's app.ts/API queue path.
// Kept independent of DOM/Comfy imports for small runnable regression checks.
import {analyzeGraph, findConverterPaths} from './graph_analysis.js';
const secret = /(?:password|api.?key|token|credential|secret|authorization)/i;
const nodes = graph => graph.nodes ?? graph._nodes ?? [];
// The current frontend keeps links in a Map; older graphs used a plain object.
const links = graph => graph.links instanceof Map ? [...graph.links.values()] : Object.values(graph.links ?? {});
const inputSlot = (node, name) => typeof name === 'number' ? name : node.inputs?.findIndex(input => input.name === name || input.widget?.name === name);

/** Judge success gates against a run's outputs, grouped by the producing node's title. */
export function evaluateGates(gates, outputsByTitle) {
  return gates.map(({step, description, check}) => {
    if (!check) return {step, description, status: 'needs_user_review'};
    const outputs = outputsByTitle[check.node_title.trim().toLowerCase()];
    if (!outputs) return {step, description, status: 'no_output', evidence: `No output from a node titled "${check.node_title}" in the latest run. Only output nodes (Preview, Save) produce outputs; point the check at one.`};
    const texts = outputs.flatMap(output => output.text ?? []).map(String);
    const images = outputs.reduce((count, output) => count + (output.images?.length ?? 0), 0);
    const passed = {
      non_empty: () => texts.some(text => text.trim()) || images > 0,
      contains: () => texts.some(text => text.includes(String(check.value))),
      equals: () => texts.some(text => text === String(check.value)),
      regex: () => texts.some(text => new RegExp(String(check.value)).test(text)),
      image_count_at_least: () => images >= Number(check.value),
    }[check.kind]?.();
    if (passed === undefined) return {step, description, status: 'no_output', evidence: `Unknown check ${check.kind}.`};
    const evidence = check.kind === 'image_count_at_least' ? `${images} image(s)` : texts.join(' | ').slice(0, 300);
    return {step, description, status: passed ? 'passed' : 'failed', evidence};
  });
}

/** Outputs of a finished run grouped by the producing node's (lowercased) title. */
function outputsByTitle(record, graph) {
  const titles = Object.fromEntries(nodes(graph).map(node => [String(node.id), node.title]));
  const byTitle = {};
  for (const [id, output] of Object.entries(record.outputs ?? {})) {
    const title = (titles[id] ?? record.prompt?.[2]?.[id]?._meta?.title ?? '').trim().toLowerCase();
    if (title) (byTitle[title] ??= []).push(output);
  }
  return byTitle;
}

/** The most recent run of this workflow in /history, and whether it ran exactly the current graph. */
export function latestMatchingRun(history, workflowId, executable) {
  const runs = Object.values(history ?? {}).filter(run => run.prompt?.[3]?.extra_pnginfo?.workflow?.id === workflowId);
  const run = runs.at(-1);
  return run ? {run, current: JSON.stringify(run.prompt[2]) === executable} : null;
}

/** Evidence for deriving contract step states: step tags on nodes and, when the latest run of
 *  this workflow ran exactly the current graph, each gate's outcome on that run. */
export async function contractEvidence(app, api, gates) {
  const graph = app.rootGraph ?? app.graph;
  const evidence = {
    nodes: nodes(graph).map(node => ({id: node.id, title: node.title, class_type: node.type, step: node.properties?.aws_contract_step ?? null, fuzzy: node.type === 'AgentWorkflowFuzzyNode'})),
    gate_results: {}, run: null,
  };
  const history = await api.fetchApi('/history?max_items=64').then(r => (r.ok ? r.json() : {}));
  const match = latestMatchingRun(history, graph.id, JSON.stringify((await app.graphToPrompt()).output));
  if (match) {
    evidence.run = {prompt_id: match.run.prompt[1], current: match.current};
    if (match.current && match.run.status?.completed) {
      for (const gate of evaluateGates(gates ?? [], outputsByTitle(match.run, graph))) evidence.gate_results[gate.step] = gate.status;
    }
  }
  return evidence;
}

/** Draw each contract step as a native group around the nodes tagged with it (inside an edit). */
function fitStepGroups(graph, steps) {
  const LGraphGroup = globalThis.LiteGraph?.LGraphGroup;
  if (!LGraphGroup) return;
  const known = (graph.extra ??= {}).aws_step_groups ??= {};
  const groups = () => graph._groups ?? graph.groups ?? [];
  for (const {id, title} of steps) {
    const members = nodes(graph).filter(node => node.properties?.aws_contract_step === id);
    let group = groups().find(g => g.title === (known[id] ?? title));
    if (!members.length) {
      if (group) graph.remove(group);
      delete known[id];
      continue;
    }
    if (!group) graph.add(group = new LGraphGroup(title));
    group.title = title;
    // From pos/size, not resizeTo: bounding boxes of just-added nodes are only computed on draw.
    const pad = 24, head = globalThis.LiteGraph.NODE_TITLE_HEIGHT ?? 30;
    const x0 = Math.min(...members.map(n => n.pos[0])) - pad, y0 = Math.min(...members.map(n => n.pos[1])) - head - pad - head;
    const x1 = Math.max(...members.map(n => n.pos[0] + n.size[0])) + pad, y1 = Math.max(...members.map(n => n.pos[1] + n.size[1])) + pad;
    group.pos = [x0, y0];
    group.size = [x1 - x0, y1 - y0];
    known[id] = title;
  }
}

export function createCanvasBridge(app, api, {confirm, autoApproveRun = () => false} = {}) {
  let definitions, latestPrompt, submitted = false;
  const catalog = async () => definitions ??= await api.getNodeDefs();
  const resolve = (ref, added) => app.graph.getNodeById(added[ref] ?? ref);

  async function edit(operations, contractSteps) {
    if (!Array.isArray(operations) || !operations.length || operations.length > 32) throw Error('Use 1–32 edit operations.');
    const graph = app.graph;
    const defs = await catalog();
    if (app.graph !== graph) throw Error('The active workflow changed; inspect it and retry.');
    const destructive = operations.some(op => op.op === 'remove' || op.op === 'disconnect' ||
      (op.op === 'connect' && app.graph.getNodeById(op.to)?.inputs?.[inputSlot(app.graph.getNodeById(op.to),op.input)]?.link != null));
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
        if (op.op === 'add') {
          if (typeof op.key !== 'string' || !op.key || Object.hasOwn(added,op.key)) throw Error('New nodes need unique nonempty keys.');
          if (!Object.hasOwn(defs,op.class_type)) throw Error(`Node is not installed: ${op.class_type}`);
          const node = globalThis.LiteGraph.createNode(op.class_type);
          if (!node) throw Error(`Cannot create native node: ${op.class_type}`);
          if (op.title != null) {if (typeof op.title !== 'string' || op.title.length > 200) throw Error('Invalid node title.'); node.title = op.title;}
          // Tie the node to its contract step; the contract's step states are derived from these tags.
          if (op.step != null) {if (typeof op.step !== 'string' || !/^[a-z][a-z0-9_]{0,31}$/.test(op.step)) throw Error('Invalid contract step id.'); node.properties = {...node.properties, aws_contract_step: op.step};}
          const right = nodes(app.graph).reduce((x,n) => Math.max(x,n.pos[0]+n.size[0]),0);
          const position = op.position ?? [right+80,80];
          if (!Array.isArray(position) || position.length !== 2 || !position.every(Number.isFinite)) throw Error('Invalid node position.');
          node.pos = position;
          app.graph.add(node);
          added[op.key] = node.id;
        } else if (op.op === 'move' || op.op === 'title') {
          const node=resolve(op.node,added);
          if (!node) throw Error(`Node not found: ${op.node}`);
          if (op.op === 'move') {
            if (!Array.isArray(op.position)||op.position.length!==2||!op.position.every(Number.isFinite)) throw Error('Invalid node position.');
            node.pos=op.position;
          } else {
            if (typeof op.title!=='string'||op.title.length>200) throw Error('Invalid node title.');
            node.title=op.title;
          }
        } else if (op.op === 'widget') {
          const node = resolve(op.node,added), widget = node?.widgets?.find(w => w.name === op.name);
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
          const from = resolve(op.from,added), to = resolve(op.to,added);
          if (!from || !to || !Number.isInteger(op.output) || !from.outputs?.[op.output]) throw Error('Invalid connection source.');
          // Current native inputs already coexist with their widgets.
          const slot = inputSlot(to,op.input);
          if (!Number.isInteger(slot) || slot < 0 || !to.inputs?.[slot]) throw Error('Invalid connection target.');
          if (to.inputs[slot].link != null && !destructive) throw Error('Replacing an occupied input needs fresh approval.');
          if (!from.connect(op.output,to,slot)) throw Error('Native port types do not connect.');
        } else if (op.op === 'remove' || op.op === 'disconnect') {
          const node = resolve(op.node,added);
          if (!node) throw Error(`Node not found: ${op.node}`);
          if (op.op === 'remove') app.graph.remove(node);
          else {
            const slot = inputSlot(node,op.input);
            if (!Number.isInteger(slot) || slot < 0 || !node.inputs?.[slot]) throw Error('Input not found.');
            node.disconnectInput(slot);
          }
        } else throw Error(`Unsupported operation: ${op.op}`);
      }
      if (Array.isArray(contractSteps)) fitStepGroups(app.graph, contractSteps);
      app.graph.setDirtyCanvas(true,true);
      return {added, node_count:nodes(app.graph).length, message:'Applied native canvas edits. This tool did not submit a run; native Auto Queue settings still apply.'};
    } catch (error) {
      // Restore the current graph, not a new workflow/tab or root graph.
      app.graph.configure(snapshot);
      throw Error(`Edits rolled back: ${error.message}`);
    } finally {
      app.canvas.emitAfterChange();
    }
  }

  async function handle(action, args={}) {
    if (action === 'read') return {
      selected: Object.values(app.canvas.selected_nodes ?? {}).map(node => node.id),
      nodes:nodes(app.graph).map(node => ({id:node.id,class_type:node.type,title:node.title,mode:node.mode,position:Array.from(node.pos),size:Array.from(node.size),
        inputs:(node.inputs??[]).map((input,index) => ({index,name:input.name,type:input.type,link:input.link})),
        outputs:(node.outputs??[]).map((output,index) => ({index,name:output.name,type:output.type})),
        widgets:(node.widgets??[]).filter(w => !['button'].includes(w.type)).map(w => ({name:w.name,value:secret.test(w.name)?'[redacted]':(['string','number','boolean'].includes(typeof w.value)?w.value:null)})),
        // A subgraph node's type is an opaque UUID; describe what it contains.
        ...(node.isSubgraphNode?.() ? {subgraph:{name:node.subgraph?.name,nodes:nodes(node.subgraph ?? {}).map(inner => ({id:inner.id,class_type:inner.type,title:inner.title}))}} : {}),
      })),
      groups:(app.graph._groups??[]).map(group=>({title:group.title,bounding:Array.from(group._bounding??[])})),
      links:links(app.graph).map(link => ({from:link.origin_id,output:link.origin_slot,to:link.target_id,input:link.target_slot})),
    };
    if (action === 'catalog') {
      const defs = await catalog();
      if (args.class_types) {
        if (!Array.isArray(args.class_types) || args.class_types.length > 20) throw Error('Request at most 20 node schemas.');
        return {schemas:Object.fromEntries(args.class_types.map(name => {if (!Object.hasOwn(defs,name)) throw Error(`Node is not installed: ${name}`);return [name,defs[name]];}))};
      }
      const query = String(args.query??'').toLowerCase();
      const matches = Object.entries(defs).filter(([name,d]) => `${name} ${d.display_name??''} ${d.category??''} ${d.description??''} ${JSON.stringify([d.search_aliases,d.input,d.output,d.output_name])}`.toLowerCase().includes(query));
      return {total:matches.length,nodes:matches.slice(0,60).map(([name,d]) => ({class_type:name,title:d.display_name??name,category:d.category,description:d.description}))};
    }
    if (action === 'edit') return edit(args.operations, args.contract_steps);
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
    if (action === 'analyze') return analyzeGraph((app.rootGraph ?? app.graph).serialize(), await catalog());
    if (action === 'converters') {
      if (typeof args.from_type !== 'string' || typeof args.to_type !== 'string') throw Error('Give from_type and to_type.');
      return {paths: findConverterPaths(await catalog(), args.from_type, args.to_type)};
    }
    if (action === 'contract_approve') return {approved: await confirm?.(String(args.title ?? 'Approve this workflow contract?'), String(args.text ?? '')) === true};
    if (action === 'contract_evidence') return contractEvidence(app, api, Array.isArray(args.gates) ? args.gates : []);
    if (action === 'gates') {
      if (!latestPrompt) throw Error('Run the workflow first; gates are checked against the latest run.');
      const response = await api.fetchApi(`/history/${encodeURIComponent(latestPrompt)}`);
      if (!response.ok) throw Error('Cannot read native execution history.');
      const record = (await response.json())[latestPrompt];
      if (!record?.status?.completed) throw Error('The latest run has not completed yet.');
      return {prompt_id: latestPrompt, gates: evaluateGates(Array.isArray(args.gates) ? args.gates : [], outputsByTitle(record, app.graph))};
    }
    if (action === 'results') {
      // Wait (up to a minute) for this chat's run to finish, so the agent need not poll.
      for (let waited = 0; latestPrompt && waited < 60000; waited += 1000) {
        const finished = await api.fetchApi(`/history/${encodeURIComponent(latestPrompt)}`).then(r => (r.ok ? r.json() : {}));
        if (finished[latestPrompt]) break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      const queue = await api.getQueue();
      const result = {submitted,prompt_id:latestPrompt??null,queue,validation_errors:app.extensionManager?.lastNodeErrors??null,execution_error:app.extensionManager?.lastExecutionError??null};
      if (latestPrompt) {
        const response = await api.fetchApi(`/history/${encodeURIComponent(latestPrompt)}`);
        if (!response.ok) throw Error('Cannot read native execution history.');
        const record = (await response.json())[latestPrompt];
        if (record) {
          result.status=record.status; result.outputs=record.outputs;
          result.image_previews=[];
          for (const output of Object.values(record.outputs??{})) {
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
