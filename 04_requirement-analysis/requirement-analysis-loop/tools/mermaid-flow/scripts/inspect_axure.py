#!/usr/bin/env python3
"""Extract an Axure ZIP safely, or inventory static page evidence. Python stdlib only."""
import argparse
import hashlib
import json
import re
import stat
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def local_path(root, base, ref):
    parsed = urlsplit(ref)
    if parsed.scheme or parsed.netloc:
        raise ValueError('Only local relative assets are supported: ' + ref)
    p = (base / unquote(parsed.path)).resolve()
    p.relative_to(root.resolve())
    return p


def prepare(args):
    source, dest = Path(args.input).resolve(), Path(args.out).resolve()
    if dest.exists():
        raise ValueError('Output must be a new directory: ' + str(dest))
    entries, names = [], set()
    with zipfile.ZipFile(source) as archive:
        total = sum(i.file_size for i in archive.infolist())
        if total > args.max_bytes:
            raise ValueError('Archive exceeds --max-bytes; inspect before raising the limit.')
        for item in archive.infolist():
            name = item.filename
            if not item.flag_bits & 0x800:
                try:
                    name = name.encode('cp437').decode('utf-8')
                except (UnicodeError, ValueError):
                    pass
            name = name.replace('\\', '/')
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or not p.parts or ':' in p.parts[0]:
                raise ValueError('Unsafe ZIP entry: ' + name)
            if '__MACOSX' in p.parts or any(x.startswith('._') for x in p.parts):
                continue
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('Symlink entry is not accepted: ' + name)
            # casefold prevents accidental Windows/macOS filename collisions.
            key = str(p).casefold().rstrip('/')
            if key in names:
                raise ValueError('Duplicate archive destination: ' + name)
            names.add(key)
            entries.append((item, p))
        dest.mkdir(parents=True)
        for item, p in entries:
            target = dest.joinpath(*p.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as inp, target.open('wb') as out:
                    while True:
                        block = inp.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
    roots = sorted(str(p.parent.parent.relative_to(dest)) for p in dest.rglob('document.js') if p.parent.name == 'data')
    with source.open('rb') as f:
        digest = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    manifest = {'source_name': source.name, 'sha256': digest.hexdigest(), 'files': sum(not i.is_dir() for i, _ in entries), 'root_candidates': roots}
    write_json(dest / '_source_manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False))


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.widgets, self.links, self.styles = [], [], [], []
        self.last_comment = ''

    def handle_comment(self, data):
        self.last_comment = data.strip()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and attrs.get('rel') == 'stylesheet' and attrs.get('href'):
            self.links.append(attrs['href'])
        widget = next((f['widget'] for f in reversed(self.stack) if f.get('widget')), None)
        if 'ax_default' in attrs.get('class', '').split() and attrs.get('id'):
            widget = {'id': attrs['id'], 'class': attrs.get('class', ''), 'source_type': self.last_comment, 'text_parts': [], 'images': [], 'inline_style': attrs.get('style', ''), 'paint_order': len(self.widgets)}
            self.widgets.append(widget)
        is_text = attrs.get('id', '').endswith('_text') or any(f.get('is_text') for f in self.stack)
        if widget and tag in ('br', 'p', 'li', 'tr') and is_text:
            widget['text_parts'].append('\n')
        if widget and tag in ('td', 'th') and is_text:
            widget['text_parts'].append('\t')
        if widget and tag == 'img' and attrs.get('src'):
            widget['images'].append(attrs['src'])
        if tag not in ('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'):
            self.stack.append({'tag': tag, 'widget': widget, 'is_text': is_text})

    def handle_endtag(self, tag):
        if tag in ('p', 'li', 'tr') and self.stack and self.stack[-1].get('widget') and self.stack[-1].get('is_text'):
            self.stack[-1]['widget']['text_parts'].append('\n')
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1].get('is_text') and self.stack[-1].get('widget'):
            self.stack[-1]['widget']['text_parts'].append(data)


def declarations(css):
    return dict(re.findall(r'([\w-]+)\s*:\s*([^;{}]+)', css))


def page(args):
    root = Path(args.root).resolve()
    html = local_path(root, root, args.html)
    parser = PageParser()
    parser.feed(html.read_text(encoding='utf-8-sig'))
    css, warnings = {}, []
    for ref in parser.links:
        try:
            content = local_path(root, html.parent, ref).read_text(encoding='utf-8-sig')
        except (ValueError, OSError) as e:
            warnings.append(str(e))
            continue
        for match in re.finditer(r'#([A-Za-z_][\w-]*)\s*\{([^{}]*)\}', content):
            css.setdefault(match[1], {}).update(declarations(match[2]))
    nodes, links = [], []
    for item in parser.widgets:
        item['text'] = re.sub(r'\n[ \t]*\n+', '\n', ''.join(item.pop('text_parts'))).replace('\xa0', ' ').strip()
        style = dict(css.get(item['id'], {}))
        style.update(declarations(item.pop('inline_style')))
        item['style'], item['shape_style'] = style, css.get(item['id'] + '_div', {})
        for key, prop in [('x', 'left'), ('y', 'top'), ('w', 'width'), ('h', 'height')]:
            v = style.get(prop, '')
            item[key] = float(v[:-2]) if re.fullmatch(r'-?\d+(?:\.\d+)?px', v) else None
        segments = [ref for ref in item['images'] if '_seg' in ref and urlsplit(ref).path.lower().endswith('.svg')]
        item['svg_segments'] = []
        for ref in segments:
            try:
                path = local_path(root, html.parent, ref)
                svg = ET.fromstring(path.read_bytes().lstrip(b'\xef\xbb\xbf'))
                item['svg_segments'].append({'file': ref, 'viewBox': svg.get('viewBox'), 'paths': [dict(p.attrib) for p in svg.iter() if p.tag.rsplit('}', 1)[-1] == 'path']})
            except (OSError, ValueError, ET.ParseError) as e:
                warnings.append(str(e))
        (links if segments else nodes).append(item)
    result = {'page': args.html, 'nodes': nodes, 'links': links, 'warnings': warnings, 'boundary': 'Static candidates only; verify visibility, transforms, arrow direction and semantics against the source.'}
    write_json(args.out, result)
    print(json.dumps({'nodes': len(nodes), 'links': len(links), 'warnings': len(warnings)}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('prepare')
    s.add_argument('--input', required=True)
    s.add_argument('--out', required=True)
    s.add_argument('--max-bytes', type=int, default=2 * 1024 ** 3)
    s.set_defaults(func=prepare)
    s = sub.add_parser('page')
    s.add_argument('--root', required=True)
    s.add_argument('--html', required=True)
    s.add_argument('--out', required=True)
    s.set_defaults(func=page)
    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
