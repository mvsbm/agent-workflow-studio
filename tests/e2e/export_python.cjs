// Export as Python: the contact matching template on a small sample is run, checked and published;
// the Contract tab's Export as Python button starts the agent (fake_pi.py), which checks the release
// and exports it; the program is installed in a fresh environment, its check passes, and the
// packages are there to download. Requires BASE and ARTIFACTS.
// Verifies: R41
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;
const template = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'custom_nodes', 'agent_workflow_studio', 'example_workflows', 'Studio - Contact matching.json'), 'utf8'));
const sample = template.extra.aws_contract.examples[0].files;  // the contract's own sample exports

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const prepared = await page.evaluate(async ({workflow, files}) => {
      const app = window.comfyAPI.app.app, api = window.comfyAPI.api.api;
      // The user's exports, uploaded into input/sample the way the Data folder's button does.
      for (const [rel, text] of Object.entries(files)) {
        const form = new FormData();
        form.append('image', new Blob([text], {type: 'text/csv'}), rel.split('/').at(-1));
        form.append('subfolder', 'sample');
        form.append('type', 'input');
        form.append('overwrite', 'true');
        const up = await api.fetchApi('/upload/image', {method: 'POST', body: form});
        if (!up.ok) throw Error(`upload ${rel}: ${up.status}`);
      }
      await app.loadGraphData(workflow, true, true, 'Studio - Contact matching');
      app.graph.nodes.find(n => n.properties?.aws_input === 'exports').widgets.find(w => w.name === 'folder').value = 'sample';
      const ran = await new Promise(resolve => {
        api.addEventListener('execution_success', () => resolve('success'), {once: true});
        api.addEventListener('execution_error', e => resolve(`error: ${e.detail?.node_type}: ${e.detail?.exception_message}`), {once: true});
        void app.queuePrompt(0, 1);
      });
      const {runChecks, publishWorkflow} = await import('/extensions/agent_workflow_studio/publish.js');
      const checks = await runChecks(app, api, 'user');
      let published;
      try { published = await publishWorkflow(app, api, 'contact-matching', 'One entity id per contact'); } catch (error) { published = {error: error.message}; }
      return {ran, green: checks?.green, summary: checks?.summary, failing: (checks?.stages ?? []).filter(s => s.status !== 'passed'), published};
    }, {workflow: template, files: sample});
    assert.equal(prepared.ran, 'success', JSON.stringify(prepared));
    assert.equal(prepared.green, true, `checks: ${prepared.summary} ${JSON.stringify(prepared.failing)}`);
    assert.equal(prepared.published.semver, '1.0.0', JSON.stringify(prepared.published));

    // The button: the agent opens with the request, checks the release and exports it.
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/workflow_drawer.js')).openWorkflowTab('workflow-contract'));
    await page.getByRole('button', {name: 'Export as Python'}).click();
    const reply = page.getByText(/FAKE PI · (exported|cannot export)/).first();
    await reply.waitFor({timeout: 600000});
    const said = await reply.textContent();
    await page.screenshot({path: path.join(artifacts, 'export-python.png')});
    assert.match(said, /exported release 1\.0\.0: installed True, check passed True\. Files: .*contact-matching\.zip/, said);
    assert.match(said, /\.whl/, 'a wheel was built');
    const download = await page.evaluate(async () => (await window.comfyAPI.api.api.fetchApi('/view?filename=contact-matching.zip&subfolder=exports/contact-matching-1.0.0/dist&type=output')).status);
    assert.equal(download, 200, 'the zip downloads');
    assert.deepEqual(errors, []);
    console.log('PASS export_python');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'export-python-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
