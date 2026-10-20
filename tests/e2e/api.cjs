// A workflow as an API: build a staged workflow with a given input, set its signature with the
// agent's edit, publish it (refused before), call it with new data and read the outputs.
// Verifies: R16, R35
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
    const result = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, api = window.comfyAPI.api.api;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const {publishWorkflow} = await import('/extensions/agent_workflow_studio/publish.js');
      const bridge = createCanvasBridge(app, api, {confirm: async () => true});
      await bridge.handle('skeleton', {steps: [
        {id: 'greet', takes: {name: 'STRING'}, gives: {text: 'STRING'}, gate: {says: 'greets', check: {output: 'text', kind: 'contains', value: 'world'}}},
        {id: 'show', takes: {text: 'greet.text'}},
      ]});
      // The user's given input at the top level.
      const given = window.LiteGraph.createNode('PrimitiveString');
      given.title = 'Name';
      given.properties = {...given.properties, aws_given: true};
      given.widgets.find(w => w.name === 'value').value = 'hello';
      app.graph.add(given);
      await bridge.handle('edit', {operations: [
        {op: 'connect', from: given.id, output: 0, to: 'greet', input: 'name'},
        {op: 'add', stage: 'greet', key: 'cat', class_type: 'StringConcatenate'},
        {op: 'widget', stage: 'greet', node: 'cat', name: 'string_b', value: ' world'},
        {op: 'connect', stage: 'greet', from: 'input', output: 'name', to: 'cat', input: 'string_a'},
        {op: 'connect', stage: 'greet', from: 'cat', output: 0, to: 'output', input: 'text'},
        {op: 'add', stage: 'show', key: 'out', class_type: 'PreviewAny', title: 'Greeting'},
        {op: 'connect', stage: 'show', from: 'input', output: 'text', to: 'out', input: 'source'},
      ]});
      let refused;
      try { await publishWorkflow(app, api, 'greet-e2e'); } catch (error) { refused = error.message; }
      await bridge.handle('edit', {operations: [{op: 'signature', inputs: [{node: given.id, widget: 'value'}], outputs: ['show', 'greet']}]});
      const published = await publishWorkflow(app, api, 'greet-e2e', 'Greets a name');
      const call = async inputs => (await api.fetchApi('/agent-workflow-studio/api/greet-e2e/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({inputs})})).json();
      const ran = await call({value: 'hi'});
      const wrong = await call({value: 3});
      // A change inside a stage republishes as a patch; the old version stays callable, pinned.
      await bridge.handle('edit', {operations: [{op: 'widget', stage: 'greet', node: 'cat', name: 'string_b', value: ' there'}]});
      const patched = await publishWorkflow(app, api, 'greet-e2e');
      const pinned = await (await api.fetchApi('/agent-workflow-studio/api/greet-e2e@1.0.0/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({inputs: {value: 'hi'}})})).json();
      // A long call as a job: start it, poll until it is done.
      const started = await (await api.fetchApi('/agent-workflow-studio/api/greet-e2e/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({inputs: {value: 'yo'}})})).json();
      let job;
      for (let i = 0; i < 60; i++) {
        job = await (await api.fetchApi(started.poll)).json();
        if (job.status !== 'running') break;
        await new Promise(r => setTimeout(r, 500));
      }
      // The outer loop: watch the API on a schedule; the run's Checks are recorded as a version.
      await api.fetchApi('/agent-workflow-studio/api/greet-e2e/watch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({every_minutes: 0.05, inputs: {value: 'watched'}})});
      let watched;
      for (let i = 0; i < 60 && !watched?.watch?.last; i++) {
        await new Promise(r => setTimeout(r, 500));
        watched = (await (await api.fetchApi('/agent-workflow-studio/api')).json()).apis.find(a => a.name === 'greet-e2e');
      }
      await api.fetchApi('/agent-workflow-studio/api/greet-e2e/watch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({every_minutes: 0})});
      const openapi = await (await api.fetchApi('/agent-workflow-studio/api/greet-e2e/openapi.json')).json();
      const listing = await (await api.fetchApi('/agent-workflow-studio/api')).json();
      return {refused, published, ran, wrong, openapi, listing, patched, pinned, job, watched};
    });
    assert.match(result.refused, /outputs/, 'publishing without a signature is refused');
    assert.match(result.published.version, /^[0-9a-f]{8}$/);
    assert.equal(result.ran.status, 'success', JSON.stringify(result.ran));
    assert.equal(result.ran.version, result.published.version);
    assert.match(JSON.stringify(result.ran.outputs.show), /hi world/, 'the caller\'s input flowed through the frozen workflow');
    assert.match(JSON.stringify(result.ran.outputs.greet), /"passed":true/, 'the stage Check result comes with it');
    assert.match(result.wrong.error, /must be string/);
    assert.equal(result.published.semver, '1.0.0');
    assert.equal(result.patched.semver, '1.0.1', 'a change inside a stage is a patch');
    assert.equal(result.pinned.semver, '1.0.0');
    assert.match(JSON.stringify(result.pinned.outputs.show), /hi world/, 'the pinned version still behaves as published');
    assert.equal(result.job.status, 'success', JSON.stringify(result.job));
    // The watch catches the regression: the patched version says "there", so the greet Check (contains "world") fails.
    assert.deepEqual({passing: result.watched?.watch?.last?.passing, checks: result.watched?.watch?.last?.checks}, {passing: false, checks: '0/1'}, JSON.stringify(result.watched));
    assert.match(JSON.stringify(result.job.outputs.show), /yo there/, 'the job ran the latest version');
    assert.equal(result.openapi.paths['/agent-workflow-studio/api/greet-e2e/run'].post.requestBody.content['application/json'].schema.properties.inputs.properties.value.type, 'string');
    assert.ok(result.listing.apis.some(a => a.name === 'greet-e2e' && a.inputs[0] === 'value'));
    // The Contract tab shows what is published and how to call it.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-contract'));
    await page.locator('.aw-contract-api').getByText(/^Published as greet-e2e · 1\.0\.1 \([0-9a-f]{8}\)/).waitFor({timeout: 15000});
    // What changed, and the release a rollback returns to.
    await page.locator('.aw-contract-api').getByText('Patch: only inside stages (nodes or settings); the design and the API are the same.').waitFor({timeout: 5000});
    await page.locator('.aw-contract-api').getByRole('button', {name: 'Roll back to 1.0.0'}).waitFor({timeout: 5000});
    await page.screenshot({path: path.join(artifacts, 'api.png')});
    assert.deepEqual(errors, []);
    console.log('PASS api');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'api-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
