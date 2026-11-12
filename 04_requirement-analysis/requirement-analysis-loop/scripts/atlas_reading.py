"""Detect stale atlas reading shells; metadata checks do not prove visual quality."""

from html.parser import HTMLParser
from pathlib import Path


READING_LAYOUT = "desktop-wide@1"


class ReadingElements(HTMLParser):
    def __init__(self, source: str):
        super().__init__()
        self.elements: list[tuple[str, dict]] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def desktop_html_errors(html: str) -> list[str]:
    elements = ReadingElements(html).elements
    body = next((attrs for tag, attrs in elements if tag == "body"), {})
    directories = [attrs for tag, attrs in elements if tag == "details" and "atlas-directory" in attrs.get("class", "").split()]
    panels = [attrs for tag, attrs in elements if "data-diagram-panel" in attrs]
    svg_count = sum(tag == "svg" for tag, _ in elements)
    actions = {attrs.get("data-diagram-action") for _, attrs in elements}
    errors = []
    if body.get("data-reading-layout") != READING_LAYOUT or "atlas-desktop" not in body.get("class", "").split():
        errors.append("图集尚未使用桌面宽屏阅读壳，请重新生成")
    if len(directories) != 1 or "open" in directories[0]:
        errors.append("图集须有一个默认折叠的目录，不能占用固定侧栏宽度")
    if not panels or len(panels) != svg_count:
        errors.append("每张 SVG 须位于可缩放的图形面板中")
    for panel in panels:
        try:
            if not all(0 < float(panel[key]) < float("inf") for key in ("data-natural-width", "data-natural-height")):
                raise ValueError("invalid size")
        except (KeyError, TypeError, ValueError):
            errors.append("图形面板缺少有效自然尺寸，无法保持可读缩放")
    if not {"fit", "actual", "readable", "zoom-in", "zoom-out", "fullscreen", "download"}.issubset(actions):
        errors.append("图集缺少完整阅读控件")
    return errors


def desktop_atlas_errors(root: Path, relative: str) -> list[str]:
    try:
        return [f"{relative}：{error}" for error in desktop_html_errors((root / relative).read_text(encoding="utf-8"))]
    except OSError as exc:
        return [f"缺少桌面宽屏图集：{exc}"]
