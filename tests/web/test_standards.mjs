// Verifies: R11
import assert from 'node:assert/strict';
import test from 'node:test';
import {currentType, renameTypes, setRegistry, typeOfKind} from '../../custom_nodes/agent_workflow_studio/web/standards_core.js';

setRegistry({
  types: [{name: 'FOLDER', scope: 'type', kind: 'folder', aliases: ['directory'], socket: 'STRING', app_input: 'AgentWorkflowDataFolder'}],
  socket_aliases: {PAIRS: 'RECORD_PAIRS', CUSTOMERS: 'RECORD_CLUSTERS'},
});

test('a promised kind finds its registered type, by an alias too', () => {
  assert.equal(typeOfKind('directory').app_input, 'AgentWorkflowDataFolder');
  assert.equal(typeOfKind('hologram'), undefined);
});

test('a workflow saved before a rename reads with the current socket names, everywhere a type is saved', () => {
  const saved = {
    nodes: [{inputs: [{type: 'PAIRS,DICT'}], outputs: [{type: 'CUSTOMERS'}]}],
    links: [[1, 2, 0, 3, 0, 'PAIRS'], [2, 3, 0, 4, 0, 'TABLE']],
    definitions: {subgraphs: [{inputs: [{type: 'PAIRS'}], outputs: [{type: 'STRING'}], nodes: [], links: [{type: 'CUSTOMERS'}]}]},
  };
  renameTypes(saved);
  assert.equal(saved.nodes[0].inputs[0].type, 'RECORD_PAIRS,DICT');
  assert.equal(saved.nodes[0].outputs[0].type, 'RECORD_CLUSTERS');
  assert.deepEqual(saved.links.map(l => l[5]), ['RECORD_PAIRS', 'TABLE']);
  assert.equal(saved.definitions.subgraphs[0].inputs[0].type, 'RECORD_PAIRS');
  assert.equal(saved.definitions.subgraphs[0].links[0].type, 'RECORD_CLUSTERS');
  assert.equal(currentType(undefined), undefined);
});
