



































// FieldData AI - small SVG charts (no library, nothing loaded from the internet).
// Every chart has a text summary for screen readers and a tooltip (<title>) on each bar or point.
const NS = 'http://www.w3.org/2000/svg';
 
function svgEl(tag, attrs, text) {
  const e = document.createElementNS(NS, tag);
  Object.keys(attrs || {}).forEach(k => e.setAttribute(k, attrs[k]));
  if (text !== undefined) e.textContent = text;
  return e;
}
 
function tooltip(node, text) { node.append(svgEl('title', {}, text)); return node; }
function clip(s, n) { s = String(s); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
  return ([1, 1.5, 2, 3, 5, 7.5, 10].find(x => f <= x) || 10) * p;
}
 
function chartFigure(title, subtitle, svg, summary) {
  const fig = document.createElement('figure');
  fig.className = 'chart card shadow-sm mb-3';
  const body = document.createElement('div');
  body.className = 'card-body';
  const h = document.createElement('h3'); h.className = 'h6 mb-1'; h.textContent = title;
  body.append(h);
  if (subtitle) { const p = document.createElement('p'); p.className = 'small text-muted mb-2'; p.textContent = subtitle; body.append(p); }
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', summary || title);
  body.append(svg);
  fig.append(body);
  return fig;
}
 
// horizontal bars: data = [[label, value], ...]
function hbarChart(title, subtitle, data, opts = {}) {
  const rowH = 26, left = 190, right = 70, W = 640, H = Math.max(data.length, 1) * rowH + 12;
  const max = opts.max || Math.max(...data.map(d => d[1]), 1);
  const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, class: 'chart-svg'});
  data.forEach((d, i) => {
    const y = 6 + i * rowH, w = Math.max(0, (W - left - right) * d[1] / max);
    const label = svgEl('text', {x: left - 8, y: y + 15, 'text-anchor': 'end', class: 'chart-label'}, clip(d[0], 28));
    tooltip(label, String(d[0]));
    svg.append(label);
    svg.append(tooltip(svgEl('rect', {x: left, y: y + 2, width: w.toFixed(1), height: rowH - 8, rx: 3, class: 'chart-bar ' + (opts.warn ? 'warn' : '')}),
      `${d[0]}: ${d[1]}${opts.suffix || ''}`));
    svg.append(svgEl('text', {x: left + w + 6, y: y + 15, class: 'chart-value'}, `${d[1]}${opts.suffix || ''}`));
  });
  const top = data.slice(0, 3).map(d => `${d[0]} ${d[1]}${opts.suffix || ''}`).join(', ');
  return chartFigure(title, subtitle, svg, `${title}. Bar chart with ${data.length} bars. Highest: ${top}.`);
}
 
function axes(svg, W, H, L, R, T, B, max, ticks = 4) {
  for (let i = 0; i <= ticks; i++) {
    const v = max * i / ticks, y = T + (H - T - B) * (1 - i / ticks);
    svg.append(svgEl('line', {x1: L, x2: W - R, y1: y, y2: y, class: 'chart-grid'}));
    svg.append(svgEl('text', {x: L - 6, y: y + 4, 'text-anchor': 'end', class: 'chart-label'}, Number.isInteger(v) ? v : v.toFixed(1)));
  }
}
 
// vertical bars (histograms and small categories): data = [[label, value], ...]
function columnChart(title, subtitle, data) {
  const W = 640, H = 250, L = 44, R = 12, T = 12, B = 46;
  const max = niceMax(Math.max(...data.map(d => d[1]), 1));
  const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, class: 'chart-svg'});
  axes(svg, W, H, L, R, T, B, max);
  const slot = (W - L - R) / Math.max(data.length, 1), bw = slot * 0.7;
  data.forEach((d, i) => {
    const h = (H - T - B) * d[1] / max, x = L + i * slot + (slot - bw) / 2;
    svg.append(tooltip(svgEl('rect', {x: x.toFixed(1), y: (H - B - h).toFixed(1), width: bw.toFixed(1), height: h.toFixed(1), rx: 2, class: 'chart-bar'}), `${d[0]}: ${d[1]}`));
    if (data.length <= 14 || i % Math.ceil(data.length / 12) === 0) {
      svg.append(svgEl('text', {x: (L + i * slot + slot / 2).toFixed(1), y: H - B + 16, 'text-anchor': 'middle', class: 'chart-label'}, clip(d[0], 9)));
    }
  });
  return chartFigure(title, subtitle, svg, `${title}. Column chart with ${data.length} columns.`);
}
 
// line chart over time: points = [[label, value], ...]
function lineChart(title, subtitle, points) {
  const W = 640, H = 250, L = 44, R = 14, T = 12, B = 40;
  const max = niceMax(Math.max(...points.map(p => p[1]), 1));
  const svg = svgEl('svg', {viewBox: `0 0 ${W} ${H}`, class: 'chart-svg'});
  axes(svg, W, H, L, R, T, B, max);
  const n = points.length, x = i => L + (n === 1 ? (W - L - R) / 2 : i * (W - L - R) / (n - 1)), y = v => T + (H - T - B) * (1 - v / max);
  svg.append(svgEl('path', {d: points.map((p, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(p[1]).toFixed(1)).join(' '), class: 'chart-line', fill: 'none'}));
  if (n <= 90) points.forEach((p, i) => svg.append(tooltip(svgEl('circle', {cx: x(i).toFixed(1), cy: y(p[1]).toFixed(1), r: 3, class: 'chart-dot'}), `${p[0]}: ${p[1]}`)));
  [0, Math.floor((n - 1) / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i).forEach(i =>
    svg.append(svgEl('text', {x: x(i).toFixed(1), y: H - B + 18, 'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle', class: 'chart-label'}, points[i][0])));
  return chartFigure(title, subtitle, svg, `${title}. Line chart from ${points[0][0]} to ${points[n - 1][0]}, highest value ${Math.max(...points.map(p => p[1]))}.`);
}
 
