#!/usr/bin/env python3
"""Compile only business process and scenario swimlane facts to local PlantUML SVG."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from html import escape
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from business_diagrams import bind_business_svg


class PlantUMLRenderError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlantUMLRuntime:
    command: tuple[str, ...]
    description: str
    timeout_seconds: int = 45


LANE_COLORS = {
    "actor": "#EDF4FF",
    "system": "#EFFAF5",
    "external_system": "#FFF8E8",
}


def _safe_text(value: Any, fallback: str = "未命名") -> str:
    """Keep business text visible without allowing it to alter PlantUML syntax."""
    text = str(value or fallback).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "＼").replace("\n", r"\n")
    return text.translate(
        str.maketrans(
            {
                "@": "＠",
                "!": "！",
                "'": "’",
                '"': "”",
                "|": "｜",
                ":": "：",
                ";": "；",
                "(": "（",
                ")": "）",
                "[": "［",
                "]": "］",
                "{": "｛",
                "}": "｝",
                "<": "＜",
                ">": "＞",
                "#": "＃",
            }
        )
    )


def _edge_label(edge: dict[str, Any]) -> str:
    values = [str(edge.get("label", "")).strip(), str(edge.get("path_id", "")).strip()]
    return _safe_text(" · ".join(value for value in values if value), "继续")


class _ActivityCompiler:
    """Compile an acyclic flow graph into structured PlantUML activity syntax."""

    def __init__(self, detail: dict[str, Any]):
        self.detail = detail
        self.model = detail["flow_diagram"]
        self.nodes = {str(node["id"]): node for node in self.model["nodes"]}
        self.node_order = {
            str(node["id"]): (float(node["order"]), index)
            for index, node in enumerate(self.model["nodes"])
        }
        self.outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for edge in self.model["edges"]:
            source = str(edge["from"])
            target = str(edge["to"])
            self.outgoing[source].append(edge)
            self.incoming[target].append(edge)
        self.lane_aliases = {
            str(lane["id"]): f"lane_{index + 1}"
            for index, lane in enumerate(self.model["lanes"])
        }
        self.emitted: set[str] = set()
        self.lines: list[str] = []

    def compile(self) -> str:
        self._reject_cycles()
        starts = [node_id for node_id, node in self.nodes.items() if node.get("type") == "start"]
        if len(starts) != 1:
            raise PlantUMLRenderError("PlantUML 渲染要求且只能存在一个开始节点")
        for node_id, edges in self.outgoing.items():
            if len(edges) > 1 and self.nodes[node_id].get("type") != "decision":
                raise PlantUMLRenderError(
                    f"PlantUML 结构化渲染暂不支持非决策节点 {node_id} 的多出边；请改为 decision 或使用原生渲染器"
                )

        title = _safe_text(f"{self.detail.get('id', '')} · {self.detail.get('name', '业务场景')}")
        self.lines.extend(
            [
                "@startuml",
                f"title {title}",
                "skinparam backgroundColor transparent",
                "skinparam shadowing false",
                "skinparam defaultFontName SansSerif",
                "skinparam ArrowColor #55657D",
                "skinparam ArrowFontColor #344054",
                "skinparam ArrowFontSize 11",
                "skinparam activity {",
                "  BackgroundColor #FFFFFF",
                "  BorderColor #8291A8",
                "  FontColor #25324A",
                "  DiamondBackgroundColor #FFF8E8",
                "  DiamondBorderColor #D19724",
                "  DiamondFontColor #754B00",
                "  BackgroundColor<<event>> #F4EDFF",
                "  BorderColor<<event>> #8A5AC2",
                "  FontColor<<event>> #5D348D",
                "  BackgroundColor<<start_label>> #EEF2FF",
                "  BorderColor<<start_label>> #315EFB",
                "  BackgroundColor<<end_label>> #F2F4F7",
                "  BorderColor<<end_label>> #667085",
                "  BackgroundColor<<failure>> #FFF0F3",
                "  BorderColor<<failure>> #C9364F",
                "  FontColor<<failure>> #8F1930",
                "}",
                "skinparam swimlaneBorderColor #BAC5D5",
                "skinparam swimlaneTitleBackgroundColor #E9EDF3",
                "skinparam swimlaneTitleFontColor #344054",
                "skinparam swimlaneTitleFontSize 13",
                "!pragma useVerticalIf on",
            ]
        )
        for lane in self.model["lanes"]:
            lane_id = str(lane["id"])
            color = LANE_COLORS.get(str(lane.get("kind", "system")), "#F7F8FA")
            self.lines.append(f"|{color}|{self.lane_aliases[lane_id]}| {_safe_text(lane.get('name', lane_id))}")

        self._emit_path(starts[0], None, set())
        missing = sorted(set(self.nodes) - self.emitted, key=lambda item: self.node_order[item])
        if missing:
            raise PlantUMLRenderError(
                "PlantUML 无法把当前控制流规约为结构化活动图，未生成节点：" + ", ".join(missing)
            )
        self.lines.append("@enduml")
        return "\n".join(self.lines) + "\n"

    def _reject_cycles(self) -> None:
        indegree = {node_id: 0 for node_id in self.nodes}
        for edges in self.outgoing.values():
            for edge in edges:
                indegree[str(edge["to"])] += 1
        queue = deque(sorted((node_id for node_id, degree in indegree.items() if degree == 0), key=lambda item: self.node_order[item]))
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for edge in self.outgoing.get(current, []):
                target = str(edge["to"])
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(self.nodes):
            raise PlantUMLRenderError(
                "PlantUML 结构化渲染当前不接受回退环；请将重试展开为有界路径，或继续使用原生渲染器"
            )

    def _descendants(self, start: str) -> set[str]:
        result = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for edge in self.outgoing.get(current, []):
                target = str(edge["to"])
                if target not in result:
                    result.add(target)
                    queue.append(target)
        return result

    def _nearest_join(self, targets: list[str]) -> str | None:
        common = set.intersection(*(self._descendants(target) for target in targets))
        if not common:
            return None
        return min(common, key=lambda item: self.node_order[item])

    def _select_lane(self, node: dict[str, Any]) -> None:
        lane_id = str(node["lane_id"])
        self.lines.append(f"|{self.lane_aliases[lane_id]}|")

    def _node_stereotype(self, node_id: str, node: dict[str, Any]) -> str:
        if any(edge.get("type") in {"error", "failure"} for edge in self.incoming.get(node_id, [])):
            return "failure"
        if node.get("type") == "event":
            return "event"
        if node.get("type") == "start":
            return "start_label"
        if node.get("type") == "end":
            return "end_label"
        return ""

    def _emit_activity(self, node_id: str, node: dict[str, Any]) -> None:
        label = _safe_text(node.get("label", node_id))
        stereotype = self._node_stereotype(node_id, node)
        self.lines.append(f":{label};" + (f" <<{stereotype}>>" if stereotype else ""))

    def _emit_empty_branch(self) -> None:
        self.lines.append(":直接汇合; <<end_label>>")

    def _emit_path(self, node_id: str, stop_at: str | None, active: set[str]) -> None:
        if node_id == stop_at:
            return
        if node_id in active:
            raise PlantUMLRenderError(f"PlantUML 编译检测到循环：{node_id}")
        if node_id in self.emitted:
            raise PlantUMLRenderError(f"PlantUML 编译检测到非结构化汇合：{node_id}")

        node = self.nodes[node_id]
        node_type = str(node.get("type"))
        self.emitted.add(node_id)
        branch_active = set(active)
        branch_active.add(node_id)
        self._select_lane(node)

        if node_type == "start":
            self.lines.append("start")
            self._emit_activity(node_id, node)
        elif node_type == "end":
            self._emit_activity(node_id, node)
            self.lines.append("stop")
            return
        elif node_type == "decision":
            self._emit_decision(node_id, node, stop_at, branch_active)
            return
        else:
            self._emit_activity(node_id, node)

        next_edges = self.outgoing.get(node_id, [])
        if not next_edges:
            raise PlantUMLRenderError(f"PlantUML 编译发现非结束节点 {node_id} 没有后续节点")
        self._emit_path(str(next_edges[0]["to"]), stop_at, branch_active)

    def _emit_decision(
        self,
        node_id: str,
        node: dict[str, Any],
        outer_stop: str | None,
        active: set[str],
    ) -> None:
        edges = self.outgoing[node_id]
        targets = [str(edge["to"]) for edge in edges]
        join = self._nearest_join(targets)
        question = _safe_text(node.get("label", node_id))

        if len(edges) == 2:
            self.lines.append(f"if ({question}) then ({_edge_label(edges[0])})")
            if targets[0] == join:
                self._emit_empty_branch()
            else:
                self._emit_path(targets[0], join, active)
            self.lines.append(f"else ({_edge_label(edges[1])})")
            if targets[1] == join:
                self._emit_empty_branch()
            else:
                self._emit_path(targets[1], join, active)
            self.lines.append("endif")
        else:
            self.lines.append(f"switch ({question})")
            for edge, target in zip(edges, targets):
                self.lines.append(f"case ({_edge_label(edge)})")
                if target == join:
                    self._emit_empty_branch()
                else:
                    self._emit_path(target, join, active)
            self.lines.append("endswitch")

        if join and join != outer_stop:
            self._emit_path(join, outer_stop, active)


def compile_plantuml_activity(detail: dict[str, Any]) -> str:
    return _ActivityCompiler(detail).compile()


def compile_plantuml_business_flow(flow: dict[str, Any]) -> str:
    """Compile a high-level, ordered business flow to a PlantUML activity diagram."""
    flow_id = str(flow.get("id", "")).strip()
    name = str(flow.get("name", "")).strip()
    steps = flow.get("steps", [])
    if not flow_id or not name:
        raise PlantUMLRenderError("业务流程缺少 id 或 name，无法生成 PlantUML")
    if not isinstance(steps, list) or not steps or any(not str(step).strip() for step in steps):
        raise PlantUMLRenderError(f"{flow_id} 缺少有效业务步骤，无法生成 PlantUML")
    lines = [
        "@startuml",
        f"title {_safe_text(f'{flow_id} · {name}')}",
        "skinparam backgroundColor transparent",
        "skinparam shadowing false",
        "skinparam defaultFontName SansSerif",
        "skinparam ArrowColor #55657D",
        "skinparam ArrowFontColor #344054",
        "skinparam activity {",
        "  BackgroundColor #FFFFFF",
        "  BorderColor #8291A8",
        "  FontColor #25324A",
        "  StartColor #315EFB",
        "  EndColor #172033",
        "}",
        "start",
    ]
    lines.extend(f":{_safe_text(step)};" for step in steps)
    lines.extend(["stop", "@enduml"])
    return "\n".join(lines) + "\n"


def _resolve_executable(value: str, label: str) -> str:
    resolved = shutil.which(value)
    if not resolved:
        candidate = Path(value).expanduser().resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            resolved = str(candidate)
    if not resolved:
        raise PlantUMLRenderError(f"找不到可执行{label}：{value}")
    return resolved


def resolve_plantuml_runtime(
    command: str | None = None,
    jar: str | None = None,
    java_command: str | None = None,
    timeout_seconds: int = 45,
) -> PlantUMLRuntime:
    requested_command = command or os.environ.get("REQUIREMENT_ANALYSIS_PLANTUML_CMD")
    requested_jar = jar or os.environ.get("REQUIREMENT_ANALYSIS_PLANTUML_JAR")
    requested_java = java_command or os.environ.get("REQUIREMENT_ANALYSIS_JAVA_CMD")
    if requested_command:
        resolved = _resolve_executable(requested_command, " PlantUML 命令")
        return PlantUMLRuntime((resolved,), f"command:{resolved}", timeout_seconds)

    if requested_jar:
        jar_path = Path(requested_jar).expanduser().resolve()
        if not jar_path.is_file():
            raise PlantUMLRenderError(f"找不到 PlantUML JAR：{jar_path}")
        if requested_java:
            java = _resolve_executable(requested_java, " Java")
        else:
            java = shutil.which("java")
        if not java:
            raise PlantUMLRenderError("已指定 PlantUML JAR，但本机找不到 java")
        return PlantUMLRuntime(
            (java, "-Djava.awt.headless=true", "-jar", str(jar_path)),
            f"jar:{jar_path}",
            timeout_seconds,
        )

    resolved = shutil.which("plantuml")
    if resolved:
        return PlantUMLRuntime((resolved,), f"command:{resolved}", timeout_seconds)
    raise PlantUMLRenderError(
        "未找到本地 PlantUML。请安装 plantuml，或通过 --plantuml-jar / "
        "REQUIREMENT_ANALYSIS_PLANTUML_JAR 指定本地 JAR；不会回退到公网渲染服务"
    )


def _clean_svg(raw: str, detail: dict[str, Any], diagram_type: str = "swimlane") -> str:
    start = raw.find("<svg")
    end = raw.rfind("</svg>")
    if start < 0 or end < start:
        raise PlantUMLRenderError("PlantUML 没有返回有效 SVG")
    svg = raw[start : end + len("</svg>")]
    if re.search(r"syntax error|welcome to plantuml|error line", svg, flags=re.IGNORECASE):
        raise PlantUMLRenderError("PlantUML 返回语法错误图，已拒绝写入 HTML")
    if re.search(r"<(?:script|foreignObject)\b", svg, flags=re.IGNORECASE):
        raise PlantUMLRenderError("PlantUML SVG 包含不允许的可执行内容")
    if re.search(r"\son[a-z]+\s*=", svg, flags=re.IGNORECASE):
        raise PlantUMLRenderError("PlantUML SVG 包含不允许的事件处理属性")
    if re.search(r"\s(?:href|xlink:href)\s*=\s*['\"](?!#)", svg, flags=re.IGNORECASE):
        raise PlantUMLRenderError("PlantUML SVG 包含外部链接，已拒绝写入 HTML")
    svg = re.sub(r"<!--SRC=\[.*?\]-->", "", svg, flags=re.DOTALL)
    svg = re.sub(r"<\?plantuml.*?\?>", "", svg, flags=re.DOTALL)
    svg = re.sub(r'\sdata-diagram-type="[^"]*"', "", svg)
    opening_end = svg.find(">")
    opening = svg[: opening_end + 1]
    opening = re.sub(r'\sstyle="[^"]*"', "", opening)
    if re.search(r'\spreserveAspectRatio="[^"]*"', opening):
        opening = re.sub(r'\spreserveAspectRatio="[^"]*"', ' preserveAspectRatio="xMidYMin meet"', opening)
    else:
        opening = opening.replace("<svg", '<svg preserveAspectRatio="xMidYMin meet"', 1)
    svg = opening + svg[opening_end + 1 :]
    source_id = escape(str(detail.get("id", "diagram")), quote=True)
    default_names = {
        "swimlane": "业务场景泳道图",
        "business-process": "业务流程图",
    }
    default_name = default_names.get(diagram_type, "业务图")
    source_name = escape(str(detail.get("name", default_name)), quote=True)
    lane_attribute = ""
    flow_attribute = ""
    if diagram_type == "swimlane":
        lane_attribute = f' data-lane-count="{len(detail["flow_diagram"]["lanes"])}"'
    elif diagram_type == "business-process":
        flow_attribute = f' data-flow-id="{source_id}"'
    attributes = (
        f' class="plantuml-diagram" role="img" aria-label="{source_name}" '
        f'data-diagram-type="{escape(diagram_type, quote=True)}" data-renderer="plantuml" '
        f'data-source-id="{source_id}"{lane_attribute}{flow_attribute} data-plantuml-render="pass"'
    )
    return bind_business_svg(svg.replace("<svg", f"<svg{attributes}", 1), detail)


def render_plantuml_svg(
    source: str,
    detail: dict[str, Any],
    runtime: PlantUMLRuntime,
    diagram_type: str = "swimlane",
) -> str:
    if diagram_type not in {"swimlane", "business-process"}:
        raise PlantUMLRenderError("PlantUML 仅用于业务场景流程与泳道图")
    command = [*runtime.command, "-charset", "UTF-8", "-tsvg", "-pipe"]
    try:
        result = subprocess.run(
            command,
            input=source.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=runtime.timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlantUMLRenderError(f"本地 PlantUML 执行失败：{exc}") from exc
    stderr = result.stderr.decode("utf-8", errors="replace").strip()
    if result.returncode != 0:
        suffix = f"：{stderr[:500]}" if stderr else ""
        raise PlantUMLRenderError(f"本地 PlantUML 返回失败状态 {result.returncode}{suffix}")
    raw = result.stdout.decode("utf-8", errors="replace")
    return _clean_svg(raw, detail, diagram_type)
