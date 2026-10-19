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

    // Planning: the agent asks a multiple-choice question, then needs the plan approved.
    await page.getByRole('group', {name: 'Which greeting should the workflow show?'}).getByRole('button', {name: 'hello world'}).click();
    const plan = page.getByRole('group', {name: 'Approve this workflow contract?'});
    await plan.waitFor({timeout: 60000});
    const planText = await plan.textContent();
    assert.match(planText, /PURPOSE: Show the text hello world\./);
    assert.match(planText, /R1 \(must\) you said: “build a hello world workflow”/);
    assert.match(planText, /Stage: gives text: STRING/);
    assert.match(planText, /Stage: takes text ← greet\.text/);
    assert.match(planText, /Done when: The greeting says hello world\. \(a Check on text: contains hello world\)/);
    assert.match(planText, /The greeting is easy to read\. \(you confirm this one\)/);
    await plan.getByRole('button', {name: 'Allow'}).click();

    // Structural edits are checked automatically and the user sees the result.
    await page.getByText(/Graph check: .* 0 problem\(s\)\./).first().waitFor({timeout: 60000});

    // The bridge asks before a native Run; approve it.
    const run = page.getByRole('group', {name: 'Run the current ComfyUI workflow?'});
    await run.waitFor({timeout: 60000});
    await run.getByRole('button', {name: 'Allow'}).click();

    await page.getByText('FAKE PI · built source → concatenate → preview and ran it. Gates: greet=passed, show=needs_user_review. Progress: 1/2 steps verified.').waitFor({timeout: 90000});
    // The approved steps are the stages; the work is inside them.
    const stages = await page.evaluate(comfy(app => app.graph.nodes.map(node => ({
      title: node.title, step: node.properties?.aws_stage,
      inside: node.subgraph?.nodes.map(n => n.type).sort(),
    })).sort((a, b) => a.title.localeCompare(b.title))));
    assert.deepEqual(stages, [
      {title: '1 · Greet', step: 'greet', inside: ['AgentWorkflowCheck', 'PrimitiveString', 'StringConcatenate']},
      {title: '2 · Show', step: 'show', inside: ['PreviewAny']},
    ]);

    // The Contract tab shows live progress derived from the latest run of this exact graph.
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-contract'); });
    const contractStatus = page.locator('.aw-contract-status');
    await page.waitForFunction(() => /steps verified/.test(document.querySelector('.aw-contract-status')?.textContent ?? ''), null, {timeout: 15000});
    assert.equal(await contractStatus.textContent(), '✓ Aligned with the contract · 1/2 steps verified · Based on the latest run of this exact graph.');
    assert.deepEqual(await page.locator('.aw-contract-chip').allTextContents(), ['Verified', 'Built']);
    assert.match(await page.locator('.aw-contract pre').textContent(), /PURPOSE: Show the text hello world\./);
    await page.screenshot({path: path.join(artifacts, 'contract-tab.png')});


    // The agent's own view of the canvas must include the connections.
    const seen = await page.evaluate(async () => {
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(window.comfyAPI.app.app, window.comfyAPI.api.api);
      try { return await bridge.handle('read', {}); } finally { bridge.dispose(); }
    });
    assert.deepEqual(seen.nodes.map(n => n.stage.step), ['greet', 'show']);
    assert.deepEqual(seen.nodes[0].stage.nodes.map(n => n.class_type).sort(), ['AgentWorkflowCheck', 'PrimitiveString', 'StringConcatenate']);
    assert.equal(seen.links.length, 1, 'greet.text → show.text');

    const history = await page.evaluate(async () => (await window.comfyAPI.api.api.getHistory(5)).History ?? null);
    const outputs = JSON.stringify(history ?? await page.evaluate(async () => await (await fetch('/history')).json()));
    assert.match(outputs, /hello world/, 'run output should contain the concatenated text');

    // Each agent edit batch is one native undo step: the move batch, then the whole build.
    const undo = () => page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.Undo'); });
    const work = () => window.comfyAPI.app.app.graph.nodes.flatMap(n => n.subgraph?.nodes ?? []).filter(n => !['AgentWorkflowStub', 'AgentWorkflowCheck'].includes(n.type)).length;
    const inside = () => page.evaluate(work);
    const settles = n => page.waitForFunction(([source, n]) => new Function(`return (${source})()`)() === n, [work.toString(), n], {timeout: 10000});
    await undo();
    await page.waitForTimeout(600);
    assert.equal(await inside(), 3, 'first undo reverts only the move batch');
    await undo();
    await settles(0); // the build is undone; the skeleton (two stages) stays
    assert.equal(await page.evaluate(comfy(app => app.graph.nodes.length)), 2);

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
    await count(5);
    const messages = await page.locator('.aw-versions-message').allTextContents();
    assert.match(messages[0], /^Save versions-e2e/);
    assert.equal(messages[1], 'Agent canvas edit');
    assert.equal(messages[2], 'Build hello world: source, concatenate, preview');
    assert.equal(messages[3], 'Stages from the approved contract');
    assert.equal(messages[4], 'Contract approved: Show the text hello world.');
    assert.deepEqual(await page.locator('.aw-versions-author').allTextContents(), ['You', 'Agent', 'Agent', 'Agent', 'You']);

    // Restore the agent's version from the panel: canvas comes back, and the restore is a new version.
    await versions.nth(2).locator('.aw-versions-toggle').click();
    await versions.nth(2).getByText(/^\+3 nodes/).waitFor({timeout: 10000});
    await versions.nth(2).getByRole('button', {name: 'Restore this version'}).click();
    await page.waitForFunction(() => window.comfyAPI.app.app.graph.nodes.flatMap(n => n.subgraph?.nodes ?? []).length >= 4, null, {timeout: 10000});
    await count(6);
    assert.match(await page.locator('.aw-versions-message').first().textContent(), /^Restore [0-9a-f]{8}$/);
    await page.screenshot({path: path.join(artifacts, 'versions.png')});
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.Undo'); });
    await page.waitForTimeout(600);
    await settles(0); // one undo reverts a restore

    // Work outside the stages is drift: a loose node at the top level turns the tab to a warning.
    await page.evaluate(comfy(app => { const node = window.LiteGraph.createNode('PrimitiveString'); node.title = 'Stray note'; node.pos = [50, 500]; app.graph.add(node); }));
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-contract'); });
    await page.locator('.aw-contract').getByRole('button', {name: 'Refresh'}).click();
    await page.waitForFunction(() => /Stray note/.test(document.querySelector('.aw-contract-warning')?.textContent ?? ''), null, {timeout: 15000});
    assert.match(await page.locator('.aw-contract-status').textContent(), /^⚠ 1 drift\(s\) from the contract/);
    assert.equal(await page.locator('.aw-contract-warning').textContent(), 'Outside every stage: Stray note.');
    await page.evaluate(comfy(app => app.graph.remove(app.graph.nodes.find(n => n.title === 'Stray note'))));
    // Scaffolding manages itself: a Stub that stops feeding an output is gone, the Check follows the new
    // feeder, and wiring what is already wired (stage to stage) is a quiet no-op.
    const scaffold = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(app, window.comfyAPI.api.api, {confirm: async () => false});
      const built = await bridge.handle('skeleton', {steps: [
        {id: 'a', gives: {t: 'STRING'}},
        {id: 'b', takes: {t: 'a.t'}, gives: {t: 'STRING'}, gate: {says: 'has text', check: {output: 't', kind: 'non_empty'}}},
      ]});
      await bridge.handle('edit', {operations: [
        {op: 'connect', from: 'a', output: 't', to: 'b', input: 't'},
        {op: 'add', stage: 'b', key: 'cat', class_type: 'StringConcatenate'},
        {op: 'connect', stage: 'b', from: 'input', output: 't', to: 'cat', input: 'string_a'},
        {op: 'connect', stage: 'b', from: 'cat', output: 0, to: 'output', input: 't'},
      ]});
      const b = app.graph.nodes.find(n => n.properties?.aws_stage === 'b').subgraph;
      const check = b.nodes.find(n => n.type === 'AgentWorkflowCheck'), cat = b.nodes.find(n => n.type === 'StringConcatenate');
      const feed = b.links.get?.(check.inputs[0].link) ?? b.links[check.inputs[0].link];
      return {before: built.stages[1].inside, after: b.nodes.map(n => n.type).sort(), checkFollows: feed?.origin_id === cat.id};
    });
    assert.deepEqual(scaffold.before, ['Stub (input → outputs)', 'Check on t']);
    assert.deepEqual(scaffold.after, ['AgentWorkflowCheck', 'StringConcatenate']);
    assert.ok(scaffold.checkFollows, 'the Check reads what now feeds its output');
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
