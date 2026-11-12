"""Validate the confirmed S1 plan and render the project's requirements ledger."""

from graphlib import CycleError, TopologicalSorter
from html import escape

from .assertions import normalize_assertion, normalize_checkpoint
from .state import normalize_task, pack, safe_id


CHANGE_LABELS = {
    "add": "新增",
    "modify": "规则修改",
    "remove": "移除",
    "technical": "技术改动",
    "verify": "仅复验",
}


def _text(row, field, label, *, optional=False):
    value = row.get(field, "" if optional else None)
    if not isinstance(value, str) or not optional and not value.strip():
        raise ValueError(f"{label} 的 {field} 必须是非空文本")
    return value.strip()


def _ids(row, field, label, *, allow_empty=False):
    values = row.get(field, [] if allow_empty else None)
    if not isinstance(values, list) or not allow_empty and not values:
        raise ValueError(f"{label} 的 {field} 缺少编号列表")
    result = [safe_id(value, f"{label} {field}") for value in values]
    if len(set(result)) != len(result):
        raise ValueError(f"{label} 的 {field} 含有重复编号")
    return result


def _rows(plan, field, key="id"):
    values = plan.get(field)
    if not isinstance(values, list) or not values:
        raise ValueError(f"需求计划缺少 {field} 记录")
    result = {}
    seen = set()
    for row in values:
        if not isinstance(row, dict):
            raise ValueError(f"{field} 的记录必须为对象")
        identifier = safe_id(row.get(key), f"{field} {key}")
        if identifier.casefold() in seen:
            raise ValueError(f"{field} 含有重复编号 {identifier}")
        seen.add(identifier.casefold())
        result[identifier] = row
    return result


def _texts(row, identifier, fields):
    return {"id": identifier, **{field: _text(row, field, identifier) for field in fields}}


def _steps(row, field, identifier):
    value = row.get(field)
    if isinstance(value, list) and value and all(isinstance(step, str) and step.strip() for step in value):
        return [step.strip() for step in value]
    return _text(row, field, identifier)


def _known(refs, available, label):
    missing = sorted(set(refs) - set(available))
    if missing:
        raise ValueError(f"{label} 引用了未登记的编号 {'、'.join(missing)}")


def _assertion_binding(value, label):
    if not isinstance(value, dict) or set(value) not in ({"case", "step"}, {"case", "step", "checkpoint"}):
        raise ValueError(f"{label} 的验收断言须填写 case 和 step，可登记 checkpoint")
    binding = {"case": safe_id(value["case"], "case ID"), "step": normalize_assertion(value["step"])}
    if "checkpoint" in value:
        binding["checkpoint"] = normalize_checkpoint(value["checkpoint"])
    return binding


def _assertions(feature):
    values = feature.get("assertions")
    label = f"功能 {feature['id']}"
    if not isinstance(values, list):
        raise ValueError(f"{label} 的 assertions 须为列表")
    results = []
    seen = set()
    for value in values:
        binding = _assertion_binding(value, label)
        _known([binding["case"]], feature["cases"], f"{label} 的验收断言")
        key = pack(binding)
        if key in seen:
            raise ValueError(f"{label} 的验收断言重复：{binding['case']}")
        seen.add(key)
        results.append(binding)
    return results


def current_work_features(plan):
    """Return active features and the removals that need proof this round."""
    removals = {change["feature_id"] for change in plan["changes"] if change["kind"] == "remove"}
    return {
        feature["id"]: feature for feature in plan["features"]
        if feature["status"] == "active" or feature["id"] in removals
    }


def _task_shape(spec):
    task = normalize_task(spec)
    identifier = task["id"]
    if not task["cases"]:
        raise ValueError(f"任务 {identifier} 至少需要一个对应功能的批准用例")
    return {
        **_texts(task, identifier, ("name", "input", "deliverable", "done")),
        "feature_ids": _ids(task, "feature_ids", identifier),
        **{field: task[field] for field in ("paths", "deps", "edges", "cases", "risk")},
    }


def normalize_plan_task(spec, plan):
    """Normalize a planned or additional execution task against the frozen features."""
    task = _task_shape(spec)
    identifier = task["id"]
    features = current_work_features(plan)
    _known(task["feature_ids"], features, f"任务 {identifier} 的本轮功能")
    cases = {case for feature_id in task["feature_ids"] for case in features[feature_id]["cases"]}
    _known(task["cases"], cases, f"任务 {identifier} 的功能用例")
    return task


def _assertion_changes(row, label):
    entries = row["assertion_changes"]
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{label} 的 assertion_changes 须为非空列表")
    changes = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) not in ({"before", "after"}, {"before", "after", "retarget_reason"}):
            raise ValueError(f"{label} 的断言变更须填写 before 和 after，可说明 retarget_reason")
        change = {
            "before": None if entry["before"] is None else _assertion_binding(entry["before"], label),
            "after": _assertion_binding(entry["after"], label),
        }
        if "retarget_reason" in entry:
            change["retarget_reason"] = _text(entry, "retarget_reason", label)
        changes.append(change)
    for side in ("before", "after"):
        bindings = [pack(change[side]) for change in changes if change[side] is not None]
        if len(bindings) != len(set(bindings)):
            raise ValueError(f"{label} 的断言变更重复绑定同一项 {side}")
    return changes


def _change(row, feature_id):
    kind = row.get("kind")
    label = f"功能 {feature_id}"
    if not isinstance(kind, str) or kind not in CHANGE_LABELS:
        raise ValueError(f"{label} 的变更类型无效")
    change = {
        "feature_id": feature_id, "kind": kind,
        "before": _text(row, "before", feature_id, optional=kind == "add"),
        "after": _text(row, "after", feature_id, optional=kind == "remove"),
    }
    if {"assertion_changes", "assertion_reuse"} & row.keys() and kind not in {"modify", "remove"}:
        raise ValueError(f"{label} 仅在修改或移除时可登记断言变更或沿用预期的确认依据")
    if "assertion_changes" in row:
        change["assertion_changes"] = _assertion_changes(row, label)
    if "assertion_reuse" in row:
        change["assertion_reuse"] = _text(row, "assertion_reuse", feature_id)
    return change


def change_assertions(changes):
    """Return the concrete change evidence to be confirmed with the S1 contract."""
    if not isinstance(changes, list):
        raise ValueError("change_assertions 须为断言变更列表")
    if not changes:
        return []
    rows = _rows({"change_assertions": changes}, "change_assertions", key="feature_id")
    result = []
    for feature_id, row in sorted(rows.items()):
        if {"assertion_changes", "assertion_reuse"} & row.keys():
            result.append(_change(row, feature_id))
    return result


def _changes(plan, features):
    changes = []
    for feature_id, row in _rows(plan, "changes", key="feature_id").items():
        _known([feature_id], features, "计划变更的功能")
        change = _change(row, feature_id)
        kind = change["kind"]
        feature = features[feature_id]
        if (kind == "remove") != (feature["status"] == "removed"):
            raise ValueError(f"功能 {feature_id} 的变更类型与功能状态不一致")
        if kind == "remove" and not feature["cases"]:
            raise ValueError(f"本轮移除的功能 {feature_id} 至少需要一个必跑用例")
        if any(pair["after"] not in feature.get("assertions", []) for pair in change.get("assertion_changes", [])):
            raise ValueError(f"功能 {feature_id} 的变更须绑定本轮已登记的验收断言")
        changes.append(change)
    return changes


def _scenes(plan):
    return {
        identifier: {
            **_texts(row, identifier, ("name", "actor", "trigger", "outcome")),
            "requirement_ids": _ids(row, "requirement_ids", identifier),
            **{field: _steps(row, field, identifier) for field in ("preconditions", "main_flow", "alternatives")},
            "depends_on": _ids(row, "depends_on", identifier, allow_empty=True),
        }
        for identifier, row in _rows(plan, "scenes").items()
    }


def _features(plan):
    features = {}
    for identifier, row in _rows(plan, "features").items():
        status = row.get("status", "active")
        if status not in ("active", "removed"):
            raise ValueError(f"功能 {identifier} 的 status 必须为 active 或 removed")
        feature = {
            **_texts(row, identifier, ("name", "rule")),
            "requirement_ids": _ids(row, "requirement_ids", identifier),
            "scene_id": safe_id(row.get("scene_id"), f"{identifier} scene_id"),
            "cases": _ids(row, "cases", identifier, allow_empty=status == "removed"), "status": status,
        }
        if "assertions" in row:
            feature["assertions"] = _assertions({**feature, "assertions": row["assertions"]})
        features[identifier] = feature
    return features


def normalize_handoff(step, plan):
    """Normalize one completed output before the downstream outputs exist."""
    if not isinstance(plan, dict):
        raise ValueError("S1 交接产物须包含 requirements 对象")
    if step == "scenes":
        return {"scenes": list(_scenes(plan).values())}
    if step == "features":
        features = _features(plan)
        return {"features": list(features.values()), "changes": _changes(plan, features)}
    if step == "tasks":
        return {"tasks": [_task_shape(row) for row in _rows(plan, "tasks").values()]}
    raise ValueError(f"无法登记该 S1 交接步骤：{step}")


def normalize_contract(records):
    return [
        _texts(row, identifier, ("name", "source", "goal", "scope", "rule", "acceptance"))
        for identifier, row in _rows({"requirements": records}, "requirements").items()
    ]


def normalize_plan(plan, mandatory_cases, *, require_assertions=False, require_checkpoints=False):
    """Return independent, validated records from the approved S1 input."""
    if not isinstance(plan, dict):
        raise ValueError("需求计划必须为对象")
    approval = _text(plan, "approval", "需求计划")
    requirements = {row["id"]: row for row in normalize_contract(plan.get("requirements"))}
    scenes = _scenes(plan)
    for identifier, scene in scenes.items():
        _known(scene["requirement_ids"], requirements, f"场景 {identifier} 的需求")
        _known(scene["depends_on"], scenes, f"场景 {identifier} 的业务依赖")
        if identifier in scene["depends_on"]:
            raise ValueError(f"场景 {identifier} 不能依赖自身")

    features = _features(plan)
    for identifier, feature in features.items():
        scene_id = feature["scene_id"]
        _known([scene_id], scenes, f"功能 {identifier} 的场景")
        _known(feature["requirement_ids"], scenes[scene_id]["requirement_ids"], f"功能 {identifier} 的场景需求")

    changes = _changes(plan, features)
    feature_rows = list(features.values())
    feature_plan = {"features": feature_rows, "changes": changes}
    work = current_work_features(feature_plan)
    if require_assertions or require_checkpoints:
        missing = [feature_id for feature_id, feature in work.items() if not feature.get("assertions")]
        if missing:
            raise ValueError(f"本轮功能须绑定至少一条已确认的可观察验收断言：{', '.join(sorted(missing))}")
    if require_checkpoints:
        for feature_id, feature in work.items():
            for binding in feature["assertions"]:
                if "checkpoint" not in binding:
                    raise ValueError(f"功能 {feature_id} 的用例 {binding['case']} 须登记验收时点 checkpoint")
    all_cases = {case for feature in work.values() for case in feature["cases"]}
    mandatory = set(mandatory_cases)
    _known(all_cases, mandatory, "本轮功能的必跑用例")
    _known(mandatory, all_cases, "基线必跑用例的功能覆盖")
    _known(requirements, {ref for feature in features.values() for ref in feature["requirement_ids"]}, "需求的功能覆盖")
    _known(scenes, {feature["scene_id"] for feature in features.values()}, "场景的功能覆盖")
    for identifier, scene in scenes.items():
        feature_requirements = {
            ref for feature in features.values() if feature["scene_id"] == identifier for ref in feature["requirement_ids"]
        }
        _known(scene["requirement_ids"], feature_requirements, f"场景 {identifier} 的需求覆盖")

    tasks = {
        identifier: normalize_plan_task(row, feature_plan)
        for identifier, row in _rows(plan, "tasks").items()
    }
    covered = {
        feature_id for task in tasks.values() for feature_id in task["feature_ids"]
        if set(task["cases"]).intersection(work[feature_id]["cases"])
    }
    _known(work, covered, "本轮功能的任务用例覆盖")
    for identifier, task in tasks.items():
        _known(task["deps"], tasks, f"任务 {identifier} 的依赖")
    try:
        tuple(TopologicalSorter({identifier: task["deps"] for identifier, task in tasks.items()}).static_order())
    except CycleError as error:
        raise ValueError(f"S1 任务依赖成环 {' → '.join(error.args[1])}") from error

    return {
        "approval": approval, "requirements": list(requirements.values()), "scenes": list(scenes.values()),
        "features": feature_rows, "tasks": list(tasks.values()), "changes": changes,
    }


def feature_anchor(identifier):
    return safe_id(identifier, "功能编号").lower()


def _cell(value):
    if isinstance(value, list):
        return "<br>".join(_cell(item) for item in value) or "—"
    return escape(value, quote=False).replace("|", r"\|").replace("\r\n", "\n").replace("\n", "<br>") or "—"


def _table(headings, rows):
    header = "| " + " | ".join(headings) + " |"
    separator = "| " + " | ".join("---" for _ in headings) + " |"
    return "\n".join([header, separator, *("| " + " | ".join(row) + " |" for row in rows)])


def _record(row, fields):
    return [_cell(row[field]) for field in fields]


def _feature_label(feature):
    return f'<a id="{feature_anchor(feature["id"])}"></a>`{feature["id"]}`'


def _assertion_cell(feature):
    return _cell([
        f"{binding['case']} {pack(binding['step'])}" + (
            f" 时点 {pack(binding['checkpoint'])}" if "checkpoint" in binding else ""
        )
        for binding in feature.get("assertions", [])
    ])


def render_plan(plan, revision, *, tasks=None):
    """Render the current approved plan. Delivery history is appended separately."""
    task_rows = list(plan["tasks"] if tasks is None else tasks)
    task_ids = {feature["id"]: [] for feature in plan["features"]}
    for task in task_rows:
        for feature_id in task["feature_ids"]:
            task_ids[feature_id].append(task["id"])

    active = [feature for feature in plan["features"] if feature["status"] == "active"]
    retired = [feature for feature in plan["features"] if feature["status"] == "removed"]
    work = current_work_features(plan)
    removing = [feature for feature in retired if feature["id"] in work]
    removed = [feature for feature in retired if feature["id"] not in work]
    scene_requirements = {}
    for feature in active:
        scene_requirements.setdefault(feature["scene_id"], set()).update(feature["requirement_ids"])
    active_requirements = {requirement for ids in scene_requirements.values() for requirement in ids}

    requirement_table = _table(
        ("需求编号", "需求", "来源", "目标", "范围及边界", "业务规则", "验收结果"),
        (_record(row, ("id", "name", "source", "goal", "scope", "rule", "acceptance"))
         for row in plan["requirements"] if row["id"] in active_requirements),
    )
    active_scenes = [
        {**row, "requirement_ids": [identifier for identifier in row["requirement_ids"]
                                   if identifier in scene_requirements[row["id"]]]}
        for row in plan["scenes"] if row["id"] in scene_requirements
    ]
    scene_table = _table(
        ("场景编号", "场景", "来源需求", "角色", "触发", "前置条件", "主流程", "分支与异常", "结束状态", "业务依赖"),
        (_record(row, ("id", "name", "requirement_ids", "actor", "trigger", "preconditions", "main_flow",
                      "alternatives", "outcome", "depends_on")) for row in active_scenes),
    )
    feature_fields = ("功能编号", "功能点", "来源需求", "所属场景", "业务规则", "任务", "用例")
    show_assertions = any(row.get("assertions") for row in active)
    if show_assertions:
        feature_fields += ("确认断言",)
    feature_rows = []
    for row in active:
        cells = [
            _feature_label(row),
            *_record(row, ("name", "requirement_ids", "scene_id", "rule")),
            _cell(task_ids[row["id"]]), _cell(row["cases"]),
        ]
        if show_assertions:
            cells.append(_assertion_cell(row))
        cells.append("有效")
        feature_rows.append(cells)
    feature_table = _table((*feature_fields, "状态"), feature_rows)
    task_table = _table(
        ("任务编号", "任务", "覆盖功能", "输入", "交付物", "完成条件", "写入范围", "硬依赖", "契约", "用例", "风险"),
        ([*_record(row, ("id", "name", "feature_ids", "input", "deliverable", "done", "paths", "deps", "edges", "cases")),
          "高" if row["risk"] else "常规"] for row in task_rows),
    )
    change_table = _table(
        ("功能编号", "本轮计划类型", "变更前", "变更后"),
        ([f'[{row["feature_id"]}](#{feature_anchor(row["feature_id"])})', CHANGE_LABELS[row["kind"]],
          *_record(row, ("before", "after"))] for row in plan["changes"]),
    )
    sections = [
        "# 需求台账", f"- 需求基线 {_cell(revision)}\n- 确认依据 {_cell(plan['approval'])}",
        "## 需求分析", requirement_table, "## 业务场景", scene_table, "## 功能点", feature_table,
    ]
    if removing:
        sections.extend(("## 本轮移除验证", _table(
            ("功能编号", "功能点", "任务", "必跑用例", "移除断言"),
            ([_feature_label(row), _cell(row["name"]), _cell(task_ids[row["id"]]),
              _cell(row["cases"]), _assertion_cell(row)] for row in removing),
        )))
    if removed:
        sections.extend(("## 已移除功能索引", _table(
            ("功能编号", "功能点"), ([_feature_label(row), _cell(row["name"])] for row in removed),
        )))
    sections.extend(("## 实施任务", task_table, "## 本轮计划变更", change_table))
    return "\n\n".join(sections) + "\n"
