// The Templates browser's picture of each shipped template: ComfyUI shows example_workflows/<name>.jpg
// beside <name>.json. Each template opens in an isolated ComfyUI, fitted to the view in ComfyUI's
// focus mode (no panels), and is saved as that picture. Run by scripts/build_templates.sh.
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const folder = path.join(__dirname, '..', '..', 'custom_nodes', 'agent_workflow_studio', 'example_workflows');

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 900}});  // wide: the card crops its middle
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  await page.goto(base);
  await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow, null, {timeout: 60000});
  // Only the graph: no panels (focus mode), no minimap, zoom controls or canvas statistics (this ComfyUI is a throwaway).
  await page.evaluate(async () => {
    const {command, setting} = window.comfyAPI.app.app.extensionManager;
    await setting.set('Comfy.Minimap.Visible', false);
    await setting.set('Comfy.Graph.CanvasInfo', false);
    await setting.set('Comfy.Graph.CanvasMenu', false);
    await command.execute('Workspace.ToggleFocusMode');
  });
  // ONLY=<part of a name>: just those templates (the others keep their pictures).
  for (const file of fs.readdirSync(folder).filter(f => f.endsWith('.json') && f.includes(process.env.ONLY || ''))) {
    const workflow = JSON.parse(fs.readFileSync(path.join(folder, file), 'utf8'));
    await page.evaluate(async ([w, name]) => {
      const app = window.comfyAPI.app.app;
      await app.loadGraphData(w, true, true, name);
      await app.extensionManager.command.execute('Comfy.Canvas.FitView');
    }, [workflow, file.replace(/\.json$/, '')]);
    await page.waitForTimeout(1500);
    const picture = path.join(folder, file.replace(/\.json$/, '.jpg'));
    await page.screenshot({path: picture, type: 'jpeg', quality: 80});
    console.log('thumbnail', path.basename(picture));
  }
  await browser.close();
})().catch(error => {
  console.error(error);
  process.exit(1);
});
