import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {catalogChoices, configurePiModelDefinition, attachPiModelCombos} from '../../custom_nodes/agent_workflow_studio/web/pi_model_picker.js';
const catalog = {providers: [{id: 'beta'}, {id: 'alpha'}], models: [
  {provider: 'alpha', id: 'one'}, {provider: 'beta', id: 'two'}, {provider: 'alpha', id: 'one'},
]};
function fixture(providerValue = '', modelValue = '') {
  const calls = [];
  const widgets = [
    {name: 'provider', type: 'combo', value: providerValue, options: {}, callback: value => calls.push(['provider', value])},
    {name: 'model', type: 'combo', value: modelValue, options: {}, callback: value => calls.push(['model', value])},
    {name: 'thinking_level', value: 'high'}, {name: 'credential_source', value: 'pi_managed'},
  ];
  const node = {widgets, addDOMWidget() {throw Error('Redundant DOM widget forbidden');}};
  const picker = attachPiModelCombos(node, catalog);
  return {picker, widgets, calls};
}
test('native field definitions become combos without adding fields or changing labels/default IDs', () => {
  const data = {input: {required: {provider: ['STRING', {default: '', display_name: 'Provider ID'}], model: ['STRING', {default: ''}]}}};
  configurePiModelDefinition(data, catalog);
  assert.deepEqual(data.input.required.provider[0], ['', 'alpha', 'beta']);
  assert.equal(data.input.required.provider[1].display_name, 'Provider ID');
  assert.deepEqual(Object.keys(data.input.required), ['provider', 'model']);
});
test('provider selection clears old model and filters native model choices, preserving callbacks', () => {
  const {widgets, calls} = fixture('alpha', 'one');
  assert.deepEqual(widgets[1].options.values, ['', 'one']);
  widgets[0].value = 'beta'; widgets[0].callback('beta');
  assert.equal(widgets[1].value, '');
  assert.deepEqual(widgets[1].options.values, ['', 'two']);
  assert.deepEqual(calls, [['provider', 'beta'], ['model', '']]);
});
test('load/sync preserves stored custom IDs and does not auto-select a catalog model', () => {
  const {picker, widgets} = fixture('custom-provider', 'custom-model');
  picker.sync();
  assert.equal(widgets[0].value, 'custom-provider');
  assert.equal(widgets[1].value, 'custom-model');
  assert(widgets[0].options.values.includes('custom-provider'));
  assert(widgets[1].options.values.includes('custom-model'));
  assert.equal(fixture().widgets[0].value, '');
});
test('clearing provider leaves no unrelated model choices, without duplicate widgets', () => {
  const {widgets} = fixture('alpha', 'one');
  widgets[0].value = ''; widgets[0].callback('');
  assert.deepEqual(widgets[1].options.values, ['']);
  assert.equal(widgets.length, 4);
});
test('catalog failure leaves original native STRING fields editable, with no extra panel', async () => {
  let extension;
  const warnings = [];
  const source = readFileSync(new URL('../../custom_nodes/agent_workflow_studio/web/pi_model_picker_ui.js', import.meta.url), 'utf8')
    .replace(/import[^;]+;/g, '');
  vm.runInNewContext(source, {
    app: {registerExtension(value) {extension = value;}},
    api: {async fetchApi() {throw Error('offline catalog unavailable');}},
    configurePiModelDefinition, attachPiModelCombos, AbortController, setTimeout, clearTimeout,
    console: {warn(message) {warnings.push(message);}},
  });
  const data = {name: 'AgentWorkflowPiModelConfig', input: {required: {
    provider: ['STRING', {default: 'custom'}], model: ['STRING', {default: 'custom-model'}],
  }}};
  await extension.beforeRegisterNodeDef({}, data);
  assert.equal(data.input.required.provider[0], 'STRING');
  assert.equal(data.input.required.model[0], 'STRING');
  assert.equal(warnings.length, 1);
});

test('catalog filtering deduplicates models and marks manual IDs', () => {
  assert.deepEqual(catalogChoices(catalog, 'alpha', 'custom'), {
    providers: ['alpha', 'beta'], models: [{id: 'one', name: 'one'}], manualModel: true,
  });
});
