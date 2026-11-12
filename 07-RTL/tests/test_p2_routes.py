import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from loop import browser, feature_map
from loop.delivery import manifest


def _map(*entries):
    rows = "".join(f"| FUNC-{index} | A | {entry} | - | TC-A |\n" for index, entry in enumerate(entries))
    return "| 功能 | 场景 | 入口 | 契约 | 必跑用例 |\n| --- | --- | --- | --- | --- |\n" + rows


class RouteEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        project = self.root / "project"
        (project / "docs").mkdir(parents=True)
        self.map = project / "docs/feature-map.md"
        self.map.write_text(_map("index.html#/workbench", "index.html#/workbench"), encoding="utf-8")
        prototype = project / "prototype"
        prototype.mkdir()
        self.entry = prototype / "index.html"
        self.entry.write_text("<h1>Workbench</h1>", encoding="utf-8")
        self.manifest = manifest(prototype)
        self.run = {"baseline": {"feature_route_checks": True}, "project_root": str(project)}

    def _result(self, route="index.html#/workbench", *, status="pass", url=None, identity=None):
        return {route: {
            "status": status, "url": url or self.entry.as_uri() + "#/workbench",
            "identity": identity or {"kind": "route-marker", "value": "/workbench"}, "errors": [],
        }}

    def _proof(self, identifier, routes, *, route_map=None, asset_digest=None):
        feature_routes = {
            "map_sha256": hashlib.sha256((route_map or self.map).read_bytes()).hexdigest(), "routes": routes,
        }
        asset_digest = asset_digest or browser.asset_digest(self.entry.parent)
        evidence = self.root / f"{identifier}.json"
        evidence.write_text(json.dumps({"feature_routes": feature_routes, "asset_digest": asset_digest}), encoding="utf-8")
        return {"id": identifier}, {
            "feature_routes": feature_routes, "asset_digest": asset_digest, "evidence": str(evidence),
            "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        }

    def test_gate_checks_map_asset_route_identity_and_latest_actual_result(self):
        first = self._proof("initial", self._result())
        self.assertEqual(feature_map.require_live_routes(self.run, self.entry, self.manifest, [first])["routes"],
                         ["index.html#/workbench"])
        failed = self._proof("failed", self._result(status="fail"))
        with self.assertRaisesRegex(ValueError, "实际路由校验失败"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [first, failed])
        redirected = self._proof("fallback", self._result(url=self.entry.as_uri() + "#/fallback"))
        with self.assertRaisesRegex(ValueError, "实际路由校验失败"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [redirected])
        wrong_assets = self._proof("wrong-assets", self._result(), asset_digest="wrong-assets")
        with self.assertRaisesRegex(ValueError, "缺少当前功能地图"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [wrong_assets])
        self.map.write_text(self.map.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "缺少当前功能地图"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [first])

    def test_route_proof_checks_archived_report_and_preserves_legacy_rounds(self):
        row, context = self._proof("edited", self._result())
        Path(context["evidence"]).write_text("different bytes", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "报告缺失或发生变化"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [(row, context)])
        self.run["baseline"] = {}
        self.assertIsNone(feature_map.require_live_routes(self.run, self.entry, self.manifest, []))

    def test_gate_rejects_an_identity_that_does_not_identify_the_expected_route(self):
        href = self.entry.as_uri() + "#/workbench"
        bad_identities = (
            {"kind": "document", "value": "工作台"},
            {"kind": "route-marker", "value": "/other"},
            {"kind": "document-anchor", "value": "/workbench"},
            {"kind": "navigation-heading", "value": "工作台布局", "label": "错误标签", "href": href},
            {"kind": "navigation-heading", "value": "工作台布局", "label": "工作台", "href": href + "-other"},
        )
        for index, identity in enumerate(bad_identities):
            with self.subTest(identity=identity):
                report = self._proof(f"wrong-identity-{index}", self._result(identity=identity))
                with self.assertRaisesRegex(ValueError, "实际路由校验失败"):
                    feature_map.require_live_routes(self.run, self.entry, self.manifest, [report])

        current_navigation = {"kind": "navigation-heading", "value": "工作台布局", "label": "工作台", "href": href}
        report = self._proof("confirmed-navigation", self._result(identity=current_navigation))
        self.assertEqual(
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [report])["evidence_id"],
            "confirmed-navigation",
        )

        self.map.write_text(_map("index.html#hint"), encoding="utf-8")
        anchor_url = self.entry.as_uri() + "#hint"
        route = self._result("index.html#hint", url=anchor_url,
                             identity={"kind": "document-anchor", "value": "other"})
        report = self._proof("wrong-anchor", route)
        with self.assertRaisesRegex(ValueError, "实际路由校验失败"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [report])

    def test_gate_checks_references_of_the_same_map_snapshot_as_the_route_report(self):
        self.run["baseline"]["mandatory_cases"] = ["TC-A"]
        tasks = {"A": {"edges": []}}
        for changed, error in (
            (_map("index.html#/workbench").replace("| A |", "| unknown |"), "场景不存在"),
            (_map("index.html#/workbench").replace("TC-A", "TC-unfrozen"), "缺少本轮必跑用例"),
        ):
            self.map.write_text(changed, encoding="utf-8")
            report = self._proof("route-still-renders", self._result())
            # A successful runtime probe does not validate the rest of the map.
            self.assertEqual(feature_map.require_live_routes(self.run, self.entry, self.manifest, [report])["routes"],
                             ["index.html#/workbench"])
            with self.assertRaisesRegex(ValueError, error):
                feature_map.require_live_routes(self.run, self.entry, self.manifest, [report], task_specs=tasks)

    def test_feature_entry_gate_binds_each_feature_to_the_observed_scene_and_entry(self):
        self.run["baseline"]["feature_entry_checks"] = True
        route = "index.html#/workbench"
        href = self.entry.as_uri() + "#/workbench"
        results = self._result()
        legacy_report = self._proof("route-only", results)
        with self.assertRaisesRegex(ValueError, "功能点实际入口校验失败：FUNC-0, FUNC-1"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [legacy_report])

        features = {
            feature: {
                "status": "pass", "entry": route, "url": href, "errors": [],
                "identity": {"kind": "feature-marker", "feature_id": feature, "scene_id": "A"},
            }
            for feature in ("FUNC-0", "FUNC-1")
        }
        results[route]["features"] = features
        valid = self._proof("feature-report", results)
        self.assertEqual(
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [valid])["evidence_id"],
            "feature-report",
        )
        for key, value in (("feature_id", "FUNC-0"), ("scene_id", "other")):
            with self.subTest(key=key):
                features["FUNC-1"]["identity"][key] = value
                invalid = self._proof(f"wrong-{key}", results)
                with self.assertRaisesRegex(ValueError, "功能点实际入口校验失败：FUNC-1"):
                    feature_map.require_live_routes(self.run, self.entry, self.manifest, [valid, invalid])
                features["FUNC-1"]["identity"].update({"feature_id": "FUNC-1", "scene_id": "A"})
        features["FUNC-1"]["entry"] = "index.html"
        wrong_entry = self._proof("wrong-entry", results)
        with self.assertRaisesRegex(ValueError, "功能点实际入口校验失败：FUNC-1"):
            feature_map.require_live_routes(self.run, self.entry, self.manifest, [valid, wrong_entry])

    def test_route_plan_dedupes_entries_and_rejects_nonlocal_assets(self):
        self.assertEqual(
            feature_map.route_plan(_map("index.html#/workbench", "index.html#/workbench", "help/index.html#tip").encode()),
            {"index.html#/workbench": ["FUNC-0", "FUNC-1"], "help/index.html#tip": ["FUNC-2"]},
        )
        for entry in ("https://example.test/index.html", "%2e%2e/index.html", "umi.js", "index.html?q=1"):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                feature_map.route_plan(_map(entry).encode())

    def test_react_hash_root_accepts_its_same_file_default_with_matching_visible_feature(self):
        root = self.entry.parent
        bare = self.entry.as_uri()
        mapped = "index.html#/"

        def check(before, after, features=None, *, route=mapped, feature="FUNC-0"):
            location = {"before": before, "after": after}
            if features is not None:
                location["root_features"] = features
            target = self.entry.as_uri() + route[route.index("#"):]
            return feature_map._assertion_at_route(root, route, target, location, react_root_feature=feature)

        self.assertTrue(check(bare + "#/", bare + "#/"))
        self.assertTrue(check(bare, bare, ["FUNC-0"]))
        self.assertFalse(check(bare, bare))
        self.assertFalse(check(bare, bare, ["FUNC-1"]))
        self.assertFalse(check(bare, bare, ["FUNC-0"], feature=None))
        self.assertFalse(check(bare, bare + "#/", ["FUNC-0"]))
        self.assertFalse(check(bare + "#/other", bare + "#/other", ["FUNC-0"]))
        self.assertFalse(check(bare, bare, ["FUNC-0"], route="index.html#/other"))
        other = (root / "other/index.html").as_uri()
        self.assertFalse(check(other, other, ["FUNC-0"]))


@unittest.skipUnless(os.environ.get("RTL_TEST_BROWSER") == "1", "opt in with RTL_TEST_BROWSER=1")
class BrowserRouteTests(unittest.TestCase):
    def test_misassigned_function_fails_even_when_all_routes_and_cases_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            output = root / "output"
            output.mkdir()
            entry = output / "index.html"
            entry.write_text("""<!doctype html><meta charset="utf-8">
<main><h1></h1></main>
<script>
const entry = location.hash === '#/layout'
  ? {path: '/layout', scene: 'SCN-C', features: 'FUNC-C01', heading: '布局偏好'}
  : {path: '/outbound', scene: 'SCN-B', features: 'FUNC-B01 FUNC-B02', heading: '外呼任务'};
const main = document.querySelector('main');
main.dataset.rtlRoute = entry.path;
main.dataset.rtlScene = entry.scene;
main.querySelector('h1').dataset.rtlFeature = entry.features;
main.querySelector('h1').textContent = entry.heading;
</script>""", encoding="utf-8")
            suite = root / "suite.json"
            suite.write_text(json.dumps({
                "suite_revision": "s", "fixture_revision": "f",
                "cases": [{"id": "TC-B02", "steps": [{"op": "visible", "selector": "h1"}]}],
            }), encoding="utf-8")
            map_file = root / "feature-map.md"
            map_text = """| 功能 | 场景 | 入口 | 契约 | 必跑用例 |
| --- | --- | --- | --- | --- |
| FUNC-B01 | SCN-B | index.html#/outbound | - | TC-B01 |
| FUNC-B02 | SCN-B | index.html#/outbound | - | TC-B02 |
| FUNC-C01 | SCN-C | index.html#/layout | - | TC-C01 |
"""
            map_file.write_text(map_text, encoding="utf-8")
            report = root / "correct.json"
            self.assertEqual(browser.run(entry, suite, report, feature_map=map_file, feature_entries=True), 0)
            good = json.loads(report.read_text())["feature_routes"]["routes"]
            self.assertEqual(good["index.html#/outbound"]["features"]["FUNC-B02"]["identity"],
                             {"kind": "feature-marker", "feature_id": "FUNC-B02", "scene_id": "SCN-B"})

            map_file.write_text(map_text.replace(
                "FUNC-B02 | SCN-B | index.html#/outbound", "FUNC-B02 | SCN-B | index.html#/layout",
            ), encoding="utf-8")
            rejected = root / "wrong-function.json"
            self.assertEqual(browser.run(entry, suite, rejected, feature_map=map_file, feature_entries=True), 1)
            data = json.loads(rejected.read_text())
            self.assertEqual(data["cases"]["TC-B02"]["status"], "pass")
            routes = data["feature_routes"]["routes"]
            self.assertTrue(all(result["status"] == "pass" for result in routes.values()))
            self.assertEqual(routes["index.html#/layout"]["features"]["FUNC-B02"]["status"], "fail")
            self.assertEqual(routes["index.html#/layout"]["features"]["FUNC-C01"]["status"], "pass")

    def test_hash_anchor_navigation_and_same_hash_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            output = root / "output"
            output.mkdir()
            entry = output / "index.html"
            entry.write_text("""<!doctype html><meta charset="utf-8">
<nav><a href="#/workbench">工作台</a><a href="#/broken">未实现</a></nav>
<main><h1>工作台布局</h1><section id="hint">提示</section></main>
<script>
const route = location.hash;
const link = [...document.querySelectorAll('nav a')].find(item => item.hash === route);
if (link) link.setAttribute('aria-current', 'page');
if (route === '#/broken') document.querySelector('h1').textContent = '页面不存在';
</script>""", encoding="utf-8")
            suite = root / "suite.json"
            suite.write_text(json.dumps({
                "suite_revision": "s", "fixture_revision": "f",
                "cases": [{"id": "TC-A", "steps": [{"op": "visible", "selector": "h1"}]}],
            }), encoding="utf-8")
            map_file = root / "feature-map.md"
            map_file.write_text(_map("index.html", "index.html#hint", "index.html#/workbench",
                                     "index.html#/workbench"), encoding="utf-8")
            report = root / "routes.json"
            self.assertEqual(browser.run(entry, suite, report, feature_map=map_file), 0)
            proof = json.loads(report.read_text())["feature_routes"]["routes"]
            self.assertEqual(len(proof), 3)
            self.assertEqual(proof["index.html#hint"]["identity"]["kind"], "document-anchor")
            self.assertEqual(proof["index.html#/workbench"]["identity"]["kind"], "navigation-heading")

            map_file.write_text(_map("index.html#/broken"), encoding="utf-8")
            rejected = root / "fallback.json"
            self.assertEqual(browser.run(entry, suite, rejected, feature_map=map_file), 1)
            result = json.loads(rejected.read_text())["feature_routes"]["routes"]["index.html#/broken"]
            self.assertTrue(result["url"].endswith("#/broken"))
            self.assertEqual(result["status"], "fail")
            self.assertIsNone(result["identity"])
