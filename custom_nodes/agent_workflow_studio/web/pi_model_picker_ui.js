import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {configurePiModelDefinition, attachPiModelCombos} from './pi_model_picker.js';

const attached = new WeakMap();
let catalog;
let request;
async function loadCatalog() {
  if (!request) {
    request = (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await api.fetchApi('/agent-workflow-studio/pi/catalog', {signal: controller.signal});
        if (!response.ok) throw Error('Catalog unavailable');
        const data = await response.json();
        catalog = {
          providers: data.providers.map(({id}) => ({id})),
          models: data.models.map(({provider, id, name}) => ({provider, id, name})),
        };
        return catalog;
      } finally { clearTimeout(timer); }
    })();
  }
  return request;
}
function attach(node) {
  if (node.type !== 'AgentWorkflowPiModelConfig' || !catalog) return;
  if (attached.has(node)) { attached.get(node).sync(); return; }
  if (!node.graph || node.graph.getNodeById(node.id) !== node) return;
  const binding = attachPiModelCombos(node, catalog, () => node.graph?.setDirtyCanvas(true, true));
  if (binding) attached.set(node, binding);
}
app.registerExtension({
  name: 'AgentWorkflowStudio.PiModelPicker',
  async beforeRegisterNodeDef(_nodeType, data) {
    if (data.name !== 'AgentWorkflowPiModelConfig') return;
    try { configurePiModelDefinition(data, await loadCatalog()); }
    catch { console.warn('Local Pi catalog unavailable; ID fields remain editable. Refresh to retry.'); }
  },
  nodeCreated(node) { queueMicrotask(() => attach(node)); },
  loadedGraphNode(node) { attach(node); },
  afterConfigureGraph() { for (const node of app.graph.nodes) attach(node); },
});
