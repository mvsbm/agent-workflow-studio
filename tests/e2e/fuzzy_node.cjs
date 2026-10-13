// The Fuzzy node plans with the (fake) model, its plan runs as real nodes, and the plan is recorded.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    await page.waitForTimeout(1000);
    const run = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(app, window.comfyAPI.api.api, {confirm: async () => true, autoApproveRun: () => true});
      await bridge.handle('edit', {operations: [
        {op: 'add', key: 'src', class_type: 'PrimitiveString', position: [50, 100]},
        {op: 'widget', node: 'src', name: 'value', value: 'fuzzy test'},
        {op: 'add', key: 'fuzzy', class_type: 'AgentWorkflowFuzzyNode', position: [350, 100]},
        {op: 'widget', node: 'fuzzy', name: 'goal', value: 'make it loud'},
        {op: 'add', key: 'out', class_type: 'PreviewAny', position: [900, 100]},
        {op: 'connect', from: 'src', output: 0, to: 'fuzzy', input: 'text'},
        {op: 'connect', from: 'fuzzy', output: 0, to: 'out', input: 'source'},
      ]});
      await bridge.handle('run', {reason: 'e2e'});
      let result;
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 500));
        result = await bridge.handle('results', {});
        if (result.status?.completed !== undefined) break;
      }
      return {status: result.status?.status_str, outputs: JSON.stringify(result.outputs ?? null)};
    });
    assert.equal(run.status, 'success', JSON.stringify(run));
    assert.match(run.outputs, /FUZZY TEST/, 'the plan ran as real nodes');
    assert.match(run.outputs, /1\. upper = CaseConverter\(string=\$input, mode=\\"UPPERCASE\\"\)/, 'the node shows its plan');

    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-versions'); });
    const first = page.locator('.aw-versions-item').first();
    await first.waitFor({timeout: 15000});
    assert.match(await first.locator('.aw-versions-message').textContent(), /^Run · Fuzzy plan #\d+ · succeeded$/);
    await page.screenshot({path: path.join(artifacts, 'fuzzy-node.png')});
    assert.deepEqual(errors, []);
    console.log('PASS fuzzy_node');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'fuzzy-node-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
