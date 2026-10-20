// Verifies: R6, R27, R28
import assert from 'node:assert/strict';
import test from 'node:test';
import {designLabels, nodePath, partsText, requestLabel, stageSummary, stageView} from '../../custom_nodes/agent_workflow_studio/web/stage_summary.js';

test('a stage card tells what each inner node did', () => {
  const tool = {type: 'AgentWorkflowAgentTool', properties: {aws_tables: [{title: 'Operation', rows: []}, {title: 'Data in → out', rows: [['in', 'mentions.csv', '13,575', ''], ['out', 'pairs.jsonl', '94,254', '']]}]}};
  const rules = {type: 'AgentWorkflowDecisionRules', properties: {aws_flow: [{total: 9, branches: [{label: 'a', decision: 'match', count: 2}, {label: 'b', decision: 'review', count: 3}, {label: 'c', decision: 'match', count: 4}]}]}};
  const guard = {type: 'AgentWorkflowClusterGuard', properties: {aws_tree: [{label: 'Candidate pairs', count: 9, children: [
    {label: 'Merges', count: 6, children: [{label: 'accepted', count: 5}, {label: 'blocked by the guard', count: 1}]},
    {label: 'Customers: 4 (largest 3 records)', count: 7, children: []}]}]}};
  const exportNode = {type: 'AgentWorkflowNayoseExport', properties: {aws_tables: [{title: 'claims export', rows: [['rows', '1808'], ['file', 'input/nayose_bench/claims.csv']]}]}};
  assert.deepEqual(stageSummary([tool, rules, guard, exportNode]), [
    'mentions.csv 13,575 → pairs.jsonl 94,254',
    'match 6 · review 3',
    'Customers: 4 (largest 3 records)',
    '1 merge blocked',
  ]);
  assert.deepEqual(stageSummary([exportNode]), ['claims.csv 1,808']);
  const profile = {type: 'AgentWorkflowProfileData', properties: {aws_tables: [{title: 'Files', rows: [['claims.csv', '1,808', '17', '8'], ['koza.csv', '5,396', '9', '1']]}]}};
  assert.deepEqual(stageSummary([profile]), ['2 files profiled · 9 mixed-format columns']);
  const step = (x, into, out) => ({type: 'AgentWorkflowNormalizeText', pos: [x, 0], properties: {aws_tables: [{title: 'Normalize text', rows: [['rows in', into], ['rows out', out]]}]}});
  assert.deepEqual(stageSummary([step(500, '13,575', '13,575'), step(0, '—', '3,589')]), ['2 steps · 3,589 rows → 13,575']);
});

test('a node badge reads its step parts in order, ports of a kind together', () => {
  assert.equal(partsText(['gives different', 'gives matched', 'does', 'takes data', 'decision D2']), 'takes data · does · gives different, matched · decision D2');
  assert.equal(partsText(['does']), 'does');
});

test('design badges: a stage serves titled requests; inner nodes are numbered in reading order', () => {
  const requests = [{id: 'R3', title: 'Own cleaning lane per system'}, {id: 'R1', we_understood: 'When run, the workflow shows hello world.'}];
  assert.deepEqual(designLabels({properties: {aws_step: {covers: ['R3', 'R1']}}}, null, requests).map(l => l.text),
    ['serves R3 · Own cleaning lane per system', 'serves R1 · Shows hello world']);
  const read = {type: 'Read', pos: [0, 0], properties: {aws_realizes: ['takes folder', 'does']}};
  const split = {type: 'Split', pos: [400, 0], properties: {}};
  const check = {type: 'AgentWorkflowCheck', pos: [200, 300]};
  const stage = {properties: {aws_stage_order: 2}, subgraph: {nodes: [split, check, read]}};
  assert.deepEqual(designLabels(read, stage), [{text: '2.1 · takes folder · does', kind: 'part'}]);
  assert.deepEqual(designLabels(split, stage), [{text: '2.2 · + extra, not in the design', kind: 'extra'}]);
  assert.equal(designLabels(check, stage)[0].text, 'verifies the stage');
  assert.deepEqual(designLabels({type: 'X', properties: {}}, null), []); // top level, not a stage
  assert.equal(requestLabel({id: 'R2', title: 'You check uncertain pairs'}), 'R2 · You check uncertain pairs');
});

test('a stage shows each part of its step with the nodes realizing it, and why each node exists', () => {
  const requests = [{id: 'R3', title: 'Own cleaning lane per system'}];
  const read = {type: 'Read', title: 'Read policies', pos: [0, 0], properties: {aws_realizes: ['takes folder', 'does']}};
  const roles = {type: 'Stack', title: 'Stack roles', pos: [300, 0], properties: {aws_realizes: ['does', 'gives table']}};
  const loose = {type: 'Preview', title: 'Peek', pos: [600, 0], properties: {}};
  const check = {type: 'AgentWorkflowCheck', title: 'Check · table', pos: [300, 300]};
  const stage = {title: '2 · Clean policies', properties: {aws_stage_order: 2, aws_step: {covers: ['R3'], takes: {folder: 'x'}, gives: {table: 'TABLE'}, gate: {says: 'At least 4,500 rows'}}},
    subgraph: {description: 'Does: clean', nodes: [loose, check, roles, read]}};
  const view = stageView(stage, requests);
  assert.deepEqual(view.parts, [
    {part: 'takes folder', nodes: ['2.1 Read policies']},
    {part: 'does', nodes: ['2.1 Read policies', '2.2 Stack roles']},
    {part: 'gives table', nodes: ['2.2 Stack roles']},
  ]);
  assert.deepEqual([view.checks, view.extra, view.serves.map(r => r.id)], [['Check · table'], ['2.3 Peek'], ['R3']]);
  assert.deepEqual(nodePath(roles, stage, requests), ['R3 · Own cleaning lane per system', '2 · Clean policies', '2.2 Stack roles · does · gives table']);
  assert.equal(nodePath(loose, stage, requests)[2], '2.3 Peek · not in the design');
});
