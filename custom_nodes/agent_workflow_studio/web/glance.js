// The workflow at a glance: each wire is labelled with what flows along it ("5,480 rows",
// "1,204 matched"), and when zoomed out every node shows one large line instead of its tables.
// Labels and headlines come from the node's last run (properties aws_wires / aws_headline, set by table_view.js).
import {app} from '../../scripts/app.js';

// Below this zoom in-node tables are unreadable (ComfyUI's own low-quality threshold varies by setting).
const ZOOMED_OUT = 0.6;

/** The label for a node's output slot; a stage card (subgraph) shows what its inner node sends out. */
export function wireLabel(graph, id, slot, depth = 0) {
  const node = graph?.getNodeById?.(id);
  if (!node || depth > 4) return '';
  if (node.isSubgraphNode?.()) {
    const inner = node.subgraph.outputs?.[slot]?.linkIds?.[0];
    const link = inner == null ? null : node.subgraph.links.get?.(inner) ?? node.subgraph.links[inner];
    return link ? wireLabel(node.subgraph, link.origin_id, link.origin_slot, depth + 1) : '';
  }
  return node.properties?.aws_wires?.[slot] ?? '';
}

/** One line for the zoomed-out view: the node's headline, else its first summary line. */
export function headline(node) {
  if (node.isSubgraphNode?.()) return node.properties?.aws_card?.lines?.[0] ?? '';
  return node.properties?.aws_headline ?? '';
}

function pill(ctx, text, x, y, scale) {
  const size = 11 / scale;
  ctx.font = `${size}px sans-serif`;
  const width = ctx.measureText(text).width + size, height = size * 1.6;
  ctx.fillStyle = 'rgba(18, 22, 28, 0.92)';
  ctx.beginPath();
  ctx.roundRect(x - width / 2, y - height / 2, width, height, height / 2);
  ctx.fill();
  ctx.fillStyle = '#e6edf5';
  ctx.fillText(text, x, y);
}

// Zoomed out, a node's tables give way to its title and headline, in its own HTML so it shows
// in both the classic canvas and Vue nodes mode (where nodes are DOM above the canvas).
function showGlance(node) {
  const host = node.aws_card ?? node.widgets?.find(w => w.name === 'table_view')?.element;
  if (!host) return;
  let glance = host.querySelector(':scope > .aw-glance');
  if (!glance) host.prepend(glance = Object.assign(document.createElement('div'), {className: 'aw-glance'}));
  const title = node.title ?? '', line = headline(node);
  if (glance.dataset.text === title + line) return;
  glance.dataset.text = title + line;
  glance.replaceChildren(Object.assign(document.createElement('b'), {textContent: title}), Object.assign(document.createElement('span'), {textContent: line}));
}

// Classic canvas, low quality: ComfyUI hides DOM widgets, so draw the same on the canvas.
function wrap(ctx, text, width) {
  const lines = [];
  let line = '';
  // Japanese has no spaces: break between any characters when a word does not fit.
  for (const word of text.split(/(?<=\s)|(?=·)/)) {
    for (const piece of ctx.measureText(word).width > width ? [...word] : [word]) {
      if (line && ctx.measureText(line + piece).width > width) {
        lines.push(line.trim());
        line = '';
      }
      line += piece;
    }
  }
  return [...lines, line.trim()].filter(Boolean);
}

function drawGlance(ctx, node, scale) {
  if (node.collapsed) return;
  const title = node.title ?? '', text = headline(node);
  const [x, y] = node.pos, [w, h] = node.size, pad = 10 / scale;
  ctx.fillStyle = node.bgcolor ?? '#353535';
  ctx.fillRect(x, y, w, h);
  let size = 13 / scale, titles, lines;
  // Shrink until it fits the node, but never below what is readable on screen.
  for (;;) {
    ctx.font = `bold ${size}px sans-serif`;
    titles = wrap(ctx, title, w - 2 * pad);
    ctx.font = `${size}px sans-serif`;
    lines = text ? wrap(ctx, text, w - 2 * pad) : [];
    if ((titles.length + lines.length) * size * 1.25 + size * 0.5 <= h - 2 * pad || size <= 9 / scale) break;
    size *= 0.9;
  }
  ctx.textAlign = 'left';
  ctx.textBaseline = 'top';
  ctx.fillStyle = '#ffffff';
  ctx.font = `bold ${size}px sans-serif`;
  titles.forEach((line, i) => ctx.fillText(line, x + pad, y + pad + i * size * 1.25));
  ctx.fillStyle = '#d6dee8';
  ctx.font = `${size}px sans-serif`;
  const top = y + pad + titles.length * size * 1.25 + size * 0.5;
  lines.forEach((line, i) => ctx.fillText(line, x + pad, top + i * size * 1.25));
}

app.registerExtension({
  name: 'AgentWorkflowStudio.Glance',
  setup() {
    const style = document.createElement('style');
    // --aw-glance is 1/zoom, so the headline keeps a readable size on screen.
    style.textContent = `
      .aw-glance { display: none; }
      .aw-zoomed-out :is(.aw-table-view, .aw-card) > :not(.aw-glance) { display: none; }
      .aw-zoomed-out .aw-glance { display: flex; flex-direction: column; gap: 0.3em; font-size: calc(13px * var(--aw-glance, 1)); line-height: 1.2; white-space: normal; overflow: hidden; }`;
    document.head.append(style);
    const canvas = app.canvas, previous = canvas.onDrawForeground;
    canvas.onDrawForeground = function (ctx, area) {
      previous?.call(this, ctx, area);
      const scale = this.ds.scale, graph = this.graph, zoomedOut = scale < ZOOMED_OUT;
      document.body.classList.toggle('aw-zoomed-out', zoomedOut);
      if (zoomedOut) {
        document.body.style.setProperty('--aw-glance', (1 / scale).toFixed(2));
        for (const node of graph?.nodes ?? []) showGlance(node);
      }
      ctx.save();
      // Covered by the DOM nodes in Vue nodes mode, where the HTML headline shows instead.
      if (this.low_quality) for (const node of graph?.nodes ?? []) drawGlance(ctx, node, scale);
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const link of this.renderedPaths ?? []) {
        const text = link._pos && wireLabel(graph, link.origin_id, link.origin_slot);
        if (text) pill(ctx, text, link._pos[0], link._pos[1] - 14 / scale, scale);
      }
      ctx.restore();
    };
  },
});
