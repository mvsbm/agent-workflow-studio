import assert from 'node:assert/strict';
import test from 'node:test';
import {evaluateGates} from './pi_canvas.js';

const outputs = {
  answer: [{text: ['{"sentiment": "positive"}']}],
  'saved image': [{images: [{filename: 'a.png'}]}],
};

test('gates pass, fail or need review from real outputs', () => {
  const result = evaluateGates([
    {step: 'a', description: 'has sentiment', check: {node_title: 'Answer', kind: 'contains', value: 'positive'}},
    {step: 'b', description: 'is negative', check: {node_title: ' answer ', kind: 'regex', value: 'negative'}},
    {step: 'c', description: 'one image', check: {node_title: 'Saved image', kind: 'image_count_at_least', value: 1}},
    {step: 'd', description: 'reads well'},
    {step: 'e', description: 'missing node', check: {node_title: 'Nope', kind: 'non_empty'}},
  ], outputs);
  assert.deepEqual(result.map(r => r.status), ['passed', 'failed', 'passed', 'needs_user_review', 'no_output']);
  assert.match(result[0].evidence, /positive/);
});

import {latestMatchingRun} from './pi_canvas.js';

test('latest run of this workflow, and whether it ran exactly the current graph', () => {
  const run = (id, workflow, graph) => ({prompt: [0, id, graph, {extra_pnginfo: {workflow: {id: workflow}}}, []]});
  const history = {a: run('a', 'wf', {1: 'old'}), b: run('b', 'other', {1: 'x'}), c: run('c', 'wf', {1: 'new'})};
  assert.deepEqual(latestMatchingRun(history, 'wf', JSON.stringify({1: 'new'})), {run: history.c, current: true});
  assert.equal(latestMatchingRun(history, 'wf', JSON.stringify({1: 'changed'})).current, false);
  assert.equal(latestMatchingRun(history, 'none', '{}'), null);
});

import {compactOutputs} from './pi_canvas.js';

test('run outputs are compacted for the agent', () => {
  const huge = {7: {text: ['x'.repeat(9000)], rows: Array.from({length: 50}, (_, i) => i)}};
  const compact = compactOutputs(huge);
  assert.match(compact[7].text[0], /… \[5000 more characters\]$/);
  assert.equal(compact[7].rows.length, 21);
  assert.equal(compact[7].rows.at(-1), '… [30 more items]');
  const many = Object.fromEntries(Array.from({length: 300}, (_, i) => [i, {text: ['y'.repeat(4000)]}]));
  assert.ok(JSON.stringify(compactOutputs(many)).length < 400000, 'a large run still fits the chat limit');
});
