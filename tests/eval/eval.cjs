// One evaluation task: a real chat agent (Pi or Claude Code, real model) gets a request in the chat UI.
// Scoring uses evidence only: run outputs, canvas, versions, never the agent's claims.
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const base = process.env.BASE, out = process.env.OUT;
const task = JSON.parse(process.env.TASK), config = JSON.parse(process.env.PI_CONFIG);
const SETTLED = ['No native workflow was submitted by Pi this turn.', 'No native workflow was submitted by Claude this turn.', 'Native workflow submitted; completion has not been verified.',
  'Native workflow failed; inspect execution errors.', 'Native node execution succeeded; inspect outputs for usefulness.'];

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const consoleErrors = [];
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text().slice(0, 300)); });
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  const result = {id: task.id};
  let save = async () => {};
  let lastSave = Date.now();
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
        // A workflow the user built by hand, before any contract: plain nodes at the top level.
        const src = window.LiteGraph.createNode('PrimitiveString'), out = window.LiteGraph.createNode('PreviewAny');
        src.pos = [50, 100];
        out.pos = [450, 100];
        app.graph.add(src);
        app.graph.add(out);
        src.widgets.find(w => w.name === 'value').value = 'good morning';
        src.connect(0, out, 0);
        ids = [src.id, out.id];
      }
      app.extensionManager.workflow.activeWorkflow.changeTracker.checkState();
      localStorage.setItem(`agent-workflow-studio:pi-chat:${app.rootGraph.id}`, JSON.stringify({pi_config: config}));
      return ids;
    }, {setup: task.setup, config});
    if (task.template) {
      // Start from a shipped template (fresh id, so every run has its own history), and run it
      // once so its data exists before the agent starts, exactly as a user would.
      const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'workflows', task.template), 'utf8'));
      workflow.id = crypto.randomUUID();
      await page.evaluate(async workflow => {
        const app = window.comfyAPI.app.app;
        await app.loadGraphData(workflow);
        await app.queuePrompt(0, 1);
      }, workflow);
      await page.evaluate(async config => {
        const app = window.comfyAPI.app.app;
        localStorage.setItem(`agent-workflow-studio:pi-chat:${app.rootGraph.id}`, JSON.stringify({pi_config: config}));
      }, config);
      await page.waitForFunction(async () => Object.values(await (await fetch('/history')).json()).some(h => h.status?.status_str === 'success'), null, {timeout: 180000, polling: 2000});
    }
    result.workflow_id = await page.evaluate(() => window.comfyAPI.app.app.rootGraph.id);
    // Visible mode: save the workflow to the library now, so the user can open it and watch
    // its Versions grow while the agent works; it is saved again at the end.
    save = async () => process.env.SAVE_AS && page.evaluate(async name => {
      await fetch(`/api/userdata/${encodeURIComponent(`workflows/${name}.json`)}?overwrite=true&full_info=true`, {method: 'POST', body: JSON.stringify(window.comfyAPI.app.app.graph.serialize())});
    }, process.env.SAVE_AS);
    await save();
    await page.evaluate(() => { void window.comfyAPI.app.app.extensionManager.command.execute('Workspace.ToggleSidebarTab.pi-chat'); });
    const message = page.getByRole('textbox', {name: 'Message the agent'});
    await message.waitFor({timeout: 30000});
    await page.waitForFunction(() => !document.querySelector('textarea[aria-label="Message the agent"]')?.disabled, null, {timeout: 120000});
    const autoApprove = page.getByRole('checkbox', {name: 'Auto-approve workflow runs'});
    if (!await autoApprove.isChecked()) await autoApprove.check();
    await message.fill(task.prompt);
    await page.getByRole('button', {name: 'Send'}).click();
    // Act as a user: answer questions with the first option, approve plans and permission requests.
    result.approvals = 0;
    result.cards = [];
    const deadline = Date.now() + (task.timeout_minutes ?? 7) * 60000;
    while (!await page.evaluate(texts => texts.some(t => document.body.innerText.includes(t)), SETTLED)) {
      // Stop waiting (but still collect evidence) on a timeout or a dropped session.
      if (Date.now() > deadline) { result.error = `Agent did not settle within ${task.timeout_minutes ?? 7} minutes.`; break; }
      const dropped = await page.evaluate(() => document.body.innerText.match(/(Pi|Claude) disconnected[^.]*\./)?.[0]);
      if (dropped) { result.error = dropped; break; }
      const card = page.locator('.aw-pi-permission').first();
      if (await card.count()) {
        result.cards.push((await card.innerText()).slice(0, 4000));
        const allow = card.getByRole('button', {name: 'Allow'});
        const options = await card.getByRole('button').allTextContents();
        const preferred = options.find(option => (task.prefer ?? []).some(word => option.includes(word)));
        await (await allow.count() ? allow : preferred ? card.getByRole('button', {name: preferred, exact: true}) : card.getByRole('button').first()).click();
        result.approvals += 1;
      }
      await page.waitForTimeout(1000);
      // Visible mode: keep the saved copy current, so reopening it shows the agent's latest nodes.
      if (Date.now() - lastSave > 20000) { lastSave = Date.now(); await save().catch(() => {}); }
    }
    await page.waitForTimeout(1500);
    const evidence = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      const history = await (await fetch('/history')).json();
      const outputs = Object.values(history).flatMap(h => Object.values(h.outputs ?? {}));
      const statuses = Object.values(history).map(h => h.status?.status_str);
      const log = await (await fetch(`/agent-workflow-studio/projects/${encodeURIComponent(app.rootGraph.id)}/log`)).json();
      // The final contract report, as the Contract tab computes it.
      let contract = null;
      try {
        const base = `/agent-workflow-studio/projects/${encodeURIComponent(app.rootGraph.id)}`;
        const {contract: current} = await (await fetch(`${base}/contract`)).json();
        if (current) {
          const {contractEvidence} = await import('/extensions/agent_workflow_studio/pi_canvas.js');
          const evidence = await contractEvidence(app, window.comfyAPI.api.api);
          const status = await (await fetch(`${base}/contract/status`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(evidence)})).json();
          // The design as agreed: how many steps carry how/why/pattern, entities, outputs mapped, examples.
          const steps = current.steps ?? [];
          contract = {aligned: status.aligned, drift: status.drift, progress: status.progress, freshness: status.freshness, todo: status.todo,
            designed: steps.length > 0 && steps.every(s => s.uses && s.why && s.pattern), entities: Object.keys(current.data ?? {}),
            api: (current.promises?.outputs ?? []).some(o => o.from), examples: (current.examples ?? []).length};
        }
      } catch (error) { contract = {error: String(error)}; }
      const tools = [...document.querySelectorAll('.aw-pi-host summary, .aw-pi-host details > summary')].map(s => s.textContent.trim()).filter(t => /^(Done|Failed) · /.test(t));
      // Why tool calls failed (the agent sees this; useful for spotting recurring mistakes).
      const failures = [...document.querySelectorAll('.aw-pi-host details')].filter(d => /^Failed · /.test(d.querySelector('summary')?.textContent.trim() ?? '')).map(d => d.textContent.trim().slice(0, 400));
      return {
        contract, tools, failures,
        // The work lives inside stages: node types include what is inside them.
        node_types: app.rootGraph.nodes.flatMap(n => (n.properties?.aws_stage ? n.subgraph.nodes.map(i => i.type) : [n.type])), node_ids: app.rootGraph.nodes.flatMap(n => (n.properties?.aws_stage ? n.subgraph.nodes.map(i => i.id) : [n.id])),
        stages: app.rootGraph.nodes.filter(n => n.properties?.aws_stage).map(n => n.title),
        loose: app.rootGraph.nodes.filter(n => !n.properties?.aws_stage && !n.properties?.aws_given && !n.properties?.aws_input && !/Note$/.test(n.type)).map(n => n.title),
        texts: outputs.flatMap(o => o.text ?? []).map(String), images: outputs.flatMap(o => o.images ?? []).length,
        run_statuses: statuses, versions: (log.commits ?? []).map(c => `${c.author}: ${c.message}`),
        chat: document.querySelector('.aw-pi-host')?.innerText.slice(-8000) ?? '',
        workflow: app.rootGraph.serialize(),
      };
    });
    Object.assign(result, evidence);
  } catch (error) {
    result.error = String(error).slice(0, 500);
  }
  await save().catch(() => {});
  result.seconds = Math.round((Date.now() - started) / 1000);
  result.console_errors = consoleErrors.slice(-20);
  await page.screenshot({path: path.join(out, `${task.id}.png`)}).catch(() => {});
  fs.writeFileSync(path.join(out, `${task.id}.json`), JSON.stringify(result, null, 1));
  await browser.close();
})();
