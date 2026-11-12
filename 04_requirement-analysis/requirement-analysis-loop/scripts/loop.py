#!/usr/bin/env python3
"""Manage the Requirement Analysis Loop state without external dependencies."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import sys
from typing import Any
import zipfile
from frozen_snapshot import approval_snapshot
from diagram_policy import POLICY, requested, included, selection

from swimlane_layout import audit_swimlane_layout, build_swimlane_layout
from swimlane_model import inspect_swimlane
from source_index import inspect_source_index, source_issue_errors
from feature_contract import feature_artifact_errors
from operation_attribution import attribution_report, scenario_coverage_errors
from atlas_reading import desktop_atlas_errors
from analysis_contract import (
    WORKFLOW_VERSION,
    PAGE_STRUCTURE,
    approval_basis,
    approval_basis_errors,
    interface_errors,
    page_errors,
    page_html_errors,
    sequence_html_errors,
)


STAGES = [
    ("S0", "输入接收"),
    ("S1", "用户故事规范化"),
    ("S2", "业务场景清单"),
    ("S3", "业务流程与场景泳道图"),
    ("S4", "外部接口核验"),
    ("S5", "交互时序分析"),
    ("S6", "页面结构图与页面规划"),
    ("S7", "功能清单与确认基线"),
    ("S8", "全局一致性核验"),
    ("S9", "冻结交付"),
]
STAGE_IDS = [item[0] for item in STAGES]
GATES = {
    "G1": {"after": "S2", "next": "S3", "name": "场景范围确认"},
    "G2": {"after": "S4", "next": "S5", "name": "业务与接口冲突确认"},
    "G3": {"after": "S8", "next": "S9", "name": "最终基线冻结"},
}
GATE_AFTER = {meta["after"]: gate for gate, meta in GATES.items()}
PREFIX_STAGE = {
    "SRC": "S1",
    "US": "S1",
    "AC": "S1",
    "SC": "S2",
    "BR": "S3",
    "SEQ": "S5",
    "API": "S4",
    "FLOW": "S3",
    "FUNC": "S7",
    "PAGE": "S6",
    "ACTION": "S6",
    "ISSUE": "S8",
}
DRAFT_MARKER = "status: draft"
ENGINE_VERSION = "0.16.0"
FEATURE_CHANGE_TYPES = {"new", "iteration"}
DEFAULT_EVIDENCE = {
    "S0": ["inputs/user-stories.md"],
    "S1": ["memory/normalized-user-stories.json"],
    "S2": ["scenarios/scenario-index.json", "outputs/business-scenario-list.md"],
    "S3": ["scenarios/scenario-index.json", "memory/business-rules.json", "outputs/business-scenario-atlas.html"],
    "S4": ["interfaces/interface-index.json", "outputs/system-sequence-interface-verification-matrix.md"],
    "S5": ["sequences/sequence-index.json", "outputs/interaction-sequence-atlas.html", "outputs/system-sequence-interface-verification-matrix.md"],
    "S6": ["planning/page-index.json", "outputs/menu-and-page-planning-reference.md", PAGE_STRUCTURE],
    "S7": [
        "memory/project-facts.json",
        "memory/business-rules.json",
        "memory/feature-list.json",
        "outputs/feature-list.md",
        "outputs/product-structure-and-business-process-baseline.md",
    ],
    "S8": ["runtime/verification-report.json"],
    "S9": ["deliverables/delivery-manifest.json"],
}


class LoopError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LoopError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise LoopError(f"JSON 无法解析：{path}（{exc}）") from exc


def project_root(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def state_path(root: Path) -> Path:
    return root / "runtime" / "loop-state.json"


def load_state(root: Path, *, read_only: bool = False) -> dict[str, Any]:
    state = load_json(state_path(root))
    if state.get("schema_version") != "1.0":
        raise LoopError("不支持的 loop-state schema_version")
    if not read_only and state.get("workflow_version") != WORKFLOW_VERSION:
        raise LoopError("旧版或未知生成顺序：未改写状态。请在新隔离目录按新版重新分析；旧包仍可只读导入，不得直接把旧 S4/S5/S6/S7 改名续跑。")
    return state


def save_state(root: Path, state: dict[str, Any], event: str, details: dict[str, Any] | None = None) -> None:
    timestamp = now()
    state["engine"] = {"name": "requirement-analysis-loop", "version": ENGINE_VERSION}
    for stage_id, title in STAGES:
        if stage_id in state.get("stages", {}):
            state["stages"][stage_id]["name"] = title
    for gate_id, meta in GATES.items():
        if gate_id in state.get("gates", {}):
            state["gates"][gate_id]["name"] = meta["name"]
    state["run"]["updated_at"] = timestamp
    entry = {"at": timestamp, "event": event}
    if details:
        entry.update(details)
    state.setdefault("history", []).append(entry)
    atomic_json(state_path(root), state)


def safe_relative(root: Path, raw: str) -> Path:
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise LoopError(f"项目内文件路径越界：{raw}") from exc
    return candidate


def text_ready(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    return DRAFT_MARKER not in path.read_text(encoding="utf-8", errors="replace").lower()


def indexed_records(root: Path, relative: str, key: str) -> list[dict[str, Any]]:
    data = load_json(root / relative)
    value = data.get(key)
    if not isinstance(value, list):
        raise LoopError(f"{relative} 的 {key} 必须是数组")
    return value


def s9_version_reference(root: Path) -> dict[str, Any] | None:
    """Only an absent marker is an unbound legacy workspace."""
    from project_versions import VersionError, _plain_path, reference
    try:
        marker = _plain_path(root / "project-version.json")
        if not marker.exists():
            return None
        ref = reference(root)
        if (not isinstance(ref, dict) or ref.get("schema_version") != "1.0"
                or any(not isinstance(ref.get(key), str) or not ref[key].strip()
                       for key in ("project_id", "version_id", "registry"))
                or not Path(ref["registry"]).is_absolute()):
            raise LoopError("项目版本绑定无效，不可按未绑定兼容流程完成")
        _plain_path(ref["registry"])
        return ref
    except (VersionError, OSError, ValueError, KeyError, TypeError) as exc:
        raise LoopError("项目版本绑定无效：" + str(exc)) from exc


@contextmanager
def s9_completion_guard(root: Path):
    """Hold the existing ledger lock and reject drift before committing state."""
    from frozen_snapshot import require_approved_snapshot
    from project_versions import VersionError, _plain_path, validate_workspace
    ref = s9_version_reference(root)
    marker = root / "project-version.json"
    marker_bytes = marker.read_bytes() if ref else None
    state_bytes = state_path(root).read_bytes()
    report_path = root / "runtime/verification-report.json"
    report_bytes = report_path.read_bytes()
    try:
        lock = (_plain_path(Path(ref["registry"]).with_suffix(Path(ref["registry"]).suffix + ".lock"))
                if ref else None)
        with lock.open("rb") if lock else nullcontext(None) as handle:
            if handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            try:
                ledger_bytes = Path(ref["registry"]).read_bytes() if ref else None

                def recheck():
                    current_ref = s9_version_reference(root)
                    if (current_ref != ref or (marker.read_bytes() if current_ref else None) != marker_bytes
                            or state_path(root).read_bytes() != state_bytes
                            or report_path.read_bytes() != report_bytes):
                        raise LoopError("S9 校验期间版本绑定、流程状态或核验报告发生变化，未写完成状态")
                    if ref:
                        if Path(ref["registry"]).read_bytes() != ledger_bytes:
                            raise LoopError("S9 校验期间共用台账发生变化，未写完成状态")
                        validate_workspace(root, "analysis")
                    require_approved_snapshot(root, load_state(root), load_json(report_path))

                yield recheck
            finally:
                if handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except BlockingIOError as exc:
        raise LoopError("共用台账正在更新，S9 未写完成状态；待更新完成后重新校验") from exc
    except (VersionError, OSError, ValueError, KeyError, TypeError) as exc:
        raise LoopError("S9 完成前复核失败：" + str(exc)) from exc


def s9_delivery_manifest(root: Path) -> Path:
    """Read the current delivery receipt; never regenerate or repair a package."""
    from frozen_snapshot import require_approved_snapshot
    from package_delivery import PackageError, build_handoff, collect_files, file_entries, sha256
    from project_versions import (
        VersionError, _layout, _plain_path, _relative_file, _verified_files,
        read, validate_workspace, version,
    )

    try:
        state = load_state(root)
        report = load_json(root / "runtime/verification-report.json")
        if any(state.get("gates", {}).get(gate, {}).get("status") != "approved" for gate in GATES):
            raise LoopError("S9 需要当前 G1/G2/G3 批准")
        if report.get("status") != "pass":
            raise LoopError("S9 全局核验报告尚未通过")
        require_approved_snapshot(root, state, report)
        # An already frozen historical package still uses its original approval record.
        if any(key in state["gates"][gate] for gate, key in
               (("G1", "scenario_fingerprint"), ("G2", "interface_sources_fingerprint"))):
            errors = approval_basis_errors(root, state, ("G1", "G2"))
            if errors:
                raise LoopError("；".join(errors))

        ref = s9_version_reference(root)
        record = None
        output_dir = root / "deliverables"
        package_root = root
        receipt = None
        if ref:
            _plain_path(ref["registry"])
            receipt = validate_workspace(root, "analysis")
            data = read(ref["registry"])
            current = version(data, ref["version_id"])
            matches = [item for item in current.get("artifacts", [])
                       if item.get("stage") == "analysis" and item.get("snapshot_id") == receipt["snapshot_id"]]
            if len(matches) > 1:
                raise LoopError("当前需求快照有多个交付登记，无法确定 S9 证据")
            if matches:
                record = matches[0]
                if record.get("kind") != "requirement-package" or record.get("revision") != current["revision"]:
                    raise LoopError("当前需求交付登记类型或版本修订不匹配")
                expected = _layout(data, ref["version_id"], record["project_root"], "analysis", receipt["snapshot_id"], root)
                output_dir = _plain_path(record["destination"])
                if output_dir != Path(expected["destination"]):
                    raise LoopError("需求交付登记不在当前标准快照目录")
                _verified_files(output_dir, {"files": [{"path": name, "sha256": sha}
                                                       for name, sha in record["files"].items()]})
                package_root = output_dir
            elif data.get("project_root"):
                expected = _layout(data, ref["version_id"], data["project_root"], "analysis", receipt["snapshot_id"], root)
                if Path(expected["destination"]).exists():
                    raise LoopError("当前标准需求快照缺少交付登记，不可退回兼容包")

        manifest_path = _plain_path(output_dir / "delivery-manifest.json")
        handoff_path = _plain_path(output_dir / "prototype-handoff.json")
        manifest_bytes = manifest_path.read_bytes()
        handoff_bytes = handoff_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        handoff = json.loads(handoff_bytes)
        package_file = _relative_file(manifest.get("package_file"))
        package_path = _plain_path(package_root / package_file)
        if not package_path.is_relative_to(package_root) or not package_path.is_file():
            raise LoopError("交付清单未指向有效的冻结包")
        if record and record.get("entrypoint") != package_file.as_posix():
            raise LoopError("冻结包与当前登记的交付入口不一致")

        semantic_entries = file_entries(root, collect_files(root))
        frozen_at = manifest.get("frozen_at")
        if not isinstance(frozen_at, str) or not frozen_at.strip():
            raise LoopError("交付清单缺少冻结时间")
        expected_handoff = build_handoff(root, state, report, frozen_at, semantic_entries)
        if ref:
            expected_handoff["project_version"] = {**ref, "snapshot_id": receipt["snapshot_id"]}
        if handoff != expected_handoff:
            raise LoopError("原型交接契约与当前批准事实或项目版本不一致")
        baseline = expected_handoff["baseline"]
        if (manifest.get("schema_version") != "1.1"
                or manifest.get("analysis_engine") != expected_handoff["analysis_engine"]
                or manifest.get("baseline_fingerprint") != baseline["fingerprint"]
                or manifest.get("baseline_id") != baseline["id"]
                or manifest.get("handoff_contract") != expected_handoff["contract"]
                or manifest.get("project") != state["project"]
                or manifest.get("verification_status") != "pass"):
            raise LoopError("交付清单与当前确认基线不一致")
        expected_files = [*semantic_entries, {"path": "prototype-handoff.json", "bytes": len(handoff_bytes),
                                             "sha256": hashlib.sha256(handoff_bytes).hexdigest()}]
        if manifest.get("files") != expected_files:
            raise LoopError("交付清单与当前冻结文件摘要不一致")
        package_digest = sha256(package_path)
        with zipfile.ZipFile(package_path) as archive:
            names = archive.namelist()
            expected_names = {"delivery-manifest.json", *(item["path"] for item in expected_files)}
            if len(names) != len(set(names)) or set(names) != expected_names:
                raise LoopError("冻结 ZIP 文件集合与交付清单不一致")
            if any(stat.S_ISLNK(item.external_attr >> 16) for item in archive.infolist()):
                raise LoopError("冻结 ZIP 不得含符号链接成员")
            if archive.read("delivery-manifest.json") != manifest_bytes:
                raise LoopError("冻结 ZIP 内外交付清单不一致")
            for item in expected_files:
                raw = archive.read(item["path"])
                if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                    raise LoopError("冻结 ZIP 内容变化：" + item["path"])
        if (manifest_path.read_bytes() != manifest_bytes or handoff_path.read_bytes() != handoff_bytes
                or sha256(package_path) != package_digest):
            raise LoopError("S9 校验期间交付文件发生变化，未写完成状态")
        if record:
            _verified_files(output_dir, {"files": [{"path": name, "sha256": sha}
                                                   for name, sha in record["files"].items()]})
        return manifest_path
    except (VersionError, PackageError, OSError, ValueError, KeyError, TypeError,
            AttributeError, zipfile.BadZipFile) as exc:
        raise LoopError("S9 交付校验失败：" + str(exc)) from exc


def earlier_approval_errors(root: Path, state: dict[str, Any], stage: str) -> list[str]:
    if stage in {"S3", "S4"}:
        return approval_basis_errors(root, state, ("G1",))
    if stage in {"S5", "S6", "S7", "S8"}:
        return approval_basis_errors(root, state, ("G1", "G2"))
    return []


def readiness_errors(root: Path, stage: str) -> list[str]:
    errors: list[str] = []

    def require_text(relative: str) -> None:
        if not text_ready(root / relative):
            errors.append(f"{relative} 缺失、为空或仍含草稿标记")

    try:
        if stage in {"S3", "S4", "S5", "S6", "S7", "S8"}:
            errors.extend(earlier_approval_errors(root, load_state(root), stage))
        if stage == "S0":
            require_text("inputs/user-stories.md")
            sources, source_errors = inspect_source_index(root)
            errors.extend(source_errors)
            if sources:
                errors.extend(source_issue_errors(sources, load_json(root / "runtime/open-items.json").get("items", [])))
        elif stage == "S1":
            stories = indexed_records(root, "memory/normalized-user-stories.json", "user_stories")
            if not stories:
                errors.append("normalized-user-stories.json 尚无用户故事")
            for item in stories:
                if not re.fullmatch(r"US-\d{3,}", str(item.get("id", ""))):
                    errors.append(f"用户故事 ID 不合法：{item.get('id')!r}")
                if not item.get("acceptance_criteria"):
                    errors.append(f"{item.get('id', '未知故事')} 缺少 acceptance_criteria")
        elif stage == "S2":
            scenarios = indexed_records(root, "scenarios/scenario-index.json", "scenarios")
            if not scenarios:
                errors.append("scenario-index.json 尚无业务场景")
            require_text("outputs/business-scenario-list.md")
        elif stage == "S3":
            flows = indexed_records(root, "memory/business-rules.json", "business_flows")
            if not flows:
                errors.append("S3 必须先形成 FLOW-* 跨场景业务流程，不能留到功能清单阶段")
            for flow in flows:
                if not re.fullmatch(r"FLOW-\d{3,}", str(flow.get("id", ""))) or not flow.get("name") or not flow.get("scenario_ids") or not flow.get("steps"):
                    errors.append(f"{flow.get('id')} 缺少有效业务流程事实")
            scenarios = indexed_records(root, "scenarios/scenario-index.json", "scenarios")
            in_scope = [item for item in scenarios if item.get("scope", "in_scope") == "in_scope"]
            if not in_scope:
                errors.append("没有 in_scope 业务场景")
            for item in in_scope:
                relative = item.get("file")
                if not relative:
                    errors.append(f"{item.get('id')} 缺少详情文件路径")
                    continue
                detail_path = safe_relative(root, relative)
                detail = load_json(detail_path) if detail_path.is_file() else None
                if not detail:
                    errors.append(f"{item.get('id')} 缺少详情文件：{relative}")
                elif detail.get("id") != item.get("id") or not detail.get("main_flow"):
                    errors.append(f"{item.get('id')} 详情 ID 不一致或主流程为空")
                else:
                    swimlane_problems = inspect_swimlane(detail)
                    for problem in swimlane_problems:
                        errors.append(problem["message"])
                    if not swimlane_problems and requested(root, 'business_flow'):
                        for problem in audit_swimlane_layout(build_swimlane_layout(detail["flow_diagram"])):
                            errors.append(f"{item.get('id')} 的泳道布局无效：{problem}")
            if requested(root, 'business_flow'):
                require_text("outputs/business-scenario-atlas.html")
                errors.extend(desktop_atlas_errors(root, "outputs/business-scenario-atlas.html"))
            atlas = root / "outputs/business-scenario-atlas.html"
            if requested(root, 'business_flow') and atlas.is_file():
                html = atlas.read_text(encoding="utf-8")
                for flow in flows:
                    if f'data-flow-id="{flow.get("id")}"' not in html:
                        errors.append(f"{flow.get('id')} 尚未进入 S3 业务流程 HTML")
        elif stage == "S4":
            errors.extend(interface_errors(root))
            require_text("outputs/system-sequence-interface-verification-matrix.md")
        elif stage == "S5":
            errors.extend(interface_errors(root, require_sequences=True))
            scenarios = indexed_records(root, "scenarios/scenario-index.json", "scenarios")
            required = {item.get("id") for item in scenarios if item.get("scope", "in_scope") == "in_scope"}
            sequences = indexed_records(root, "sequences/sequence-index.json", "sequences")
            covered = {item.get("scenario_id") for item in sequences}
            for scenario_id in sorted(required - covered):
                errors.append(f"{scenario_id} 缺少交互时序")
            for item in sequences:
                relative = item.get("file")
                if not relative:
                    errors.append(f"{item.get('id')} 缺少详情文件路径")
                    continue
                detail_path = safe_relative(root, relative)
                detail = load_json(detail_path) if detail_path.is_file() else None
                if not detail or not detail.get("messages"):
                    errors.append(f"{item.get('id')} 缺少有效时序消息")
            if requested(root, 'interaction_sequence'):
                require_text("outputs/interaction-sequence-atlas.html")
                errors.extend(sequence_html_errors(root))
        elif stage == "S6":
            errors.extend(page_errors(root))
            errors.extend(page_html_errors(root))
            require_text("outputs/menu-and-page-planning-reference.md")
        elif stage == "S7":
            errors.extend(feature_artifact_errors(root))
            errors.extend(page_errors(root, final=True))
            facts = load_json(root / "memory/project-facts.json")
            if not facts.get("project"):
                errors.append("project-facts.json 的 project 尚未填写")
            rule_data = load_json(root / "memory/business-rules.json")
            business_flows = rule_data.get("business_flows", [])
            features = indexed_records(root, "memory/feature-list.json", "features")
            scenarios = indexed_records(root, "scenarios/scenario-index.json", "scenarios")
            in_scope_scenarios = {
                str(item.get("id"))
                for item in scenarios
                if item.get("scope", "in_scope") == "in_scope"
            }
            if in_scope_scenarios and not features and not attribution_report(root)["validated_retained_keys_with_direct_ownership"]:
                errors.append("feature-list.json 尚无本期功能或有效存量归属，无法覆盖本期业务场景")
            if in_scope_scenarios and (not isinstance(business_flows, list) or not business_flows):
                errors.append("business-rules.json 尚无业务流程，无法生成跨场景业务流程图")
            if isinstance(business_flows, list):
                seen_flow_ids: set[str] = set()
                for flow in business_flows:
                    if not isinstance(flow, dict):
                        errors.append("business_flows 包含非对象记录")
                        continue
                    flow_id = str(flow.get("id", ""))
                    if not re.fullmatch(r"FLOW-\d{3,}", flow_id):
                        errors.append(f"业务流程 ID 不合法：{flow_id!r}")
                    elif flow_id in seen_flow_ids:
                        errors.append(f"业务流程 ID 重复：{flow_id}")
                    seen_flow_ids.add(flow_id)
                    if not isinstance(flow.get("name"), str) or not str(flow.get("name")).strip():
                        errors.append(f"{flow_id or '未知业务流程'} 缺少 name")
                    steps = flow.get("steps", [])
                    if not isinstance(steps, list) or not steps or any(not str(step).strip() for step in steps):
                        errors.append(f"{flow_id or '未知业务流程'} 缺少有效业务步骤")
            covered_scenarios: set[str] = set()
            seen_feature_ids: set[str] = set()
            for item in features:
                feature_id = str(item.get("id", ""))
                if not re.fullmatch(r"FUNC-\d{3,}", feature_id):
                    errors.append(f"功能 ID 不合法：{feature_id!r}")
                elif feature_id in seen_feature_ids:
                    errors.append(f"功能 ID 重复：{feature_id}")
                seen_feature_ids.add(feature_id)
                for field in ("version", "module_l1", "module_l2", "name", "description"):
                    value = item.get(field)
                    if not isinstance(value, str) or not value.strip():
                        errors.append(f"{feature_id or '未知功能'} 缺少 {field}")
                if item.get("module_l3") is not None and (
                    not isinstance(item.get("module_l3"), str) or not item.get("module_l3", "").strip()
                ):
                    errors.append(f"{feature_id or '未知功能'} 的 module_l3 应为非空文本或 null")
                if item.get("notes") is not None and not isinstance(item.get("notes"), str):
                    errors.append(f"{feature_id or '未知功能'} 的 notes 应为文本或 null")
                if item.get("change_type") not in FEATURE_CHANGE_TYPES:
                    errors.append(f"{feature_id or '未知功能'} 的 change_type 只允许 new 或 iteration")
                covered_scenarios.update(str(value) for value in item.get("source_scenario_ids", []))
            errors.extend(scenario_coverage_errors(root, covered_scenarios, in_scope_scenarios))
            require_text("outputs/feature-list.md")
            require_text("outputs/product-structure-and-business-process-baseline.md")
        elif stage == "S8":
            report = load_json(root / "runtime/verification-report.json")
            if report.get("status") != "pass":
                errors.append("全局核验报告尚未通过")
        elif stage == "S9":
            s9_delivery_manifest(root)
    except (LoopError, ValueError, OSError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return errors


def next_stage(stage: str) -> str | None:
    index = STAGE_IDS.index(stage)
    return STAGE_IDS[index + 1] if index + 1 < len(STAGE_IDS) else None


def create_initial_state(name: str, version: str) -> dict[str, Any]:
    timestamp = now()
    return {
        "schema_version": "1.0",
        "workflow_version": WORKFLOW_VERSION,
        "engine": {"name": "requirement-analysis-loop", "version": ENGINE_VERSION},
        "project": {"name": name, "version": version},
        "run": {
            "mode": "first_run",
            "status": "active",
            "current_stage": "S0",
            "started_at": timestamp,
            "updated_at": timestamp,
        },
        "stages": {
            stage_id: {
                "name": title,
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "evidence": [],
                "issues": [],
            }
            for stage_id, title in STAGES
        },
        "gates": {
            gate_id: {
                "name": meta["name"],
                "after_stage": meta["after"],
                "status": "pending",
                "approved_by": None,
                "approved_at": None,
                "note": None,
            }
            for gate_id, meta in GATES.items()
        },
        "history": [{"at": timestamp, "event": "initialized"}],
    }


def cmd_init(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    if state_path(root).exists():
        raise LoopError(f"项目已经初始化：{state_path(root)}")

    template = Path(__file__).resolve().parent.parent / "assets" / "project-template"
    if not template.is_dir():
        raise LoopError(f"缺少项目模板：{template}")

    conflicts = []
    if root.exists():
        for source in template.rglob("*"):
            if source.is_file() and (root / source.relative_to(template)).exists():
                conflicts.append(str(source.relative_to(template)))
    if conflicts:
        raise LoopError("目标目录存在模板同名文件，未写入：" + ", ".join(conflicts))

    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, root, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('business-scenario-atlas.html', 'interaction-sequence-atlas.html'))
    for relative in ["inputs/interface-documents", "deliverables"]:
        (root / relative).mkdir(parents=True, exist_ok=True)
    state = create_initial_state(args.name, args.version)
    atomic_json(state_path(root), state)
    print(f"已初始化需求分析 Loop：{root}")
    print("当前阶段：S0 输入接收")


def cmd_status(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    state = load_state(root, read_only=True)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    project = state["project"]
    run = state["run"]
    print(f"项目：{project['name']}  版本：{project['version']}")
    print(f"运行状态：{run['status']}  当前阶段：{run['current_stage']}")
    if state.get("workflow_version") != WORKFLOW_VERSION:
        print("旧版/未知顺序：仅展示原阶段名称；升级请在新隔离目录重跑，当前状态未改写。")
    for stage_id, title in STAGES:
        title = state["stages"][stage_id]["name"]
        marker = "→" if stage_id == run["current_stage"] else " "
        print(f"{marker} {stage_id} {title}: {state['stages'][stage_id]['status']}")
    print("门禁：" + "  ".join(f"{gate}={state['gates'][gate]['status']}" for gate in GATES))


def cmd_start(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    state = load_state(root)
    run = state["run"]
    stage = run["current_stage"]
    if run["status"] == "complete":
        raise LoopError("Loop 已完成；如有变化请先执行 invalidate")
    record = state["stages"][stage]
    if run["status"] == "needs_confirmation" and record["status"] == "passed":
        raise LoopError("当前停在人工门禁，需取得明确批准后执行 approve")
    if record["status"] not in {"pending", "stale", "failed", "blocked", "needs_confirmation"}:
        raise LoopError(f"{stage} 当前状态为 {record['status']}，不能重新开始")
    errors = earlier_approval_errors(root, state, stage)
    if errors:
        raise LoopError("；".join(errors))
    timestamp = now()
    record["status"] = "in_progress"
    record["started_at"] = timestamp
    record["completed_at"] = None
    run["status"] = "active"
    save_state(root, state, "stage_started", {"stage": stage})
    print(f"已开始 {stage} {record['name']}")


def cmd_complete(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    state = load_state(root)
    if state["run"]["current_stage"] == "S9":
        with s9_completion_guard(root) as recheck:
            current_state = load_state(root)
            if current_state != state:
                raise LoopError("S9 完成前流程状态已变化，未写完成状态")
            complete_stage(root, state, args, recheck)
    else:
        complete_stage(root, state, args)


def s8_review_snapshot(root: Path) -> dict:
    report = load_json(root / "runtime/verification-report.json")
    if report.get("status") != "pass":
        raise LoopError("全局核验报告未通过")
    reviewed = report.get("approval_snapshot")
    if reviewed != approval_snapshot(root):
        raise LoopError("核验报告已过期，当前事实或图集与核验版本不一致，须重新核验")
    return reviewed


def complete_stage(root: Path, state: dict[str, Any], args: argparse.Namespace, recheck=None) -> None:
    run = state["run"]
    stage = run["current_stage"]
    if args.stage and args.stage != stage:
        raise LoopError(f"当前阶段是 {stage}，不能完成 {args.stage}")
    record = state["stages"][stage]
    if record["status"] != "in_progress":
        raise LoopError(f"{stage} 必须先进入 in_progress，当前为 {record['status']}")
    delivery_manifest = s9_delivery_manifest(root) if stage == "S9" else None
    errors = readiness_errors(root, stage) if stage != "S9" else []
    if errors:
        raise LoopError("阶段完成条件未满足：\n- " + "\n- ".join(errors))

    default_evidence = ([str(delivery_manifest)] if stage == "S9" else DEFAULT_EVIDENCE[stage])
    default_evidence = [p for p in default_evidence if included(root, p)]
    evidence = list(dict.fromkeys(args.evidence or default_evidence))
    for relative in evidence:
        candidate = Path(relative).expanduser()
        if not candidate.is_absolute():
            candidate = safe_relative(root, relative)
        if not candidate.exists():
            raise LoopError(f"证据文件不存在：{relative}")

    if recheck:
        recheck()
    reviewed = s8_review_snapshot(root) if stage == "S8" else None
    timestamp = now()
    if reviewed is not None:
        if s8_review_snapshot(root) != reviewed or load_state(root) != state:
            raise LoopError("S8 完成期间流程状态或核验版本发生变化，请重新核对")
        record["reviewed_fingerprint"] = reviewed["fingerprint"]
    record["status"] = "passed"
    record["completed_at"] = timestamp
    record["evidence"] = evidence
    gate = GATE_AFTER.get(stage)
    if gate:
        run["status"] = "needs_confirmation"
        save_state(root, state, "stage_completed_waiting_gate", {"stage": stage, "gate": gate})
        print(f"{stage} 已通过，等待人工门禁 {gate}：{GATES[gate]['name']}")
        return

    following = next_stage(stage)
    if following:
        run["current_stage"] = following
        run["status"] = "active"
        save_state(root, state, "stage_completed", {"stage": stage, "next_stage": following})
        print(f"{stage} 已通过；下一阶段：{following} {state['stages'][following]['name']}")
    else:
        run["status"] = "complete"
        save_state(root, state, "loop_completed", {"stage": stage})
        print("需求分析 Loop 已完成")


def unresolved_high_items(root: Path) -> list[str]:
    data = load_json(root / "runtime" / "open-items.json")
    result = []
    for item in data.get("items", []):
        if item.get("status", "open") == "open" and item.get("severity") in {"critical", "high"}:
            result.append(str(item.get("id", "未编号问题")))
    return result


def cmd_approve(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    state = load_state(root)
    gate_id = args.gate
    meta = GATES[gate_id]
    gate = state["gates"][gate_id]
    if state["stages"][meta["after"]]["status"] != "passed":
        raise LoopError(f"{meta['after']} 尚未通过，不能批准 {gate_id}")
    if state["run"]["current_stage"] != meta["after"] or state["run"]["status"] != "needs_confirmation":
        raise LoopError(f"当前运行状态不在 {gate_id} 门禁")
    basis = None
    reviewed_snapshot = None
    if gate_id in {"G1", "G2"}:
        basis = approval_basis(root, gate_id)
        errors = readiness_errors(root, meta["after"])
        if errors:
            raise LoopError(f"{gate_id} 确认条件未满足：" + "；".join(errors))
    if gate_id == "G3":
        errors = approval_basis_errors(root, state, ("G1", "G2"))
        if errors:
            raise LoopError("；".join(errors))
        reviewed_snapshot = s8_review_snapshot(root)
        if state["stages"]["S8"].get("reviewed_fingerprint") != reviewed_snapshot["fingerprint"]:
            raise LoopError("核验版本与 S8 完成时记录的确认依据不一致，须回到 S8 重新核验后确认 G3")
        from verify_analysis import Verifier
        fresh_report = Verifier(root).verify()
        if fresh_report['status'] != 'pass':
            raise LoopError('当前交付内容未通过重新核验，不能批准 G3')
        if fresh_report['approval_snapshot'] != reviewed_snapshot:
            raise LoopError('G3 重新核验内容与原报告不一致，须重新核验后确认 G3')
        blockers = unresolved_high_items(root)
        if blockers:
            raise LoopError("存在未关闭的关键问题：" + ", ".join(blockers))

    timestamp = now()
    if basis is not None and approval_basis(root, gate_id) != basis:
        raise LoopError(f"{gate_id} 确认期间依据发生变化，请重新核验与确认")
    if reviewed_snapshot is not None and approval_snapshot(root) != reviewed_snapshot:
        raise LoopError('G3 确认期间交付内容发生变化，须重新核验后确认 G3')
    if load_state(root) != state:
        raise LoopError(f"{gate_id} 确认期间流程状态已变化，请重新核对")
    gate.update({"status": "approved", "approved_by": args.by, "approved_at": timestamp, "note": args.note})
    if reviewed_snapshot is not None:
        gate['approval_snapshot'] = reviewed_snapshot
    if basis is not None:
        gate.update(basis)
    state["run"]["current_stage"] = meta["next"]
    state["run"]["status"] = "active"
    save_state(root, state, "gate_approved", {"gate": gate_id, "by": args.by, "next_stage": meta["next"]})
    print(f"{gate_id} 已批准；下一阶段：{meta['next']} {state['stages'][meta['next']]['name']}")


def prefix_stage(item_id: str) -> str:
    match = re.fullmatch(r"([A-Z]+)-\d{3,}", item_id)
    if not match or match.group(1) not in PREFIX_STAGE:
        raise LoopError(f"无法判断对象的所属阶段：{item_id}")
    return PREFIX_STAGE[match.group(1)]


def impacted_ids(root: Path, seeds: list[str]) -> list[str]:
    trace = load_json(root / "runtime" / "traceability.json")
    graph: dict[str, set[str]] = defaultdict(set)
    for link in trace.get("links", []):
        source, target = link.get("from"), link.get("to")
        if source and target:
            graph[str(source)].add(str(target))
    seen = set(seeds)
    queue = deque(seeds)
    while queue:
        current = queue.popleft()
        for target in graph.get(current, set()):
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return sorted(seen)


def cmd_invalidate(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    state = load_state(root)
    if args.from_stage:
        impacted: list[str] = []
        changed_ids: list[str] = []
        earliest = args.from_stage
    else:
        changed_ids = args.ids
        impacted = impacted_ids(root, changed_ids)
        # Semantic backlinks (SEQ calls API; FUNC is surfaced by PAGE) are not
        # generation dependencies. Restart at changed facts, not every referenced fact.
        stages = [prefix_stage(item_id) for item_id in changed_ids]
        earliest = min(stages, key=STAGE_IDS.index)
    # A second invalidation must never jump over an earlier unfinished stage/gate.
    unfinished = [stage_id for stage_id in STAGE_IDS if state["stages"][stage_id]["status"] != "passed"]
    unfinished.extend(meta["after"] for gate_id, meta in GATES.items()
                      if state["gates"][gate_id]["status"] != "approved")
    earliest = min([earliest, *unfinished], key=STAGE_IDS.index)
    earliest_index = STAGE_IDS.index(earliest)

    from project_versions import register_change, VersionError
    try:
        register_change(root, getattr(args, "change_id", None), "analysis", args.reason,
                        str(root / "runtime/change-impact.json"), getattr(args, "ids", None) or [])
    except VersionError as exc:
        raise LoopError(str(exc)) from exc

    for stage_id in STAGE_IDS[earliest_index:]:
        record = state["stages"][stage_id]
        record.pop("reviewed_fingerprint", None)
        if record["status"] != "pending" or stage_id == earliest:
            record["status"] = "stale"
            record["completed_at"] = None
    for gate_id, meta in GATES.items():
        if STAGE_IDS.index(meta["after"]) >= earliest_index:
            state["gates"][gate_id].update(
                {"status": "pending", "approved_by": None, "approved_at": None, "note": None}
            )
            for key in ("scenario_fingerprint", "interface_fingerprint", "interface_sources_fingerprint", "approval_snapshot"):
                state["gates"][gate_id].pop(key, None)
    state["run"].update({"mode": "incremental", "status": "active", "current_stage": earliest})
    impact = {
        "schema_version": "1.0",
        "generated_at": now(),
        "reason": args.reason,
        "changed_ids": changed_ids,
        "impacted_ids": impacted,
        "restart_stage": earliest,
    }
    atomic_json(root / "runtime" / "change-impact.json", impact)
    save_state(
        root,
        state,
        "invalidated",
        {"changed_ids": changed_ids, "restart_stage": earliest, "reason": args.reason},
    )
    if impacted:
        print(f"已计算影响对象 {len(impacted)} 个；从 {earliest} 重新运行")
    else:
        print(f"已按阶段失效；从 {earliest} 重新运行")
    print("影响清单：runtime/change-impact.json")


def cmd_request_diagrams(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    selection(root)
    if not (root / POLICY).is_file():
        raise LoopError('旧项目保留原图集合同；请在新工作副本采用可选图集规则')
    value = load_json(root / POLICY)
    kinds = [k for k, enabled in [('business_flow', args.business_flow),
                                  ('interaction_sequence', args.interaction_sequence)] if enabled]
    if not kinds or not args.by.strip() or not args.reason.strip():
        raise LoopError('必须指定图集类型、请求人及请求依据')
    changed = [k for k in kinds if not value['requested'][k]]
    if not changed:
        print('对应图集已请求；未改写基线')
        return
    args.from_stage = 'S3' if 'business_flow' in changed else 'S5'
    args.ids = []
    cmd_invalidate(args)
    for kind in changed:
        value['requested'][kind] = True
        value['requests'].append({'kind': kind, 'by': args.by, 'reason': args.reason, 'at': now()})
    atomic_json(root / POLICY, value)
    print('已登记显式图集请求；须重新渲染、核验并确认 G3，旧冻结包保持不变')


def cmd_hold(args: argparse.Namespace) -> None:
    root = project_root(args.project_dir)
    state = load_state(root)
    stage = state["run"]["current_stage"]
    record = state["stages"][stage]
    record["status"] = args.status
    record.setdefault("issues", []).append({"at": now(), "status": args.status, "reason": args.reason})
    state["run"]["status"] = args.status
    save_state(root, state, "stage_held", {"stage": stage, "status": args.status, "reason": args.reason})
    print(f"{stage} 已标记为 {args.status}：{args.reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="需求分析 Loop 状态管理")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="初始化分析项目")
    init.add_argument("project_dir")
    init.add_argument("--name", required=True)
    init.add_argument("--version", default="0.1.0")
    init.set_defaults(func=cmd_init)

    diagrams = sub.add_parser('request-diagrams', help='记录用户显式请求并失效相关阶段')
    diagrams.add_argument('project_dir')
    diagrams.add_argument('--business-flow', action='store_true')
    diagrams.add_argument('--interaction-sequence', action='store_true')
    diagrams.add_argument('--by', required=True)
    diagrams.add_argument('--reason', required=True)
    diagrams.add_argument('--change-id')
    diagrams.set_defaults(func=cmd_request_diagrams)

    status = sub.add_parser("status", help="查看状态")
    status.add_argument("project_dir")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    start = sub.add_parser("start", help="开始当前阶段")
    start.add_argument("project_dir")
    start.set_defaults(func=cmd_start)

    complete = sub.add_parser("complete", help="完成当前阶段")
    complete.add_argument("project_dir")
    complete.add_argument("--stage", choices=STAGE_IDS)
    complete.add_argument("--evidence", action="append", default=[])
    complete.set_defaults(func=cmd_complete)

    approve = sub.add_parser("approve", help="记录明确的人工门禁批准")
    approve.add_argument("project_dir")
    approve.add_argument("gate", choices=sorted(GATES))
    approve.add_argument("--by", required=True)
    approve.add_argument("--note", required=True)
    approve.set_defaults(func=cmd_approve)

    invalidate = sub.add_parser("invalidate", help="计算变更影响并回退状态")
    invalidate.add_argument("project_dir")
    invalidate_target = invalidate.add_mutually_exclusive_group(required=True)
    invalidate_target.add_argument("--id", dest="ids", action="append")
    invalidate_target.add_argument("--from-stage", choices=STAGE_IDS[1:])
    invalidate.add_argument("--reason", required=True)
    invalidate.add_argument("--change-id", help="Versioned project change ID; required after attach")
    invalidate.set_defaults(func=cmd_invalidate)

    hold = sub.add_parser("hold", help="标记当前阶段阻塞或失败")
    hold.add_argument("project_dir")
    hold.add_argument("--status", choices=["blocked", "failed", "needs_confirmation"], required=True)
    hold.add_argument("--reason", required=True)
    hold.set_defaults(func=cmd_hold)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except (LoopError, ValueError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
