// Decision rules: pairs decided by visible rules and points; the flow shows counts per branch;
// uncertain pairs reach the Review tab; the user's decision there overrides the rules next run.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;
const output = process.env.OUTPUT;
const PAIRS = [
  {id: 'p1', account: 'same', reading: 'different', birth: 'conflict', a: {name: '鈴木 恵子'}, b: {name: 'ｽｽﾞｷ ｹｲｺ'}},
  {id: 'p2', reading: 'exact', birth: 'exact', a: {name: '山田 太郎', birth: 'S45.03.12'}, b: {name: '山田 太郎', birth: '1970/03/12'}},
  {id: 'p3', reading: 'exact', birth: 'missing', a: {name: '佐藤 一郎'}, b: {name: 'サトウ イチロウ'}},
  {id: 'p4', reading: 'different', birth: 'close', a: {name: '田中 花子'}, b: {name: '中田 花子'}},
];

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);
    const run = path.join(output, 'agent_tools', id, 'compare-20261014-120000');
    fs.mkdirSync(run, {recursive: true});
    fs.writeFileSync(path.join(run, 'pairs.jsonl'), PAIRS.map(p => JSON.stringify(p)).join('\n'));
    const queue = async () => page.evaluate(async file => {
      const app = window.comfyAPI.app.app;
      let node = app.graph.nodes.find(n => n.type === 'AgentWorkflowDecisionRules');
      if (!node) {
        node = window.LiteGraph.createNode('AgentWorkflowDecisionRules');
        node.pos = [100, 100];
        app.graph.add(node);
        node.widgets.find(w => w.name === 'pairs_file').value = file;
      }
      await app.queuePrompt(0, 1);
    }, `agent_tools/${id}/compare-20261014-120000/pairs.jsonl`);

    await queue();
    const flow = page.locator('.aw-flow');
    await flow.waitFor({timeout: 60000});
    // Default rules: same account → match; then reading/birth points with match ≥ 9, review ≥ 4.
    const branches = async () => (await flow.locator('li').allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim());
    // Each branch shows its condition: the rule's `when`, or the scorecard for score branches.
    const points = 'the sum of the points in the scorecard below';
    assert.deepEqual(await branches(), ['Same bank account MATCH 1 when account = same', `Score ≥ 9 MATCH 1 ${points}`, `Score 4 to 9 REVIEW 1 ${points}`, `Score < 4 DIFFERENT 1 ${points}`]);
    const decisions = fs.readFileSync(path.join(fs.readdirSync(path.join(output, 'agent_tools', id)).filter(d => d.startsWith('decision_rules-')).map(d => path.join(output, 'agent_tools', id, d)).sort().pop(), 'decisions.csv'), 'utf8');
    assert.match(decisions, /p3,,,review,Score 4 to 9,4,\+4 reading exact = 4/);

    // The uncertain pair shows in Review with its reason; deciding it overrides the rules.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-review'));
    await page.getByText('+4 reading exact = 4').waitFor({timeout: 30000});
    await page.getByRole('group', {name: 'Is pair p3 the same person?'}).getByRole('button', {name: 'Same'}).click();
    await page.locator('.aw-review-status').filter({hasText: /Saved · version/}).waitFor({timeout: 30000});
    await queue();
    await page.waitForFunction(() => document.querySelector('.aw-flow')?.innerText.includes('Your review'), null, {timeout: 60000});
    assert.equal((await branches())[0], 'Your review MATCH 1 your decisions in the Review tab');
    // Monitoring: each rule's pairs on the previous run next to now (the reviewed pair left the scorecard).
    const rules = await page.locator('.aw-table-view section').filter({hasText: 'Rules in effect'}).locator('tbody tr').allInnerTexts();
    assert.ok(rules.some(r => /Score 4 to 9|total ≥ 4/.test(r) && r.includes('1 → 0')), rules.join('\n'));
    await page.screenshot({path: path.join(artifacts, 'decision-rules.png')});
    assert.deepEqual(errors, []);
    console.log('PASS decision_rules');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'decision-rules-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
