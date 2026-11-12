import copy
import json
from pathlib import Path
import tempfile
import unittest

from loop import lifecycle, requirements, revision_registry
from loop.state import LoopError, Store


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "skills/requirement-architecture/references/requirements-example.json"
CASE = "TC-C01"


class RevisionRegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.origin = self.root / "first/state.sqlite"
        self.second = self.root / "second/state.sqlite"
        self.index = self.project / revision_registry.REGISTRY
        plan = requirements.normalize_plan(json.loads(EXAMPLE.read_text()), [CASE])
        self.baseline = {
            "revision": "r1",
            "suite_revision": "s1",
            "fixture_revision": "f1",
            "suite_digests": {"a" * 64: [CASE]},
            "requirements": plan,
            "normative_change_checks": True,
            "s1_review": {"confirmation": {"review_id": "review-first"}, "steps": []},
        }

    def record(self, baseline=None, *, run_id="first", ledger=None):
        revision_registry.record_revision(
            self.project, "project", self.baseline if baseline is None else baseline, run_id,
            ledger=self.origin if ledger is None else ledger,
        )

    def validate(self, baseline, *, run_id="next", ledger=None):
        revision_registry.validate_registry(
            self.project, "project", baseline, run_id=run_id,
            ledger=self.second if ledger is None else ledger,
        )

    def renewed(self):
        proposed = copy.deepcopy(self.baseline)
        proposed["s1_review"]["confirmation"]["review_id"] = "review-next"
        return proposed

    def test_superseded_revision_labels_cannot_change_on_a_second_ledger(self):
        self.record()
        self.assertIn("Project-ID: project", (self.project / "memory/project.md").read_text())
        proposed = self.renewed()
        proposed["requirements"]["scenes"][0]["actor"] = "租户管理员"
        with self.assertRaisesRegex(LoopError, "需求契约修订 r1 已绑定历史业务内容"):
            self.validate(proposed)
        proposed["revision"] = "r2"
        proposed["suite_digests"] = {"b" * 64: [CASE]}
        with self.assertRaisesRegex(LoopError, "用例修订 s1 已绑定历史用例摘要"):
            self.validate(proposed)
        proposed["suite_revision"] = "s2"
        self.validate(proposed)
        self.record(proposed, run_id="next", ledger=self.second)

    def test_additional_assertion_is_allowed_but_cannot_disappear_from_historical_label(self):
        self.record()
        extra = self.renewed()
        extra["suite_revision"] = "s2"
        extra["suite_digests"] = {"b" * 64: [CASE]}
        extra["requirements"]["features"][0]["assertions"].append(
            {"case": CASE, "step": {"op": "visible", "selector": "#layout-preview"}}
        )
        self.record(extra, run_id="next", ledger=self.second)
        old = self.renewed()
        old["s1_review"]["confirmation"]["review_id"] = "review-later"
        with self.assertRaisesRegex(LoopError, "历史验收预期"):
            self.validate(old, run_id="later")

    def test_review_use_is_idempotent_for_one_run_and_not_reusable_across_ledgers(self):
        self.record()
        first = self.index.read_bytes()
        self.record()
        self.assertEqual(self.index.read_bytes(), first)
        self.validate(self.baseline, run_id="first", ledger=self.origin)
        with self.assertRaisesRegex(LoopError, "已用于另一轮"):
            self.validate(self.baseline, run_id="first")
        with self.assertRaisesRegex(LoopError, "已用于另一轮"):
            self.validate(self.baseline)

    def test_review_cannot_be_reused_by_editing_later_steps(self):
        self.record()
        edited = copy.deepcopy(self.baseline)
        edited["s1_review"]["steps"] = [{"step": "tasks", "digest": "b" * 64}]
        with self.assertRaisesRegex(LoopError, "已用于另一轮"):
            self.validate(edited)

    def test_preupgrade_snapshot_reserves_label_without_claiming_delivery(self):
        snapshot = self.project / "memory/baselines/rtl-s1-superseded.json"
        snapshot.parent.mkdir(parents=True)
        legacy = copy.deepcopy(self.baseline)
        legacy.pop("s1_review")
        snapshot.write_text(json.dumps(legacy), encoding="utf-8")
        proposed = self.renewed()
        proposed["requirements"]["features"][0]["rule"] = "覆盖偏好"
        with self.assertRaisesRegex(LoopError, "r1 已绑定历史业务内容"):
            self.validate(proposed)
        self.record(self.renewed(), run_id="next", ledger=self.second)
        self.assertFalse((self.project / "memory/requirements-history.json").exists())

    def test_corrupt_preupgrade_snapshot_does_not_mean_no_history(self):
        snapshot = self.project / "memory/baselines/rtl-s1-known.json"
        snapshot.parent.mkdir(parents=True)
        snapshot.write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(LoopError, "无法从旧冻结快照核对"):
            self.validate(self.renewed())

    def test_known_structured_registry_loss_or_tampering_fails_closed(self):
        self.record()
        original = self.index.read_bytes()
        payload = json.loads(original)
        payload["business"] = {}
        self.index.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(LoopError, "无法核对结构化需求修订登记"):
            self.validate(self.renewed())
        self.index.unlink()
        with self.assertRaisesRegex(LoopError, "结构化需求修订登记缺失"):
            self.validate(self.renewed())

    def test_real_store_supersede_reserves_revision_for_next_ledger(self):
        registry = self.root / "reservations"
        first = Store(self.origin.parent, reservation_root=registry)
        first.create_run(self.project, self.project / "prototype", self.baseline, project_id="project", run_id="first")
        lifecycle.supersede(first, "first", 1, reason="采用新版需求", approval="用户确认替代")
        second = Store(self.second.parent, reservation_root=registry)
        bad = self.renewed()
        bad["requirements"]["scenes"][0]["actor"] = "其他角色"
        with self.assertRaisesRegex(LoopError, "r1 已绑定历史业务内容"):
            second.create_run(self.project, self.project / "prototype", bad, project_id="project", run_id="next")
        self.assertEqual(second.runs(), [])
        bad["revision"] = "r2"
        accepted = second.create_run(self.project, self.project / "prototype", bad, project_id="project", run_id="next")
        self.assertEqual(accepted["baseline"]["revision"], "r2")

    def test_formatting_label_whitespace_does_not_bypass_binding(self):
        self.record()
        proposed = self.renewed()
        proposed["revision"] = " r1 "
        proposed["requirements"]["features"][0]["rule"] = "覆盖偏好"
        with self.assertRaisesRegex(LoopError, "r1 已绑定历史业务内容"):
            self.validate(proposed)
