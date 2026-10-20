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
    const planned = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      app.graph.clear();
      app.graph.add(window.LiteGraph.createNode('AgentWorkflowFuzzyNode'));
      await new Promise(r => setTimeout(r, 300));
      const node = app.graph.nodes[0];
      return {color: node.color, view: node.widgets.find(w => w.name === 'plan_view')?.value};
    });
    assert.match(planned.view, /^PLANNED/, 'a new fuzzy step starts as Planned');
    const run = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(app, window.comfyAPI.api.api, {confirm: async () => true, autoApproveRun: () => true});
      await bridge.handle('skeleton', {steps: [{id: 'loud'}]}); // the agent builds inside a contract step's stage
      await bridge.handle('edit', {operations: [
        {stage: 'loud', op: 'add', key: 'src', class_type: 'PrimitiveString', position: [50, 100]},
        {stage: 'loud', op: 'widget', node: 'src', name: 'value', value: 'fuzzy test'},
        {stage: 'loud', op: 'add', key: 'fuzzy', class_type: 'AgentWorkflowFuzzyNode', position: [350, 100]},
        {stage: 'loud', op: 'widget', node: 'fuzzy', name: 'goal', value: 'make it loud'},
        {stage: 'loud', op: 'add', key: 'out', class_type: 'PreviewAny', position: [900, 100]},
        {stage: 'loud', op: 'connect', from: 'src', output: 0, to: 'fuzzy', input: 'text'},
        {stage: 'loud', op: 'connect', from: 'fuzzy', output: 0, to: 'out', input: 'source'},
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

    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-versions'));
    const first = page.locator('.aw-versions-item').first();
    await first.waitFor({timeout: 15000});
    assert.match(await first.locator('.aw-versions-message').textContent(), /^Run · Fuzzy plan #[\d:]+ · succeeded$/);

    // After a run the step is Fuzzy and shows its plan on the node.
    const fuzzy = await page.evaluate(() => {
      const node = window.comfyAPI.app.app.graph.nodes[0].subgraph.nodes.find(n => n.type === 'AgentWorkflowFuzzyNode');
      const options = []; node.getExtraMenuOptions(window.comfyAPI.app.app.canvas, options);
      return {view: node.widgets.find(w => w.name === 'plan_view').value, menu: options.filter(Boolean).map(o => [o.content, !!o.disabled])};
    });
    assert.match(fuzzy.view, /^FUZZY/);
    assert.match(fuzzy.view, /upper = CaseConverter/);
    assert.deepEqual(fuzzy.menu.find(([label]) => label.startsWith('Harden')), ['Harden this step (freeze the latest plan)', false]);
    await page.screenshot({path: path.join(artifacts, 'fuzzy-node.png')});

    // Harden: the plan becomes a native subgraph titled with the goal; later runs call no model.
    const hardened = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      const stage = () => app.graph.nodes[0].subgraph; // the work is inside the step's stage
      const types = () => stage().nodes.map(n => n.type);
      const run = cmd => app.extensionManager.command.execute(cmd);
      // The agent proposes hardening; the user approves (confirm), exactly as from the chat.
      const {createCanvasBridge: bridgeFor} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const fuzzyId = stage().nodes.find(n => n.type === 'AgentWorkflowFuzzyNode').id;
      await bridgeFor(app, window.comfyAPI.api.api, {confirm: async () => true}).handle('harden', {stage: 'loud', node: fuzzyId});
      // Hardening is exactly one undo step: undo restores the fuzzy step, redo re-applies it.
      await run('Comfy.Undo');
      await new Promise(r => setTimeout(r, 400));
      const undone = types();
      await run('Comfy.Redo');
      await new Promise(r => setTimeout(r, 400));
      const step = stage().nodes.find(n => n.isSubgraphNode?.());
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(app, window.comfyAPI.api.api, {autoApproveRun: () => true});
      await new Promise(r => setTimeout(r, 300));
      await bridge.handle('run', {reason: 'hardened'});
      let result;
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 500));
        result = await bridge.handle('results', {});
        if (result.status?.completed !== undefined) break;
      }
      const log = await (await fetch(`/agent-workflow-studio/projects/${encodeURIComponent(app.rootGraph.id)}/log`)).json();
      return {
        undone, types: types(), title: step.title, state: step.properties.aws_step.state,
        inner: step.subgraph.nodes.map(n => n.type), status: result.status?.status_str, outputs: JSON.stringify(result.outputs),
        fuzzyRuns: log.commits.filter(c => c.message.startsWith('Run · Fuzzy plan')).length,
      };
    });
    assert.ok(hardened.undone.includes('AgentWorkflowFuzzyNode'), `one undo reverts hardening: ${hardened.undone}`);
    assert.ok(!hardened.types.includes('AgentWorkflowFuzzyNode'), JSON.stringify(hardened));
    assert.equal(hardened.title, 'make it loud');
    assert.equal(hardened.state, 'hardened');
    assert.deepEqual(hardened.inner, ['CaseConverter']);
    assert.equal(hardened.status, 'success');
    assert.match(hardened.outputs, /FUZZY TEST/);
    assert.equal(hardened.fuzzyRuns, 1, 'the hardened step made no model call');
    await page.screenshot({path: path.join(artifacts, 'hardened.png')});

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
