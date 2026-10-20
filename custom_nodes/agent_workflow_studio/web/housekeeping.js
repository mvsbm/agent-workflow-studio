// Old run outputs, kept for a set number of days (a native ComfyUI setting) and cleaned at startup;
// "Clean up old run outputs…" in the menu shows what it frees first. Runs never overwrite, so this
// is what keeps the output folder from growing without end. The newest of each kind always stays.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';

const SETTING = 'AgentWorkflowStudio.KeepOutputsDays';
// Nothing is deleted automatically until the user has seen a clean-up and confirmed it once.
const AGREED = 'AgentWorkflowStudio.OutputsCleanupAgreed';
const days = () => Number(app.extensionManager?.setting?.get?.(SETTING) ?? 30);
const size = bytes => (bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.round(bytes / 1e6)} MB`);

async function cleanup(dryRun) {
  const response = await api.fetchApi('/agent-workflow-studio/outputs/cleanup', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({days: days(), dry_run: dryRun}),
  });
  if (!response.ok) throw Error((await response.json()).error ?? 'Clean-up failed.');
  return response.json();
}

async function confirmAndClean() {
  const toast = app.extensionManager?.toast;
  if (days() <= 0) return toast?.add({severity: 'info', summary: 'Run outputs are kept forever', detail: `Set "${SETTING}" above 0 to clean old ones.`, life: 6000});
  const preview = await cleanup(true);
  if (!preview.count) return toast?.add({severity: 'info', summary: 'Nothing to clean', detail: `No run outputs older than ${days()} days (the newest of each is always kept).`, life: 5000});
  const message = `Remove ${preview.count} run output(s) older than ${days()} days, freeing ${size(preview.freed_bytes)}? The newest result of every workflow, tool and file name stays, and answer keys are never touched.`;
  const ok = await (app.extensionManager?.dialog?.confirm?.({title: 'Clean up old run outputs', message}) ?? Promise.resolve(window.confirm(message)));
  if (!ok) return;
  const done = await cleanup(false);
  await app.extensionManager?.setting?.set?.(AGREED, true);
  toast?.add({severity: 'success', summary: 'Old run outputs removed', detail: `${done.count} removed, ${size(done.freed_bytes)} freed.`, life: 6000});
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Housekeeping',
  settings: [{
    id: SETTING,
    category: ['Agent Workflow Studio', 'Outputs', 'Keep run outputs'],
    name: 'Keep run outputs for (days; 0 keeps them forever)',
    tooltip: 'Older run outputs are removed at startup, once you have confirmed a clean-up from the menu; the newest result of every workflow, tool and file name is always kept.',
    type: 'number',
    attrs: {min: 0, step: 1},
    defaultValue: 30,
  }, {id: AGREED, name: 'Old run outputs are cleaned at startup', type: 'hidden', defaultValue: false}],
  commands: [{id: 'AgentWorkflowStudio.CleanOutputs', label: 'Clean up old run outputs…', function: () => confirmAndClean().catch(error => app.extensionManager?.toast?.add({severity: 'error', summary: 'Clean-up failed', detail: error.message, life: 8000}))}],
  menuCommands: [{path: ['Agent Workflow Studio'], commands: ['AgentWorkflowStudio.CleanOutputs']}],
  async setup() {
    // A watched API that fails stays on screen until dismissed (ComfyUI's own toast); its recovery too.
    api.addEventListener('aws.watch', ({detail: w}) => app.extensionManager?.toast?.add(w.passing
      ? {severity: 'success', summary: `Watch ${w.name}: passing again`, detail: `Checks ${w.checks} on ${w.semver ?? 'the published version'}.`, life: 8000}
      : {severity: 'error', summary: `Watch ${w.name}: FAILING`, detail: `Checks ${w.checks} on ${w.semver ?? 'the published version'}. Open the workflow's Contract tab to see what failed.`}));
    if (days() <= 0) return;
    if (app.extensionManager?.setting?.get?.(AGREED)) return void cleanup(false).catch(() => {});  // agreed once: quietly
    // Not agreed yet: say what old runs take, and where to clean them; delete nothing.
    const preview = await cleanup(true).catch(() => null);
    if (preview?.count) app.extensionManager?.toast?.add({severity: 'info', summary: `Old run outputs: ${size(preview.freed_bytes)}`,
      detail: 'Menu → Agent Workflow Studio → Clean up old run outputs… shows what it removes first.', life: 12000});
  },
});
