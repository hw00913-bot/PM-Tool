#!/usr/bin/env python3
"""Create an immutable, checksummed prototype-input snapshot."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import zipfile

from source_index import SOURCE_INDEX, inspect_source_index, source_issue_errors
from analysis_contract import WORKFLOW_VERSION, PAGE_STRUCTURE, approval_basis_errors, page_html_errors, sequence_html_errors
from feature_contract import feature_artifact_errors
from atlas_reading import desktop_atlas_errors
from loop import load_state, LoopError
from frozen_snapshot import require_approved_snapshot
from verify_analysis import Verifier
from diagram_policy import POLICY, PATHS, requested, included, selection


INCLUDE_DIRS = ["inputs", "memory", "scenarios", "sequences", "interfaces", "planning", "outputs"]
INCLUDE_RUNTIME = ["runtime/traceability.json", "runtime/open-items.json", "runtime/verification-report.json"]
HANDOFF_CONTRACT = {"name": "requirement-analysis-to-prototype", "version": "1.0"}

ENTRYPOINTS = {
    "normalized_user_stories": ("memory/normalized-user-stories.json", "application/json", "business_fact"),
    "scenario_index": ("scenarios/scenario-index.json", "application/json", "business_fact"),
    "sequence_index": ("sequences/sequence-index.json", "application/json", "interaction_fact"),
    "interface_index": ("interfaces/interface-index.json", "application/json", "external_dependency_fact"),
    "project_facts": ("memory/project-facts.json", "application/json", "business_fact"),
    "business_rules": ("memory/business-rules.json", "application/json", "business_fact"),
    "feature_list": ("memory/feature-list.json", "application/json", "product_fact"),
    "page_index": ("planning/page-index.json", "application/json", "prototype_planning_fact"),
    "traceability": ("runtime/traceability.json", "application/json", "trace_fact"),
    "open_items": ("runtime/open-items.json", "application/json", "risk_fact"),
    "verification_report": ("runtime/verification-report.json", "application/json", "verification_evidence"),
    "business_scenario_atlas": ("outputs/business-scenario-atlas.html", "text/html", "review_view"),
    "interaction_sequence_atlas": ("outputs/interaction-sequence-atlas.html", "text/html", "review_view"),
    "interface_verification_matrix": (
        "outputs/system-sequence-interface-verification-matrix.md",
        "text/markdown",
        "review_view",
    ),
    "product_baseline": (
        "outputs/product-structure-and-business-process-baseline.md",
        "text/markdown",
        "review_view",
    ),
    "feature_list_view": ("outputs/feature-list.md", "text/markdown", "review_view"),
    "page_planning": ("outputs/menu-and-page-planning-reference.md", "text/markdown", "review_view"),
    "page_structure_atlas": (PAGE_STRUCTURE, "text/html", "review_view"),
}


class PackageError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PackageError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise PackageError(f"JSON 无法解析：{path}（{exc}）") from exc


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slug(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-_.")
    return cleaned or "requirement-analysis"


def collect_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_dir in INCLUDE_DIRS:
        directory = root / relative_dir
        if not directory.is_dir():
            raise PackageError(f"缺少交付目录：{relative_dir}")
        files.extend(path for path in directory.rglob("*") if path.is_file() and not path.name.startswith("."))
    for relative in INCLUDE_RUNTIME:
        path = root / relative
        if not path.is_file():
            raise PackageError(f"缺少交付文件：{relative}")
        files.append(path)
    if (root / "project-version.json").exists():
        files.append(root / "project-version.json")
    return sorted({p for p in files if included(root, p.relative_to(root).as_posix())}, key=lambda path: str(path.relative_to(root)))


def file_entries(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in files
    ]


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(encoded)


def required_entrypoints(root: Path) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for name, (relative, media_type, role) in ENTRYPOINTS.items():
        if not included(root, relative):
            continue
        if not (root / relative).is_file():
            raise PackageError(f"缺少原型交接入口：{relative}")
        result[name] = {"path": relative, "media_type": media_type, "role": role}
    if (root / SOURCE_INDEX).is_file():
        result["source_index"] = {"path": SOURCE_INDEX, "media_type": "application/json", "role": "provenance_only"}
    return result


def id_catalog(root: Path) -> dict[str, list[str]]:
    stories = load_json(root / "memory" / "normalized-user-stories.json").get("user_stories", [])
    scenarios = load_json(root / "scenarios" / "scenario-index.json").get("scenarios", [])
    sequences = load_json(root / "sequences" / "sequence-index.json").get("sequences", [])
    interfaces = load_json(root / "interfaces" / "interface-index.json").get("interfaces", [])
    features = load_json(root / "memory" / "feature-list.json").get("features", [])
    pages = load_json(root / "planning" / "page-index.json").get("pages", [])

    def ids(items: list[Any]) -> list[str]:
        return sorted({str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")})

    acceptance_ids = sorted(
        {
            str(criteria.get("id"))
            for story in stories
            if isinstance(story, dict)
            for criteria in story.get("acceptance_criteria", [])
            if isinstance(criteria, dict) and criteria.get("id")
        }
    )
    in_scope_stories = [
        story for story in stories
        if isinstance(story, dict) and story.get("status", "in_scope") == "in_scope"
    ]
    required_acceptance_ids = sorted(
        {
            str(criteria.get("id"))
            for story in in_scope_stories
            for criteria in story.get("acceptance_criteria", [])
            if isinstance(criteria, dict) and criteria.get("id")
        }
    )
    action_ids = sorted(
        {
            str(action.get("id"))
            for page in pages
            if isinstance(page, dict)
            for action in page.get("actions", [])
            if isinstance(action, dict) and action.get("id")
        }
    )
    return {
        "user_story_ids": ids(stories),
        "in_scope_user_story_ids": ids(in_scope_stories),
        "acceptance_ids": acceptance_ids,
        "required_acceptance_ids": required_acceptance_ids,
        "scenario_ids": ids(scenarios),
        "in_scope_scenario_ids": ids([
            item for item in scenarios
            if isinstance(item, dict) and item.get("scope", "in_scope") == "in_scope"
        ]),
        "sequence_ids": ids(sequences),
        "interface_ids": ids(interfaces),
        "feature_ids": ids(features),
        "page_ids": ids(pages),
        "action_ids": action_ids,
    }


def accepted_risks(root: Path) -> list[dict[str, Any]]:
    items = load_json(root / "runtime" / "open-items.json").get("items", [])
    return [
        {
            "id": item.get("id"),
            "severity": item.get("severity"),
            "summary": item.get("summary") or item.get("title") or item.get("description"),
            "related_ids": item.get("related_ids", []),
            "resolution": item.get("resolution") or item.get("decision") or item.get("note"),
        }
        for item in items
        if isinstance(item, dict) and item.get("status") == "accepted_risk"
    ]


def diagram_renderer_mode(html: str, diagram_type: str) -> str:
    openings = re.findall(r"<svg\b[^>]*>", html, flags=re.IGNORECASE)
    matching = [
        opening
        for opening in openings
        if f'data-diagram-type="{diagram_type}"' in opening
        or f"data-diagram-type='{diagram_type}'" in opening
    ]
    has_plantuml = any('data-plantuml-render="pass"' in opening for opening in matching)
    has_native = any('data-plantuml-render="pass"' not in opening for opening in matching)
    if has_plantuml and has_native:
        return "compare"
    if has_plantuml:
        return "plantuml"
    return "native"


def diagram_metadata(root: Path) -> dict[str, dict[str, Any]]:
    scenario_index = load_json(root / "scenarios" / "scenario-index.json")
    sequence_index = load_json(root / "sequences" / "sequence-index.json")
    in_scope_scenarios = [
        item
        for item in scenario_index.get("scenarios", [])
        if isinstance(item, dict) and item.get("scope", "in_scope") == "in_scope"
    ]
    business_flows = load_json(root / "memory" / "business-rules.json").get("business_flows", [])
    scenario_html = (root / PATHS['business_flow']).read_text(encoding="utf-8") if requested(root, 'business_flow') else ''
    sequence_html = (root / PATHS['interaction_sequence']).read_text(encoding="utf-8") if requested(root, 'interaction_sequence') else ''
    result = {
        "business_processes": {
            "source": "memory/business-rules.json#business_flows",
            "html": "outputs/business-scenario-atlas.html",
            "count": len(business_flows) if isinstance(business_flows, list) else 0,
            "renderer_mode": diagram_renderer_mode(scenario_html, "business-process"),
            "self_contained": True,
        },
        "business_scenarios": {
            "source": "scenarios/scenario-index.json",
            "html": "outputs/business-scenario-atlas.html",
            "count": len(in_scope_scenarios),
            "renderer_mode": diagram_renderer_mode(scenario_html, "swimlane"),
            "self_contained": True,
        },
        "interaction_sequences": {
            "source": "sequences/sequence-index.json",
            "html": "outputs/interaction-sequence-atlas.html",
            "count": len(sequence_index.get("sequences", [])),
            "renderer_mode": diagram_renderer_mode(sequence_html, "sequence"),
            "self_contained": True,
        },
        "page_structure": {
            "source": "planning/page-index.json",
            "html": PAGE_STRUCTURE,
            "count": 1,
            "renderer_mode": "native",
            "self_contained": True,
        },
    }


    for key, kind in [('business_processes', 'business_flow'), ('business_scenarios', 'business_flow'),
                      ('interaction_sequences', 'interaction_sequence')]:
        if not requested(root, kind):
            result[key].update(html=None, renderer_mode=None, self_contained=False, status='not_requested')
    return result


def build_handoff(
    root: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    frozen_at: str,
    semantic_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    project = state.get("project", {})
    fingerprint = canonical_hash({"project": project, "files": semantic_entries})
    baseline_id = f"ra-{slug(str(project.get('name', root.name))).lower()}-{fingerprint[:16]}"
    gates = {
        gate_id: {
            "status": gate.get("status"),
            "approved_by": gate.get("approved_by"),
            "approved_at": gate.get("approved_at"),
            "note": gate.get("note"),
            **({'approval_snapshot': gate['approval_snapshot']} if 'approval_snapshot' in gate else {}),
            **({'scenario_fingerprint': gate['scenario_fingerprint']} if 'scenario_fingerprint' in gate else {}),
            **({key: gate[key] for key in ('interface_fingerprint', 'interface_sources_fingerprint')}
               if 'interface_sources_fingerprint' in gate else {}),
        }
        for gate_id, gate in state.get("gates", {}).items()
        if gate_id in {"G1", "G2", "G3"}
    }
    return {
        "schema_version": "1.0",
        "contract": HANDOFF_CONTRACT,
        "baseline": {
            "id": baseline_id,
            "fingerprint": fingerprint,
            "frozen_at": frozen_at,
            "immutability": "manifest_sha256",
        },
        "analysis_engine": state.get("engine", {"name": "requirement-analysis-loop", "version": "legacy"}),
        "analysis_workflow_version": WORKFLOW_VERSION,
        "project": project,
        "gates": gates,
        "verification": {
            "status": report.get("status"),
            "generated_at": report.get("generated_at"),
            "summary": report.get("summary", {}),
            "warning_count": len(report.get("warnings", [])),
        },
        "accepted_risks": accepted_risks(root),
        "id_catalog": id_catalog(root),
        "entrypoints": required_entrypoints(root),
        "diagrams": diagram_metadata(root),
        **({'diagram_deliverables': load_json(root / POLICY)} if (root / POLICY).exists() else {}),
        "consumer_rules": {
            "llm_wiki_consumed_by": "requirement-analysis-loop",
            "raw_inputs_are_audit_only": True,
            "business_facts_are_read_only": True,
            "prototype_may_add_presentation_decisions": True,
            "diagram_html_must_be_reused_without_geometry_regeneration": True,
            "scope_change_requires_new_analysis_baseline": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成冻结的原型输入包")
    parser.add_argument("project_dir")
    parser.add_argument("--project-root", help="将已批准结果保存到项目/需求/业务版本/snapshots/确认快照；保留默认deliverables兼容入口")
    args = parser.parse_args()
    root = Path(args.project_dir).expanduser().resolve()
    try:
        try:
            state = load_state(root)
        except LoopError as exc:
            raise PackageError(str(exc)) from exc
        approval_errors = approval_basis_errors(root, state, ("G1", "G2"))
        if approval_errors:
            raise PackageError("；".join(approval_errors))
        diagram_problems = (page_html_errors(root) + feature_artifact_errors(root)
                           + (sequence_html_errors(root) if requested(root, 'interaction_sequence') else [])
                           + (desktop_atlas_errors(root, PATHS['business_flow']) if requested(root, 'business_flow') else []))
        if diagram_problems:
            raise PackageError("；".join(diagram_problems))
        if state.get("gates", {}).get("G3", {}).get("status") != "approved":
            raise PackageError("G3 尚未由用户批准，不能生成正式交付包")
        if state.get("run", {}).get("current_stage") != "S9":
            raise PackageError("当前阶段不是 S9")
        report = load_json(root / "runtime" / "verification-report.json")
        if report.get("status") != "pass":
            raise PackageError("全局核验报告未通过")
        approved = require_approved_snapshot(root, state, report)
        fresh_report = Verifier(root).verify()
        if fresh_report['status'] != 'pass':
            raise PackageError('打包前重新核验失败：' + '；'.join(p['message'] for p in fresh_report['errors']))
        if fresh_report['approval_snapshot'] != approved:
            raise PackageError('打包前核验版本与 G3 批准不一致')
        sources, source_errors = inspect_source_index(root)
        source_errors.extend(source_issue_errors(sources, load_json(root / "runtime/open-items.json").get("items", [])))
        if source_errors:
            raise PackageError("来源索引核验失败：" + "；".join(source_errors))

        files = collect_files(root)
        if require_approved_snapshot(root, state, report) != approved:
            raise PackageError('打包期间源事实发生变化')
        semantic_entries = file_entries(root, files)
        project = state.get("project", {})
        frozen_at = now()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        package_name = f"{slug(str(project.get('name', root.name)))}-prototype-input-v{slug(str(project.get('version', '0.0.0')))}-{timestamp}.zip"
        relative_package = Path("deliverables") / package_name
        package_path = root / relative_package
        snapshot_dir = None
        if args.project_root:
            from project_versions import delivery_destination, VersionError
            try:
                snapshot_dir = delivery_destination(root, args.project_root, "analysis")
            except (VersionError, OSError, ValueError, KeyError, TypeError) as exc:
                raise PackageError(f"需求版本出口被阻止：{exc}") from exc
            snapshot_dir.mkdir(parents=True, exist_ok=False)
            relative_package = Path(package_name)
            package_path = snapshot_dir / package_name
        handoff = build_handoff(root, state, report, frozen_at, semantic_entries)
        from project_versions import reference, validate_reference
        version_ref = reference(root)
        if version_ref:
            try:
                bound = validate_reference(version_ref, "analysis")
            except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
                raise PackageError(f"项目版本基线批准已失效：{exc}") from exc
            handoff["project_version"] = {**version_ref, "snapshot_id": bound["snapshot_id"]}

        handoff_bytes = (json.dumps(handoff, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        handoff_entry = {
            "path": "prototype-handoff.json",
            "bytes": len(handoff_bytes),
            "sha256": sha256_bytes(handoff_bytes),
        }
        manifest = {
            "schema_version": "1.1",
            "analysis_engine": state.get("engine", {"name": "requirement-analysis-loop", "version": "legacy"}),
            "handoff_contract": HANDOFF_CONTRACT,
            "project": project,
            "frozen_at": frozen_at,
            "verification_status": report.get("status"),
            "baseline_id": handoff["baseline"]["id"],
            "baseline_fingerprint": handoff["baseline"]["fingerprint"],
            "package_file": relative_package.as_posix(),
            "files": [*semantic_entries, handoff_entry],
        }
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        package_path.parent.mkdir(parents=True, exist_ok=True)
        temp_package = package_path.with_suffix(".zip.tmp")
        with zipfile.ZipFile(temp_package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("delivery-manifest.json", manifest_bytes)
            archive.writestr("prototype-handoff.json", handoff_bytes)
            for path in files:
                relative = path.relative_to(root).as_posix()
                raw = path.read_bytes()
                expected = next(entry for entry in semantic_entries if entry['path'] == relative)
                if sha256_bytes(raw) != expected['sha256']:
                    raise PackageError('打包期间内容发生变化：' + relative)
                archive.writestr(relative, raw)
        require_approved_snapshot(root, state, report)
        temp_package.replace(package_path)
        output_dir = snapshot_dir or root / "deliverables"
        atomic_json(output_dir / "delivery-manifest.json", manifest)
        atomic_json(output_dir / "prototype-handoff.json", handoff)
        if snapshot_dir:
            from project_versions import record_artifact, publish_version_index, reference, VersionError
            outer_files = [{"path": p.name, "sha256": sha256(p)} for p in sorted(snapshot_dir.iterdir()) if p.is_file()]
            try:
                record_artifact(root, "analysis", snapshot_dir, {"files": outer_files}, entrypoint=package_name, kind="requirement-package")
                publish_version_index(reference(root)["registry"], args.project_root, reference(root)["version_id"])
            except (VersionError, OSError, ValueError, KeyError, TypeError) as exc:
                raise PackageError(f"快照未登记为正式版本结果：{exc}") from exc
            for p in snapshot_dir.iterdir():
                if p.is_file():p.chmod(0o444)
            snapshot_dir.chmod(0o555)
    except (PackageError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"已生成冻结包：{package_path}")
    print(f"基线：{handoff['baseline']['id']}")
    print(f"文件数量：{len(manifest['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
