// The type standard (standards.py) in the frontend, as pure functions over the registry the server
// serves (/agent-workflow-studio/standards; loaded by standards.js). No ComfyUI imports, so Node
// tests use it as it is.
let registry = {types: [], socket_aliases: {}};

export const setRegistry = value => { registry = value ?? registry; };

/** The registered core type a promised API kind names (an alias included), or undefined. */
export const typeOfKind = kind => registry.types.find(t => t.scope === 'type' && (t.kind === kind || t.aliases.includes(kind)));

/** Old socket names → current ones, in a type string (comma unions included). */
export const currentType = type => (typeof type === 'string'
  ? type.split(',').map(part => registry.socket_aliases[part] ?? part).join(',')
  : type);

/** Every saved type in a workflow's JSON, rewritten in place to the current socket names: so a
 *  workflow saved before a rename shows today's types (ComfyUI runs it either way, checking links
 *  against the current node definitions). */
export function renameTypes(graph) {
  const rewrite = item => { if (item && 'type' in item) item.type = currentType(item.type); };
  for (const g of [graph, ...(graph?.definitions?.subgraphs ?? [])]) {
    for (const node of g?.nodes ?? []) [...(node.inputs ?? []), ...(node.outputs ?? [])].forEach(rewrite);
    for (const link of g?.links ?? []) {
      if (Array.isArray(link)) link[5] = currentType(link[5]);
      else rewrite(link);
    }
    [...(g?.inputs ?? []), ...(g?.outputs ?? [])].forEach(rewrite);
  }
  return graph;
}
