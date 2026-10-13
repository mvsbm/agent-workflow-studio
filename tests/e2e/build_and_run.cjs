// Agent builds and runs a 3-node workflow through the real chat UI, bridge, canvas and queue.
// Only the LLM is faked (fake_pi.py). Requires BASE (ComfyUI URL) and ARTIFACTS (output dir).
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;
const comfy = fn => `(() => { const app = window.comfyAPI.app.app; return (${fn})(app); })()`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  // Tests must never reach the network beyond the isolated instance.
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(
      () => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker,
      null,
      {timeout: 60000},
    );
    await page.waitForTimeout(1000);
    // A fresh instance opens ComfyUI's example graph; start from an empty canvas.
    await page.evaluate(comfy(app => {
      app.graph.clear();
      app.extensionManager.workflow.activeWorkflow.changeTracker.checkState();
    }));
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.pi-chat'); });
    const message = page.getByRole('textbox', {name: 'Message Pi'});
    await message.waitFor({timeout: 30000});
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Message Pi"]')?.disabled, null, {timeout: 60000});

    await message.fill('build a hello world workflow and run it');
    await page.getByRole('button', {name: 'Send'}).click();

    // The bridge asks before a native Run; approve it.
    const allow = page.getByRole('button', {name: 'Allow'});
    await allow.waitFor({timeout: 60000});
    await allow.click();

    await page.getByText('FAKE PI · built source → concatenate → preview and ran it.').waitFor({timeout: 90000});
    const graph = await page.evaluate(comfy(app => ({
      types: app.graph.nodes.map(node => node.type).sort(),
      links: app.graph.links instanceof Map ? app.graph.links.size : Object.keys(app.graph.links ?? {}).length,
    })));
    assert.deepEqual(graph.types, ['PreviewAny', 'PrimitiveString', 'StringConcatenate']);
    assert.equal(graph.links, 2);

    // The agent's own view of the canvas must include the connections.
    const seen = await page.evaluate(async () => {
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(window.comfyAPI.app.app, window.comfyAPI.api.api);
      try { return await bridge.handle('read', {}); } finally { bridge.dispose(); }
    });
    assert.equal(seen.nodes.length, 3);
    assert.equal(seen.links.length, 2);

    const history = await page.evaluate(async () => (await window.comfyAPI.api.api.getHistory(5)).History ?? null);
    const outputs = JSON.stringify(history ?? await page.evaluate(async () => await (await fetch('/history')).json()));
    assert.match(outputs, /hello world/, 'run output should contain the concatenated text');

    // One native undo reverts the agent's whole edit batch.
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.Undo'); });
    await page.waitForTimeout(600);
    assert.equal(await page.evaluate(comfy(app => app.graph.nodes.length)), 0);

    assert.deepEqual(errors, []);
    console.log('PASS build_and_run');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
