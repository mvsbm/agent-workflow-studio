// A data API, contract first and native end to end: the caller uploads a CSV with ComfyUI's own
// upload (claim check), the workflow's checks run green, it is published, and a call with the
// uploaded folder returns the rows typed by the design; bad calls get Problem Details, long calls
// are ComfyUI's native jobs.
// Verifies: R16, R35, R38
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;

const intent = {
  contract: 2,
  purpose: 'Return the orders a caller uploads as a table.',
  requests: [{id: 'R1', title: 'Read uploaded orders', you_said: 'read my orders file', we_understood: 'When orders are uploaded, the workflow returns them as a table.', priority: 'must'}],
  examples: [{id: 'E1', covers: ['R1'], given: {orders: 'orders_e2e'}, expect: {totals: 'north'}}],
  promises: {
    inputs: [{name: 'orders', kind: 'folder', description: 'The folder of order exports, uploaded first.', example: 'orders_e2e'}],
    outputs: [{name: 'totals', kind: 'table', description: 'Every order as a row.', from: 'tally.totals'}],
  },
  decisions: [], not_now: [], open_questions: [],
};
const steps = [{
  id: 'tally', purpose: 'Read the uploaded orders.', approach: 'fixed', covers: ['R1'],
  takes: {orders: 'input.orders'}, gives: {totals: 'TABLE'}, uses: 'Read table', why: 'the caller uploads a CSV',
  pattern: 'Pipes and filters', gate: {says: 'At least 2 orders come back.', check: {output: 'totals', kind: 'rows_at_least', value: 2}},
}];
const meta = {decisions: [], order: ['tally'], data: {totals: {description: 'One row per order', key: ['order_id'], fields: ['order_id', 'region', 'amount']}}};
const promises = {...intent.promises, outputs: intent.promises.outputs};

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const result = await page.evaluate(async ({intent, steps, meta, promises}) => {
      const app = window.comfyAPI.app.app, api = window.comfyAPI.api.api;
      app.graph.clear();
      const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
      const {runChecks, publishWorkflow} = await import('/extensions/agent_workflow_studio/publish.js');
      const bridge = createCanvasBridge(app, api, {confirm: async () => true});
      const post = async (url, body) => { const r = await api.fetchApi(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); return {status: r.status, type: r.headers.get('content-type'), body: await r.json()}; };
      // The caller's data, through ComfyUI's own upload: any file, into input/<folder>/.
      const form = new FormData();
      form.append('image', new Blob(['order_id,region,amount\n1,north,10\n2,south,20\n3,north,5\n'], {type: 'text/csv'}), 'orders.csv');
      form.append('type', 'input');
      form.append('subfolder', 'orders_e2e');
      form.append('overwrite', 'true');
      const uploaded = await (await api.fetchApi('/upload/image', {method: 'POST', body: form})).json();
      // Contract first: the skeleton (stage, input node, output node, App mode), then the stage's LLD.
      await bridge.handle('skeleton', {steps, promises, intent, meta});
      await bridge.handle('build', {step: 'tally', nodes: {read: {type: 'AgentWorkflowReadTable', set: {file: 'orders.csv'}, realizes: ['takes orders', 'does', 'gives totals']}},
        wires: [{from: 'input.orders', to: 'read.folder'}, {from: 'read.table', to: 'output.totals'}]});
      // Run once on the canvas (the stage's Check), then the workflow's CI, then publish.
      const queued = await bridge.handle('run', {reason: 'verify'});
      for (let i = 0; i < 60; i++) { const h = await (await api.fetchApi(`/history/${queued.prompt_id}`)).json(); if (h[queued.prompt_id]?.status?.completed !== undefined) break; await new Promise(r => setTimeout(r, 500)); }
      const checks = await runChecks(app, api);
      const published = await publishWorkflow(app, api, 'orders-e2e', 'Orders as a table');
      const ran = await post('/agent-workflow-studio/api/orders-e2e/run', {inputs: {orders: 'orders_e2e'}});
      const missing = await post('/agent-workflow-studio/api/orders-e2e/run', {inputs: {orders: 'never_uploaded'}});
      const unknown = await post('/agent-workflow-studio/api/no-such-api/run', {inputs: {}});
      // A long call is a native ComfyUI job: poll and cancel at ComfyUI's own addresses.
      const started = (await post('/agent-workflow-studio/api/orders-e2e/jobs', {inputs: {orders: 'orders_e2e'}})).body;
      let job;
      for (let i = 0; i < 60; i++) { job = await (await api.fetchApi(started.poll)).json(); if (['completed', 'failed'].includes(job.status)) break; await new Promise(r => setTimeout(r, 500)); }
      const cancelled = await api.fetchApi(started.cancel, {method: 'POST'});
      const openapi = await (await api.fetchApi('/agent-workflow-studio/api/orders-e2e/openapi.json')).json();
      return {uploaded, checks: checks.summary, green: checks.green, published, ran, missing, unknown, started, job: {status: job.status, outputs: JSON.stringify(job.outputs)}, cancel: cancelled.status, openapi};
    }, {intent, steps, meta, promises});
    assert.deepEqual(result.uploaded, {...result.uploaded, name: 'orders.csv', subfolder: 'orders_e2e', type: 'input'});
    assert.equal(result.checks, '✓ design · ✓ stage Checks · ✓ examples · ✓ no regression');
    assert.equal(result.published.semver, '1.0.0');
    // The answer: completed, the rows typed by the design's entity, and the full table as a native file.
    assert.equal(result.ran.status, 200);
    assert.equal(result.ran.body.status, 'completed', JSON.stringify(result.ran.body));
    assert.deepEqual(result.ran.body.data.totals.columns, ['order_id', 'region', 'amount']);
    assert.equal(result.ran.body.data.totals.total_rows, 3);
    // Bad calls: RFC 9457 Problem Details, naming the fix.
    assert.equal(result.missing.status, 400);
    assert.match(result.missing.type, /application\/problem\+json/);
    assert.match(result.missing.body.detail, /orders: no folder 'never_uploaded'.*upload it first with POST \/upload\/image/);
    assert.equal(result.unknown.status, 404);
    // Native jobs.
    assert.equal(result.started.status, 'pending');
    assert.match(result.started.poll, /^\/api\/jobs\//);
    assert.equal(result.job.status, 'completed');
    assert.match(result.job.outputs, /aws_output/);
    assert.equal(result.cancel, 200);
    // The description a caller reads: the upload step, the typed table, the operations.
    const doc = result.openapi;
    assert.ok(doc.paths['/upload/image'], 'the native upload is described');
    assert.equal(doc.paths['/agent-workflow-studio/api/orders-e2e/run'].post.requestBody.content['application/json'].schema.properties.inputs.properties.orders.format, 'comfyui-input-folder');
    assert.deepEqual(doc.paths['/agent-workflow-studio/api/orders-e2e/run'].post.responses['200'].content['application/json'].schema.properties.data.properties.totals.properties.columns.items.enum, ['order_id', 'region', 'amount']);
    await page.screenshot({path: path.join(artifacts, 'data-api.png')});
    assert.deepEqual(errors, []);
    console.log('PASS data_api');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
