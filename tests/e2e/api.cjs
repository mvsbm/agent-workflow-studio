// A workflow as an API: build a staged workflow with a given input, set its signature with the
// agent's edit, publish it (refused before), call it with new data and read the outputs.
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
      const openapi = await (await api.fetchApi('/agent-workflow-studio/api/greet-e2e/openapi.json')).json();
      const listing = await (await api.fetchApi('/agent-workflow-studio/api')).json();
      return {refused, published, ran, wrong, openapi, listing};
    });
    assert.match(result.refused, /outputs/, 'publishing without a signature is refused');
    assert.match(result.published.version, /^[0-9a-f]{8}$/);
    assert.equal(result.ran.status, 'success', JSON.stringify(result.ran));
    assert.equal(result.ran.version, result.published.version);
    assert.match(JSON.stringify(result.ran.outputs.show), /hi world/, 'the caller\'s input flowed through the frozen workflow');
    assert.match(JSON.stringify(result.ran.outputs.greet), /"passed":true/, 'the stage Check result comes with it');
    assert.match(result.wrong.error, /must be string/);
    assert.equal(result.openapi.paths['/agent-workflow-studio/api/greet-e2e/run'].post.requestBody.content['application/json'].schema.properties.inputs.properties.value.type, 'string');
    assert.ok(result.listing.apis.some(a => a.name === 'greet-e2e' && a.inputs[0] === 'value'));
    // The Contract tab shows what is published and how to call it.
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.workflow-contract'); });
    await page.locator('.aw-contract-api').getByText(/Published as greet-e2e · version/).waitFor({timeout: 15000});
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
