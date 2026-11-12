"""Verify project-wide identities for approved business and browser-suite revisions."""

from .assertions import observable_checkpoint, observable_predicate
from .state import LoopError, pack, unpack


RULE_FIELDS = ("goal", "scope", "rule", "acceptance")
SCENE_FIELDS = ("actor", "trigger", "preconditions", "main_flow", "alternatives", "outcome", "depends_on")
FLOW_FIELDS = {"preconditions", "main_flow", "alternatives"}
RULE_LABELS = {"goal": "目标", "scope": "范围", "rule": "规则", "acceptance": "验收"}
SCENE_LABELS = {
    "actor": "角色",
    "trigger": "触发",
    "preconditions": "前置",
    "main_flow": "主流程",
    "alternatives": "分支与异常",
    "outcome": "结果",
    "depends_on": "业务依赖",
}


def _by_id(records):
    return {item["id"]: item for item in records}


def _scene_fields(scene):
    result = {}
    for field in SCENE_FIELDS:
        value = scene.get(field, [] if field == "depends_on" else "")
        if field == "depends_on":
            value = sorted(value)
        elif field in FLOW_FIELDS and isinstance(value, str):
            value = [value]
        result[field] = value
    return result


def _feature_terms(plan, feature):
    """Keep business behavior separate from prose labels and technical work."""
    requirements = _by_id(plan["requirements"])
    scenes = _by_id(plan["scenes"])
    terms = {"功能规则": feature["rule"]}
    scene_id = feature["scene_id"]
    terms.update({
        f"场景 {scene_id} {SCENE_LABELS[field]}": value
        for field, value in _scene_fields(scenes[scene_id]).items()
    })
    for requirement_id in sorted(feature["requirement_ids"]):
        requirement = requirements[requirement_id]
        terms.update({
            f"需求 {requirement_id} {RULE_LABELS[field]}": requirement.get(field, "")
            for field in RULE_FIELDS
        })
    return terms


def _business_identity(plan):
    return {
        feature["id"]: _feature_terms(plan, feature)
        for feature in plan["features"] if feature["status"] == "active"
    }


def _acceptance_changed(former, current):
    before = {observable_predicate(binding["step"]) for binding in former.get("assertions", [])}
    after = {observable_predicate(binding["step"]) for binding in current.get("assertions", [])}
    if before and after and not before <= after:
        return True
    old_checkpoints = {
        context for binding in former.get("assertions", [])
        if (context := observable_checkpoint(binding)) is not None
    }
    new_checkpoints = {
        context for binding in current.get("assertions", [])
        if (context := observable_checkpoint(binding)) is not None
    }
    return bool(old_checkpoints and new_checkpoints and not old_checkpoints <= new_checkpoints)


def _suite_identity(baseline):
    return baseline.get("fixture_revision"), {
        digest: sorted(cases) for digest, cases in baseline["suite_digests"].items()
    }


def _histories(db, project, project_id):
    verified = []
    if project is not None:
        from .requirements_history import historical_baselines

        verified = historical_baselines(db, project, project_id)
    rows = [
        (row["status"], unpack(row["baseline"]))
        for row in db.execute(
            "SELECT status,baseline FROM runs WHERE project_id=? ORDER BY rowid DESC",
            (project_id,),
        )
    ]
    # Older local runs may predate the structured history index. Their frozen
    # SQLite baselines still reserve their own revision labels.
    previous = next((item for item in verified if "requirements" in item), None)
    if previous is None:
        previous = next((item for status, item in rows if status == "delivered" and "requirements" in item), None)
    return previous, [*verified, *(item for _, item in rows)]


def _observation_family(step):
    operation = step["op"]
    if operation in {"visible", "hidden"}:
        return ("visibility",)
    if operation == "attribute":
        return operation, step["attribute"]
    if operation == "css":
        return operation, step["property"]
    return (operation,)


def _check_assertion_changes(change, historical, current):
    feature_id = change["feature_id"]
    pairs = change.get("assertion_changes")
    if not pairs:
        raise LoopError(f"业务变更 {feature_id} 须登记经确认的改版前后验收断言")
    for pair in pairs:
        before, after = pair["before"], pair["after"]
        if before not in (historical or (None,)):
            raise LoopError(f"业务变更 {feature_id} 的旧验收断言与已交付基线不符")
        if after not in current:
            raise LoopError(f"业务变更 {feature_id} 的新验收断言须已登记在本轮功能中")
        if before is None:
            continue
        previous_result = observable_predicate(before["step"])
        if observable_predicate(after["step"]) == previous_result:
            if not change.get("assertion_reuse"):
                raise LoopError(f"业务变更 {feature_id} 沿用旧预期须登记确认依据")
            continue
        checkpoint = observable_checkpoint(before)
        still_expected = any(
            binding["case"] == before["case"] and observable_predicate(binding["step"]) == previous_result
            and (checkpoint is None or observable_checkpoint(binding) == checkpoint)
            for binding in current
        )
        if still_expected:
            raise LoopError(f"业务变更 {feature_id} 已替换的旧验收预期仍在原用例与时点生效")
        if _observation_family(before["step"]) != _observation_family(after["step"]) and not pair.get("retarget_reason"):
            raise LoopError(f"业务变更 {feature_id} 更换可观察结果的类型须登记经确认的 retarget_reason")


def _require_changed_feature_proof(previous, baseline, old_features, new_features):
    if not baseline.get("change_assertion_checks"):
        return
    for change in baseline["requirements"]["changes"]:
        if change["kind"] not in {"add", "modify", "remove"}:
            continue
        feature_id = change["feature_id"]
        assertions = new_features[feature_id].get("assertions", [])
        if not assertions:
            raise LoopError(f"业务变更 {feature_id} 缺少已确认的验收断言")
        historical = old_features.get(feature_id, {}).get("assertions", [])
        if baseline.get("change_proof_checks") and change["kind"] in {"modify", "remove"}:
            _check_assertion_changes(change, historical, assertions)
        else:
            old_predicates = {observable_predicate(binding["step"]) for binding in historical}
            current_predicates = {observable_predicate(binding["step"]) for binding in assertions}
            if old_predicates and not current_predicates - old_predicates and not change.get("assertion_reuse"):
                raise LoopError(f"业务变更 {feature_id} 须绑定新预期断言，或登记沿用预期的确认依据")
        bound_cases = {binding["case"] for binding in assertions}
        old_digests = previous.get("suite_digests", {})
        for digest, cases in baseline.get("suite_digests", {}).items():
            if digest in old_digests and bound_cases.intersection(cases):
                raise LoopError(f"业务变更 {feature_id} 的断言用例须使用新冻结文件，不能沿用上一轮旧摘要")


def _validate_successor(previous, baseline, before, after):
    earlier, plan = previous["requirements"], baseline["requirements"]
    old_features, new_features = _by_id(earlier["features"]), _by_id(plan["features"])
    missing = before.keys() - new_features.keys()
    if missing:
        raise LoopError(f"需求基线须显式保留移除的功能编号：{', '.join(sorted(missing))}")
    kinds = {change["feature_id"]: change["kind"] for change in plan["changes"]}
    for feature_id, feature in new_features.items():
        was_active = feature_id in before
        is_active = feature_id in after
        kind = kinds.get(feature_id)
        expected = None
        if was_active != is_active:
            expected = "add" if is_active else "remove"
        elif is_active and (
            before[feature_id] != after[feature_id]
            or _acceptance_changed(old_features[feature_id], feature)
        ):
            expected = "modify"
        if expected is not None and kind != expected:
            raise LoopError(f"功能 {feature_id} 的基线差异须登记 {expected} 变更")
        if expected is None and kind in {"add", "remove"}:
            raise LoopError(f"功能 {feature_id} 的 {kind} 与已交付基线不符")
        if is_active and was_active and (
            set(feature["cases"]) != set(old_features[feature_id]["cases"])
        ) and kind not in {"technical", "modify"}:
            raise LoopError(f"功能 {feature_id} 的用例关联变更须显式登记")
    if (before != after or "modify" in kinds.values()) and previous["revision"] == baseline["revision"]:
        raise LoopError("业务规则或范围改变后须登记新的需求契约修订")
    _require_changed_feature_proof(previous, baseline, old_features, new_features)


def _validate_labels(baseline, current, histories):
    features = _by_id(baseline["requirements"]["features"])
    for prior in histories:
        if "requirements" in prior and prior.get("revision") == baseline["revision"]:
            former_plan = prior["requirements"]
            former_features = _by_id(former_plan["features"])
            if _business_identity(former_plan) != current or any(
                _acceptance_changed(former_features[feature_id], features[feature_id])
                for feature_id in current
            ):
                raise LoopError(f"需求契约修订 {baseline['revision']} 已绑定历史业务内容，请使用新修订")
        if (
            baseline.get("suite_revision") and "suite_digests" in baseline and "suite_digests" in prior
            and prior.get("suite_revision") == baseline["suite_revision"]
            and _suite_identity(prior) != _suite_identity(baseline)
        ):
            raise LoopError(f"用例修订 {baseline['suite_revision']} 已绑定历史用例摘要和夹具修订，请使用新用例修订")


def _normative_text(terms, fields):
    lines = []
    for field in fields:
        if field not in terms:
            continue
        value = terms[field]
        if isinstance(value, list):
            value = "\n".join(value)
        if value:
            lines.append(f"{field}\n{value}")
    return "\n".join(lines)


def _normative_changes(plan, before, after, *, previous=None):
    result = {}
    old_features = _by_id(previous["requirements"]["features"]) if previous else {}
    new_features = _by_id(plan["features"])
    for change in plan["changes"]:
        feature_id = change["feature_id"]
        former, current = before.get(feature_id, {}), after.get(feature_id, {})
        if former and current and _acceptance_changed(old_features[feature_id], new_features[feature_id]):
            former = {**former, "验收断言": [pack(row) for row in old_features[feature_id]["assertions"]]}
            current = {**current, "验收断言": [pack(row) for row in new_features[feature_id]["assertions"]]}
        if former and current:
            fields = [
                field for field in dict.fromkeys((*former, *current))
                if former.get(field) != current.get(field)
            ] or ["功能规则"]
        else:
            fields = ["功能规则"]
        result[feature_id] = {
            "before": _normative_text(former, fields),
            "after": _normative_text(current, fields),
        }
    return result


def validate_revision(db, project_id, baseline, *, project=None):
    """Validate the proposed S1 contract and return the exact baseline to store."""
    plan = baseline.get("requirements")
    if plan is None:
        return baseline
    previous, histories = _histories(db, project, project_id)
    before = _business_identity(previous["requirements"]) if previous else {}
    current = _business_identity(plan)
    if previous:
        _validate_successor(previous, baseline, before, current)
    _validate_labels(baseline, current, histories)
    if baseline.get("normative_change_checks"):
        return {**baseline, "normative_changes": _normative_changes(plan, before, current, previous=previous)}
    return baseline
