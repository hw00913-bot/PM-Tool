// Runs inside the rendering browser. This function has no module closure dependencies.
export function normalizeRoutes(data) {
  const svg = document.querySelector('#graph svg');
  const rects = new Map();
  const eq = (a, b) => Math.abs(a - b) < .02;
  const axis = (a, b) => eq(a[0], b[0]) ? 'V' : eq(a[1], b[1]) ? 'H' : 'D';
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
  function clean(points) {
    const out = [];
    for (const p of points) {
      if (out.length && Math.hypot(p[0] - out.at(-1)[0], p[1] - out.at(-1)[1]) < .02) continue;
      if (out.length > 1) {
        const a = out.at(-2), b = out.at(-1);
        // Preserve reversals: only remove a middle point between its neighbours.
        if (axis(a, b) === axis(b, p) && axis(a, b) !== 'D' && (b[0]-a[0])*(p[0]-b[0])+(b[1]-a[1])*(p[1]-b[1]) >= 0) out.pop();
      }
      out.push(p);
    }
    return out;
  }
  for (const n of data.nodes) {
    const el = svg.querySelector('[id*="flowchart-' + n.mermaid_id + '-"]');
    if (!el) throw Error('Missing node ' + n.id);
    el.dataset.nodeId = n.id;
    const b = el.getBBox(), m = svg.getCTM().inverse().multiply(el.getCTM());
    // This limited normalizer operates in one unscaled coordinate system.
    // The independent audit understands transforms; do not silently mix them here.
    if (Math.abs(m.a-1)>.00001 || Math.abs(m.d-1)>.00001 || Math.abs(m.b)>.00001 || Math.abs(m.c)>.00001) throw Error('Transformed node needs layout review: '+n.id);
    rects.set(n.id, { x: b.x + m.e, y: b.y + m.f, w: b.width, h: b.height, cx: b.x + m.e + b.width / 2, cy: b.y + m.f + b.height / 2 });
  }
  function crossings(pts, e) {
    const found = [];
    for (const [id, r] of rects) {
      if (id === e.source || id === e.target) continue;
      for (let i=1; i<pts.length; i++) {
        const a=pts[i-1], b=pts[i], inset=2;
        const hit = axis(a,b)==='V'
          ? a[0]>r.x+inset && a[0]<r.x+r.w-inset && Math.min(a[1],b[1])<r.y+r.h-inset && Math.max(a[1],b[1])>r.y+inset
          : axis(a,b)==='H' && a[1]>r.y+inset && a[1]<r.y+r.h-inset && Math.min(a[0],b[0])<r.x+r.w-inset && Math.max(a[0],b[0])>r.x+inset;
        if (hit) { found.push(id); break; }
      }
    }
    return found;
  }
  const routes=[];
  for (const e of data.edges) {
    const el = svg.querySelector('[id="full-flow-'+e.mermaid_id+'_0"]') || svg.querySelector('[id="full-flow-'+e.mermaid_id+'"]');
    if (!el) throw Error('Missing edge '+e.id);
    const matrix=svg.getCTM().inverse().multiply(el.getCTM());
    if (Math.abs(matrix.a-1)>.00001 || Math.abs(matrix.d-1)>.00001 || Math.abs(matrix.b)>.00001 || Math.abs(matrix.c)>.00001 || Math.abs(matrix.e)>.00001 || Math.abs(matrix.f)>.00001) throw Error('Transformed route needs layout review: '+e.id);
    const old = el.getAttribute('d');
    let pts = clean(parse(old));
    if (pts.length < 2) throw Error('Unrecognized edge path '+e.id);
    const originalEndpoints=[pts[0].slice(),pts.at(-1).slice()];
    const before = Math.max(0, pts.length-2);
    const priorCrossings = crossings(pts,e);
    let i=0, iterations=0;
    while (i<pts.length-1) {
      if (++iterations>10000) throw Error('Route cleanup did not converge: '+e.id);
      if (axis(pts[i],pts[i+1])!=='D') { i++; continue; }
      let j=i+1;
      while (j<pts.length-1 && axis(pts[j],pts[j+1])==='D') j++;
      const prev=i>0?axis(pts[i-1],pts[i]):null;
      const next=j<pts.length-1?axis(pts[j],pts[j+1]):null;
      if (prev && next && prev!==next) {
        if (Math.hypot(pts[j][0]-pts[i][0],pts[j][1]-pts[i][1])>30) throw Error('Large diagonal needs layout review: '+e.id);
        const c=prev==='V'?[pts[i][0],pts[j][1]]:[pts[j][0],pts[i][1]];
        pts.splice(i+1,j-i-1,c); i+=2;
      } else if (!prev && next) {
        if (Math.hypot(pts[j][0]-pts[0][0],pts[j][1]-pts[0][1])>30) throw Error('Large endpoint diagonal needs layout review: '+e.id);
        const p=next==='V'?[pts[j][0],pts[0][1]]:[pts[0][0],pts[j][1]];
        // Keep the actual Mermaid endpoint. Inserting an elbow can still create
        // a collision or detach a label; geometry_audit.mjs must block those.
        pts.splice(1,j-1,p); i=0;
      } else if (prev && !next) {
        if (Math.hypot(pts.at(-1)[0]-pts[i][0],pts.at(-1)[1]-pts[i][1])>30) throw Error('Large endpoint diagonal needs layout review: '+e.id);
        const p=prev==='V'?[pts[i][0],pts.at(-1)[1]]:[pts.at(-1)[0],pts[i][1]];
        pts.splice(i+1,pts.length-i-2,p); i++;
      } else throw Error('Ambiguous diagonal route '+e.id+'; review the layout.');
    }
    pts=clean(pts);
    if (pts[0].some((v,k)=>v!==originalEndpoints[0][k]) || pts.at(-1).some((v,k)=>v!==originalEndpoints[1][k])) throw Error('Route cleanup changed a true endpoint: '+e.id);
    for (let k=1;k<pts.length;k++) if (axis(pts[k-1],pts[k])==='D') throw Error('Remaining diagonal: '+e.id);
    const afterCrossings=crossings(pts,e);
    el.id='full-flow-'+e.id;
    el.dataset.edgeId=e.id;
    el.dataset.sourceId=e.source;
    el.dataset.targetId=e.target;
    el.dataset.originalEndpoints=JSON.stringify(originalEndpoints);
    el.setAttribute('d',pts.map((p,i)=>(i?'L':'M')+p.map(x=>+x.toFixed(3)).join(',')).join(''));
    el.setAttribute('stroke-linejoin','miter');
    el.setAttribute('stroke-linecap','butt');
    const short = [];
    for (let k=2;k<pts.length-1;k++) {
      const len=Math.hypot(pts[k][0]-pts[k-1][0],pts[k][1]-pts[k-1][1]);
      if (len<24) short.push({segment:k,length:+len.toFixed(2)});
    }
    routes.push({id:e.id,kind:e.kind,points:pts,bends:Math.max(0,pts.length-2),before_bends:before,short_inner_segments:short,node_bbox_crossings:afterCrossings,new_bbox_crossings:afterCrossings.filter(id=>!priorCrossings.includes(id))});
  }
  const style=document.createElementNS('http://www.w3.org/2000/svg','style');
  style.textContent='.flowchart-link{stroke-linejoin:miter!important;stroke-linecap:butt!important}.note .nodeLabel{text-align:left!important}';
  svg.appendChild(style);
  return {nodes:rects.size,edges:routes.length,diagonal_segments:0,bends:routes.reduce((s,e)=>s+e.bends,0),before_bends:routes.reduce((s,e)=>s+e.before_bends,0),straight_edges:routes.filter(e=>e.bends===0).length,short_inner_segments:routes.reduce((s,e)=>s+e.short_inner_segments.length,0),node_bbox_crossing_candidates:routes.filter(e=>e.node_bbox_crossings.length).length,approximate_anchors:[],endpoints_preserved:true,geometry_audit_required:true,routes};
}
