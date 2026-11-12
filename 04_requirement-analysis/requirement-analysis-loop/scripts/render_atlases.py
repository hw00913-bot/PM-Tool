#!/usr/bin/env python3
"""Render self-contained business scenario and interaction sequence atlases."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path
import re
import sys
import textwrap
from typing import Any
from xml.etree import ElementTree as ET
from business_diagrams import bind_business_svg

from plantuml_renderer import (
    PlantUMLRenderError,
    PlantUMLRuntime,
    compile_plantuml_activity,
    compile_plantuml_business_flow,
    render_plantuml_svg,
    resolve_plantuml_runtime,
)
from swimlane_layout import audit_swimlane_layout, build_swimlane_layout, estimate_label_width
from swimlane_model import inspect_swimlane
from analysis_contract import PAGE_STRUCTURE, page_errors, page_structure_fingerprint, fact_fingerprint
from native_diagrams import page_structure_svg, wrap_label, text_lines
from atlas_reading import READING_LAYOUT


class RenderError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RenderError(f"缺少文件：{path}") from exc
    except json.JSONDecodeError as exc:
        raise RenderError(f"JSON 无法解析：{path}（{exc}）") from exc


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RenderError(f"项目内文件路径越界：{relative}") from exc
    return path


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def load_records(root: Path, index_file: str, key: str, include: Any = None) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    index = load_json(root / index_file)
    entries = index.get(key)
    if not isinstance(entries, list) or not entries:
        raise RenderError(f"{index_file} 的 {key} 为空，无法生成图集")
    if include:
        entries = [entry for entry in entries if include(entry)]
    if not entries:
        raise RenderError(f"{index_file} 没有需要生成图集的 {key}")
    records = []
    for entry in entries:
        relative = entry.get("file")
        if not relative:
            raise RenderError(f"{entry.get('id', '未知对象')} 缺少 file")
        detail = load_json(safe_path(root, str(relative)))
        if detail.get("id") != entry.get("id"):
            raise RenderError(f"{relative} 的 ID 与索引不一致")
        records.append((entry, detail))
    return records


BASE_CSS = """
:root{--bg:#f5f7fb;--paper:#fff;--ink:#172033;--muted:#667085;--line:#d7deea;--primary:#315efb;--primary-soft:#eef2ff;--good:#11865b;--warn:#b76e00;--danger:#c9364f;--shadow:0 12px 32px rgba(23,32,51,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
header{background:linear-gradient(135deg,#172d67,#315efb);color:#fff;padding:42px max(28px,calc((100vw - 1180px)/2));box-shadow:var(--shadow)}
header h1{margin:0 0 8px;font-size:32px}header p{margin:0;opacity:.8}.layout{max-width:1180px;margin:28px auto 72px;padding:0 24px;display:grid;grid-template-columns:250px minmax(0,1fr);gap:24px}
nav{position:sticky;top:20px;align-self:start;background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:16px;max-height:calc(100vh - 40px);overflow:auto}
nav strong{display:block;margin-bottom:8px}nav a{display:block;color:#344054;text-decoration:none;padding:7px 9px;border-radius:8px}nav a:hover{background:var(--primary-soft);color:var(--primary)}main{min-width:0}.card{background:var(--paper);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:24px;margin-bottom:22px;scroll-margin-top:20px}
.card h2{font-size:23px;margin:0 0 6px}.meta{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 18px}.badge{display:inline-flex;align-items:center;background:var(--primary-soft);color:#2448c7;border-radius:999px;padding:3px 10px;font-size:12px;font-weight:650}.badge.neutral{background:#f2f4f7;color:#475467}.section{margin-top:20px}.section h3{font-size:15px;margin:0 0 10px;color:#344054}.chips{display:flex;flex-wrap:wrap;gap:8px}.chip{border:1px solid var(--line);border-radius:999px;padding:4px 10px;background:#fff}
.muted{color:var(--muted)}ul.clean{margin:8px 0;padding-left:20px}.alt{border-left:4px solid #f2b84b;background:#fffbeb;padding:10px 14px;border-radius:0 10px 10px 0;margin:10px 0}.atlas-heading{margin:0 0 16px;padding:4px 2px}.atlas-heading:not(:first-child){margin-top:34px}.atlas-heading h2{margin:0 0 4px;font-size:25px}.diagram{overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fcfdff;padding:12px}.diagram:fullscreen{background:#fff;padding:24px}.diagram svg{display:block;margin:auto;max-width:100%;height:auto}.diagram-panel{margin-top:10px}.diagram-toolbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:0 0 8px}.diagram-toolbar button{appearance:none;border:1px solid var(--line);border-radius:8px;background:#fff;color:#344054;padding:5px 10px;font:inherit;font-size:12px;cursor:pointer}.diagram-toolbar button:hover{border-color:#9aaaf5;background:var(--primary-soft);color:#2448c7}.diagram-toolbar .renderer-label{margin-right:auto;color:var(--muted);font-size:12px}.diagram-versions{display:grid;gap:12px}.diagram-version{border:1px solid var(--line);border-radius:12px;background:#fff;padding:12px}.diagram-version>summary{cursor:pointer;font-weight:650;color:#344054}.diagram-version[open]>summary{margin-bottom:10px}.message-table,.flow-table{width:100%;border-collapse:collapse;margin-top:14px}.message-table th,.message-table td,.flow-table th,.flow-table td{border-bottom:1px solid var(--line);padding:9px;text-align:left;vertical-align:top}.message-table th,.flow-table th{color:#475467;font-size:12px}.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin:8px 0}.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px}.swimlane-note{margin:8px 0 0;color:var(--muted);font-size:12px}
footer{max-width:1180px;margin:-46px auto 30px;padding:0 24px;color:var(--muted);font-size:12px}@media(max-width:800px){.atlas-document .layout{grid-template-columns:1fr}.atlas-document nav{position:relative;top:0}.atlas-document header{padding:28px 24px}.atlas-document .card{padding:18px}}
.atlas-desktop header{padding:18px 20px}.atlas-desktop header h1{font-size:26px}.atlas-desktop .layout{max-width:none;width:100%;margin:16px 0 64px;padding:0 16px;display:block}.atlas-desktop .card{padding:16px}.atlas-desktop footer{max-width:none;padding:0 20px}.atlas-directory{margin:0 0 14px;border:1px solid var(--line);border-radius:10px;background:var(--paper);padding:9px 12px}.atlas-directory>summary{cursor:pointer;font-weight:650}.atlas-directory nav{position:static;max-height:35vh;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:4px;border:0;padding:8px 0 0}.atlas-desktop .diagram svg{width:100%;min-width:var(--diagram-natural-width);max-width:none;height:auto}.diagram-panel:fullscreen{overflow:auto;background:#fff;padding:16px}.diagram-panel:fullscreen .diagram-toolbar{position:sticky;top:0;z-index:2;background:#fff;padding:8px 0}.diagram-status{color:var(--muted);font-size:12px}
"""


PAGE_SCRIPT = r"""
(function () {
function dimensions(panel) {
  return {width: Number(panel.dataset.naturalWidth), height: Number(panel.dataset.naturalHeight)};
}
function applyScale(panel, scale, mode) {
  const svg = panel.querySelector("svg");
  const size = dimensions(panel);
  panel.dataset.scale = String(scale);
  panel.dataset.viewMode = mode;
  svg.style.minWidth = "0";
  svg.style.maxWidth = "none";
  svg.style.width = `${Math.round(size.width * scale)}px`;
  svg.style.height = `${Math.round(size.height * scale)}px`;
}
function fitWidth(panel) {
  const canvas = panel.querySelector(".diagram");
  const style = getComputedStyle(canvas);
  const available = canvas.clientWidth - parseFloat(style.paddingLeft || 0) - parseFloat(style.paddingRight || 0);
  if (available > 0) applyScale(panel, available / dimensions(panel).width, "fit");
}
document.addEventListener("click", function (event) {
  const button = event.target.closest("[data-diagram-action]");
  if (!button) return;
  const panel = button.closest("[data-diagram-panel]");
  const svg = panel && panel.querySelector("svg");
  if (!panel || !svg) return;
  const action = button.dataset.diagramAction;
  const baseWidth = dimensions(panel).width;
  const scale = panel.dataset.scale ? Number(panel.dataset.scale) : svg.getBoundingClientRect().width / baseWidth;
  if (action === "fit") fitWidth(panel);
  if (action === "readable") {
    delete panel.dataset.scale;
    panel.dataset.viewMode = "readable";
    svg.style.width = "";
    svg.style.height = "";
    svg.style.minWidth = "";
    svg.style.maxWidth = "";
  }
  if (action === "actual") applyScale(panel, 1, "manual");
  if (action === "zoom-in") applyScale(panel, scale * 1.2, "manual");
  if (action === "zoom-out") applyScale(panel, Math.max(0.1, scale / 1.2), "manual");
  if (action === "fullscreen") {
    const status = panel.querySelector(".diagram-status");
    if (document.fullscreenElement === panel && document.exitFullscreen) document.exitFullscreen();
    else if (panel.requestFullscreen) panel.requestFullscreen().catch(() => { status.textContent = "全屏未开启，可继续缩放或使用浏览器全屏。"; });
    else status.textContent = "当前环境不支持全屏，可继续缩放或使用浏览器全屏。";
  }
  if (action === "download") {
    const clone = svg.cloneNode(true);
    clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    clone.style.width = "";
    clone.style.height = "";
    clone.style.minWidth = "";
    clone.style.maxWidth = "";
    const blob = new Blob([new XMLSerializer().serializeToString(clone)], {type: "image/svg+xml;charset=utf-8"});
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = panel.dataset.filename || "business-flow.svg";
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }
});
function refitPanels() {
  document.querySelectorAll('[data-diagram-panel][data-view-mode="fit"]').forEach(fitWidth);
}
window.addEventListener("resize", refitPanels);
document.addEventListener("fullscreenchange", refitPanels);
document.addEventListener("toggle", refitPanels, true);
})();
"""


def page_shell(title: str, project: str, records: list[tuple[str, str]], body: str, desktop: bool = False) -> str:
    nav = "".join(f'<a href="#{escape(anchor)}">{escape(label)}</a>' for anchor, label in records)
    directory = (f'<details class="atlas-directory"><summary>图集目录 · 点击展开</summary><nav aria-label="图集目录">{nav}</nav></details>'
                 if desktop else f'<nav><strong>目录</strong>{nav}</nav>')
    body_class = "atlas-desktop" if desktop else "atlas-document"
    reading_attribute = f' data-reading-layout="{READING_LAYOUT}"' if desktop else ""
    reading_note = " · 桌面宽屏阅读：窄图铺满宽度，宽图保持可读比例，可横向滚动或手动适应宽度" if desktop else ""
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
<title>{escape(title)} · {escape(project)}</title><style>{BASE_CSS}</style></head>
<body class="{body_class}"{reading_attribute}><header><h1>{escape(title)}</h1><p>{escape(project)} · 自动汇编于 {escape(now())}{reading_note}</p></header>
<div class="layout">{directory}<main>{body}</main></div>
<footer>本图集由结构化事实源生成；如图文不一致，以对应 JSON 和最新核验结果为准。</footer>
<script>{PAGE_SCRIPT}</script></body></html>"""


def list_html(values: list[Any]) -> str:
    if not values:
        return '<span class="muted">无</span>'
    return '<ul class="clean">' + "".join(f"<li>{escape(str(value))}</li>" for value in values) + "</ul>"


def validate_swimlane_model(detail: dict[str, Any]) -> dict[str, Any]:
    scenario_id = str(detail.get("id", "未知场景"))
    problems = inspect_swimlane(detail)
    if problems:
        raise RenderError(f"{scenario_id} 的泳道图无效：{problems[0]['message']}")
    return detail["flow_diagram"]


def wrapped_lines(value: Any, width: int) -> list[str]:
    text = str(value or "")
    return textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False) or [""]


def svg_text(x: float, y: float, label: Any, width: int = 14, color: str = "#172033", size: int = 12) -> str:
    lines = wrapped_lines(label, width)[:3]
    start_y = y - (len(lines) - 1) * 7
    spans = "".join(
        f'<tspan x="{x}" y="{start_y + index * 14}">{escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    return f'<text x="{x}" y="{y}" text-anchor="middle" font-size="{size}" fill="{color}">{spans}</text>'


def svg_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def audited_swimlane_layout(detail: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    model = validate_swimlane_model(detail)
    layout = build_swimlane_layout(model)
    layout_problems = audit_swimlane_layout(layout)
    if layout_problems:
        raise RenderError(f"{detail.get('id', '未知场景')} 的泳道布局无效：{layout_problems[0]}")
    return model, layout


def swimlane_svg(detail: dict[str, Any]) -> str:
    model, layout = audited_swimlane_layout(detail)
    lanes = model["lanes"]
    nodes = model["nodes"]
    lane_width = float(layout["lane_width"])
    width = float(layout["width"])
    height = float(layout["height"])
    lane_top = float(layout["lane_top"])
    lane_bottom = float(layout["lane_bottom"])
    header_height = float(layout["header_height"])
    lane_x = layout["lane_left"]
    geometry = layout["nodes"]

    scenario_token = "".join(char if char.isalnum() else "-" for char in str(detail.get("id", "scenario")))
    marker_id = f"flow-arrow-{scenario_token}"
    failure_marker_id = f"flow-arrow-failure-{scenario_token}"
    parts = [
        f'<svg viewBox="0 0 {svg_number(width)} {svg_number(height)}" width="{svg_number(width)}" height="{svg_number(height)}" role="img" '
        f'aria-label="{escape(str(detail.get("name", "业务场景泳道图")))}" data-diagram-type="swimlane" '
        f'data-source-id="{escape(str(detail.get("id", "scenario")))}" data-scenario-id="{escape(str(detail.get("id", "scenario")))}" '
        f'data-lane-count="{len(lanes)}" data-router-version="2" data-layout-audit="pass">',
        f'<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#55657d"/></marker>'
        f'<marker id="{failure_marker_id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="#c9364f"/></marker></defs>',
    ]
    lane_colors = {
        "actor": ("#edf4ff", "#d7e6ff", "#2859a6"),
        "system": ("#effaf5", "#d8f2e5", "#17704c"),
        "external_system": ("#fff8e8", "#f9e9bd", "#9a6100"),
    }
    for index, lane in enumerate(lanes):
        lane_id = str(lane["id"])
        kind = str(lane.get("kind", "system"))
        body_fill, header_fill, header_text = lane_colors.get(kind, ("#f7f8fa", "#e9edf3", "#475467"))
        x = float(lane_x[lane_id])
        parts.append(
            f'<g class="swimlane lane" data-lane-id="{escape(lane_id)}">'
            f'<rect x="{svg_number(x)}" y="{svg_number(lane_top)}" width="{svg_number(lane_width)}" height="{svg_number(lane_bottom - lane_top)}" fill="{body_fill}" stroke="#bac5d5"/>'
            f'<rect x="{svg_number(x)}" y="{svg_number(lane_top)}" width="{svg_number(lane_width)}" height="{svg_number(header_height)}" fill="{header_fill}" stroke="#bac5d5"/>'
            f'{svg_text(x + lane_width / 2, lane_top + 29, lane.get("name", lane_id), 20, header_text, 13)}'
            f'<text x="{svg_number(x + lane_width / 2)}" y="{svg_number(lane_top + 50)}" text-anchor="middle" font-size="10" fill="#667085">{escape(kind)}</text></g>'
        )

    labels: list[str] = []
    for edge in layout["edges"]:
        edge_index = int(edge["index"])
        source_id = str(edge["source_id"])
        target_id = str(edge["target_id"])
        raw_edge = edge["raw"]
        points = edge["points"]
        path_data = " ".join(
            ("M" if point_index == 0 else "L") + f" {svg_number(float(point[0]))} {svg_number(float(point[1]))}"
            for point_index, point in enumerate(points)
        )
        edge_color = "#c9364f" if raw_edge.get("type") in {"error", "failure"} else "#55657d"
        edge_marker_id = failure_marker_id if raw_edge.get("type") in {"error", "failure"} else marker_id
        route_channel = edge.get("corridor_channel", "none")
        parts.append(
            f'<path class="flow-edge-halo" d="{path_data}" fill="none" stroke="#fcfdff" stroke-width="7" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<path class="flow-edge" data-edge-index="{edge_index}" data-from="{escape(source_id)}" data-to="{escape(target_id)}" '
            f'data-route-kind="{escape(str(edge["route_kind"]))}" data-route-channel="{escape(str(route_channel))}" '
            f'data-source-port="{svg_number(float(points[0][0]))},{svg_number(float(points[0][1]))}" '
            f'data-target-port="{svg_number(float(points[-1][0]))},{svg_number(float(points[-1][1]))}" '
            f'd="{path_data}" fill="none" stroke="{edge_color}" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" marker-end="url(#{edge_marker_id})"/>'
        )
        edge_label = str(edge["label_text"])
        if edge_label:
            position = edge["label_position"]
            label_x = float(position["x"])
            label_y = float(position["y"])
            chip_width = estimate_label_width(edge_label)
            chip_x = label_x - chip_width / 2 if position["anchor"] == "middle" else label_x - 5
            labels.append(
                f'<g class="flow-edge-label" data-edge-index="{edge_index}">'
                f'<rect x="{svg_number(chip_x)}" y="{svg_number(label_y - 12)}" width="{svg_number(chip_width)}" height="18" rx="8" fill="#fff" fill-opacity=".96" stroke="#d7deea"/>'
                f'<text x="{svg_number(label_x)}" y="{svg_number(label_y + 1)}" text-anchor="{position["anchor"]}" font-size="11" font-weight="650" fill="{edge_color}">{escape(edge_label)}</text></g>'
            )
    parts.extend(labels)

    for node in nodes:
        node_id = str(node["id"])
        node_type = str(node.get("type", "action"))
        node_geometry = geometry[node_id]
        x = float(node_geometry["x"])
        y = float(node_geometry["y"])
        shape_width = float(node_geometry["width"])
        shape_height = float(node_geometry["height"])
        common = f'class="flow-node {escape(node_type)}" data-node-id="{escape(node_id)}" data-node-type="{escape(node_type)}" data-lane-id="{escape(str(node["lane_id"]))}"'
        if node_type == "decision":
            points = " ".join(
                f"{svg_number(px)},{svg_number(py)}"
                for px, py in [
                    (x, y - shape_height / 2),
                    (x + shape_width / 2, y),
                    (x, y + shape_height / 2),
                    (x - shape_width / 2, y),
                ]
            )
            parts.append(f'<polygon {common} points="{points}" fill="#fff8e8" stroke="#d19724" stroke-width="1.6"/>')
            parts.append(svg_text(x, y + 4, node.get("label", node_id), 12, "#754b00", 11))
        elif node_type == "start":
            parts.append(f'<rect {common} x="{svg_number(x - shape_width / 2)}" y="{svg_number(y - shape_height / 2)}" width="{svg_number(shape_width)}" height="{svg_number(shape_height)}" rx="21" fill="#315efb" stroke="#2448c7" stroke-width="1.6"/>')
            parts.append(svg_text(x, y + 4, node.get("label", "开始"), 12, "#fff", 12))
        elif node_type == "end":
            parts.append(f'<rect {common} x="{svg_number(x - shape_width / 2)}" y="{svg_number(y - shape_height / 2)}" width="{svg_number(shape_width)}" height="{svg_number(shape_height)}" rx="21" fill="#172033" stroke="#0c111d" stroke-width="1.6"/>')
            parts.append(svg_text(x, y + 4, node.get("label", "结束"), 12, "#fff", 12))
        elif node_type == "event":
            half_w, half_h = shape_width / 2, shape_height / 2
            points = " ".join(
                f"{svg_number(px)},{svg_number(py)}"
                for px, py in [
                    (x - half_w + 14, y - half_h),
                    (x + half_w - 14, y - half_h),
                    (x + half_w, y),
                    (x + half_w - 14, y + half_h),
                    (x - half_w + 14, y + half_h),
                    (x - half_w, y),
                ]
            )
            parts.append(f'<polygon {common} points="{points}" fill="#f4edff" stroke="#8a5ac2" stroke-width="1.6"/>')
            parts.append(svg_text(x, y + 4, node.get("label", node_id), 15, "#5d348d", 12))
        else:
            parts.append(f'<rect {common} x="{svg_number(x - shape_width / 2)}" y="{svg_number(y - shape_height / 2)}" width="{svg_number(shape_width)}" height="{svg_number(shape_height)}" rx="10" fill="#fff" stroke="#8291a8" stroke-width="1.5"/>')
            parts.append(svg_text(x, y + 4, node.get("label", node_id), 15, "#25324a", 12))
    parts.append("</svg>")
    return bind_business_svg("".join(parts), detail)


def diagram_panel(svg: str, renderer: str, scenario_id: str) -> str:
    renderer_label = "PlantUML 本地渲染" if renderer == "plantuml" else "原生 SVG 渲染"
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", scenario_id).strip("-.") or "scenario"
    filename = f"{safe_id}-{renderer}.svg"
    root = ET.fromstring(svg)
    view_box = root.get("viewBox", "").replace(",", " ").split()
    if len(view_box) == 4:
        width, height = float(view_box[2]), float(view_box[3])
    else:
        width = float(root.get("width", "800").removesuffix("px"))
        height = float(root.get("height", "600").removesuffix("px"))
    if not (0 < width < float("inf") and 0 < height < float("inf")):
        raise RenderError(f"{scenario_id} 的 SVG 尺寸不合法")
    return (
        f'<div class="diagram-panel" data-diagram-panel data-renderer="{escape(renderer)}" '
        f'data-filename="{escape(filename)}" data-natural-width="{width:g}" data-natural-height="{height:g}" '
        f'data-view-mode="readable" style="--diagram-natural-width:{width:g}px">'
        f'<div class="diagram-toolbar"><span class="renderer-label">{escape(renderer_label)}</span>'
        '<button type="button" data-diagram-action="zoom-out" aria-label="缩小流程图">缩小</button>'
        '<button type="button" data-diagram-action="zoom-in" aria-label="放大流程图">放大</button>'
        '<button type="button" data-diagram-action="fit" aria-label="流程图适应容器宽度">适应宽度</button>'
        '<button type="button" data-diagram-action="readable" aria-label="恢复默认阅读比例">阅读模式</button>'
        '<button type="button" data-diagram-action="actual" aria-label="流程图恢复原始比例">100%</button>'
        '<button type="button" data-diagram-action="fullscreen" aria-label="全屏查看流程图">全屏</button>'
        '<button type="button" data-diagram-action="download" aria-label="下载 SVG">下载 SVG</button>'
        '<span class="diagram-status" role="status"></span></div>'
        f'<div class="diagram">{svg}</div></div>'
    )


def validate_business_flow(flow: dict[str, Any]) -> list[str]:
    flow_id = str(flow.get("id", "")).strip()
    name = str(flow.get("name", "")).strip()
    steps = flow.get("steps", [])
    if not flow_id or not name:
        raise RenderError("业务流程缺少 id 或 name")
    if not isinstance(steps, list) or not steps or any(not str(step).strip() for step in steps):
        raise RenderError(f"{flow_id} 缺少有效业务步骤")
    return [str(step).strip() for step in steps]


def business_flow_svg(flow: dict[str, Any]) -> str:
    steps = validate_business_flow(flow)
    flow_id = str(flow["id"])
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", flow_id).strip("-.") or "flow"
    width = 760.0
    center_x = width / 2
    box_width = 600.0
    box_height = 68.0
    first_y = 142.0
    row_gap = 126.0
    start_y = 48.0
    end_y = first_y + (len(steps) - 1) * row_gap + 112.0
    height = end_y + 56.0
    marker_id = f"business-flow-arrow-{safe_id}"
    parts = [
        f'<svg viewBox="0 0 {svg_number(width)} {svg_number(height)}" width="{svg_number(width)}" height="{svg_number(height)}" '
        f'role="img" aria-label="{escape(str(flow.get("name", "业务流程")))}" data-diagram-type="business-process" '
        f'data-source-id="{escape(flow_id)}" data-flow-id="{escape(flow_id)}" data-layout-audit="pass">',
        f'<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#55657d"/></marker></defs>',
    ]
    first_top = first_y - box_height / 2
    parts.append(
        f'<path d="M {svg_number(center_x)} {svg_number(start_y + 21)} L {svg_number(center_x)} {svg_number(first_top)}" '
        f'fill="none" stroke="#55657d" stroke-width="2" marker-end="url(#{marker_id})"/>'
    )
    for index in range(1, len(steps)):
        previous_bottom = first_y + (index - 1) * row_gap + box_height / 2
        current_top = first_y + index * row_gap - box_height / 2
        parts.append(
            f'<path d="M {svg_number(center_x)} {svg_number(previous_bottom)} L {svg_number(center_x)} {svg_number(current_top)}" '
            f'fill="none" stroke="#55657d" stroke-width="2" marker-end="url(#{marker_id})"/>'
        )
    last_bottom = first_y + (len(steps) - 1) * row_gap + box_height / 2
    parts.append(
        f'<path d="M {svg_number(center_x)} {svg_number(last_bottom)} L {svg_number(center_x)} {svg_number(end_y - 21)}" '
        f'fill="none" stroke="#55657d" stroke-width="2" marker-end="url(#{marker_id})"/>'
    )
    parts.append(
        f'<rect x="{svg_number(center_x - 78)}" y="{svg_number(start_y - 21)}" width="156" height="42" rx="21" '
        'fill="#315efb" stroke="#2448c7" stroke-width="1.6"/>'
        f'{svg_text(center_x, start_y + 4, "业务流程开始", 12, "#fff", 12)}'
    )
    box_x = center_x - box_width / 2
    for index, step in enumerate(steps, start=1):
        y = first_y + (index - 1) * row_gap
        parts.append(
            f'<g class="business-flow-step" data-step-index="{index}"><rect x="{svg_number(box_x)}" '
            f'y="{svg_number(y - box_height / 2)}" width="{svg_number(box_width)}" height="{svg_number(box_height)}" '
            'rx="12" fill="#fff" stroke="#8291a8" stroke-width="1.5"/>'
            f'<circle cx="{svg_number(box_x + 38)}" cy="{svg_number(y)}" r="19" fill="#eef2ff" stroke="#9aaaf5"/>'
            f'<text x="{svg_number(box_x + 38)}" y="{svg_number(y + 5)}" text-anchor="middle" font-size="12" '
            f'font-weight="700" fill="#2448c7">{index}</text>{svg_text(center_x + 18, y + 4, step, 38, "#25324a", 13)}</g>'
        )
    parts.append(
        f'<rect x="{svg_number(center_x - 78)}" y="{svg_number(end_y - 21)}" width="156" height="42" rx="21" '
        'fill="#172033" stroke="#0c111d" stroke-width="1.6"/>'
        f'{svg_text(center_x, end_y + 4, "业务流程结束", 12, "#fff", 12)}</svg>'
    )
    return bind_business_svg("".join(parts), flow)


def retain_plantuml_source(path: Path, source: str) -> None:
    """Keep optional generated diagnostics without overwriting prior material."""
    expected = source.rstrip() + "\n"
    for directory in (path.parent.parent, path.parent):
        if directory.is_symlink():
            raise RenderError(f"诊断目录不能是符号链接，须在新的诊断工作区生成：{directory}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_text(encoding="utf-8") != expected:
            raise RenderError(f"已有诊断源码与当前事实不同，须保留原文件并在新的诊断工作区生成：{path}")
        return
    with path.open("x", encoding="utf-8") as handle:
        handle.write(expected)


def business_flow_diagram_html(
    flow: dict[str, Any],
    renderer: str,
    plantuml_runtime: PlantUMLRuntime | None,
    plantuml_source_dir: Path | None,
) -> str:
    flow_id = str(flow.get("id", "flow"))
    native_panel = diagram_panel(business_flow_svg(flow), "native", flow_id)
    if renderer == "native":
        return native_panel
    if plantuml_runtime is None:
        raise RenderError("PlantUML 渲染模式缺少本地运行时")
    source = compile_plantuml_business_flow(flow)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", flow_id).strip("-.") or "flow"
    if plantuml_source_dir is not None:
        retain_plantuml_source(plantuml_source_dir / f"{safe_id}.puml", source)
    plantuml_svg = render_plantuml_svg(source, flow, plantuml_runtime, "business-process")
    plantuml_panel = diagram_panel(plantuml_svg, "plantuml", flow_id)
    if renderer == "plantuml":
        return plantuml_panel
    return (
        '<div class="diagram-versions" data-renderer-mode="compare">'
        f'<details class="diagram-version" open><summary>PlantUML 业务流程图</summary>{plantuml_panel}</details>'
        f'<details class="diagram-version"><summary>原生流程基线</summary>{native_panel}</details>'
        "</div>"
    )


def scenario_diagram_html(
    detail: dict[str, Any],
    renderer: str,
    plantuml_runtime: PlantUMLRuntime | None,
    plantuml_source_dir: Path | None,
) -> str:
    scenario_id = str(detail.get("id", "scenario"))
    if renderer == "native":
        return diagram_panel(swimlane_svg(detail), "native", scenario_id)

    # The established deterministic audit remains mandatory while PlantUML is a candidate backend.
    audited_swimlane_layout(detail)
    if plantuml_runtime is None:
        raise RenderError("PlantUML 渲染模式缺少本地运行时")
    source = compile_plantuml_activity(detail)
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", scenario_id).strip("-.") or "scenario"
    if plantuml_source_dir is not None:
        retain_plantuml_source(plantuml_source_dir / f"{safe_id}.puml", source)
    plantuml_svg = render_plantuml_svg(source, detail, plantuml_runtime)
    plantuml_panel = diagram_panel(plantuml_svg, "plantuml", scenario_id)
    if renderer == "plantuml":
        return plantuml_panel

    native_panel = diagram_panel(swimlane_svg(detail), "native", scenario_id)
    return (
        '<div class="diagram-versions" data-renderer-mode="compare">'
        f'<details class="diagram-version" open><summary>PlantUML 主泳道图</summary>{plantuml_panel}</details>'
        f'<details class="diagram-version"><summary>原生核验基线</summary>{native_panel}</details>'
        "</div>"
    )


def render_scenarios(
    root: Path,
    project: str,
    renderer: str = "compare",
    plantuml_runtime: PlantUMLRuntime | None = None,
    keep_plantuml_source: bool = False,
) -> Path:
    plantuml_source_dir = root / "runtime" / "plantuml" if keep_plantuml_source else None
    rule_data = load_json(root / "memory" / "business-rules.json")
    business_flows = rule_data.get("business_flows", [])
    if not isinstance(business_flows, list):
        raise RenderError("memory/business-rules.json 的 business_flows 必须是数组")
    records = load_records(
        root,
        "scenarios/scenario-index.json",
        "scenarios",
        include=lambda item: item.get("scope", "in_scope") == "in_scope",
    )
    flow_cards = []
    scenario_cards = []
    nav = []
    for flow in business_flows:
        if not isinstance(flow, dict):
            raise RenderError("business_flows 包含非对象记录")
        flow_id = str(flow.get("id", ""))
        steps = validate_business_flow(flow)
        nav.append((flow_id, f"{flow_id} {flow.get('name', '')}"))
        scenario_chips = "".join(
            f'<span class="chip">{escape(str(value))}</span>' for value in flow.get("scenario_ids", [])
        ) or '<span class="muted">未关联场景</span>'
        flow_cards.append(
            f'<article class="card business-flow-card" id="{escape(flow_id)}"><h2>{escape(flow_id)} · {escape(str(flow.get("name", "")))}</h2>'
            f'<p class="muted">跨场景端到端业务流程，共 {len(steps)} 个业务步骤。</p><div class="meta">'
            f'<span class="badge">业务流程</span><span class="badge neutral">步骤：{len(steps)}</span>'
            f'<span class="badge neutral">渲染：{escape(renderer)}</span></div>'
            f'<div class="section"><h3>关联场景</h3><div class="chips">{scenario_chips}</div></div>'
            f'<div class="section"><h3>跨场景业务流程图</h3>{business_flow_diagram_html(flow, renderer, plantuml_runtime, plantuml_source_dir)}</div></article>'
        )
    for entry, detail in records:
        scenario_id = str(entry["id"])
        nav.append((scenario_id, f"{scenario_id} {entry.get('name', '')}"))
        flow_model = validate_swimlane_model(detail)
        diagram_html = scenario_diagram_html(
            detail,
            renderer,
            plantuml_runtime,
            plantuml_source_dir,
        )
        actors = "".join(
            f'<span class="chip">{escape(str(lane.get("name", lane.get("id", ""))))}</span>'
            for lane in flow_model.get("lanes", [])
        )
        flow_rows = []
        for step in detail.get("main_flow", []):
            rules = ", ".join(str(item) for item in step.get("rule_ids", []))
            flow_rows.append(
                "<tr>"
                f'<td>{escape(str(step.get("step", "?")))}</td><td>{escape(str(step.get("actor", "未知参与者")))}</td>'
                f'<td>{escape(str(step.get("action", "")))}</td><td>{escape(str(step.get("system_response") or "—"))}</td>'
                f'<td>{escape(rules or "—")}</td></tr>'
            )
        alternatives = []
        for alternative in detail.get("alternate_flows", []):
            steps = alternative.get("steps", [])
            alternatives.append(
                f'<div class="alt"><b>{escape(str(alternative.get("id", "备选路径")))}</b> · '
                f'{escape(str(alternative.get("condition", "")))}{list_html(steps)}</div>'
            )
        alternatives_html = "".join(alternatives) or '<span class="muted">未填写</span>'
        scenario_cards.append(
            f'<article class="card" id="{escape(scenario_id)}"><h2>{escape(scenario_id)} · {escape(str(detail.get("name", "")))}</h2>'
            f'<p class="muted">{escape(str(detail.get("summary", "")))}</p><div class="meta">'
            f'<span class="badge">{escape(str(entry.get("priority", "未分级")))}</span>'
            f'<span class="badge neutral">{escape(str(entry.get("scope", "未标记范围")))}</span>'
            f'<span class="badge neutral">UI: {escape(str(entry.get("ui_surface", "unknown")))}</span>'
            f'<span class="badge neutral">泳道：{len(flow_model.get("lanes", []))}</span>'
            f'<span class="badge neutral">渲染：{escape(renderer)}</span></div>'
            f'<div class="section"><h3>泳道参与方</h3><div class="chips">{actors}</div></div>'
            f'<div class="section"><h3>触发</h3><p>{escape(str(detail.get("trigger", "未填写")))}</p></div>'
            f'<div class="section"><h3>前置条件</h3>{list_html(detail.get("preconditions", []))}</div>'
            f'<div class="section"><h3>业务场景泳道图</h3>{diagram_html}'
            f'<p class="swimlane-note">纵向表示业务推进顺序，横向跨栏表示责任主体切换；PlantUML 模式由本地引擎生成并以内嵌 SVG 交付。</p></div>'
            '<div class="section"><h3>主流程明细</h3><table class="flow-table"><thead><tr><th>#</th><th>责任方</th><th>业务动作</th><th>系统反馈</th><th>业务规则</th></tr></thead>'
            f'<tbody>{"".join(flow_rows)}</tbody></table></div>'
            f'<div class="section"><h3>备选与异常路径</h3>{alternatives_html}</div>'
            f'<div class="section"><h3>后置状态</h3>{list_html(detail.get("postconditions", []))}</div>'
            f'<div class="section"><h3>验收点</h3>{list_html(detail.get("acceptance_points", []))}</div></article>'
        )
    output = root / "outputs" / "business-scenario-atlas.html"
    body = (
        '<div class="atlas-heading"><h2>跨场景业务流程</h2><p class="muted">从产品能力视角展示 FLOW-* 的端到端业务推进。</p></div>'
        + ("".join(flow_cards) or '<article class="card"><p class="muted">尚未定义跨场景业务流程。</p></article>')
        + '<div class="atlas-heading"><h2>详细业务场景泳道</h2><p class="muted">按责任主体拆解 SC-* 的正常、异常与结束路径。</p></div>'
        + "".join(scenario_cards)
    )
    write_text(output, page_shell("业务流程与场景泳道图集", project, nav, body, desktop=True))
    return output


def validate_sequence(detail: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sequence_id = str(detail.get("id", "未知时序"))
    participants = detail.get("participants", [])
    messages = detail.get("messages", [])
    if not isinstance(participants, list) or not participants:
        raise RenderError(f"{sequence_id} 缺少参与方")
    if not isinstance(messages, list) or not messages:
        raise RenderError(f"{sequence_id} 缺少消息")
    participant_ids = [str(item.get("id", "")).strip() for item in participants if isinstance(item, dict)]
    if len(participant_ids) != len(participants) or any(not value for value in participant_ids):
        raise RenderError(f"{sequence_id} 包含无效参与方")
    if len(participant_ids) != len(set(participant_ids)):
        raise RenderError(f"{sequence_id} 的参与方 ID 重复")
    known = set(participant_ids)
    for index, message in enumerate(messages, start=1):
        if not isinstance(message, dict):
            raise RenderError(f"{sequence_id} 的第 {index} 条消息无效")
        source = str(message.get("from", "")).strip()
        target = str(message.get("to", "")).strip()
        if source not in known or target not in known:
            raise RenderError(f"{sequence_id} 的第 {index} 条消息端点不存在：{source or '空'} -> {target or '空'}")
        if not str(message.get("message", "")).strip():
            raise RenderError(f"{sequence_id} 的第 {index} 条消息为空")
    return participants, messages


def sequence_svg(detail: dict[str, Any]) -> str:
    participants, messages = validate_sequence(detail)
    sequence_id = str(detail.get("id", "sequence"))
    marker_id = "sequence-arrow-" + re.sub(r"[^A-Za-z0-9_-]", "-", sequence_id)
    spacing, margin, box_width = 260, 140, 200
    xs = {str(item["id"]): margin + index * spacing for index, item in enumerate(participants)}
    names = [wrap_label(item.get("name") or item["id"], box_width - 24) for item in participants]
    header_height = max(len(lines) for lines in names) * 18 + 24
    width = max(760, max(xs.values()) + 260)
    cursor = 18 + header_height + 24
    message_parts = []
    type_labels = {"request": "请求", "response": "响应", "event": "事件", "external_call": "外部调用",
                   "error": "错误", "timeout": "超时", "compensation": "补偿"}
    for index, message in enumerate(messages):
        source, target = str(message["from"]), str(message["to"])
        x1, x2 = xs[source], xs[target]
        msg_type = str(message.get("type", "message"))
        label_bits = [f"{message.get('step', index + 1)}. [{type_labels.get(msg_type, msg_type)}]"]
        if message.get("condition"):
            label_bits.append(f"[条件：{message['condition']}]")
        if message.get("api_id"):
            label_bits.append(f"[{message['api_id']}]")
        label_bits.append(str(message["message"]))
        label = " ".join(label_bits)
        available = 220 if x1 == x2 else min(520, abs(x2 - x1) - 28)
        lines = wrap_label(label, available, 12)
        y = cursor + len(lines) * 18 + 8
        label_x = x1 + 112 if x1 == x2 else (x1 + x2) / 2
        color = {"event": "#7a4cc2", "response": "#11865b", "error": "#c9364f", "timeout": "#b76e00"}.get(msg_type, "#475467")
        message_parts.append(f'<g data-message-index="{index + 1}" data-message-type="{escape(msg_type, quote=True)}">'
                             f'<title>{escape(label)}</title>')
        message_parts.append(text_lines(lines, label_x, cursor + 14, size=12, color=color))
        dashed = ' stroke-dasharray="6 4"' if msg_type == "response" else ""
        if x1 == x2:
            message_parts.append(f'<path d="M {x1} {y} h 84 v 30 h -84" fill="none" stroke="{color}" stroke-width="1.7"{dashed} marker-end="url(#{marker_id})"/>')
        else:
            message_parts.append(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{color}" stroke-width="1.7"{dashed} marker-end="url(#{marker_id})"/>')
        message_parts.append("</g>")
        cursor = y + (30 if x1 == x2 else 0) + 26
    height = cursor + 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
             f'aria-label="{escape(str(detail.get("name", "交互时序")))}" data-diagram-type="sequence" data-renderer="native" '
             f'data-source-id="{escape(sequence_id)}" data-sequence-id="{escape(sequence_id)}" '
             f'data-sequence-sha256="{fact_fingerprint(detail)}" data-layout-audit="pass">',
             f'<defs><marker id="{marker_id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#475467"/></marker></defs>']
    for participant, lines in zip(participants, names):
        x = xs[str(participant["id"])]
        parts.append(f'<rect x="{x - box_width / 2}" y="18" width="{box_width}" height="{header_height}" rx="9" fill="#eef2ff" stroke="#9aaaf5"/>')
        parts.append(text_lines(lines, x, 18 + 27))
        parts.append(f'<line x1="{x}" y1="{18 + header_height}" x2="{x}" y2="{height - 20}" stroke="#b8c2d6" stroke-dasharray="5 5"/>')
    return "".join([*parts, *message_parts, "</svg>"])


def render_sequences(
    root: Path,
    project: str,
    renderer: str = "native",
) -> Path:
    if renderer != "native":
        raise RenderError("时序图只使用原生 SVG，不使用 PlantUML")
    records = load_records(root, "sequences/sequence-index.json", "sequences")
    cards = []
    nav = []
    for entry, detail in records:
        sequence_id = str(entry["id"])
        nav.append((sequence_id, f"{sequence_id} {entry.get('name', '')}"))
        validate_sequence(detail)
        diagram_html = diagram_panel(sequence_svg(detail), "native", sequence_id)
        rows = []
        for message in detail.get("messages", []):
            rows.append(
                "<tr>"
                f'<td>{escape(str(message.get("step", "")))}</td><td>{escape(str(message.get("from", "")))}</td>'
                f'<td>{escape(str(message.get("to", "")))}</td><td>{escape(str(message.get("message", "")))}</td>'
                f'<td>{escape(str(message.get("type", "")))}</td><td>{escape(str(message.get("condition") or "—"))}</td><td>{escape(str(message.get("api_id") or "—"))}</td>'
                "</tr>"
            )
        cards.append(
            f'<article class="card" id="{escape(sequence_id)}"><h2>{escape(sequence_id)} · {escape(str(detail.get("name", "")))}</h2>'
            f'<div class="meta"><span class="badge">场景 {escape(str(detail.get("scenario_id", "未关联")))}</span>'
            f'<span class="badge neutral">{len(detail.get("participants", []))} 个参与方</span>'
            f'<span class="badge neutral">{len(detail.get("messages", []))} 条消息</span>'
            f'<span class="badge neutral">渲染：{escape(renderer)}</span></div>'
            '<div class="legend"><span><i class="dot" style="background:#475467"></i>请求/消息</span><span><i class="dot" style="background:#11865b"></i>响应</span><span><i class="dot" style="background:#7a4cc2"></i>事件</span><span><i class="dot" style="background:#c9364f"></i>错误</span></div>'
            f'{diagram_html}'
            '<table class="message-table"><thead><tr><th>#</th><th>发送方</th><th>接收方</th><th>消息</th><th>类型</th><th>条件</th><th>接口</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table><div class="section"><h3>说明</h3>{list_html(detail.get("notes", []))}</div></article>'
        )
    output = root / "outputs" / "interaction-sequence-atlas.html"
    write_text(output, page_shell("交互时序图集", project, nav, "".join(cards), desktop=True))
    return output


def render_page_structure(root: Path, project: str) -> Path:
    problems = page_errors(root)
    if problems:
        raise RenderError("页面结构无效：" + "；".join(problems))
    pages = load_json(root / "planning/page-index.json")["pages"]
    svg = page_structure_svg(pages, project)
    ids = ",".join(sorted(page["id"] for page in pages))
    svg = svg.replace("<svg", f'<svg data-page-ids="{escape(ids, quote=True)}" data-page-structure-sha256="{page_structure_fingerprint(root)}"', 1)
    body = '<article class="card" id="page-structure"><h2>页面结构图</h2>'
    body += '<p class="muted">连线表示菜单层级与页面归属，不表示业务流程或交互先后。无菜单入口的页面直接归根。</p>'
    body += diagram_panel(svg, "native", "page-structure") + '</article>'
    nav = [("page-structure", "页面结构总览")]
    for page in pages:
        page_id = page["id"]
        nav.append((page_id, f"{page_id} · {page['name']}"))
        body += (f'<article class="card" id="{escape(page_id)}"><h2>{escape(page_id)} · {escape(page["name"])}</h2>'
                 f'<p>菜单：{escape(" / ".join(page.get("menu_path", [])) or "无菜单入口")}</p>'
                 f'<p>来源场景：{escape(", ".join(page.get("scenario_ids", [])))}</p>'
                 f'<p>交互依据：{escape(", ".join(page.get("sequence_ids", [])))}</p>'
                 f'<p>功能关联：{escape(", ".join(page.get("function_ids", [])) or "S7 最终回填")}</p></article>')
    output = root / PAGE_STRUCTURE
    write_text(output, page_shell("页面结构图", project, nav, body))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="生成业务场景泳道与交互时序 HTML 图集")
    parser.add_argument("project_dir")
    parser.add_argument("--kind", choices=["all", "scenarios", "sequences", "pages"], default="all")
    parser.add_argument(
        "--scenario-renderer",
        choices=["native", "plantuml", "compare"],
        default="compare",
        help="业务流程和场景泳道渲染后端；默认 compare，以 PlantUML 为主图并保留原生核验基线",
    )
    parser.add_argument(
        "--sequence-renderer",
        choices=["native"],
        default="native",
        help="交互时序图固定使用原生 SVG；不接受 PlantUML 或 compare",
    )
    parser.add_argument("--plantuml-command", help="本地 plantuml 可执行命令或绝对路径")
    parser.add_argument("--plantuml-jar", help="本地 PlantUML JAR 路径")
    parser.add_argument("--java-command", help="运行 PlantUML JAR 的本地 Java 可执行命令或绝对路径")
    parser.add_argument("--plantuml-timeout", type=int, default=45, help="单张 PlantUML 图的最长渲染秒数")
    parser.add_argument("--keep-plantuml-source", action="store_true", help="诊断时按需将 FLOW/SC 源码保存到 runtime/plantuml；默认只在内存中渲染，不新增 .puml 文件")
    args = parser.parse_args()
    root = Path(args.project_dir).expanduser().resolve()
    try:
        if args.plantuml_timeout <= 0:
            raise RenderError("--plantuml-timeout 必须是正整数")
        state = load_json(root / "runtime" / "loop-state.json")
        project = state.get("project", {}).get("name", root.name)
        from diagram_policy import requested
        outputs = []
        business_requested = requested(root, 'business_flow')
        sequence_requested = requested(root, 'interaction_sequence')
        if not business_requested and args.kind in {'all', 'scenarios'}:
            print('未请求业务流程／泳道图：跳过图形渲染，结构化事实仍须核验')
        if not sequence_requested and args.kind in {'all', 'sequences'}:
            print('未请求时序图：跳过图形渲染，结构化消息仍须核验')
        plantuml_runtime = None
        needs_plantuml = (
            business_requested and args.kind in {"all", "scenarios"} and args.scenario_renderer in {"plantuml", "compare"}
        )
        if needs_plantuml:
            plantuml_runtime = resolve_plantuml_runtime(
                command=args.plantuml_command,
                jar=args.plantuml_jar,
                java_command=args.java_command,
                timeout_seconds=args.plantuml_timeout,
            )
        if business_requested and args.kind in {"all", "scenarios"}:
            outputs.append(render_scenarios(root, project, args.scenario_renderer, plantuml_runtime, args.keep_plantuml_source))
        if sequence_requested and args.kind in {"all", "sequences"}:
            outputs.append(render_sequences(root, project, args.sequence_renderer))
        if args.kind in {"all", "pages"}:
            outputs.append(render_page_structure(root, project))
    except (RenderError, PlantUMLRenderError, ValueError, OSError, KeyError, TypeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    for output in outputs:
        print(f"已生成：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
