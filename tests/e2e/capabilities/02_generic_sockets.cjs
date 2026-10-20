// Generic table steps keep the type they are given (MatchType); unions and wrong types are enforced.
// Verifies: R11
module.exports = {
  id: 'generic_sockets',
  assumes: 'MatchType with our types: the output follows the input through a chain, wrong types are refused, unions accept each member, and the backend runs it',
  usedBy: ['typed sockets', 'stubs', 'generic table nodes'],
  fallback: 'one node per type, no generic stubs',
  async check({page, run}) {
    const built = await page.evaluate(() => {
      const L = window.LiteGraph, g = window.comfyAPI.app.app.rootGraph;
      const make = type => { const n = L.createNode(type); g.add(n); return n; };
      const pairs = make('CapMakePairs'), a = make('CapPassthrough'), b = make('CapPassthrough');
      pairs.connect(0, a, 0);
      a.connect(0, b, 0);
      const report = make('CapReport'), customers = make('CapTakeCustomers'), union = make('CapTakeUnion');
      return {
        followed: [a.outputs[0].type, b.outputs[0].type],
        refused: !b.connect(0, customers, 0),
        unionAccepts: Boolean(b.connect(0, union, 0)),
        toReport: Boolean(b.connect(0, report, 0)),
      };
    });
    const ran = await run();
    const report = Object.values(ran.outputs).find(o => o.text)?.text?.[0];
    const ok = built.followed.every(t => t === 'PAIRS') && built.refused && built.unionAccepts && built.toReport && ran.status === 'success' && report === 'rows 1';
    return {ok, evidence: {...built, run: ran.status, error: ran.error, report}};
  },
};
