// A planned step is an empty stage subgraph with typed ports, made from code, wired from its
// ports, nested, surviving save/reload, and runnable end to end once a stub sits inside.
module.exports = {
  id: 'stage_skeleton',
  assumes: 'An empty subgraph with typed ports can be created and wired from code, nests, survives save/reload, and runs with a stub inside',
  usedBy: ['skeleton on contract approval', 'build_stage'],
  fallback: 'step list stays in the contract; stages are packed when built (today)',
  async check({page, run, roundtrip}) {
    const built = await page.evaluate(() => {
      const app = window.comfyAPI.app.app, L = window.LiteGraph, g = app.rootGraph;
      const make = (graph, type) => { const n = L.createNode(type); graph.add(n); return n; };
      function emptyStage(graph, name, ins, outs) {
        const before = new Set(graph.nodes);
        graph.convertToSubgraph(new Set([make(graph, 'CapPassthrough')]));
        const stage = graph.nodes.find(n => !before.has(n) && n.isSubgraphNode?.());
        for (const n of [...stage.subgraph.nodes]) stage.subgraph.remove(n);
        for (const [n, t] of ins) stage.subgraph.addInput(n, t);
        for (const [n, t] of outs) stage.subgraph.addOutput(n, t);
        stage.subgraph.name = stage.title = name;
        return stage;
      }
      const stage = emptyStage(g, 'Matching', [['pairs', 'PAIRS']], [['matched', 'PAIRS']]);
      const nested = emptyStage(stage.subgraph, 'Rules', [['pairs', 'PAIRS']], [['matched', 'PAIRS']]);
      // The stub: wire the stage's input through a passthrough to its output, from the ports.
      const sg = stage.subgraph, stub = make(sg, 'CapPassthrough');
      sg.inputNode.slots[0].connect(stub.inputs[0], stub);
      sg.outputNode.slots[0].connect(stub.outputs[0], stub);
      const pairs = make(g, 'CapMakePairs'), report = make(g, 'CapReport'), customers = make(g, 'CapTakeCustomers');
      return {
        ports: [stage.inputs.map(i => `${i.name}:${i.type}`), stage.outputs.map(o => `${o.name}:${o.type}`)],
        nestedPorts: nested.inputs.map(i => `${i.name}:${i.type}`),
        wiredIn: Boolean(pairs.connect(0, stage, 0)),
        wiredOut: Boolean(stage.connect(0, report, 0)),
        wrongTypeRefused: !stage.connect(0, customers, 0),
        stubLinks: sg.links.size ?? Object.keys(sg.links).length,
      };
    });
    const ran = await run();
    await roundtrip();
    const reloaded = await page.evaluate(() => {
      const g = window.comfyAPI.app.app.rootGraph, stage = g.nodes.find(n => n.title === 'Matching');
      return {
        ports: stage && [stage.inputs.map(i => `${i.name}:${i.type}`), stage.outputs.map(o => `${o.name}:${o.type}`)],
        nested: stage?.subgraph.nodes.some(n => n.isSubgraphNode?.() && n.subgraph.name === 'Rules'),
        definitions: g.subgraphs.size,
      };
    });
    const report = Object.values(ran.outputs).find(o => o.text)?.text?.[0];
    const ok = built.ports[0][0] === 'pairs:PAIRS' && built.ports[1][0] === 'matched:PAIRS' && built.wiredIn && built.wiredOut && built.wrongTypeRefused
      && ran.status === 'success' && report === 'rows 1' && JSON.stringify(reloaded.ports) === JSON.stringify(built.ports) && reloaded.nested;
    return {ok, evidence: {built, run: ran.status, error: ran.error, report, reloaded}};
  },
};
