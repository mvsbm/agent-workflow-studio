// Builds workflows/Studio - Nayose benchmark.json in an isolated ComfyUI: the data generator as a
// subgraph (people → activity → three legacy exports), a Score node, and a how-to note; run once so
// every stage's tables are saved in the template. Usage: scripts/build_templates.sh
const {chromium} = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.BASE;
const target = path.join(__dirname, '..', '..', 'workflows', 'Studio - Nayose benchmark.json');
const NOTE = `# Nayose benchmark: everything on this canvas

1. **Synthetic data** (double-click to open): people and their life histories → clean insurance activity → three legacy exports, each damaged its own way. Every stage shows its data as tables. Change *households*, *update rate*, *damage* or *seed* and press **Run**.
2. **Ask Pi** (Pi tab): *Build a near state of the art Japanese nayose workflow for the exports in the input folder nayose_bench (README.txt explains them). Give every person mention a customer ID, save nayose_result.csv (system,record_id,role,customer_id), build it so the graph mirrors the architecture (one cleansing lane per source system merging into candidates, then a matching cascade — deterministic, probabilistic, my review — whose decided pairs go straight to consolidation) from visible nodes (no hidden code where a node exists) so I can see and edit the logic, and let me review uncertain matches.* You approve its design (one sentence per stage); then watch the stages fill in and run. Check **Contract** (status, to-dos, API), **Data** (the entities), **Versions**, **Review** and **Customers**.
3. **Score**: set its *Result file* to the file the workflow writes (e.g. nayose_result.csv under output/) and run. It compares against the hidden answer key and shows only totals; the agent never sees it.`;

(async () => {
  const browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1600, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => (route.request().url().startsWith(base) ? route.continue() : route.abort()));
  await page.goto(base);
  await page.waitForFunction(() => window.comfyAPI?.app?.app?.extensionManager?.workflow?.activeWorkflow?.changeTracker, null, {timeout: 60000});
  const summary = await page.evaluate(async note => {
    const app = window.comfyAPI.app.app;
    const LiteGraph = window.LiteGraph;
    const graph = app.graph;
    graph.clear();
    const add = (type, pos, title) => { const node = LiteGraph.createNode(type); node.pos = pos; if (title) node.title = title; graph.add(node); return node; };
    const widget = (node, name, value) => { node.widgets.find(w => w.name === name).value = value; };
    const slot = (node, name) => node.inputs.findIndex(input => input.name === name);
    const people = add('AgentWorkflowNayosePeople', [0, 0]);
    const activity = add('AgentWorkflowNayoseActivity', [600, 0]);
    const exports = ['policies', 'claims', 'payments'].map((system, i) => {
      const node = add('AgentWorkflowNayoseExport', [1200, i * 480], `Export · ${system}`);
      widget(node, 'system', system);
      widget(node, 'folder', 'nayose_bench');
      return node;
    });
    const folder = add('PreviewAny', [520, 160], 'Data folder');
    const score = add('AgentWorkflowNayoseScore', [2700, 160], 'Score · hidden answer key');
    const guide = add('MarkdownNote', [0, -420], 'How to use');
    guide.widgets[0].value = note;
    guide.size = [900, 360];
    people.connect(0, activity, 0);
    for (const node of exports) activity.connect(0, node, 0);
    exports[0].connect(0, exports[1], slot(exports[1], 'after'));
    exports[1].connect(0, exports[2], slot(exports[2], 'after'));
    exports[2].connect(0, folder, 0);
    exports[2].connect(0, score, slot(score, 'folder'));
    for (const node of [people, activity, ...exports]) node.properties = {...node.properties, aws_given: true};
    const converted = graph.convertToSubgraph(new Set([people, activity, ...exports]));
    const synthetic = converted?.node ?? graph.nodes.find(node => node.isSubgraphNode?.());
    // The frontend promotes every stage's seed; keep one shared seed for the whole generator.
    const sub = synthetic.subgraph;
    const shared = sub.inputs.find(input => input.name === 'seed');
    const linkOf = id => (sub.links instanceof Map ? sub.links.get(id) : sub.links[id]);
    const targets = [];
    for (const input of [...sub.inputs]) {
      if (input === shared) continue;
      for (const id of input.linkIds ?? []) {
        const link = linkOf(id);
        if (link) targets.push([sub.getNodeById(link.target_id), link.target_slot]);
      }
      sub.removeInput(input);
    }
    for (const [node, index] of targets) shared?.connect(node.inputs[index], node);
    synthetic.title = 'Synthetic data · 3 legacy systems';
    sub.name = 'Synthetic data';
    synthetic.pos = [0, 160];
    for (const node of [synthetic, folder, score, guide]) node.properties = {...node.properties, aws_given: true};
    await app.queuePrompt(0, 1);
    return {inputs: synthetic.inputs.map(i => i.name), outputs: synthetic.outputs.map(o => o.name)};
  }, NOTE);
  await page.waitForFunction(() => {
    const app = window.comfyAPI.app.app;
    const sub = app.graph.nodes.find(node => node.isSubgraphNode?.());
    return sub?.subgraph.nodes.every(node => node.properties?.aws_tables?.length) && app.graph.nodes.some(n => n.properties?.aws_tables?.length);
  }, null, {timeout: 120000});
  const workflow = await page.evaluate(() => {
    const app = window.comfyAPI.app.app;
    // Open framed on everything: the note, the generator, the space for the agent, and Score.
    const boxes = app.graph.nodes.map(n => [n.pos[0], n.pos[1] - 30, n.pos[0] + n.size[0], n.pos[1] + n.size[1]]);
    const [x0, y0, x1, y1] = [0, 1, 2, 3].map(i => (i < 2 ? Math.min : Math.max)(...boxes.map(b => b[i])));
    const scale = Math.min(1500 / (x1 - x0 + 80), 900 / (y1 - y0 + 80));
    const graph = app.graph.serialize();
    graph.extra = {...graph.extra, ds: {scale, offset: [140 / scale - x0, 80 / scale - y0]}};
    return graph;
  });
  fs.writeFileSync(target, JSON.stringify(workflow, null, 1) + '\n');
  console.log(JSON.stringify({...summary, errors, saved: target}));
  await browser.close();
  if (errors.length) process.exit(1);
})().catch(error => { console.error(error); process.exit(1); });
