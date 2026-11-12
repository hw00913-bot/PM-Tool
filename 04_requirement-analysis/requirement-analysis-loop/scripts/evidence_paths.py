"""Locate preserved evidence after an explicitly recorded project relocation.

The manifest changes lookup only. Callers still verify the original source
bytes, SHA bindings, quotations and scope; this module never edits evidence or
turns a relocation into an approval.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class EvidencePathError(ValueError):
    pass


def _plain_text(value):
    return (isinstance(value, str) and bool(value) and value == value.strip()
            and not any(ord(c) < 32 or ord(c) == 127 for c in value)
            and '\\' not in value)


def _absolute(value, label):
    if not _plain_text(value) or not value.startswith('/') or value.startswith('//'):
        raise EvidencePathError('Invalid relocation ' + label)
    path = Path(value)
    if '..' in path.parts:
        raise EvidencePathError('Traversal in relocation ' + label)
    return path


def _relative(value):
    if (not _plain_text(value) or value.startswith('/')
            or any(part in ('', '.', '..') for part in value.split('/'))
            or ':' in value or '?' in value or '#' in value):
        raise EvidencePathError('Invalid relocation target; a project-relative path is required')
    return Path(value)


class EvidencePathResolver:
    """Use the current workspace's ancestor manifest, never an old project's.

    A recorded source prefix is authoritative even if the old file still exists.
    Multiple matching prefixes are accepted only when they produce the same
    physical target. Missing/unsafe mapped files never fall back to old paths.
    With no manifest the established evidence lookup remains unchanged.
    """
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.project_root = None
        self.original_root = None
        self.mappings = []
        self.error = None
        try:
            self._load()
        except (EvidencePathError, OSError, ValueError) as exc:
            self.error = str(exc)

    def _load(self):
        found = []
        required = []
        for ancestor in (self.workspace, *self.workspace.parents):
            ledger = ancestor / 'memory/project-versions.json'
            if ledger.is_file():
                data = json.loads(ledger.read_text(encoding='utf-8'))
                if isinstance(data, dict) and data.get('directory_layout') == {'schema': 'version-bundle@1'}:
                    if ledger.is_symlink() or ledger.resolve().parent.parent != ancestor:
                        raise EvidencePathError('Version-bundle ledger must be a local project file')
                    required.append(ancestor)
            manifest = ancestor / 'memory/path-relocations.json'
            if manifest.exists() or manifest.is_symlink():
                found.append((ancestor, manifest))
        if len(required) > 1:
            raise EvidencePathError('Ambiguous ancestor version-bundle projects')
        if required and not any(root == required[0] for root, _ in found):
            raise EvidencePathError('Version-bundle project requires its own path-relocations.json')
        if not found:
            return
        if len(found) != 1:
            raise EvidencePathError('Ambiguous ancestor relocation manifests')
        physical_root, manifest = found[0]
        if (manifest.is_symlink() or not manifest.is_file()
                or manifest.resolve().parent.parent != physical_root):
            raise EvidencePathError('Relocation manifest must be a local project file')
        data = json.loads(manifest.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema') != 'path-relocations@1':
            raise EvidencePathError('Unsupported relocation manifest schema')
        original_root = _absolute(data.get('project_root'), 'project root')
        mappings = data.get('mappings')
        if not isinstance(mappings, list) or not mappings:
            raise EvidencePathError('Relocation mappings must be a non-empty array')
        seen = set()
        for item in mappings:
            if not isinstance(item, dict):
                raise EvidencePathError('Invalid relocation mapping')
            source = _absolute(item.get('from'), 'source')
            if not source.is_relative_to(original_root):
                raise EvidencePathError('Relocation source is outside the declared original project')
            if source in seen:
                raise EvidencePathError('Duplicate relocation source prefix')
            seen.add(source)
            target = physical_root / _relative(item.get('to'))
            if not target.resolve().is_relative_to(physical_root):
                raise EvidencePathError('Relocation target escapes the current project')
            self.mappings.append((source, target))
        self.project_root = physical_root
        self.original_root = original_root

    def resolve(self, value, *, internal=False, must_exist=True):
        if self.error:
            raise EvidencePathError(self.error)
        if not isinstance(value, str) or not value.strip() or value.startswith(('https://', 'http://')):
            return None
        raw = value.split('#', 1)[0]
        if not raw or any(ord(c) < 32 or ord(c) == 127 for c in raw) or '\\' in raw:
            return None
        # Normalize lexically before matching; resolving symlinks first could
        # silently follow the original project's compatibility links.
        lexical = Path(os.path.abspath(self.workspace / raw))
        matches = []
        for source, target in self.mappings:
            if lexical.is_relative_to(source):
                candidate = (target / lexical.relative_to(source)).resolve()
                if not candidate.is_relative_to(self.project_root):
                    raise EvidencePathError('Mapped evidence escapes the current project')
                matches.append(candidate)
        if matches:
            if len(set(matches)) != 1:
                raise EvidencePathError('Ambiguous evidence relocation targets')
            candidate = matches[0]
        else:
            if self.original_root and lexical.is_relative_to(self.original_root):
                # An unmapped historical project path cannot read the original
                # checkout. Already-current workspace/target paths remain valid
                # when a real migration retains the original project root.
                current = (lexical.is_relative_to(self.workspace)
                           or any(lexical.is_relative_to(target) for _, target in self.mappings))
                if self.original_root != self.project_root or not current:
                    return None
            candidate = lexical.resolve()
            if (self.project_root and lexical.is_relative_to(self.project_root)
                    and not candidate.is_relative_to(self.project_root)):
                raise EvidencePathError('Evidence symlink escapes the current project')
        if internal and not candidate.is_relative_to(self.workspace):
            return None
        return candidate if not must_exist or candidate.is_file() else None
