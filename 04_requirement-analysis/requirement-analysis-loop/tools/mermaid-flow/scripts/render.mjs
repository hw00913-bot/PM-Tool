#!/usr/bin/env node
// Optional candidate renderer. It never writes formal atlases or approves a Loop stage.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { normalizeRoutes } from './route_geometry.mjs';
import { auditRenderedGeometry } from './geometry_audit.mjs';
import { loadBoundCandidate, ensureFreshRender, recordRender, inspectSafeSvg, escapeText, inlineJSON, readRuntimeVersions } from './candidate_io.mjs';

const skill = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const { values: args } = parseArgs({options:{
  input:{type:'string'}, out:{type:'string'}, runtime:{type:'string'}, browser:{type:'string'},
  'check-input':{type:'boolean',default:false},
}});
if (!args.input || !args.out) throw Error('Use render_mermaid_candidate.py to prepare a new bound candidate.');
const {dir:out, manifest, data, mmd} = loadBoundCandidate(args.input, args.out);
if (args['check-input']) {
  console.log(JSON.stringify({status:'source_binding_checked', formal_delivery:false,
    browser:'not_run', diagram_id:manifest.diagram_id}));
  process.exit(0);
}
ensureFreshRender(out);
const checks=[], errors=[];
const check=(name,ok,detail=null)=>checks.push({name,ok:!!ok,detail});
let browser, geometry=null, opened=null, runtimeVersions={}, fatal=null;
try {
  const cache = path.join(process.env.CODEX_HOME || path.join(os.homedir(),'.codex'),
    'skill-runtimes','requirement-analysis-mermaid');
  const runtime = path.resolve(args.runtime || process.env.REQUIREMENT_ANALYSIS_MERMAID_RUNTIME
    || (fs.existsSync(path.join(cache,'package.json')) ? cache : skill));
  const req = createRequire(path.join(runtime, 'package.json'));
  const pins = JSON.parse(fs.readFileSync(path.join(skill, 'package.json'), 'utf8')).dependencies;
  runtimeVersions=readRuntimeVersions(runtime,pins);
  const { build } = req('esbuild');
  const { chromium } = req('playwright');
  const bundle = await build({stdin:{contents:
    "import mermaid from 'mermaid'; import elk from '@mermaid-js/layout-elk'; mermaid.registerLayoutLoaders(elk); window.mermaid=mermaid;",
    resolveDir:runtime},bundle:true,write:false,format:'iife',platform:'browser',minify:true,logLevel:'silent'});
  const executablePath=args.browser || process.env.FLOW_BROWSER;
  // No fallback to a different browser, online renderer or a browser installation.
  browser=await chromium.launch({headless:true,timeout:30000,...(executablePath?{executablePath}:{})});
  runtimeVersions.browser=browser.version();
  const page=await browser.newPage({viewport:{width:1920,height:1080},deviceScaleFactor:1});
  page.setDefaultTimeout(10000);
  page.on('pageerror',e=>errors.push(String(e)));
  await page.route('**/*',r=>/^(file:|data:|about:)/.test(r.request().url())?r.continue():r.abort());
  await page.setContent('<!doctype html><html><meta charset="utf-8"><style>body{margin:0}#graph{width:max-content}</style><div id="graph"></div></html>');
  await page.addScriptTag({content:bundle.outputFiles[0].text});
  await page.evaluate(async ({mmd,data})=>{
    mermaid.initialize({startOnLoad:false,securityLevel:'strict',maxTextSize:Math.max(500000,mmd.length*2),
      maxEdges:Math.max(3000,data.edges.length+50),theme:'base',layout:'elk',
      themeVariables:{fontFamily:'PingFang SC, Microsoft YaHei, sans-serif',fontSize:'15px',lineColor:'#8794a5',edgeLabelBackground:'#fff'},
      flowchart:{curve:'linear',htmlLabels:true,useMaxWidth:false,nodeSpacing:data.layout.nodeSpacing,rankSpacing:data.layout.rankSpacing,padding:16},
      elk:{mergeEdges:false,nodePlacementStrategy:'NETWORK_SIMPLEX'}});
    const result=await mermaid.render('full-flow',mmd);
    document.getElementById('graph').innerHTML=result.svg;
    await document.fonts.ready;
    // Mermaid emits unused shadow filter definitions even for classic diagrams.
    // Remove only definitions with no active paint use; never allow filter execution.
    const svg=document.querySelector('#graph svg');
    for(const filter of svg.querySelectorAll('defs filter')){
      const id=filter.id;
      const used=[...svg.querySelectorAll('*')].some(el=>!el.closest('defs')
        &&(el.getAttribute('filter')?.includes('#'+id)||getComputedStyle(el).filter.includes('#'+id)));
      if(used)throw Error('Active SVG filters are not supported: '+id);
      filter.remove();
    }
  },{mmd,data});
  await page.evaluate(inspectSafeSvg);
  const normalization=await page.evaluate(normalizeRoutes,data);
  geometry=await page.evaluate(auditRenderedGeometry,data);
  geometry.normalization=normalization;
  check('Actual SVG geometry has no blocking candidates',geometry.status==='pass',geometry.blockers);
  const drawing=await page.evaluate(data=>{
    const svg=document.querySelector('#graph svg');
    const norm=s=>s.replace(/\s/g,'');
    const mismatches=data.nodes.filter(n=>norm(svg.querySelector('[data-node-id="'+n.id+'"]')?.textContent||'')!==norm(n.display_text)).map(n=>n.id);
    const markers=data.edges.filter(e=>{
      const p=svg.querySelector('[data-edge-id="'+e.id+'"]');
      if(!p)return true;
      const start=!!p.getAttribute('marker-start'),end=!!p.getAttribute('marker-end');
      return e.direction==='both'?!(start&&end):e.direction==='none'?(start||end):(!end||start);
    }).map(e=>e.id);
    const vb=svg.viewBox.baseVal;
    svg.setAttribute('width',vb.width);svg.setAttribute('height',vb.height);
    svg.setAttribute('data-candidate-only','true');
    const serialized=new XMLSerializer().serializeToString(svg);
    return {serialized,mismatches,markers,
      xmlValid:!new DOMParser().parseFromString(serialized,'image/svg+xml').querySelector('parsererror'),
      nodeCount:svg.querySelectorAll('g.node').length,edgeCount:svg.querySelectorAll('path.flowchart-link').length};
  },data);
  check('All nodes including notes are rendered',drawing.nodeCount===data.nodes.length);
  check('All reviewed edges are rendered',drawing.edgeCount===data.edges.length);
  check('Node text matches the reviewed graph',drawing.mismatches.length===0,drawing.mismatches);
  check('Arrow direction types match',drawing.markers.length===0,drawing.markers);
  check('SVG is valid XML',drawing.xmlValid);
  // Blocked geometry never becomes a seemingly deliverable HTML.
  if(checks.some(c=>!c.ok)) throw Error('Candidate layout blocked; inspect verification.json and prepare a new run.');
  const replacements={TITLE:escapeText(data.title),
    STATS:escapeText(data.stats.flow_nodes+' 个流程节点 · '+data.stats.original_edges+' 条连线 · 候选评审，非正式泳道'),
    SVG:drawing.serialized,DATA:inlineJSON(data)};
  const viewer=fs.readFileSync(path.join(skill,'assets/viewer.html'),'utf8')
    .replace(/@@(TITLE|STATS|SVG|DATA)@@/g,(_,k)=>replacements[k]);
  fs.writeFileSync(path.join(out,'完整流程.svg'),drawing.serialized,{flag:'wx'});
  fs.writeFileSync(path.join(out,'index.html'),viewer,{flag:'wx'});
  await page.goto(pathToFileURL(path.join(out,'index.html')).href,{waitUntil:'load'});
  opened='file://';
  await page.waitForFunction(()=>window.flowViewer);
  check('Direct offline opening',page.url().startsWith('file:'));
  check('One complete candidate canvas',await page.locator('#diagram>svg').count()===1);
  const requiredControls=['directory-toggle','directory','zoom-in','zoom-out','fit-width','actual-size',
    'readable-size','fit-all','go-start','fullscreen','search-input','search-submit','close-detail'];
  const missingControls=[];
  for(const id of requiredControls)if(await page.locator('#'+id).count()!==1)missingControls.push(id);
  check('Viewer controls are present',missingControls.length===0,missingControls);
  if(missingControls.length)throw Error('Viewer control contract does not match this renderer.');
  check('Directory starts hidden without fixed allocation',await page.evaluate(()=>{
    const directory=document.getElementById('directory'),viewport=document.getElementById('viewport');
    return directory.hidden&&getComputedStyle(directory).display==='none'
      &&document.getElementById('directory-toggle').getAttribute('aria-expanded')==='false'
      &&window.flowViewer.getState().directoryOpen===false
      &&Math.abs(viewport.getBoundingClientRect().width-document.querySelector('.main').getBoundingClientRect().width)<2;
  }));
  const closedDirectoryWidth=await page.locator('#viewport').evaluate(el=>el.getBoundingClientRect().width);
  await page.locator('#directory-toggle').click();
  check('Directory opens as an overlay',await page.evaluate(closedWidth=>{
    const directory=document.getElementById('directory');
    return !directory.hidden&&getComputedStyle(directory).display!=='none'
      &&getComputedStyle(directory).position==='absolute'
      &&document.getElementById('directory-toggle').getAttribute('aria-expanded')==='true'
      &&window.flowViewer.getState().directoryOpen===true
      &&Math.abs(document.getElementById('viewport').getBoundingClientRect().width-closedWidth)<2;
  },closedDirectoryWidth));
  if(Array.isArray(data.phases)&&data.phases.length){
    check('All phase navigation entries are present',await page.locator('#phases .phase').count()===data.phases.length);
    await page.locator('#phases .phase').first().click();
    check('Phase navigation selects the reviewed entry without filtering nodes',await page.evaluate(({entry,nodeCount})=>{
      const selected=document.querySelector('#diagram .node.selected');
      return selected?.getAttribute('data-node-id')===entry&&window.flowViewer.getState().zoom===1
        &&document.querySelectorAll('#diagram g.node').length===nodeCount;
    },{entry:data.phases[0].entry,nodeCount:data.nodes.length}));
  }
  await page.locator('#directory-toggle').click();
  check('Directory can be closed',await page.evaluate(()=>document.getElementById('directory').hidden
    &&window.flowViewer.getState().directoryOpen===false));
  const before=await page.evaluate(()=>window.flowViewer.getState());
  await page.locator('#zoom-in').click();
  check('Zoom responds',(await page.evaluate(()=>window.flowViewer.getState())).zoom>before.zoom);
  await page.locator('#fit-all').click();
  check('Fit-all uses both available canvas dimensions',await page.evaluate(()=>{
    const viewport=document.getElementById('viewport'),box=document.querySelector('#diagram>svg').viewBox.baseVal;
    const expected=Math.min(Math.max(1,viewport.clientWidth-24)/box.width,Math.max(1,viewport.clientHeight-24)/box.height);
    return Math.abs(window.flowViewer.getState().zoom-expected)<1e-6;
  }));
  check('Full graph remains present',await page.locator('#diagram g.node').count()===data.nodes.length);
  const query=data.nodes[0].text.split('\n')[0].slice(0,20);
  await page.locator('#search-input').fill(query);
  await page.locator('#search-submit').click();
  check('Search and original text',(await page.locator('#detail-content').innerText()).includes(query));
  await page.locator('#close-detail').click();
  const vp=await page.locator('#viewport').boundingBox();
  const panBefore=await page.evaluate(()=>window.flowViewer.getState());
  await page.mouse.move(vp.x+30,vp.y+40);await page.mouse.down();
  await page.mouse.move(vp.x+130,vp.y+100,{steps:6});await page.mouse.up();
  const panAfter=await page.evaluate(()=>window.flowViewer.getState());
  check('Canvas pan responds',Math.abs(panAfter.tx-panBefore.tx-100)<2&&Math.abs(panAfter.ty-panBefore.ty-60)<2);
  for(const size of [{width:1366,height:768},{width:1440,height:900},{width:1920,height:1080},{width:2560,height:1440}]){
    await page.setViewportSize(size);
    const label=size.width+'x'+size.height;
    check('Desktop canvas is full-width at '+label,await page.evaluate(()=>{
      const viewport=document.getElementById('viewport').getBoundingClientRect();
      return document.body.scrollWidth<=innerWidth+1&&viewport.width>=innerWidth-2
        &&document.getElementById('directory').hidden;
    }));
    await page.locator('#fit-width').click();
    check('Fit-width mode at '+label,await page.evaluate(()=>{
      const viewport=document.getElementById('viewport'),box=document.querySelector('#diagram>svg').viewBox.baseVal;
      const state=window.flowViewer.getState();
      return Math.abs(state.zoom-Math.max(1,viewport.clientWidth-24)/box.width)<1e-6&&state.ty===12;
    }));
    await page.locator('#actual-size').click();
    check('100 percent mode at '+label,await page.evaluate(()=>window.flowViewer.getState().zoom===1));
    await page.locator('#readable-size').click();
    check('Natural readable mode at '+label,await page.evaluate(()=>{
      const viewport=document.getElementById('viewport'),box=document.querySelector('#diagram>svg').viewBox.baseVal;
      const expected=Math.max(1,Math.min(1.25,Math.max(1,viewport.clientWidth-24)/box.width));
      return Math.abs(window.flowViewer.getState().zoom-expected)<1e-6;
    }));
  }
  const fullscreenSupported=await page.evaluate(()=>document.fullscreenEnabled
    &&typeof document.getElementById('viewer-panel').requestFullscreen==='function'
    &&typeof document.exitFullscreen==='function');
  if(!fullscreenSupported){
    check('Fullscreen includes review notice and toolbar',false,'not_tested: Fullscreen API is unavailable in this browser; the UI check is blocked.');
  }else{
    try{
      await page.locator('#fullscreen').click();
      await page.waitForFunction(()=>document.fullscreenElement===document.getElementById('viewer-panel')
        &&document.getElementById('fullscreen').getAttribute('aria-pressed')==='true',null,{timeout:3000});
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
      check('Fullscreen includes review notice and toolbar',await page.evaluate(()=>{
        const panel=document.fullscreenElement,toolbar=document.querySelector('.toolbar'),notice=document.querySelector('.review-notice');
        const visible=el=>{const rect=el.getBoundingClientRect();return rect.width>0&&rect.height>0&&rect.top>=0&&rect.bottom<=innerHeight+1};
        return panel===document.getElementById('viewer-panel')&&panel.contains(toolbar)&&panel.contains(notice)
          &&visible(toolbar)&&visible(notice)&&document.getElementById('fullscreen').getAttribute('aria-pressed')==='true';
      }));
      await page.locator('#fullscreen').click();
      await page.waitForFunction(()=>!document.fullscreenElement
        &&document.getElementById('fullscreen').getAttribute('aria-pressed')==='false',null,{timeout:3000});
      check('Fullscreen can exit',await page.locator('#fullscreen').getAttribute('aria-pressed')==='false');
    }catch(error){
      check('Fullscreen interaction completed',false,'not_tested_or_failed: '+String(error));
    }
  }
  check('Browser has no runtime errors',errors.length===0,errors);
} catch (error) {
  fatal=String(error);
  check('Candidate render completed',false,fatal);
} finally {
  if(browser) {
    try { await browser.close(); }
    catch(error) { check('Browser closed cleanly',false,String(error)); }
  }
}
const failed=checks.filter(c=>!c.ok).map(c=>c.name);
const report={contract:'mermaid-candidate-verification@1',created_at:new Date().toISOString(),
  status:failed.length?'blocked':'passed',formal_delivery:false,diagram_id:manifest.diagram_id,
  runtime:runtimeVersions,opened_via:opened,browser_started:!!browser,browser_errors:errors,
  checks,failed,geometry:geometry||{status:'blocked',blockers:[{kind:'not_measured',ids:[],detail:fatal||'Browser not run'}],warnings:[]},
  source_coverage:data.source_coverage,source_review:'manual_required',visual_review:'not_performed',
  business_acceptance:'not_claimed'};
recordRender(out,manifest,report);
console.log(JSON.stringify({status:report.status,formal_delivery:false,failed,output:out,browser:opened?'opened':'not_run'}));
if(failed.length)process.exitCode=1;
