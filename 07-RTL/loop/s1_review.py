"""Record the ordered S1 review before a run is initialized."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .state import LoopError, pack


STEPS = ("analysis", "scenes", "features", "tasks")
SCHEMA = "rtl-s1-review-v1"
PENDING_MARKERS = {"tbd", "todo", "待补", "待定", "待确认", "未确认", "尚未确认"}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _human_acceptance(document):
    value = document.get("human_acceptance", False)
    if type(value) is not bool:
        raise LoopError("人工验收要求 human_acceptance 必须是布尔值")
    return value


def _require_confirmed_acceptance(confirmation, expected):
    if not isinstance(confirmation, dict) or type(confirmation.get("human_acceptance")) is not bool:
        raise LoopError("S1 确认记录缺少已绑定的人工验收要求，请重新执行 s1-confirm 并登记后续交接")
    if confirmation["human_acceptance"] != expected:
        raise LoopError("人工验收要求与已确认的 S1 契约不符，请核对后重新确认")


def _contract(revision, requirements, changes, *, human_acceptance, analysis_origin=None):
    from .requirements import change_assertions, normalize_contract

    contract = {
        "revision": revision.strip(), "requirements": normalize_contract(requirements),
        "human_acceptance": human_acceptance,
    }
    if changes := change_assertions(list(changes)):
        contract["change_assertions"] = changes
    if analysis_origin is not None:
        contract["analysis_origin"] = analysis_origin
    return contract


def _contract_digest(contract):
    return hashlib.sha256(pack(contract).encode()).hexdigest()


def _record_path(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise LoopError("S1 评审记录须位于已确认的实际绝对路径")
    return path


def _load(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        raise LoopError(f"已有文件不是 RTL S1 评审记录：{path}")
    if not isinstance(record.get("record_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", record["record_id"]):
        raise LoopError("S1 评审记录缺少有效的记录编号")
    steps = record.get("steps")
    if not isinstance(steps, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("digest"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", item["digest"])
        or not isinstance(item.get("recorded_at"), str)
        or ("artifact_sha256" in item and (
            not isinstance(item["artifact_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["artifact_sha256"])
        ))
        for item in steps
    ) or tuple(item.get("step") for item in steps) != STEPS[:len(steps)]:
        raise LoopError("S1 评审记录的四步顺序或读取凭证不完整")
    return record


@contextmanager
def _edit(path):
    from .delivery import _atomic_text

    path = _record_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    if lock.resolve() != lock:
        raise LoopError("S1 评审锁须位于本地记录目录")
    with lock.open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        record = _load(path) if path.exists() else {"schema": SCHEMA, "steps": []}
        yield record
        _atomic_text(path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def record_step(path, skill):
    step = skill["step"]
    index = STEPS.index(step)
    with _edit(path) as record:
        if index > len(record["steps"]):
            raise LoopError(f"请先记录 S1 步骤 {STEPS[len(record['steps'])]}")
        if index and not record.get("confirmation"):
            raise LoopError("请先用 s1-confirm 登记需求契约的用户确认")
        if not index:
            record["record_id"] = uuid4().hex
            record.pop("confirmation", None)
        receipt = {"step": step, "digest": skill["digest"], "recorded_at": _now()}
        receipt.update({key: skill[key] for key in ("purpose", "source_path", "references") if key in skill})
        record["steps"] = record["steps"][:index] + [receipt]


def _handoff_value(step, artifact):
    from .requirements import normalize_handoff

    if not isinstance(artifact, dict):
        raise LoopError("S1 交接文件须为 JSON 对象")
    value = normalize_handoff(step, artifact.get("requirements"))
    if step != "tasks":
        return value
    from .state import safe_id
    from .suite_catalog import acceptance_browsers
    from .verification import INPUT_REVISIONS, validate_input_baseline

    if not {"suite_digests", *INPUT_REVISIONS} <= artifact.keys():
        raise LoopError("tasks 交接须登记冻结用例摘要、用例修订和数据修订")
    cases = artifact.get("mandatory_cases")
    if not isinstance(cases, list) or not cases:
        raise LoopError("tasks 交接须登记必跑用例编号")
    case_ids = [safe_id(case, "case ID") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise LoopError("tasks 交接的必跑用例编号不得重复")
    validate_input_baseline(artifact)
    handoff = {
        **value,
        "acceptance_browsers": acceptance_browsers(artifact),
        "human_acceptance": _human_acceptance(artifact),
        "mandatory_cases": sorted(case_ids),
        "suite_digests": {digest: sorted(cases) for digest, cases in artifact["suite_digests"].items()},
        **{key: artifact[key] for key in INPUT_REVISIONS},
    }
    if "analysis_mapping" in artifact:
        if not isinstance(artifact["analysis_mapping"], dict):
            raise LoopError("04 验收与场景映射须为 analysis_mapping 对象")
        handoff["analysis_mapping"] = artifact["analysis_mapping"]
    return handoff


def record_handoff(path, step, artifact):
    if step not in STEPS[1:]:
        raise LoopError("交接须选择 scenes、features 或 tasks")
    value = _handoff_value(step, artifact)
    digest = _contract_digest(value)
    index = STEPS.index(step)
    with _edit(path) as record:
        steps = record["steps"]
        if not record.get("confirmation") or len(steps) <= index:
            raise LoopError(f"请先登记用户确认并读取 {step} 步骤")
        for earlier in steps[1:index]:
            if "artifact_sha256" not in earlier:
                raise LoopError(f"请先登记 {earlier['step']} 的交接产物")
        if step == "tasks":
            _require_confirmed_acceptance(record["confirmation"], value["human_acceptance"])
            if ("analysis_origin" in record["confirmation"]) != ("analysis_mapping" in value):
                raise LoopError("04 映射须与 S1 已登记的上游冻结包对应")
        if steps[index].get("artifact_sha256") != digest:
            steps[index]["artifact_sha256"] = digest
            record["steps"] = steps[:index + 1]
        return dict(steps[index])


def confirm(path, project, contract, approval, *, analysis_origin=None):
    if not isinstance(approval, str) or not approval.strip() or approval.strip().casefold() in PENDING_MARKERS:
        raise LoopError("请记录用户对需求契约的确认依据")
    if (
        not isinstance(contract, dict) or not isinstance(contract.get("revision"), str)
        or not contract["revision"].strip() or contract.get("pending") != []
    ):
        raise LoopError("需求契约须包含修订及空的 pending 列表；请先处理所有待决事项")
    changes = contract.get("change_assertions", [])
    if not isinstance(changes, list):
        raise LoopError("确认的 change_assertions 须为断言变更列表")
    if "analysis_origin" in contract and contract["analysis_origin"] != analysis_origin:
        raise LoopError("请用 s1-confirm --analysis-package 核对并绑定对应的 04 冻结包")
    approved = _contract(
        contract["revision"], contract.get("requirements"), changes,
        human_acceptance=_human_acceptance(contract), analysis_origin=analysis_origin,
    )
    missing_sources = [
        item["id"] for item in approved["requirements"] if item["source"].casefold() in PENDING_MARKERS
    ]
    if missing_sources:
        raise LoopError(f"需求条款缺少确认的来源：{', '.join(missing_sources)}")
    confirmation = {
        "review_id": uuid4().hex,
        "project": str(project), "revision": approved["revision"], "approval": approval.strip(),
        "human_acceptance": approved["human_acceptance"],
        "contract_sha256": _contract_digest(approved), "recorded_at": _now(),
    }
    if analysis_origin is not None:
        confirmation["analysis_origin"] = analysis_origin
    with _edit(path) as record:
        if not record["steps"]:
            raise LoopError("请先读取并记录 S1 的 analysis 步骤")
        record["steps"] = record["steps"][:1]
        record["confirmation"] = confirmation
    return confirmation


def for_init(path, project, baseline):
    record = _load(_record_path(path))
    if tuple(item["step"] for item in record["steps"]) != STEPS:
        raise LoopError("S1 尚未记录 analysis、scenes、features、tasks 四步")
    confirmation = record.get("confirmation")
    plan = baseline["requirements"]
    changes = plan.get("changes", []) if baseline.get("change_proof_checks") else []
    human_acceptance = _human_acceptance(baseline)
    _require_confirmed_acceptance(confirmation, human_acceptance)
    origin = confirmation.get("analysis_origin")
    if "analysis_origin" in baseline and baseline["analysis_origin"] != origin:
        raise LoopError("启动基线的 04 来源与 S1 已登记冻结包不符")
    if (origin is not None) != ("analysis_mapping" in baseline):
        raise LoopError("04 映射须与 S1 已登记的上游冻结包对应")
    contract = _contract(
        baseline["revision"], plan["requirements"], changes,
        human_acceptance=human_acceptance, analysis_origin=origin,
    )
    if (
        not isinstance(confirmation.get("review_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", confirmation["review_id"])
        or confirmation.get("project") != str(project)
        or confirmation.get("revision") != contract["revision"]
        or confirmation.get("approval") != plan["approval"]
        or confirmation.get("contract_sha256") != _contract_digest(contract)
    ):
        raise LoopError("启动基线与已确认的 S1 需求契约不符，请重新确认并登记下游步骤")
    if baseline.get("s1_handoff_checks"):
        for receipt in record["steps"][1:]:
            step = receipt["step"]
            if receipt.get("artifact_sha256") != _contract_digest(_handoff_value(step, baseline)):
                raise LoopError(f"启动基线与 {step} 交接产物不符，请核对并重新登记交接")
    if origin is not None:
        from . import analysis_mapping, upstream_analysis

        bundle = upstream_analysis.require_current(origin)
        record["analysis_lineage"] = analysis_mapping.bind(bundle, baseline, baseline["analysis_mapping"])
    return record
