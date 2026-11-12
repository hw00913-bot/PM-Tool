from __future__ import annotations

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from render_atlases import BASE_CSS, PAGE_SCRIPT, diagram_panel, page_shell
from atlas_reading import desktop_html_errors, READING_LAYOUT


class Elements(HTMLParser):
    def __init__(self, source: str):
        super().__init__()
        self.elements: list[tuple[str, dict]] = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class AtlasReadingTest(unittest.TestCase):
    def test_desktop_shell_full_width_and_natural_size(self) -> None:
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3000 900" width="3000" height="900"><text>完整标签</text></svg>'
        panel = diagram_panel(svg, "native", "SEQ-001")
        html = page_shell("时序图", "隔离验证", [("SEQ-001", "测试时序")], panel, desktop=True)
        elements = Elements(html).elements
        self.assertIn(("body", {"class": "atlas-desktop", "data-reading-layout": READING_LAYOUT}), elements)
        directory = next(attrs for tag, attrs in elements if tag == "details")
        self.assertNotIn("open", directory)
        self.assertEqual("atlas-directory", directory["class"])
        metadata = next(attrs for _, attrs in elements if "data-diagram-panel" in attrs)
        self.assertEqual("3000", metadata["data-natural-width"])
        self.assertEqual("900", metadata["data-natural-height"])
        self.assertEqual("--diagram-natural-width:3000px", metadata["style"])
        self.assertIn('.atlas-desktop .layout{max-width:none;width:100%', BASE_CSS)
        self.assertIn('.atlas-desktop .diagram svg{width:100%;min-width:var(--diagram-natural-width);max-width:none;height:auto}', BASE_CSS)
        self.assertIn(".diagram-panel:fullscreen .diagram-toolbar", BASE_CSS)
        self.assertIn(svg, html, "阅读壳不改 SVG 几何或标签")
        self.assertIn('class="atlas-document"', page_shell("页面结构", "测试", [], panel))
        self.assertNotIn('@media(max-width:800px){.layout', BASE_CSS)
        self.assertEqual([], desktop_html_errors(html))
        self.assertTrue(desktop_html_errors(html.replace('data-reading-layout="desktop-wide@1"', 'data-reading-layout="old"')))
        self.assertTrue(desktop_html_errors(html.replace('<details class="atlas-directory">', '<details open class="atlas-directory">')))
        self.assertTrue(desktop_html_errors(html.replace('data-natural-width="3000"', 'data-natural-width="nan"')))
        self.assertTrue(desktop_html_errors(html.replace('data-diagram-action="fit"', 'data-diagram-action="missing"')))

    @unittest.skipUnless(shutil.which("node"), "Node unavailable; interaction logic test requires a local JavaScript runtime")
    def test_zoom_fit_resize_and_fullscreen_logic_without_browser(self) -> None:
        harness = r'''
const assert = require("node:assert/strict");
const vm = require("node:vm");
const script = JSON.parse(require("node:fs").readFileSync(0, "utf8")).script;
const handlers = {}, windowHandlers = {};
const canvas = {clientWidth: 1400};
const status = {textContent: ""};
const svg = {style: {}, getBoundingClientRect() {return {width: Math.max(canvas.clientWidth - 24, Number(panel.dataset.naturalWidth))};}};
let fullscreenTarget = null;
const panel = {
  dataset: {naturalWidth: "640", naturalHeight: "480", viewMode: "readable"},
  querySelector(selector) {return {svg, ".diagram": canvas, ".diagram-status": status}[selector];},
  requestFullscreen() {fullscreenTarget = this; document.fullscreenElement = this; return Promise.resolve();}
};
const document = {
  fullscreenElement: null,
  addEventListener(name, callback) {handlers[name] = callback;},
  querySelectorAll() {return panel.dataset.viewMode === "fit" ? [panel] : [];},
  exitFullscreen() {document.fullscreenElement = null; return Promise.resolve();}
};
const context = {document, window: {addEventListener(name, cb) {windowHandlers[name] = cb;}},
  getComputedStyle() {return {paddingLeft: "12px", paddingRight: "12px"};}};
vm.runInNewContext(script, context);
function click(action) {
  const button = {dataset: {diagramAction: action}, closest() {return panel;}};
  handlers.click({target: {closest() {return button;}}});
}
for (const viewport of [1440, 1920, 2560]) {
  canvas.clientWidth = viewport - 96;
  for (const naturalWidth of [640, 1800, 3000]) {
    panel.dataset.naturalWidth = String(naturalWidth);
    panel.dataset.naturalHeight = "900";
    click("readable");
    const readingScale = Math.max(canvas.clientWidth - 24, naturalWidth) / naturalWidth;
    click("zoom-in");
    assert.equal(svg.style.width, `${Math.round(naturalWidth * readingScale * 1.2)}px`);
    click("fit");
    assert.equal(svg.style.width, `${canvas.clientWidth - 24}px`);
    assert.equal(svg.style.height, `${Math.round(900 * (canvas.clientWidth - 24) / naturalWidth)}px`);
    assert.equal(svg.style.minWidth, "0", "fit must be allowed to shrink a wide SVG");
    canvas.clientWidth += 64;
    windowHandlers.resize();
    assert.equal(svg.style.width, `${canvas.clientWidth - 24}px`);
    click("actual");
    assert.equal(svg.style.width, `${naturalWidth}px`);
    click("zoom-out");
    assert.equal(svg.style.width, `${Math.round(naturalWidth / 1.2)}px`);
    const manualWidth = svg.style.width;
    windowHandlers.resize();
    assert.equal(svg.style.width, manualWidth, "manual zoom survives resize");
  }
}
click("fit");
click("fullscreen");
assert.equal(fullscreenTarget, panel, "fullscreen includes the toolbar, not only the canvas");
canvas.clientWidth = 3000;
handlers.fullscreenchange();
assert.equal(svg.style.width, "2976px");
click("fullscreen");
assert.equal(document.fullscreenElement, null);
click("readable");
assert.equal(panel.dataset.viewMode, "readable");
assert.equal(svg.style.minWidth, "");
assert.equal(svg.style.width, "");
panel.requestFullscreen = undefined;
click("fullscreen");
assert.match(status.textContent, /不支持全屏/);
process.stdout.write("desktop reading controls: pass\n");
'''
        result = subprocess.run([shutil.which("node"), "-e", harness], input=json.dumps({"script": PAGE_SCRIPT}), text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("pass", result.stdout)


if __name__ == "__main__":
    unittest.main()
