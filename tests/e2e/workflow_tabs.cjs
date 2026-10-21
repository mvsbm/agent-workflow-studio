// The workflow tabs speak in the trace ids, on the nayose reference: Versions say which layer each
// version changed and which stages it touched, with the request each serves; the Contract finds the
// reference true to its own design; Data lists the design's entities; App mode asks for the exports
// as a data folder to upload; Review groups pairs by the decision stage that sent them; Clusters
// names the stage and rule behind every join.
// Verifies: R7, R8, R27, R30
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;
const output = process.env.OUTPUT;
const REFERENCE = 'custom_nodes/agent_workflow_studio/example_workflows/Studio - Record matching reference.json';
const R1 = 'serves R1 · One customer id per person';

// What the reference's two Decision rules stages write (decision_rules.py), cut down to a pair each.
const review = (stage, title, pair) => ({title: 'Decision rules', stage: {id: stage, title, node: 'Decision rules'}, pairs: [pair]});
const RULES = review('decide_rules', '6 · Decide: certain rules', {id: 'p1', branch: 'Same phone and same reading', reason: 'Spot check · Same phone and same reading decided match', a: {name: '石井 康平'}, b: {name: 'イシイコウヘイ'}});
const SCORE = review('decide_score', '7 · Decide: learned score', {id: 'p2', branch: 'Score -4.1 to 1.4', reason: 'Score 0.3: unsure', a: {name: '山本 夏美'}, b: {name: '山本 奈津美'}});
// What Cluster guard writes (matching_nodes.explain_clusters).
const CUSTOMERS = {
  C000001: {
    records: [{id: 'claims/1/claimant', name: '山本 夏美'}, {id: 'policies/7/holder', name: '山本 夏美'}, {id: 'payments/3/account_holder', name: '山本 奈津美'}],
    joined_by: [
      {a: 'claims/1/claimant', b: 'policies/7/holder', stage: '6 · Decide: certain rules', branch: 'Same phone and same reading', why: 'phone=same, kana=exact'},
      {a: 'claims/1/claimant', b: 'payments/3/account_holder', stage: '7 · Decide: learned score', branch: 'Score above 1.4', why: 'score 2.3'},
    ],
    kept_apart: [{a: 'policies/7/holder', b: 'claims/9/claimant', stage: '7 · Decide: learned score', branch: 'Score above 1.4', why: 'score 1.6', reason: 'decided different by 6 · Decide: certain rules'}],
  },
};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  const open = id => page.evaluate(async id => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab(id), id);
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const workflow = JSON.parse(fs.readFileSync(REFERENCE, 'utf8'));
    await page.evaluate(async w => window.comfyAPI.app.app.loadGraphData(w, true, true, 'Nayose reference'), workflow);
    const id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);

    // Versions: the template's contract (recorded on load), then the agent rebuilding one stage inside.
    await page.waitForFunction(async id => (await (await fetch(`/agent-workflow-studio/projects/${id}/log`)).json()).commits?.length === 1, id, {timeout: 30000, polling: 500});
    await page.evaluate(async () => {
      const {commitWorkflow} = await import('/extensions/agent_workflow_studio/project_versions.js');
      const app = window.comfyAPI.app.app;
      const stage = app.rootGraph.nodes.find(n => n.properties?.aws_stage === 'decide_score');
      stage.subgraph.nodes[0].title = 'Learn weights (retuned)';
      await commitWorkflow('Build decide_score: retuned weights', 'agent');
    });
    await open('workflow-versions');
    const versions = page.locator('.aw-versions-item');
    await page.waitForFunction(() => document.querySelectorAll('.aw-versions-item').length === 2, null, {timeout: 15000});
    assert.deepEqual(await page.locator('.aw-versions-layer').allTextContents(), ['Stage build (LLD)', 'Contract']);
    await versions.first().locator('.aw-versions-toggle').click();
    const stages = versions.first().locator('.aw-versions-nodes').first();
    await stages.waitFor({timeout: 10000});
    assert.equal((await stages.innerText()).replace(/\s+/g, ' ').trim(), `7 · Decide: learned score ${R1}`);
    // The contract version touched every stage, listed in canvas order (10 after 9, not after 1).
    await versions.nth(1).locator('.aw-versions-toggle').click();
    await versions.nth(1).locator('.aw-versions-nodes').first().waitFor({timeout: 10000});
    const all = (await versions.nth(1).locator('.aw-versions-nodes').first().locator('li').allInnerTexts()).map(t => t.split('\n')[0]);
    assert.equal(all[0], '1 · Profile');
    assert.equal(all.at(-1), '10 · Publish');
    await page.screenshot({path: path.join(artifacts, 'tabs-versions.png')});

    // Contract: the shipped reference is true to its own design (settings on a stage card are not data).
    await open('workflow-contract');
    const gaps = page.locator('.aw-contract-gaps');
    await gaps.waitFor({timeout: 30000});
    assert.doesNotMatch(await gaps.innerText(), /Differs from the design/);
    // Data: the design's entities, one section each.
    await open('workflow-data');
    await page.waitForFunction(() => document.querySelectorAll('.aw-data-entity').length > 3, null, {timeout: 15000});
    const entities = await page.locator('.aw-data-entity .aw-section-title > strong').allInnerTexts();
    assert.ok(entities.some(e => e.startsWith('customer_file')), entities.join(' | '));
    // App mode asks for the exports as a data folder, with its upload button.
    const asked = await page.evaluate(() => {
      const graph = window.comfyAPI.app.app.rootGraph, [[id, widget]] = graph.extra.linearData.inputs, node = graph.getNodeById(Number(id));
      return {type: node.type, widget, asks: graph.extra.linearData.inputs.map(([, name]) => name), upload: Boolean(node.widgets.find(w => w.name === 'upload'))};
    });
    assert.deepEqual(asked, {type: 'AgentWorkflowDataFolder', widget: 'folder', asks: ['folder', 'upload'], upload: true});

    // Review: one section per decision stage, in canvas order, with the request it serves.
    for (const [name, body] of [['decision_rules-20261021-120000-60_39', SCORE], ['decision_rules-20261021-120000-58_39', RULES]]) {
      fs.mkdirSync(path.join(output, 'agent_tools', id, name), {recursive: true});
      fs.writeFileSync(path.join(output, 'agent_tools', id, name, 'review.json'), JSON.stringify(body));
    }
    await open('workflow-review');
    const sections = page.locator('.aw-review-stage');
    await page.waitForFunction(() => document.querySelectorAll('.aw-review-stage').length === 2, null, {timeout: 30000});
    const heads = (await sections.locator('summary').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());
    assert.deepEqual(heads, [`6 · Decide: certain rules ${R1} 1`, `7 · Decide: learned score ${R1} 1`]);
    const scored = sections.nth(1);
    assert.equal(await scored.locator('.aw-review-why').textContent(), 'Left to you by: Score -4.1 to 1.4');
    assert.equal(await sections.nth(0).locator('.aw-review-why').count(), 0);  // a spot check was decided, not left to you
    await scored.getByRole('group', {name: 'Is pair p2 the same person?'}).getByRole('button', {name: 'Different'}).click();
    await page.locator('.aw-review-status').filter({hasText: /Pairs: 1 of 2 decided · Saved · version/}).waitFor({timeout: 30000});
    await page.waitForFunction(() => document.querySelectorAll('.aw-review-stage').length === 1, null, {timeout: 10000});
    await page.screenshot({path: path.join(artifacts, 'tabs-review.png')});

    // Clusters: each join under the stage that made it, with its rule and evidence; refusals too.
    fs.mkdirSync(path.join(output, 'agent_tools', id, 'cluster_guard-20261021-120000'), {recursive: true});
    fs.writeFileSync(path.join(output, 'agent_tools', id, 'cluster_guard-20261021-120000', 'clusters.json'), JSON.stringify(CUSTOMERS));
    await open('workflow-clusters');
    const customer = page.locator('.aw-review-list details').first();
    await customer.locator('summary').click({timeout: 30000});
    assert.equal((await customer.locator('summary').innerText()).replace(/\s+/g, ' ').trim(), 'C000001 · 3 records 山本 夏美 · 山本 奈津美 · 1 kept apart');
    await customer.locator('.aw-cluster-steps').first().waitFor({timeout: 10000});
    // The tab is generic (record_clusters); it speaks the workflow's own word, its port's name.
    assert.match(await page.locator('.aw-review-status').last().textContent(), /^1 customer · largest first/);
    const text = (await customer.locator('.aw-section-body').innerText()).replace(/\s+/g, ' ');
    assert.ok(text.includes(`JOINED BY (2) 6 · Decide: certain rules ${R1} claims/1/claimant ↔ policies/7/holder Same phone and same reading · phone=same, kana=exact 7 · Decide: learned score ${R1}`), text);
    assert.ok(text.includes('KEPT APART (1) 7 · Decide: learned score'), text);
    assert.ok(text.includes('policies/7/holder ↔ claims/9/claimant Score above 1.4 said match; refused: decided different by 6 · Decide: certain rules'), text);
    await page.screenshot({path: path.join(artifacts, 'tabs-clusters.png')});
    assert.deepEqual(errors, []);
    console.log('PASS workflow_tabs');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'workflow-tabs-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
