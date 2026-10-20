// The platform behaviours that bit us, kept as checks so an upgrade that changes them is noticed.
// Verifies: R15
module.exports = {
  id: 'known_pitfalls',
  assumes: 'ui values are lists (a bare string arrives as characters); Vue nodes mode renders nodes as DOM above the canvas, classic does not',
  usedBy: ['every node ui (wrap strings in a tuple)', 'glance (DOM headline in Vue mode, canvas in classic)'],
  fallback: 'n/a: these describe the platform; update the code that relies on them',
  async check({page, run, mode}) {
    await page.evaluate(() => {
      const L = window.LiteGraph, g = window.comfyAPI.app.app.rootGraph;
      const pairs = L.createNode('CapMakePairs'), report = L.createNode('CapReport');
      g.add(pairs); g.add(report); pairs.connect(0, report, 0);
    });
    const ran = await run();
    const bare = Object.values(ran.outputs).find(o => o.bare)?.bare;
    await page.waitForTimeout(500);
    const domNodes = await page.evaluate(() => document.querySelectorAll('[data-node-id]').length);
    const vueIsDom = mode === 'vue' ? domNodes > 0 : domNodes === 0;
    return {ok: JSON.stringify(bare) === JSON.stringify(['a', 'b', 'c']) && vueIsDom, evidence: {bare, domNodes, mode}};
  },
};
