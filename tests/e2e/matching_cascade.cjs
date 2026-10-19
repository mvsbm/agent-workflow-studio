// The matching cascade on a canvas: Learn weights writes the scorecard into Decision rules,
// Cluster guard builds customers and draws the whole decision tree, blocking chained merges.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;
const output = process.env.OUTPUT;

function pairs() {
  let seed = 7;
  const random = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const level = (match, m, u) => (random() < 0.15 ? 'missing' : random() < (match ? m : u) ? 'same' : 'different');
  return Array.from({length: 600}, (_, i) => {
    const match = random() < 0.15;
    return {id: `p${i}`, left: `m${i % 150}`, right: `m${(i * 7 + 3) % 150}`, a: {name: `A${i}`}, b: {name: `B${i}`},
      reading: level(match, 0.9, 0.05), birth: level(match, 0.8, 0.03), tel: level(match, 0.5, 0.03)};
  });
}

const RULES = `rules:
  - name: Birth and reading both different
    when: {birth: different, reading: different}
    then: different
score:
  reading: {same: 1, different: -1, missing: 0}
  birth: {same: 1, different: -1, missing: 0}
  tel: {same: 1, different: -1, missing: 0}
decide: {match: 2, review: 1}
`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1700, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);
    const run = path.join(output, 'agent_tools', id, 'compare-20261014-120000');
    fs.mkdirSync(run, {recursive: true});
    fs.writeFileSync(path.join(run, 'pairs.jsonl'), pairs().map(p => JSON.stringify(p)).join('\n'));
    await page.evaluate(async ({file, rules}) => {
      const app = window.comfyAPI.app.app, LiteGraph = window.LiteGraph;
      app.graph.clear();
      const add = type => { const node = LiteGraph.createNode(type); app.graph.add(node); return node; };
      const slot = (node, name) => node.inputs.findIndex(input => input.name === name || input.widget?.name === name);
      const learn = add('AgentWorkflowLearnWeights'), decide = add('AgentWorkflowDecisionRules'), guard = add('AgentWorkflowClusterGuard');
      learn.widgets.find(w => w.name === 'pairs_file').value = file;
      learn.widgets.find(w => w.name === 'rules').value = rules;
      decide.widgets.find(w => w.name === 'pairs_file').value = file;
      learn.connect(0, decide, slot(decide, 'rules'));
      decide.connect(0, guard, slot(guard, 'data'));
      const tag = (node, step) => { node.properties = {...node.properties, aws_contract_step: step}; };
      tag(learn, 'weights'); tag(decide, 'decide'); tag(guard, 'customers');
      const {arrangeGraph} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      arrangeGraph(app.graph);
      await app.queuePrompt(0, 1);
    }, {file: `agent_tools/${id}/compare-20261014-120000/pairs.jsonl`, rules: RULES});

    const tree = page.locator('.aw-tree-root');
    await tree.waitFor({timeout: 90000});
    const text = (await tree.innerText()).replace(/\s+/g, ' ');
    assert.match(text, /Candidate pairs 600/);
    assert.match(text, /Birth and reading both different/);
    assert.match(text, /Merges .* accepted .* blocked by the guard/);
    assert.match(text, /Customers: \d+/);
    // Learn weights replaced the rough ±1 points with learned ones, and Decision rules used them.
    const learned = await page.evaluate(() => window.comfyAPI.app.app.graph.nodes.find(n => n.type === 'AgentWorkflowLearnWeights').properties.aws_tables[1].rows);
    const sameReading = learned.find(row => row[0] === 'reading' && row[1] === 'same');
    assert.ok(Number(sameReading[2]) > 2, `learned points for reading=same: ${sameReading[2]}`);
    const flow = (await page.locator('.aw-flow').innerText()).replace(/\s+/g, ' ');
    assert.match(flow, /the sum of the points in the scorecard below/);
    // graph.clear() gives the workflow a new id, so look in every workflow's run folders.
    const tools = path.join(output, 'agent_tools');
    const clusters = fs.readdirSync(tools).flatMap(w => fs.readdirSync(path.join(tools, w)).filter(d => d.startsWith('cluster_guard-')).map(d => path.join(tools, w, d)));
    assert.ok(fs.readFileSync(path.join(clusters[0], 'clusters.csv'), 'utf8').startsWith('mention,customer'));
    await page.screenshot({path: path.join(artifacts, 'matching-cascade.png')});
    // Typed sockets: an unsettled branch (PAIRS) cannot feed Cluster guard (DECISIONS); a Stub takes its input's type.
    const typed = await page.evaluate(() => {
      const app = window.comfyAPI.app.app, g = app.graph, L = window.LiteGraph;
      const rules = g.nodes.find(n => n.type === 'AgentWorkflowDecisionRules'), guard = g.nodes.find(n => n.type === 'AgentWorkflowClusterGuard');
      const out = name => rules.outputs.findIndex(o => o.name === name), into = name => guard.inputs.findIndex(i => i.name === name);
      const refused = !rules.connect(out('unsettled'), guard, into('matched_1'));
      const stub = L.createNode('AgentWorkflowStub');
      g.add(stub);
      rules.connect(out('unsettled'), stub, 0);
      const result = {refused, types: rules.outputs.map(o => o.type), stub: stub.outputs[0].type};
      g.remove(stub);
      return result;
    });
    assert.ok(typed.refused, 'unsettled pairs must not reach Cluster guard');
    assert.deepEqual(typed.types, ['DECISIONS', 'STRING', 'DECISIONS', 'DECISIONS', 'PAIRS']);
    assert.equal(typed.stub, 'PAIRS');
    // Customers tab: the largest customer opens to its records and the chain of matches that joined them.
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-customers'); });
    const largest = page.locator('.aw-review-list details').first();
    await largest.locator('summary').click({timeout: 30000});
    await largest.locator('ol').waitFor({timeout: 10000});
    const why = (await largest.innerText()).replace(/\s+/g, ' ');
    assert.match(why, /Joined by \(\d+\) .* ↔ .* — /, why);
    await page.screenshot({path: path.join(artifacts, 'customers-tab.png')});
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-customers'); });
    // At a glance: every wire is labelled with what flows along it, and zoomed out each node shows its headline.
    const glance = () => page.evaluate(async () => {
      const app = window.comfyAPI.app.app, canvas = app.canvas;
      const {wireLabel, headline} = await import('/extensions/agent_workflow_studio/glance.js');
      canvas.ds.scale = 1;
      canvas.setDirty(true, true);
      canvas.draw(true, true);
      const wires = [...canvas.renderedPaths].map(link => wireLabel(canvas.graph, link.origin_id, link.origin_slot));
      return {wires, headlines: canvas.graph.nodes.map(headline)};
    });
    const flat = await glance();
    assert.ok(flat.wires.includes('learned scorecard') && flat.wires.some(w => /^[\d,]+ pairs$/.test(w)), flat.wires.join(' | '));
    assert.ok(flat.headlines.some(h => /^[\d,]+ customers · /.test(h)), flat.headlines.join(' | '));
    await page.evaluate(() => { const canvas = window.comfyAPI.app.app.canvas; canvas.ds.scale = 0.4; canvas.setDirty(true, true); canvas.draw(true, true); });
    // Classic canvas at low quality hides DOM widgets: the headline is drawn on the canvas (Vue nodes mode shows it in HTML).
    assert.ok(await page.evaluate(() => document.body.classList.contains('aw-zoomed-out') && window.comfyAPI.app.app.canvas.low_quality));
    await page.screenshot({path: path.join(artifacts, 'matching-zoomed-out.png')});

    // Organize into stages: one card per step on top, the nodes inside; runs still work.
    await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      const {packIntoStage, arrangeGraph} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      ['weights', 'decide', 'customers'].forEach((id, i) => packIntoStage(app.graph, app.graph.nodes.filter(n => n.properties?.aws_contract_step === id), id, i + 1));
      arrangeGraph(app.graph);
      await app.queuePrompt(0, 1);
    });
    await page.waitForFunction(() => {
      const cards = [...document.querySelectorAll('.aw-card')].map(card => card.innerText);
      return cards.length === 3 && cards.some(t => /match [\d,]+/.test(t)) && cards.some(t => /Customers: \d+/.test(t)) && cards.some(t => /learned: .* of pairs match/.test(t));
    }, null, {timeout: 90000});
    const top = await page.evaluate(() => window.comfyAPI.app.app.graph.nodes.map(n => n.title));
    // Short titles from the step ids, numbered by step: the purpose is on the card itself.
    assert.deepEqual(top.sort(), ['1 · Weights', '2 · Decide', '3 · Customers']);
    await page.screenshot({path: path.join(artifacts, 'matching-stages.png')});
    // Between stage cards, a wire shows what the stage's inner node sends out.
    const stages = await glance();
    assert.ok(stages.wires.some(w => /[\d,]+ pairs$/.test(w)), stages.wires.join(' | '));
    assert.deepEqual(errors, []);
    console.log('PASS matching_cascade');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'matching-cascade-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
