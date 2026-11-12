"""Regressions for binding business changes to actual browser assertions."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from loop import browser, requirements, requirements_run, verification
from loop.state import LoopError, pack


CASE = "TC-C01"
FEATURE = "FUNC-C01"
OLD_STEP = {"op": "text", "selector": "#layout", "value": "紧凑"}
NEW_STEP = {**OLD_STEP, "value": "标准"}
EXAMPLE = Path(__file__).resolve().parents[1] / "skills/requirement-architecture/references/requirements-example.json"


def plan_for(step):
    plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    plan["features"][0]["assertions"] = [{"case": CASE, "step": step}]
    return plan


def suite_for(step, *, suite_revision="s1"):
    return json.dumps({
        "fixture_revision": "f1", "suite_revision": suite_revision,
        "cases": [{"id": CASE, "steps": [step]}],
    }, ensure_ascii=False)


def suite_digest(suite):
    return hashlib.sha256(suite.encode()).hexdigest()


def baseline_for(step, *, suites, revision="r1", suite_revision="s1"):
    return {
        "revision": revision, "fixture_revision": "f1", "suite_revision": suite_revision,
        "mandatory_cases": [CASE], "suite_digests": {suite_digest(suite): [CASE] for suite in suites},
        "change_assertion_checks": True,
        "requirements": requirements.normalize_plan(plan_for(step), [CASE], require_assertions=True),
    }


class AssertionBindingTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def test_strict_plan_requires_exact_supported_predicate_and_owned_case(self):
        plan = plan_for(NEW_STEP)
        plan["features"][0].pop("assertions")
        requirements.normalize_plan(plan, [CASE])
        with self.assertRaisesRegex(ValueError, "已确认的可观察验收断言"):
            requirements.normalize_plan(plan, [CASE], require_assertions=True)
        plan["features"][0]["assertions"] = [{"case": "TC-OTHER", "step": NEW_STEP}]
        with self.assertRaisesRegex(ValueError, "TC-OTHER"):
            requirements.normalize_plan(plan, [CASE], require_assertions=True)
        plan["features"][0]["assertions"] = [{"case": CASE, "step": {"op": "click", "selector": "#save"}}]
        with self.assertRaisesRegex(ValueError, "浏览器断言操作"):
            requirements.normalize_plan(plan, [CASE], require_assertions=True)

    def check_successor(self, previous, current):
        with sqlite3.connect(":memory:") as db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE runs(project_id TEXT, status TEXT, baseline TEXT)")
            db.execute("INSERT INTO runs VALUES('project', 'delivered', ?)", (pack(previous),))
            requirements_run.validate_revision(db, "project", current)

    def test_new_business_rule_cannot_reuse_legacy_frozen_case_bytes(self):
        prior_suite, next_suite = suite_for(OLD_STEP), suite_for(NEW_STEP, suite_revision="s2")
        prior = baseline_for(OLD_STEP, suites=[prior_suite])
        prior.pop("change_assertion_checks")
        prior["requirements"]["features"][0].pop("assertions")
        current = baseline_for(NEW_STEP, suites=[prior_suite, next_suite], revision="r2", suite_revision="s2")
        plan = current["requirements"]
        plan["requirements"][0].update(rule="展示标准布局", acceptance="看到标准布局")
        plan["features"][0]["rule"] = "展示标准布局"
        plan["changes"][0].update(kind="modify", before="展示紧凑布局", after="展示标准布局")
        with self.assertRaisesRegex(LoopError, "不能沿用上一轮旧摘要"):
            self.check_successor(prior, current)
        current["suite_digests"].pop(suite_digest(prior_suite))
        self.check_successor(prior, current)

    def test_changed_condition_can_reuse_expected_value_with_review_and_new_case(self):
        prior = baseline_for(OLD_STEP, suites=[suite_for(OLD_STEP)])
        next_suite = suite_for(OLD_STEP, suite_revision="s2").replace('"steps": [', '"steps": [{"op": "reload"}, ')
        current = baseline_for(OLD_STEP, suites=[next_suite], revision="r2", suite_revision="s2")
        plan = current["requirements"]
        plan["requirements"][0]["rule"] = "打开并刷新后仍显示紧凑"
        plan["features"][0]["rule"] = "打开并刷新后仍显示紧凑"
        plan["changes"][0].update(kind="modify", before="打开后显示紧凑", after="打开并刷新后仍显示紧凑")
        with self.assertRaisesRegex(LoopError, "新预期断言"):
            self.check_successor(prior, current)
        plan["changes"][0]["assertion_reuse"] = "用户确认预期仍为紧凑，新增刷新前置步骤"
        self.check_successor(prior, current)

    def test_confirmed_revision_replaces_the_historical_observation(self):
        prior = baseline_for(OLD_STEP, suites=[suite_for(OLD_STEP)])
        current = baseline_for(
            NEW_STEP, suites=[suite_for(NEW_STEP, suite_revision="s2")], revision="r2", suite_revision="s2",
        )
        current["change_proof_checks"] = True
        plan = current["requirements"]
        plan["requirements"][0].update(rule="展示标准布局", acceptance="看到标准布局")
        feature, change = plan["features"][0], plan["changes"][0]
        feature["rule"] = "展示标准布局"
        change.update(kind="modify", before="展示紧凑布局", after="展示标准布局")
        before = prior["requirements"]["features"][0]["assertions"][0]
        approved_after = feature["assertions"][0]
        unrelated = {"case": CASE, "step": {"op": "visible", "selector": "#heading"}}
        feature["assertions"] = [before, unrelated]
        with self.assertRaisesRegex(LoopError, "经确认的改版前后验收断言"):
            self.check_successor(prior, current)
        change["assertion_changes"] = [{"before": before, "after": unrelated}]
        with self.assertRaisesRegex(LoopError, "旧验收预期仍在原用例"):
            self.check_successor(prior, current)
        feature["assertions"] = [unrelated]
        with self.assertRaisesRegex(LoopError, "更换可观察结果的类型"):
            self.check_successor(prior, current)
        change["assertion_changes"] = [{"before": None, "after": approved_after}]
        feature["assertions"] = [approved_after, unrelated]
        with self.assertRaisesRegex(LoopError, "旧验收断言与已交付基线不符"):
            self.check_successor(prior, current)
        change["assertion_changes"] = [{"before": before, "after": approved_after}]
        self.check_successor(prior, current)

    def test_new_observation_type_can_be_confirmed_with_its_own_reason(self):
        prior = baseline_for(OLD_STEP, suites=[suite_for(OLD_STEP)])
        hidden = {"op": "hidden", "selector": "#layout"}
        current = baseline_for(
            hidden, suites=[suite_for(hidden, suite_revision="s2")], revision="r2", suite_revision="s2",
        )
        current["change_proof_checks"] = True
        plan = current["requirements"]
        plan["features"][0]["rule"] = "关闭布局功能后隐藏布局控件"
        before = prior["requirements"]["features"][0]["assertions"][0]
        after = plan["features"][0]["assertions"][0]
        plan["changes"][0].update(
            kind="modify", before="显示布局值", after="隐藏布局控件",
            assertion_changes=[{"before": before, "after": after, "retarget_reason": "确认用控件隐藏证明关闭行为"}],
        )
        self.check_successor(prior, current)

    def test_confirmed_revision_reuses_the_same_outcome_with_explicit_basis(self):
        prior = baseline_for(OLD_STEP, suites=[suite_for(OLD_STEP)])
        next_suite = suite_for(OLD_STEP, suite_revision="s2").replace('"steps": [', '"steps": [{"op": "reload"}, ')
        current = baseline_for(OLD_STEP, suites=[next_suite], revision="r2", suite_revision="s2")
        current["change_proof_checks"] = True
        plan = current["requirements"]
        before = prior["requirements"]["features"][0]["assertions"][0]
        after = {**before, "checkpoint": {"index": 1, "after": [{"op": "reload"}]}}
        plan["features"][0].update(rule="刷新后仍显示紧凑", assertions=[after])
        plan["changes"][0].update(
            kind="modify", before="打开后显示紧凑", after="刷新后仍显示紧凑",
            assertion_changes=[{"before": before, "after": after}],
        )
        with self.assertRaisesRegex(LoopError, "沿用旧预期须登记确认依据"):
            self.check_successor(prior, current)
        plan["changes"][0]["assertion_reuse"] = "用户确认新增刷新步骤，仍显示紧凑"
        self.check_successor(prior, current)

    def evidence(self, suite, report_step, *, status="pass", filename="report.json"):
        context = {
            "path": str(self.root / "index.html"), "url": (self.root / "index.html").as_uri(),
            "browser": "test-browser", "fixture_revision": "f1", "suite_revision": "s1",
            "asset_digest": "a" * 64, "suite_digest": suite_digest(suite),
        }
        case = {
            "status": status, "executed": [report_step["op"] + " " + report_step["selector"]],
            "assertions": [report_step], "errors": [] if status == "pass" else ["failed assertion"],
        }
        raw = {
            **context, "protocol": "file:", "entry": context["url"], "suite_input": suite,
            "cases": {CASE: case},
        }
        path = self.root / filename
        path.write_text(json.dumps(raw), encoding="utf-8")
        context.update(evidence=str(path), evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return {"cases": pack({CASE: case})}, context

    def test_old_assertion_and_fabricated_new_assertion_cannot_prove_new_rule(self):
        old_suite, new_suite = suite_for(OLD_STEP), suite_for(NEW_STEP)
        run = {"baseline": baseline_for(NEW_STEP, suites=[old_suite, new_suite])}
        genuine_old = self.evidence(old_suite, OLD_STEP)
        with self.assertRaisesRegex(ValueError, "冻结断言未在"):
            verification.require_feature_assertions(run, [genuine_old])
        invented = self.evidence(old_suite, NEW_STEP, filename="invented.json")
        with self.assertRaisesRegex(ValueError, "缺少已执行断言"):
            verification.require_feature_assertions(run, [invented])
        genuine_new = self.evidence(new_suite, NEW_STEP, filename="new.json")
        verification.require_feature_assertions(run, [genuine_old, genuine_new])
        with self.assertRaisesRegex(ValueError, "冻结断言未在"):
            verification.require_feature_assertions(run, [genuine_new, genuine_old])
        Path(genuine_new[1]["evidence"]).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "缺少已执行断言"):
            verification.require_feature_assertions(run, [genuine_new])

    @unittest.skipUnless(os.environ.get("RTL_TEST_BROWSER") == "1", "opt in with RTL_TEST_BROWSER=1")
    def test_real_browser_records_the_exact_observed_predicate(self):
        delivered = self.root / "prototype"
        delivered.mkdir()
        entry = delivered / "index.html"
        entry.write_text('<!doctype html><meta charset="utf-8"><h1 id="layout">紧凑</h1>', encoding="utf-8")
        old_suite, new_suite = suite_for(OLD_STEP), suite_for(NEW_STEP)
        old_path, new_path = self.root / "old-suite.json", self.root / "new-suite.json"
        old_path.write_text(old_suite, encoding="utf-8")
        new_path.write_text(new_suite, encoding="utf-8")
        old_report = self.root / "old-browser.json"
        new_report = self.root / "new-browser.json"
        self.assertEqual(browser.run(entry, old_path, old_report), 0)
        self.assertNotEqual(browser.run(entry, new_path, new_report), 0)
        old = json.loads(old_report.read_text())
        new = json.loads(new_report.read_text())
        self.assertEqual(old["suite_input"], old_suite)
        self.assertEqual(old["cases"][CASE]["assertions"], [OLD_STEP])
        self.assertNotIn("assertions", new["cases"][CASE])
        context = {**old, "url": old["entry"], "evidence": str(old_report),
                   "evidence_sha256": hashlib.sha256(old_report.read_bytes()).hexdigest()}
        run = {"baseline": baseline_for(NEW_STEP, suites=[old_suite, new_suite])}
        with self.assertRaisesRegex(ValueError, "冻结断言未在"):
            verification.require_feature_assertions(run, [({"cases": pack(old["cases"])}, context)])
