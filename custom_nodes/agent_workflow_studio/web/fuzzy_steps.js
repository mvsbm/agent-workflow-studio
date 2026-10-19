// Fuzzy steps on the canvas: an unfinished step is a Fuzzy node with a plain-language goal.
// Its state is visible by color and on the node: Planned (not run) -> Fuzzy (the model
// plans it each run; latest plan shown) -> Hardened (the plan frozen into a native
// subgraph of real nodes, titled with the goal; no model call on later runs).
import {deepNodes} from './pi_canvas.js';
import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {commitWorkflow} from './project_versions.js';

const FUZZY = 'AgentWorkflowFuzzyNode';
const STATES = {
  planned: {color: '#3d4a5c', bgcolor: '#2b3340', label: 'PLANNED · not run yet. Run the workflow to let the model plan this step.'},
  fuzzy: {color: '#5b3d7a', bgcolor: '#3a2a4f', label: 'FUZZY · the model plans this step on every run. Latest plan:'},
  hardened: {color: '#2f5d3a', bgcolor: '#24402b'},
};

function setState(node, state, detail = '') {
  Object.assign(node, {color: STATES[state].color, bgcolor: STATES[state].bgcolor});
  const view = node.widgets?.find(w => w.name === 'plan_view');
  if (view) view.value = detail ? `${STATES[state].label}\n${detail}` : STATES[state].label;
  node.setDirtyCanvas?.(true, true);
}

function showPlan(node) {
  const plan = node.properties?.aws_plan;
  if (!plan) return setState(node, 'planned');
  const lines = (node.properties.aws_plan_text ?? '').split('\n').filter(line => !/^(Run record:|FUZZY PLAN · )/.test(line));
  setState(node, 'fuzzy', `${lines.join('\n')}\nRight-click → Harden this step to freeze this plan.`);
}

function addPlanView(node) {
  if (node.widgets?.some(w => w.name === 'plan_view')) return;
  const pre = document.createElement('pre');
  pre.className = 'aw-fuzzy-plan';
  pre.style.cssText = 'margin:0;white-space:pre-wrap;font-size:11px;opacity:.85;overflow:auto;max-height:180px';
  // Display only: never sent to the backend as an input.
  node.addDOMWidget('plan_view', 'aws_plan_view', pre, {
    serialize: false,
    getValue: () => pre.textContent,
    setValue: value => { pre.textContent = value ?? ''; },
    getMinHeight: () => 120,
  });
}

// Links live in the graph the node is in (a stage's subgraph, or the top level).
const linkIn = (graph, id) => (graph.links instanceof Map ? graph.links.get(id) : graph.links?.[id]);

/** Replace a Fuzzy node by its latest plan as real nodes, grouped in a subgraph titled with the goal. */
export async function hardenStep(node) {
  const plan = node.properties?.aws_plan;
  if (!plan) throw Error('Run the workflow first so the model can plan this step.');
  const graph = node.graph ?? app.graph;
  const goal = String(node.widgets?.find(w => w.name === 'goal')?.value ?? '').trim() || 'Hardened step';
  const origin = [...node.pos];
  const snapshot = structuredClone(graph.serialize());
  app.canvas.emitBeforeChange();
  try {
    // Where the step's input text comes from: the linked node, or the typed widget value.
    const textSlot = node.inputs.findIndex(input => input.name === 'text');
    const inputLink = linkIn(graph, node.inputs[textSlot]?.link);
    let source;
    if (inputLink) {
      source = {node: graph.getNodeById(inputLink.origin_id), slot: inputLink.origin_slot};
    } else {
      const literal = window.LiteGraph.createNode('PrimitiveString');
      literal.title = 'Input text';
      literal.pos = [node.pos[0] - 260, node.pos[1]];
      graph.add(literal);
      literal.widgets.find(w => w.name === 'value').value = node.widgets.find(w => w.name === 'text')?.value ?? '';
      source = {node: literal, slot: 0};
    }
    const consumers = (node.outputs[0]?.links ?? []).map(id => linkIn(graph, id)).filter(Boolean)
      .map(link => ({node: graph.getNodeById(link.target_id), slot: link.target_slot}));

    const built = {}, created = [];
    const resolve = ref => (ref.from === '$input' ? source : {node: built[ref.from], slot: ref.output ?? 0});
    plan.steps.forEach((step, index) => {
      const created_ = window.LiteGraph.createNode(step.class_type);
      if (!created_) throw Error(`Node is not installed: ${step.class_type}`);
      created_.pos = [node.pos[0] + index * 280, node.pos[1] + 40];
      graph.add(created_);
      created.push(created_);
      built[step.id] = created_;
      for (const [name, value] of Object.entries(step.inputs)) {
        if (value && typeof value === 'object') continue;
        const widget = created_.widgets?.find(w => w.name === name);
        if (!widget) throw Error(`${step.class_type} has no widget for ${name}; this plan cannot be frozen yet.`);
        widget.value = value;
        widget.callback?.(value, app.canvas, created_);
      }
    });
    plan.steps.forEach(step => {
      for (const [name, value] of Object.entries(step.inputs)) {
        if (!value || typeof value !== 'object') continue;
        const from = resolve(value), target = built[step.id];
        if (!from.node.connect(from.slot, target, target.findInputSlot(name))) throw Error(`Cannot connect ${step.id}.${name}`);
      }
    });
    const result = resolve(plan.output);
    graph.remove(node);
    for (const consumer of consumers) result.node.connect(result.slot, consumer.node, consumer.slot);
    if (!created.length) return null; // the plan passed its input straight through

    // Links to the outside must exist before converting: they become the subgraph's inputs/outputs.
    const {node: step} = graph.convertToSubgraph(new Set(created));
    step.pos = origin; // the hardened step takes the fuzzy step's place
    step.title = goal.slice(0, 80);
    if (step.subgraph) step.subgraph.name = goal.slice(0, 80);
    step.properties = {...step.properties, aws_step: {state: 'hardened', goal, plan}};
    setState(step, 'hardened');
    return step;
  } catch (error) {
    graph.configure(snapshot);
    throw Error(`Hardening rolled back: ${error.message}`);
  } finally {
    app.canvas.emitAfterChange();
  }
}

function notify(summary, detail, severity = 'error') {
  if (app.extensionManager.toast?.add) app.extensionManager.toast.add({severity, summary, detail, life: 6000});
  else window.alert(`${summary}: ${detail}`);
}

app.registerExtension({
  name: 'AgentWorkflowStudio.FuzzySteps',
  beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== FUZZY) return;
    // LiteGraph always calls these, including for nodes the agent creates via createNode.
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      addPlanView(this);
      showPlan(this);
      return result;
    };
    const configure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const result = configure?.apply(this, arguments);
      showPlan(this);
      return result;
    };
    const original = nodeType.prototype.getExtraMenuOptions;
    nodeType.prototype.getExtraMenuOptions = function (canvas, options) {
      original?.apply(this, arguments);
      options.push(null, {
        content: 'Harden this step (freeze the latest plan)',
        disabled: !this.properties?.aws_plan,
        callback: async () => {
          const goal = this.widgets?.find(w => w.name === 'goal')?.value || 'step';
          try {
            await hardenStep(this);
            await commitWorkflow(`Harden: ${goal}`, 'user');
          } catch (error) {
            notify('Could not harden step', error.message);
          }
        },
      });
    };
  },
  setup() {
    api.addEventListener('executed', ({detail}) => {
      const plan = detail?.output?.aws_plan?.[0];
      if (!plan) return;
      // Inside a stage the run names the node by its path ("stage:node").
      const node = deepNodes(app.rootGraph ?? app.graph).find(({runId}) => runId === String(detail.node))?.node;
      if (node?.type !== FUZZY) return;
      node.properties = {...node.properties, aws_plan: plan, aws_plan_text: detail.output.text?.[0] ?? ''};
      showPlan(node);
    });
  },
});
