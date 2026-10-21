// The studio's shell is data and text only: a new tab starts empty (not ComfyUI's image example), the
// Templates browser lists only the studio's templates, ComfyUI's image, 3D and audio editors are off,
// and the page uses no ComfyUI API that is being removed. A workflow saved before the type standard
// opens with today's type names, every wire kept.
// Verifies: R11, R37
const {chromium} = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const artifacts = process.env.ARTIFACTS;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1500, height: 950}});
  const errors = [], warnings = [];
  page.on('pageerror', error => errors.push(String(error)));
  page.on('console', message => message.type() === 'warning' && warnings.push(message.text()));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  try {
    await page.goto(base);
    await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
    const shell = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app;
      await app.extensionManager.command.execute('Comfy.LoadDefaultWorkflow');
      return {
        defaultNodes: app.rootGraph.nodes.length,
        coreTemplates: await (await fetch('/templates/index.json')).json(),
        studioTemplates: (await (await fetch('/workflow_templates')).json()).agent_workflow_studio?.length ?? 0,
        disabled: app.extensionManager.setting.get('Comfy.Extension.Disabled'),
        badge: app.extensionManager.setting.get('Comfy.NodeBadge.NodeSourceBadgeMode'),
      };
    });
    assert.equal(shell.defaultNodes, 0);  // "Load Default Workflow" (and a closed last tab) is an empty canvas
    assert.deepEqual(shell.coreTemplates, []);
    assert.ok(shell.studioTemplates >= 2);
    for (const editor of ['Comfy.MaskEditor', 'Comfy.Load3D', 'Comfy.ImageCrop']) assert.ok(shell.disabled.includes(editor), editor);
    assert.equal(shell.badge, 'None');
    // Saved before the type standard (PAIRS): it opens with the registered names, no wire lost.
    const saved = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'saved_before_type_standard.json'), 'utf8'));
    const opened = await page.evaluate(async w => {
      const app = window.comfyAPI.app.app;
      await app.loadGraphData(w, true, true, 'saved before');
      const sub = app.rootGraph.nodes.find(n => n.subgraph)?.subgraph;
      const links = [...(sub?.links?.values?.() ?? Object.values(sub?.links ?? {}))].filter(Boolean);
      return {links: links.length, linkTypes: [...new Set(links.map(l => l.type))].sort(), ports: sub.inputs.map(i => i.type)};
    }, structuredClone(saved));
    assert.equal(opened.links, saved.definitions.subgraphs[0].links.length);
    assert.ok(!opened.linkTypes.includes('PAIRS') && opened.linkTypes.includes('RECORD_PAIRS'), opened.linkTypes.join(', '));
    assert.match(opened.ports[0], /^RECORD_PAIRS/);  // was PAIRS,DICT
    // The credentials dialog is a native <dialog> (not ComfyUI's deprecated scripts/ui.js one).
    await page.evaluate(async () => (await import('/extensions/agent_workflow_studio/pi_settings.js')).openPiSettings());
    await page.locator('dialog.agent-workflow-studio-pi-settings[open]').waitFor({timeout: 15000});
    await page.screenshot({path: path.join(artifacts, 'studio-credentials.png')});
    await page.keyboard.press('Escape');
    await page.locator('dialog.agent-workflow-studio-pi-settings[open]').waitFor({state: 'detached', timeout: 5000}).catch(() => {});
    assert.equal(await page.locator('dialog.agent-workflow-studio-pi-settings[open]').count(), 0);
    const deprecated = warnings.filter(w => /scripts\/ui\.js|input\.link is deprecated/.test(w));
    assert.deepEqual(deprecated, []);
    assert.deepEqual(errors, []);
    console.log('PASS studio_shell');
  } catch (error) {
    await page.screenshot({path: path.join(artifacts, 'studio-shell-failure.png'), fullPage: true}).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
