// Loads the type standard for the frontend (standards_core.js holds the logic) and rewrites old
// socket names in every workflow before ComfyUI configures it.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {renameTypes, setRegistry} from './standards_core.js';

const loaded = api.fetchApi('/agent-workflow-studio/standards').then(r => r.json()).then(setRegistry).catch(() => {});

app.registerExtension({
  name: 'AgentWorkflowStudio.Standards',
  async setup() { await loaded; },
  async beforeConfigureGraph(graph) {
    await loaded;
    renameTypes(graph);
  },
});
