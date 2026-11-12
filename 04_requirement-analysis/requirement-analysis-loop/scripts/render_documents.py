#!/usr/bin/env python3
"""Render reviewable Markdown views from canonical requirement-analysis data."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import sys
from typing import Any

from feature_contract import BOUNDARY_TEXT_FIELDS, BOUNDARY_LIST_FIELDS, feature_boundary_errors, feature_view_marker, load_acceptance
from operation_attribution import attribution_report


class RenderError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RenderError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(f"JSON 无法解析：{path}（{exc}）") from exc


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text.rstrip() + "\n", encoding="utf-8")
    temp.replace(path)


def cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        text = "、".join(str(item) for item in value) or "—"
    else:
        text = str(value) or "—"
    return text.replace("|", "\\|").replace("\n", "<br>")


def bullets(values: list[Any], empty: str = "无") -> str:
    return "\n".join(f"- {value}" for value in values) if values else f"- {empty}"


def heading(title: str, project: str) -> list[str]:
    return [f"# {title}", "", f"> 项目：{project}  ", f"> 生成时间：{now()}  ", "> 本文档由结构化事实源自动汇编。", ""]


def render_scenario_list(root: Path, project: str) -> Path:
    records = read_json(root / "scenarios" / "scenario-index.json").get("scenarios", [])
    if not records:
        raise RenderError("scenario-index.json 尚无业务场景")
    lines = heading("业务场景清单", project)
    lines += ["| ID | 业务场景 | 优先级 | 范围 | 状态 | 用户故事 | UI 承载 |", "|---|---|---|---|---|---|---|"]
    for item in records:
        lines.append(
            f"| {cell(item.get('id'))} | {cell(item.get('name'))} | {cell(item.get('priority'))} | "
            f"{cell(item.get('scope'))} | {cell(item.get('status'))} | {cell(item.get('source_story_ids', []))} | {cell(item.get('ui_surface'))} |"
        )
    in_scope = sum(1 for item in records if item.get("scope", "in_scope") == "in_scope")
    lines += ["", "## 汇总", "", f"- 场景总数：{len(records)}", f"- 本期场景：{in_scope}", f"- 非本期场景：{len(records) - in_scope}"]
    output = root / "outputs" / "business-scenario-list.md"
    write_text(output, "\n".join(lines))
    return output


def render_interface_matrix(root: Path, project: str) -> Path:
    records = read_json(root / "interfaces" / "interface-index.json").get("interfaces", [])
    lines = heading("系统时序接口核验矩阵", project)
    lines += ["S4 先按业务场景核验接口；S5 再回填实际时序引用。关联时序为空不代表不需要接口。", "",
              "| ID | 接口/事件 | 提供方 | 来源场景 | 关联时序 | 核验状态 | 认证 | 超时 | 幂等 | 限流 | 来源 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for item in records:
        lines.append(
            f"| {cell(item.get('id'))} | {cell(item.get('name'))} | {cell(item.get('provider'))} | "
            f"{cell(item.get('source_scenario_ids', []))} | {cell(item.get('used_by_sequence_ids', []))} | {cell(item.get('verification_status'))} | "
            f"{cell(item.get('auth'))} | {cell(item.get('timeout'))} | {cell(item.get('idempotency'))} | "
            f"{cell(item.get('rate_limit'))} | {cell(item.get('source_refs', []))} |"
        )
    if not records:
        lines.append("| — | 本期没有登记外部接口 | — | — | — | not_required | — | — | — | — | — |")
    gaps = [item for item in records if item.get("verification_status") in {"partial", "missing", "conflict"}]
    lines += ["", "## 待处理缺口", ""]
    if gaps:
        for item in gaps:
            lines.append(f"- **{item.get('id')} · {item.get('name')}**：{item.get('verification_status')}；{cell(item.get('notes', []))}")
    else:
        lines.append("- 无未处置的接口缺口。")
    output = root / "outputs" / "system-sequence-interface-verification-matrix.md"
    write_text(output, "\n".join(lines))
    return output


def named(value: Any) -> str:
    if isinstance(value, dict):
        name = value.get("name") or value.get("id") or "未命名"
        description = value.get("description")
        return f"**{name}**：{description}" if description else str(name)
    return str(value)


CHANGE_TYPE_LABELS = {"new": "新增", "iteration": "迭代"}


def change_type_label(value: Any) -> str:
    return CHANGE_TYPE_LABELS.get(str(value), str(value or "—"))


def feature_table_lines(features: list[dict[str, Any]], include_trace: bool = False) -> list[str]:
    headers = ["功能 ID", "版本", "一级模块", "二级模块", "三级模块", "功能名称", "功能说明", "备注", "修改类型"]
    if include_trace:
        headers += ["用户故事", "验收条件", "业务场景"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for feature in features:
        values = [
            feature.get("id"),
            feature.get("version"),
            feature.get("module_l1"),
            feature.get("module_l2"),
            feature.get("module_l3"),
            feature.get("name"),
            feature.get("description"),
            feature.get("notes"),
            change_type_label(feature.get("change_type")),
        ]
        if include_trace:
            values += [
                feature.get("source_story_ids", []),
                feature.get("source_acceptance_ids", []),
                feature.get("source_scenario_ids", []),
            ]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    return lines


def render_feature_list(root: Path, project: str) -> Path:
    data = read_json(root / "memory" / "feature-list.json")
    errors = feature_boundary_errors(data)
    if errors:
        raise RenderError("；".join(errors))
    features = data["features"]
    scenarios = read_json(root / "scenarios" / "scenario-index.json").get("scenarios", [])
    in_scope = {item.get('id') for item in scenarios if item.get('scope', 'in_scope') == 'in_scope'}
    if in_scope and not features:
        try:
            report = attribution_report(root)
            covered = set(report['retained_coverage']['source_scenario_ids'])
            if (report['attribution_errors'] or not report['validated_retained_keys_with_direct_ownership']
                    or not in_scope <= covered):
                raise RenderError('存在本期业务场景，但缺少覆盖场景的有效存量操作归属')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise RenderError(f'无法核验存量操作归属：{exc}') from exc
    try:
        acceptance = load_acceptance(root)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RenderError(f"无法读取功能验收依据：{exc}") from exc
    lines = heading("功能清单", project)
    lines += [
        "> 模块层级表达产品能力结构，不等同于菜单路径；结构化事实源为 `memory/feature-list.json`。",
        "> FUNC 是单一可验收业务操作（业务对象＋主操作＋结果），不等同于场景或模块；条目数量不是标准功能点数，不代表相同工作量或单价。",
        feature_view_marker(data, acceptance),
        "",
    ]
    lines += feature_table_lines(features, include_trace=True)
    if not features:
        lines.append("| — | — | — | — | — | 本期没有新增或迭代功能；存量操作见结构化事实 | — | — | — | — | — | — |")
    new_count = sum(1 for item in features if item.get("change_type") == "new")
    iteration_count = sum(1 for item in features if item.get("change_type") == "iteration")
    lines += [
        "",
        "## 汇总",
        "",
        f"- 功能总数：{len(features)}",
        f"- 新增：{new_count}",
        f"- 迭代：{iteration_count}",
    ]
    lines += ["", "## 功能范围与验收明细", "", "> 供按需生成说明文档或报价时引用；本清单不自动生成报价，也不把可选说明文档变为原型交付门禁。"]
    for feature in features:
        lines += ["", f"### {cell(feature['id'])} · {cell(feature.get('name'))}", ""]
        for field, label in {**BOUNDARY_TEXT_FIELDS, **BOUNDARY_LIST_FIELDS}.items():
            value = feature["boundary"][field]
            lines.append(f"- {label}：{cell(value) if value != [] else '无（已确认）'}")
        lines.append("- 验收依据：")
        for aid in feature.get("source_acceptance_ids", []):
            lines.append(f"  - {cell(aid)}：{cell(acceptance.get(aid))}")
        change = feature["change_detail"]
        lines += [
            f"- 变更前：{cell(change['before']) if change['before'] is not None else '不适用（新增）'}",
            f"- 变更后：{cell(change['after'])}",
            f"- 本轮变更范围：{cell(change['impact'])}",
        ]
    output = root / "outputs" / "feature-list.md"
    write_text(output, "\n".join(lines))
    return output


def render_baseline(root: Path, project: str) -> Path:
    facts = read_json(root / "memory" / "project-facts.json")
    rules = read_json(root / "memory" / "business-rules.json")
    features = read_json(root / "memory" / "feature-list.json").get("features", [])
    scenarios = read_json(root / "scenarios" / "scenario-index.json").get("scenarios", [])
    project_data = facts.get("project", {})
    if not project_data:
        raise RenderError("project-facts.json 的 project 尚未填写")
    lines = heading("产品结构与业务流程确认基线", project)
    lines += [
        "## 阅读路线", "",
        "- **需求方与业务评审**：从本基线核对目标、范围和产品结构；按需查看 [功能清单与验收](feature-list.md)、[业务场景](business-scenario-list.md)、[业务流程与泳道](business-scenario-atlas.html) 及 [交互时序](interaction-sequence-atlas.html)。",
        "- **原型与开发交接**：对照 [页面规划](menu-and-page-planning-reference.md)、[页面结构](page-structure-atlas.html) 和 [接口核验](system-sequence-interface-verification-matrix.md)；使用同一版本的完整冻结包，交接后从产品原型和开发交付物各自的阅读入口继续。",
        "这些正文与图集由同一份确认事实派生，不维护另一套需求或变更台账。JSON、交接清单和核验记录保留供工具读取及按需核查；本阅读路线不替代版本确认或开工检查。", "",
    ]
    lines += ["## 1. 产品目标", "", str(project_data.get("objective") or "未填写"), "", "## 2. 确认范围", "", "### 本期范围", "", bullets(facts.get("scope", {}).get("in", [])), "", "### 非本期范围", "", bullets(facts.get("scope", {}).get("out", [])), ""]
    lines += ["## 3. 角色与系统边界", "", "### 角色", "", bullets([named(value) for value in facts.get("actors", [])]), "", "### 系统", "", bullets([named(value) for value in facts.get("systems", [])]), ""]
    lines += ["## 4. 产品能力结构", "", bullets([named(value) for value in facts.get("product_structure", [])]), ""]
    lines += ["## 5. 功能清单基线", ""]
    lines += feature_table_lines(features)
    if not features:
        lines.append("| — | — | — | — | — | 本期没有登记功能 | — | — | — |")
    lines += ["", "> 完整来源追溯、业务过程边界、验收和版本差异见 `outputs/feature-list.md`；条目数不是标准功能点数或价格。", "", "## 6. 业务流程", ""]
    flows = rules.get("business_flows", [])
    if flows:
        for flow in flows:
            flow_id = flow.get("id", "FLOW-?")
            lines += [
                f"### {flow_id} · {flow.get('name', '未命名流程')}",
                "",
                f"关联场景：{cell(flow.get('scenario_ids', []))}",
                "",
                f"可视化：[在 HTML 图集中查看 {flow_id}](business-scenario-atlas.html#{flow_id})",
                "",
            ]
            for index, step in enumerate(flow.get("steps", []), start=1):
                lines.append(f"{index}. {step}")
            if not flow.get("steps"):
                lines.append("1. 未填写流程步骤。")
            lines.append("")
    else:
        lines += ["- 尚未定义业务流程。", ""]
    lines += ["## 7. 业务规则", ""]
    business_rules = rules.get("business_rules", [])
    if business_rules:
        lines += ["| ID | 规则 | 说明 | 来源场景 |", "|---|---|---|---|"]
        for rule in business_rules:
            lines.append(f"| {cell(rule.get('id'))} | {cell(rule.get('name'))} | {cell(rule.get('statement'))} | {cell(rule.get('source_scenario_ids', []))} |")
    else:
        lines.append("- 尚未登记业务规则。")
    lines += ["", "## 8. 核心状态模型", ""]
    for model in facts.get("state_models", []):
        lines += [f"### {model.get('name', model.get('id', '未命名状态模型'))}", "", f"状态：{cell(model.get('states', []))}", "", f"转换：{cell(model.get('transitions', []))}", ""]
    if not facts.get("state_models"):
        lines += ["- 本期未登记状态模型。", ""]
    lines += ["## 9. 假设与约束", "", bullets(facts.get("assumptions", [])), "", "## 10. 场景基线", "", "| 场景 | 名称 | 范围 | 状态 |", "|---|---|---|---|"]
    for scenario in scenarios:
        lines.append(f"| {cell(scenario.get('id'))} | {cell(scenario.get('name'))} | {cell(scenario.get('scope'))} | {cell(scenario.get('status'))} |")
    output = root / "outputs" / "product-structure-and-business-process-baseline.md"
    from diagram_policy import requested
    text = '\n'.join(lines)
    if not requested(root, 'business_flow'):
        text = re.sub(r'\[([^\]]+)\]\(business-scenario-atlas\.html[^)]*\)', r'\1（未请求图集）', text)
    if not requested(root, 'interaction_sequence'):
        text = text.replace('[交互时序](interaction-sequence-atlas.html)', '交互时序事实（未请求图集）')
    write_text(output, text)
    return output


def render_page_plan(root: Path, project: str) -> Path:
    pages = read_json(root / "planning" / "page-index.json").get("pages", [])
    features = read_json(root / "memory" / "feature-list.json").get("features", [])
    feature_ids = {str(item.get("id")) for item in features}
    scenarios = read_json(root / "scenarios" / "scenario-index.json").get("scenarios", [])
    required_ui = [item for item in scenarios if item.get("scope", "in_scope") == "in_scope" and item.get("ui_surface", "required") == "required"]
    if required_ui and not pages:
        raise RenderError("存在需要 UI 的场景，但 page-index.json 尚无页面")
    lines = heading("菜单与页面规划参考", project)
    lines += ["[页面结构图（HTML）](page-structure-atlas.html)", "",
              "页面先从业务场景与时序形成；功能关联在 S7 最终确认时回填。", ""]
    lines += ["## 1. 菜单与页面总览", "", "| ID | 菜单路径 | 页面 | 角色 | 承载功能 | 承载场景 |", "|---|---|---|---|---|---|"]
    for page in pages:
        unknown = {str(value) for value in page.get("function_ids", [])} - feature_ids
        if unknown:
            raise RenderError(f"{page.get('id')} 引用了不存在的功能：{', '.join(sorted(unknown))}")
        lines.append(f"| {cell(page.get('id'))} | {cell(page.get('menu_path', []))} | {cell(page.get('name'))} | {cell(page.get('roles', []))} | {cell(page.get('function_ids', []))} | {cell(page.get('scenario_ids', []))} |")
    if not pages:
        lines.append("| — | — | 本期没有 UI 页面 | — | — | — |")
    lines += ["", "## 2. 页面明细", ""]
    for page in pages:
        lines += [f"### {page.get('id')} · {page.get('name')}", "",
                  f"- 菜单路径：{cell(page.get('menu_path', []))}", f"- 适用角色：{cell(page.get('roles', []))}",
                  f"- 承载功能：{cell(page.get('function_ids', []))}", f"- 承载场景：{cell(page.get('scenario_ids', []))}",
                  f"- 交互时序：{cell(page.get('sequence_ids', []))}", f"- 页面状态：{cell(page.get('states', []))}",
                  "", "字段：", ""]
        fields = page.get("fields", [])
        lines.append(bullets([named(field) for field in fields], "无特别字段"))
        lines += ["", "操作：", ""]
        actions = page.get("actions", [])
        if actions:
            for action in actions:
                lines.append(f"- **{action.get('id')} · {action.get('name')}**：来源功能 {cell(action.get('source_function_ids', []))}；来源场景 {cell(action.get('source_scenario_ids', []))}；权限 {cell(action.get('permission'))}")
        else:
            lines.append("- 无业务操作。")
        lines += ["", "原型验收点：", "", bullets(page.get("acceptance_points", []), "待补充"), ""]
    output = root / "outputs" / "menu-and-page-planning-reference.md"
    write_text(output, "\n".join(lines))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="从结构化事实源生成 Markdown 评审文档")
    parser.add_argument("project_dir")
    parser.add_argument("--kind", choices=["all", "scenarios", "interfaces", "features", "baseline", "pages"], default="all")
    args = parser.parse_args()
    root = Path(args.project_dir).expanduser().resolve()
    try:
        state = read_json(root / "runtime" / "loop-state.json")
        project = str(state.get("project", {}).get("name", root.name))
        renderers = {
            "scenarios": render_scenario_list,
            "interfaces": render_interface_matrix,
            "features": render_feature_list,
            "baseline": render_baseline,
            "pages": render_page_plan,
        }
        if args.kind == "all":
            selected = ["scenarios", "interfaces", "features", "baseline", "pages"]
        elif args.kind == "baseline":
            selected = ["features", "baseline"]
        else:
            selected = [args.kind]
        outputs = [renderers[kind](root, project) for kind in selected]
    except RenderError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    for output in outputs:
        print(f"已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
