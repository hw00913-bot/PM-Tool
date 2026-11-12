#!/usr/bin/env python3
"""Verify structural consistency and coverage across a requirement-analysis project."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any
from frozen_snapshot import approval_snapshot
from business_diagrams import business_html_errors

from swimlane_layout import audit_swimlane_layout, build_swimlane_layout
from swimlane_model import inspect_swimlane
from source_index import SOURCE_INDEX, inspect_source_index, source_issue_errors
from feature_contract import feature_artifact_errors
from operation_attribution import attribution_report, scenario_coverage_errors
from atlas_reading import desktop_atlas_errors
from analysis_contract import PAGE_STRUCTURE, approval_basis_errors, interface_errors, page_errors, page_html_errors, sequence_html_errors


ID_PATTERN = re.compile(r"([A-Z]+)-\d{3,}")
ALLOWED_PREFIXES = {"SRC", "US", "AC", "SC", "BR", "FLOW", "SEQ", "API", "FUNC", "PAGE", "ACTION", "ISSUE"}
INTERFACE_STATUSES = {"verified", "partial", "missing", "conflict", "not_required"}
FEATURE_CHANGE_TYPES = {"new", "iteration"}
ISSUE_SEVERITIES = {"critical", "high", "medium", "low"}
ISSUE_STATUSES = {"open", "resolved", "accepted_risk"}
ISSUE_RESOLUTION_SCOPES = {"first_party_analysis_design", "interface_contract_disposition"}
DRAFT_MARKER = "status: draft"
CORE_OUTPUTS = [
    "outputs/business-scenario-list.md",
    "outputs/business-scenario-atlas.html",
    "outputs/interaction-sequence-atlas.html",
    "outputs/system-sequence-interface-verification-matrix.md",
    "outputs/feature-list.md",
    "outputs/product-structure-and-business-process-baseline.md",
    "outputs/menu-and-page-planning-reference.md",
    PAGE_STRUCTURE,
]


def issue_disposes_interface(item: dict[str, Any]) -> bool:
    """Closing our analysis design never accepts an unresolved external API.

    Legacy resolved rows without this optional scope retain their contract.
    Unknown explicit values fail closed and are also reported by the verifier.
    """
    if ("resolution_scope" in item and (not isinstance(item["resolution_scope"], str)
                                       or item["resolution_scope"] not in ISSUE_RESOLUTION_SCOPES)):
        return False
    if item.get("status") == "accepted_risk":
        return True
    return (item.get("status") == "resolved"
            and item.get("resolution_scope") != "first_party_analysis_design")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


class Verifier:
    def __init__(self, root: Path):
        self.root = root
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        self.summary: dict[str, Any] = {}

    def add(self, level: str, code: str, message: str, artifact: str | None = None, ids: list[str] | None = None) -> None:
        item: dict[str, Any] = {"code": code, "message": message}
        if artifact:
            item["artifact"] = artifact
        if ids:
            item["ids"] = ids
        (self.errors if level == "error" else self.warnings).append(item)

    def error(self, code: str, message: str, artifact: str | None = None, ids: list[str] | None = None) -> None:
        self.add("error", code, message, artifact, ids)

    def warning(self, code: str, message: str, artifact: str | None = None, ids: list[str] | None = None) -> None:
        self.add("warning", code, message, artifact, ids)

    def load(self, relative: str, default: Any) -> Any:
        path = self.root / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.error("missing_file", f"缺少文件：{relative}", relative)
        except json.JSONDecodeError as exc:
            self.error("invalid_json", f"JSON 无法解析：{exc}", relative)
        return default

    def safe_path(self, relative: str, owner_id: str) -> Path | None:
        path = (self.root / relative).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            self.error("path_escape", f"{owner_id} 的详情文件越出项目目录", ids=[owner_id])
            return None
        return path

    def check_ids(self, records: list[dict[str, Any]], prefix: str, artifact: str) -> set[str]:
        values = [str(item.get("id", "")) for item in records]
        for value in values:
            if not re.fullmatch(rf"{prefix}-\d{{3,}}", value):
                self.error("invalid_id", f"ID 不符合 {prefix}-NNN：{value!r}", artifact, [value] if value else None)
        for value, count in Counter(values).items():
            if value and count > 1:
                self.error("duplicate_id", f"ID 重复：{value}", artifact, [value])
        return {value for value in values if value}

    def check_source_ref(self, source_ref: Any, owner_id: str, artifact: str) -> None:
        value = str(source_ref or "")
        if not value:
            self.error("empty_source_ref", f"{owner_id} 包含空的来源引用", artifact, [owner_id])
            return
        if "://" in value:
            return
        file_part = value.split("#", 1)[0]
        if not file_part:
            self.error("unlocatable_source_ref", f"{owner_id} 的来源无法定位：{value}", artifact, [owner_id])
            return
        path = self.safe_path(file_part, owner_id)
        if path and not path.is_file():
            self.error("missing_source_file", f"{owner_id} 的来源文件不存在：{file_part}", artifact, [owner_id])

    def verify(self) -> dict[str, Any]:
        from diagram_policy import requested, included
        business_requested = requested(self.root, 'business_flow')
        sequence_requested = requested(self.root, 'interaction_sequence')
        initial_snapshot = approval_snapshot(self.root)
        for problem in (business_html_errors(self.root) if business_requested else []):
            self.error('business_diagram_binding', problem)
        state = self.load("runtime/loop-state.json", {})
        for gate_id, code in (("G1", "changed_g1_scenarios"), ("G2", "changed_g2_interfaces")):
            if state.get("gates", {}).get(gate_id, {}).get("status") != "approved":
                continue
            try:
                for message in approval_basis_errors(self.root, state, (gate_id,)):
                    self.error(code, message)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                self.error(code, str(exc))
        for check in (lambda: interface_errors(self.root, require_sequences=True),
                      lambda: page_errors(self.root, final=True), lambda: page_html_errors(self.root),
                      lambda: sequence_html_errors(self.root) if sequence_requested else []):
            try:
                for message in check():
                    self.error("generation_order_contract", message)
            except (ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
                self.error("generation_order_contract", str(exc))
        stories_data = self.load("memory/normalized-user-stories.json", {"user_stories": []})
        scenario_data = self.load("scenarios/scenario-index.json", {"scenarios": []})
        sequence_data = self.load("sequences/sequence-index.json", {"sequences": []})
        interface_data = self.load("interfaces/interface-index.json", {"interfaces": []})
        feature_data = self.load("memory/feature-list.json", {"features": []})
        for problem in feature_artifact_errors(self.root):
            self.error("invalid_feature_boundary", problem, "memory/feature-list.json")
        page_data = self.load("planning/page-index.json", {"pages": []})
        rule_data = self.load("memory/business-rules.json", {"business_rules": [], "business_flows": []})
        trace_data = self.load("runtime/traceability.json", {"links": []})
        open_data = self.load("runtime/open-items.json", {"items": []})

        stories = stories_data.get("user_stories", []) if isinstance(stories_data.get("user_stories", []), list) else []
        scenarios = scenario_data.get("scenarios", []) if isinstance(scenario_data.get("scenarios", []), list) else []
        sequences = sequence_data.get("sequences", []) if isinstance(sequence_data.get("sequences", []), list) else []
        interfaces = interface_data.get("interfaces", []) if isinstance(interface_data.get("interfaces", []), list) else []
        features = feature_data.get("features", []) if isinstance(feature_data.get("features", []), list) else []
        pages = page_data.get("pages", []) if isinstance(page_data.get("pages", []), list) else []
        rules = rule_data.get("business_rules", []) if isinstance(rule_data.get("business_rules", []), list) else []
        flows = rule_data.get("business_flows", []) if isinstance(rule_data.get("business_flows", []), list) else []
        open_items = open_data.get("items", []) if isinstance(open_data.get("items", []), list) else []
        input_sources, source_errors = inspect_source_index(self.root)
        for message in [*source_errors, *source_issue_errors(input_sources, open_items)]:
            self.error("invalid_source_index", message, SOURCE_INDEX)
        source_ids = {str(item.get("id")) for item in input_sources if item.get("id")}

        story_ids = self.check_ids(stories, "US", "memory/normalized-user-stories.json")
        scenario_ids = self.check_ids(scenarios, "SC", "scenarios/scenario-index.json")
        sequence_ids = self.check_ids(sequences, "SEQ", "sequences/sequence-index.json")
        api_ids = self.check_ids(interfaces, "API", "interfaces/interface-index.json")
        feature_ids = self.check_ids(features, "FUNC", "memory/feature-list.json")
        page_ids = self.check_ids(pages, "PAGE", "planning/page-index.json")
        rule_ids = self.check_ids(rules, "BR", "memory/business-rules.json")
        flow_ids = self.check_ids(flows, "FLOW", "memory/business-rules.json")
        issue_ids = self.check_ids(open_items, "ISSUE", "runtime/open-items.json")

        action_records = [action for page in pages for action in page.get("actions", []) if isinstance(action, dict)]
        action_ids = self.check_ids(action_records, "ACTION", "planning/page-index.json")
        acceptance_records = [criterion for story in stories for criterion in story.get("acceptance_criteria", []) if isinstance(criterion, dict)]
        acceptance_ids = self.check_ids(acceptance_records, "AC", "memory/normalized-user-stories.json")
        known_ids = source_ids | story_ids | acceptance_ids | scenario_ids | sequence_ids | api_ids | feature_ids | page_ids | rule_ids | flow_ids | issue_ids | action_ids

        acceptance_owner: dict[str, str] = {}
        for story in stories:
            story_id = str(story.get("id"))
            if not story.get("acceptance_criteria"):
                self.error("story_without_acceptance", f"{story_id} 没有验收条件", "memory/normalized-user-stories.json", [story_id])
            for criterion in story.get("acceptance_criteria", []):
                if isinstance(criterion, dict) and criterion.get("id"):
                    acceptance_owner[str(criterion.get("id"))] = story_id
            for source_ref in story.get("source_refs", []):
                self.check_source_ref(source_ref, story_id, "memory/normalized-user-stories.json")

        in_scope_stories = {str(item.get("id")) for item in stories if item.get("status", "in_scope") == "in_scope"}
        in_scope_acceptance_ids = {
            acceptance_id
            for acceptance_id, story_id in acceptance_owner.items()
            if story_id in in_scope_stories
        }
        in_scope_scenarios = {
            str(item.get("id")) for item in scenarios if item.get("scope", "in_scope") == "in_scope"
        }
        scenario_by_id = {str(item.get("id")): item for item in scenarios}
        sequence_by_id = {str(item.get("id")): item for item in sequences}
        api_by_id = {str(item.get("id")): item for item in interfaces}

        story_to_scenarios: dict[str, set[str]] = {story_id: set() for story_id in story_ids}
        required_links: set[tuple[str, str, str]] = set()
        for acceptance_id, story_id in acceptance_owner.items():
            required_links.add((story_id, acceptance_id, "has_acceptance"))
        referenced_rules: set[str] = set()
        scenario_details: dict[str, dict[str, Any]] = {}
        valid_swimlanes: set[str] = set()
        for scenario in scenarios:
            scenario_id = str(scenario.get("id"))
            for story_id in scenario.get("source_story_ids", []):
                story_id = str(story_id)
                if story_id not in story_ids:
                    self.error("unknown_story_ref", f"{scenario_id} 引用了不存在的用户故事 {story_id}", ids=[scenario_id, story_id])
                else:
                    story_to_scenarios.setdefault(story_id, set()).add(scenario_id)
                    required_links.add((story_id, scenario_id, "decomposes_to"))
            relative = scenario.get("file")
            if not relative:
                self.error("missing_detail_ref", f"{scenario_id} 缺少详情文件路径", "scenarios/scenario-index.json", [scenario_id])
                continue
            path = self.safe_path(str(relative), scenario_id)
            if not path:
                continue
            detail = self.load(str(path.relative_to(self.root)), {})
            scenario_details[scenario_id] = detail
            if detail.get("id") != scenario_id:
                self.error("detail_id_mismatch", f"{scenario_id} 的详情 ID 不一致", str(relative), [scenario_id])
            if scenario_id in in_scope_scenarios and not detail.get("main_flow"):
                self.error("empty_main_flow", f"{scenario_id} 主流程为空", str(relative), [scenario_id])
            if scenario_id in in_scope_scenarios:
                swimlane_problems = inspect_swimlane(detail)
                if swimlane_problems:
                    for problem in swimlane_problems:
                        self.error(problem["code"], problem["message"], str(relative), problem.get("ids"))
                else:
                    layout_problems = audit_swimlane_layout(build_swimlane_layout(detail["flow_diagram"])) if business_requested else []
                    if layout_problems:
                        for message in layout_problems:
                            self.error(
                                "invalid_swimlane_layout",
                                f"{scenario_id} 的泳道布局无效：{message}",
                                str(relative),
                                [scenario_id],
                            )
                    else:
                        valid_swimlanes.add(scenario_id)
            detail_sources = {str(value) for value in detail.get("source_story_ids", [])}
            index_sources = {str(value) for value in scenario.get("source_story_ids", [])}
            if detail_sources != index_sources:
                self.error("scenario_source_mismatch", f"{scenario_id} 索引与详情的 source_story_ids 不一致", str(relative), [scenario_id])
            for step in detail.get("main_flow", []):
                for rule_id in step.get("rule_ids", []):
                    rule_id = str(rule_id)
                    referenced_rules.add(rule_id)
                    required_links.add((scenario_id, rule_id, "governed_by"))

        for story_id in sorted(in_scope_stories):
            if not story_to_scenarios.get(story_id):
                self.error("uncovered_story", f"{story_id} 没有对应业务场景", ids=[story_id])
        for rule_id in sorted(referenced_rules - rule_ids):
            self.error("unknown_rule_ref", f"场景引用了不存在的业务规则 {rule_id}", ids=[rule_id])

        story_to_features: dict[str, set[str]] = {story_id: set() for story_id in story_ids}
        acceptance_to_features: dict[str, set[str]] = {
            acceptance_id: set() for acceptance_id in acceptance_ids
        }
        scenario_to_features: dict[str, set[str]] = {
            scenario_id: set() for scenario_id in scenario_ids
        }
        feature_by_id = {str(item.get("id")): item for item in features}
        for feature in features:
            feature_id = str(feature.get("id", ""))
            for field in ("version", "module_l1", "module_l2", "name", "description"):
                value = feature.get(field)
                if not isinstance(value, str) or not value.strip():
                    self.error(
                        "missing_feature_field",
                        f"{feature_id or '未知功能'} 缺少必填字段 {field}",
                        "memory/feature-list.json",
                        [feature_id] if feature_id else None,
                    )
            module_l3 = feature.get("module_l3")
            if module_l3 is not None and (not isinstance(module_l3, str) or not module_l3.strip()):
                self.error(
                    "invalid_feature_module_l3",
                    f"{feature_id or '未知功能'} 的 module_l3 应为非空文本或 null",
                    "memory/feature-list.json",
                    [feature_id] if feature_id else None,
                )
            notes = feature.get("notes")
            if notes is not None and not isinstance(notes, str):
                self.error(
                    "invalid_feature_notes",
                    f"{feature_id or '未知功能'} 的 notes 应为文本或 null",
                    "memory/feature-list.json",
                    [feature_id] if feature_id else None,
                )
            if feature.get("change_type") not in FEATURE_CHANGE_TYPES:
                self.error(
                    "invalid_feature_change_type",
                    f"{feature_id or '未知功能'} 的 change_type 只允许 new 或 iteration",
                    "memory/feature-list.json",
                    [feature_id] if feature_id else None,
                )

            reference_fields = {
                "source_story_ids": feature.get("source_story_ids", []),
                "source_acceptance_ids": feature.get("source_acceptance_ids", []),
                "source_scenario_ids": feature.get("source_scenario_ids", []),
            }
            for field, values in reference_fields.items():
                if not isinstance(values, list) or not values:
                    self.error(
                        "missing_feature_trace",
                        f"{feature_id or '未知功能'} 缺少 {field}",
                        "memory/feature-list.json",
                        [feature_id] if feature_id else None,
                    )
                    reference_fields[field] = []

            source_story_ids = {str(value) for value in reference_fields["source_story_ids"]}
            source_acceptance_ids = {str(value) for value in reference_fields["source_acceptance_ids"]}
            source_scenario_ids = {str(value) for value in reference_fields["source_scenario_ids"]}
            for story_id in source_story_ids:
                if story_id not in story_ids:
                    self.error("unknown_story_ref", f"{feature_id} 引用了不存在的用户故事 {story_id}", ids=[feature_id, story_id])
                else:
                    story_to_features.setdefault(story_id, set()).add(feature_id)
                    required_links.add((story_id, feature_id, "specifies"))
            for acceptance_id in source_acceptance_ids:
                if acceptance_id not in acceptance_ids:
                    self.error("unknown_acceptance_ref", f"{feature_id} 引用了不存在的验收条件 {acceptance_id}", ids=[feature_id, acceptance_id])
                else:
                    acceptance_to_features.setdefault(acceptance_id, set()).add(feature_id)
                    required_links.add((acceptance_id, feature_id, "verifies"))
                    owner = acceptance_owner.get(acceptance_id)
                    if owner and owner not in source_story_ids:
                        self.error(
                            "feature_acceptance_story_mismatch",
                            f"{feature_id} 引用 {acceptance_id}，但未同时引用其所属用户故事 {owner}",
                            ids=[feature_id, acceptance_id, owner],
                        )
            for scenario_id in source_scenario_ids:
                if scenario_id not in scenario_ids:
                    self.error("unknown_scenario_ref", f"{feature_id} 引用了不存在的业务场景 {scenario_id}", ids=[feature_id, scenario_id])
                else:
                    scenario_to_features.setdefault(scenario_id, set()).add(feature_id)
                    required_links.add((scenario_id, feature_id, "realized_by"))

        ownership = attribution_report(self.root)
        retained_coverage = ownership["retained_coverage"]
        # Coverage here means a validated reference, not complete acceptance.
        # Ownership errors and unresolved scopes remain in page_errors and the
        # scenario gate; production/critical risk gates below remain unchanged.
        for story_id in sorted(in_scope_stories):
            if not story_to_features.get(story_id) and story_id not in retained_coverage["source_story_ids"]:
                self.error("story_without_feature", f"{story_id} 没有本期功能或有效存量归属", ids=[story_id])
        for acceptance_id in sorted(in_scope_acceptance_ids):
            if not acceptance_to_features.get(acceptance_id) and acceptance_id not in retained_coverage["source_acceptance_ids"]:
                self.error("acceptance_without_feature", f"{acceptance_id} 没有本期功能或有效存量归属", ids=[acceptance_id])
        feature_scenarios = {sid for sid, refs in scenario_to_features.items() if refs}
        for message in scenario_coverage_errors(self.root, feature_scenarios, in_scope_scenarios):
            self.error("scenario_operation_coverage", message)

        sequence_details: dict[str, dict[str, Any]] = {}
        scenarios_with_sequences: set[str] = set()
        actual_api_usage: dict[str, set[str]] = {api_id: set() for api_id in api_ids}
        for sequence in sequences:
            sequence_id = str(sequence.get("id"))
            scenario_id = str(sequence.get("scenario_id", ""))
            if scenario_id not in scenario_ids:
                self.error("unknown_scenario_ref", f"{sequence_id} 引用了不存在的场景 {scenario_id}", ids=[sequence_id, scenario_id])
            else:
                scenarios_with_sequences.add(scenario_id)
                required_links.add((scenario_id, sequence_id, "sequenced_by"))
            relative = sequence.get("file")
            if not relative:
                self.error("missing_detail_ref", f"{sequence_id} 缺少详情文件路径", "sequences/sequence-index.json", [sequence_id])
                continue
            path = self.safe_path(str(relative), sequence_id)
            if not path:
                continue
            detail = self.load(str(path.relative_to(self.root)), {})
            sequence_details[sequence_id] = detail
            if detail.get("id") != sequence_id or str(detail.get("scenario_id", "")) != scenario_id:
                self.error("detail_id_mismatch", f"{sequence_id} 的详情 ID 或 scenario_id 不一致", str(relative), [sequence_id])
            participants = detail.get("participants", [])
            participant_ids = {str(item.get("id")) for item in participants if item.get("id")}
            if sequence_id in sequence_ids and not participants:
                self.error("empty_participants", f"{sequence_id} 没有参与方", str(relative), [sequence_id])
            messages = detail.get("messages", [])
            if not messages:
                self.error("empty_messages", f"{sequence_id} 没有时序消息", str(relative), [sequence_id])
            for message in messages:
                source, target = str(message.get("from", "")), str(message.get("to", ""))
                for participant_id in (source, target):
                    if participant_id not in participant_ids:
                        self.error("unknown_participant", f"{sequence_id} 消息引用未知参与方 {participant_id}", str(relative), [sequence_id])
                api_id = message.get("api_id")
                if message.get("type") == "external_call" and not api_id:
                    self.error("external_call_without_api", f"{sequence_id} 的外部调用未关联 API", str(relative), [sequence_id])
                if api_id:
                    api_id = str(api_id)
                    if api_id not in api_ids:
                        self.error("unknown_api_ref", f"{sequence_id} 引用了不存在的接口 {api_id}", str(relative), [sequence_id, api_id])
                    else:
                        actual_api_usage.setdefault(api_id, set()).add(sequence_id)
                        required_links.add((sequence_id, api_id, "calls"))

        for scenario_id in sorted(in_scope_scenarios - scenarios_with_sequences):
            self.error("scenario_without_sequence", f"{scenario_id} 没有交互时序", ids=[scenario_id])

        accepted_risks: dict[str, list[str]] = {}
        blockers: list[str] = []
        for item in open_items:
            issue_id = str(item.get("id"))
            severity = item.get("severity")
            status = item.get("status", "open")
            if severity not in ISSUE_SEVERITIES:
                self.error("invalid_issue_severity", f"{issue_id} severity 不合法：{severity}", "runtime/open-items.json", [issue_id])
            if status not in ISSUE_STATUSES:
                self.error("invalid_issue_status", f"{issue_id} status 不合法：{status}", "runtime/open-items.json", [issue_id])
            if ("resolution_scope" in item and (not isinstance(item["resolution_scope"], str)
                                               or item["resolution_scope"] not in ISSUE_RESOLUTION_SCOPES)):
                self.error("invalid_issue_resolution_scope", f"{issue_id} resolution_scope 不合法：{item['resolution_scope']}", "runtime/open-items.json", [issue_id])
            for related_id in item.get("related_ids", []):
                related_id = str(related_id)
                if related_id not in known_ids:
                    self.error("unknown_issue_ref", f"{issue_id} 关联未知对象 {related_id}", "runtime/open-items.json", [issue_id, related_id])
                required_links.add((related_id, issue_id, "has_issue"))
                if issue_disposes_interface(item):
                    accepted_risks.setdefault(related_id, []).append(issue_id)
            if status == "open" and severity in {"critical", "high"}:
                blockers.append(issue_id)
                self.error("blocking_open_item", f"{issue_id} 是未关闭的 {severity} 问题", "runtime/open-items.json", [issue_id])
            elif status == "open":
                self.warning("open_item", f"{issue_id} 尚未关闭", "runtime/open-items.json", [issue_id])

        resolved_interface_count = 0
        for api in interfaces:
            api_id = str(api.get("id"))
            for scenario_id in api.get("source_scenario_ids", []):
                required_links.add((str(scenario_id), api_id, "requires_interface"))
            status = api.get("verification_status")
            if status not in INTERFACE_STATUSES:
                self.error("invalid_interface_status", f"{api_id} verification_status 不合法：{status}", "interfaces/interface-index.json", [api_id])
            if status == "verified":
                if not api.get("source_refs"):
                    self.error("verified_without_source", f"{api_id} 标为 verified 但没有 source_refs", "interfaces/interface-index.json", [api_id])
                else:
                    for source_ref in api.get("source_refs", []):
                        self.check_source_ref(source_ref, api_id, "interfaces/interface-index.json")
                    resolved_interface_count += 1
            elif status == "not_required":
                if actual_api_usage.get(api_id):
                    self.error("used_api_not_required", f"{api_id} 被时序调用，却标记为 not_required", ids=[api_id])
                else:
                    resolved_interface_count += 1
            elif status in {"partial", "missing", "conflict"}:
                if accepted_risks.get(api_id):
                    resolved_interface_count += 1
                    self.warning("accepted_interface_risk", f"{api_id} 为 {status}，已由问题项记录处置", ids=[api_id] + accepted_risks[api_id])
                else:
                    self.error("unresolved_interface", f"{api_id} 为 {status}，且没有已处置的问题项", ids=[api_id])
            declared = {str(value) for value in api.get("used_by_sequence_ids", [])}
            actual = actual_api_usage.get(api_id, set())
            if declared != actual:
                self.error("api_usage_mismatch", f"{api_id} 的 used_by_sequence_ids 与实际时序引用不一致", ids=[api_id])

        scenarios_with_pages: set[str] = set()
        features_with_pages: set[str] = set()
        for page in pages:
            page_id = str(page.get("id"))
            for sequence_id in page.get("sequence_ids", []):
                required_links.add((str(sequence_id), page_id, "informs_page"))
            for scenario_id in page.get("scenario_ids", []):
                scenario_id = str(scenario_id)
                if scenario_id not in scenario_ids:
                    self.error("unknown_scenario_ref", f"{page_id} 引用了不存在的场景 {scenario_id}", ids=[page_id, scenario_id])
                else:
                    if page.get("lifecycle_status") != "retired_source":
                        scenarios_with_pages.add(scenario_id)
                    required_links.add((scenario_id, page_id, "surfaced_by"))
            function_refs = page.get("function_ids", [])
            if not isinstance(function_refs, list):
                self.error("invalid_function_refs", f"{page_id} function_ids必须为数组", ids=[page_id])
                function_refs = []
            # Main operation ownership (including nonempty retained/history
            # evidence) is enforced by the shared contract, never by FUNC count.
            for feature_id in function_refs:
                feature_id = str(feature_id)
                if feature_id not in feature_ids:
                    self.error("unknown_feature_ref", f"{page_id} 引用了不存在的功能 {feature_id}", ids=[page_id, feature_id])
                else:
                    if page.get("lifecycle_status") != "retired_source":
                        features_with_pages.add(feature_id)
                    required_links.add((feature_id, page_id, "surfaced_by"))
            for action in page.get("actions", []):
                action_id = str(action.get("id"))
                required_links.add((page_id, action_id, "contains"))
                sources = action.get("source_scenario_ids", [])
                if not sources:
                    self.error("action_without_source", f"{action_id} 没有 source_scenario_ids", ids=[page_id, action_id])
                for scenario_id in sources:
                    scenario_id = str(scenario_id)
                    if scenario_id not in scenario_ids:
                        self.error("unknown_scenario_ref", f"{action_id} 引用了不存在的场景 {scenario_id}", ids=[action_id, scenario_id])
                    else:
                        required_links.add((scenario_id, action_id, "performed_by"))
                function_sources = action.get("source_function_ids", [])
                if not isinstance(function_sources, list):
                    self.error("invalid_function_refs", f"{action_id} source_function_ids必须为数组", ids=[page_id, action_id])
                    function_sources = []
                for feature_id in function_sources:
                    feature_id = str(feature_id)
                    if feature_id not in feature_ids:
                        self.error("unknown_feature_ref", f"{action_id} 引用了不存在的功能 {feature_id}", ids=[action_id, feature_id])
                    else:
                        required_links.add((feature_id, action_id, "operated_by"))

        required_ui_scenarios = {
            scenario_id
            for scenario_id in in_scope_scenarios
            if scenario_by_id.get(scenario_id, {}).get("ui_surface", "required") == "required"
        }
        for scenario_id in sorted(required_ui_scenarios - scenarios_with_pages):
            self.error("scenario_without_page", f"{scenario_id} 需要 UI，但没有页面承载", ids=[scenario_id])
        required_ui_features = {
            feature_id
            for feature_id, feature in feature_by_id.items()
            if required_ui_scenarios.intersection(
                str(value) for value in feature.get("source_scenario_ids", [])
            )
        }
        for feature_id in sorted(required_ui_features - features_with_pages):
            self.error("feature_without_page", f"{feature_id} 关联需要 UI 的场景，但没有页面承载", ids=[feature_id])

        for flow in flows:
            flow_id = str(flow.get("id"))
            if not isinstance(flow.get("name"), str) or not str(flow.get("name")).strip():
                self.error("missing_business_flow_name", f"{flow_id} 缺少业务流程名称", "memory/business-rules.json", [flow_id])
            flow_steps = flow.get("steps", [])
            if not isinstance(flow_steps, list) or not flow_steps or any(not str(step).strip() for step in flow_steps):
                self.error("missing_business_flow_steps", f"{flow_id} 缺少有效业务步骤", "memory/business-rules.json", [flow_id])
            for scenario_id in flow.get("scenario_ids", []):
                scenario_id = str(scenario_id)
                if scenario_id not in scenario_ids:
                    self.error("unknown_scenario_ref", f"{flow_id} 引用了不存在的场景 {scenario_id}", ids=[flow_id, scenario_id])
                else:
                    required_links.add((scenario_id, flow_id, "part_of_flow"))

        actual_links: set[tuple[str, str, str]] = set()
        for link in trace_data.get("links", []):
            source, target, relation = str(link.get("from", "")), str(link.get("to", "")), str(link.get("type", ""))
            if source not in known_ids:
                self.error("unknown_trace_endpoint", f"追溯关系来源不存在：{source}", "runtime/traceability.json", [source] if source else None)
            if target not in known_ids:
                self.error("unknown_trace_endpoint", f"追溯关系目标不存在：{target}", "runtime/traceability.json", [target] if target else None)
            actual_links.add((source, target, relation))
        for source, target, relation in sorted(required_links - actual_links):
            self.error("missing_trace_link", f"缺少追溯关系：{source} --{relation}--> {target}", "runtime/traceability.json", [source, target])

        for relative in CORE_OUTPUTS:
            if not included(self.root, relative):
                continue
            path = self.root / relative
            if not path.is_file() or path.stat().st_size == 0:
                self.error("missing_output", f"核心产物缺失或为空：{relative}", relative)
            elif DRAFT_MARKER in path.read_text(encoding="utf-8", errors="replace").lower():
                self.error("draft_output", f"核心产物仍含草稿标记：{relative}", relative)

        plantuml_diagram_count = 0
        plantuml_business_flow_count = 0
        rendered_business_flow_ids: set[str] = set()
        scenario_atlas = self.root / "outputs" / "business-scenario-atlas.html"
        for error in (desktop_atlas_errors(self.root, "outputs/business-scenario-atlas.html") if business_requested else []):
            self.error("invalid_atlas_reading_layout", error, "outputs/business-scenario-atlas.html")
        if business_requested and scenario_atlas.is_file():
            atlas_text = scenario_atlas.read_text(encoding="utf-8", errors="replace")
            svg_openings = re.findall(r"<svg\b[^>]*>", atlas_text, flags=re.IGNORECASE)
            plantuml_diagram_count = sum(
                'data-diagram-type="swimlane"' in opening and 'data-plantuml-render="pass"' in opening
                for opening in svg_openings
            )
            plantuml_business_flow_count = sum(
                'data-diagram-type="business-process"' in opening and 'data-plantuml-render="pass"' in opening
                for opening in svg_openings
            )
            rendered_business_flow_ids = set(
                re.findall(r'data-flow-id=["\']([^"\']+)["\']', atlas_text)
            )
            for flow_id in sorted(flow_ids - rendered_business_flow_ids):
                self.error(
                    "missing_business_flow_diagram",
                    f"{flow_id} 没有在 HTML 图集中生成业务流程图",
                    "outputs/business-scenario-atlas.html",
                    [flow_id],
                )
            if plantuml_diagram_count != len(in_scope_scenarios):
                self.error(
                    "partial_plantuml_atlas",
                    f"PlantUML 图集仅生成 {plantuml_diagram_count}/{len(in_scope_scenarios)} 个在范围场景",
                    "outputs/business-scenario-atlas.html",
                )
            if plantuml_business_flow_count != len(flow_ids):
                self.error(
                    "partial_plantuml_business_flow_atlas",
                    f"PlantUML 业务流程图仅生成 {plantuml_business_flow_count}/{len(flow_ids)} 个",
                    "outputs/business-scenario-atlas.html",
                )
            forbidden_remote_renderers = [
                value
                for value in ("kroki.io", "plantuml.com/plantuml", "mermaid.ink")
                if value in atlas_text.lower()
            ]
            if forbidden_remote_renderers:
                self.error(
                    "remote_diagram_dependency",
                    "业务场景图集包含公网渲染依赖：" + ", ".join(forbidden_remote_renderers),
                    "outputs/business-scenario-atlas.html",
                )

        native_sequence_count = 0
        rendered_sequence_ids: set[str] = set()
        sequence_atlas = self.root / "outputs" / "interaction-sequence-atlas.html"
        if sequence_requested and sequence_atlas.is_file():
            sequence_atlas_text = sequence_atlas.read_text(encoding="utf-8", errors="replace")
            sequence_svg_openings = re.findall(r"<svg\b[^>]*>", sequence_atlas_text, flags=re.IGNORECASE)
            native_sequence_count = sum(
                'data-diagram-type="sequence"' in opening and 'data-renderer="native"' in opening
                and 'data-plantuml-render=' not in opening
                for opening in sequence_svg_openings
            )
            rendered_sequence_ids = set(
                sequence_id for opening in sequence_svg_openings
                if 'data-diagram-type="sequence"' in opening and 'data-renderer="native"' in opening
                and 'data-plantuml-render=' not in opening
                for sequence_id in re.findall(r'data-sequence-id=["\']([^"\']+)["\']', opening)
            )
            for sequence_id in sorted(sequence_ids - rendered_sequence_ids):
                self.error(
                    "missing_sequence_diagram",
                    f"{sequence_id} 没有在 HTML 图集中生成交互时序图",
                    "outputs/interaction-sequence-atlas.html",
                    [sequence_id],
                )
            if native_sequence_count != len(sequence_ids):
                self.error(
                    "partial_native_sequence_atlas",
                    f"原生 SVG 交互时序图仅生成 {native_sequence_count}/{len(sequence_ids)} 个",
                    "outputs/interaction-sequence-atlas.html",
                )
            forbidden_remote_renderers = [
                value
                for value in ("kroki.io", "plantuml.com/plantuml", "mermaid.ink")
                if value in sequence_atlas_text.lower()
            ]
            if forbidden_remote_renderers:
                self.error(
                    "remote_diagram_dependency",
                    "交互时序图集包含公网渲染依赖：" + ", ".join(forbidden_remote_renderers),
                    "outputs/interaction-sequence-atlas.html",
                )

        story_covered = sum(1 for story_id in in_scope_stories if story_to_scenarios.get(story_id))
        scenario_sequenced = len(in_scope_scenarios & scenarios_with_sequences)
        scenario_surfaced = len(required_ui_scenarios & scenarios_with_pages)
        self.summary = {
            "input_sources": {"total": len(input_sources), "llm_wiki": sum(item.get("type") == "llm_wiki" for item in input_sources)},
            "user_stories": {"in_scope": len(in_scope_stories), "covered": story_covered},
            "business_scenarios": {"in_scope": len(in_scope_scenarios), "with_sequence": scenario_sequenced},
            "sequence_diagrams": {
                "required": len(sequence_ids) if sequence_requested else 0,
                "valid": len(sequence_ids & rendered_sequence_ids),
            },
            "business_process_diagrams": {
                "required": len(flow_ids) if business_requested else 0,
                "valid": len(flow_ids & rendered_business_flow_ids),
            },
            "swimlane_diagrams": {"required": len(in_scope_scenarios) if business_requested else 0,
                                  "valid": len(valid_swimlanes) if business_requested else 0,
                                  "status": 'checked' if business_requested else 'not_requested'},
            "plantuml_diagrams": {
                "present": plantuml_diagram_count,
                "complete": plantuml_diagram_count == len(in_scope_scenarios) if business_requested else None,
            },
            "plantuml_business_process_diagrams": {
                "present": plantuml_business_flow_count,
                "complete": plantuml_business_flow_count == len(flow_ids) if business_requested else None,
            },
            "native_sequence_diagrams": {
                "present": native_sequence_count,
                "complete": native_sequence_count == len(sequence_ids) if sequence_requested else None,
            },
            "interfaces": {"referenced": len({api for api, uses in actual_api_usage.items() if uses}), "resolved": resolved_interface_count},
            "features": {
                "total": len(feature_ids),
                "new": sum(1 for item in features if item.get("change_type") == "new"),
                "iteration": sum(1 for item in features if item.get("change_type") == "iteration"),
                "with_page": len(features_with_pages),
            },
            "ui_scenarios": {"required": len(required_ui_scenarios), "with_page": scenario_surfaced},
            "pages": len(page_ids),
            "retained_reference_attribution": {
                "valid_direct_original_operations": len(ownership["validated_retained_keys_with_direct_ownership"]),
                "reference_coverage": retained_coverage,
                "current_pages": len(ownership["current_page_ids"]),
                "historical_source_pages": len(ownership["historical_source_page_ids"]),
                "counts_as_new_FUNC": False,
                "does_not_prove_full_acceptance_or_production": True,
            },
            "actions": len(action_ids),
            "trace_links": len(actual_links),
            "blocking_open_items": blockers,
        }
        final_snapshot = approval_snapshot(self.root)
        if initial_snapshot != final_snapshot:
            self.error('changed_during_verification', '核验期间输入或图集发生变化，请重新核验')
        status = "pass" if not self.errors else "fail"
        return {
            "schema_version": "1.0",
            "generated_at": now(),
            "status": status,
            "summary": self.summary,
            "errors": self.errors,
            "warnings": self.warnings,
            "approval_snapshot": final_snapshot,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="核验需求分析项目的结构、覆盖率和追溯关系")
    parser.add_argument("project_dir")
    args = parser.parse_args()
    root = Path(args.project_dir).expanduser().resolve()
    verifier = Verifier(root)
    report = verifier.verify()
    output = root / "runtime" / "verification-report.json"
    atomic_json(output, report)
    print(f"核验结果：{report['status']}  错误：{len(report['errors'])}  警告：{len(report['warnings'])}")
    summary = report["summary"]
    if summary:
        print(
            "覆盖率："
            f"用户故事 {summary['user_stories']['covered']}/{summary['user_stories']['in_scope']}，"
            f"场景泳道 {summary['swimlane_diagrams']['valid']}/{summary['swimlane_diagrams']['required']}，"
            f"时序事实 {summary['business_scenarios']['with_sequence']}/{summary['business_scenarios']['in_scope']}，"
            f"时序图 {summary['sequence_diagrams']['valid']}/{summary['sequence_diagrams']['required']}，"
            f"功能 {summary['features']['total']}（新增 {summary['features']['new']} / 迭代 {summary['features']['iteration']}），"
            f"UI 场景 {summary['ui_scenarios']['with_page']}/{summary['ui_scenarios']['required']}"
        )
    print(f"报告：{output}")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
