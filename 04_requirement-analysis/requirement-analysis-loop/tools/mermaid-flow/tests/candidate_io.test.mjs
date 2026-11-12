import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { loadBoundCandidate, ensureFreshRender, recordRender, inspectSafeSvg, escapeText, inlineJSON, sha256, readRuntimeVersions } from '../scripts/candidate_io.mjs';

function fixture(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(),'mermaid-io-test-'));
  try {
    const files = {'graph.json':JSON.stringify({nodes:[{id:'N1',mermaid_id:'n_N1'}],edges:[],source_coverage:{status:'passed'}}),
      '完整流程.mmd':'flowchart TB\n n_N1["示例"]\n','source-snapshot.json':'{}'};
    const manifest={contract:'mermaid-candidate@1',diagram_id:'SC-001',formal_delivery:false,
      source_files:{'scenarios/SC-001.json':sha256('{}')},files:{}};
    for(const [name,content] of Object.entries(files)){
      fs.writeFileSync(path.join(dir,name),content);
      manifest.files[name]=sha256(content);
    }
    fs.writeFileSync(path.join(dir,'candidate-manifest.json'),JSON.stringify(manifest));
    return run(dir,manifest);
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
}

test('bound inputs can be checked without a browser or renderer dependencies',()=>fixture(dir=>{
  assert.equal(loadBoundCandidate(path.join(dir,'graph.json'),dir).manifest.diagram_id,'SC-001');
  const script=fileURLToPath(new URL('../scripts/render.mjs',import.meta.url));
  const result=spawnSync(process.execPath,[script,'--input',path.join(dir,'graph.json'),'--out',dir,'--check-input'],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  assert.equal(JSON.parse(result.stdout).browser,'not_run');
  assert.equal(fs.existsSync(path.join(dir,'verification.json')),false);
}));

test('changed Mermaid or graph cannot be rendered with stale input binding',()=>fixture(dir=>{
  fs.appendFileSync(path.join(dir,'完整流程.mmd'),'newnode');
  assert.throws(()=>loadBoundCandidate(path.join(dir,'graph.json'),dir),/changed/);
}));

test('candidate render cannot overwrite existing output or follow artifact symlinks',()=>fixture(dir=>{
  fs.writeFileSync(path.join(dir,'index.html'),'existing');
  assert.throws(()=>ensureFreshRender(dir),/must not be overwritten/);
  fs.unlinkSync(path.join(dir,'graph.json'));
  fs.symlinkSync(path.join(dir,'source-snapshot.json'),path.join(dir,'graph.json'));
  assert.throws(()=>loadBoundCandidate(path.join(dir,'graph.json'),dir),/regular file/);
}));

test('failed interaction checks cannot become browser passed merely by opening HTML',()=>fixture((dir,manifest)=>{
  recordRender(dir,manifest,{status:'blocked',opened_via:'file://',browser_started:true,
    checks:[{name:'zoom',ok:false}],failed:['zoom'],browser_errors:[]});
  assert.equal(manifest.browser.status,'failed');
  assert.equal(manifest.render.status,'blocked');
  assert.equal(manifest.visual_review,'not_performed');
}));

test('dependency failure records no browser run and does not invent HTML',()=>fixture((dir,manifest)=>{
  recordRender(dir,manifest,{status:'blocked',opened_via:null,browser_started:false,
    checks:[{name:'runtime',ok:false}],failed:['runtime'],browser_errors:[]});
  assert.equal(manifest.browser.status,'not_run');
  assert.deepEqual(Object.keys(manifest.rendered_files),['verification.json']);
}));

test('HTML text and JSON preserve content without creating an HTML injection',()=>{
  const text='</script><img src=x onerror="alert(1)"> & 用户';
  assert.ok(!escapeText(text).includes('<'));
  assert.ok(!inlineJSON({text}).includes('<'));
  assert.equal(JSON.parse(inlineJSON({text})).text,text);
});

function svgNode(tag, attributes={}, children=[], textContent='') {
  return {localName:tag,attributes:Object.entries(attributes).map(([name,value])=>({name,value})),
    textContent,querySelectorAll:()=>children};
}
function checkSvg(svg) {
  const saved=globalThis.document;
  globalThis.document={querySelector:()=>svg};
  try {return inspectSafeSvg();} finally {globalThis.document=saved;}
}
test('SVG sanitizer accepts local shapes and fragment markers only',()=>{
  assert.equal(checkSvg(svgNode('svg',{},[svgNode('path',{'marker-end':'url(#arrow)'})])),true);
});
test('SVG sanitizer rejects external paint/marker resources and executable HTML',()=>{
  for(const name of ['marker-end','fill','stroke','filter','clip-path','mask'])
    assert.throws(()=>checkSvg(svgNode('svg',{},[svgNode('path',{[name]:'url(https://example.invalid/a#b)'})])),/unsafe/);
  for(const node of [svgNode('script'),svgNode('iframe'),svgNode('image',{href:'x'}),
    svgNode('div',{onclick:'evil()'}),svgNode('style',{},[],'@import "remote.css";')])
    assert.throws(()=>checkSvg(svgNode('svg',{},[node])));
});

test('version checks support ESM-only package exports and reject version drift',()=>fixture(dir=>{
  const name='@mermaid-js/layout-elk', folder=path.join(dir,'node_modules',name);
  fs.mkdirSync(folder,{recursive:true});
  fs.writeFileSync(path.join(folder,'package.json'),JSON.stringify({name,version:'0.1.9',exports:{import:'./index.mjs'}}));
  assert.deepEqual(readRuntimeVersions(dir,{[name]:'0.1.9'}),{[name]:'0.1.9'});
  assert.throws(()=>readRuntimeVersions(dir,{[name]:'0.2.0'}),/mismatch/);
}));
