"""Validate optional input provenance without interpreting Wiki/query bodies."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


SOURCE_INDEX = "inputs/source-index.json"
SOURCE_STATUSES = {"verified", "partial", "missing", "conflict"}


def inspect_source_index(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Legacy/no-Wiki projects may omit the index; Wiki snapshots must be registered."""
    root = root.resolve()
    path = root / SOURCE_INDEX
    errors: list[str] = []
    sources: list[dict[str, Any]] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return [], [f"{SOURCE_INDEX} 无法读取：{exc}"]
        if not isinstance(data, dict) or data.get("schema_version") != "1.0" or not isinstance(data.get("sources"), list):
            return [], [f"{SOURCE_INDEX} 必须包含 schema_version=1.0 和 sources 数组"]
        for item in data["sources"]:
            if not isinstance(item, dict):
                errors.append(f"{SOURCE_INDEX} 的 sources 包含非对象记录")
            else:
                sources.append(item)

    registered: set[Path] = set()
    seen: set[str] = set()
    for source in sources:
        source_id = str(source.get("id", ""))
        if not re.fullmatch(r"SRC-\d{3,}", source_id) or source_id in seen:
            errors.append(f"来源 ID 不合法或重复：{source_id!r}")
        seen.add(source_id)
        for key in ("type", "title"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                errors.append(f"{source_id} 缺少 {key}")
        if source.get("type") != "llm_wiki":
            continue
        for key in ("wiki_ref", "version", "received_at"):
            if not isinstance(source.get(key), str) or not source[key].strip():
                errors.append(f"{source_id} 缺少 {key}（未知版本或日期写 unknown）")
        if "raw_ref" not in source or (source["raw_ref"] is not None and (not isinstance(source["raw_ref"], str) or not source["raw_ref"].strip())):
            errors.append(f"{source_id} 的 raw_ref 必须为非空文本或 null")
        status = source.get("status")
        if not isinstance(status, str) or status not in SOURCE_STATUSES:
            errors.append(f"{source_id} 的 status 必须为 verified/partial/missing/conflict")
        coverage = source.get("coverage")
        if not isinstance(coverage, list) or not coverage or any(not isinstance(item, str) or not item.strip() for item in coverage):
            errors.append(f"{source_id} 缺少明确的 coverage 数组")
        snapshot = source.get("snapshot_path")
        if snapshot is None and status == "missing":
            pass
        elif not isinstance(snapshot, str) or not snapshot.strip():
            errors.append(f"{source_id} 缺少 snapshot_path")
        else:
            candidate = (root / snapshot).resolve()
            if not snapshot.startswith("inputs/") or not candidate.is_relative_to(root / "inputs"):
                errors.append(f"{source_id} 的 Wiki 快照必须在项目 inputs/ 内")
            elif not candidate.is_file():
                errors.append(f"{source_id} 的 Wiki 快照不存在：{snapshot}")
            else:
                registered.add(candidate)
        issue_ids = source.get("issue_ids", [])
        if not isinstance(issue_ids, list) or any(not isinstance(item, str) or not re.fullmatch(r"ISSUE-\d{3,}", item) for item in issue_ids):
            errors.append(f"{source_id} 的 issue_ids 必须为 ISSUE-* 数组")
        elif isinstance(status, str) and status in {"partial", "missing", "conflict"} and not issue_ids:
            errors.append(f"{source_id} 的 {status} 缺口必须关联 issue_ids")

    wiki_dir = root / "inputs" / "wiki-context"
    if wiki_dir.is_dir():
        for snapshot in wiki_dir.rglob("*"):
            if snapshot.is_file() and not snapshot.name.startswith(".") and snapshot.resolve() not in registered:
                errors.append(f"Wiki 快照未登记到 {SOURCE_INDEX}：{snapshot.relative_to(root)}")
    return sources, errors


def source_issue_errors(sources: list[dict[str, Any]], open_items: list[Any]) -> list[str]:
    if not isinstance(open_items, list):
        return ["runtime/open-items.json 的 items 必须为数组"]
    issue_ids = {str(item.get("id")) for item in open_items if isinstance(item, dict)}
    return [
        f"{source.get('id')} 引用的来源问题不存在：{issue_id}"
        for source in sources
        if source.get("type") == "llm_wiki" and isinstance(source.get("issue_ids", []), list)
        for issue_id in source.get("issue_ids", [])
        if str(issue_id) not in issue_ids
    ]
