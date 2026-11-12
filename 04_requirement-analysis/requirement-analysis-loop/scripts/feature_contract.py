"""Structural checks for business-operation scope; semantic granularity needs G3 review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


GRANULARITY_VERSION = "business-operation@2"
BOUNDARY_TEXT_FIELDS = {
    "business_object": "业务对象",
    "primary_action": "单一主操作",
    "system_boundary": "系统与交付责任边界",
    "initiator": "业务发起方",
    "trigger": "触发事件",
    "primary_outcome": "主要业务目的",
    "completion_result": "完成结果",
    "split_rationale": "拆分／合并依据",
}
BOUNDARY_LIST_FIELDS = {
    "included_scope": "包含范围",
    "excluded_scope": "不包含范围",
    "business_rules": "处理规则",
    "exception_paths": "异常范围",
    "dependencies": "依赖条件",
    "assumptions": "假设与待确认边界",
}


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def text_list(value: Any, required: bool = False) -> bool:
    return isinstance(value, list) and (bool(value) or not required) and all(nonempty_text(v) for v in value)


def feature_boundary_errors(data: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["feature-list.json 必须为对象"]
    if data.get("granularity_version") != GRANULARITY_VERSION:
        errors.append(f"功能清单须声明 granularity_version={GRANULARITY_VERSION} 并完成边界复核")
    features = data.get("features")
    if not isinstance(features, list):
        return errors + ["feature-list.json 的 features 必须为数组"]
    for item in features:
        if not isinstance(item, dict):
            errors.append("features 包含非对象记录")
            continue
        fid = str(item.get("id") or "未知功能")
        if item.get("change_type") not in {"new", "iteration"}:
            errors.append(f"{fid} 的 change_type 只允许 new 或 iteration")
        if not text_list(item.get("source_acceptance_ids"), required=True):
            errors.append(f"{fid} 的 source_acceptance_ids 必须为非空验收 ID 数组")
        boundary = item.get("boundary")
        if not isinstance(boundary, dict):
            errors.append(f"{fid} 缺少 boundary 业务过程边界")
        else:
            for field in BOUNDARY_TEXT_FIELDS:
                if not nonempty_text(boundary.get(field)):
                    errors.append(f"{fid} 的 boundary.{field} 必须为非空文本")
            if isinstance(boundary.get("primary_action"), str) and boundary["primary_action"].strip() in {
                "管理", "综合管理", "生命周期管理", "全生命周期管理", "业务处理", "全流程处理",
            }:
                errors.append(f"{fid} 的 primary_action 是管理包概述，须明确单一可验收业务操作并复核是否拆分")
            for field in BOUNDARY_LIST_FIELDS:
                if not text_list(boundary.get(field), required=field == "included_scope"):
                    errors.append(f"{fid} 的 boundary.{field} 必须为文本数组，包含范围不得为空；已确认无内容时其余数组可为 []")
        change = item.get("change_detail")
        if not isinstance(change, dict):
            errors.append(f"{fid} 缺少 change_detail 版本差异")
            continue
        if item.get("change_type") == "iteration" and not nonempty_text(change.get("before")):
            errors.append(f"{fid} 迭代必须写明 change_detail.before 原版本行为及版本／基线定位")
        if item.get("change_type") == "new" and ("before" not in change or change["before"] is not None):
            errors.append(f"{fid} 新增功能的 change_detail.before 必须为 null")
        if not nonempty_text(change.get("after")):
            errors.append(f"{fid} 的 change_detail.after 必须描述本版本交付结果")
        if not text_list(change.get("impact"), required=True):
            errors.append(f"{fid} 的 change_detail.impact 必须为非空文本数组，说明本轮新增／改变的范围")
    return errors


def load_acceptance(root: Path) -> dict[str, str]:
    stories = json.loads((root / "memory/normalized-user-stories.json").read_text(encoding="utf-8")).get("user_stories", [])
    return {ac["id"]: ac.get("statement", "") for story in stories for ac in story.get("acceptance_criteria", [])}


def feature_view_marker(data: Any, acceptance: dict[str, str]) -> str:
    refs = {aid for feature in data.get("features", []) if isinstance(feature, dict)
            for aid in feature.get("source_acceptance_ids", [])}
    facts = {"feature_list": data, "acceptance": {aid: acceptance.get(aid) for aid in sorted(refs)}}
    encoded = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"<!-- feature-facts-sha256: {hashlib.sha256(encoded).hexdigest()} -->"


def feature_artifact_errors(root: Path) -> list[str]:
    try:
        data = json.loads((root / "memory/feature-list.json").read_text(encoding="utf-8"))
        errors = feature_boundary_errors(data)
        if errors:
            return errors
        view = (root / "outputs/feature-list.md").read_text(encoding="utf-8")
        if feature_view_marker(data, load_acceptance(root)) not in view:
            errors.append("功能清单视图未绑定当前功能、范围及验收事实，请重新生成 outputs/feature-list.md")
        return errors
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [f"功能清单边界核验失败：{exc}"]
