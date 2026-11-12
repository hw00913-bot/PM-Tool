import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { auditRenderedGeometry, parseAbsolutePolyline, collinearOverlap } from '../scripts/geometry_audit.mjs';
import { normalizeRoutes } from '../scripts/route_geometry.mjs';

// Minimal SVG geometry DOM, not a browser or a claimed rendering experiment.
class Matrix {
  constructor(a=1,b=0,c=0,d=1,e=0,f=0){Object.assign(this,{a,b,c,d,e,f});}
  inverse(){const det=this.a*this.d-this.b*this.c;return new Matrix(this.d/det,-this.b/det,-this.c/det,this.a/det,(this.c*this.f-this.d*this.e)/det,(this.b*this.e-this.a*this.f)/det);}
  multiply(m){return new Matrix(this.a*m.a+this.c*m.b,this.b*m.a+this.d*m.b,this.a*m.c+this.c*m.d,this.b*m.c+this.d*m.d,this.a*m.e+this.c*m.f+this.e,this.b*m.e+this.d*m.f+this.f);}
}
class Element {
  constructor(tag,attrs={},box={x:0,y:0,width:1,height:1},children=[]){this.tagName=tag;this.attrs={...attrs};this.box=box;this.children=[];this.dataset=new Proxy({},{get:(_,key)=>this.attrs['data-'+String(key).replace(/[A-Z]/g,s=>'-'+s.toLowerCase())],set:(_,key,value)=>{this.attrs['data-'+String(key).replace(/[A-Z]/g,s=>'-'+s.toLowerCase())]=value;return true;}});children.forEach(el=>this.appendChild(el));}
  get id(){return this.attrs.id;} set id(value){this.attrs.id=value;}
  get textContent(){return this.text??this.children.map(el=>el.textContent).join('');} set textContent(value){this.text=value;}
  getAttribute(key){return Object.hasOwn(this.attrs,key)?this.attrs[key]:null;}
  setAttribute(key,value){this.attrs[key]=String(value);}
  appendChild(el){el.parent=this;this.children.push(el);return el;}
  getBBox(){return {...this.box};}
  getCTM(){return this.matrix??new Matrix();}
  matches(selector){
    if(selector==='[id*="flowchart-'+this.attrs.id?.replace(/^flowchart-/,'').replace(/-\d+$/,'')+'-"]')return true;
    if(selector.startsWith('[id*='))return String(this.attrs.id??'').includes(selector.slice(6,-2));
    const attribute=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    if(attribute)return attribute[2]===undefined?this.getAttribute(attribute[1])!==null:this.getAttribute(attribute[1])===attribute[2];
    const [tag,cls]=selector.split('.');
    return (!tag||this.tagName===tag)&&(!cls||String(this.attrs.class??'').split(/\s+/).includes(cls));
  }
  querySelectorAll(selectors){const alternatives=selectors.split(',').map(s=>s.trim());const descendants=this.children.flatMap(el=>[el,...el.querySelectorAll('*')]);return selectors==='*'?descendants:descendants.filter(el=>alternatives.some(selector=>el.matches(selector)));}
  querySelector(selector){return this.querySelectorAll(selector)[0]??null;}
  contains(el){return this===el||this.children.some(child=>child.contains(el));}
  closest(selectors){if(selectors.split(',').some(selector=>this.matches(selector.trim())))return this;return this.parent?.closest(selectors)??null;}
}
const box=(x,y,w,h)=>({x,y,width:w,height:h});
function node(id,x,y,w=20,h=20,shape='rect'){
  const shapeEl=new Element(shape,{},box(x,y,w,h));
  const label=new Element('g',{class:'label'},box(x+3,y+3,w-6,h-6));label.textContent=id;
  return new Element('g',{id:'flowchart-n_'+id+'-0',class:'node','data-node-id':id},box(x,y,w,h),[shapeEl,label]);
}
function edge(id,source,target,d,text=''){
  const el=new Element('path',{id:'full-flow-e_'+id+'_0',class:'flowchart-link','data-edge-id':id,d},box(0,0,1,1));
  return {model:{id,mermaid_id:'e_'+id,source,target,text,direction:'none',kind:'flow'},el};
}
function label(owner,text,x,y,w=20,h=10){const inner=new Element('g',{class:'label','data-id':'e_'+owner},box(x,y,w,h));inner.textContent=text;return new Element('g',{class:'edgeLabel'},box(x,y,w,h),[inner]);}
function fixture(nodeEls,edges,labels=[]){
  const data={nodes:nodeEls.map(el=>({id:el.getAttribute('data-node-id'),mermaid_id:'n_'+el.getAttribute('data-node-id'),kind:'process'})),edges:edges.map(e=>e.model)};
  const svg=new Element('svg',{},box(0,0,500,500),[...nodeEls,...edges.map(e=>e.el),...labels]);
  const document={querySelector:selector=>selector==='#graph svg'?svg:null,createElementNS:(_,tag)=>new Element(tag)};
  const context={document};
  // Deserializing the exported function proves there are no module-closure
  // helpers accidentally relied on by page.evaluate.
  const run=()=>vm.runInNewContext('('+auditRenderedGeometry.toString()+')',context)(data);
  const normalize=()=>vm.runInNewContext('('+normalizeRoutes.toString()+')',context)(data);
  return {data,svg,run,normalize};
}
const kinds=result=>result.blockers.map(b=>b.kind);

test('strict M/L parser rejects unsupported, relative, implicit and malformed paths',()=>{
  assert.deepEqual(parseAbsolutePolyline('M 1,2 L3 4 L -5.5,6e1'),[[1,2],[3,4],[-5.5,60]]);
  for(const value of ['M0,0 H10','m0,0 l10,10','M0,0 C1,2 3,4 5,6','M0,0 10,10','M0,0 M2,2','M0,0 L2,2 junk','M0,0 LNaN,2','M0,,0 L2,2'])assert.throws(()=>parseAbsolutePolyline(value),value);
});

test('pure collinear helper distinguishes perpendicular crossing from overlap',()=>{
  assert.equal(collinearOverlap([0,0],[20,0],[10,0],[30,0]),10);
  assert.equal(collinearOverlap([0,0],[20,0],[10,-10],[10,10]),0);
  assert.equal(collinearOverlap([0,0],[20,0],[20,0],[30,0]),0);
});

test('simple actual rectangle geometry passes only candidate checks',()=>{
  const f=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M20,10 L100,10','yes')],[label('E','yes',45,5)]);
  const result=f.run();
  assert.equal(result.status,'pass',JSON.stringify(result));
  assert.equal(result.visual_review,'manual_required');
  assert.equal(result.business_acceptance,'not_claimed');
  assert.equal(result.edge_labels,1);
});

test('overlapping rendered nodes are blocking candidates',()=>{
  const result=fixture([node('A',0,0),node('B',15,0)],[]).run();
  assert.equal(result.status,'blocked');assert.ok(kinds(result).includes('node_overlap'));
});

test('edge through an unrelated node is blocked',()=>{
  const result=fixture([node('A',0,0),node('B',100,0),node('C',45,0)],[edge('E','A','B','M20,10 L100,10')]).run();
  assert.ok(kinds(result).includes('edge_crosses_node'));assert.equal(result.edge_node_crossing_candidates,1);
});

test('collinear route overlap blocks even when the edges share source/target',()=>{
  const result=fixture([node('A',0,0),node('B',100,0)],[edge('E1','A','B','M20,10 L100,10'),edge('E2','A','B','M20,10 L100,10')]).run();
  assert.ok(kinds(result).includes('edge_collinear_overlap'));assert.equal(result.collinear_overlap_candidates,1);
});

test('normal perpendicular crossing is only a warning',()=>{
  const result=fixture([node('A',0,40),node('B',100,40),node('C',40,0),node('D',40,100)],[edge('E1','A','B','M20,50 L100,50'),edge('E2','C','D','M50,20 L50,100')]).run();
  assert.equal(result.status,'pass',JSON.stringify(result));assert.equal(result.orthogonal_crossings,1);assert.equal(result.warnings[0].kind,'orthogonal_crossing');
});

test('same global label text cannot satisfy a different edge',()=>{
  const result=fixture([node('A',0,0),node('B',100,0),node('C',0,60),node('D',100,60)],[edge('E1','A','B','M20,10 L100,10','yes'),edge('E2','C','D','M20,70 L100,70','yes')],[label('E1','yes',45,5)]).run();
  assert.ok(result.blockers.some(b=>b.kind==='edge_label_missing_or_duplicate'&&b.ids[0]==='E2'));
});

test('unowned labels and an owned label detached after rerouting are blocked',()=>{
  const unowned=label('OTHER','yes',45,5);
  const result=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M20,10 L100,10','yes')],[unowned,label('E','yes',45,35)]).run();
  assert.ok(kinds(result).includes('edge_label_owner_unverified'));assert.ok(kinds(result).includes('edge_label_detached'));
});

test('owned branch labels must match their own text',()=>{
  const result=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M20,10 L100,10','no')],[label('E','yes',45,5)]).run();
  assert.ok(kinds(result).includes('edge_label_text_mismatch'));
});

test('labels overlapping non-owning nodes or each other are blocked',()=>{
  const result=fixture([node('A',0,0),node('B',100,0),node('C',45,5)],[edge('E1','A','B','M20,10 L100,10','yes'),edge('E2','A','B','M20,15 L100,15','no')],[label('E1','yes',45,5),label('E2','no',45,10)]).run();
  assert.ok(kinds(result).includes('label_over_node'));assert.ok(kinds(result).includes('label_overlap'));
});

test('unknown actual SVG shape does not borrow rectangular anchors from kind',()=>{
  const result=fixture([node('A',0,0,20,20,'path'),node('B',100,0)],[edge('E','A','B','M20,10 L100,10')]).run();
  assert.equal(result.status,'blocked');assert.equal(result.unknown_shapes,1);assert.ok(kinds(result).includes('shape_boundary_unverified'));
});

test('diamond endpoints use the polygon boundary rather than its rectangular bounds',()=>{
  const b=node('B',100,0,20,20,'polygon');b.children[0].setAttribute('points','100,10 110,0 120,10 110,20');
  const good=fixture([node('A',0,0),b],[edge('E','A','B','M20,10 L100,10')]).run();
  assert.equal(good.status,'pass',JSON.stringify(good));
  const bad=fixture([node('A',0,0),b],[edge('E','A','B','M20,5 L100,5')]).run();
  assert.ok(kinds(bad).includes('endpoint_off_boundary'));
});

test('rounded rectangles and ellipses do not silently accept bbox corners as anchors',()=>{
  const b=node('B',100,0);b.children[0].setAttribute('rx','8');b.children[0].setAttribute('ry','8');
  const rounded=fixture([node('A',0,-10),b],[edge('E','A','B','M20,0 L100,0')]).run();
  assert.ok(kinds(rounded).includes('endpoint_off_boundary'));
  const ellipse=fixture([node('A',0,0),node('B',100,0,20,20,'ellipse')],[edge('E','A','B','M20,10 L100,10')]).run();
  assert.equal(ellipse.status,'pass',JSON.stringify(ellipse));
});

test('self-crossing star polygons are explicitly unknown even with consistent local turns',()=>{
  const b=node('B',100,0,20,20,'polygon');
  const points=Array.from({length:5},(_,i)=>[110+10*Math.cos(i*4*Math.PI/5),10+10*Math.sin(i*4*Math.PI/5)]);
  b.children[0].setAttribute('points',points.map(p=>p.join(',')).join(' '));
  const result=fixture([node('A',0,0),b],[edge('E','A','B','M20,10 L100,10')]).run();
  assert.ok(kinds(result).includes('shape_boundary_unverified'));
});

test('endpoint gaps and wrong departure directions are blocked',()=>{
  const gap=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M24,10 L96,10')]).run();
  assert.ok(kinds(gap).includes('endpoint_off_boundary'));assert.equal(gap.endpoint_candidates,2);
  const inside=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M0,10 L100,10')]).run();
  assert.ok(kinds(inside).includes('endpoint_enters_node'));
});

test('missing marker geometry blocks instead of treating any short gap as fine',()=>{
  const e=edge('E','A','B','M20,10 L96,10');e.el.setAttribute('marker-end','url(#missing)');
  const result=fixture([node('A',0,0),node('B',100,0)],[e]).run();
  assert.ok(kinds(result).includes('endpoint_marker_unverified'));
});

test('measured triangular marker tip can establish true endpoint contact',()=>{
  const e=edge('E','A','B','M20,10 L96,10');e.el.setAttribute('marker-end','url(#arrow)');
  const f=fixture([node('A',0,0),node('B',100,0)],[e]);
  f.svg.appendChild(new Element('marker',{id:'arrow',orient:'auto',viewBox:'0 0 10 10',markerWidth:'10',markerHeight:'10',refX:'6',refY:'5',markerUnits:'userSpaceOnUse'},box(0,0,10,10),[new Element('path',{d:'M0,0 L10,5 L0,10 Z'},box(0,0,10,10))]));
  const result=f.run();assert.equal(result.status,'pass',JSON.stringify(result));
});

test('node coordinate scale is measured by the audit, never guessed from group translation',()=>{
  const a=node('A',0,0);a.matrix=new Matrix(2,0,0,2);a.children.forEach(el=>el.matrix=a.matrix);
  const f=fixture([a,node('B',100,0)],[edge('E','A','B','M40,10 L100,10')]);
  const result=f.run();assert.equal(result.status,'pass',JSON.stringify(result));
  assert.throws(()=>f.normalize(),/Transformed node needs layout review/);
});

test('limited normalization preserves true endpoints and blocks detached labels',()=>{
  const e=edge('E','A','B','M20,10 L25,15 L80,15 L100,10','yes');
  const f=fixture([node('A',0,0),node('B',100,0)],[e],[label('E','yes',40,30)]);
  const before=parseAbsolutePolyline(e.el.getAttribute('d'));
  const normalized=f.normalize(),after=parseAbsolutePolyline(e.el.getAttribute('d'));
  assert.deepEqual(after[0],before[0]);assert.deepEqual(after.at(-1),before.at(-1));
  assert.equal(normalized.endpoints_preserved,true);assert.equal(normalized.geometry_audit_required,true);
  assert.equal(e.el.getAttribute('data-source-id'),'A');assert.equal(e.el.getAttribute('data-target-id'),'B');
  assert.ok(kinds(f.run()).includes('edge_label_detached'));
});

test('diagonals, malformed paths and tampered endpoint metadata fail closed',()=>{
  const diagonal=fixture([node('A',0,0),node('B',100,40)],[edge('E','A','B','M20,10 L100,50')]).run();
  assert.ok(kinds(diagonal).includes('diagonal_segment'));
  const malformed=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M20,10 H100')]).run();
  assert.ok(kinds(malformed).includes('edge_geometry_unavailable'));
  const e=edge('E','A','B','M20,10 L100,10');e.el.setAttribute('data-original-endpoints','[[20,10],[98,10]]');
  assert.ok(kinds(fixture([node('A',0,0),node('B',100,0)],[e]).run()).includes('edge_endpoint_changed'));
});

test('missing SVG fails closed',()=>{
  const result=vm.runInNewContext('('+auditRenderedGeometry.toString()+')',{document:{querySelector:()=>null}})({nodes:[],edges:[]});
  assert.equal(result.status,'blocked');assert.equal(result.blockers[0].kind,'svg_missing');
});

test('an empty graph cannot pass candidate verification',()=>{
  const result=fixture([],[]).run();assert.equal(result.status,'blocked');assert.ok(kinds(result).includes('empty_graph'));
});

test('Mermaid ELK label suffix resolves exact ownership without text or proximity guesses',()=>{
  const owned=label('E_0','yes',45,5);
  const good=fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M20,10 L100,10','yes')],[owned]).run();
  assert.equal(good.status,'pass',JSON.stringify(good));assert.equal(good.edge_labels,1);
  owned.children[0].setAttribute('data-id','e_OTHER_0');
  assert.ok(kinds(fixture([node('A',0,0),node('B',100,0)],[edge('E','A','B','M20,10 L100,10','yes')],[owned]).run()).includes('edge_label_owner_unverified'));
});

test('closed linear cubic outline is measured exactly, nonlinear and open paths remain blocked',()=>{
  const make=d=>{const n=node('B',100,0,20,20,'path');n.children[0].setAttribute('fill','#fff');n.children[0].setAttribute('d',d);return n;};
  const run=n=>fixture([node('A',0,0),n],[edge('E','A','B','M20,10 L100,10')]).run();
  const linear='M100,0 C105,0 115,0 120,0 C120,5 120,15 120,20 C115,20 105,20 100,20 C100,15 100,5 100,0';
  assert.equal(run(make(linear)).status,'pass');
  for(const d of [linear.replace('105,0','105,3'),linear.replace(/ C100,15.*$/,''),linear+' M100,0 L120,20',linear.replace('120,0','120,NaN')]){
    assert.ok(kinds(run(make(d))).includes('shape_boundary_unverified'),d);
  }
  const unfilled=make(linear);unfilled.children[0].setAttribute('fill','none');
  assert.ok(kinds(run(unfilled)).includes('shape_boundary_unverified'));
});

test('endpoint reroute through a diamond bbox corner passes, actual diamond interior is blocked',()=>{
  const a=node('A',0,0,40,40,'polygon');a.children[0].setAttribute('points','0,20 20,0 40,20 20,40');
  // Leave lower right boundary, then travel vertically in its empty bbox corner.
  const good=fixture([a,node('B',60,60)],[edge('E','A','B','M30,30 L35,30 L35,70 L60,70')]).run();
  assert.equal(good.status,'pass',JSON.stringify(good));
  const bad=fixture([a,node('B',60,60)],[edge('E','A','B','M30,30 L50,30 L50,20 L10,20 L10,70 L60,70')]).run();
  assert.ok(kinds(bad).includes('edge_reenters_endpoint_node'));
});

test('closed nonconvex and self-crossing linear cubic paths remain blocked',()=>{
  for(const d of ['M100,0 L120,0 L110,10 L120,20 L100,20 Z','M100,0 L120,20 L100,20 L120,0 Z']){
    const n=node('B',100,0,20,20,'path');n.children[0].setAttribute('fill','#fff');n.children[0].setAttribute('d',d);
    assert.ok(kinds(fixture([node('A',0,0),n],[]).run()).includes('shape_boundary_unverified'));
  }
});
