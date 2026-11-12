import copy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from loop import requirements, requirements_history
from loop.requirements_revision import validate_revision
from loop.state import LoopError, pack


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "skills/requirement-architecture/references/requirements-example.json"
CASE = "TC-C01"
FEATURE = "FUNC-C01"


def baseline():
    plan = requirements.normalize_plan(json.loads(EXAMPLE.read_text()), [CASE])
    return {
        "revision": "r1",
        "suite_revision": "s1",
        "fixture_revision": "f1",
        "suite_digests": {"a" * 64: [CASE]},
        "mandatory_cases": [CASE],
        "requirements": plan,
    }


def mark_change(data, kind="modify"):
    data["requirements"]["changes"][0].update(kind=kind, before="先前描述", after="新描述")
    return data


class RevisionIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE runs(project_id TEXT, status TEXT, baseline TEXT)")
        self.addCleanup(self.db.close)
        self.prior = baseline()
        self.record(self.prior)

    def record(self, previous, *, status="delivered"):
        self.db.execute("INSERT INTO runs VALUES('project', ?, ?)", (status, pack(previous)))

    def validate(self, current):
        return validate_revision(self.db, "project", current)

    def test_scene_semantics_require_modify_and_new_contract_revision(self):
        changes = {
            "actor": "来访者",
            "trigger": "关闭工作台",
            "preconditions": ["仅查看模式"],
            "main_flow": ["关闭", "再次进入"],
            "alternatives": ["加载失败时清空布局"],
            "outcome": "布局总是回到标准",
        }
        for field, updated in changes.items():
            with self.subTest(field=field):
                current = mark_change(copy.deepcopy(self.prior), "verify")
                current["requirements"]["scenes"][0][field] = updated
                with self.assertRaisesRegex(LoopError, "须登记 modify"):
                    self.validate(current)
                mark_change(current)
                with self.assertRaisesRegex(LoopError, "新的需求契约修订"):
                    self.validate(current)
                current["revision"] = "r2"
                self.assertEqual(self.validate(current), current)

    def test_scene_dependency_change_requires_explicit_affected_feature(self):
        prior = copy.deepcopy(self.prior)
        plan = prior["requirements"]
        scene, feature, task, change = [plan[key][0] for key in ("scenes", "features", "tasks", "changes")]
        plan["scenes"].append({**scene, "id": "SCN-A"})
        plan["features"].append({**feature, "id": "FUNC-A01", "scene_id": "SCN-A", "cases": ["TC-A01"]})
        plan["tasks"].append({**task, "id": "A", "feature_ids": ["FUNC-A01"], "cases": ["TC-A01"]})
        plan["changes"].append({**change, "feature_id": "FUNC-A01"})
        prior["revision"] = "r2"
        self.record(prior)
        current = mark_change(copy.deepcopy(prior), "technical")
        current["requirements"]["scenes"][0]["depends_on"] = ["SCN-A"]
        current["requirements"]["changes"].pop()
        current["revision"] = "r3"
        with self.assertRaisesRegex(LoopError, f"{FEATURE}.*modify"):
            self.validate(current)
        mark_change(current)
        self.assertEqual(self.validate(current), current)

    def test_historical_business_label_cannot_change_even_after_new_revision(self):
        latest = mark_change(copy.deepcopy(self.prior))
        latest["revision"] = "r2"
        latest["requirements"]["features"][0]["rule"] = "覆盖已保存的布局"
        self.record(latest)
        current = mark_change(copy.deepcopy(latest))
        current["revision"] = "r1"
        current["requirements"]["features"][0]["rule"] = "取消已保存的布局"
        with self.assertRaisesRegex(LoopError, "修订 r1 已绑定历史业务内容"):
            self.validate(current)
        # Reinstating the original contract uses the identical earlier identity.
        reinstated = mark_change(copy.deepcopy(self.prior))
        self.assertEqual(self.validate(reinstated), reinstated)

    def test_suite_label_is_project_wide_even_when_fixture_label_changes(self):
        second = mark_change(copy.deepcopy(self.prior), "verify")
        second.update(suite_revision="s2", suite_digests={"b" * 64: [CASE]})
        self.record(second)
        attempt = mark_change(copy.deepcopy(second), "verify")
        attempt.update(suite_revision="s1", fixture_revision="f2")
        with self.assertRaisesRegex(LoopError, "用例修订 s1 已绑定历史"):
            self.validate(attempt)
        attempt["suite_digests"] = self.prior["suite_digests"]
        with self.assertRaisesRegex(LoopError, "用例修订 s1 已绑定历史"):
            self.validate(attempt)
        attempt["suite_revision"] = "s3"
        self.assertEqual(self.validate(attempt), attempt)

    def test_prepared_local_revision_labels_survive_supersede(self):
        superseded = mark_change(copy.deepcopy(self.prior))
        superseded["revision"] = "r2"
        superseded["requirements"]["features"][0]["rule"] = "关闭原型时保存"
        self.record(superseded, status="superseded")
        current = mark_change(copy.deepcopy(superseded))
        current["requirements"]["features"][0]["rule"] = "关闭原型时清空"
        with self.assertRaisesRegex(LoopError, "修订 r2 已绑定历史业务内容"):
            self.validate(current)

    def test_verified_cross_ledger_baselines_reserve_revision_labels(self):
        older = copy.deepcopy(self.prior)
        older.update(revision="foreign-r1", suite_revision="foreign-suite")
        older["requirements"]["features"][0]["rule"] = "关闭时保存"
        current = mark_change(copy.deepcopy(self.prior))
        current["revision"] = "foreign-r1"
        current["requirements"]["features"][0]["rule"] = "关闭时清空"
        with patch.object(requirements_history, "historical_baselines", return_value=[self.prior, older], create=True):
            with self.assertRaisesRegex(LoopError, "修订 foreign-r1 已绑定历史"):
                validate_revision(self.db, "project", current, project=ROOT)
            current.update(revision="r2", suite_revision="foreign-suite", suite_digests={"f" * 64: [CASE]})
            with self.assertRaisesRegex(LoopError, "用例修订 foreign-suite 已绑定历史"):
                validate_revision(self.db, "project", current, project=ROOT)

    def test_provenance_title_and_task_only_adjustments_preserve_revision(self):
        current = mark_change(copy.deepcopy(self.prior), "technical")
        plan = current["requirements"]
        plan["approval"] = "已确认技术性修订"
        plan["requirements"][0].update(name="新标题", source="新的资料链接")
        plan["scenes"][0].update(name="新的场景称呼", preconditions=["原型已打开，布局初始为标准"])
        plan["features"][0]["name"] = "新的功能称呼"
        plan["tasks"][0]["paths"] = ["src/layout"]
        self.assertEqual(self.validate(current), current)

    def test_replacing_an_existing_observable_expectation_requires_revision_and_modify(self):
        current = mark_change(copy.deepcopy(self.prior), "verify")
        current["requirements"]["features"][0]["assertions"][0]["step"]["value"] = "standard"
        with self.assertRaisesRegex(LoopError, "须登记 modify"):
            self.validate(current)
        mark_change(current)
        with self.assertRaisesRegex(LoopError, "新的需求契约修订"):
            self.validate(current)
        current.update(revision="r2", normative_change_checks=True)
        changed = self.validate(current)["normative_changes"][FEATURE]
        self.assertIn("compact", changed["before"])
        self.assertIn("standard", changed["after"])

    def test_moving_approved_result_to_initial_state_cannot_keep_the_revision(self):
        current = mark_change(copy.deepcopy(self.prior), "verify")
        binding = current["requirements"]["features"][0]["assertions"][0]
        binding["checkpoint"] = {"index": 0, "after": []}
        with self.assertRaisesRegex(LoopError, "须登记 modify"):
            self.validate(current)
        mark_change(current)
        with self.assertRaisesRegex(LoopError, "新的需求契约修订"):
            self.validate(current)

    def test_supplemental_evidence_can_keep_existing_business_expectations(self):
        current = mark_change(copy.deepcopy(self.prior), "technical")
        current["requirements"]["features"][0]["assertions"].append({
            "case": CASE, "step": {"op": "visible", "selector": "#result"},
        })
        self.assertEqual(self.validate(current), current)

    def test_normative_change_comes_from_frozen_contract_and_scene(self):
        current = mark_change(copy.deepcopy(self.prior))
        current.update(revision="r2", normative_change_checks=True)
        current["requirements"]["scenes"][0]["actor"] = "租户管理员"
        current["requirements"]["features"][0]["rule"] = "保存后固定为标准"
        checked = self.validate(current)
        change = checked["normative_changes"][FEATURE]
        self.assertIn("工作台用户", change["before"])
        self.assertIn("租户管理员", change["after"])
        self.assertIn("保存后固定为标准", change["after"])
        self.assertNotIn("新描述", change["after"])
        self.assertNotIn("normative_changes", current)
