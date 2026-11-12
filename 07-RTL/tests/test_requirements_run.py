import copy
import json
from pathlib import Path
import tempfile
import unittest

from loop import artifacts, dispatch, requirements, requirements_run
from loop.skills import read_skill
from loop.state import LoopError, Store


PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "skills/requirement-architecture/references/requirements-example.json"


class RequirementsRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.output = self.project / "prototype"
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")
        self.baseline = {
            "revision": "baseline-r1", "mandatory_cases": ["TC-C01"],
            "requirements": requirements.normalize_plan(json.loads(EXAMPLE.read_text()), ["TC-C01"]),
        }
        self.run = self.store.create_run(self.project, self.output, self.baseline, run_id="first")
        artifacts.initialize_repo(self.store, self.run["id"], 1)

    def test_dispatch_requires_s1_and_frozen_snapshot_can_be_recovered(self):
        run_id = self.run["id"]
        with self.assertRaisesRegex(LoopError, "S1 需求契约"):
            dispatch.reserve(self.store, run_id, 1, "C")
        ready = requirements_run.prepare(self.store, run_id, 1)
        snapshot = Path(ready["baseline"])
        content = snapshot.read_bytes()
        self.assertEqual(json.loads(content)["requirements"], self.baseline["requirements"])
        self.assertEqual(ready, requirements_run.check(self.store, run_id))
        snapshot.write_text("{}")
        with self.assertRaisesRegex(LoopError, "S1 冻结基线"):
            dispatch.reserve(self.store, run_id, 1, "C")
        with self.assertRaisesRegex(ValueError, "原决定不符"):
            requirements_run.prepare(self.store, run_id, 1)
        snapshot.unlink()
        self.assertEqual(requirements_run.prepare(self.store, run_id, 1), ready)
        self.assertEqual(snapshot.read_bytes(), content)
        ledger = Path(ready["ledger"])
        ledger.write_text(ledger.read_text().replace("rtl-s1-first.json", "rtl-s1-another.json"))
        with self.assertRaisesRegex(ValueError, "需求台账与本轮"):
            dispatch.reserve(self.store, run_id, 1, "C")
        self.assertEqual(requirements_run.prepare(self.store, run_id, 1), ready)
        attempt = dispatch.reserve(self.store, run_id, 1, "C")
        self.assertEqual(attempt["task_id"], "C")
        self.assertEqual(attempt["input"]["requirements"]["snapshot"], requirements_run.snapshot_relative_path(run_id))

    def test_registered_tasks_keep_the_approved_spec_and_valid_feature_links(self):
        run_id = self.run["id"]
        requirements_run.prepare(self.store, run_id, 1)
        spec = self.baseline["requirements"]["tasks"][0]
        with self.assertRaisesRegex(LoopError, "已确认的 S1 任务包"):
            self.store.add_task(run_id, 1, {**spec, "done": "another completion rule"})
        with self.assertRaisesRegex(ValueError, "未登记的编号"):
            self.store.add_task(run_id, 1, {**spec, "id": "X", "feature_ids": ["FUNC-MISSING"]})
        self.store.add_task(run_id, 1, {**spec, "id": "shared", "paths": ["layout-support.js"]})
        self.assertEqual(requirements_run.check(self.store, run_id)["registered_tasks"], ["C", "shared"])

    def test_new_business_rule_requires_explicit_change_and_revision(self):
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET status='delivered' WHERE id='first'")
        changed = copy.deepcopy(self.baseline)
        plan = changed["requirements"]
        plan["requirements"][0]["rule"] = "取消后恢复原布局"
        plan["features"][0]["rule"] = "取消后恢复原布局"
        plan["changes"][0]["kind"] = "technical"
        with self.assertRaisesRegex(LoopError, "须登记 modify"):
            self.store.create_run(self.project, self.output, changed, run_id="next")
        plan["changes"][0].update(kind="modify", before="保存后刷新恢复", after="取消后恢复原布局")
        with self.assertRaisesRegex(LoopError, "新的需求契约修订"):
            self.store.create_run(self.project, self.output, changed, run_id="next")
        changed["revision"] = "baseline-r2"
        self.assertEqual(self.store.create_run(self.project, self.output, changed, run_id="next")["id"], "next")


class RequirementSkillStepTests(unittest.TestCase):
    def test_each_step_is_callable_under_existing_architect_entry(self):
        steps = [
            read_skill("requirement-architecture", "architect", "S1", step=step)
            for step in ("analysis", "scenes", "features", "tasks")
        ]
        self.assertEqual(len({step["digest"] for step in steps}), 4)
        self.assertIn("BMad", steps[0]["content"])
        with self.assertRaisesRegex(ValueError, "S1 四步规范"):
            read_skill("requirement-architecture", "architect", "S2", step="analysis")
