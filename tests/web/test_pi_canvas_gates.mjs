import assert from 'node:assert/strict';
import test from 'node:test';
import {agentOutputs, gateResults, stageNumbers, stageSummaries} from '../../custom_nodes/agent_workflow_studio/web/pi_canvas.js';

// A stage with its Check nodes, as the graph holds it: outputs are keyed "stage:node".
const node = (id, type, extra = {}) => ({id, type, title: `${type} ${id}`, properties: {}, ...extra});
const stage = (id, step, inner) => node(id, 'uuid', {properties: {aws_stage: step}, isSubgraphNode: () => true, subgraph: {nodes: inner}});

test("a step's gate comes from the Check nodes inside its stage", () => {
  const graph = {nodes: [
    stage(1, 'clean', [node(2, 'AgentWorkflowCheck'), node(3, 'AgentWorkflowCheck')]),
    stage(4, 'match', [node(5, 'AgentWorkflowCheck')]),
    stage(6, 'review', [node(7, 'AgentWorkflowStub')]),
    stage(8, 'publish', [node(9, 'AgentWorkflowCheck')]),
  ]};
  const record = {outputs: {
    '1:2': {aws_check: [{passed: true, evidence: '5,480 row(s)'}]}, '1:3': {aws_check: [{passed: true, evidence: 'x'}]},
    '4:5': {aws_check: [{passed: false, evidence: '0 row(s)'}]},
  }};
  const gates = gateResults(record, graph);
  assert.deepEqual(gates.map(g => [g.step, g.status]), [['clean', 'passed'], ['match', 'failed'], ['review', 'needs_user_review'], ['publish', 'no_output']]);
  assert.equal(gates[1].checks[0].evidence, '0 row(s)');
});

test('a review step is verified once the queue reached you, and says how many are open', () => {
  const graph = {nodes: [stage(1, 'review', [node(2, 'AgentWorkflowReviewDecisions')]), stage(3, 'later', [node(4, 'AgentWorkflowReviewDecisions')])]};
  const gates = gateResults({outputs: {'1:2': {aws_human: [{open: 1606, decided: 4}]}}}, graph);
  assert.deepEqual(gates.map(g => g.status), ['passed', 'no_output']);
  assert.match(gates[0].checks[0].evidence, /4 decided · 1,606 open \(your turn/);
});

test('independent steps share a number (lanes); steps without inputs run in order', () => {
  assert.deepEqual(stageNumbers([
    {id: 'profile', takes: {folder: 'STRING'}, gives: {folder: 'STRING'}},
    {id: 'policies', takes: {folder: 'profile.folder'}}, {id: 'claims', takes: {folder: 'profile.folder'}},
    {id: 'merge', takes: {a: 'policies.t', b: 'claims.t'}},
  ]), {profile: 1, policies: 2, claims: 2, merge: 3});
  assert.deepEqual(stageNumbers([{id: 'greet'}, {id: 'show'}]), {greet: 1, show: 2});
});

import {latestMatchingRun} from '../../custom_nodes/agent_workflow_studio/web/pi_canvas.js';

test('latest run of this workflow, and whether it ran exactly the current graph', () => {
  const run = (id, workflow, graph) => ({prompt: [0, id, graph, {extra_pnginfo: {workflow: {id: workflow}}}, []]});
  const history = {a: run('a', 'wf', {1: 'old'}), b: run('b', 'other', {1: 'x'}), c: run('c', 'wf', {1: 'new'})};
  assert.deepEqual(latestMatchingRun(history, 'wf', JSON.stringify({1: 'new'})), {run: history.c, current: true});
  assert.equal(latestMatchingRun(history, 'wf', JSON.stringify({1: 'changed'})).current, false);
  assert.equal(latestMatchingRun(history, 'none', '{}'), null);
  // The examples run later through the API with other inputs: the canvas's own run still counts.
  const examples = {...history, d: run('d', 'wf', {1: 'new', input: 'example'})};
  assert.deepEqual(latestMatchingRun(examples, 'wf', JSON.stringify({1: 'new'})), {run: history.c, current: true});
});

import {compactOutputs} from '../../custom_nodes/agent_workflow_studio/web/pi_canvas.js';

test('run outputs are compacted for the agent', () => {
  const huge = {7: {text: ['x'.repeat(9000)], rows: Array.from({length: 50}, (_, i) => i)}};
  const compact = compactOutputs(huge);
  assert.match(compact[7].text[0], /… \[5000 more characters\]$/);
  assert.equal(compact[7].rows.length, 21);
  assert.equal(compact[7].rows.at(-1), '… [30 more items]');
  const many = Object.fromEntries(Array.from({length: 300}, (_, i) => [i, {text: ['y'.repeat(4000)]}]));
  assert.ok(JSON.stringify(compactOutputs(many)).length < 400000, 'a large run still fits the chat limit');
});

test('scores against a hidden answer key never reach the agent', () => {
  const record = {
    outputs: {'3': {text: ['matched']}, '9': {aws_tables: [{title: 'Score'}]}},
    prompt: [0, 'id', {'3': {class_type: 'PreviewAny'}, '9': {class_type: 'AgentWorkflowNayoseScore'}}],
  };
  assert.deepEqual(Object.keys(agentOutputs(record)), ['3']);
});

test("a run's numbers per stage, by node title, without the hidden score", () => {
  const graph = {nodes: [stage(1, 'group', [node(2, 'AgentWorkflowClusterGuard', {title: 'Build customers'}), node(3, 'AgentWorkflowNayoseScore'), node(4, 'Note')])]};
  const record = {outputs: {'1:2': {aws_headline: ['2,434 customers · 47,304 merges · 2,830 blocked']}, '1:3': {text: ['F1 0.94']}}};
  assert.deepEqual(stageSummaries(record, graph), [{stage: 'uuid 1', nodes: [{node: 'Build customers', said: '2,434 customers · 47,304 merges · 2,830 blocked'}]}]);
});
