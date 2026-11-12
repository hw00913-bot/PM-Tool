import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';

export const INPUT_FILES = ['graph.json', '完整流程.mmd', 'source-snapshot.json'];
export const OUTPUT_FILES = ['index.html', '完整流程.svg', 'verification.json'];
export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
export const escapeText = value => String(value).replace(/[&<>"']/g, c =>
  ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
export const inlineJSON = data => JSON.stringify(data).replace(/</g, '\\u003c');

export function readRuntimeVersions(runtime, pins) {
  const req = createRequire(path.join(path.resolve(runtime), 'package.json'));
  const versions = {};
  for (const [name, expected] of Object.entries(pins)) {
    // Read metadata via module search roots: ELK intentionally exports only ESM
    // and cannot be resolved with the CommonJS "require" condition.
    const metadata = (req.resolve.paths(name) || [])
      .map(base => path.join(base, name, 'package.json')).find(file => fs.existsSync(file));
    if (!metadata) throw Error('Missing local dependency: ' + name);
    const pkg = JSON.parse(fs.readFileSync(metadata, 'utf8'));
    if (pkg.name !== name || pkg.version !== expected)
      throw Error('Runtime version mismatch for ' + name + ': expected ' + expected + ', got ' + pkg.version);
    versions[name] = pkg.version;
  }
  return versions;
}

function regularFile(dir, name) {
  const target = path.join(dir, name);
  if (fs.lstatSync(target).isSymbolicLink() || !fs.statSync(target).isFile())
    throw Error('Candidate file must be a regular file: ' + name);
  return target;
}

export function loadBoundCandidate(input, output) {
  const dir = fs.realpathSync(path.dirname(path.resolve(input)));
  if (path.basename(input) !== 'graph.json' || fs.realpathSync(output) !== dir)
    throw Error('Render only inside the prepared candidate directory.');
  const manifest = JSON.parse(fs.readFileSync(regularFile(dir, 'candidate-manifest.json'), 'utf8'));
  if (manifest.contract !== 'mermaid-candidate@1' || manifest.formal_delivery !== false
      || !/^(SC|FLOW)-\d{3,}$/.test(manifest.diagram_id))
    throw Error('Missing candidate-only source binding; use render_mermaid_candidate.py.');
  if (!manifest.source_files || !Object.keys(manifest.source_files).length)
    throw Error('Candidate has no bound upstream source.');
  for (const [name, digest] of Object.entries(manifest.source_files)) {
    if (name.includes('\\') || path.posix.isAbsolute(name) || name.split('/').includes('..')
        || !/^[a-f0-9]{64}$/.test(digest))
      throw Error('Invalid source binding: ' + name);
  }
  if (!manifest.files || Object.keys(manifest.files).sort().join('|') !== INPUT_FILES.slice().sort().join('|'))
    throw Error('Candidate input manifest must bind graph, Mermaid and source snapshot.');
  for (const name of INPUT_FILES) {
    if (sha256(fs.readFileSync(regularFile(dir, name))) !== manifest.files[name])
      throw Error('Candidate changed after preparation: ' + name);
  }
  const data = JSON.parse(fs.readFileSync(path.join(dir, 'graph.json'), 'utf8'));
  if (!data.nodes?.length || !Array.isArray(data.edges) || !data.source_coverage
      || !data.nodes.every(n => /^[A-Za-z][A-Za-z0-9_]*$/.test(n.id) && n.mermaid_id === 'n_' + n.id))
    throw Error('Unnormalized candidate graph.');
  return {dir, manifest, data, mmd: fs.readFileSync(path.join(dir, '完整流程.mmd'), 'utf8')};
}

export function ensureFreshRender(dir) {
  for (const name of OUTPUT_FILES) {
    if (fs.existsSync(path.join(dir, name)))
      throw Error('Existing candidate render must not be overwritten; prepare a new directory: ' + name);
  }
}

export function recordRender(dir, manifest, report) {
  const target = path.join(dir, 'verification.json');
  if (fs.existsSync(target)) throw Error('Existing verification cannot be overwritten.');
  fs.writeFileSync(target, JSON.stringify(report, null, 2) + '\n', {flag:'wx'});
  const passed = report.status === 'passed' && report.opened_via === 'file://'
    && report.failed?.length === 0 && report.checks?.length > 0
    && report.checks.every(check => check.ok === true) && report.browser_errors?.length === 0;
  manifest.browser = {status: passed ? 'passed' : report.browser_started ? 'failed' : 'not_run'};
  manifest.render = {status: report.status};
  manifest.visual_review = 'not_performed';
  manifest.business_acceptance = 'not_claimed';
  manifest.rendered_files = {};
  for (const name of OUTPUT_FILES) {
    if (fs.existsSync(path.join(dir, name)))
      manifest.rendered_files[name] = sha256(fs.readFileSync(regularFile(dir, name)));
  }
  fs.writeFileSync(path.join(dir, 'candidate-manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
}

// Executed in the rendering page. No closure dependencies; fail closed before exporting SVG.
export function inspectSafeSvg() {
  const svg = document.querySelector('#graph svg');
  if (!svg) throw Error('SVG missing.');
  const allowed = new Set(['svg','g','defs','marker','path','rect','circle','ellipse','polygon',
    'polyline','line','text','tspan','title','desc','style','clippath','mask','lineargradient',
    'radialgradient','stop','foreignobject','div','span','p','br','b','strong','i','em']);
  const unsafeCSS = value => /@import|expression\s*\(|url\s*\(\s*['"]?(?!#)[^)\s'"]+/i.test(value);
  for (const el of [svg, ...svg.querySelectorAll('*')]) {
    if (!allowed.has(el.localName.toLowerCase())) throw Error('Unsupported SVG element: ' + el.localName);
    if (el.localName.toLowerCase() === 'style' && unsafeCSS(el.textContent))
      throw Error('SVG contains a non-local CSS resource.');
    for (const attr of el.attributes) {
      const name = attr.name.toLowerCase();
      if (name.startsWith('on') || name === 'src' || name === 'srcset'
          || (name.endsWith('href') && !attr.value.startsWith('#'))
          || unsafeCSS(attr.value))
        throw Error('SVG contains unsafe attribute: ' + name);
    }
  }
  return true;
}
