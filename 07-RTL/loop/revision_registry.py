"""Reserve approved S1 revision labels across all runtime ledgers of a project."""

import hashlib
import json
from pathlib import Path
import re

from .assertions import observable_checkpoint, observable_predicate
from .requirements_revision import _business_identity, _suite_identity
from .state import LoopError, pack, safe_id


REGISTRY = "memory/requirements-revisions.json"
MARKER = "<!-- rtl:requirements-revisions:v1 -->"
DIGEST = re.compile(r"[0-9a-f]{64}")


def _digest(value):
    return hashlib.sha256(pack(value).encode()).hexdigest()


def _enabled(baseline):
    return "requirements" in baseline and (baseline.get("normative_change_checks") or baseline.get("s1_review"))


def _identity(project, project_id):
    from .delivery import _safe_project_child

    memory = _safe_project_child(project, "memory/project.md")
    text = memory.read_text(encoding="utf-8") if memory.is_file() else ""
    ids = re.findall(r"^Project-ID:\s*(\S+)\s*$", text, re.MULTILINE)
    if any(identifier != project_id for identifier in ids):
        raise LoopError("项目记忆中的编号与需求修订登记不符")
    return memory, text, bool(ids)


def _registry_path(project):
    from .delivery import _safe_project_child

    return _safe_project_child(project, REGISTRY)


def _business_record(plan):
    predicates = {
        feature["id"]: sorted({
            _digest(observable_predicate(binding["step"]))
            for binding in feature.get("assertions", [])
        })
        for feature in plan["features"] if feature["status"] == "active"
    }
    checkpoints = {
        feature["id"]: sorted({
            _digest(context) for binding in feature.get("assertions", [])
            if (context := observable_checkpoint(binding)) is not None
        })
        for feature in plan["features"] if feature["status"] == "active"
    }
    result = {"sha256": _digest(_business_identity(plan)), "predicates": predicates}
    if any(checkpoints.values()):
        result["checkpoints"] = checkpoints
    return result


def _merge_business(records, label, current, *, migrating):
    old = records.get(label)
    if old is None:
        records[label] = current
        return
    if old["sha256"] != current["sha256"]:
        raise LoopError(f"需求契约修订 {label} 已绑定历史业务内容，请使用新修订")
    for feature_id, proposed in current["predicates"].items():
        former = set(old["predicates"].get(feature_id, []))
        proposed = set(proposed)
        if not former <= proposed and not (migrating and proposed <= former):
            raise LoopError(f"需求契约修订 {label} 已绑定功能 {feature_id} 的历史验收预期，请使用新修订")
        old["predicates"][feature_id] = sorted(former | proposed)
    if current.get("checkpoints"):
        recorded = old.setdefault("checkpoints", {})
        for feature_id, contexts in current["checkpoints"].items():
            former, proposed = set(recorded.get(feature_id, [])), set(contexts)
            if not former <= proposed and not (migrating and proposed <= former):
                raise LoopError(f"需求契约修订 {label} 已绑定功能 {feature_id} 的历史验收时点，请使用新修订")
            recorded[feature_id] = sorted(former | proposed)


def _review_key(review):
    confirmation = review.get("confirmation") if isinstance(review, dict) else None
    if not isinstance(confirmation, dict):
        raise LoopError("S1 评审记录缺少已确认契约")
    if "review_id" in confirmation:
        return safe_id(confirmation["review_id"], "S1 review ID")
    return _digest(review)


def _merge_baseline(payload, baseline, run_id, ledger, *, migrating=False):
    plan = baseline.get("requirements")
    revision = baseline.get("revision")
    if not isinstance(plan, dict) or not isinstance(revision, str) or not revision.strip():
        raise LoopError("缺少可核对的冻结业务修订")
    _merge_business(payload["business"], revision.strip(), _business_record(plan), migrating=migrating)
    if baseline.get("suite_revision") and "suite_digests" in baseline:
        label = baseline["suite_revision"].strip()
        fingerprint = _digest(_suite_identity(baseline))
        if payload["suites"].setdefault(label, fingerprint) != fingerprint:
            raise LoopError(f"用例修订 {label} 已绑定历史用例摘要和夹具修订，请使用新用例修订")
    if "s1_review" in baseline:
        key = _review_key(baseline["s1_review"])
        owner = {"run_id": safe_id(run_id, "run ID"), "ledger": str(ledger) if ledger else None,
                 "baseline_sha256": _digest(baseline)}
        recorded = payload["reviews"].setdefault(key, owner)
        if recorded != owner:
            raise LoopError(f"此 S1 确认记录已用于另一轮 {recorded['run_id']}，请重新登记本轮确认")


def _digest_text(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _check_registry(payload, project_id):
    if (
        not isinstance(payload, dict) or payload.get("version") != 1
        or payload.get("project_id") != project_id
        or not all(isinstance(payload.get(key), dict) for key in ("business", "suites", "reviews"))
    ):
        raise LoopError("结构化需求修订登记与项目不符")
    if payload.get("records_sha256") != _digest([payload[key] for key in ("business", "suites", "reviews")]):
        raise LoopError("结构化需求修订登记摘要不符")
    for record in payload["business"].values():
        if (
            not isinstance(record, dict) or not _digest_text(record.get("sha256"))
            or not isinstance(record.get("predicates"), dict)
            or not isinstance(record.get("checkpoints", {}), dict)
            or any(not isinstance(items, list) or not all(_digest_text(item) for item in items)
                   for entries in (record["predicates"], record.get("checkpoints", {}))
                   for items in entries.values())
        ):
            raise LoopError("结构化需求修订登记含无效业务指纹")
    if not all(_digest_text(value) for value in payload["suites"].values()):
        raise LoopError("结构化需求修订登记含无效用例指纹")
    for owner in payload["reviews"].values():
        if (
            not isinstance(owner, dict) or not _digest_text(owner.get("baseline_sha256"))
            or "ledger" not in owner or owner["ledger"] is not None and not isinstance(owner["ledger"], str)
        ):
            raise LoopError("结构化需求修订登记含无效 S1 确认来源")
        safe_id(owner.get("run_id"), "run ID")


def _load(project, project_id, memory_text):
    from .delivery import _safe_project_child

    path = _registry_path(project)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _check_registry(payload, project_id)
            return payload
        except (OSError, ValueError) as error:
            raise LoopError(f"无法核对结构化需求修订登记，请先恢复：{path}") from error
    if MARKER in memory_text:
        raise LoopError(f"结构化需求修订登记缺失，请先恢复：{path}")
    payload = {"version": 1, "project_id": project_id, "business": {}, "suites": {}, "reviews": {}}
    for snapshot in sorted((project / "memory/baselines").glob("rtl-s1-*.json")):
        try:
            snapshot = _safe_project_child(project, snapshot.relative_to(project))
            baseline = json.loads(snapshot.read_text(encoding="utf-8"))
            if not isinstance(baseline, dict):
                raise LoopError("冻结基线不是对象")
            _merge_baseline(payload, baseline, snapshot.stem.removeprefix("rtl-s1-"), None, migrating=True)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise LoopError(f"无法从旧冻结快照核对需求修订登记：{snapshot}") from error
    return payload


def validate_registry(project, project_id, baseline, *, run_id=None, ledger=None):
    """Check an approved baseline under the shared project reservation lock."""
    if not _enabled(baseline):
        return
    project = Path(project)
    _, text, _ = _identity(project, project_id)
    _merge_baseline(_load(project, project_id, text), baseline, run_id, ledger)


def record_revision(project, project_id, baseline, run_id, *, ledger=None):
    """Persist approved labels while the caller holds the reservation lock."""
    if not _enabled(baseline):
        return
    from .delivery import _append_marker, _atomic_text

    project = Path(project)
    memory, text, has_project_id = _identity(project, project_id)
    payload = _load(project, project_id, text)
    _merge_baseline(payload, baseline, run_id, ledger)
    payload["records_sha256"] = _digest([payload[key] for key in ("business", "suites", "reviews")])
    if not has_project_id:
        if not text.strip():
            _atomic_text(memory, f"# 项目记忆\n\nProject-ID: {project_id}\n")
        else:
            _append_marker(memory, f"Project-ID: {project_id}", f"Project-ID: {project_id}")
    path = _registry_path(project)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != rendered:
        _atomic_text(path, rendered)
    _append_marker(memory, MARKER, MARKER)
