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

    // Versions: the agent's batch is a commit; saving the user's change (the undo) is another.
    page.on('dialog', dialog => dialog.accept());
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.SaveWorkflow'); });
    const filename = page.locator('[role=dialog] input').first();
    await filename.waitFor({timeout: 10000});
    await filename.fill('versions-e2e');
    await filename.press('Enter');
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-versions'); });
    const versions = page.locator('.aw-versions-item');
    const count = n => page.waitForFunction(n => document.querySelectorAll('.aw-versions-item').length === n, n, {timeout: 15000});
    await count(2);
    const messages = await page.locator('.aw-versions-message').allTextContents();
    assert.match(messages[0], /^Save versions-e2e/);
    assert.equal(messages[1], 'Build hello world: source, concatenate, preview');
    assert.deepEqual(await page.locator('.aw-versions-author').allTextContents(), ['You', 'Agent']);

    // Restore the agent's version from the panel: canvas comes back, and the restore is a new version.
    await versions.nth(1).locator('.aw-versions-toggle').click();
    await versions.nth(1).getByText('+3 nodes · +2 links').waitFor({timeout: 10000});
    await versions.nth(1).getByRole('button', {name: 'Restore this version'}).click();
    await page.waitForFunction(() => window.comfyAPI.app.app.graph.nodes.length === 3, null, {timeout: 10000});
    await count(3);
    assert.match(await page.locator('.aw-versions-message').first().textContent(), /^Restore [0-9a-f]{8}$/);
    await page.screenshot({path: path.join(artifacts, 'versions.png')});
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.Undo'); });
    await page.waitForTimeout(600);
    assert.equal(await page.evaluate(comfy(app => app.graph.nodes.length)), 0, 'one undo reverts a restore');

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
