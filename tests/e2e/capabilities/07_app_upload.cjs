// Data uploads in App mode: the Data folder node's folder list and its Upload button show in App mode,
// an upload goes into input/<folder>/ through ComfyUI's own upload, and the list then offers it.
// Verifies: R15, R16
module.exports = {
  id: 'app_upload',
  assumes: 'a node’s DOM widget (the upload button) and its combo render in App mode when chosen as inputs; ComfyUI’s upload takes any file into an input subfolder; refreshComboInNodes re-lists the folders',
  usedBy: ['data inputs (kind folder) in App mode'],
  fallback: 'App mode shows the folder list only; data is uploaded beforehand (curl or the API)',
  async check({page}) {
    const before = await page.evaluate(async () => {
      const app = window.comfyAPI.app.app, g = app.rootGraph;
      const {uploadFolder} = await import('/extensions/agent_workflow_studio/data_folder.js');
      const {setSignature} = await import('/extensions/agent_workflow_studio/publish.js');
      const node = window.LiteGraph.createNode('AgentWorkflowDataFolder'); g.add(node);
      const preview = window.LiteGraph.createNode('PreviewAny'); g.add(preview);
      node.connect(0, preview, 0);
      await uploadFolder([new File(['a,b\n1,2\n'], 'orders.csv', {type: 'text/csv'})], 'cap_upload');
      await app.refreshComboInNodes();
      const offered = node.widgets.find(w => w.name === 'folder').options.values.includes('cap_upload');
      setSignature(g, [{node: node.id, widget: 'folder'}, {node: node.id, widget: 'upload'}], [preview.id]);
      await app.extensionManager.command.execute('Comfy.ToggleLinear');
      return {offered, id: node.id};
    });
    await page.waitForTimeout(2000);
    const inApp = await page.evaluate(() => ({
      button: [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Upload files…' && b.offsetParent !== null),
      linear: Boolean(window.comfyAPI.app.app.extensionManager.workflow.activeWorkflow?.changeTracker?.activeState?.extra?.linearMode ?? true),
    }));
    await page.evaluate(() => window.comfyAPI.app.app.extensionManager.command.execute('Comfy.ToggleLinear'));
    return {ok: before.offered && inApp.button, evidence: {...before, ...inApp}};
  },
};
