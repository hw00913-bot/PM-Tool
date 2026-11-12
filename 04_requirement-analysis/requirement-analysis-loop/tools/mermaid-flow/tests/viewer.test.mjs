// Pure Node DOM simulation only. This does not certify browser layout or SVG geometry.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { runInNewContext } from 'node:vm';

const html = readFileSync(new URL('../assets/viewer.html', import.meta.url), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const attack = '\"><img src=https://invalid.example/x onerror=alert(1)><script>alert(2)</script>&\'';
const cssEscape = value => String(value).replace(/[^a-zA-Z0-9_-]/g, c => '\\' + c.codePointAt(0).toString(16) + ' ');

class ClassList {
  constructor() { this.values = new Set(); }
  add(value) { this.values.add(value); }
  remove(value) { this.values.delete(value); }
  contains(value) { return this.values.has(value); }
  toggle(value, enabled = !this.contains(value)) { enabled ? this.add(value) : this.remove(value); }
}

class Element {
  constructor(id = '') {
    this.id = id; this.textContent = ''; this.innerHTML = ''; this.style = {}; this.dataset = {};
    this.classList = new ClassList(); this.attributes = {}; this.events = new Map();
    this.hidden = false; this.clientWidth = 1600; this.clientHeight = 900; this.offsetWidth = 360;
    this.width = 268; this.height = 368; this.value = '';
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, fn) { this.events.set(name, fn); }
  dispatch(name, args = {}) { return this.events.get(name)?.({ preventDefault() {}, target: this, ...args }); }
  click(args = {}) { return this.onclick?.({ preventDefault() {}, target: this, ...args }); }
  getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth, height: this.clientHeight }; }
  setPointerCapture() {}
  releasePointerCapture() {}
  closest() { return null; }
}

function fixture() {
  return {
    title: attack, page_id: attack, start: 'n1',
    nodes: [
      { id: 'n1', kind: 'process', text: '完整步骤\n第二行', phase: 'p1', x: attack, y: attack,
        source_ids: ['source-1', attack], source_texts: ['单份完整原文 ' + attack],
        original_id: attack, original_label: '来源标签 ' + attack,
        source_lane_name: '业务责任方 ' + attack, source_refs: [{ id: 'SC-001', detail: attack }],
        jumps: [attack], unconnected: true },
      { id: 'n2', kind: 'note', text: '完整旁注 ' + attack, note_no: attack, anchor: 'n1',
        x: 2, y: 3, source_ids: [], source_texts: [], jumps: [] },
      { id: attack, kind: attack, text: attack, phase: attack, x: 0, y: 0,
        source_ids: [], source_texts: [], jumps: [], anchor: attack },
    ],
    edges: [{ id: 'e1', source: 'n1', target: attack, kind: 'flow', text: '分支原文 ' + attack }],
    phases: [{ id: attack, entry: attack, title: attack }],
    jumps: [{ source: 'n1', target: null }],
    issues: [{ ids: [attack], detail: attack }], revision_notes: [{ text: attack }],
  };
}

function boot({ width = 2400, height = 3600, data = fixture() } = {}) {
  const ids = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element(match[1])]));
  ids.get('directory').hidden = true;
  ids.get('graph-data').textContent = JSON.stringify(data);
  const nodeElements = new Map();
  for (const node of data.nodes) {
    const element = new Element('svg-' + node.id);
    element.dataset.nodeId = node.id;
    element.getBBox = () => ({ x: 20, y: 30, width: 240, height: 100 });
    element.getCTM = () => ({ e: 0, f: 0 });
    nodeElements.set(node.id, element);
  }
  const edges = new Map(data.edges.map(edge => [edge.id, new Element('full-flow-' + edge.id)]));
  const svg = new Element('svg-root');
  svg.viewBox = { baseVal: { x: 0, y: 0, width, height } };
  svg.getCTM = () => ({ inverse: () => ({ multiply: matrix => matrix }) });
  svg.querySelector = selector => {
    for (const [id, element] of nodeElements) if (selector === '[data-node-id="' + cssEscape(id) + '"]') return element;
    for (const [id, element] of edges) if (selector === '[id="full-flow-' + cssEscape(id) + '"]') return element;
    return null;
  };
  ids.get('diagram').querySelector = selector => selector === 'svg' ? svg : null;
  const drawing = [];
  ids.get('minimap').getContext = () => new Proxy({}, { get: (_, key) => (...args) => drawing.push([key, ...args]) });
  const documentEvents = new Map();
  const document = {
    fullscreenElement: null,
    getElementById: id => ids.get(id),
    querySelectorAll: () => [],
    elementFromPoint: () => null,
    addEventListener: (name, fn) => documentEvents.set(name, fn),
    async exitFullscreen() { document.fullscreenElement = null; documentEvents.get('fullscreenchange')?.(); },
  };
  ids.get('viewer-panel').requestFullscreen = async () => {
    document.fullscreenElement = ids.get('viewer-panel'); documentEvents.get('fullscreenchange')?.();
  };
  const window = {};
  runInNewContext(script, {
    document, window, CSS: { escape: cssEscape },
    ResizeObserver: class { observe() {} },
    requestAnimationFrame: fn => fn(), setTimeout: () => 1, clearTimeout() {},
  }, { timeout: 1000 });
  return { ids, viewer: window.flowViewer, document, documentEvents, drawing, nodeElements, svg };
}

function noRawInjection(value) {
  assert.ok(!value.includes('<img'), 'User text must not create an image element');
  assert.ok(!value.includes('<script>'), 'User text must not create a script element');
  assert.ok(value.includes('&lt;img'), 'User text must remain visible as escaped text');
}

test('local-only CSP matches the exact inline script and retains four template placeholders', () => {
  const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  const digest = createHash('sha256').update(script).digest('base64');
  assert.ok(csp.includes("script-src 'sha256-" + digest + "'"));
  assert.ok(!/script-src[^;]*unsafe-inline/.test(csp));
  for (const directive of ['connect-src', 'object-src', 'frame-src', 'worker-src', 'media-src', 'font-src', 'base-uri', 'form-action']) {
    assert.ok(csp.includes(directive + " 'none'"));
  }
  assert.ok(csp.includes('img-src data:'));
  for (const token of ['@@TITLE@@', '@@STATS@@', '@@SVG@@', '@@DATA@@']) assert.ok(html.includes(token));
  assert.match(html, /href="完整流程\.mmd" download/);
  assert.match(html, /href="完整流程\.svg" download/);
  assert.doesNotMatch(html, /<(?:script|iframe|img)\b[^>]+src=/i);
  assert.doesNotMatch(html, /<form\b/i);
});

test('desktop full-width candidate surface defaults to closed overlay directory', () => {
  assert.match(html, /候选评审图 \/ 非正式泳道 \/ 未完成视觉与业务确认/);
  assert.match(html, /<aside id="directory" hidden>/);
  assert.match(html, /\.layout\{flex:1;[^}]*grid-template-columns:minmax\(0,1fr\)/);
  assert.doesNotMatch(html, /@media/);
  const { ids, viewer } = boot();
  assert.equal(viewer.getState().directoryOpen, false);
  ids.get('directory-toggle').click();
  assert.equal(viewer.getState().directoryOpen, true);
  assert.equal(ids.get('directory-toggle').attributes['aria-expanded'], 'true');
  ids.get('directory-toggle').click();
  assert.equal(viewer.getState().directoryOpen, false);
});

test('wide diagrams keep a readable natural scale and support width, actual, and whole-chart modes', () => {
  const { viewer, ids, svg } = boot();
  assert.equal(viewer.getState().zoom, 1);
  assert.equal(svg.attributes.width, '2400');
  assert.equal(svg.attributes.height, '3600');
  viewer.fitWidth();
  assert.equal(viewer.getState().zoom, (1600 - 24) / 2400);
  assert.equal(viewer.getState().ty, 12);
  viewer.fitAll();
  assert.equal(viewer.getState().zoom, (900 - 24) / 3600);
  ids.get('actual-size').click();
  assert.equal(viewer.getState().zoom, 1);
  ids.get('zoom-in').click();
  assert.equal(viewer.getState().zoom, 1.25);
  viewer.readableSize();
  assert.equal(viewer.getState().zoom, 1);
  assert.equal(boot({ width: 700, height: 1200 }).viewer.getState().zoom, 1.25);
});

test('all dynamic node, note, source-coordinate, phase and edge fields are escaped without losing source text', () => {
  const { viewer, ids } = boot();
  noRawInjection(ids.get('phases').innerHTML);
  viewer.showNode('n1');
  const content = ids.get('detail-content').innerHTML;
  noRawInjection(content);
  for (const label of ['来源泳道', '来源 ID', '来源引用', '来源节点原文', '单份完整原文', '完整步骤', 'SC-001']) assert.ok(content.includes(label));
  assert.ok(!content.includes('data-target="' + attack));
  viewer.showNode('n2');
  assert.equal(ids.get('detail-title').textContent, attack + ' · 旁注原文');
  noRawInjection(ids.get('detail-content').innerHTML);
  viewer.showNode(attack);
  noRawInjection(ids.get('detail-content').innerHTML);
  viewer.showEdge('e1');
  noRawInjection(ids.get('detail-content').innerHTML);
  assert.equal(viewer.data.nodes[0].source_texts[0], '单份完整原文 ' + attack);
});

test('search includes complete source texts and provenance without navigation or network access', () => {
  const { ids, viewer } = boot();
  ids.get('search-input').value = '单份完整原文';
  ids.get('search-submit').click();
  assert.equal(ids.get('search-result').textContent, '1 / 1');
  assert.equal(viewer.getState().detailOpen, true);
  ids.get('search-input').value = 'SC-001';
  ids.get('search-input').dispatch('keydown', { key: 'Enter' });
  assert.equal(ids.get('search-result').textContent, '1 / 1');
  ids.get('show-info').click();
  noRawInjection(ids.get('modal-content').innerHTML);
  assert.match(ids.get('modal-content').innerHTML, /不签发门禁结论/);
  ids.get('show-history').click();
  noRawInjection(ids.get('modal-content').innerHTML);
});

test('fullscreen uses the complete page containing notice and toolbar, and can exit', async () => {
  const { ids, document } = boot();
  await ids.get('fullscreen').click();
  assert.equal(document.fullscreenElement, ids.get('viewer-panel'));
  assert.equal(ids.get('fullscreen').attributes['aria-pressed'], 'true');
  assert.match(html, /<body id="viewer-panel">[\s\S]*review-notice[\s\S]*class="toolbar"/);
  await ids.get('fullscreen').click();
  assert.equal(document.fullscreenElement, null);
  assert.equal(ids.get('fullscreen').attributes['aria-pressed'], 'false');
});

test('drag, wheel zoom, keyboard panning and minimap handlers remain active', () => {
  const { ids, viewer, drawing } = boot();
  const viewport = ids.get('viewport'), before = viewer.getState();
  viewport.dispatch('pointerdown', { button: 0, pointerId: 1, clientX: 20, clientY: 30 });
  viewport.dispatch('pointermove', { pointerId: 1, clientX: 120, clientY: 130 });
  assert.equal(viewer.getState().tx, before.tx + 100);
  viewport.dispatch('pointerup', { pointerId: 1, clientX: 120, clientY: 130 });
  const zoom = viewer.getState().zoom;
  viewport.dispatch('wheel', { ctrlKey: true, metaKey: false, deltaY: -40, deltaX: 0, clientX: 80, clientY: 80 });
  assert.ok(viewer.getState().zoom > zoom);
  const x = viewer.getState().tx;
  viewport.dispatch('keydown', { key: 'ArrowLeft' });
  assert.equal(viewer.getState().tx, x + 100);
  ids.get('minimap').click({ clientX: 100, clientY: 100 });
  assert.ok(Number.isFinite(viewer.getState().tx));
  assert.ok(drawing.some(([name]) => name === 'strokeRect'));
});

test('optional fields may be absent and unknown jump targets remain readable', () => {
  const data = { title: '最小图', start: 'a', nodes: [{ id: 'a', text: '保留原文', jumps: ['missing'] }], edges: [] };
  const { viewer, ids } = boot({ data });
  viewer.showNode('a');
  assert.match(ids.get('detail-content').innerHTML, /missing/);
  assert.equal(ids.get('show-history').hidden, true);
});
