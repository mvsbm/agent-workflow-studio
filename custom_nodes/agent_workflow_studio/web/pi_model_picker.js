// Native view adapter: backend inputs remain STRING; no extra controls are added.
export function catalogChoices(catalog, provider, currentModel = '') {
  const providers = [...new Set([
    ...(catalog.providers ?? []).map(item => item.id),
    ...(catalog.models ?? []).map(item => item.provider),
  ].filter(value => typeof value === 'string' && value))].sort();
  const seen = new Set();
  const models = (catalog.models ?? []).filter(item => item.provider === provider &&
    typeof item.id === 'string' && item.id && !seen.has(item.id) && seen.add(item.id))
    .map(item => ({id: item.id, name: item.name || item.id}));
  return {providers, models, manualModel: !!currentModel && !models.some(item => item.id === currentModel)};
}
function valuesWithCurrent(values, current) {
  return [...new Set(['', ...values, ...(typeof current === 'string' && current ? [current] : [])])];
}
export function configurePiModelDefinition(data, catalog) {
  const inputs = data.input?.required;
  if (!inputs?.provider || !inputs?.model) return;
  const {providers} = catalogChoices(catalog, '');
  const allModels = (catalog.models ?? []).map(item => item.id).filter(id => typeof id === 'string');
  for (const [name, choices] of [['provider', providers], ['model', allModels]]) {
    const options = inputs[name][1] ?? {};
    inputs[name] = [valuesWithCurrent(choices, options.default), {
      ...options,
      tooltip: 'Choose an ID from the offline Pi catalog. Catalog membership does not verify authentication.',
    }];
  }
}
export function attachPiModelCombos(node, catalog, markDirty = () => {}) {
  const provider = node.widgets?.find(widget => widget.name === 'provider');
  const model = node.widgets?.find(widget => widget.name === 'model');
  if (!provider || !model || provider.type !== 'combo' || model.type !== 'combo') return null;
  let previousProvider = provider.value;
  const sync = () => {
    const choices = catalogChoices(catalog, provider.value, model.value);
    provider.options.values = valuesWithCurrent(choices.providers, provider.value);
    model.options.values = valuesWithCurrent(choices.models.map(item => item.id), model.value);
    previousProvider = provider.value;
    markDirty();
  };
  const original = provider.callback;
  provider.callback = function (...args) {
    const changed = provider.value !== previousProvider;
    const result = original?.apply(this, args);
    if (changed) {
      model.value = '';
      model.callback?.('');
    }
    sync();
    return result;
  };
  sync();
  return {sync};
}
