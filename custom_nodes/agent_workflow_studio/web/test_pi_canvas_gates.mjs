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
