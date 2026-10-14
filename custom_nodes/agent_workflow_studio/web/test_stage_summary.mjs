import assert from 'node:assert/strict';
import test from 'node:test';
import {stageSummary} from './stage_summary.js';

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
