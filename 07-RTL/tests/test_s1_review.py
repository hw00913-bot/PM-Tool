from copy import deepcopy
from functools import partial
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import __main__ as cli, revision_registry, s1_review
from loop.skills import read_skill
from loop.state import Store
from s1_workflow_fixture import record_s1


PLAN = Path(__file__).resolve().parents[1] / "skills/requirement-architecture/references/requirements-example.json"


class S1ReviewTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        self.record = root / "s1.json"
        self.project = root / "prototype-project"
        self.plan = json.loads(PLAN.read_text())
        self.baseline = {"revision": "layout-r1", "requirements": self.plan}
        self.contract = {
            "revision": "layout-r1", "requirements": self.plan["requirements"], "pending": [],
        }

    def step(self, name):
        s1_review.record_step(self.record, read_skill("requirement-architecture", "architect", "S1", step=name))

    def confirm(self):
        return s1_review.confirm(self.record, self.project, self.contract, self.plan["approval"])

    def test_requires_order_and_confirmed_contract_with_no_pending_decisions(self):
        with self.assertRaisesRegex(ValueError, "analysis"):
            self.step("scenes")
        self.step("analysis")
        with self.assertRaisesRegex(ValueError, "s1-confirm"):
            self.step("scenes")
        with self.assertRaisesRegex(ValueError, "待决事项"):
            s1_review.confirm(self.record, self.project, {**self.contract, "pending": ["scope"]}, self.plan["approval"])
        source_missing = deepcopy(self.contract)
        source_missing["requirements"][0]["source"] = "TBD"
        with self.assertRaisesRegex(ValueError, "缺少确认的来源"):
            s1_review.confirm(self.record, self.project, source_missing, self.plan["approval"])
        with self.assertRaisesRegex(ValueError, "确认依据"):
            s1_review.confirm(self.record, self.project, self.contract, "尚未确认")
        self.confirm()
        with self.assertRaisesRegex(ValueError, "scenes"):
            self.step("features")
        for name in ("scenes", "features", "tasks"):
            self.step(name)
        approved = s1_review.for_init(self.record, self.project, self.baseline)
        self.assertEqual([row["step"] for row in approved["steps"]], list(s1_review.STEPS))
        self.assertEqual(approved["confirmation"]["approval"], self.plan["approval"])
        self.assertIs(approved["confirmation"]["human_acceptance"], False)

    def test_changed_contract_project_or_approval_requires_new_confirmation(self):
        self.step("analysis")
        self.confirm()
        for name in ("scenes", "features", "tasks"):
            self.step(name)
        changed = deepcopy(self.baseline)
        changed["requirements"]["requirements"][0]["rule"] = "另一条规则"
        with self.assertRaisesRegex(ValueError, "与已确认"):
            s1_review.for_init(self.record, self.project, changed)
        with self.assertRaisesRegex(ValueError, "与已确认"):
            s1_review.for_init(self.record, self.project.with_name("another-project"), self.baseline)
        changed = deepcopy(self.baseline)
        changed["requirements"]["approval"] = "不同来源"
        with self.assertRaisesRegex(ValueError, "与已确认"):
            s1_review.for_init(self.record, self.project, changed)
        self.step("analysis")
        with self.assertRaisesRegex(ValueError, "s1-confirm"):
            self.step("scenes")


    def test_confirmation_pins_the_exact_business_change_assertions(self):
        self.baseline["change_proof_checks"] = True
        original = self.plan["features"][0]["assertions"][0]
        expected = {**original, "step": {**original["step"], "value": "standard"}}
        self.plan["changes"][0].update(
            kind="modify", before="保存紧凑", after="保存标准",
            assertion_changes=[{"before": original, "after": expected}],
        )
        self.plan["features"][0]["assertions"] = [expected]
        self.contract["change_assertions"] = deepcopy(self.plan["changes"])
        self.step("analysis")
        self.confirm()
        for name in ("scenes", "features", "tasks"):
            self.step(name)
        s1_review.for_init(self.record, self.project, self.baseline)
        changed = deepcopy(self.baseline)
        replacement = changed["requirements"]["changes"][0]["assertion_changes"][0]["after"]
        replacement["step"]["value"] = "compact"
        with self.assertRaisesRegex(ValueError, "与已确认"):
            s1_review.for_init(self.record, self.project, changed)

    def frozen_inputs(self):
        root = self.record.parent
        suite_path = root / "suite.json"
        binding = self.plan["features"][0]["assertions"][0]
        suite_path.write_text(json.dumps({
            "suite_revision": "s1", "fixture_revision": "f1",
            "cases": [{"id": "TC-C01", "steps": [*binding["checkpoint"]["after"], binding["step"]]}],
        }))
        frozen = {
            **self.baseline, "mandatory_cases": ["TC-C01"],
            "suite_revision": "s1", "fixture_revision": "f1",
            "suite_digests": {hashlib.sha256(suite_path.read_bytes()).hexdigest(): ["TC-C01"]},
        }
        return frozen, suite_path

    def review_handoffs(self, frozen):
        self.step("analysis")
        confirmed = self.confirm()
        slices = {
            "scenes": {"scenes": self.plan["scenes"]},
            "features": {key: self.plan[key] for key in ("features", "changes")},
            "tasks": {"tasks": self.plan["tasks"]},
        }
        for name, plan_slice in slices.items():
            self.step(name)
            artifact = {"requirements": plan_slice}
            if name == "tasks":
                artifact = {**frozen, **artifact}
            s1_review.record_handoff(self.record, name, artifact)
        return confirmed

    def test_handoff_receipts_pin_outputs_and_browser_targets(self):
        frozen, suite_path = self.frozen_inputs()
        self.review_handoffs(frozen)
        frozen["s1_handoff_checks"] = True
        approved = s1_review.for_init(self.record, self.project, frozen)
        self.assertTrue(all("artifact_sha256" in step for step in approved["steps"][1:]))
        for step, edit in (
            ("scenes", lambda value: value["requirements"]["scenes"][0].update(actor="另一角色")),
            ("features", lambda value: value["requirements"]["features"][0]["assertions"][0]["checkpoint"].update(index=99)),
            ("tasks", lambda value: value["requirements"]["tasks"][0]["paths"].append("extra.js")),
            ("tasks", lambda value: value.update(acceptance_browsers=["chromium", "firefox"])),
        ):
            with self.subTest(step=step):
                changed = deepcopy(frozen)
                edit(changed)
                parser, args = self.init_args(changed, suite_path)
                with self.assertRaisesRegex(ValueError, f"{step} 交接产物不符"):
                    cli.invoke(args, parser)
                self.assertFalse((self.record.parent / "data" / "state.sqlite").exists())
        s1_review.record_handoff(self.record, "scenes", frozen)
        self.assertEqual(s1_review.for_init(self.record, self.project, frozen), approved)
        changed = deepcopy(frozen)
        changed["requirements"]["scenes"][0]["actor"] = "新确认的角色"
        s1_review.record_handoff(self.record, "scenes", changed)
        with self.assertRaisesRegex(ValueError, "尚未记录"):
            s1_review.for_init(self.record, self.project, changed)

    def init_args(self, frozen, suite_path):
        root = self.record.parent
        baseline_path = root / "baseline.json"
        baseline_path.write_text(json.dumps(frozen))
        parser = cli.build_parser()
        args = parser.parse_args([
            "--data", str(root / "data"), "init", "--project", str(self.project),
            "--baseline", str(baseline_path), "--suite", str(suite_path),
            "--s1-record", str(self.record), "--frontend", "static",
        ])
        return parser, args

    def test_public_s1_receipt_preserves_reference_metadata_in_frozen_baseline(self):
        frozen, suite_path = self.frozen_inputs()
        root = self.record.parent
        baseline_path = root / "baseline.json"
        baseline_path.write_text(json.dumps(frozen), encoding="utf-8")
        packets = {}
        store = partial(Store, reservation_root=root / "reservations")

        def invoke(*words):
            parser = cli.build_parser()
            args = parser.parse_args(["--data", str(root / "data"), *map(str, words)])
            with patch.object(cli, "Store", store):
                result = cli.invoke(args, parser)
            if args.action == "skill":
                packets[args.step] = result
            return result

        self.record = record_s1(invoke, root, self.project, baseline_path)
        review = json.loads(self.record.read_text(encoding="utf-8"))
        self.assertEqual([receipt["step"] for receipt in review["steps"]], list(s1_review.STEPS))
        for receipt in review["steps"]:
            packet = packets[receipt["step"]]
            self.assertTrue(receipt["references"])
            for field in ("digest", "source_path", "references"):
                self.assertEqual(receipt[field], packet[field])
            self.assertNotIn("content", receipt)
            self.assertTrue(all(set(ref) == {"ref", "path", "section", "title", "sha256"}
                                for ref in receipt["references"]))

        run = invoke("init", "--project", self.project, "--baseline", baseline_path,
                     "--s1-record", self.record, "--suite", suite_path, "--frontend", "static")
        checked = invoke("requirements-check", run["id"])
        snapshot = json.loads(Path(checked["baseline"]).read_text(encoding="utf-8"))
        self.assertEqual(run["baseline"]["s1_review"], review)
        self.assertEqual(snapshot["s1_review"], review)

    def test_init_rejects_downgrade_from_confirmed_human_acceptance(self):
        frozen, suite_path = self.frozen_inputs()
        frozen["human_acceptance"] = True
        self.contract["human_acceptance"] = True
        self.plan["approval"] = "确认本轮必须执行人工验收"
        self.review_handoffs(frozen)
        before = self.record.read_bytes()
        data = self.record.parent / "data"
        store = partial(Store, reservation_root=self.record.parent / "reservations")

        with patch.object(cli, "Store", store):
            for altered in ({**frozen, "human_acceptance": False},
                            {key: value for key, value in frozen.items() if key != "human_acceptance"}):
                parser, args = self.init_args(altered, suite_path)
                with self.assertRaisesRegex(ValueError, "人工验收"):
                    cli.invoke(args, parser)
                self.assertFalse((data / "state.sqlite").exists())
                self.assertEqual(self.record.read_bytes(), before)

            parser, args = self.init_args(frozen, suite_path)
            run = cli.invoke(args, parser)
        review = run["baseline"]["s1_review"]
        self.assertIs(run["baseline"]["human_acceptance"], True)
        self.assertIs(review["confirmation"]["human_acceptance"], True)
        handoff = s1_review._handoff_value("tasks", frozen)
        self.assertIs(handoff["human_acceptance"], True)
        self.assertEqual(review["steps"][-1]["artifact_sha256"], s1_review._contract_digest(handoff))

    def test_tasks_handoff_must_use_confirmed_human_acceptance(self):
        frozen, _ = self.frozen_inputs()
        self.contract["human_acceptance"] = True
        with self.assertRaisesRegex(ValueError, "人工验收"):
            self.review_handoffs(frozen)
        self.assertNotIn("artifact_sha256", json.loads(self.record.read_text())["steps"][-1])
        with self.assertRaisesRegex(ValueError, "布尔"):
            s1_review.confirm(self.record, self.project, {**self.contract, "human_acceptance": 1},
                              self.plan["approval"])

    def test_legacy_unbound_confirmation_requires_confirmation_before_init(self):
        frozen, suite_path = self.frozen_inputs()
        self.review_handoffs(frozen)
        old = json.loads(self.record.read_text())
        old["confirmation"].pop("human_acceptance", None)
        self.record.write_text(json.dumps(old))
        parser, args = self.init_args(frozen, suite_path)
        store = partial(Store, reservation_root=self.record.parent / "reservations")
        with patch.object(cli, "Store", store):
            with self.assertRaisesRegex(ValueError, "s1-confirm"):
                cli.invoke(args, parser)
        self.assertFalse((self.record.parent / "data" / "state.sqlite").exists())

    def test_init_rejects_frozen_suite_checkpoint_before_registering(self):
        frozen, suite_path = self.frozen_inputs()
        self.plan["features"][0]["assertions"][0]["checkpoint"]["index"] = 99
        self.review_handoffs(frozen)
        parser, args = self.init_args(frozen, suite_path)
        with self.assertRaisesRegex(ValueError, "不含全部约定验收时点"):
            cli.invoke(args, parser)
        self.assertFalse((self.record.parent / "data" / "state.sqlite").exists())
        self.assertFalse((self.project / "memory" / "baselines").exists())

    def test_original_init_without_run_id_recovers_after_precommit_reservation(self):
        frozen, suite_path = self.frozen_inputs()
        confirmed = self.review_handoffs(frozen)
        parser, args = self.init_args(frozen, suite_path)
        root = self.record.parent
        data = root / "data"
        actual_record = revision_registry.record_revision

        def record_then_interrupt(*args, **kwargs):
            actual_record(*args, **kwargs)
            raise RuntimeError("interrupted before SQLite insertion")

        store = partial(Store, reservation_root=root / "reservations")
        with patch.object(cli, "Store", store):
            with patch.object(revision_registry, "record_revision", side_effect=record_then_interrupt):
                with self.assertRaisesRegex(RuntimeError, "before SQLite"):
                    cli.invoke(args, parser)
            self.assertEqual(store(data, reserve=False).runs(), [])
            restored = cli.invoke(args, parser)
        self.assertEqual(restored["id"], confirmed["review_id"])
        self.assertTrue((self.project / "docs/需求台账.md").is_file())
