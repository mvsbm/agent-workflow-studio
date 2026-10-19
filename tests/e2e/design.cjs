// The design pipeline on the canvas: the approved HLD builds stages, the API's input node and its
// signature (contract-first); top-level edits that contradict the design are refused; an amendment
// marks changed stages stale; the workflow publishes without hand-made signature.
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;

const steps = [
  {id: 'greet', takes: {name: 'input.name'}, gives: {greeting: 'STRING'}, gate: {says: 'greets', check: {output: 'greeting', kind: 'contains', value: 'world'}}},
  {id: 'shout', takes: {greeting: 'greet.greeting'}, gives: {loud: 'STRING'}},
  {id: 'other', gives: {text: 'STRING'}},
];
const promises = {inputs: [{name: 'name', kind: 'text', example: 'hello'}], outputs: [{name: 'loud', kind: 'text', from: 'shout.loud'}]};
const design = [
  {id: 'greet', title: '1 · Greet', sentence: 'takes name (your input) → greets'},
  {id: 'shout', title: '2 · Shout', sentence: 'takes greeting from 1 · Greet → shouts'},
  {id: 'other', title: '1 · Other', sentence: 'makes other text'},
];
const wiring = {links: [['greet', 'greeting', 'shout', 'greeting']], inputs: [['name', 'greet', 'name']]};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const result = await page.evaluate(async ({steps, promises, design, wiring}) => {
      const app = window.comfyAPI.app.app, api = window.comfyAPI.api.api;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const {publishWorkflow} = await import('/extensions/agent_workflow_studio/publish.js');
      const bridge = createCanvasBridge(app, api, {confirm: async () => true});
      const built = await bridge.handle('skeleton', {steps, promises, design});
      const input = app.graph.nodes.find(n => n.properties?.aws_input === 'name');
      const signature = structuredClone(app.graph.extra.linearData);
      const stage = step => app.graph.nodes.find(n => n.properties?.aws_stage === step);
      const tryEdit = async ops => { try { await bridge.handle('edit', {operations: ops, design: wiring}); return 'ok'; } catch (error) { return error.message; } };
      // Building inside the stages is free; wiring against the design is refused.
      const build = await tryEdit([
        {op: 'add', stage: 'greet', key: 'cat', class_type: 'StringConcatenate'},
        {op: 'widget', stage: 'greet', node: 'cat', name: 'string_b', value: ' world'},
        {op: 'connect', stage: 'greet', from: 'input', output: 'name', to: 'cat', input: 'string_a'},
        {op: 'connect', stage: 'greet', from: 'cat', output: 0, to: 'output', input: 'greeting'},
        {op: 'add', stage: 'shout', key: 'up', class_type: 'StringConcatenate'},
        {op: 'widget', stage: 'shout', node: 'up', name: 'string_b', value: '!'},
        {op: 'connect', stage: 'shout', from: 'input', output: 'greeting', to: 'up', input: 'string_a'},
        {op: 'connect', stage: 'shout', from: 'up', output: 0, to: 'output', input: 'loud'},
      ]);
      const undeclared = await tryEdit([{op: 'connect', from: 'other', output: 'text', to: 'shout', input: 'greeting'}]);
      const unwire = await tryEdit([{op: 'disconnect', node: 'shout', input: 'greeting'}]);
      const dropInput = await tryEdit([{op: 'remove', node: input.id}]);
      // An amendment that changes a step's design marks its built stage stale.
      const amended = await bridge.handle('skeleton', {steps, promises, design: design.map(d => (d.id === 'greet' ? {...d, sentence: 'takes name (your input) → greets warmly'} : d))});
      const published = await publishWorkflow(app, api, 'design-e2e');
      const ran = await (await api.fetchApi('/agent-workflow-studio/api/design-e2e/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({inputs: {name: 'hi'}})})).json();
      const inputNow = app.graph.nodes.find(n => n.properties?.aws_input === 'name'); // rollbacks rebuild node objects
      return {
        titles: app.graph.nodes.filter(n => n.properties?.aws_stage).map(n => n.title).sort(),
        input: inputNow && {title: inputNow.title, value: inputNow.widgets.find(w => w.name === 'value').value, linked: inputNow.outputs[0].links?.length},
        signature, outputStage: stage('shout').id, build, undeclared, unwire, dropInput,
        stale: {greet: stage('greet').properties.aws_stale, shout: stage('shout').properties.aws_stale}, notes: amended.notes,
        published: published.version, ran: ran.status, output: JSON.stringify(ran.outputs ?? ran), data: ran.data,
      };
    }, {steps, promises, design, wiring});
    assert.deepEqual(result.titles, ['1 · Greet', '1 · Other', '2 · Shout']);
    assert.deepEqual(result.input, {title: 'Input · name', value: 'hello', linked: 1});
    assert.equal(result.signature.inputs[0][1], 'value', 'the promised input node is the API input');
    assert.equal(result.signature.outputs[0], result.outputStage, 'the stage delivering the promised output is the API output');
    assert.equal(result.build, 'ok');
    assert.match(result.undeclared, /Not in the approved design: other\.text → 2 · Shout \(greeting\)\. The design wires greet\.greeting there/);
    assert.match(result.unwire, /approved design wires 2 · Shout/);
    assert.match(result.dropInput, /promised inputs/);
    assert.deepEqual(result.stale, {greet: true, shout: false});
    assert.match(result.notes.join(' '), /1 · Greet: its design changed/);
    assert.match(result.published, /^[0-9a-f]{8}$/);
    assert.equal(result.ran, 'success', result.output);
    assert.deepEqual(result.data, {loud: 'hi world!'}, 'API-native: the promised output comes back by name, without a hand-made signature');
    await page.screenshot({path: path.join(artifacts, 'design.png')});
    assert.deepEqual(errors, []);
    console.log('PASS design');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'design-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
