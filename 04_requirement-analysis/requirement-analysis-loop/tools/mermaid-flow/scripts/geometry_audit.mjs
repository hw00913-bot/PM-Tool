// Deliberately narrow geometry support. A blocked candidate is never an automatic
// acceptance. Even a pass still requires human visual and business review.
export function parseAbsolutePolyline(value) {
  const number='[-+]?(?:\\d*\\.\\d+|\\d+\\.?\\d*)(?:[eE][-+]?\\d+)?';
  const command=new RegExp('([ML])\\s*('+number+')(?:\\s*,\\s*|\\s+)('+number+')','g');
  const text=String(value ?? ''), points=[];
  let end=0, match;
  while ((match=command.exec(text))) {
    if (text.slice(end,match.index).trim() || (points.length===0 ? match[1]!=='M' : match[1]!=='L')) throw Error('Only one absolute M followed by explicit L commands is supported');
    points.push([Number(match[2]),Number(match[3])]); end=command.lastIndex;
  }
  if (text.slice(end).trim() || points.length<2 || !points.flat().every(Number.isFinite)) throw Error('Malformed or unsupported SVG path');
  return points;
}

export function collinearOverlap(a,b,c,d,tolerance=.02) {
  const axis=(p,q)=>Math.abs(p[0]-q[0])<=tolerance?'V':Math.abs(p[1]-q[1])<=tolerance?'H':'D';
  const first=axis(a,b), second=axis(c,d);
  if(first==='D' || first!==second) return 0;
  const fixed=first==='V'?0:1, moving=1-fixed;
  if(Math.abs(a[fixed]-c[fixed])>tolerance) return 0;
  return Math.max(0,Math.min(Math.max(a[moving],b[moving]),Math.max(c[moving],d[moving]))-Math.max(Math.min(a[moving],b[moving]),Math.min(c[moving],d[moving])));
}

// Serializable with page.evaluate(auditRenderedGeometry, data): all helpers are
// intentionally inside this function. Unit tests execute its serialized source
// with mocked SVG DOM geometry; they are not a substitute for browser evidence.
export function auditRenderedGeometry(data) {
  const blockers=[], warnings=[], seen=new Set();
  const metric={nodes:0,edges:0,edge_labels:0,node_labels:0,node_overlap_candidates:0,edge_node_crossing_candidates:0,collinear_overlap_candidates:0,orthogonal_crossings:0,label_overlap_candidates:0,label_detachment_candidates:0,endpoint_candidates:0,unknown_shapes:0};
  const eps=.02, contactTolerance=1.25;
  const block=(kind,ids,detail)=>{
    const key=kind+'|'+ids.join('|')+'|'+detail;
    if(!seen.has(key)){seen.add(key);blockers.push({kind,ids,detail});}
  };
  const finish=()=>({status:blockers.length?'blocked':'pass',blockers,warnings,...metric,candidate_check_only:true,visual_review:'manual_required',business_acceptance:'not_claimed',tolerance_svg_units:{axis:eps,endpoint:contactTolerance}});
  const norm=s=>String(s??'').replace(/\s/g,'');
  const axis=(a,b)=>Math.abs(a[0]-b[0])<=eps?'V':Math.abs(a[1]-b[1])<=eps?'H':'D';
  function parse(value) {
    const number='[-+]?(?:\\d*\\.\\d+|\\d+\\.?\\d*)(?:[eE][-+]?\\d+)?';
    const command=new RegExp('([ML])\\s*('+number+')(?:\\s*,\\s*|\\s+)('+number+')','g');
    const text=String(value??''),points=[];let end=0,match;
    while((match=command.exec(text))){
      if(text.slice(end,match.index).trim() || (points.length===0?match[1]!=='M':match[1]!=='L')) throw Error('Only explicit absolute M/L paths are supported');
      points.push([Number(match[2]),Number(match[3])]);end=command.lastIndex;
    }
    if(text.slice(end).trim() || points.length<2 || !points.flat().every(Number.isFinite)) throw Error('Malformed or unsupported SVG path');
    return points;
  }
  const overlap=(r,s,inset=eps)=>Math.min(r.x+r.w,s.x+s.w)-Math.max(r.x,s.x)>inset && Math.min(r.y+r.h,s.y+s.h)-Math.max(r.y,s.y)>inset;
  function segmentRect(a,b,r,inset=0) {
    const box={x:r.x+inset,y:r.y+inset,w:r.w-2*inset,h:r.h-2*inset};
    if(box.w<=0 || box.h<=0) return false;
    // Liang-Barsky works for axis-aligned and diagonal segments alike.
    const dx=b[0]-a[0],dy=b[1]-a[1],p=[-dx,dx,-dy,dy],q=[a[0]-box.x,box.x+box.w-a[0],a[1]-box.y,box.y+box.h-a[1]];
    let low=0,high=1;
    for(let i=0;i<4;i++){
      if(Math.abs(p[i])<1e-10){if(q[i]<0)return false;}
      else {const u=q[i]/p[i];if(p[i]<0)low=Math.max(low,u);else high=Math.min(high,u);if(low>high)return false;}
    }
    return true;
  }
  function distanceToSegment(p,a,b) {
    const dx=b[0]-a[0],dy=b[1]-a[1],length=dx*dx+dy*dy;
    const t=length?Math.max(0,Math.min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/length)):0;
    return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);
  }
  const pointInside=(p,shape)=>{
    const r=shape.box;
    if(shape.type==='ellipse') return ((p[0]-r.x-r.w/2)/(r.w/2))**2+((p[1]-r.y-r.h/2)/(r.h/2))**2<1-1e-7;
    if(shape.type==='rect') return p[0]>r.x+eps && p[0]<r.x+r.w-eps && p[1]>r.y+eps && p[1]<r.y+r.h-eps;
    let inside=false;
    const points=shape.outline;
    for(let i=0,j=points.length-1;i<points.length;j=i++){
      const a=points[i],b=points[j];
      if((a[1]>p[1])!==(b[1]>p[1]) && p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])inside=!inside;
    }
    return inside;
  };
  const distanceToOutline=(p,shape)=>Math.min(...shape.outline.map((a,i)=>distanceToSegment(p,a,shape.outline[(i+1)%shape.outline.length])));
  function segmentInsideShape(a,b,shape) {
    // Clip against the verified convex outline, not its enclosing rectangle.
    // The small inward tolerance excludes contact along the boundary.
    const pts=shape.outline;
    const area=pts.reduce((sum,p,i)=>{const q=pts[(i+1)%pts.length];return sum+p[0]*q[1]-p[1]*q[0];},0);
    const sign=Math.sign(area);let low=0,high=1;
    for(let i=0;i<pts.length;i++){
      const p=pts[i],q=pts[(i+1)%pts.length],dx=q[0]-p[0],dy=q[1]-p[1],length=Math.hypot(dx,dy);
      if(length<1e-8)continue;
      const start=sign*(dx*(a[1]-p[1])-dy*(a[0]-p[0]))/length-eps;
      const slope=sign*(dx*(b[1]-a[1])-dy*(b[0]-a[0]))/length;
      if(Math.abs(slope)<1e-10){if(start<=0)return false;}
      else {const t=-start/slope;if(slope>0)low=Math.max(low,t);else high=Math.min(high,t);if(low>=high)return false;}
    }
    return high-low>1e-8;
  }
  function closedLinearPath(value) {
    // Mermaid classic stadiums use C commands whose controls lie on their
    // chord. These are exact straight segments, not sampled arbitrary curves.
    const number='[-+]?(?:\\d*\\.\\d+|\\d+\\.?\\d*)(?:[eE][-+]?\\d+)?';
    const token=new RegExp('[MLCZ]|'+number,'g'),text=String(value??'');
    const items=[];let end=0,match;
    while((match=token.exec(text))){if(text.slice(end,match.index).replace(/[\s,]/g,''))throw Error('Unsupported shape path token');items.push(match[0]);end=token.lastIndex;}
    if(text.slice(end).trim())throw Error('Unsupported shape path suffix');
    const pts=[];let i=0,closed=false;
    while(i<items.length){
      const command=items[i++],count={M:2,L:2,C:6,Z:0}[command];
      if(count===undefined||closed||(pts.length===0?command!=='M':command==='M'))throw Error('Shape needs one closed absolute outline');
      const values=items.slice(i,i+count).map(Number);i+=count;
      if(values.length!==count||!values.every(Number.isFinite))throw Error('Malformed shape path');
      if(command==='Z'){closed=true;continue;}
      const p=values.slice(-2);
      if(command==='C'){
        const a=pts.at(-1),length=Math.hypot(p[0]-a[0],p[1]-a[1]);
        for(const c of [values.slice(0,2),values.slice(2,4)]){
          if(distanceToSegment(c,a,p)>1e-7)throw Error('Nonlinear cubic shape boundary requires review');
          if(length<1e-8&&Math.hypot(c[0]-a[0],c[1]-a[1])>1e-7)throw Error('Degenerate curved shape boundary');
        }
      }
      if(!pts.length||Math.hypot(p[0]-pts.at(-1)[0],p[1]-pts.at(-1)[1])>1e-8)pts.push(p);
      if(pts.length>4096)throw Error('Shape outline too complex');
    }
    if(!closed&&(!pts.length||Math.hypot(pts[0][0]-pts.at(-1)[0],pts[0][1]-pts.at(-1)[1])>1e-7))throw Error('Open shape path boundary');
    if(pts.length>1&&Math.hypot(pts[0][0]-pts.at(-1)[0],pts[0][1]-pts.at(-1)[1])<1e-7)pts.pop();
    if(pts.length<3)throw Error('Empty shape outline');
    return pts;
  }
  let svg;
  try {svg=document.querySelector('#graph svg');} catch(error) {block('svg_missing',[],String(error));return finish();}
  if(!svg){block('svg_missing',[],'No rendered #graph svg is available');return finish();}
  if(!Array.isArray(data?.nodes)||!Array.isArray(data?.edges)){block('invalid_graph',[],'nodes and edges must be arrays');return finish();}
  if(!data.nodes.length){block('empty_graph',[],'A graph with no rendered business nodes cannot pass');return finish();}
  const attr=(el,name)=>el?.getAttribute?.(name);
  const all=(el,selector)=>Array.from(el.querySelectorAll(selector));
  const relative=el=>{
    const root=svg.getCTM(),matrix=el.getCTM();
    if(!root || !matrix)throw Error('Missing rendered SVG transform');
    const m=root.inverse().multiply(matrix);
    if(![m.a,m.b,m.c,m.d,m.e,m.f].every(Number.isFinite))throw Error('Non-finite SVG transform');
    return m;
  };
  const transform=(m,p)=>[m.a*p[0]+m.c*p[1]+m.e,m.b*p[0]+m.d*p[1]+m.f];
  const bounding=el=>{
    const b=el.getBBox(),m=relative(el);
    const pts=[[b.x,b.y],[b.x+b.width,b.y],[b.x+b.width,b.y+b.height],[b.x,b.y+b.height]].map(p=>transform(m,p));
    const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]);
    const box={x:Math.min(...xs),y:Math.min(...ys),w:Math.max(...xs)-Math.min(...xs),h:Math.max(...ys)-Math.min(...ys)};
    if(!Object.values(box).every(Number.isFinite)||box.w<=0||box.h<=0)throw Error('Empty or non-finite rendered bounds');
    return box;
  };
  function shapeOf(el) {
    const candidates=all(el,'rect,polygon,ellipse,circle,path').filter(child=>!child.closest?.('.label, .nodeLabel, foreignObject'));
    const ranked=candidates.map(child=>({el:child,box:bounding(child)})).sort((a,b)=>b.box.w*b.box.h-a.box.w*a.box.h);
    if(!ranked.length)throw Error('No measurable supported shape element');
    const selected=ranked[0],m=relative(selected.el),r=selected.box,tag=selected.el.tagName.toLowerCase();
    if(Math.abs(m.b)>eps||Math.abs(m.c)>eps||m.a<=0||m.d<=0)throw Error('Rotated, reflected or skewed shape boundary requires review');
    // Nested subprocess decoration is acceptable, but multiple disjoint shapes
    // do not establish a single trustworthy outer node boundary.
    if(ranked.some(item=>item.box.x<r.x-eps||item.box.y<r.y-eps||item.box.x+item.box.w>r.x+r.w+eps||item.box.y+item.box.h>r.y+r.h+eps))throw Error('Composite shape has no verified single outer boundary');
    let outline=[],type=tag;
    if(tag==='rect'){
      const computed=typeof getComputedStyle==='function'?getComputedStyle(selected.el):null;
      const radius=key=>{const raw=computed?.getPropertyValue?.(key)||attr(selected.el,key);if(raw==null||raw===''||raw==='auto')return null;if(!/^(?:\d*\.)?\d+(?:px)?$/.test(raw))throw Error('Unverified rounded rectangle radius '+raw);return Number.parseFloat(raw);};
      const rawRx=radius('rx'),rawRy=radius('ry');
      const rx=Math.min(r.w/2,(rawRx??rawRy??0)*m.a),ry=Math.min(r.h/2,(rawRy??rawRx??0)*m.d);
      if(rx>0&&ry>0){
        type='rounded-rect';
        for(const [cx,cy,start] of [[r.x+r.w-rx,r.y+ry,-90],[r.x+r.w-rx,r.y+r.h-ry,0],[r.x+rx,r.y+r.h-ry,90],[r.x+rx,r.y+ry,180]]){
          for(let i=0;i<=64;i++){const angle=(start+i*90/64)*Math.PI/180;outline.push([cx+rx*Math.cos(angle),cy+ry*Math.sin(angle)]);}
        }
      }else outline=[[r.x,r.y],[r.x+r.w,r.y],[r.x+r.w,r.y+r.h],[r.x,r.y+r.h]];
    }else if(tag==='ellipse'||tag==='circle'){
      type='ellipse';
      for(let i=0;i<256;i++){const angle=i*Math.PI/128;outline.push([r.x+r.w/2+r.w/2*Math.cos(angle),r.y+r.h/2+r.h/2*Math.sin(angle)]);}
    }else if(tag==='polygon'||tag==='path'){
      if(tag==='path'){
        if(attr(selected.el,'fill')==='none')throw Error('Unfilled compound path cannot establish a node interior');
        outline=closedLinearPath(attr(selected.el,'d')).map(p=>transform(m,p));type='polygon';
      }else{
        const values=String(attr(selected.el,'points')??'').trim().split(/[\s,]+/).map(Number);
        if(values.length<6||values.length%2||!values.every(Number.isFinite))throw Error('Malformed polygon');
        outline=Array.from({length:values.length/2},(_,i)=>transform(m,[values[i*2],values[i*2+1]]));
      }
      // Only convex, single outlines establish a measurable interior here.
      // Nonlinear curves, cylinders and arbitrary compound paths remain blocked.
      const turns=outline.map((a,i)=>{const b=outline[(i+1)%outline.length],c=outline[(i+2)%outline.length];return (b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]);}).filter(v=>Math.abs(v)>eps);
      if(!turns.length||turns.some(v=>Math.sign(v)!==Math.sign(turns[0])))throw Error('Non-convex polygon boundary requires review');
      for(let i=0;i<outline.length;i++){
        const a=outline[i],b=outline[(i+1)%outline.length];
        if(outline.some(p=>{const cross=(b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]);return Math.abs(cross)>eps&&Math.sign(cross)!==Math.sign(turns[0]);}))throw Error('Self-intersecting or non-convex polygon boundary requires review');
      }
    }else throw Error('Unsupported actual SVG shape '+tag+'; no bounding-box anchor approximation is accepted');
    // Large curves would need finer approximation; do not silently accept an
    // unknown approximation error near an endpoint.
    if((type==='ellipse'||type==='rounded-rect')&&Math.max(r.w,r.h)>3000)throw Error('Curve approximation tolerance not verified at this size');
    return {type,box:r,outline};
  }
  const renderedNodes=all(svg,'g.node'),renderedEdges=all(svg,'path.flowchart-link');
  const nodeList=[],edgeList=[],labels=[],nodeMap=new Map();
  if(renderedNodes.length!==data.nodes.length)block('node_count_mismatch',[],`Expected ${data.nodes.length}, rendered ${renderedNodes.length}`);
  if(renderedEdges.length!==data.edges.length)block('edge_count_mismatch',[],`Expected ${data.edges.length}, rendered ${renderedEdges.length}`);
  for(const n of data.nodes){
    const matches=renderedNodes.filter(el=>attr(el,'data-node-id')===n.id||String(attr(el,'id')??'').startsWith('flowchart-'+n.mermaid_id+'-'));
    if(matches.length!==1){block('node_identity_unverified',[n.id],`Expected one exact node match, found ${matches.length}`);continue;}
    const el=matches[0];
    try{
      const node={id:n.id,box:bounding(el),shape:null};
      try{node.shape=shapeOf(el);}catch(error){metric.unknown_shapes++;block('shape_boundary_unverified',[n.id],String(error.message));}
      nodeList.push(node);nodeMap.set(n.id,node);
      const label=el.querySelector('.label')||el.querySelector('.nodeLabel');
      if(label&&norm(label.textContent)){labels.push({id:'node-label:'+n.id,owner:n.id,kind:'node',box:bounding(label)});metric.node_labels++;}
    }catch(error){block('node_geometry_unavailable',[n.id],String(error.message));}
  }
  metric.nodes=nodeList.length;
  for(let i=0;i<nodeList.length;i++)for(let j=i+1;j<nodeList.length;j++){
    if(overlap(nodeList[i].box,nodeList[j].box)){metric.node_overlap_candidates++;block('node_overlap',[nodeList[i].id,nodeList[j].id],'Rendered node bounds overlap; inspect actual shapes and labels');}
  }
  for(const e of data.edges){
    const aliases=new Set([e.id,e.mermaid_id,e.mermaid_id+'_0','full-flow-'+e.id,'full-flow-'+e.mermaid_id,'full-flow-'+e.mermaid_id+'_0']);
    const matches=renderedEdges.filter(el=>attr(el,'data-edge-id')===e.id||aliases.has(attr(el,'id')));
    if(matches.length!==1){block('edge_identity_unverified',[e.id],`Expected one exact path match, found ${matches.length}`);continue;}
    const el=matches[0];
    try{
      const m=relative(el),points=parse(attr(el,'d')).map(p=>transform(m,p));
      if(!nodeMap.has(e.source)||!nodeMap.has(e.target))block('edge_endpoint_identity_unverified',[e.id,e.source,e.target],'Declared source/target is not a verified rendered node');
      for(const [name,expected] of [['data-source-id',e.source],['data-target-id',e.target]])if(attr(el,name)&&attr(el,name)!==expected)block('edge_endpoint_identity_mismatch',[e.id],name+' contradicts the graph');
      const prior=attr(el,'data-original-endpoints');
      if(prior){
        const endpoints=JSON.parse(prior).map(p=>transform(m,p));
        if(endpoints.length!==2||endpoints.some((p,i)=>Math.hypot(p[0]-points[i?points.length-1:0][0],p[1]-points[i?points.length-1:0][1])>.002))block('edge_endpoint_changed',[e.id],'Normalization changed an original SVG endpoint');
      }
      const segments=points.slice(1).map((p,i)=>[points[i],p]);
      if(segments.some(([a,b])=>axis(a,b)==='D'))block('diagonal_segment',[e.id],'A diagonal segment remains in the rendered path');
      if(segments.some(([a,b])=>Math.hypot(b[0]-a[0],b[1]-a[1])<=eps))block('zero_length_segment',[e.id],'Zero-length segments prevent trustworthy endpoint direction checks');
      const edge={...e,el,points,segments,aliases};edgeList.push(edge);
      for(const node of nodeList){
        if(node.id===e.source||node.id===e.target)continue;
        if(segments.some(([a,b])=>segmentRect(a,b,node.box,eps))){metric.edge_node_crossing_candidates++;block('edge_crosses_node',[e.id,node.id],'Path intersects another rendered node bounding box; candidate blocks automatic approval');}
      }
      for(let i=1;i<segments.length;i++){
        const [a,b]=segments[i-1],[c,d]=segments[i];
        if(axis(a,b)===axis(c,d)&&((b[0]-a[0])*(d[0]-c[0])+(b[1]-a[1])*(d[1]-c[1]))<0)block('edge_backtracking',[e.id],`Adjacent route segments reverse direction at bend ${i}`);
      }
    }catch(error){block('edge_geometry_unavailable',[e.id],String(error.message));}
  }
  metric.edges=edgeList.length;
  for(let i=0;i<edgeList.length;i++)for(let j=i+1;j<edgeList.length;j++){
    let maximum=0,crossed=false;
    for(const [a,b] of edgeList[i].segments)for(const [c,d] of edgeList[j].segments){
      const one=axis(a,b),two=axis(c,d);
      if(one==='D'||two==='D')continue;
      if(one===two){
        const fixed=one==='V'?0:1,moving=1-fixed;
        if(Math.abs(a[fixed]-c[fixed])<=eps)maximum=Math.max(maximum,Math.min(Math.max(a[moving],b[moving]),Math.max(c[moving],d[moving]))-Math.max(Math.min(a[moving],b[moving]),Math.min(c[moving],d[moving])));
      }else{
        const v=one==='V'?[a,b]:[c,d],h=one==='H'?[a,b]:[c,d];
        if(v[0][0]>Math.min(h[0][0],h[1][0])+eps&&v[0][0]<Math.max(h[0][0],h[1][0])-eps&&h[0][1]>Math.min(v[0][1],v[1][1])+eps&&h[0][1]<Math.max(v[0][1],v[1][1])-eps)crossed=true;
      }
    }
    if(maximum>eps){metric.collinear_overlap_candidates++;block('edge_collinear_overlap',[edgeList[i].id,edgeList[j].id],`Different edges share ${maximum.toFixed(3)} SVG units of collinear path`);}
    if(crossed){metric.orthogonal_crossings++;warnings.push({kind:'orthogonal_crossing',ids:[edgeList[i].id,edgeList[j].id],detail:'Perpendicular crossing is not collinear overlap; manually confirm readability and that no junction is implied'});}
  }
  const rawLabels=all(svg,'.edgeLabel');
  const outerLabels=rawLabels.filter(el=>!rawLabels.some(other=>other!==el&&other.contains(el)));
  const assigned=new Map();
  for(const [index,el] of outerLabels.entries()){
    if(!norm(el.textContent))continue;
    const refs=new Set();
    for(const part of [el,...all(el,'[data-id],[data-edge-id],[data-edge],[id]')])for(const key of ['data-id','data-edge-id','data-edge','id']){
      const value=attr(part,key);if(value)refs.add(value);
    }
    const matches=edgeList.filter(edge=>[...edge.aliases].some(alias=>refs.has(alias)||refs.has('label_'+alias)||refs.has(alias+'-label')));
    if(matches.length!==1){block('edge_label_owner_unverified',[],`Rendered edge label ${index} has ${matches.length} verified owners; text-only or nearest-edge matching is forbidden`);continue;}
    const edge=matches[0];
    assigned.set(edge.id,[...(assigned.get(edge.id)||[]),el]);
    if(norm(el.textContent)!==norm(edge.text))block('edge_label_text_mismatch',[edge.id],`Its own rendered label does not match the edge condition`);
    try{
      const label={id:'edge-label:'+edge.id,owner:edge.id,kind:'edge',box:bounding(el)};
      labels.push(label);metric.edge_labels++;
      if(!edge.segments.some(([a,b])=>segmentRect(a,b,label.box,-2))){metric.label_detachment_candidates++;block('edge_label_detached',[edge.id],'Owned label is not on its current path after normalization; do not infer another business edge');}
    }catch(error){block('edge_label_geometry_unavailable',[edge.id],String(error.message));}
  }
  for(const edge of edgeList){
    const count=(assigned.get(edge.id)||[]).length;
    if(norm(edge.text)&&count!==1)block('edge_label_missing_or_duplicate',[edge.id],`Expected one associated label, found ${count}`);
    if(!norm(edge.text)&&count)block('unexpected_edge_label',[edge.id],'An unlabeled graph edge has rendered label content');
  }
  for(const label of labels)for(const node of nodeList){
    if(label.kind==='node'&&label.owner===node.id)continue;
    if(overlap(label.box,node.box)){metric.label_overlap_candidates++;block('label_over_node',[label.id,node.id],'Rendered label overlaps a non-owning node');}
  }
  for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++)if(overlap(labels[i].box,labels[j].box)){
    metric.label_overlap_candidates++;block('label_overlap',[labels[i].id,labels[j].id],'Rendered label bounds overlap');
  }
  function markerTip(edge,isStart,tangent) {
    const value=attr(edge.el,isStart?'marker-start':'marker-end');
    const endpoint=edge.points[isStart?0:edge.points.length-1];
    if(!value||value==='none')return endpoint;
    const match=String(value).match(/^url\(["']?(?:[^#]*#)([^)"']+)["']?\)$/);
    if(!match)throw Error('Unsupported marker reference');
    const markers=all(svg,'marker').filter(el=>attr(el,'id')===match[1]);
    if(markers.length!==1)throw Error('Marker definition missing or ambiguous');
    const marker=markers[0],orient=attr(marker,'orient');
    if(!['auto','auto-start-reverse'].includes(orient))throw Error('Marker orientation is not verified automatic orientation');
    const vb=String(attr(marker,'viewBox')??'').trim().split(/[\s,]+/).map(Number);
    const refX=Number(attr(marker,'refX')),refY=Number(attr(marker,'refY'));
    const width=Number(attr(marker,'markerWidth')??3),height=Number(attr(marker,'markerHeight')??3);
    if(vb.length!==4||!vb.every(Number.isFinite)||vb[2]<=0||vb[3]<=0||!Number.isFinite(refX)||!Number.isFinite(refY)||width<=0||height<=0)throw Error('Marker viewport geometry is unavailable');
    const sx=width/vb[2],sy=height/vb[3];
    if(Math.abs(sx-sy)>eps)throw Error('Non-uniform marker viewport requires review');
    let stroke=1;
    const units=attr(marker,'markerUnits')||'strokeWidth';
    if(units==='strokeWidth'){
      const computed=typeof getComputedStyle==='function'?getComputedStyle(edge.el).strokeWidth:attr(edge.el,'stroke-width');
      stroke=Number.parseFloat(computed);
      if(!Number.isFinite(stroke)||stroke<=0)throw Error('Marker stroke scale unavailable');
    }else if(units!=='userSpaceOnUse')throw Error('Unsupported marker units');
    const pathMatrix=relative(edge.el);
    if(Math.abs(pathMatrix.b)>eps||Math.abs(pathMatrix.c)>eps||Math.abs(pathMatrix.a-pathMatrix.d)>eps||pathMatrix.a<=0)throw Error('Marker path transform needs review');
    const shapes=all(marker,'path,polygon');
    if(shapes.length!==1||attr(shapes[0],'transform'))throw Error('Composite or transformed marker shape requires review');
    const tag=shapes[0].tagName.toLowerCase();let points;
    if(tag==='polygon'){
      const nums=String(attr(shapes[0],'points')??'').trim().split(/[\s,]+/).map(Number);
      if(nums.length%2||!nums.every(Number.isFinite))throw Error('Malformed arrow polygon');
      points=Array.from({length:nums.length/2},(_,i)=>[nums[2*i],nums[2*i+1]]);
    }else{
      const d=String(attr(shapes[0],'d')??'').trim();
      if(!/[zZ]$/.test(d))throw Error('Open marker path is not a verified arrowhead');
      points=parse(d.slice(0,-1));
    }
    if(points.length!==3)throw Error('Only a verified triangular arrowhead is supported');
    const direction=isStart&&orient!=='auto-start-reverse'?-1:1;
    const extent=Math.max(...points.map(p=>p[0]*direction));
    const tips=points.filter(p=>Math.abs(p[0]*direction-extent)<=eps);
    if(tips.length!==1||Math.abs(tips[0][1]-refY)>eps)throw Error('Arrow tip is not a verified centered single vertex');
    const scale=sx*stroke*pathMatrix.a;
    const offset=(tips[0][0]-refX)*direction*scale;
    if(offset<0||!Number.isFinite(offset))throw Error('Marker tip does not extend toward the declared node');
    return [endpoint[0]+tangent[0]*offset,endpoint[1]+tangent[1]*offset];
  }
  for(const edge of edgeList)for(const isStart of [true,false]){
    const node=nodeMap.get(isStart?edge.source:edge.target);
    if(!node?.shape)continue;
    const point=edge.points[isStart?0:edge.points.length-1],other=edge.points[isStart?1:edge.points.length-2];
    const length=Math.hypot(point[0]-other[0],point[1]-other[1]);
    if(length<=eps)continue;
    const towardNode=[(point[0]-other[0])/length,(point[1]-other[1])/length];
    try{
      const tip=markerTip(edge,isStart,towardNode),distance=distanceToOutline(tip,node.shape);
      if(distance>contactTolerance){metric.endpoint_candidates++;block('endpoint_off_boundary',[edge.id,node.id],`${isStart?'Source':'Target'} tip is ${distance.toFixed(3)} SVG units from its verified shape boundary`);}
      // The adjacent path must leave the node, not enter through its interior.
      const away=[tip[0]-towardNode[0]*3,tip[1]-towardNode[1]*3];
      if(pointInside(away,node.shape)){metric.endpoint_candidates++;block('endpoint_enters_node',[edge.id,node.id],'Adjacent edge direction runs through its own node interior');}
      const interiorSegments=isStart?edge.segments.slice(1):edge.segments.slice(0,-1);
      if(interiorSegments.some(([a,b])=>segmentInsideShape(a,b,node.shape)))block('edge_reenters_endpoint_node',[edge.id,node.id],'A non-adjacent edge segment intersects its verified endpoint shape interior');
    }catch(error){metric.endpoint_candidates++;block('endpoint_marker_unverified',[edge.id,node.id],String(error.message));}
  }
  return finish();
}
