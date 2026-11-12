#!/usr/bin/env python3
"""Derive traceability links from canonical requirement-analysis JSON files."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


class TraceError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TraceError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise TraceError(f"JSON 无法解析：{path}（{exc}）") from exc


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise TraceError(f"项目内文件路径越界：{relative}") from exc
    return path


def write_json(path: Path, data: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def add_link(links: set[tuple[str, str, str]], source: Any, target: Any, relation: str) -> None:
    if source and target:
        links.add((str(source), str(target), relation))


def derive(root: Path) -> list[dict[str, str]]:
    links: set[tuple[str, str, str]] = set()

    stories = read_json(root / "memory" / "normalized-user-stories.json")
    for story in stories.get("user_stories", []):
        story_id = story.get("id")
        for criterion in story.get("acceptance_criteria", []):
            if isinstance(criterion, dict):
                add_link(links, story_id, criterion.get("id"), "has_acceptance")

    scenario_index = read_json(root / "scenarios" / "scenario-index.json")
    for scenario in scenario_index.get("scenarios", []):
        scenario_id = scenario.get("id")
        for story_id in scenario.get("source_story_ids", []):
            add_link(links, story_id, scenario_id, "decomposes_to")
        detail_file = scenario.get("file")
        if detail_file and safe_path(root, detail_file).is_file():
            detail = read_json(safe_path(root, detail_file))
            for step in detail.get("main_flow", []):
                for rule_id in step.get("rule_ids", []):
                    add_link(links, scenario_id, rule_id, "governed_by")
            for flow in detail.get("alternate_flows", []):
                for rule_id in flow.get("rule_ids", []):
                    add_link(links, scenario_id, rule_id, "governed_by")

    sequence_index = read_json(root / "sequences" / "sequence-index.json")
    for sequence in sequence_index.get("sequences", []):
        sequence_id = sequence.get("id")
        add_link(links, sequence.get("scenario_id"), sequence_id, "sequenced_by")
        detail_file = sequence.get("file")
        if detail_file and safe_path(root, detail_file).is_file():
            detail = read_json(safe_path(root, detail_file))
            for message in detail.get("messages", []):
                add_link(links, sequence_id, message.get("api_id"), "calls")

    interfaces = read_json(root / "interfaces" / "interface-index.json")
    for api in interfaces.get("interfaces", []):
        for scenario_id in api.get("source_scenario_ids", []):
            add_link(links, scenario_id, api.get("id"), "requires_interface")
        for sequence_id in api.get("used_by_sequence_ids", []):
            add_link(links, api.get("id"), sequence_id, "constrains_sequence")

    rules = read_json(root / "memory" / "business-rules.json")
    for flow in rules.get("business_flows", []):
        flow_id = flow.get("id")
        for scenario_id in flow.get("scenario_ids", []):
            add_link(links, scenario_id, flow_id, "part_of_flow")

    feature_list = read_json(root / "memory" / "feature-list.json")
    for feature in feature_list.get("features", []):
        feature_id = feature.get("id")
        for story_id in feature.get("source_story_ids", []):
            add_link(links, story_id, feature_id, "specifies")
        for acceptance_id in feature.get("source_acceptance_ids", []):
            add_link(links, acceptance_id, feature_id, "verifies")
        for scenario_id in feature.get("source_scenario_ids", []):
            add_link(links, scenario_id, feature_id, "realized_by")

    pages = read_json(root / "planning" / "page-index.json")
    for page in pages.get("pages", []):
        page_id = page.get("id")
        for sequence_id in page.get("sequence_ids", []):
            add_link(links, sequence_id, page_id, "informs_page")
        for scenario_id in page.get("scenario_ids", []):
            add_link(links, scenario_id, page_id, "surfaced_by")
        for feature_id in page.get("function_ids", []):
            add_link(links, feature_id, page_id, "surfaced_by")
        for action in page.get("actions", []):
            action_id = action.get("id")
            add_link(links, page_id, action_id, "contains")
            for scenario_id in action.get("source_scenario_ids", []):
                add_link(links, scenario_id, action_id, "performed_by")
            for feature_id in action.get("source_function_ids", []):
                add_link(links, feature_id, action_id, "operated_by")

    open_items = read_json(root / "runtime" / "open-items.json")
    for item in open_items.get("items", []):
        issue_id = item.get("id")
        for related_id in item.get("related_ids", []):
            add_link(links, related_id, issue_id, "has_issue")

    return [
        {"from": source, "to": target, "type": relation}
        for source, target, relation in sorted(links)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="从事实源重建追溯关系")
    parser.add_argument("project_dir")
    args = parser.parse_args()
    root = Path(args.project_dir).expanduser().resolve()
    try:
        links = derive(root)
        output = {
            "schema_version": "1.0",
            "generated_at": now(),
            "generated_from": [
                "memory/normalized-user-stories.json",
                "scenarios/scenario-index.json",
                "sequences/sequence-index.json",
                "interfaces/interface-index.json",
                "memory/business-rules.json",
                "memory/feature-list.json",
                "planning/page-index.json",
                "runtime/open-items.json",
            ],
            "links": links,
        }
        write_json(root / "runtime" / "traceability.json", output)
    except TraceError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"已重建追溯关系：{len(links)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
