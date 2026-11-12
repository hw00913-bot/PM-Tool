"""Project verified 04 facts and pin the local S1 interpretation."""

from copy import deepcopy

from .state import safe_id


def _index(rows, label):
    if not isinstance(rows, list):
        raise ValueError(f"{label}须为列表")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label}须为对象记录")
        identifier = safe_id(row.get("id"), label)
        if identifier in result:
            raise ValueError(f"{label}含有重复编号：{identifier}")
        result[identifier] = row
    return result


def _entry(facts, name, field):
    try:
        path = facts["handoff"]["entrypoints"][name]["path"]
        data = facts["payloads"][path]
        return data[field]
    except (KeyError, TypeError) as error:
        raise ValueError(f"04 冻结包缺少已核验的结构化事实：{name}/{field}") from error


def project(bundle):
    """Return business facts with source IDs, without fabricating an executable S1."""
    facts = bundle["facts"]
    stories = _index(_entry(facts, "normalized_user_stories", "user_stories"), "04 用户故事")
    scenarios = _index(_entry(facts, "scenario_index", "scenarios"), "04 场景")
    features = _index(_entry(facts, "feature_list", "features"), "04 功能")
    pages = _index(_entry(facts, "page_index", "pages"), "04 页面")
    feature_path = facts["handoff"]["entrypoints"]["feature_list"]["path"]

    requirements = [
        {"id": identifier, "name": story["title"], **{
            key: story[key] for key in ("goal", "role", "trigger", "status", "source_refs", "acceptance_criteria")
            if key in story
        }}
        for identifier, story in stories.items()
    ]
    scene_rows = []
    for identifier, scenario in scenarios.items():
        detail = facts["payloads"].get(scenario["file"])
        if not isinstance(detail, dict) or detail.get("id") != identifier:
            raise ValueError(f"04 缺少已核验的场景详情：{identifier}")
        scene_rows.append({
            "id": identifier, "name": scenario["name"], "source_story_ids": scenario.get("source_story_ids", []),
            "scope": scenario.get("scope", "in_scope"), "ui_surface": scenario.get("ui_surface", "required"),
            **{key: detail[key] for key in ("actors", "trigger", "preconditions", "main_flow",
                                         "alternate_flows", "postconditions", "acceptance_points") if key in detail},
        })
    feature_rows = []
    for identifier, feature in features.items():
        scene_ids = feature["source_scenario_ids"]
        feature_rows.append({
            "id": identifier, "name": feature["name"], "description": feature["description"],
            "source_story_ids": feature["source_story_ids"], "source_scenario_ids": scene_ids,
            "source_acceptance_ids": feature["source_acceptance_ids"],
            "boundary": feature["boundary"], "change_type": feature["change_type"],
            "change_detail": feature["change_detail"],
            "page_ids": [
                page_id for page_id, page in pages.items() if identifier in page.get("function_ids", [])
                or any(identifier in action.get("source_function_ids", []) for action in page.get("actions", []))
            ],
        })
    retained = facts["payloads"][feature_path].get("retained_capability_references", [])
    retained_fields = ("draft_key", "delivery_class", "business_object", "primary_action", "source_story_ids",
                       "source_acceptance_ids", "source_scenario_ids")
    page_rows = []
    for page in pages.values():
        row = {key: page[key] for key in ("id", "name", "menu_path", "scenario_ids", "function_ids") if key in page}
        row["actions"] = [
            {key: item[key] for key in ("id", "name", "source_scenario_ids", "source_function_ids",
                                       "retained_operation_refs") if key in item}
            for item in page.get("actions", [])
        ]
        page_rows.append(row)
    return deepcopy({
        "origin": bundle["binding"], "requirements": requirements, "scenes": scene_rows,
        "features": feature_rows, "pages": page_rows,
        "interfaces": _entry(facts, "interface_index", "interfaces"),
        "retained_capability_references": [{key: row[key] for key in retained_fields if key in row} for row in retained],
        "pending_s1": ["确认 RTL 场景归属", "将 AC 关联到浏览器断言和验收时点", "确认任务、写入范围和用例"],
    })


def _checkpoint_refs(rows, assertions, label):
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label}须关联至少一个本轮已确认的浏览器验收时点")
    approved = {}
    for item in assertions:
        if "checkpoint" in item:
            key = (item["case"], item["checkpoint"]["index"])
            approved.setdefault(key, []).append(item)
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"case", "checkpoint"}:
            raise ValueError(f"{label}的映射须填写 case 与 checkpoint 步骤序号")
        key = (safe_id(row["case"], "用例"), row["checkpoint"])
        if type(key[1]) is not int or key[1] < 0:
            raise ValueError(f"{label}的 checkpoint 须为从 0 开始的步骤序号")
        if key in result or len(approved.get(key, ())) != 1:
            raise ValueError(f"{label}的 {key[0]} 第 {key[1]} 步须对应唯一的本轮已确认断言")
        result[key] = row
    return [dict(result[key]) for key in sorted(result)]


def bind(bundle, baseline, mapping):
    """Validate source lineage and the explicitly reviewed S1 ownership and checks."""
    source = project(bundle)
    if not isinstance(mapping, dict) or set(mapping) != {"features"} or not isinstance(mapping["features"], dict):
        raise ValueError("04 交接映射须在 analysis_mapping.features 逐项登记功能")
    planned = _index(baseline["requirements"]["features"], "RTL 功能")
    scenes = {row["id"]: row for row in source["scenes"]}
    feature_ids = {feature["id"] for feature in source["features"]}
    if set(mapping["features"]) != feature_ids:
        raise ValueError("04 交接映射须覆盖冻结包中的全部本期功能")
    bound = {}
    for feature in sorted(source["features"], key=lambda item: item["id"]):
        identifier = feature["id"]
        local = planned.get(identifier)
        row = mapping["features"][identifier]
        if not local or local.get("status", "active") != "active":
            raise ValueError(f"RTL S1 未承接 04 本期功能：{identifier}")
        if not isinstance(row, dict) or set(row) != {"scene_id", "acceptance_cases"}:
            raise ValueError(f"功能 {identifier} 须明确确认 RTL 场景归属及 AC 对应用例")
        owner = safe_id(row["scene_id"], "RTL 场景归属")
        if (owner not in feature["source_scenario_ids"] or scenes.get(owner, {}).get("scope") != "in_scope"
                or local["scene_id"] != owner):
            raise ValueError(f"功能 {identifier} 的 RTL 场景归属须来自 04 本期源场景且匹配已确认 S1")
        if set(local["requirement_ids"]) != set(feature["source_story_ids"]):
            raise ValueError(f"功能 {identifier} 的 RTL 需求关联与 04 用户故事不一致")
        acceptance = row["acceptance_cases"]
        if not isinstance(acceptance, dict) or set(acceptance) != set(feature["source_acceptance_ids"]):
            raise ValueError(f"功能 {identifier} 须映射全部 04 来源 AC")
        bound[identifier] = {
            "scene_id": owner, "source_scenario_ids": feature["source_scenario_ids"],
            "source_story_ids": feature["source_story_ids"],
            "source_scene_ui": {scene_id: scenes[scene_id]["ui_surface"] for scene_id in feature["source_scenario_ids"]},
            "source_page_ids": feature["page_ids"],
            "acceptance_cases": {
                ac: _checkpoint_refs(acceptance[ac], local.get("assertions", []), f"功能 {identifier} 的 {ac}")
                for ac in sorted(acceptance)
            },
        }
    return {"origin": source["origin"], "features": bound,
            "retained_capability_references": source["retained_capability_references"]}
