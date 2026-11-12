"""Explicit visual deliverables; semantic FLOW/SC/SEQ facts remain mandatory."""
import json
from pathlib import Path

POLICY = 'memory/diagram-deliverables.json'
PATHS = {'business_flow': 'outputs/business-scenario-atlas.html',
         'interaction_sequence': 'outputs/interaction-sequence-atlas.html'}

def selection(root: Path) -> dict:
    path = root / POLICY
    if path.is_symlink():
        raise ValueError('图集交付选择不能是符号链接')
    if not path.exists():
        # Existing workspaces retain their original mandatory-diagram contract.
        return dict.fromkeys(PATHS, True)
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or value.get('contract') != 'optional-analysis-diagrams@1'
            or not isinstance(value.get('requested'), dict)
            or set(value.get('requested', {})) != set(PATHS)
            or any(type(v) is not bool for v in value['requested'].values())
            or not isinstance(value.get('requests'), list)):
        raise ValueError('图集交付选择格式无效')
    confirmed = dict.fromkeys(PATHS, False)
    for event in value['requests']:
        if (not isinstance(event, dict) or event.get('kind') not in PATHS
                or not all(isinstance(event.get(k), str) and event[k].strip() for k in ('by', 'reason', 'at'))):
            raise ValueError('图集请求缺少请求人、依据或时间')
        confirmed[event['kind']] = True
    if confirmed != value['requested']:
        raise ValueError('图集交付选择与显式请求记录不一致')
    return confirmed

def requested(root: Path, kind: str) -> bool:
    return selection(root)[kind]

def included(root: Path, relative: str) -> bool:
    return all(relative != path or requested(root, kind) for kind, path in PATHS.items())
