// Native graph APIs, following Comfy-Org/ComfyUI_frontend's app.ts/API queue path.
// Kept independent of DOM/Comfy imports for small runnable regression checks.
const secret = /(?:password|api.?key|token|credential|secret|authorization)/i;
const nodes = graph => graph.nodes ?? graph._nodes ?? [];
// The current frontend keeps links in a Map; older graphs used a plain object.
const links = graph => graph.links instanceof Map ? [...graph.links.values()] : Object.values(graph.links ?? {});
const inputSlot = (node, name) => typeof name === 'number' ? name : node.inputs?.findIndex(input => input.name === name || input.widget?.name === name);

export function createCanvasBridge(app, api, {confirm, autoApproveRun = () => false} = {}) {
  let definitions, latestPrompt, awaitingStart = false, submitted = false;
  const executionStart = event => {
    if (awaitingStart && event.detail?.prompt_id) {
      latestPrompt = event.detail.prompt_id;
      awaitingStart = false;
    }
  };
  api.addEventListener('execution_start', executionStart);
  const catalog = async () => definitions ??= await api.getNodeDefs();
  const resolve = (ref, added) => app.graph.getNodeById(added[ref] ?? ref);

  async function edit(operations) {
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
    if (action === 'edit') return edit(args.operations);
    if (action === 'run') {
      const before = JSON.stringify(app.rootGraph?.serialize?.() ?? app.graph.serialize());
      if (!autoApproveRun() && !await confirm?.('Run the current ComfyUI workflow?', String(args.reason??''))) throw Error('Workflow execution was not authorized.');
      const queue = await api.getQueue();
      if (before !== JSON.stringify(app.rootGraph?.serialize?.() ?? app.graph.serialize())) throw Error('Workflow changed before submission; inspect it again before running.');
      // ponytail: one chat-owned run at a time; correlate native client events.
      // Use native request IDs if ComfyUI exposes queue IDs through app.queuePrompt.
      if (queue.Running?.length || queue.Pending?.length || app.processingQueue) throw Error('Native queue is busy; wait before starting a chat-owned run.');
      latestPrompt = null; submitted = false; awaitingStart = true;
      try {
        const accepted = await app.queuePrompt(0,1);
        if (accepted === false) throw Error('Native ComfyUI validation rejected the workflow. Inspect execution_results and the native error panel.');
        submitted = true;
        return {submitted:true,prompt_id:latestPrompt??null,message:'Submitted through native Run; inspect execution_results before claiming success.'};
      } catch (error) {awaitingStart=false;throw error;}
    }
    if (action === 'results') {
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
  return {handle,dispose:()=>api.removeEventListener('execution_start',executionStart)};
}
