#!/usr/bin/env python3
"""Validate a reviewed graph and generate Mermaid; does not infer business edges."""
import argparse
import copy
import html
import json
import math
import re
import unicodedata
from collections import Counter
from pathlib import Path

KINDS = {'process', 'decision', 'terminal', 'database', 'subprocess', 'reference', 'note'}
SAFE_ID = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def norm(text):
    return re.sub(r'\s+', '', text)


def label(text, width):
    lines = []
    for raw in text.split('\n'):
        line, size = '', 0
        for c in raw:
            step = 2 if unicodedata.east_asian_width(c) in 'WF' else 1
            if size + step > width:
                lines.append(line)
                line, size = '', 0
            line += c
            size += step
        lines.append(line)
    return '<br/>'.join(html.escape(x, quote=True).replace('|', '&#124;').replace('`', '&#96;') for x in lines)


def build(data, inventory=None):
    data = copy.deepcopy(data)
    require(isinstance(data.get('title'), str) and data['title'].strip(), 'title is required')
    nodes, edges = data.get('nodes', []), data.get('edges', [])
    require(isinstance(nodes, list) and bool(nodes) and all(isinstance(n, dict) for n in nodes), 'nodes must be non-empty objects')
    require(isinstance(edges, list) and all(isinstance(e, dict) for e in edges), 'edges must be objects')
    for collection in (nodes, edges):
        ids = [x.get('id', '') for x in collection]
        require(len(ids) == len(set(ids)), 'Duplicate graph IDs')
        require(all(isinstance(x, str) and SAFE_ID.fullmatch(x) for x in ids), 'IDs must match [A-Za-z][A-Za-z0-9_]*')
    by = {n['id']: n for n in nodes}
    inv = None
    if inventory is not None:
        items = inventory.get('nodes', []) + inventory.get('links', [])
        require(len(items) == len({x['id'] for x in items}), 'Duplicate source inventory IDs')
        inv = {x['id']: x for x in items}
    used = []

    def sources(item, required=True):
        ids = item.setdefault('source_ids', [])
        require(isinstance(ids, list) and (bool(ids) or not required), 'Missing source_ids: ' + item['id'])
        require(all(isinstance(s, str) for s in ids), 'Source IDs must be strings')
        used.extend(ids)
        if inv is not None:
            require(all(i in inv for i in ids), 'Unknown source ID in ' + item['id'])
            expected = [inv[i].get('text', '') for i in ids]
            if 'source_texts' not in item:
                item['source_texts'] = expected
            require([norm(t) for t in item['source_texts']] == [norm(t) for t in expected], 'Source text mismatch: ' + item['id'])
        else:
            item.setdefault('source_texts', [item['text']] if len(ids) == 1 else [])
        require(len(item['source_texts']) == len(ids), 'source_ids/source_texts length mismatch: ' + item['id'])
        if required:
            same = norm(item['text']) == norm('\n'.join(item['source_texts']))
            require(same or bool(item.get('display_reason')), 'Changed display text needs display_reason: ' + item['id'])

    data.setdefault('issues', [])
    data.setdefault('exclusions', [])
    data.setdefault('revision_notes', [])
    data.setdefault('phases', [])
    data.setdefault('page_id', '')
    data.setdefault('source_url', '')
    data.setdefault('navigation', [])
    for n in nodes:
        require(n.get('kind') in KINDS, 'Unsupported node kind: ' + n['id'])
        require(isinstance(n.get('text'), str) and n['text'].strip(), 'Empty node text: ' + n['id'])
        sources(n)
        if n['kind'] == 'note':
            n.setdefault('note_no', 'R' + str(sum(x.get('note_no') is not None for x in nodes) + 1).zfill(2))
        require('note_no' not in n or isinstance(n['note_no'], str), 'note_no must be a string: ' + n['id'])
        n['mermaid_id'] = 'n_' + n['id']
        n['display_text'] = ((n.get('note_no', '') + ' · ') if n.get('note_no') else '') + n['text']
        n.setdefault('phase', '')
        for k in ('x', 'y', 'w', 'h'):
            n.setdefault(k, None)
            value = n[k]
            require(value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)), k + ' must be a finite number or null: ' + n['id'])
        if n.get('anchor'):
            require(n['kind'] == 'note' and n['anchor'] in by, 'Invalid note anchor: ' + n['id'])
    for e in edges:
        require(e.get('kind') in ('flow', 'annotation'), 'Navigation must be stored separately: ' + e['id'])
        require(e.get('source') in by and e.get('target') in by, 'Missing edge endpoint: ' + e['id'])
        require(isinstance(e.get('evidence'), str) and e['evidence'].strip(), 'Missing edge evidence: ' + e['id'])
        e.setdefault('text', '')
        require(isinstance(e['text'], str), 'Edge text must be a string')
        e.setdefault('direction', 'forward' if e['kind'] == 'flow' else 'none')
        require(e['direction'] in ('forward', 'both', 'none'), 'Invalid direction: ' + e['id'])
        if e['kind'] == 'annotation':
            require(e['direction'] == 'none', 'Annotation is a reading association, not an execution arrow')
            require(by[e['source']]['kind'] == 'note' or by[e['target']]['kind'] == 'note', 'Annotation edge must touch a note')
        sources(e, e['kind'] == 'flow')
        e['mermaid_id'] = 'e_' + e['id']
    for n in nodes:
        if n.get('anchor'):
            require(any(e['kind'] == 'annotation' and {e['source'], e['target']} == {n['id'], n['anchor']} for e in edges), 'Anchor needs matching annotation edge: ' + n['id'])
    data['jumps'] = data['navigation']
    for n in nodes:
        n['jumps'] = []
    for j in data['navigation']:
        require(j.get('source') in by, 'Invalid navigation source')
        require(j.get('target') is None or j['target'] in by, 'Invalid navigation target')
        require(bool(j.get('evidence')), 'Navigation needs evidence')
        if j.get('target'):
            by[j['source']]['jumps'].append(j['target'])
        else:
            data['issues'].append({'kind': 'missing_jump_target', 'ids': [j['source']], 'detail': j.get('detail', '原型跳转去向待确认。')})
    phase_ids = [p.get('id') for p in data['phases']]
    require(len(phase_ids) == len(set(phase_ids)) and all(isinstance(i, str) and SAFE_ID.fullmatch(i) for i in phase_ids), 'Invalid phase IDs')
    for phase in data['phases']:
        require(phase.get('entry') in by and isinstance(phase.get('title'), str), 'Invalid phase entry/title')
    require(all(not n['phase'] or n['phase'] in phase_ids for n in nodes), 'Unknown node phase')
    data.setdefault('start', nodes[0]['id'])
    require(data['start'] in by, 'Invalid start node')
    connected = {e[k] for e in edges if e['kind'] == 'flow' for k in ('source', 'target')}
    navigated = {j[k] for j in data['navigation'] for k in ('source', 'target') if j.get(k)}
    for n in nodes:
        n['unconnected'] = n['kind'] != 'note' and n['id'] not in connected and n['id'] not in navigated
        if n['unconnected']:
            data['issues'].append({'kind': 'unconnected', 'ids': [n['id']], 'detail': '来源中未确认此节点的业务连线；已保留节点。'})
    exclusions = data['exclusions']
    require(all(isinstance(x.get('reason'), str) and x['reason'].strip() and isinstance(x.get('id'), str) for x in exclusions), 'Exclusion requires id and reason')
    accounted = used + [x['id'] for x in exclusions]
    duplicates = [i for i, count in Counter(accounted).items() if count > 1]
    require(not duplicates, 'Source ID used more than once: ' + ', '.join(duplicates))
    coverage = {'status': 'not_checked', 'reason': 'No source inventory supplied'}
    if inv is not None:
        missing, extra = sorted(set(inv) - set(accounted)), sorted(set(accounted) - set(inv))
        require(not missing and not extra, 'Inventory mismatch; missing=' + str(missing) + '; extra=' + str(extra))
        coverage = {'status': 'passed', 'source_items': len(inv), 'mapped': len(used), 'excluded': len(exclusions)}
    cfg = {'direction': 'TB', 'nodeSpacing': 60, 'rankSpacing': 85}
    cfg.update(data.get('layout', {}))
    require(cfg['direction'] in ('TB', 'LR'), 'Supported directions: TB, LR')
    require(all(isinstance(cfg[k], (int, float)) and 10 <= cfg[k] <= 1000 for k in ('nodeSpacing', 'rankSpacing')), 'Spacing must be between 10 and 1000')
    data['layout'] = cfg
    data['stats'] = {'logical_nodes': len(nodes), 'flow_nodes': sum(n['kind'] != 'note' for n in nodes), 'annotations': sum(n['kind'] == 'note' for n in nodes), 'original_edges': sum(e['kind'] == 'flow' for e in edges), 'annotation_links': sum(e['kind'] == 'annotation' for e in edges), 'rendered_edges': len(edges), 'navigation_links': len(data['navigation'])}
    data['source_coverage'] = coverage
    lines = ['---', 'config:', '  layout: elk', '  flowchart:', '    curve: linear', '    nodeSpacing: ' + str(cfg['nodeSpacing']), '    rankSpacing: ' + str(cfg['rankSpacing']), '---', 'flowchart ' + cfg['direction'], '%% 全流程与旁注位于同一画布；导航不是业务边。']
    for n in nodes:
        form = {'decision': ('{"', '"}'), 'terminal': ('(["', '"])'), 'database': ('[("', '")]'), 'subprocess': ('[["', '"]]')}.get(n['kind'], ('["', '"]'))
        lines.append('  ' + n['mermaid_id'] + form[0] + label(n['display_text'], 52 if n['kind'] == 'note' else 30) + form[1] + ':::' + n['kind'])
    for e in edges:
        arrow = '-.-' if e['kind'] == 'annotation' else {'forward': '-->', 'both': '<-->', 'none': '---'}[e['direction']]
        middle = arrow + ('|"' + label(e['text'], 48) + '"|' if e['text'] else '')
        lines.append('  ' + by[e['source']]['mermaid_id'] + ' ' + e['mermaid_id'] + '@' + middle + ' ' + by[e['target']]['mermaid_id'])
    lines.extend(['  classDef process fill:#ffffff,stroke:#77879b,color:#213047,stroke-width:1.3px', '  classDef decision fill:#f0f5fc,stroke:#698ab4,color:#183351,stroke-width:1.3px', '  classDef terminal fill:#e8f2ed,stroke:#6a9785,color:#1f4c3e', '  classDef database fill:#eff1fa,stroke:#8d8fb3,color:#38355c', '  classDef subprocess fill:#eaf3f5,stroke:#6e9aa5,color:#24454e', '  classDef reference fill:#f8f2df,stroke:#b8a263,color:#5b4b23', '  classDef note fill:#fff8ed,stroke:#d0a267,color:#574326,stroke-dasharray:4 4'])
    for i, e in enumerate(edges):
        if e['kind'] == 'annotation':
            lines.append('  linkStyle ' + str(i) + ' stroke:#b69a76,stroke-width:1px')
    return data, '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True)
    p.add_argument('--inventory')
    p.add_argument('--out', required=True)
    args = p.parse_args()
    data = json.loads(Path(args.input).read_text(encoding='utf-8-sig'))
    inv = json.loads(Path(args.inventory).read_text(encoding='utf-8-sig')) if args.inventory else None
    data, mmd = build(data, inv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'graph.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / '完整流程.mmd').write_text(mmd, encoding='utf-8')
    print(json.dumps({'stats': data['stats'], 'source_coverage': data['source_coverage']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
