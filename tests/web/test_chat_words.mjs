// Verifies: R40
// The chat speaks the user's words: tools by what they do, the end of a reply by what happened.
import assert from 'node:assert/strict';
import test from 'node:test';
import {finishedText, toolPhrase} from '../../custom_nodes/agent_workflow_studio/web/chat_words.js';
import {setSignature} from '../../custom_nodes/agent_workflow_studio/web/publish.js';

test('tools read as what they do, also through MCP and when unknown', () => {
  assert.equal(toolPhrase('comfy_build_step'), 'Building a step');
  assert.equal(toolPhrase('mcp__studio__comfy_ask_user'), 'Asking you');
  assert.equal(toolPhrase('comfy_new_thing'), 'New thing');
});

test('a finished reply says what happened to the workflow', () => {
  assert.equal(finishedText({}), 'Done');
  assert.equal(finishedText({completed: true}), 'Done · the workflow ran');
  assert.match(finishedText({failed: true, completed: true}), /failed/);
});

test("App mode labels inputs in the contract's words, and keeps a label set before", () => {
  const graph = {extra: {}};
  setSignature(graph, [{node: 1, widget: 'folder', description: 'Your orders CSV'}, {node: 1, widget: 'upload'}], [2]);
  assert.deepEqual(graph.extra.linearData, {inputs: [[1, 'folder', {description: 'Your orders CSV'}], [1, 'upload']], outputs: [2]});
  graph.extra.linearData.inputs[0][2] = {description: 'Edited in App mode'};
  setSignature(graph, [{node: 1, widget: 'folder', description: 'Your orders CSV'}], [2]);
  assert.deepEqual(graph.extra.linearData.inputs, [[1, 'folder', {description: 'Edited in App mode'}]]);
});

test("a Data folder's Upload button follows its folder into App mode", () => {
  const graph = {extra: {}, getNodeById: id => (id === 5 ? {type: 'AgentWorkflowDataFolder'} : null)};
  setSignature(graph, [{node: 5, widget: 'folder', description: 'Your orders'}], [2]);
  assert.deepEqual(graph.extra.linearData.inputs, [[5, 'folder', {description: 'Your orders'}], [5, 'upload', {description: 'Upload: your orders'}]]);
});
