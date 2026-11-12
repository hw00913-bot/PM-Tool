"""S1 admission checks the exact declared browser suite bytes."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from loop import suite_catalog


CASE = "TC-C"
RESULT = {"op": "text", "selector": "#layout", "value": "compact"}
RELOAD = {"op": "reload"}


class SuiteCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.baseline = {
            "fixture_revision": "fixture", "suite_revision": "suite", "mandatory_cases": [CASE],
            "acceptance_browsers": ["chromium"], "suite_digests": {},
            "requirements": {"changes": [], "features": [{
                "id": "FUNC-C", "status": "active",
                "assertions": [{"case": CASE, "step": RESULT, "checkpoint": {"index": 1, "after": [RELOAD]}}],
            }]},
        }

    def suite(self, name, *, engine=None, steps=(RELOAD, RESULT), cases=None, **options):
        path = self.root / f"{name}.json"
        suite = {
            "suite_revision": "suite", "fixture_revision": "fixture",
            "cases": [{"id": CASE, "steps": list(steps)}] if cases is None else cases,
            **options,
        }
        if engine is not None:
            suite["engine"] = engine
        path.write_text(json.dumps(suite), encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.baseline["suite_digests"][digest] = [CASE]
        return path, digest

    def test_catalog_is_built_from_actual_bytes_for_each_approved_browser(self):
        self.baseline["acceptance_browsers"] = suite_catalog.acceptance_browsers(
            {"acceptance_browsers": ["firefox", "chromium"]}
        )
        chromium, first = self.suite("chromium")
        firefox, second = self.suite("firefox", engine="firefox")
        self.assertEqual(suite_catalog.freeze(self.baseline, [chromium, firefox]), {
            first: {"engine": "chromium", "cases": {CASE: [RELOAD, RESULT]}},
            second: {"engine": "firefox", "cases": {CASE: [RELOAD, RESULT]}},
        })
        self.assertEqual(suite_catalog.acceptance_browsers({}), ["chromium"])
        for invalid in ([], ["chromium", "chromium"], ["safari"], [None], "chromium"):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "acceptance_browsers"):
                suite_catalog.acceptance_browsers({"acceptance_browsers": invalid})

    def test_requires_all_declared_digests_and_rejects_tampered_bytes(self):
        path, _ = self.suite("one")
        second, _ = self.suite("two", engine="firefox")
        with self.assertRaisesRegex(ValueError, "缺少摘要"):
            suite_catalog.freeze(self.baseline, [path])
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "摘要未登记"):
            suite_catalog.freeze(self.baseline, [path, second])

    def test_rejects_declared_case_mismatch_and_invalid_execution_steps(self):
        wrong_case, _ = self.suite("wrong-case", cases=[{"id": "TC-X", "steps": [RESULT]}])
        with self.assertRaisesRegex(ValueError, "实际编号"):
            suite_catalog.freeze(self.baseline, [wrong_case])
        unknown_action, _ = self.suite("unknown-action", steps=[{"op": "sleep"}, RESULT])
        with self.assertRaisesRegex(ValueError, "步骤不符"):
            suite_catalog.freeze(self.baseline, [unknown_action])
        wrong_revision, _ = self.suite("wrong-revision", fixture_revision="different")
        with self.assertRaisesRegex(ValueError, "数据修订"):
            suite_catalog.freeze(self.baseline, [wrong_revision])

    def test_target_must_have_mandatory_cases_and_real_checkpoints(self):
        self.baseline["acceptance_browsers"] = ["chromium", "firefox"]
        chromium, _ = self.suite("chromium")
        with self.assertRaisesRegex(ValueError, "firefox.*未覆盖必跑"):
            suite_catalog.freeze(self.baseline, [chromium])
        firefox_wrong, _ = self.suite("firefox-wrong", engine="firefox", steps=[RESULT])
        with self.assertRaisesRegex(ValueError, "firefox.*不含全部约定验收时点"):
            suite_catalog.freeze(self.baseline, [chromium, firefox_wrong])
        firefox_correct, _ = self.suite("firefox-correct", engine="firefox")
        suite_catalog.freeze(self.baseline, [chromium, firefox_wrong, firefox_correct])

    def test_one_suite_variant_must_execute_all_bindings_for_one_case(self):
        heading = {"op": "visible", "selector": "#heading"}
        self.baseline["requirements"]["features"][0]["assertions"].append({
            "case": CASE, "step": heading, "checkpoint": {"index": 2, "after": [RELOAD]},
        })
        left, _ = self.suite("left")
        right, _ = self.suite("right", steps=[RELOAD, {**RESULT, "value": "standard"}, heading])
        with self.assertRaisesRegex(ValueError, "不含全部约定验收时点"):
            suite_catalog.freeze(self.baseline, [left, right])
        complete, _ = self.suite("complete", steps=[RELOAD, RESULT, heading])
        suite_catalog.freeze(self.baseline, [left, right, complete])
