"""Phase-aware contracts for interfaces before sequences and pages before features."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from operation_attribution import attribution_errors
from atlas_reading import desktop_html_errors
import re
from typing import Any
from xml.etree import ElementTree as ET

WORKFLOW_VERSION = "stories-flows-interfaces-sequences-pages-features@1"
PAGE_STRUCTURE = "outputs/page-structure-atlas.html"


def read(root: Path, relative: str) -> dict[str, Any]:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"项目内文件路径越界：{relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def interface_fingerprint(root: Path) -> str:
    # S5 may backfill usage, but must not change the interface facts approved at G2.
    records = [{key: value for key, value in api.items() if key != "used_by_sequence_ids"}
               for api in read(root, "interfaces/interface-index.json").get("interfaces", [])]
    payload = json.dumps(sorted(records, key=lambda api: api["id"]), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def scenario_fingerprint(root: Path) -> str:
    # S3 adds scenario details after G1. The scope decision uses the S2 index.
    scenarios = read(root, "scenarios/scenario-index.json")["scenarios"]
    return fact_fingerprint(sorted(scenarios, key=lambda scenario: scenario["id"]))


def interface_sources_fingerprint(root: Path) -> str:
    project = root.resolve()
    sources = {}
    for api in read(project, "interfaces/interface-index.json")["interfaces"]:
        refs = api.get("source_refs", [])
        if not isinstance(refs, list):
            continue  # Readiness handles the required references for verified interfaces.
        for reference in refs:
            if not isinstance(reference, str) or not reference.strip() or reference.startswith(("https://", "http://")):
                continue
            relative = reference.split("#", 1)[0]
            if relative in sources:
                continue
            path = (project / relative).resolve()
            # An unresolved source may be accepted as a risk. Bind its absence as well.
            sources[relative] = (hashlib.sha256(path.read_bytes()).hexdigest()
                                 if path.is_relative_to(project) and path.is_file() else None)
    return fact_fingerprint(sources)


def approval_basis(root: Path, gate: str) -> dict[str, str]:
    if gate == "G1":
        return {"scenario_fingerprint": scenario_fingerprint(root)}
    if gate == "G2":
        return {"interface_fingerprint": interface_fingerprint(root),
                "interface_sources_fingerprint": interface_sources_fingerprint(root)}
    raise ValueError(f"未定义批准依据：{gate}")


def approval_basis_errors(root: Path, state: dict, gate_ids: tuple[str, ...]) -> list[str]:
    errors = []
    for gate_id in gate_ids:
        gate = state.get("gates", {}).get(gate_id, {})
        stage = "S2" if gate_id == "G1" else "S4"
        if gate.get("status") != "approved":
            errors.append(f"{gate_id} 尚未批准")
            continue
        expected = approval_basis(root, gate_id)
        if any(not gate.get(key) for key in expected):
            errors.append(f"{gate_id} 旧批准缺少确认依据摘要，须回到 {stage} 重新核验与确认")
        elif any(gate[key] != value for key, value in expected.items()):
            facts = "场景范围或优先级" if gate_id == "G1" else "接口事实或本地来源"
            errors.append(f"{gate_id} 确认的{facts}已变化，须回到 {stage} 重新核验与确认")
    return errors


def interface_errors(root: Path, *, require_sequences: bool = False) -> list[str]:
    errors: list[str] = []
    scenarios = {sc["id"]: sc for sc in read(root, "scenarios/scenario-index.json")["scenarios"]
                 if sc.get("scope", "in_scope") == "in_scope"}
    apis = read(root, "interfaces/interface-index.json")["interfaces"]
    by_id = {api.get("id"): api for api in apis}
    covered: set[str] = set()
    if len(by_id) != len(apis):
        errors.append("接口 ID 重复")
    for api in apis:
        api_id = str(api.get("id", ""))
        if not re.fullmatch(r"API-\d{3,}", api_id):
            errors.append(f"接口 ID 不合法：{api_id}")
        refs = api.get("source_scenario_ids", [])
        if not isinstance(refs, list) or not refs or any(ref not in scenarios for ref in refs):
            errors.append(f"{api_id} 必须通过 source_scenario_ids 关联本期业务场景")
        else:
            covered.update(refs)
        status = api.get("verification_status")
        if status not in {"verified", "partial", "missing", "conflict", "not_required"}:
            errors.append(f"{api_id} verification_status 不合法")
        if status == "not_required" and not api.get("notes"):
            errors.append(f"{api_id} 不需要接口时必须在 notes 说明依据")
        if status == "verified":
            refs = api.get("source_refs", [])
            if not isinstance(refs, list) or not refs:
                errors.append(f"{api_id} 标为 verified 但没有 source_refs")
            else:
                for ref in refs:
                    if not isinstance(ref, str) or not ref.strip():
                        errors.append(f"{api_id} 的 source_refs 无效")
                    elif not ref.startswith(("https://", "http://")):
                        path = (root / ref.split("#", 1)[0]).resolve()
                        if not path.is_relative_to(root.resolve()) or not path.is_file():
                            errors.append(f"{api_id} 的接口证据不存在或越界：{ref}")
    for sc_id, sc in scenarios.items():
        detail = read(root, sc["file"])
        if any(lane.get("kind") == "external_system" for lane in detail.get("flow_diagram", {}).get("lanes", [])) and sc_id not in covered:
            errors.append(f"{sc_id} 涉及外部系统，但没有接口核验结论")
    if require_sequences:
        actual: dict[str, set[str]] = {str(api_id): set() for api_id in by_id}
        for seq in read(root, "sequences/sequence-index.json")["sequences"]:
            detail = read(root, seq["file"])
            for message in detail.get("messages", []):
                api_id = message.get("api_id")
                if not api_id:
                    continue
                if api_id not in by_id:
                    errors.append(f"{seq['id']} 引用了未核验接口 {api_id}；回到 S4")
                else:
                    api = by_id[api_id]
                    if seq["scenario_id"] not in api.get("source_scenario_ids", []):
                        errors.append(f"{seq['id']} 超出 {api_id} 的场景核验范围；回到 S4")
                    if api.get("verification_status") == "not_required":
                        errors.append(f"{seq['id']} 调用了 not_required 接口 {api_id}")
                    actual[api_id].add(seq["id"])
        for api_id, api in by_id.items():
            if set(api.get("used_by_sequence_ids", [])) != actual[api_id]:
                errors.append(f"{api_id} 的 used_by_sequence_ids 尚未按实际时序回填")
    return errors


def page_errors(root: Path, *, final: bool = False) -> list[str]:
    errors: list[str] = []
    scenarios = {sc["id"]: sc for sc in read(root, "scenarios/scenario-index.json")["scenarios"]
                 if sc.get("scope", "in_scope") == "in_scope"}
    sequences = {seq["id"]: seq for seq in read(root, "sequences/sequence-index.json")["sequences"]}
    pages = read(root, "planning/page-index.json")["pages"]
    features = {feat["id"]: feat for feat in read(root, "memory/feature-list.json")["features"]} if final else {}
    covered: set[str] = set()
    surfaced: set[str] = set()
    seen: set[str] = set()
    for page in pages:
        page_id = str(page.get("id", ""))
        if not re.fullmatch(r"PAGE-\d{3,}", page_id) or page_id in seen:
            errors.append(f"页面 ID 不合法或重复：{page_id}")
        seen.add(page_id)
        if not isinstance(page.get("name"), str) or not page["name"].strip():
            errors.append(f"{page_id} 缺少页面名称")
        path = page.get("menu_path")
        if not isinstance(path, list) or any(not isinstance(part, str) or not part.strip() for part in path):
            errors.append(f"{page_id} menu_path 必须是非空文本数组（无菜单入口时用 []）")
        sc_refs = page.get("scenario_ids", [])
        if not sc_refs or any(ref not in scenarios for ref in sc_refs):
            errors.append(f"{page_id} 缺少有效业务场景来源")
        if page.get("lifecycle_status") != "retired_source":
            covered.update(sc_refs)
        seq_refs = page.get("sequence_ids", [])
        if not seq_refs or any(ref not in sequences or sequences[ref]["scenario_id"] not in sc_refs for ref in seq_refs):
            errors.append(f"{page_id} 缺少与场景一致的 sequence_ids")
        for sc_id in sc_refs:
            if not any(ref in sequences and sequences[ref]["scenario_id"] == sc_id for ref in seq_refs):
                errors.append(f"{page_id} 未引用 {sc_id} 的交互时序")
        for action in page.get("actions", []):
            action_refs = action.get("source_scenario_ids", [])
            if not action_refs or not set(action_refs).issubset(sc_refs):
                errors.append(f"{action.get('id')} 缺少页面范围内的业务场景来源")
        if final and page.get("lifecycle_status") != "retired_source":
            surfaced.update(page.get("function_ids", []))
    required_ui = {key for key, sc in scenarios.items() if sc.get("ui_surface", "required") == "required"}
    for sc_id in sorted(required_ui - covered):
        errors.append(f"{sc_id} 需要 UI，但没有页面承载")
    if final:
        # A validated retained source or history migration is legal ownership,
        # not a new FUNC. Unknown/empty/candidate/unresolved ownership still fails.
        errors.extend(attribution_errors(root, validate_feature_bodies=False))
        for feat_id, feat in features.items():
            if required_ui.intersection(feat.get("source_scenario_ids", [])) and feat_id not in surfaced:
                errors.append(f"{feat_id} 关联需要 UI 的场景，但没有页面承载")
    return errors


def page_html_errors(root: Path) -> list[str]:
    path = root / PAGE_STRUCTURE
    if not path.is_file():
        return [f"缺少页面结构 HTML：{PAGE_STRUCTURE}"]
    html = path.read_text(encoding="utf-8")
    svgs = svg_roots(html)
    page_ids = sorted(page["id"] for page in read(root, "planning/page-index.json")["pages"])
    matching = [svg for svg in svgs if svg.get("data-diagram-type") == "page-structure"
                and svg.get("data-renderer") == "native" and svg.get("data-layout-audit") == "pass"
                and svg.get("data-page-ids") == ",".join(page_ids)
                and svg.get("data-page-structure-sha256") == page_structure_fingerprint(root)
                and sorted(node.get("data-page-node") for node in svg.iter() if node.get("data-page-node")) == page_ids
                and any(node.tag.endswith("}rect") for node in svg.iter())]
    if len(svgs) != 1 or len(matching) != 1 or "status: draft" in html.lower() or any(is_plantuml(svg) for svg in svgs):
        return ["页面结构图必须是完整的原生 SVG（不使用 PlantUML），且 PAGE 覆盖与事实指纹一致"]
    return []


def fact_fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def page_structure_fingerprint(root: Path) -> str:
    return fact_fingerprint(read(root, "planning/page-index.json")["pages"])


def svg_roots(html: str) -> list[ET.Element]:
    try:
        return [ET.fromstring(svg) for svg in re.findall(r"<svg\b.*?</svg\s*>", html, flags=re.DOTALL | re.IGNORECASE)]
    except ET.ParseError as exc:
        raise ValueError(f"内嵌 SVG 无法解析：{exc}") from exc


def is_plantuml(svg: ET.Element) -> bool:
    return svg.get("data-renderer") == "plantuml" or svg.get("data-plantuml-render") is not None


def sequence_html_errors(root: Path) -> list[str]:
    path = root / "outputs/interaction-sequence-atlas.html"
    if not path.is_file():
        return ["缺少交互时序 HTML：outputs/interaction-sequence-atlas.html"]
    html = path.read_text(encoding="utf-8")
    svgs = svg_roots(html)
    errors: list[str] = desktop_html_errors(html)
    if any(is_plantuml(svg) for svg in svgs):
        errors.append("交互时序图不得使用或混入 PlantUML SVG；请重新生成原生 SVG 图集")
    entries = read(root, "sequences/sequence-index.json")["sequences"]
    if "status: draft" in html.lower() or len(svgs) != len(entries):
        errors.append("交互时序图集仍为草稿或图数与时序索引不一致")
    for entry in entries:
        detail = read(root, entry["file"])
        expected_messages = [str(index + 1) for index in range(len(detail.get("messages", [])))]
        matching = [svg for svg in svgs if svg.get("data-diagram-type") == "sequence"
                    and svg.get("data-sequence-id") == entry["id"]
                    and svg.get("data-renderer") == "native" and not is_plantuml(svg)
                    and svg.get("data-layout-audit") == "pass"
                    and svg.get("data-sequence-sha256") == fact_fingerprint(detail)
                    and [node.get("data-message-index") for node in svg.iter() if node.get("data-message-index")] == expected_messages
                    and any(node.tag.endswith("}rect") for node in svg.iter())]
        if len(matching) != 1 or not expected_messages:
            errors.append(f"{entry['id']} 缺少唯一完整的原生时序 SVG，或图集与时序事实不一致；请重新渲染")
    return errors
