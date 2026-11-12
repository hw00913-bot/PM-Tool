"""Offline SVG helpers for sequence labels and page hierarchy (no external engine)."""

from __future__ import annotations

from html import escape
import re
from typing import Any
import unicodedata


def wrap_label(value: Any, max_width: float, font_size: int = 13) -> list[str]:
    """Wrap complete Unicode text conservatively; never silently truncate facts."""
    lines: list[str] = []

    def char_width(char: str) -> float:
        return font_size if unicodedata.east_asian_width(char) in {"W", "F"} else font_size * 0.65

    for paragraph in str(value).splitlines() or [""]:
        line, width = "", 0.0
        # Keep short identifiers such as API-001 intact; split only overlong tokens.
        for token in re.findall(r"[A-Za-z0-9_./:-]+|.", paragraph):
            token_width = sum(char_width(char) for char in token)
            fragments = list(token) if token_width > max_width else [token]
            for fragment in fragments:
                size = sum(char_width(char) for char in fragment)
                if line and width + size > max_width:
                    lines.append(line)
                    line, width = "", 0.0
                line += fragment
                width += size
        lines.append(line)
    return lines


def text_lines(lines: list[str], x: float, first_y: float, *, size: int = 13, color: str = "#243b77") -> str:
    return (f'<text x="{x}" y="{first_y}" text-anchor="middle" font-size="{size}" fill="{color}" '
            'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,PingFang SC,Microsoft YaHei,sans-serif">'
            + "".join(f'<tspan x="{x}" y="{first_y + index * 18}">{escape(line)}</tspan>'
                      for index, line in enumerate(lines)) + "</text>")


def page_structure_svg(pages: list[dict[str, Any]], project: str) -> str:
    root: dict[str, Any] = {"label": project, "menus": {}, "pages": []}
    for page in pages:
        node = root
        for label in page.get("menu_path", []):
            node = node["menus"].setdefault(label, {"label": label, "menus": {}, "pages": []})
        node["pages"].append({"label": f"{page['id']} · {page['name']}", "page_id": page["id"], "menus": {}, "pages": []})
    if not pages:
        root["pages"].append({"label": "本期没有 UI 页面", "menus": {}, "pages": []})
    box_width, column_gap, row_gap = 252, 64, 24
    nodes: list[dict[str, Any]] = []

    def measure(node: dict[str, Any]) -> float:
        node["children"] = [*node["menus"].values(), *node["pages"]]
        node["lines"] = wrap_label(node["label"], box_width - 32)
        node["height"] = len(node["lines"]) * 18 + 24
        children_height = sum(measure(child) for child in node["children"])
        children_height += max(0, len(node["children"]) - 1) * row_gap
        node["subtree_height"] = max(node["height"], children_height)
        return node["subtree_height"]

    def place(node: dict[str, Any], top: float, depth: int) -> None:
        node["x"] = 24 + depth * (box_width + column_gap)
        node["y"] = top + (node["subtree_height"] - node["height"]) / 2
        nodes.append(node)
        child_top = top
        for child in node["children"]:
            place(child, child_top, depth + 1)
            child_top += child["subtree_height"] + row_gap

    height = measure(root) + 48
    place(root, 24, 0)
    width = max(node["x"] for node in nodes) + box_width + 24
    for index, node in enumerate(nodes):
        for other in nodes[index + 1:]:
            if (node["x"] < other["x"] + box_width and other["x"] < node["x"] + box_width
                    and node["y"] < other["y"] + other["height"] and other["y"] < node["y"] + node["height"]):
                raise ValueError("页面结构节点重叠，拒绝输出")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
             'role="img" aria-label="页面结构图" data-diagram-type="page-structure" data-renderer="native" data-layout-audit="pass">']
    for node in nodes:
        children = node["children"]
        if not children:
            continue
        x, center = node["x"] + box_width, node["y"] + node["height"] / 2
        junction = x + column_gap / 2
        child_centers = [child["y"] + child["height"] / 2 for child in children]
        parts.append(f'<path d="M {x} {center} H {junction} M {junction} {min(center, *child_centers)} V {max(center, *child_centers)}" '
                     'fill="none" stroke="#8291a8" stroke-width="1.5"/>')
        for child, y in zip(children, child_centers):
            parts.append(f'<path d="M {junction} {y} H {child["x"]}" fill="none" stroke="#8291a8" stroke-width="1.5"/>')
    for node in nodes:
        page_id = escape(str(node.get("page_id", "")), quote=True)
        parts.append(f'<g data-page-node="{page_id}"><rect x="{node["x"]}" y="{node["y"]}" width="{box_width}" '
                     f'height="{node["height"]}" rx="9" fill="{"#eef2ff" if page_id else "#f2f4f7"}" stroke="#9aaaf5"/>')
        parts.append(text_lines(node["lines"], node["x"] + box_width / 2, node["y"] + 27))
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)
