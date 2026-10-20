import assert from 'node:assert/strict';
import test from 'node:test';
import {arrangeGraph} from '../../custom_nodes/agent_workflow_studio/web/pi_canvas.js';

const node = (id, type = 'X', size = [200, 100]) => ({id, type, pos: [500, 500], size});

test('flows left to right by depth, previews under their source, notes above', () => {
  const [source, tool, preview, sink, note] = [node(1), node(2, 'Tool', [300, 150]), node(3, 'PreviewAny'), node(4), node(5, 'MarkdownNote', [400, 80])];
  const graph = {nodes: [source, tool, preview, sink, note], links: new Map([
    [1, {origin_id: 1, target_id: 2}], [2, {origin_id: 2, target_id: 3}], [3, {origin_id: 2, target_id: 4}], [4, {origin_id: 1, target_id: 4}],
  ])};
  arrangeGraph(graph);
  assert.deepEqual(source.pos, [500, 500]);
  assert.deepEqual(tool.pos, [500 + 200 + 200, 500]);
  assert.deepEqual(preview.pos, [900, 500 + 150 + 110], 'a preview sits under the node it shows');
  assert.deepEqual(sink.pos, [900 + 300 + 200, 500], 'a node reading two nodes gets its own column');
  assert.deepEqual(note.pos, [500, 500 - 80 - 110]);
});

test('feedback loops do not hang', () => {
  const [a, b] = [node(1), node(2)];
  arrangeGraph({nodes: [a, b], links: new Map([[1, {origin_id: 1, target_id: 2}], [2, {origin_id: 2, target_id: 1}]])});
  assert.ok(Number.isFinite(a.pos[0]) && Number.isFinite(b.pos[0]));
});

test('given nodes come first in their column and never hang under a stage', () => {
  const data = {...node(1), properties: {aws_given: true}};
  const tool = node(2);
  const score = {...node(3), properties: {aws_given: true}};
  tool.pos = [0, 0]; // above the data node before arranging
  const graph = {nodes: [data, tool, score], links: new Map([[1, {origin_id: 2, target_id: 3}]])};
  arrangeGraph(graph);
  assert.ok(data.pos[1] < tool.pos[1], 'the given source sits on top of its column');
  assert.ok(score.pos[0] > tool.pos[0], 'the given scorer gets its own column');
});

test('a stage holding an output node counts as an output (it runs)', async () => {
  const {withStageOutputs} = await import('../../custom_nodes/agent_workflow_studio/web/pi_canvas.js');
  const inner = {id: 1, type: 'PreviewAny', pos: [0, 0], size: [1, 1]};
  const stage = {id: 9, type: 'uuid-stage', isSubgraphNode: () => true, subgraph: {nodes: [inner], links: new Map()}};
  const defs = withStageOutputs({nodes: [stage], links: new Map()}, {PreviewAny: {output_node: true}});
  assert.equal(defs['uuid-stage'].output_node, true);
});

test('stages with the same step number are parallel lanes in one column', () => {
  const stage = (id, order) => ({id, type: `s${id}`, pos: [0, 0], size: [200, 100], isSubgraphNode: () => true, properties: {aws_stage: `s${id}`, aws_stage_order: order}});
  const [a, b, c, d] = [stage(1, 1), stage(2, 2), stage(3, 2), stage(4, 3)];
  arrangeGraph({nodes: [a, b, c, d], links: new Map()});
  assert.equal(b.pos[0], c.pos[0], 'lanes share a column');
  assert.notEqual(b.pos[1], c.pos[1], 'stacked');
  assert.ok(d.pos[0] > b.pos[0]);
});
