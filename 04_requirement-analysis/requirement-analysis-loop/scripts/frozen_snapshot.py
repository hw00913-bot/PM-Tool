"""Bind verification and approval to the exact deliverable facts and review views."""
import hashlib
import json
from pathlib import Path
from diagram_policy import included

DIRECTORIES = ('inputs', 'memory', 'scenarios', 'sequences', 'interfaces', 'planning', 'outputs')
RUNTIME_FILES = ('runtime/traceability.json', 'runtime/open-items.json')

def approval_snapshot(root: Path) -> dict:
    root = root.resolve()
    paths = []
    for name in DIRECTORIES:
        directory = root / name
        if not directory.is_dir():
            raise ValueError('缺少冻结目录：' + name)
        paths.extend(p for p in directory.rglob('*') if p.is_file() and not p.name.startswith('.'))
    paths.extend(root / name for name in RUNTIME_FILES)
    entries = []
    for path in sorted(set(paths)):
        if not included(root, path.relative_to(root).as_posix()):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('冻结文件不得通过符号链接越界：' + str(path))
        raw = path.read_bytes()
        entries.append({'path': path.relative_to(root).as_posix(), 'bytes': len(raw),
                        'sha256': hashlib.sha256(raw).hexdigest()})
    project = json.loads((root / 'runtime/loop-state.json').read_text())['project']
    value = {'contract': 'analysis-approval-snapshot@1', 'project': project, 'files': entries}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return {**value, 'fingerprint': hashlib.sha256(raw).hexdigest()}

def require_approved_snapshot(root: Path, state: dict, report: dict) -> dict:
    current = approval_snapshot(root)
    if report.get('approval_snapshot') != current:
        raise ValueError('核验报告与当前交付内容不一致，须重新核验并确认 G3')
    if state.get('gates', {}).get('G3', {}).get('approval_snapshot') != current:
        raise ValueError('G3 批准版本与当前交付内容不一致，须失效重跑并重新确认 G3')
    return current
