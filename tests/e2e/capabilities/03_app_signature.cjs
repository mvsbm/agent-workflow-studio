// The workflow's interface is App mode's selection (extra.linearData): it must survive save and
// point at nodes inside stages, and the flattened prompt must carry those nodes under known ids.
// Verifies: R15, R16
module.exports = {
  id: 'app_signature',
  assumes: 'App mode inputs/outputs (extra.linearData) survive save/reload; they hold bare node ids (no stage path), so the signature names top-level nodes only',
  caveat: 'a node inside a stage is ambiguous in linearData (ids repeat per graph level): interface nodes stay at the top level',
  usedBy: ['API signature', 'publish'],
  fallback: 'Workflow input / Workflow output nodes at the top level',
  async check({page, roundtrip}) {
    const set = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, L = window.LiteGraph, g = app.rootGraph;
      const make = (graph, type) => { const n = L.createNode(type); graph.add(n); return n; };
      const pairs = make(g, 'CapMakePairs'), mid = make(g, 'CapPassthrough'), report = make(g, 'CapReport');
      pairs.connect(0, mid, 0);
      mid.connect(0, report, 0);
      const before = new Set(g.nodes);
      g.convertToSubgraph(new Set([mid, report]));
      const stage = g.nodes.find(n => !before.has(n) && n.isSubgraphNode?.());
      const inner = stage.subgraph.nodes.find(n => n.type === 'CapReport');
      g.extra.linearData = {inputs: [[pairs.id, 'unused']], outputs: [inner.id]};
      const {output} = await app.graphToPrompt();
      return {stage: stage.id, inner: inner.id, promptIds: Object.keys(output)};
    });
    await roundtrip();
    const after = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.extra?.linearData ?? null);
    const flatId = `${set.stage}:${set.inner}`;
    // Bare ids, not "stage:node" paths: if this ever changes, inner nodes could join the signature.
    const ok = Boolean(after) && after.outputs?.[0] === set.inner && !String(after.outputs[0]).includes(':') && set.promptIds.includes(flatId);
    return {ok, evidence: {...set, flatId, linearDataAfterReload: after, note: 'linearData names a node id only; inside a stage the stage id path must be added by us'}};
  },
};
