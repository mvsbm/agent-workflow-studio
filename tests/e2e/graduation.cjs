// Graduation, end to end: a workflow's approved tool becomes a studio node by its graduation brief.
// The brief names the stage that runs the tool; the node written from it (tests/e2e/graduation_pack)
// passes the tool's examples, run as a node; the stage is rebuilt with it (the build an agent's
// comfy_build_step makes), and the workflow's whole CI stays green, now with no code of its own.
// Verifies: R23
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
    // The fake agent builds a workflow whose stage runs an approved tool (as in agent_tool.cjs).
    await page.evaluate(() => { const manager = window.comfyAPI.app.app.extensionManager; if (manager.sidebarTab.activeSidebarTabId !== 'pi-chat') void manager.command.execute('Workspace.ToggleSidebarTab.pi-chat'); });
    const message = page.getByRole('textbox', {name: 'Message the agent'});
    await message.waitFor({timeout: 30000});
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Message the agent"]')?.disabled, null, {timeout: 60000});
    await message.fill('make it loud with a tool');
    await page.getByRole('button', {name: 'Send'}).click();
    for (const name of ['Approve this contract?', 'Approve this design?', 'Approve tool shout?', 'Run the current ComfyUI workflow?']) {
      await page.getByRole('group', {name}).getByRole('button', {name: 'Allow'}).click({timeout: 120000});
    }
    await page.getByText('FAKE PI · tool workflow ran. Gates: shout=passed.').waitFor({timeout: 120000});
    const id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);
    const tool = await page.evaluate(async id => (await fetch(`/agent-workflow-studio/projects/${id}/tools/shout`)).json(), id);

    // 1. The brief: the interface to keep, the tests to carry over, the stage to rebuild.
    const {brief} = await page.evaluate(async id => (await fetch(`/agent-workflow-studio/projects/${id}/tools/shout/graduation`)).json(), id);
    assert.match(brief, /\| text \| STRING \| io\.String \|/);
    assert.match(brief, /rebuild each stage that runs `shout` with comfy_build_step.*1 · Shout \(step `shout`\)/s);

    // 2. The node written from it passes the tool's examples, run as a node in ComfyUI.
    for (const example of tool.manifest.examples) {
      const got = await page.evaluate(async text => {
        const prompt = {1: {class_type: 'GraduatedShout', inputs: {text}}, 2: {class_type: 'PreviewAny', inputs: {source: ['1', 0]}}};
        const {prompt_id: pid} = await (await fetch('/prompt', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt})})).json();
        for (let i = 0; i < 100; i++) {
          const history = (await (await fetch(`/history/${pid}`)).json())[pid];
          if (history?.outputs?.['2']) return history.outputs['2'].text?.[0];
          await new Promise(r => setTimeout(r, 200));
        }
        return null;
      }, example.inputs.text);
      assert.equal(got, example.expect.text);
    }

    // 3. The stage rebuilt with the node where the tool was, then the workflow's whole CI.
    const result = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, api = window.comfyAPI.api.api;
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const {runChecks, runExamples} = await import('/extensions/agent_workflow_studio/publish.js');
      const bridge = createCanvasBridge(app, api, {confirm: async () => true});
      await bridge.handle('build', {step: 'shout', nodes: {shout: {type: 'GraduatedShout', set: {text: 'hello'}, realizes: ['does', 'gives text']}}, wires: [{from: 'shout.0', to: 'output.text'}]});
      const stage = app.rootGraph.nodes.find(n => n.properties?.aws_stage === 'shout');
      const inside = stage.subgraph.nodes.map(n => n.type);
      const ran = await new Promise(resolve => {
        api.addEventListener('execution_success', () => resolve('success'), {once: true});
        api.addEventListener('execution_error', e => resolve(`error: ${e.detail?.exception_message}`), {once: true});
        void app.queuePrompt(0, 1);
      });
      await runExamples(app, api);
      const checks = await runChecks(app, api);
      return {inside, ran, summary: checks.summary, unit: checks.stages.find(s => s.stage === 'unit').detail, green: checks.green};
    });
    assert.ok(result.inside.includes('GraduatedShout') && !result.inside.includes('AgentWorkflowAgentTool'), result.inside.join(', '));
    assert.equal(result.ran, 'success');
    assert.deepEqual(result.unit, ["No workflow code: every step uses the studio's nodes."]);
    assert.ok(result.green, result.summary);
    await page.screenshot({path: path.join(artifacts, 'graduation.png')});
    assert.deepEqual(errors, []);
    console.log('PASS graduation');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'graduation-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
