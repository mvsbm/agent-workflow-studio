import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";
import { $el, ComfyDialog } from "../../scripts/ui.js";

const route = "/agent-workflow-studio/opencode-go";

class OpenCodeCredentialsDialog extends ComfyDialog {
  constructor() {
    super();
    this.element.classList.add("agent-workflow-studio-credentials");
  }

  createButtons() {
    return [];
  }

  async open() {
    let configured = false;
    let statusText = "Checking OS credential vault…";
    try {
      const response = await api.fetchApi(`${route}/status`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "Could not read credential status");
      configured = body.configured;
      statusText = configured ? `Key saved in OS credential vault · ${body.model}` : "No key saved";
    } catch (error) {
      statusText = error.message;
    }

    const status = $el("div.agent-workflow-studio-credential-status", { textContent: statusText });
    const key = $el("input", {
      type: "password",
      name: "opencode-go-api-key",
      placeholder: "Paste a newly rotated OpenCode Go API key",
      autocomplete: "new-password",
      spellcheck: false,
      className: "agent-workflow-studio-credential-input",
    });
    const result = $el("div.agent-workflow-studio-credential-result", { role: "status" });
    const setResult = (text) => { result.textContent = text; };
    const save = $el("button", {
      type: "button",
      textContent: "Save key",
      onclick: async () => {
        if (!key.value.trim()) return setResult("Enter an API key first.");
        save.disabled = true;
        try {
          const response = await api.fetchApi(`${route}/credential`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ api_key: key.value }),
          });
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || "Could not save key");
          key.value = "";
          status.textContent = `Key saved in OS credential vault · ${body.model}`;
          test.disabled = false;
          remove.disabled = false;
          setResult("Saved. The key is not stored in browser settings or the workflow.");
        } catch (error) {
          setResult(error.message);
        } finally {
          save.disabled = false;
        }
      },
    });
    const test = $el("button", {
      type: "button",
      textContent: "Test OpenCode Go",
      disabled: !configured,
      onclick: async () => {
        test.disabled = true;
        setResult("Testing provider connection…");
        try {
          const response = await api.fetchApi(`${route}/test`, { method: "POST" });
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || "Connection test failed");
          setResult(`Connected · ${body.model}`);
        } catch (error) {
          setResult(error.message);
        } finally {
          test.disabled = false;
        }
      },
    });
    const remove = $el("button", {
      type: "button",
      textContent: "Remove saved key",
      disabled: !configured,
      onclick: async () => {
        remove.disabled = true;
        try {
          const response = await api.fetchApi(`${route}/credential`, { method: "DELETE" });
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || "Could not remove key");
          status.textContent = "No key saved";
          key.value = "";
          setResult("Removed from the OS credential vault.");
          test.disabled = true;
        } catch (error) {
          setResult(error.message);
          remove.disabled = false;
        }
      },
    });
    const panel = $el("div.agent-workflow-studio-credential-panel", [
      $el("h3", { textContent: "OpenCode Go credentials" }),
      $el("p", { textContent: "Used by the OpenCode Go · Structured Agent node. The key is stored in the server OS credential vault; it is never returned to the browser or saved in a workflow." }),
      status,
      key,
      $el("div.agent-workflow-studio-credential-actions", [save, test, remove]),
      result,
    ]);
    this.show(panel);
    key.focus();
  }
}

const dialog = new OpenCodeCredentialsDialog();
app.registerExtension({
  name: "AgentWorkflowStudio.OpenCodeGoCredentials",
  getCanvasMenuItems() {
    return [{
      content: "Agent Workflow Studio",
      submenu: {
        options: [{
          content: "OpenCode Go API key…",
          callback: () => { void dialog.open(); },
        }],
      },
    }];
  },
});
