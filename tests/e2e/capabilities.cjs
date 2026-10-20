// Capability tests: every platform assumption the architecture relies on, checked the way the
// product uses it (both node modes, through save/reload and the backend), with its fallback.
// Run by capabilities.py; writes capabilities.json (results + ComfyUI and frontend versions).
// Verifies: R9, R15
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const DIR = path.join(__dirname, 'capabilities');
const MODES = ['classic', 'vue'];

const helpers = page => ({
  // An empty canvas for each check.
  fresh: () => page.evaluate(async () => { const app = window.comfyAPI.app.app; app.rootGraph.clear(); app.canvas.setGraph?.(app.rootGraph); }),
  // Queue the canvas; resolve with the run's status, id and its history outputs.
  run: () => page.evaluate(() => new Promise(resolve => {
    const {app} = window.comfyAPI.app, {api} = window.comfyAPI.api;
    const done = async (status, detail) => {
      const id = detail?.prompt_id;
      const history = id ? await (await api.fetchApi(`/history/${id}`)).json() : {};
      resolve({status, error: detail?.exception_message, outputs: history[id]?.outputs ?? {}});
    };
    api.addEventListener('execution_success', e => done('success', e.detail), {once: true});
    api.addEventListener('execution_error', e => done('error', e.detail), {once: true});
    app.queuePrompt(0, 1).then(r => r?.error && resolve({status: 'refused', error: JSON.stringify(r.error).slice(0, 300)}), e => resolve({status: 'refused', error: String(e).slice(0, 300)}));
  })),
  // Save and reload through the workflow JSON, as Versions and the API will.
  roundtrip: () => page.evaluate(async () => {
    const app = window.comfyAPI.app.app, saved = JSON.parse(JSON.stringify(app.rootGraph.serialize()));
    await app.loadGraphData(saved);
    return saved;
  }),
});

(async () => {
  const caps = fs.readdirSync(DIR).filter(f => f.endsWith('.cjs')).sort().map(f => require(path.join(DIR, f)));
  const browser = await chromium.launch({headless: true});
  const results = [];
  let versions = {};
  for (const mode of MODES) {
    await fetch(`${base}/api/settings/Comfy.VueNodes.Enabled`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(mode === 'vue')});
    const page = await browser.newPage({viewport: {width: 1500, height: 900}});
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    versions = await page.evaluate(async () => ({frontend: window.__COMFYUI_FRONTEND_VERSION__, ...(await (await fetch('/api/system_stats')).json()).system}));
    for (const cap of caps) {
      if (cap.modes && !cap.modes.includes(mode)) continue;
      let result;
      try {
        await helpers(page).fresh();
        result = await cap.check({page, mode, ...helpers(page)});
      } catch (error) {
        result = {ok: false, evidence: String(error).split('\n')[0].slice(0, 300)};
      }
      results.push({id: cap.id, mode, ok: Boolean(result.ok), evidence: result.evidence, assumes: cap.assumes, ...(cap.caveat && {caveat: cap.caveat}), fallback: cap.fallback});
      console.log(`${result.ok ? 'PASS' : 'FAIL'} ${cap.id} [${mode}] ${JSON.stringify(result.evidence).slice(0, 400)}`);
    }
    if (errors.length) console.log(`page errors [${mode}]: ${errors.slice(0, 3).join(' | ')}`);
    await page.close();
  }
  await browser.close();
  const record = {versions: {comfyui: versions.comfyui_version, frontend: versions.frontend, python: versions.python_version?.split(' ')[0]}, results};
  fs.writeFileSync(path.join(__dirname, 'capabilities.json'), JSON.stringify(record, null, 1) + '\n');
  process.exit(results.every(r => r.ok) ? 0 : 1);
})().catch(error => { console.error(error); process.exit(2); });
