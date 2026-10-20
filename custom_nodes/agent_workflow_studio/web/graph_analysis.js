// Static analysis of a ComfyUI graph: no model, no run. Pure functions over a serialized
// graph (app.graph.serialize()) and node definitions (/object_info), so they are testable
// without a browser. A ComfyUI workflow is a typed DAG executed backwards from its output nodes.

const WIDGET_TYPES = new Set(['INT', 'FLOAT', 'STRING', 'BOOLEAN', 'COMBO']);
const ACTIVE = 0; // LiteGraph mode: 0 always, 2 never (muted), 4 bypass

const isWidgetInput = spec => Array.isArray(spec?.[0]) || (WIDGET_TYPES.has(spec?.[0]) && !spec?.[1]?.forceInput);
const typesOf = type => String(type ?? '*').split(',').map(t => t.trim());
const compatible = (from, to) => {
  const a = typesOf(from), b = typesOf(to);
  return a.includes('*') || b.includes('*') || a.some(t => b.includes(t));
};

/** Problems and structure of a graph. */
export function analyzeGraph(graph, defs) {
  const nodes = new Map((graph.nodes ?? []).map(node => [node.id, node]));
  const links = (graph.links ?? []).map(l => (Array.isArray(l) ? {id: l[0], from: l[1], fromSlot: l[2], to: l[3], toSlot: l[4], type: l[5]} : {
    id: l.id, from: l.origin_id, fromSlot: l.origin_slot, to: l.target_id, toSlot: l.target_slot, type: l.type,
  })).filter(l => nodes.has(l.from) && nodes.has(l.to));
  const parents = new Map([...nodes.keys()].map(id => [id, new Set()]));
  for (const link of links) parents.get(link.to).add(link.from);
  const label = id => {
    const node = nodes.get(id);
    return {id, title: node.title || defs[node.type]?.display_name || node.type, class_type: node.type};
  };

  const outputs = [...nodes.values()].filter(n => (n.mode ?? ACTIVE) === ACTIVE && defs[n.type]?.output_node).map(n => n.id);

  // Reachability: what executes is exactly the ancestors of active output nodes.
  const ancestors = id => {
    const seen = new Set(), stack = [id];
    while (stack.length) for (const parent of parents.get(stack.pop()) ?? []) if (!seen.has(parent)) { seen.add(parent); stack.push(parent); }
    return seen;
  };
  const runs = new Set(outputs.flatMap(id => [id, ...ancestors(id)]));
  const notes = node => !defs[node.type] && !node.inputs?.length && !node.outputs?.length;
  const dead = [...nodes.values()].filter(n => !runs.has(n.id) && !notes(n)).map(n => label(n.id));

  const problems = [];
  if (!outputs.length) problems.push({kind: 'no_output_node', message: 'Nothing will run: the graph has no active output node (Preview, Save, …).'});
  for (const node of nodes.values()) {
    const def = defs[node.type];
    if (!def) continue;
    if (def.deprecated) problems.push({kind: 'deprecated', node: label(node.id), message: `${node.type} is deprecated; prefer a current node.`});
    for (const [name, spec] of Object.entries(def.input?.required ?? {})) {
      if (isWidgetInput(spec)) continue;
      const input = node.inputs?.find(i => i.name === name);
      if (input?.link == null) problems.push({kind: 'unconnected_input', node: label(node.id), input: name, message: `Required input "${name}" (${spec[0]}) is not connected.`});
    }
  }
  for (const link of links) {
    const fromType = nodes.get(link.from).outputs?.[link.fromSlot]?.type ?? link.type;
    const toType = nodes.get(link.to).inputs?.[link.toSlot]?.type ?? link.type;
    if (!compatible(fromType, toType)) problems.push({kind: 'type_mismatch', node: label(link.to), message: `${fromType} is connected to an input expecting ${toType}.`});
  }

  // Cycles: Kahn's algorithm leaves the nodes of any cycle unordered.
  const indegree = new Map([...nodes.keys()].map(id => [id, parents.get(id).size]));
  const order = [...nodes.keys()].filter(id => !indegree.get(id));
  for (let i = 0; i < order.length; i++) {
    for (const link of links) if (link.from === order[i] && indegree.set(link.to, indegree.get(link.to) - 1).get(link.to) === 0) order.push(link.to);
  }
  if (order.length < nodes.size) problems.push({kind: 'cycle', message: 'The graph contains a cycle.', nodes: [...nodes.keys()].filter(id => !order.includes(id)).map(label)});

  // Dominators: nodes every path from a source to the output passes through. A failure there
  // stops the output, so they are the natural places for success gates.
  const checkpoints = outputs.map(output => {
    const scope = [...ancestors(output), output];
    const sources = scope.filter(id => !parents.get(id).size);
    const dom = new Map(scope.map(id => [id, new Set(sources.includes(id) ? [id] : scope)]));
    for (let changed = true; changed;) {
      changed = false;
      for (const id of order.filter(id => scope.includes(id) && !sources.includes(id))) {
        const sets = [...parents.get(id)].map(p => dom.get(p));
        const next = new Set([id, ...[...sets[0]].filter(d => sets.every(s => s.has(d)))]);
        if (next.size !== dom.get(id).size) { dom.set(id, next); changed = true; }
      }
    }
    return {output: label(output), must_pass: order.filter(id => dom.get(output).has(id) && id !== output).map(label)};
  });

  return {
    outputs: outputs.map(label),
    will_not_run: dead,
    problems,
    checkpoints,
    summary: `${nodes.size} node(s), ${outputs.length} output node(s), ${dead.length} that will not run, ${problems.length} problem(s).`,
  };
}

/** Shortest chains of installed nodes that turn one data type into another (breadth-first search). */
export function findConverterPaths(defs, fromType, toType, {maxSteps = 3, limit = 5} = {}) {
  const edges = [];
  for (const [classType, def] of Object.entries(defs)) {
    if (def.deprecated || def.output_node) continue;
    const sockets = Object.values(def.input?.required ?? {}).filter(spec => !isWidgetInput(spec));
    if (sockets.length !== 1) continue; // a converter takes exactly one connected input
    for (const [slot, output] of (def.output ?? []).entries()) {
      if (output !== '*' && sockets[0][0] !== '*') edges.push({classType, from: sockets[0][0], to: output, slot});
    }
  }
  const paths = [], queue = [{type: fromType, path: []}];
  while (queue.length && paths.length < limit) {
    const {type, path} = queue.shift();
    if (path.length && typesOf(type).includes(toType)) { paths.push(path); continue; }
    if (path.length >= maxSteps) continue;
    for (const edge of edges) {
      if (typesOf(edge.from).includes(type) && !path.some(step => step.class_type === edge.classType)) {
        queue.push({type: edge.to, path: [...path, {class_type: edge.classType, output: edge.slot, gives: edge.to}]});
      }
    }
  }
  return paths;
}
