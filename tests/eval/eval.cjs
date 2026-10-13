// One evaluation task: a real Pi agent (real model) gets a request in the chat UI.
// Scoring uses evidence only: run outputs, canvas, versions, never the agent's claims.
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE, out = process.env.OUT;
const task = JSON.parse(process.env.TASK), config = JSON.parse(process.env.PI_CONFIG);
const SETTLED = ['No native workflow was submitted by Pi this turn.', 'Native workflow submitted; completion has not been verified.',
  'Native workflow failed; inspect execution errors.', 'Native node execution succeeded; inspect outputs for usefulness.'];

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  const result = {id: task.id};
  const started = Date.now();
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    await page.waitForTimeout(1000);
    result.setup_ids = await page.evaluate(async ({setup, config}) => {
      const app = window.comfyAPI.app.app;
      app.graph.clear();
      let ids = [];
      if (setup === 'good_morning') {
        const {createCanvasBridge} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
        const bridge = createCanvasBridge(app, window.comfyAPI.api.api);
        const r = await bridge.handle('edit', {operations: [
          {op: 'add', key: 'src', class_type: 'PrimitiveString', position: [50, 100]},
          {op: 'widget', node: 'src', name: 'value', value: 'good morning'},
          {op: 'add', key: 'out', class_type: 'PreviewAny', position: [450, 100]},
          {op: 'connect', from: 'src', output: 0, to: 'out', input: 'source'}]});
        ids = Object.values(r.added);
      }
      app.extensionManager.workflow.activeWorkflow.changeTracker.checkState();
      localStorage.setItem(`agent-workflow-studio:pi-chat:${app.rootGraph.id}`, JSON.stringify({pi_config: config}));
      return ids;
    }, {setup: task.setup, config});
    result.workflow_id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.pi-chat'); });
    const message = page.getByRole('textbox', {name: 'Message Pi'});
    await message.waitFor({timeout: 30000});
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Message Pi"]')?.disabled, null, {timeout: 120000});
    const autoApprove = page.getByRole('checkbox', {name: 'Auto-approve workflow runs'});
    if (!await autoApprove.isChecked()) await autoApprove.check();
    await message.fill(task.prompt);
    await page.getByRole('button', {name: 'Send'}).click();
    // Act as a user: answer questions with the first option, approve plans and permission requests.
    result.approvals = 0;
    result.cards = [];
    const deadline = Date.now() + (task.timeout_minutes ?? 7) * 60000;
    while (!await page.evaluate(texts => texts.some(t => document.body.innerText.includes(t)), SETTLED)) {
      if (Date.now() > deadline) throw Error(`Agent did not settle within ${task.timeout_minutes ?? 7} minutes.`);
      const card = page.locator('.aw-pi-permission').first();
      if (await card.count()) {
        result.cards.push((await card.innerText()).slice(0, 4000));
        const allow = card.getByRole('button', {name: 'Allow'});
        await (await allow.count() ? allow : card.getByRole('button').first()).click();
        result.approvals += 1;
      }
      await page.waitForTimeout(1000);
    }
    await page.waitForTimeout(1500);
    const evidence = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      const history = await (await fetch('/history')).json();
      const outputs = Object.values(history).flatMap(h => Object.values(h.outputs ?? {}));
      const statuses = Object.values(history).map(h => h.status?.status_str);
      const log = await (await fetch(`/agent-workflow-studio/projects/${encodeURIComponent(app.rootGraph.id)}/log`)).json();
      return {
        node_types: app.rootGraph.nodes.map(n => n.type), node_ids: app.rootGraph.nodes.map(n => n.id),
        step_groups: (app.rootGraph._groups ?? app.rootGraph.groups ?? []).map(g => g.title),
        untagged: app.rootGraph.nodes.filter(n => !n.properties?.aws_contract_step).map(n => n.title),
        texts: outputs.flatMap(o => o.text ?? []).map(String), images: outputs.flatMap(o => o.images ?? []).length,
        run_statuses: statuses, versions: (log.commits ?? []).map(c => `${c.author}: ${c.message}`),
        chat: document.querySelector('.aw-pi-host')?.innerText.slice(-3000) ?? '',
      };
    });
    Object.assign(result, evidence);
  } catch (error) {
    result.error = String(error).slice(0, 500);
  }
  result.seconds = Math.round((Date.now() - started) / 1000);
  await page.screenshot({path: path.join(out, `${task.id}.png`)}).catch(() => {});
  fs.writeFileSync(path.join(out, `${task.id}.json`), JSON.stringify(result, null, 1));
  await browser.close();
})();
