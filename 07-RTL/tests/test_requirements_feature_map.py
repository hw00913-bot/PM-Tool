import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from loop import browser, feature_map
from loop.delivery import manifest


class RequirementFeatureMapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        project = self.root / "project"
        (project / "docs").mkdir(parents=True)
        self.feature_map = project / "docs/feature-map.md"
        (project / "prototype").mkdir()
        self.entry = project / "prototype/index.html"
        self.entry.write_text("<h1>外呼</h1>", encoding="utf-8")
        self.assets = manifest(self.entry.parent)
        self.features = [
            {"id": "FUNC-B02", "scene_id": "SCN-B", "cases": ["TC-B02", "TC-B03"], "status": "active"},
            {"id": "FUNC-C01", "scene_id": "SCN-C", "cases": ["TC-C01"], "status": "active"},
            {"id": "FUNC-OLD", "scene_id": "SCN-B", "cases": [], "status": "removed"},
        ]
        self.run = {
            "project_root": str(project),
            "candidate": "tested-candidate",
            "baseline": {
                "frontend": "react", "feature_route_checks": True,
                "mandatory_cases": ["TC-B02", "TC-B03", "TC-C01"],
                "requirements": {"features": self.features, "changes": []},
            },
        }
        self.tasks = {
            "TASK-B-UI": {"feature_ids": ["FUNC-B02"], "edges": []},
            "TASK-B-CONTRACT": {"feature_ids": ["FUNC-B02"], "edges": ["category-query"]},
            "TASK-C": {"feature_ids": ["FUNC-C01"], "edges": ["layout-sync"]},
        }
        self.b = "| FUNC-B02 | SCN-B | index.html#/outbound | category-query | TC-B03, TC-B02 |"
        self.c = "| FUNC-C01 | SCN-C | index.html#/outbound | - | TC-C01 |"
        self.write_map(self.b, self.c)

    def write_map(self, *records):
        self.feature_map.write_text(
            "| 功能 | 场景 | 入口 | 契约 | 必跑用例 |\n"
            "| --- | --- | --- | --- | --- |\n" + "\n".join(records) + "\n",
            encoding="utf-8",
        )

    def check_map(self):
        store = SimpleNamespace(
            run=lambda run_id: self.run,
            tasks=lambda run_id: [{"id": key, "spec": value} for key, value in self.tasks.items()],
        )
        with patch.object(feature_map.frontend, "get_build", return_value={"manifest": self.assets}):
            return feature_map.check(store, "run")

    def proof(self):
        route = "index.html#/outbound"
        feature_routes = {
            "map_sha256": hashlib.sha256(self.feature_map.read_bytes()).hexdigest(),
            "routes": {route: {
                "status": "pass", "url": self.entry.as_uri() + "#/outbound",
                "identity": {"kind": "route-marker", "value": "/outbound"}, "errors": [],
            }},
        }
        asset_digest = browser.asset_digest(self.entry.parent)
        archive = self.root / "report.json"
        archive.write_text(json.dumps({"feature_routes": feature_routes, "asset_digest": asset_digest}), encoding="utf-8")
        return [({"id": "browser-proof"}, {
            "feature_routes": feature_routes, "asset_digest": asset_digest, "evidence": str(archive),
            "evidence_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        })]

    def test_one_row_per_feature_uses_actual_business_scene_and_all_owning_tasks(self):
        result = self.check_map()
        self.assertEqual(result["features"], ["FUNC-B02", "FUNC-C01"])
        self.assertEqual(result["cases"], ["TC-B02", "TC-B03", "TC-C01"])
        self.assertEqual(result["routes"], ["index.html#/outbound"])
        observed = feature_map.require_live_routes(
            self.run, self.entry, self.assets, self.proof(), task_specs=self.tasks,
        )
        self.assertEqual(observed["evidence_id"], "browser-proof")
        self.assertEqual(observed["map_sha256"], result["map_sha256"])

    def test_frozen_feature_coverage_and_case_ownership(self):
        invalid_maps = (
            ((self.b.replace("SCN-B", "TASK-B-UI"), self.c), "业务场景与冻结台账"),
            ((self.b.replace("TC-B03, TC-B02", "TC-B03, TC-C01"),
              self.c.replace("TC-C01", "TC-B02")), "必跑用例与冻结台账"),
            ((self.b, self.c, self.b), "重复登记功能点"),
            ((self.b, self.c, self.b.replace("FUNC-B02", "FUNC-OLD")), "已移除的功能点"),
            ((self.b,), "缺少已冻结功能点：FUNC-C01"),
            ((self.b.replace("category-query", "layout-sync"), self.c), "关联任务未声明的契约"),
        )
        for records, expected in invalid_maps:
            with self.subTest(records=records):
                self.write_map(*records)
                with self.assertRaisesRegex(ValueError, expected):
                    self.check_map()

    def test_browser_proof_checks_frozen_associations_from_its_exact_map_snapshot(self):
        self.write_map(self.b.replace("SCN-B", "SCN-C"), self.c)
        with self.assertRaisesRegex(ValueError, "业务场景与冻结台账"):
            feature_map.require_live_routes(
                self.run, self.entry, self.assets, self.proof(), task_specs=self.tasks,
            )

    def test_frozen_feature_requires_registered_task(self):
        self.tasks.pop("TASK-C")
        with self.assertRaisesRegex(ValueError, "没有关联的已登记任务：FUNC-C01"):
            self.check_map()

    def test_removal_task_does_not_require_an_active_route(self):
        self.features[-1]["cases"] = ["TC-REMOVE"]
        self.run["baseline"]["requirements"]["changes"] = [{"feature_id": "FUNC-OLD", "kind": "remove"}]
        self.run["baseline"]["mandatory_cases"].append("TC-REMOVE")
        self.tasks["TASK-REMOVE"] = {"feature_ids": ["FUNC-OLD"], "edges": []}
        self.assertEqual(self.check_map()["features"], ["FUNC-B02", "FUNC-C01"])
        self.write_map(self.b, self.c, self.b.replace("FUNC-B02", "FUNC-OLD"))
        with self.assertRaisesRegex(ValueError, "已移除"):
            self.check_map()

    def test_planned_map_checks_scene_and_cases_before_candidate_build(self):
        self.write_map(self.b.replace("SCN-B", "TASK-B-UI"), self.c)
        with self.assertRaisesRegex(ValueError, "应填写业务场景编号 SCN-B"):
            feature_map.check_planned_references(self.run, self.tasks)
        self.write_map(self.b.replace("index.html#/outbound", "not-built.html#/outbound"), self.c)
        self.assertEqual(
            feature_map.check_planned_references(self.run, self.tasks)["features"],
            ["FUNC-B02", "FUNC-C01"],
        )
        with self.assertRaisesRegex(ValueError, "入口不在候选版本"):
            self.check_map()
        self.feature_map.unlink()
        self.assertIsNone(feature_map.check_planned_references(self.run, self.tasks))

    def _last_feature_removed(self):
        baseline = self.run["baseline"]
        baseline["feature_entry_checks"] = True
        baseline["mandatory_cases"] = ["TC-REMOVE"]
        self.features[:] = [
            {"id": "FUNC-OLD", "scene_id": "SCN-B", "cases": ["TC-REMOVE"], "status": "removed"},
        ]
        baseline["requirements"]["changes"] = [{"feature_id": "FUNC-OLD", "kind": "remove"}]
        self.tasks = {"TASK-REMOVE": {"feature_ids": ["FUNC-OLD"], "edges": []}}
        self.write_map()

    def _archived_empty_map(self, feature_routes):
        asset_digest = browser.asset_digest(self.entry.parent)
        archive = self.root / "empty-report.json"
        archive.write_text(json.dumps({"feature_routes": feature_routes, "asset_digest": asset_digest}))
        return [({"id": "empty-map-proof"}, {
            "feature_routes": feature_routes, "asset_digest": asset_digest, "evidence": str(archive),
            "evidence_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        })]

    def test_last_removal_requires_explicit_empty_map_and_actual_index(self):
        with self.assertRaisesRegex(ValueError, "缺少"):
            feature_map.rows("| 功能 | 场景 | 入口 | 契约 | 必跑用例 |", allow_empty=True)
        self.write_map()
        with self.assertRaisesRegex(ValueError, "缺少"):
            self.check_map()
        self._last_feature_removed()
        self.assertEqual(self.check_map()["routes"], [])
        self.assertEqual(feature_map.check_planned_references(self.run, self.tasks)["features"], [])
        observed = {"map_sha256": hashlib.sha256(self.feature_map.read_bytes()).hexdigest(), "routes": {}}
        with self.assertRaisesRegex(ValueError, "实测实际落盘入口"):
            feature_map.require_live_routes(
                self.run, self.entry, self.assets, self._archived_empty_map(observed), task_specs=self.tasks,
            )
        observed["entry_check"] = {
            "status": "pass", "url": self.entry.as_uri(),
            "identity": {"kind": "document", "value": "外呼"}, "errors": [],
        }
        evidence = self._archived_empty_map(observed)
        self.assertEqual(
            feature_map.require_live_routes(self.run, self.entry, self.assets, evidence, task_specs=self.tasks)["routes"],
            [],
        )

    @unittest.skipUnless(os.environ.get("RTL_TEST_BROWSER") == "1", "opt in with RTL_TEST_BROWSER=1")
    def test_empty_map_browser_proves_shell_and_runs_removal_case(self):
        self._last_feature_removed()
        suite = self.root / "removal-suite.json"
        suite.write_text(json.dumps({
            "fixture_revision": "removal-fixture",
            "suite_revision": "removal-suite",
            "cases": [{"id": "TC-REMOVE", "steps": [
                {"op": "hidden", "selector": "#old-feature"},
                {"op": "text", "selector": "h1", "value": "外呼"},
            ]}],
        }))
        report_file = self.root / "browser-removal.json"
        self.assertEqual(
            browser.run(self.entry, suite, report_file, feature_map=self.feature_map, feature_entries=True), 0,
        )
        report = json.loads(report_file.read_text())
        self.assertEqual(report["cases"]["TC-REMOVE"]["status"], "pass")
        self.assertEqual(report["feature_routes"]["routes"], {})
        self.assertEqual(report["feature_routes"]["entry_check"]["url"], self.entry.as_uri())
        self.assertEqual(report["feature_routes"]["entry_check"]["status"], "pass")
        proof = feature_map.require_live_routes(
            self.run, self.entry, self.assets, self._archived_empty_map(report["feature_routes"]), task_specs=self.tasks,
        )
        self.assertEqual(proof["routes"], [])

    def test_legacy_map_still_uses_task_id_in_scene_column(self):
        self.run["baseline"].pop("requirements")
        self.tasks = {"B": {"edges": ["category-query"]}, "C": {"edges": []}}
        self.write_map(self.b.replace("SCN-B", "B"), self.c.replace("SCN-C", "C"))
        self.assertEqual(self.check_map()["features"], ["FUNC-B02", "FUNC-C01"])
