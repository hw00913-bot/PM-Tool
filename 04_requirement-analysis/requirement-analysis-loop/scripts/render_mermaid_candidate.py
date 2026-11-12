#!/usr/bin/env python3
"""Read analysis facts into an isolated, non-formal Mermaid comparison candidate."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from swimlane_model import inspect_swimlane

CONTRACT = "mermaid-candidate@1"
TOOL = Path(__file__).resolve().parents[1] / "tools" / "mermaid-flow"
DERIVED_FILES = {"graph.json", "完整流程.mmd", "source-snapshot.json"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON 重复字段：" + key)
        result[key] = value
    return result


def json_read(raw):
    def invalid(value):
        raise ValueError("JSON 非有限数字：" + value)
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=invalid)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def within(path, base):
    return path == base or base in path.parents


def source_path(root, relative, *, must_exist=True):
    require(isinstance(relative, str) and relative and "\\" not in relative and "\x00" not in relative, "非法来源路径")
    rel = Path(relative)
    require(rel.parts and not rel.is_absolute() and ".." not in rel.parts and ":" not in rel.parts[0], "来源路径越界：" + relative)
    path = (root / rel).resolve()
    require(within(path, root) and path != root, "来源路径越界：" + relative)
    if must_exist:
        require(path.is_file(), "来源文件不存在：" + relative)
    return path


def candidate_path(root, destination, *, new):
    lexical = Path(os.path.abspath(destination))
    path = lexical.resolve()
    allowed = root / "runtime" / "mermaid-candidates"
    if within(lexical, root) or within(path, root):
        require(within(lexical, allowed) and lexical != allowed and within(path, allowed) and path != allowed,
                "项目内候选只允许写入 runtime/mermaid-candidates 的新子目录")
        require(path == lexical, "候选路径不能通过符号链接转向其他目录")
    require(not within(root, path), "候选目录不能是项目目录或其祖先")
    if new:
        require(not lexical.exists() and not lexical.is_symlink() and not path.exists(), "候选输出目录必须全新，不能覆盖：" + str(path))
    else:
        require(path.is_dir(), "候选目录不存在")
    return path


def records(data, key, prefix):
    values = data.get(key)
    require(isinstance(values, list), key + " 必须是数组")
    ids = []
    for item in values:
        require(isinstance(item, dict), key + " 包含非对象")
        value = item.get("id")
        require(isinstance(value, str) and re.fullmatch(prefix + r"-[0-9]{3,}", value), key + " 包含非法 ID")
        ids.append(value)
    require(len(ids) == len(set(ids)), key + " 包含重复 ID")
    return values


def _nonempty(value, field):
    require(isinstance(value, str) and value.strip(), field + " 必须是非空字符串")
    return value


def _refs(item, primary):
    refs = item.get("source_refs", [])
    require(isinstance(refs, list) and all(isinstance(ref, str) for ref in refs), "source_refs 必须是字符串数组")
    return [primary] + [ref for ref in refs if ref != primary]


def prepare_payload(root, diagram_id):
    """Deterministic read-only transformation, reused by the verifier."""
    require(root.is_dir(), "项目目录不存在")
    require(re.fullmatch(r"(?:SC|FLOW)-[0-9]{3,}", diagram_id), "只支持至少三位编号的 SC/FLOW 图 ID")
    raw_sources, loaded = {}, {}

    def read(relative):
        path = source_path(root, relative)
        raw = path.read_bytes()
        parsed = json_read(raw)
        require(isinstance(parsed, dict), relative + " 顶层必须是对象")
        raw_sources[relative], loaded[relative] = raw, parsed
        return parsed

    index_ref, rules_ref = "scenarios/scenario-index.json", "memory/business-rules.json"
    graph = {"page_id": diagram_id, "formal_delivery": False, "diagram_type": "ordinary-flow-candidate",
             "nodes": [], "edges": [], "phases": [], "navigation": [], "issues": [], "revision_notes": [],
             "layout": {"direction": "TB", "nodeSpacing": 60, "rankSpacing": 85}}
    inventory = {"nodes": [], "links": []}
    if diagram_id.startswith("SC-"):
        scenarios = records(read(index_ref), "scenarios", "SC")
        paths = []
        for indexed in scenarios:
            path = source_path(root, indexed.get("file"), must_exist=False)
            require(within(path, root / "scenarios") and path != root / "scenarios", "场景详情必须位于 scenarios 目录")
            paths.append(path)
        require(len(paths) == len(set(paths)), "场景索引重复引用详情文件")
        entry = next((item for item in scenarios if item["id"] == diagram_id), None)
        require(entry is not None, "场景索引中不存在 " + diagram_id)
        detail_ref = entry["file"]
        detail = read(detail_ref)
        require(detail.get("id") == diagram_id, "场景索引与详情 ID 不一致")
        model = detail.get("flow_diagram")
        require(isinstance(model, dict), "缺少 flow_diagram")
        for key in ("lanes", "nodes", "edges"):
            require(isinstance(model.get(key), list) and all(isinstance(item, dict) for item in model[key]), key + " 必须是对象数组")
        for key in ("lanes", "nodes"):
            for item in model[key]:
                _nonempty(item.get("id"), key + ".id")
        for node in model["nodes"]:
            _nonempty(node.get("lane_id"), "lane_id")
            _nonempty(node.get("label"), "label")
            order = node.get("order")
            require(isinstance(order, (int, float)) and not isinstance(order, bool) and math.isfinite(order), "order 必须是有限数字")
        for edge in model["edges"]:
            _nonempty(edge.get("from"), "from")
            _nonempty(edge.get("to"), "to")
            require(isinstance(edge.get("label", ""), str), "边 label 必须是字符串")
            require(edge.get("path_id") is None or isinstance(edge["path_id"], str), "path_id 必须是字符串")
        problems = inspect_swimlane(detail)
        require(not problems, "来源泳道模型检查未通过：" + "; ".join(p["message"] for p in problems))
        explicit_edges = [e["id"] for e in model["edges"] if "id" in e]
        require(all(isinstance(value, str) and value for value in explicit_edges) and len(explicit_edges) == len(set(explicit_edges)), "重复或非法连线 ID")
        lanes = {lane["id"]: lane for lane in model["lanes"]}
        ids = {node["id"]: f"N{i + 1:04d}" for i, node in enumerate(model["nodes"])}
        graph["title"] = f"{diagram_id} · {_nonempty(detail.get('name', entry.get('name')), 'name')}（普通流程候选，非泳道）"
        graph["source_lanes"] = model["lanes"]
        graph["source_url"] = detail_ref
        graph["issues"].append({"kind": "not_formal_swimlane", "ids": [diagram_id], "detail": "保留来源责任泳道归属，但仅呈现为节点责任标记；此候选不是正式泳道，也不代表业务验收。"})
        for i, node in enumerate(model["nodes"]):
            lane = lanes[node["lane_id"]]
            lane_name = _nonempty(lane.get("name"), "lane.name")
            ref = detail_ref + f"#/flow_diagram/nodes/{i}"
            text = "责任：" + lane_name + "\n" + node["label"]
            graph["nodes"].append({"id": ids[node["id"]], "kind": {"start": "terminal", "end": "terminal", "decision": "decision"}.get(node["type"], "process"),
                                   "text": text, "original_id": node["id"], "original_label": node["label"], "source_lane_id": node["lane_id"], "source_lane_name": lane_name,
                                   "source_type": node["type"], "source_refs": _refs(node, ref), "source_ids": [ref], "source_texts": [node["label"]], "display_reason": "仅附加来源明确的责任方；不改写原节点标签", "original_properties": node})
            inventory["nodes"].append({"id": ref, "text": node["label"]})
            if node["type"] == "start":
                graph["start"] = ids[node["id"]]
        for i, edge in enumerate(model["edges"]):
            ref = detail_ref + f"#/flow_diagram/edges/{i}"
            text = edge.get("label", "")
            graph["edges"].append({"id": f"E{i + 1:04d}", "kind": "flow", "source": ids[edge["from"]], "target": ids[edge["to"]], "text": text,
                                   "original_id": edge.get("id", ref), "original_label": text, "original_source": edge["from"], "original_target": edge["to"],
                                   "path_id": edge.get("path_id"), "source_refs": _refs(edge, ref), "source_ids": [ref], "source_texts": [text], "original_properties": edge, "evidence": "来源 flow_diagram.edges 原始显式连线"})
            inventory["links"].append({"id": ref, "text": text})
    else:
        flows = records(read(rules_ref), "business_flows", "FLOW")
        flow = next((item for item in flows if item["id"] == diagram_id), None)
        require(flow is not None, "业务流程中不存在 " + diagram_id)
        name = _nonempty(flow.get("name"), "name")
        steps = flow.get("steps")
        require(isinstance(steps, list) and steps and all(isinstance(step, str) and step.strip() for step in steps), "FLOW steps 必须是非空字符串数组")
        flow_index = flows.index(flow)
        base = rules_ref + f"#/business_flows/{flow_index}"
        graph["title"] = f"{diagram_id} · {name}（线性流程候选）"
        graph["source_url"] = base
        for i, step in enumerate(steps):
            ref = base + f"/steps/{i}"
            graph["nodes"].append({"id": f"N{i + 1:04d}", "kind": "process", "text": step, "original_id": ref, "original_label": step, "source_lane_name": None, "source_refs": [ref], "source_ids": [ref], "source_texts": [step]})
            inventory["nodes"].append({"id": ref, "text": step})
            if i:
                edge_ref = base + f"/steps-order/{i - 1}-{i}"
                graph["edges"].append({"id": f"E{i:04d}", "kind": "flow", "source": f"N{i:04d}", "target": f"N{i + 1:04d}", "text": "", "original_id": edge_ref, "original_label": "", "path_id": None, "source_refs": [base + f"/steps/{i - 1}", ref], "source_ids": [edge_ref], "source_texts": [""], "evidence": "仅按来源 steps 的既有数组顺序连接，不推断分支"})
                inventory["links"].append({"id": edge_ref, "text": ""})
    spec = importlib.util.spec_from_file_location("candidate_build_graph", TOOL / "scripts" / "build_graph.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph, mmd = module.build(graph, inventory)
    snapshot = {"contract": CONTRACT, "diagram_id": diagram_id, "formal_delivery": False, "purpose": "audit_only_not_new_business_fact", "sources": loaded}
    files = {"graph.json": json_bytes(graph), "完整流程.mmd": mmd.encode("utf-8"), "source-snapshot.json": json_bytes(snapshot)}
    return {relative: sha(raw) for relative, raw in raw_sources.items()}, files


def create_candidate(project, diagram_id, destination, *, prepare_only=False, runtime=None, node_command="node"):
    root = Path(project).resolve()
    out = candidate_path(root, destination, new=True)
    sources, files = prepare_payload(root, diagram_id)
    out.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        (out / name).write_bytes(raw)
    manifest = {"contract": CONTRACT, "diagram_id": diagram_id, "formal_delivery": False, "source_files": sources,
                "files": {name: sha(raw) for name, raw in files.items()}, "render": {"status": "not_run"}, "browser": {"status": "not_run"},
                "visual_review": "not_performed", "business_acceptance": "not_claimed"}
    (out / "candidate-manifest.json").write_bytes(json_bytes(manifest))
    if not prepare_only:
        command = [str(node_command), str(TOOL / "scripts" / "render.mjs"), "--input", str(out / "graph.json"), "--out", str(out)]
        if runtime:
            command += ["--runtime", str(Path(runtime).resolve())]
        subprocess.run(command, check=True)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir")
    parser.add_argument("--diagram-id", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--runtime")
    parser.add_argument("--node-command", default="node")
    args = parser.parse_args()
    try:
        result = create_candidate(args.project_dir, args.diagram_id, args.out, prepare_only=args.prepare_only, runtime=args.runtime, node_command=args.node_command)
        print(json.dumps({"candidate": str(result), "formal_delivery": False, "browser": "not_run" if args.prepare_only else "see_candidate_manifest"}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print("候选生成失败：" + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
