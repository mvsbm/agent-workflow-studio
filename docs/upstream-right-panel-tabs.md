# Draft issue for Comfy-Org/ComfyUI_frontend (not filed)

**Title:** Extension API for right side panel tabs (`rightSidePanelTabs`)

**Problem.** Extensions can add sidebar tabs, bottom panel tabs and action bar buttons, but not tabs in the right side panel. The panel's tab list is hard-coded (errors, parameters, nodes, info, settings), and a watcher resets any tab it does not know. Information about the open workflow (as opposed to the whole app) belongs in the panel's **Workflow overview**, which the panel shows when nothing is selected. Today an extension has to attach to the panel's DOM by test id, which breaks on any change to the panel.

**Use case.** Agent Workflow Studio shows, for the open workflow:
- its approved contract, and progress against it;
- its version history;
- the data passed between its stages;
- a review queue for undecided results;
- the customers it built.

These are workflow-scoped, so the left sidebar (global tools) is the wrong place for them.

**Proposal.**

```js
app.registerExtension({
  name: 'example.workflowPanels',
  rightSidePanelTabs: [{
    id: 'contract',
    label: 'Contract',          // or () => t('…')
    icon: 'icon-[lucide--clipboard-check]',
    when: 'workflow',           // 'workflow' (nothing selected) | 'node' | 'always'
    render: (el, {workflow, selection}) => { /* mount into el; return an optional cleanup */ },
  }],
})
```

- The panel adds these tabs to its list; `isActiveTabAvailable` treats them as known.
- `rightSidePanelStore.openPanel('ext:<name>:<id>')` opens one, so an action bar button or a command can jump to it.

**Compatibility.** The change is additive. With no extension tabs, the panel is unchanged.
