// The Data folder node's "Upload files…" button: puts the chosen files into a new folder in ComfyUI's
// input folder with ComfyUI's own upload (POST /upload/image), refreshes the node's folder list the
// native way and selects the new folder. It works on the canvas and in App mode.
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';

/** Upload files into input/<folder>/ (ComfyUI's own upload); returns the folder name. */
export async function uploadFolder(files, folder) {
  for (const file of files) {
    const form = new FormData();
    form.append('image', file, file.name);
    form.append('type', 'input');
    form.append('subfolder', folder);
    form.append('overwrite', 'true');
    const response = await api.fetchApi('/upload/image', {method: 'POST', body: form});
    if (!response.ok) throw Error(`Could not upload ${file.name}.`);
  }
  return folder;
}

const stamp = () => new Date().toISOString().slice(0, 16).replace(/[-:T]/g, '').replace(/^(\d{8})/, '$1_');

function addUpload(node) {
  const picker = Object.assign(document.createElement('input'), {type: 'file', multiple: true, hidden: true});
  const button = Object.assign(document.createElement('button'), {type: 'button', className: 'p-button p-component p-button-sm', textContent: 'Upload files…'});
  button.style.width = '100%';
  button.addEventListener('click', () => picker.click());
  picker.addEventListener('change', async () => {
    if (!picker.files.length) return;
    const toast = app.extensionManager?.toast;
    const name = (await app.extensionManager?.dialog?.prompt?.({title: 'New data folder', message: 'Name of the folder the files go into', defaultValue: `upload_${stamp()}`})) ?? `upload_${stamp()}`;
    const folder = String(name).trim().replace(/[^A-Za-z0-9_.-]+/g, '_');
    if (!folder) return;
    try {
      await uploadFolder([...picker.files], folder);
      await app.refreshComboInNodes?.();  // the folder list, re-read from the node's schema
      const widget = node.widgets?.find(w => w.name === 'folder');
      if (widget) { widget.value = folder; widget.callback?.(folder); }
      toast?.add({severity: 'success', summary: `Uploaded to ${folder}`, detail: `${picker.files.length} file(s) in ComfyUI's input folder.`, life: 5000});
    } catch (error) {
      toast?.add({severity: 'error', summary: 'Upload failed', detail: error.message, life: 8000});
    } finally {
      picker.value = '';
    }
  });
  const box = document.createElement('div');
  box.append(button, picker);
  const widget = node.addDOMWidget('upload', 'aws_upload', box, {serialize: false, getMinHeight: () => 34});
  widget.label = 'Upload data files';
}

app.registerExtension({
  name: 'AgentWorkflowStudio.DataFolder',
  nodeCreated(node) {
    if (node.comfyClass !== 'AgentWorkflowDataFolder' && node.type !== 'AgentWorkflowDataFolder') return;
    addUpload(node);
    // A folder uploaded since the page loaded (by an API caller or another tab) joins the list, re-read
    // the native way; only when needed, since ComfyUI announces each refresh.
    queueMicrotask(() => {
      const folder = node.widgets?.find(w => w.name === 'folder');
      if (folder?.value && !folder.options?.values?.includes(folder.value)) app.refreshComboInNodes?.();
    });
  },
});
