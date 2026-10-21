import { api } from "../../scripts/api.js";
import { app } from "../../scripts/app.js";

// A small element builder ("tag.class", props, children), in place of ComfyUI's deprecated scripts/ui.js.
function $el(tag, props = {}, children = []) {
  if (Array.isArray(props)) [props, children] = [{}, props];
  const [name, ...classes] = tag.split(".");
  const node = document.createElement(name);
  const { dataset, ...rest } = props;
  Object.assign(node, rest);
  Object.assign(node.dataset, dataset ?? {});
  if (classes.length) node.classList.add(...classes);
  node.append(...children);
  return node;
}

const route = "/agent-workflow-studio/pi";

if (!document.getElementById("agent-workflow-studio-pi-settings-css")) {
  const stylesheet = document.createElement("link");
  stylesheet.id = "agent-workflow-studio-pi-settings-css";
  stylesheet.rel = "stylesheet";
  stylesheet.href = new URL("./pi_settings.css", import.meta.url).href;
  document.head.append(stylesheet);
}

// A native modal <dialog>: focus stays inside, Escape closes it, the page behind is inert.
class PiSettingsDialog {
  constructor() {
    this.element = $el("dialog.agent-workflow-studio-pi-settings");
    this.element.setAttribute("aria-label", "Pi provider credentials & model catalog");
    // Closing by any way (Escape included) clears typed keys, so none stays in the page.
    this.element.addEventListener("close", () => {
      for (const input of this.element.querySelectorAll('input[type="password"]')) input.value = "";
    });
  }

  show(content) {
    if (!this.element.isConnected) document.body.append(this.element);
    this.element.replaceChildren(content);
    if (!this.element.open) this.element.showModal();
  }

  async open() {
    const loading = $el("div.agent-workflow-studio-pi-panel", [
      $el("h3", { textContent: "Pi provider credentials & model catalog" }),
      $el("p", { textContent: "Loading the local Pi model catalog…" }),
    ]);
    this.show(loading);
    try {
      const response = await api.fetchApi(`${route}/catalog`);
      const catalog = await response.json();
      if (!response.ok) throw new Error(catalog.error || "Pi model catalog unavailable");
      this.render(catalog);
    } catch (error) {
      this.show(this.errorPanel(error.message));
    }
  }

  errorPanel(message) {
    const close = this.closeButton();
    return $el("div.agent-workflow-studio-pi-panel", [
      $el("div.agent-workflow-studio-pi-header", [
        $el("h3", { textContent: "Pi provider credentials & model catalog" }), close,
      ]),
      $el("p", { textContent: message, dataset: { state: "error" } }),
    ]);
  }

  closeButton(clear = () => {}) {
    return $el("button.agent-workflow-studio-pi-close", {
      type: "button",
      textContent: "×",
      title: "Close",
      onclick: () => {
        clear();
        this.element.close();
      },
    });
  }

  render(catalog) {
    const models = Array.isArray(catalog.models) ? catalog.models : [];
    const providers = Array.isArray(catalog.providers) ? catalog.providers : [];
    const providerSelect = $el("select", { className: "agent-workflow-studio-pi-select" });
    const modelSelect = $el("select", { className: "agent-workflow-studio-pi-select" });
    const credentialSource = $el("select", { className: "agent-workflow-studio-pi-select" }, [
      $el("option", { value: "pi_managed", textContent: "Pi-managed authentication" }),
      $el("option", { value: "studio_keychain", textContent: "Studio OS Keychain API key" }),
    ]);
    for (const provider of providers) {
      providerSelect.append($el("option", {
        value: provider.id,
        textContent: provider.has_models ? provider.id : `${provider.id} · enter model ID in the workflow node`,
      }));
    }
    const status = $el("div.agent-workflow-studio-pi-status", {
      role: "status",
      textContent: catalog.credential_vault_available
        ? "OS credential vault available."
        : "OS credential vault unavailable; key status cannot be read.",
    });
    const result = $el("div.agent-workflow-studio-pi-result", { role: "status" });
    const credentialRows = $el("div.agent-workflow-studio-pi-credentials");
    const modelSummary = $el("div.agent-workflow-studio-pi-model-summary");
    const close = this.closeButton(() => {
      for (const input of this.element.querySelectorAll('input[type="password"]')) input.value = "";
    });

    const providerInfo = () => providers.find((item) => item.id === providerSelect.value);
    const selectedModel = () => models.find((item) => item.provider === providerSelect.value && item.id === modelSelect.value);
    const setResult = (message, stateName = "") => {
      result.textContent = message;
      result.dataset.state = stateName;
    };
    const refreshModels = () => {
      const providerModels = models.filter((item) => item.provider === providerSelect.value);
      modelSelect.replaceChildren(...providerModels.map((item) => $el("option", {
        value: item.id,
        textContent: `${item.name} · ${item.id}`,
      })));
      refreshModelSummary();
      refreshCredentials();
    };
    const refreshModelSummary = () => {
      const model = selectedModel();
      modelSummary.textContent = model
        ? `${model.provider} / ${model.id}${model.api ? ` · ${model.api}` : ""}`
        : "No model available for this provider.";
    };
    const refreshCredentials = () => {
      for (const input of credentialRows.querySelectorAll('input[type="password"]')) input.value = "";
      credentialRows.replaceChildren();
      const info = providerInfo();
      if (!info?.api_key_fields?.length) {
        credentialRows.append($el("p", {
          textContent: "Pi exposes no API-key environment field for this provider. Use Pi-managed authentication or configure provider-specific credentials in Pi.",
        }));
        return;
      }
      for (const envName of info.api_key_fields) {
        const saved = Boolean(info.keychain?.[envName]);
        const key = $el("input", {
          type: "password",
          name: `pi-key-${envName}`,
          placeholder: saved ? "Replace saved key" : `Enter ${envName}`,
          autocomplete: "new-password",
          spellcheck: false,
          className: "agent-workflow-studio-pi-key",
        });
        const fieldStatus = $el("span", {
          textContent: saved ? "Saved in OS Keychain" : "Not saved",
          className: "agent-workflow-studio-pi-field-status",
        });
        const save = $el("button", {
          type: "button",
          textContent: "Save",
          onclick: async () => {
            if (!key.value.trim()) return setResult("Enter a key before saving.", "error");
            save.disabled = true;
            try {
              const response = await api.fetchApi(`${route}/credential`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ provider: providerSelect.value, env_name: envName, api_key: key.value }),
              });
              const body = await response.json();
              if (!response.ok) throw new Error(body.error || "Could not save API key");
              key.value = "";
              info.keychain[envName] = true;
              fieldStatus.textContent = "Saved in OS Keychain";
              remove.disabled = false;
              key.placeholder = "Replace saved key";
              setResult(`${envName} saved in OS Keychain. The value is not stored in the workflow or browser settings.`, "success");
            } catch (error) {
              setResult(error.message, "error");
            } finally {
              save.disabled = false;
            }
          },
        });
        const remove = $el("button", {
          type: "button",
          textContent: "Remove",
          disabled: !saved,
          onclick: async () => {
            remove.disabled = true;
            try {
              const query = new URLSearchParams({ provider: providerSelect.value, env_name: envName });
              const response = await api.fetchApi(`${route}/credential?${query}`, { method: "DELETE" });
              const body = await response.json();
              if (!response.ok) throw new Error(body.error || "Could not remove API key");
              info.keychain[envName] = false;
              fieldStatus.textContent = "Not saved";
              key.placeholder = `Enter ${envName}`;
              setResult(`${envName} removed from OS Keychain.`, "success");
            } catch (error) {
              setResult(error.message, "error");
              remove.disabled = false;
            }
          },
        });
        credentialRows.append($el("div.agent-workflow-studio-pi-key-row", [
          $el("div", [
            $el("strong", { textContent: envName }),
            fieldStatus,
          ]),
          key,
          $el("div.agent-workflow-studio-pi-actions", [save, remove]),
        ]));
      }
    };

    const copyModel = $el("button", {
      type: "button",
      textContent: "Copy provider + model IDs",
      onclick: async () => {
        const model = selectedModel();
        if (!model) return;
        try {
          await navigator.clipboard.writeText(`${model.provider}\n${model.id}`);
          setResult("Copied provider and model IDs. Paste them into a Pi CLI · Model Config node.", "success");
        } catch {
          setResult("Clipboard access was unavailable. Select the provider and model IDs in the catalog instead.", "error");
        }
      },
    });
    const applyModel = $el("button", {
      type: "button",
      textContent: "Apply to selected Pi Model Config node",
      onclick: () => {
        const model = selectedModel();
        if (!model) return setResult("Select a model with an available catalog entry first.", "error");
        const selected = Object.values(app.canvas?.selected_nodes || {});
        const configs = selected.filter((node) =>
          node.comfyClass === "AgentWorkflowPiModelConfig"
          || node.type === "AgentWorkflowPiModelConfig",
        );
        for (const node of configs) {
          for (const widget of node.widgets || []) {
            if (widget.name === "provider") widget.value = model.provider;
            if (widget.name === "model") widget.value = model.id;
          }
          node.setDirtyCanvas?.(true, true);
        }
        app.graph?.setDirtyCanvas?.(true, true);
        setResult(configs.length
          ? `Set ${model.provider} / ${model.id} on ${configs.length} selected Pi Model Config node(s).`
          : "Select one or more Pi CLI · Model Config nodes on the canvas first.", configs.length ? "success" : "error");
      },
    });
    const authCheck = $el("button", {
      type: "button",
      textContent: "Check local auth (no request)",
      onclick: async () => {
        const model = selectedModel();
        if (!model) return setResult("Select a model first.", "error");
        authCheck.disabled = true;
        setResult("Checking Pi credential readiness locally; no provider request or token refresh…", "pending");
        try {
          const response = await api.fetchApi(`${route}/auth-check`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              provider: model.provider,
              model: model.id,
              credential_source: credentialSource.value,
            }),
          });
          const body = await response.json();
          if (!response.ok) throw new Error(body.error || "Pi auth check failed");
          const method = body.auth_method ? ` · Pi auth method: ${body.auth_method}` : "";
          const reason = body.reason ? ` · ${body.reason}` : "";
          setResult(`Local auth status: ${body.status} · ${body.auth_source}${method}${reason}. No provider request was made.`, body.status === "ready" ? "success" : "error");
        } catch (error) {
          setResult(error.message, "error");
        } finally {
          authCheck.disabled = false;
        }
      },
    });
    providerSelect.addEventListener("change", refreshModels);
    modelSelect.addEventListener("change", refreshModelSummary);

    const panel = $el("div.agent-workflow-studio-pi-panel", [
      $el("div.agent-workflow-studio-pi-header", [
        $el("h3", { textContent: "Pi provider credentials & model catalog" }), close,
      ]),
      $el("p", { textContent: "Models are read from Pi CLI in offline mode. This panel stores only API keys in the OS Keychain; workflows contain provider/model selections, never credentials." }),
      status,
      $el("label", ["Provider", providerSelect]),
      $el("label", ["Model", modelSelect]),
      modelSummary,
      $el("div.agent-workflow-studio-pi-actions", [copyModel, applyModel]),
      $el("h4", { textContent: "Provider API keys / tokens" }),
      $el("p", { textContent: "Key fields are discovered from the installed Pi provider metadata. Keys are passed only to a local Pi process as environment variables and are never returned by these routes. Some providers also need non-secret project/account settings or ambient credentials configured in Pi." }),
      credentialRows,
      $el("h4", { textContent: "Credential status" }),
      $el("label", ["Auth source", credentialSource]),
      $el("div.agent-workflow-studio-pi-actions", [authCheck]),
      $el("p", { textContent: "This check uses `pi auth check --no-refresh`: local readiness only, no model call and no OAuth refresh. A live provider test is intentionally separate and is not performed here." }),
      result,
    ]);
    providerSelect.value = providers[0]?.id || "";
    refreshModels();
    this.show(panel);
  }
}

const dialog = new PiSettingsDialog();
export const openPiSettings = () => dialog.open();
app.registerExtension({
  name: "AgentWorkflowStudio.PiSettings",
  getCanvasMenuItems() {
    return [{
      content: "Agent Workflow Studio",
      submenu: {
        options: [{
          content: "Pi provider credentials & model catalog…",
          callback: () => { void dialog.open(); },
        }],
      },
    }];
  },
});
