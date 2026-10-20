// A Pi Structured LLM node runs natively (fake Pi answers through the typed-output tool),
// and its run record appears in the Versions panel with the output.
// Verifies: R8
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
      await bridge.handle('skeleton', {steps: [{id: 'answer'}]}); // the agent builds inside a contract step's stage
      await bridge.handle('edit', {operations: [
        {stage: 'answer', op: 'add', key: 'llm', class_type: 'AgentWorkflowPiStructuredAgent', position: [50, 100]},
        {stage: 'answer', op: 'widget', node: 'llm', name: 'prompt', value: 'Say something'},
        {stage: 'answer', op: 'widget', node: 'llm', name: 'max_tokens', value: 512}, // an ordinary setting, not a secret
        {stage: 'answer', op: 'add', key: 'out', class_type: 'PreviewAny', position: [600, 100]},
        {stage: 'answer', op: 'connect', from: 'llm', output: 0, to: 'out', input: 'source'},
      ]});
      const submitted = await bridge.handle('run', {reason: 'e2e'});
      let result;
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 500));
        result = await bridge.handle('results', {});
        if (result.status?.completed !== undefined) break;
      }
      return {submitted, status: result.status?.status_str, outputs: JSON.stringify(result.outputs ?? null)};
    });
    assert.equal(run.status, 'success', JSON.stringify(run));
    assert.match(run.outputs, /fake answer/);
    assert.match(run.outputs, /Run record: version [0-9a-f]{8}/);

    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-versions'));
    const first = page.locator('.aw-versions-item').first();
    await first.waitFor({timeout: 15000});
    assert.match(await first.locator('.aw-versions-message').textContent(), /^Run · Pi CLI Structured LLM #[\d:]+ · succeeded$/);
    await first.locator('.aw-versions-toggle').click();
    await first.getByText('Run record · succeeded').waitFor({timeout: 10000});
    assert.match(await first.locator('.aw-versions-record pre').first().textContent(), /fake answer/);
    await page.screenshot({path: path.join(artifacts, 'llm-record.png')});
    assert.deepEqual(errors, []);
    console.log('PASS llm_record');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'llm-record-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
