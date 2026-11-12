"""Bind each business SVG to its source facts and actual SVG content."""
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
ET.register_namespace('', 'http://www.w3.org/2000/svg')

def fact_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()

def content_hash(svg):
    node = ET.fromstring(svg) if isinstance(svg, str) else svg
    node.attrib.pop('data-business-svg-sha256', None)
    return hashlib.sha256(ET.tostring(node, encoding='utf-8')).hexdigest()

def bind_business_svg(svg, facts):
    node = ET.fromstring(svg)
    node.set('data-business-facts-sha256', fact_hash(facts))
    digest = content_hash(node)
    node.set('data-business-svg-sha256', digest)
    return ET.tostring(node, encoding='unicode')

def business_html_errors(root: Path):
    errors = []
    try:
        html = (root / 'outputs/business-scenario-atlas.html').read_text()
        expected = {}
        for flow in json.loads((root / 'memory/business-rules.json').read_text()).get('business_flows', []):
            expected[('business-process', flow['id'])] = flow
        for item in json.loads((root / 'scenarios/scenario-index.json').read_text())['scenarios']:
            if item.get('scope', 'in_scope') != 'in_scope':
                continue
            path = (root / item['file']).resolve()
            if not path.is_relative_to(root.resolve()):
                raise ValueError('业务图来源越界')
            expected[('swimlane', item['id'])] = json.loads(path.read_text())
        seen = set()
        for raw in re.findall(r'<svg\b.*?</svg\s*>', html, flags=re.S | re.I):
            svg = ET.fromstring(raw)
            kind = svg.get('data-diagram-type')
            if kind not in {'business-process', 'swimlane'}:
                continue
            key = (kind, svg.get('data-source-id'))
            renderer = svg.get('data-renderer', 'native')
            identity = (*key, renderer)
            if key not in expected or identity in seen:
                errors.append('业务图 ID 未知或重复：' + str(identity))
                continue
            seen.add(identity)
            if svg.get('data-business-facts-sha256') != fact_hash(expected[key]):
                errors.append(str(key) + ' 业务图与源事实不一致，须从 S3 重新生成')
            digest = svg.get('data-business-svg-sha256')
            if not digest or digest != content_hash(svg):
                errors.append(str(key) + ' 业务 SVG 内容缺失或已改变')
            visible = [n for n in svg.iter() if n.tag.split('}')[-1] in
                       {'rect', 'path', 'polygon', 'polyline', 'line', 'ellipse', 'circle', 'text'}]
            if not visible:
                errors.append(str(key) + ' 正式图没有有效图形内容')
        for key in expected:
            if (*key, 'plantuml') not in seen:
                errors.append(str(key) + ' 缺少正式 PlantUML 图')
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
        errors.append('业务图核验失败：' + str(exc))
    return errors
