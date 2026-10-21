// Agent builds and runs a 3-node workflow through the real chat UI, bridge, canvas and queue.
// Only the LLM is faked (fake_pi.py). Requires BASE (ComfyUI URL) and ARTIFACTS (output dir).
// Verifies: R1, R2, R8, R27, R35
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
    await page.evaluate(() => { const manager = window.comfyAPI.app.app.extensionManager; if (manager.sidebarTab.activeSidebarTabId !== 'pi-chat') void manager.command.execute('Workspace.ToggleSidebarTab.pi-chat'); });
    const message = page.getByRole('textbox', {name: 'Message the agent'});
    await message.waitFor({timeout: 30000});
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Message the agent"]')?.disabled, null, {timeout: 60000});

    await message.fill('build a hello world workflow and run it');
    await page.getByRole('button', {name: 'Send'}).click();

    // Planning: the agent asks a multiple-choice question, then needs the contract (what the user
    // wants) approved, then its design (how: the stages).
    await page.getByRole('group', {name: 'Which greeting should the workflow show?'}).getByRole('button', {name: 'hello world'}).click();
    const contract = page.getByRole('group', {name: 'Approve this contract?'});
    await contract.waitFor({timeout: 60000});
    const contractText = await contract.textContent();
    assert.match(contractText, /PURPOSE: Show the text hello world\./);
    assert.match(contractText, /R1 · Show hello world \(must\) you said: “build a hello world workflow”/);
    assert.doesNotMatch(contractText, /Stage:/);
    // The card leads with a few lines to decide on; the full contract is one click away.
    assert.equal(await contract.locator('.aw-pi-summary').innerText(), 'Hello world\nR1 · Show hello world\nYou get: text (text)\nChecked by 1 example');
    assert.equal(await contract.locator('.aw-pi-full').evaluate(d => d.open), false);
    await page.screenshot({path: path.join(artifacts, 'approve-contract.png')});
    await contract.getByRole('button', {name: 'Allow'}).click();
    // Approved, the unsaved workflow is saved under the contract's title, the native way.
    await page.waitForFunction(() => { const w = window.comfyAPI.app.app.extensionManager.workflow.activeWorkflow; return w.filename === 'Hello world' && !w.isTemporary; }, null, {timeout: 30000});
    const plan = page.getByRole('group', {name: 'Approve this design?'});
    await plan.waitFor({timeout: 60000});
    const planText = await plan.textContent();
    assert.match(planText, /FOR: Show the text hello world\.\s*DESIGN \(/, 'the design card leads with the design');
    assert.doesNotMatch(planText, /WHAT YOU ASKED FOR/);
    assert.match(planText, /Stage: gives text: STRING/);
    await page.screenshot({path: path.join(artifacts, 'approve-design.png')});
    assert.match(planText, /Stage: takes text ← greet\.text/);
    assert.match(planText, /Done when: The greeting says hello world\. \(a Check on text: contains hello world\)/);
    assert.match(planText, /The greeting is easy to read\. \(you confirm this one\)/);
    await plan.getByRole('button', {name: 'Allow'}).click();

    // Structural edits are checked automatically and the user sees the result.
    await page.getByText(/Graph check: .* 0 problem\(s\)\./).first().waitFor({timeout: 60000});
    // After a build the whole workflow is in view (ComfyUI's own Fit view, beside the open chat).
    await page.waitForFunction(() => {
      const canvas = window.comfyAPI.app.app.canvas, box = canvas.canvas.getBoundingClientRect(), chat = document.querySelector('.aw-pi-chat').getBoundingClientRect();
      return canvas.graph.nodes.every(n => { const [x, y] = canvas.ds.convertOffsetToCanvas(n.pos), [r, b] = canvas.ds.convertOffsetToCanvas([n.pos[0] + n.size[0], n.pos[1] + n.size[1]]);
        return x >= Math.max(0, chat.right - box.left) && y >= 0 && r <= box.width && b <= box.height; });
    }, null, {timeout: 15000});

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
    // The promised output is a top-level output node beside the stages (App mode shows it).
    assert.deepEqual(stages, [
      {title: '1 · Greet', step: 'greet', inside: ['AgentWorkflowCheck', 'PrimitiveString', 'StringConcatenate']},
      {title: '2 · Show', step: 'show', inside: ['PreviewAny']},
      {title: 'Output · text', step: undefined, inside: undefined},
    ]);

    // Each node built from a stage's LLD says which LLD node it is and which HLD clauses it realizes.
    assert.deepEqual(await page.evaluate(comfy(app => app.graph.nodes.find(n => n.properties?.aws_stage === 'greet').subgraph.nodes
      .filter(n => n.properties?.aws_lld_key).map(n => [n.properties.aws_lld_key, n.properties.aws_realizes]).sort())),
      [['cat', ['does', 'gives text']], ['src', ['does']]]);

    const panel = '[data-testid="properties-panel"]';
    // The Design tab traces both ways and follows the selection: requests → stages with nothing
    // selected, a stage's parts → its nodes, a node → the request it exists for.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-design'));
    await page.locator(`${panel} .aw-design-row`).first().waitFor({timeout: 15000});
    assert.match(await page.locator(`${panel} .aw-design-row`).first().textContent(), /^R1 · Show hello world1 · Greet, 2 · Showexample E1/);
    await page.locator(`${panel} .aw-design-row`).first().click();  // its stages, selected on the canvas
    await page.waitForFunction(() => document.querySelectorAll('[data-testid="aw-overview-content"] .aw-design-box').length === 2);
    const parts = await page.locator(`${panel} .aw-design-parts`).first().locator('tr').allTextContents();
    // Each part with the numbered nodes realizing it (titles vary with the node type's display name).
    assert.deepEqual(parts.map(row => row.replace(/(\d\.\d) [^\d]*/g, '$1;')), ['does1.1;1.2;', 'gives text1.2;', 'verified byCheck · text — The greeting says hello world.']);
    // A node inside a stage (selected without opening the stage: entering a subgraph re-lays its ports,
    // which ComfyUI records as an undo step, and the undo checks below count steps).
    await page.evaluate(comfy(app => { app.canvas.deselectAll(); const stage = app.graph.nodes.find(n => n.properties?.aws_stage === 'greet'); app.canvas.selectNode(stage.subgraph.nodes.find(n => n.properties?.aws_lld_key === 'cat')); }));
    await page.locator(`${panel} .aw-design-path`).waitFor({timeout: 10000});
    assert.match(await page.locator(`${panel} .aw-design-path`).textContent(), /^ServesR1 · Show hello worldIn stage1 · GreetThis node1\.2 .* · does · gives text$/);
    await page.evaluate(comfy(app => app.canvas.deselectAll()));
    await page.screenshot({path: path.join(artifacts, 'stages-top.png')});
    // Inside a stage: the nodes its LLD built.
    await page.evaluate(comfy(app => app.canvas.openSubgraph(app.graph.nodes.find(n => n.properties?.aws_stage === 'greet').subgraph)));
    await page.waitForTimeout(800);
    await page.screenshot({path: path.join(artifacts, 'stage-built.png')});
    await page.evaluate(comfy(app => app.canvas.setGraph(app.rootGraph)));
    await page.waitForTimeout(400);

    // The Contract tab shows live progress derived from the latest run of this exact graph.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-contract'));
    const contractStatus = page.locator('.aw-contract-status');
    await page.waitForFunction(() => /steps verified/.test(document.querySelector('.aw-contract-summary')?.textContent ?? ''), null, {timeout: 15000});
    assert.equal(await contractStatus.textContent(), '✓ Aligned with the contract · Based on the latest run of this exact graph.');
    // Plan vs built vs proven, honestly: a verified step is not a proven request until its example runs.
    assert.match(await page.locator('.aw-contract-summary').textContent(), /examples passing 0\/\d+ on this version · steps verified 1\/2/);
    assert.deepEqual(await page.locator('.aw-contract-steps .aw-contract-chip').allTextContents(), ['Verified', 'Built']);
    // The verification is kept with the workflow (fingerprint of exactly what ran), so it survives a restart.
    assert.equal(await page.evaluate(comfy(app => app.graph.extra.aws_verified?.gates?.greet)), 'passed');
    assert.match(await page.locator('.aw-contract pre').textContent(), /PURPOSE: Show the text hello world\./);
    await page.screenshot({path: path.join(artifacts, 'contract-tab.png')});


    // The workflow's CI: publishing needs a green run of its checks on exactly this graph (design,
    // stage Checks, the contract's example through the API path, no regression).
    const accepted = await page.evaluate(async () => {
      const {runChecks, publishWorkflow, rollbackApi} = await import('/extensions/agent_workflow_studio/publish.js');
      const app = window.comfyAPI.app.app, api = window.comfyAPI.api.api;
      let refused;
      try { await publishWorkflow(app, api, 'hello-e2e'); } catch (error) { refused = error.message; }
      const checks = await runChecks(app, api), examples = checks.examples;
      const published = await publishWorkflow(app, api, 'hello-e2e');
      const call = await (await api.fetchApi('/agent-workflow-studio/api/hello-e2e/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})).json();
      const back = await rollbackApi(api, 'hello-e2e', '1.0.0');
      let noSuch;
      try { await rollbackApi(api, 'hello-e2e', '9.9.9'); } catch (error) { noSuch = error.message; }
      return {refused, green: checks.green, passed: examples.passed, total: examples.total, published: published.version, semver: published.semver, notes: published.notes, data: call.data, back: back.semver, noSuch};
    });
    assert.equal(accepted.refused, 'Run the checks on this exact workflow first; all must pass.');
    assert.equal(accepted.green, true);
    assert.deepEqual({passed: accepted.passed, total: accepted.total}, {passed: 1, total: 1});
    assert.match(accepted.published, /^[0-9a-f]{8}$/);
    assert.deepEqual([accepted.semver, accepted.notes], ['1.0.0', 'First release.']);
    assert.deepEqual(accepted.data, {text: 'hello world'});
    assert.deepEqual([accepted.back, accepted.noSuch], ['1.0.0', 'hello-e2e has no version 9.9.9.']);

    // The agent's own view of the canvas must include the connections.
    const seen = await page.evaluate(async () => {
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(window.comfyAPI.app.app, window.comfyAPI.api.api);
      try { return await bridge.handle('read', {}); } finally { bridge.dispose(); }
    });
    assert.deepEqual(seen.nodes.filter(n => n.stage).map(n => n.stage.step), ['greet', 'show']);
    assert.deepEqual(seen.nodes[0].stage.nodes.map(n => n.class_type).sort(), ['AgentWorkflowCheck', 'PrimitiveString', 'StringConcatenate']);
    assert.deepEqual(seen.nodes.filter(n => !n.stage).map(n => n.title), ['Output · text'], 'the promised output, beside the stages');
    assert.equal(seen.links.length, 2, 'greet.text → show.text, and → its output node');

    const history = await page.evaluate(async () => (await window.comfyAPI.api.api.getHistory(5)).History ?? null);
    const outputs = JSON.stringify(history ?? await page.evaluate(async () => await (await fetch('/history')).json()));
    assert.match(outputs, /hello world/, 'run output should contain the concatenated text');

    // Each agent edit batch is one native undo step: the move batch, then the whole build.
    const undo = () => page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.Undo'); });
    const work = () => window.comfyAPI.app.app.graph.nodes.flatMap(n => n.subgraph?.nodes ?? []).filter(n => !['AgentWorkflowStub', 'AgentWorkflowCheck', 'AgentWorkflowApiOutput'].includes(n.type)).length;
    const inside = () => page.evaluate(work);
    const settles = n => page.waitForFunction(([source, n]) => new Function(`return (${source})()`)() === n, [work.toString(), n], {timeout: 10000});
    await undo();
    await page.waitForTimeout(600);
    assert.equal(await inside(), 3, 'first undo reverts only the move batch');
    await undo();
    await settles(2); // one stage's build is one undo step
    await undo();
    await settles(0); // both builds undone; the skeleton (two stages and the promised output) stays
    assert.equal(await page.evaluate(comfy(app => app.graph.nodes.length)), 3);

    // Versions: the agent's batch is a commit; saving the user's change (the undo) is another.
    page.on('dialog', dialog => dialog.accept());
    // Saved under the contract's title when it was approved, so Save asks no name.
    await page.evaluate(() => window.comfyAPI.app.app.extensionManager.command.execute('Comfy.SaveWorkflow'));
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-versions'));
    const versions = page.locator('.aw-versions-item');
    const count = n => page.waitForFunction(n => document.querySelectorAll('.aw-versions-item').length === n, n, {timeout: 15000});
    await count(11);
    const messages = await page.locator('.aw-versions-message').allTextContents();
    assert.match(messages[0], /^Save Hello world/);
    assert.equal(messages[1], 'Roll back API hello-e2e to 1.0.0');  // a release event, in the history
    messages.splice(1, 1);
    assert.equal(messages[1], 'Publish API hello-e2e');
    // The workflow's CI run is a version too, like a CI run on a commit; publishing needed it green.
    assert.equal(messages[2], 'Checks · ✓ design · ✓ tool tests · ✓ stage Checks · ✓ examples · ✓ no regression');
    assert.equal(messages[3], 'Acceptance · 1/1 examples passed');
    assert.equal(messages[4], 'Agent canvas edit');
    assert.equal(messages[5], 'Build show: preview');
    assert.equal(messages[6], 'Build greet: source and concatenate');
    // One version per approval: the contract and its design live in the workflow.
    assert.equal(messages[7], 'Design approved: 2 steps');
    assert.equal(messages[8], 'Contract approved: Show the text hello world.');
    assert.equal(messages[9], 'Save Hello world');  // the approval named and saved it
    assert.deepEqual(await page.locator('.aw-versions-author').allTextContents(), ['You', 'You', 'You', 'You', 'Agent', 'Agent', 'Agent', 'Agent', 'You', 'You', 'You']);

    // Restore the agent's version from the panel: canvas comes back, and the restore is a new version.
    await versions.nth(6).locator('.aw-versions-toggle').click();
    await versions.nth(6).getByText(/^\+1 node/).waitFor({timeout: 10000});
    await versions.nth(6).getByRole('button', {name: 'Restore this version'}).click();
    await page.waitForFunction(() => window.comfyAPI.app.app.graph.nodes.flatMap(n => n.subgraph?.nodes ?? []).length >= 4, null, {timeout: 10000});
    await count(12);
    assert.match(await page.locator('.aw-versions-message').first().textContent(), /^Restore [0-9a-f]{8}$/);
    await page.screenshot({path: path.join(artifacts, 'versions.png')});
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Comfy.Undo'); });
    await page.waitForTimeout(600);
    await settles(0); // one undo reverts a restore

    // The workflow's panels live in ComfyUI's own right panel, in its Workflow overview (nothing
    // selected). There is no API for that panel: this fails first if a frontend update changes it.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-contract'));
    const overview = '[data-testid="properties-panel"]';
    await page.locator(`${overview} .aw-contract-summary`).waitFor({timeout: 15000});
    assert.ok(await page.locator(`${overview} [data-testid="aw-overview-tabs"]`).isVisible(), 'our tabs are in the native Workflow overview');
    assert.ok(await page.locator(`${overview} .aw-contract-summary`).isVisible(), 'the contract shows inside the native panel');
    await page.locator('[data-testid="panel-tab-nodes"]').click();  // a native tab brings native content back
    await page.waitForFunction(() => document.querySelector('[data-testid="aw-overview-content"]')?.hidden === true);
    await page.locator('[data-testid="aw-tab-workflow-contract"]').click();
    await page.waitForFunction(() => document.querySelector('[data-testid="aw-overview-content"]')?.hidden === false);
    // A node selected: its own native view, with only the Design tab of ours beside it.
    await page.evaluate(comfy(app => app.canvas.selectNode(app.graph.nodes.find(n => n.properties?.aws_stage))));
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid^="aw-tab-"]')].map(b => b.dataset.testid).join() === 'aw-tab-workflow-design');
    assert.equal(await page.evaluate(() => document.querySelector('[data-testid="aw-overview-content"]').hidden), true, 'the native content stays');
    await page.evaluate(comfy(app => app.canvas.deselectAll()));  // nothing selected again: ours return, Contract remembered
    await page.locator(`${overview} [data-testid="aw-tab-workflow-contract"][data-state="active"]`).waitFor({timeout: 10000});
    await page.locator(`${overview} .aw-contract-summary`).waitFor({timeout: 10000});
    // Without the native panel (or with the escape hatch), the drawer shows them.
    await page.evaluate(async () => { localStorage.setItem('agent-workflow-studio:overview', 'drawer'); await (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-contract'); });
    assert.ok(await page.locator('.aw-drawer .aw-contract-summary').isVisible(), 'the drawer is the fallback');
    await page.evaluate(async () => { localStorage.removeItem('agent-workflow-studio:overview'); (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).closeWorkflowDrawer(); });
    // Work outside the stages is drift: a loose node at the top level turns the tab to a warning.
    await page.evaluate(comfy(app => { const node = window.LiteGraph.createNode('PrimitiveString'); node.title = 'Stray note'; node.pos = [50, 500]; app.graph.add(node); }));
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-contract'));
    await page.locator('.aw-contract').getByRole('button', {name: 'Refresh'}).click();
    await page.waitForFunction(() => /Stray note/.test(document.querySelector('.aw-contract-gaps')?.textContent ?? ''), null, {timeout: 15000});
    assert.match(await page.locator('.aw-contract-status').textContent(), /^⚠ 1 drift\(s\) from the contract/);
    // Drift is listed first among the gaps, as broken.
    assert.equal(await page.locator('.aw-contract-gap-group').first().innerText(), 'Broken\ndrift: Outside every stage: Stray note.');
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
    // The user's own nodes from before the contract: a step adopts them, wiring kept.
    const adopted = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, L = window.LiteGraph;
      app.graph.clear();
      const src = L.createNode('PrimitiveString'), out = L.createNode('PreviewAny');
      app.graph.add(src);
      app.graph.add(out);
      src.connect(0, out, 0);
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      await createCanvasBridge(app, window.comfyAPI.api.api).handle('skeleton', {steps: [{id: 'existing', adopts: [src.id, out.id]}]});
      const stage = app.graph.nodes.find(n => n.properties?.aws_stage === 'existing');
      return {top: app.graph.nodes.length, inside: stage.subgraph.nodes.map(n => n.type).sort(), wires: stage.subgraph.links.size ?? Object.keys(stage.subgraph.links).length};
    });
    assert.deepEqual(adopted, {top: 1, inside: ['PreviewAny', 'PrimitiveString'], wires: 1});
    // An amendment may adopt them into a stage that is already built: they move in, ids and wiring kept.
    const late = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, L = window.LiteGraph;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const bridge = createCanvasBridge(app, window.comfyAPI.api.api);
      const step = {id: 'shout', takes: {text: 'STRING'}, gives: {text: 'STRING'}};
      await bridge.handle('skeleton', {steps: [step]});
      await bridge.handle('edit', {operations: [  // built: a real node inside, as an agent builds it
        {op: 'add', stage: 'shout', key: 'loud', class_type: 'StringConcatenate'},
        {op: 'connect', stage: 'shout', from: 'input', output: 'text', to: 'loud', input: 'string_a'},
        {op: 'connect', stage: 'shout', from: 'loud', output: 0, to: 'output', input: 'text'},
      ]});
      const stage = app.graph.nodes.find(n => n.properties?.aws_stage === 'shout');
      const src = L.createNode('PrimitiveString'), out = L.createNode('PreviewAny');
      app.graph.add(src);
      app.graph.add(out);
      src.widgets.find(w => w.name === 'value').value = 'good morning';
      src.connect(0, stage, 0);
      stage.connect(0, out, 0);
      const ids = [src.id, out.id];
      const done = await bridge.handle('skeleton', {steps: [{...step, adopts: ids}]});
      const sg = stage.subgraph, link = id => sg.links.get?.(id) ?? sg.links[id];
      const [mine, show] = ids.map(id => sg.getNodeById(id));
      const feeder = sg.getNodeById(link(show?.inputs[0].link)?.origin_id);
      return {top: app.graph.nodes.map(n => n.type === stage.type), notes: done.notes, kept: [mine?.type, show?.type, mine?.widgets.find(w => w.name === 'value').value],
        chain: [feeder?.type, String(sg.getNodeById(link(feeder?.inputs[0].link)?.origin_id)?.id) === String(ids[0])]};
    });
    assert.deepEqual(late.top, [true], JSON.stringify(late));  // nothing left outside the stage
    assert.deepEqual(late.kept, ['PrimitiveString', 'PreviewAny', 'good morning']);
    assert.deepEqual(late.chain, ['StringConcatenate', true], JSON.stringify(late));  // Text → (stage content) → Preview, inside
    assert.match(late.notes.join(' '), /took over .*wiring kept/);
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
