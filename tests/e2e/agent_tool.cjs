// P5: the agent writes a tool, the user approves its code, an Agent Tool node runs it confined.
// Workflow code is a tier of node: typed and tested (its examples run before approval and in Run
// checks), shown on its stage card, in the Design tab (interface, tests, source) and in Versions (its
// code diff), with a graduation brief for turning it into a studio node.
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
    assert.match(text, /WHY CODE, NOT A NODE: /);
    assert.match(text, /TESTS: 1\/1 of its examples pass in the sandbox\./);
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
    // Run checks tests the workflow's own code: the tool's examples, in the sandbox.
    const checks = await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/publish.js')).runChecks(window.comfyAPI.app.app, window.comfyAPI.api.api));
    const unit = checks.stages.find(s => s.stage === 'unit');
    assert.deepEqual([unit.label, unit.status, unit.detail], ['tool tests', 'passed', ['1 tool(s) pass their examples.']]);
    // The stage card says it holds workflow code; the Design tab shows the tool as approved.
    const card = await page.evaluate(async () => {
      const {designLabels} = await import('/extensions/agent_workflow_studio/stage_summary.js');
      const app = window.comfyAPI.app.app, stage = app.rootGraph.nodes.find(n => n.subgraph?.nodes.some(i => i.type === 'AgentWorkflowAgentTool'));
      const tool = stage.subgraph.nodes.find(i => i.type === 'AgentWorkflowAgentTool');
      return {stage: designLabels(stage, null, []).map(l => l.text), tool: designLabels(tool, stage).map(l => l.text)};
    });
    assert.ok(card.stage.includes('workflow code'), card.stage.join(' | '));
    assert.equal(card.tool.at(-1), 'workflow code · shout');
    // The tool node selected inside its stage (without opening the stage): the Design tab follows.
    await page.evaluate(() => window.comfyAPI.app.app.canvas.setGraph(window.comfyAPI.app.app.rootGraph));
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-design'));
    await page.evaluate(() => {
      const app = window.comfyAPI.app.app, stage = app.rootGraph.nodes.find(n => n.subgraph?.nodes.some(i => i.type === 'AgentWorkflowAgentTool'));
      app.canvas.deselectAll();
      app.canvas.selectNode(stage.subgraph.nodes.find(i => i.type === 'AgentWorkflowAgentTool'));
    });
    const tier = page.locator('.aw-design-code-tier');
    await tier.waitFor({timeout: 15000});
    assert.match(await tier.textContent(), /^Workflow code · tool shout · runs sandboxed · approved in version [0-9a-f]{8}/);
    const parts = await page.locator('.aw-design-box .aw-design-parts').last().innerText();
    assert.match(parts, /takes\s+text \(STRING\)/);
    assert.match(parts, /tested by\s+✓ 1 example\(s\) pass/);
    assert.match(await page.locator('.aw-design-code summary').textContent(), /^Source · \d+ lines · sha256 [0-9a-f]{12}$/);
    await page.screenshot({path: path.join(artifacts, 'agent-tool-design.png')});
    const brief = await page.evaluate(async () => (await (await fetch(`/agent-workflow-studio/projects/${encodeURIComponent(window.comfyAPI.app.app.rootGraph.id)}/tools/shout/graduation`)).json()).brief);
    assert.match(brief, /\| text \| STRING \| io\.String \|/);
    assert.match(brief, /- inputs `\{"text": "hi"\}` → expect `\{"text": "HI!"\}`/);
    // Versions: the approval shows the exact code it added.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-versions'));
    const approved = page.locator('.aw-versions-item').filter({hasText: 'Tool approved: shout'});
    await approved.locator('.aw-versions-toggle').click({timeout: 15000});
    await approved.locator('.aw-versions-code').waitFor({timeout: 10000});
    assert.match(await approved.locator('.aw-versions-code').textContent(), /\+def run\(inputs\):/);
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
