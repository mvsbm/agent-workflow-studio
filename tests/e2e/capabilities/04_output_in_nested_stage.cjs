// A Check (output node) deep inside stages reports through history under its id path and reaches
// the node on the canvas, so step status can be read from it.
module.exports = {
  id: 'output_in_nested_stage',
  assumes: 'An output node two stages deep runs, its ui lands in history under "outer:inner:node", and the canvas node receives it',
  usedBy: ['Check nodes as gates', 'status'],
  fallback: 'gates read outputs by node title (today)',
  async check({page, run}) {
    const ids = await page.evaluate(() => {
      const L = window.LiteGraph, g = window.comfyAPI.app.app.rootGraph;
      const make = type => { const n = L.createNode(type); g.add(n); return n; };
      const pairs = make('CapMakePairs'), report = make('CapReport');
      pairs.connect(0, report, 0);
      let before = new Set(g.nodes);
      g.convertToSubgraph(new Set([report]));
      const outer = g.nodes.find(n => !before.has(n) && n.isSubgraphNode?.());
      const sg = outer.subgraph, inner0 = sg.nodes[0];
      before = new Set(sg.nodes);
      sg.convertToSubgraph(new Set([inner0]));
      const inner = sg.nodes.find(n => !before.has(n) && n.isSubgraphNode?.());
      const node = inner.subgraph.nodes[0];
      node.onExecuted = function (output) { window.__capExecuted = output; };
      return {path: `${outer.id}:${inner.id}:${node.id}`};
    });
    const ran = await run();
    const got = ran.outputs[ids.path]?.text?.[0];
    const reached = await page.evaluate(() => window.__capExecuted?.text?.[0] ?? null);
    return {ok: ran.status === 'success' && got === 'rows 1' && reached === 'rows 1', evidence: {...ids, run: ran.status, error: ran.error, keys: Object.keys(ran.outputs), got, reached}};
  },
};
