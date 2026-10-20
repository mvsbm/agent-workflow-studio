// P5: the agent writes a tool, the user approves its code, an Agent Tool node runs it confined.
// Verifies: R4, R23
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
    await page.evaluate(() => { const app = window.comfyAPI.app.app; app.graph.clear(); app.extensionManager.workflow.activeWorkflow.changeTracker.checkState(); });
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.pi-chat'); });
    const message = page.getByRole('textbox', {name: 'Message the agent'});
    await message.waitFor({timeout: 30000});
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Message the agent"]')?.disabled, null, {timeout: 60000});
    await message.fill('make it loud with a tool');
    await page.getByRole('button', {name: 'Send'}).click();

    await page.getByRole('group', {name: 'Approve this contract?'}).getByRole('button', {name: 'Allow'}).click();
    await page.getByRole('group', {name: 'Approve this design?'}).getByRole('button', {name: 'Allow'}).click();
    // The user sees the exact code before it can run.
    const approval = page.getByRole('group', {name: 'Approve tool shout?'});
    await approval.waitFor({timeout: 60000});
    const text = await approval.textContent();
    assert.match(text, /TOOL: shout/);
    assert.match(text, /SAFETY: Runs in a separate process/);
    assert.match(text, /return \{'text': inputs\['text'\]\.upper\(\) \+ '!'\}/);
    await approval.getByRole('button', {name: 'Allow'}).click();
    await page.getByRole('group', {name: 'Run the current ComfyUI workflow?'}).getByRole('button', {name: 'Allow'}).click();

    await page.getByText('FAKE PI · tool workflow ran. Gates: shout=passed.').waitFor({timeout: 120000});
    const evidence = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      const history = await (await fetch('/history')).json();
      const log = await (await fetch(`/agent-workflow-studio/projects/${encodeURIComponent(app.rootGraph.id)}/log`)).json();
      return {outputs: JSON.stringify(Object.values(history).map(h => h.outputs)), versions: log.commits.map(c => c.message)};
    });
    assert.match(evidence.outputs, /HELLO!/);
    assert.ok(evidence.versions.includes('Tool approved: shout'), evidence.versions.join(' | '));
    assert.ok(evidence.versions.some(v => /^Run · Agent tool shout #[\d:]+ · succeeded$/.test(v)), evidence.versions.join(' | '));
    await page.screenshot({path: path.join(artifacts, 'agent-tool.png')});
    assert.deepEqual(errors, []);
    console.log('PASS agent_tool');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'agent-tool-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
