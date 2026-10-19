// The design page: one shareable document generated from the workflow itself — the high-level
// design (one sentence per stage), the data entities, each stage's low-level design (its nodes
// and what they did on the last run), decisions, status, acceptance, the improve loop and the API.
// Nothing is written by hand, so it cannot disagree with the canvas.

const line = text => String(text ?? '').replace(/\s+/g, ' ').trim();

export async function designPage(app, api) {
  const graph = app.rootGraph ?? app.graph;
  const base = `/agent-workflow-studio/projects/${encodeURIComponent(graph.id)}`;
  const get = async path => { const r = await api.fetchApi(path); return r.ok ? r.json() : {}; };
  const [{contract, design = [], review = []}, {journal = []}, {apis = []}] = await Promise.all([get(`${base}/contract`), get(`${base}/loop`), get('/agent-workflow-studio/api')]);
  const out = [`# ${line(contract?.purpose ?? graph.extra?.title ?? 'Workflow')}`, ''];
  if (contract) {
    out.push('## What was asked', ...contract.requests.map(r => `- **${r.id}** (${r.priority}) “${line(r.you_said)}” — ${line(r.we_understood)}`), '');
    out.push('## High-level design', ...design.map(d => `- **${d.title}**: ${line(d.sentence)}${d.pattern ? ` *(${d.pattern})*` : ''}`), '');
    if (contract.data && Object.keys(contract.data).length) {
      out.push('## Data', ...Object.entries(contract.data).map(([name, e]) => `- **${name}**${e.key?.length ? ` (key ${e.key.join(', ')})` : ''}: ${line(e.description)}${e.fields?.length ? ` — fields ${e.fields.join(', ')}` : ''}${Object.entries(e.refs ?? {}).map(([f, t]) => ` · ${f} → ${t}`).join('')}`), '');
    }
  }
  out.push('## Low-level design (each stage)');
  for (const stage of graph.nodes.filter(n => n.properties?.aws_stage)) {
    out.push('', `### ${stage.title}`, ...(stage.properties.aws_design ? [line(stage.properties.aws_design), ''] : []));
    for (const node of stage.subgraph.nodes) {
      const did = node.properties?.aws_headline ? ` — ${line(node.properties.aws_headline)}` : '';
      out.push(`- ${line(node.title)} \`${node.type}\`${did}`);
    }
  }
  if (contract?.decisions?.length) {
    out.push('', '## Decisions', ...contract.decisions.map(d => `- **${d.id}** ${line(d.decision)} — because ${line(d.why)}${d.instead_of ? ` (instead of ${line(d.instead_of)})` : ''}${d.pattern ? ` · pattern: ${d.pattern}` : ''}${d.principle ? ` · principle: ${d.principle}` : ''} · by ${d.by === 'user' ? 'the user' : 'the agent'}`));
  }
  if (review.length) out.push('', '## Design review', ...review.map(r => `- ${r.ok ? '✓' : '✗'} ${r.item}`));
  const verified = graph.extra?.aws_verified;
  if (verified) out.push('', '## Status', `Verified on the run of ${verified.at} (fingerprint ${verified.fingerprint}):`, ...Object.entries(verified.gates).map(([step, status]) => `- ${step}: ${status}`));
  if (journal.length) out.push('', '## Improve loop', ...journal.map(j => `- #${j.n} **${j.verdict}**: ${line(j.hypothesis)} — ${line(j.change)}`));
  const mine = apis.filter(a => a.workflow_id === graph.id);
  if (mine.length) out.push('', '## API', ...mine.map(a => `- \`POST /agent-workflow-studio/api/${a.name}/run\` · version ${a.semver ?? ''} (${String(a.version).slice(0, 8)}) · inputs ${a.inputs.join(', ') || 'none'} · outputs ${a.outputs.join(', ')}`));
  return out.join('\n') + '\n';
}
