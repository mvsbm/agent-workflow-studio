// What a stage did, in a few lines read from its inner nodes' last run (no DOM, testable).
const number = text => Number(String(text).replace(/[^0-9.]/g, '')).toLocaleString();

/** The few numbers that tell what a stage did, read from its inner nodes' last run. */
export function stageSummary(inner) {
  const lines = [];
  // A graph of table steps: rows into its first step and out of its last (left to right), and how many steps.
  const steps = inner.filter(n => (n.properties?.aws_tables?.[0]?.rows ?? []).some(r => r[0] === 'rows out')).sort((a, b) => (a.pos?.[0] ?? 0) - (b.pos?.[0] ?? 0));
  if (steps.length > 1) {
    const fact = (node, key) => node.properties.aws_tables[0].rows.find(r => r[0] === key)?.[1];
    // Rows read by the stage's sources (steps with no rows in), or rows into its first step.
    const sources = steps.filter(n => fact(n, 'rows in') === '—');
    const total = sources.reduce((sum, n) => sum + Number(String(fact(n, 'rows out')).replace(/,/g, '')), 0);
    const into = sources.length ? total.toLocaleString() : fact(steps[0], 'rows in');
    lines.push(`${steps.length} steps · ${into} rows → ${fact(steps.at(-1), 'rows out')}`);
  }
  for (const node of inner) {
    const tables = node.properties?.aws_tables ?? [];
    const flow = node.properties?.aws_flow?.[0];
    const tree = node.properties?.aws_tree?.[0];
    const io = tables.find(t => t.title === 'Data in → out');
    if (io) {
      const side = dir => io.rows.filter(r => r[0] === dir).map(r => `${r[1]} ${r[2]}`).join(', ');
      if (side('in') || side('out')) lines.push(`${side('in') || '—'} → ${side('out') || '—'}`);
    } else if (flow) {
      const sum = {};
      for (const branch of flow.branches) sum[branch.decision] = (sum[branch.decision] ?? 0) + branch.count;
      lines.push(['match', 'review', 'different', 'undecided'].filter(d => sum[d]).map(d => `${d} ${sum[d].toLocaleString()}`).join(' · '));
    } else if (tree) {
      const customers = tree.children?.find(c => c.label.startsWith('Customers'));
      const merges = tree.children?.find(c => c.label === 'Merges');
      if (customers) lines.push(customers.label);
      const blocked = merges?.children?.find(c => c.label.startsWith('blocked'));
      if (blocked) lines.push(`${blocked.count.toLocaleString()} merge${blocked.count === 1 ? '' : 's'} blocked`);
    } else if (node.type === 'AgentWorkflowProfileData' && tables[0]?.title === 'Files') {
      const mixed = tables[0].rows.reduce((sum, r) => sum + Number(r[3] || 0), 0);
      lines.push(`${tables[0].rows.length} files profiled · ${mixed} mixed-format columns`);
    } else if (node.type === 'AgentWorkflowReviewDecisions' && tables[0]?.rows) {
      const get = key => tables[0].rows.find(r => r[0] === key)?.[1] ?? '0';
      lines.push(`you: ${get('you decided: same')} same · ${get('you decided: different')} different · ${String(get('still open')).split(' ')[0]} open`);
    } else if (node.type === 'AgentWorkflowLearnWeights') {
      const share = tables[0]?.rows?.find(r => r[0].startsWith('share that match'));
      if (share) lines.push(`learned: ${share[1]} of pairs match`);
    } else if (tables[0]?.rows?.length && node.type?.startsWith('AgentWorkflowNayose')) {
      const rows = tables[0].rows.find(r => ['rows', 'people'].includes(r[0]));
      const file = tables[0].rows.find(r => r[0] === 'file');
      if (rows) lines.push(`${file ? file[1].split('/').pop() : rows[0]} ${number(rows[1])}`);
    }
  }
  return lines.filter(Boolean).slice(0, 4);
}

