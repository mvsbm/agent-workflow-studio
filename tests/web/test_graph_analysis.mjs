// Verifies: R11
import assert from 'node:assert/strict';
import test from 'node:test';
import {analyzeGraph, findConverterPaths} from '../../custom_nodes/agent_workflow_studio/web/graph_analysis.js';

const S = ['STRING', {multiline: true}];
const defs = {
  PrimitiveString: {input: {required: {value: S}}, output: ['STRING']},
  CaseConverter: {input: {required: {string: S, mode: ['COMBO', {options: ['UPPERCASE']}]}}, output: ['STRING']},
  StringConcatenate: {input: {required: {string_a: S, string_b: S}}, output: ['STRING']},
  StringTrim: {input: {required: {string: S}}, output: ['STRING']},
  TextToUppercase: {input: {required: {text: S}}, output: ['STRING'], deprecated: true},
  PreviewAny: {input: {required: {source: ['*', {}]}}, output: ['STRING'], output_node: true},
  VAEDecode: {input: {required: {samples: ['LATENT', {}], vae: ['VAE', {}]}}, output: ['IMAGE']},
  ImageToMask: {input: {required: {image: ['IMAGE', {}], channel: [['red', 'green'], {}]}}, output: ['MASK']},
  MaskToImage: {input: {required: {mask: ['MASK', {}]}}, output: ['IMAGE']},
  ForcedText: {input: {required: {text: ['STRING', {forceInput: true}]}}, output: ['STRING']},
};
const node = (id, type, inputs = [], outputs = [], extra = {}) => ({
  id, type, inputs: inputs.map(([name, t, link = null]) => ({name, type: t, link})), outputs: outputs.map(t => ({type: t})), ...extra,
});

test('outputs, dead nodes, problems and checkpoints', () => {
  const graph = {
    nodes: [
      node(1, 'PrimitiveString', [], ['STRING']),
      node(2, 'CaseConverter', [['string', 'STRING', 1]], ['STRING']),
      node(7, 'CaseConverter', [['string', 'STRING', 2]], ['STRING']),
      node(8, 'StringConcatenate', [['string_a', 'STRING', 3], ['string_b', 'STRING', 4]], ['STRING']),
      node(3, 'PreviewAny', [['source', '*', 5]], ['STRING'], {title: 'Result'}),
      node(4, 'StringTrim', [['string', 'STRING']], ['STRING']),
      node(5, 'VAEDecode', [['samples', 'LATENT'], ['vae', 'VAE']], ['IMAGE']),
      node(6, 'TextToUppercase', [], ['STRING']),
      node(9, 'ForcedText', [['text', 'STRING']], ['STRING']),
      node(20, 'Note'),
    ],
    links: [[1, 1, 0, 2, 0, 'STRING'], [2, 1, 0, 7, 0, 'STRING'], [3, 2, 0, 8, 0, 'STRING'], [4, 7, 0, 8, 1, 'STRING'], [5, 8, 0, 3, 0, 'STRING']],
  };
  const a = analyzeGraph(graph, defs);
  assert.deepEqual(a.outputs.map(o => o.title), ['Result']);
  assert.deepEqual(a.will_not_run.map(n => n.id).sort(), [4, 5, 6, 9]);
  const kinds = a.problems.map(p => `${p.kind}:${p.node?.id ?? ''}:${p.input ?? ''}`).sort();
  assert.deepEqual(kinds, ['deprecated:6:', 'unconnected_input:5:samples', 'unconnected_input:5:vae', 'unconnected_input:9:text']);
  // Every path to the output passes through 1 and 8, but not through either branch.
  assert.deepEqual(a.checkpoints[0].must_pass.map(n => n.id), [1, 8]);
});

test('type mismatches, cycles and graphs with nothing to run', () => {
  const mismatch = analyzeGraph({
    nodes: [node(1, 'VAEDecode', [], ['IMAGE']), node(2, 'CaseConverter', [['string', 'STRING', 1]], ['STRING']), node(3, 'PreviewAny', [['source', '*', 2]], [])],
    links: [[1, 1, 0, 2, 0, 'IMAGE'], [2, 2, 0, 3, 0, 'STRING']],
  }, defs);
  assert.ok(mismatch.problems.some(p => p.kind === 'type_mismatch' && /IMAGE is connected to an input expecting STRING/.test(p.message)));
  const cycle = analyzeGraph({
    nodes: [node(1, 'StringTrim', [['string', 'STRING', 2]], ['STRING']), node(2, 'StringTrim', [['string', 'STRING', 1]], ['STRING'])],
    links: [[1, 1, 0, 2, 0, 'STRING'], [2, 2, 0, 1, 0, 'STRING']],
  }, defs);
  assert.deepEqual(cycle.problems.map(p => p.kind).sort(), ['cycle', 'no_output_node']);
});

test('converter paths between types', () => {
  assert.deepEqual(findConverterPaths(defs, 'IMAGE', 'MASK'), [[{class_type: 'ImageToMask', output: 0, gives: 'MASK'}]]);
  assert.deepEqual(findConverterPaths(defs, 'MASK', 'MASK', {maxSteps: 2}).map(p => p.map(s => s.class_type)), [['MaskToImage', 'ImageToMask']]);
  assert.deepEqual(findConverterPaths(defs, 'LATENT', 'IMAGE'), [], 'VAEDecode needs two connected inputs, so it is not a simple converter');
});
