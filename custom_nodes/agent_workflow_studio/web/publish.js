// Publish the open workflow as an API: its signature is App mode's selection (extra.linearData) —
// inputs are widgets on top-level nodes, outputs are the top-level output nodes of the contract's
// promises (App mode shows them natively) — and the server freezes the runnable prompt at a version
// of the project history.
const slug = text => String(text ?? '').toLowerCase().replace(/^\d+\s*·\s*/, '').replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'value';

// A widget promoted on a subgraph card belongs to the nodes inside it that its port feeds (all of
// them: one seed may feed several); the run names each "card:inner".
function promoted(node, widget) {
  if (!node.isSubgraphNode?.()) return [{node, path: node.id, name: widget}];
  const port = node.subgraph.inputs.find(input => input.name === widget);
  const targets = (port?.linkIds ?? []).map(id => node.subgraph.links.get?.(id) ?? node.subgraph.links[id]).filter(Boolean).map(link => {
    const inner = node.subgraph.getNodeById(link.target_id), input = inner?.inputs?.[link.target_slot];
    return inner && input && {node: inner, path: `${node.id}:${inner.id}`, name: input.widget?.name ?? input.name};
  }).filter(Boolean);
  if (!targets.length) throw Error(`${node.title}: ${widget} does not lead to a node inside.`);
  return targets;
}

/** A node by its App mode id (node ids are unique across the workflow, as App mode finds them: at the
 *  top level or inside a stage), with its path in a run: "12", or "12:5" for node 5 inside stage 12. */
export function nodeAtPath(graph, id) {
  const top = graph.getNodeById(id);
  if (top) return {node: top, path: String(top.id)};
  for (const stage of graph.nodes ?? []) {
    const inner = stage.subgraph?.getNodeById?.(id);
    if (inner) return {node: inner, path: `${stage.id}:${inner.id}`};
  }
  return null;
}

/** The top-level output node of a promise delivered at "step.port" (or at any port of "step."). */
export function outputNode(graph, from) {
  return (graph.nodes ?? []).find(n => n.properties?.aws_output && (n.properties.aws_output_from === from || (from.endsWith('.') && n.properties.aws_output_from?.startsWith(from)))) ?? null;
}

/** The workflow's API signature from App mode, with each input's type, choices and current value. */
export function signatureOf(graph, defs) {
  const linear = graph.extra?.linearData ?? {}, used = new Set();
  const unique = (name, fallback) => { const pick = used.has(name) ? fallback : name; used.add(pick); return pick; };
  const inputs = (linear.inputs ?? []).filter(([id, widget]) => {
    // A widget that is only UI (the Data folder's Upload button) is App mode, not the API.
    const node = graph.getNodeById(id);
    return !node || node.widgets?.find(w => w.name === widget)?.type !== 'aws_upload';
  }).map(([id, widget]) => {
    const top = graph.getNodeById(id);
    if (!top) throw Error(`App mode input ${id}/${widget} is not on the canvas any more; choose it again.`);
    // A widget promoted on a subgraph card belongs to the node inside it; the run names that "card:inner".
    const targets = promoted(top, widget), {node, path, name} = targets[0];
    const spec = defs[node.type]?.input, schema = spec?.required?.[name] ?? spec?.optional?.[name];
    const choices = Array.isArray(schema?.[0]) ? schema[0] : schema?.[0] === 'COMBO' ? schema[1]?.options : undefined;
    // A contract's promised input keeps its promised name: examples, the API and OpenAPI all speak it.
    const label = top.properties?.aws_input ?? slug(widget);
    return {name: unique(label, `${slug(top.title)}_${slug(widget)}`), node: path, widget: name, title: top.title,
      targets: targets.map(t => [t.path, t.name]),
      type: choices ? 'COMBO' : schema?.[0], ...(choices ? {choices} : {}), default: top.widgets?.find(w => w.name === widget)?.value};
  });
  const outputs = (linear.outputs ?? []).map(id => {
    const found = nodeAtPath(graph, id);
    if (!found) throw Error(`App mode output ${id} is not on the canvas any more; choose it again.`);
    const {node, path} = found;
    // An API output node answers under the contract's promised name.
    const promised = node.type === 'AgentWorkflowApiOutput' ? node.widgets?.find(w => w.name === 'name')?.value : null;
    return {name: unique(promised || slug(node.title), `output_${id}`), node: path, title: node.title};
  });
  return {inputs, outputs};
}

/** Set the signature (what the agent's "signature" edit and App mode both write). */
export function setSignature(graph, inputs, outputs) {
  graph.extra = {...graph.extra, linearData: {inputs: inputs.map(({node, widget}) => [node, widget]), outputs}};
  refreshApp();
}

/** App mode reads its selection from the workflow as ComfyUI last recorded it: record the change
 *  and have its store re-read it, so the workflow runs as an App right away. Call it again once a
 *  batch of changes is closed (inside one, ComfyUI records nothing yet). The store is reached through
 *  the Vue app (no public API); the App-mode browser tests fail first if that changes. */
export function refreshApp() {
  globalThis.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker?.checkState?.();
  globalThis.document?.querySelector('#vue-app')?.__vue_app__?.config?.globalProperties?.$pinia?._s?.get('appMode')?.resetSelectedToWorkflow?.();
}

export async function publishWorkflow(app, api, name, description = '') {
  const graph = app.rootGraph ?? app.graph;
  const signature = signatureOf(graph, await api.getNodeDefs());
  const {output} = await app.graphToPrompt();
  const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/publish`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description, prompt: output, workflow: graph.serialize(), signature}),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'Publishing failed.');
  return body;
}

/** Run the contract's examples as acceptance tests through the same path the API uses. */
export async function runExamples(app, api) {
  const graph = app.rootGraph ?? app.graph;
  const signature = signatureOf(graph, await api.getNodeDefs());
  const {output} = await app.graphToPrompt();
  const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/acceptance`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({prompt: output, workflow: graph.serialize(), signature}),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'The examples could not run.');
  return body;
}

/** The workflow's CI: run its examples on this exact graph (when it has any), then the whole pipeline
 *  (design, stage Checks, examples, no regression). The run is kept as a version; publishing needs it green. */
export async function runChecks(app, api, author = 'user') {
  const graph = app.rootGraph ?? app.graph;
  const examples = graph.extra?.aws_contract?.examples?.length ? await runExamples(app, api) : null;
  const {contractEvidence} = await import('./pi_canvas.js');
  // The interface as App mode has it now: the CI lints its OpenAPI and compares it with the last release.
  let signature = null;
  try { signature = signatureOf(graph, await api.getNodeDefs()); } catch { /* no App selection yet: the design checks name it */ }
  const response = await api.fetchApi(`/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}/pipeline`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...await contractEvidence(app, api), author, signature}),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'The checks could not run.');
  return {...body, examples};
}

/** Point the published API back at an earlier release. */
export async function rollbackApi(api, name, semver) {
  const response = await api.fetchApi(`/agent-workflow-studio/api/${encodeURIComponent(name)}/rollback`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({semver}),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error ?? 'Rollback failed.');
  return body;
}
