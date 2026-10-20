// A stage's key setting shown on its card (a promoted widget): set on the card, it reaches the node
// inside the stage in the run's prompt, and it survives save and reload.
module.exports = {
  id: 'promoted_widget',
  assumes: 'a stage input wired to an inner widget shows that widget on the stage card; its value flows into the inner node in the prompt and survives save/reload',
  usedBy: ['promoted stage settings (LLD promote)', 'the nayose reference stage cards'],
  fallback: 'settings stay inside the stages (open a stage to change them)',
  async check({page}) {
    const result = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, L = window.LiteGraph, g = app.rootGraph;
      const {packIntoStage, promoteWidget} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const cat = L.createNode('StringConcatenate'); g.add(cat);
      cat.widgets.find(w => w.name === 'string_a').value = 'a';
      cat.widgets.find(w => w.name === 'string_b').value = 'inner';
      const preview = L.createNode('PreviewAny'); g.add(preview);
      cat.connect(0, preview, 0);
      const stage = packIntoStage(g, [cat], 'cap_promote', 1);
      const inner = stage.subgraph.nodes.find(n => n.type === 'StringConcatenate');
      const label = promoteWidget(stage, inner, 'string_b');
      const card = stage.widgets?.find(w => w.name === label);
      const shown = card?.value;
      if (card) card.value = 'from the card';
      const {output} = await app.graphToPrompt();
      const innerId = Object.keys(output).find(k => output[k].class_type === 'StringConcatenate');
      const sent = output[innerId]?.inputs?.string_b;
      const saved = g.serialize();
      g.configure(saved);
      const again = g.nodes.find(n => n.properties?.aws_stage === 'cap_promote');
      return {label, cardWidgets: (stage.widgets ?? []).map(w => w.name), shown, sent, reloaded: again?.widgets?.find(w => w.name === label)?.value};
    });
    const ok = result.sent === 'from the card' && result.reloaded === 'from the card';
    return {ok, evidence: result};
  },
};
