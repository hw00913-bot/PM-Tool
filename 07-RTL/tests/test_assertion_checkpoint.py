"""Regressions for the approved timing of feature acceptance assertions."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from loop import browser, requirements, verification
from loop.assertions import browser_case_plan, observable_checkpoint
from loop.state import pack


CASE = "TC-C01"
FEATURE = "FUNC-C01"
STANDARD = {"op": "text", "selector": "#result", "value": "标准"}
COMPACT = {**STANDARD, "value": "紧凑"}
SAVE = {"op": "click", "selector": "#save"}
RELOAD = {"op": "reload"}
RESET = {"op": "click", "selector": "#reset"}
EXAMPLE = Path(__file__).resolve().parents[1] / "skills/requirement-architecture/references/requirements-example.json"


def approved_plan(*, checkpoint=None):
    plan = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    binding = {"case": CASE, "step": STANDARD}
    if checkpoint is not None:
        binding["checkpoint"] = checkpoint
    plan["features"][0]["assertions"] = [binding]
    return plan


def suite_for(steps):
    return json.dumps({
        "fixture_revision": "f1", "suite_revision": "s1",
        "cases": [{"id": CASE, "steps": steps}],
    }, ensure_ascii=False)


def digest(suite):
    return hashlib.sha256(suite.encode("utf-8")).hexdigest()


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()

    def run_for(self, suite, checkpoint):
        return {"baseline": {
            "fixture_revision": "f1", "suite_revision": "s1",
            "suite_digests": {digest(suite): [CASE]}, "assertion_checkpoint_checks": True,
            "requirements": requirements.normalize_plan(
                approved_plan(checkpoint=checkpoint), [CASE], require_checkpoints=True,
            ),
        }}

    def archive(self, suite, steps):
        entry = self.root / "index.html"
        context = {
            "path": str(entry), "url": entry.as_uri(), "browser": "test-browser",
            "fixture_revision": "f1", "suite_revision": "s1", "asset_digest": "a" * 64,
            "suite_digest": digest(suite),
        }
        executed, assertions = browser_case_plan(steps)
        cases = {CASE: {"status": "pass", "executed": executed, "assertions": assertions, "errors": []}}
        report = self.root / "archived.json"
        report.write_text(json.dumps({
            **context, "protocol": "file:", "entry": context["url"], "suite_input": suite, "cases": cases,
        }), encoding="utf-8")
        context.update(evidence=str(report), evidence_sha256=hashlib.sha256(report.read_bytes()).hexdigest())
        return [({"cases": pack(cases)}, context)]

    def test_new_plan_requires_checkpoint_and_validates_its_shape(self):
        requirements.normalize_plan(approved_plan(), [CASE], require_assertions=True)
        with self.assertRaisesRegex(ValueError, f"{FEATURE}.*checkpoint"):
            requirements.normalize_plan(approved_plan(), [CASE], require_checkpoints=True)
        invalid = (
            {"index": True, "after": []},
            {"index": -1, "after": []},
            {"index": 0, "after": [SAVE]},
            {"index": 1, "after": [STANDARD]},
            {"index": 1, "after": [{"op": "select", "selector": "#layout"}]},
            {"index": 1, "after": [{"op": "reload", "selector": "#result"}]},
        )
        for checkpoint in invalid:
            with self.subTest(checkpoint=checkpoint):
                with self.assertRaisesRegex(ValueError, "验收时点|前置动作"):
                    requirements.normalize_plan(approved_plan(checkpoint=checkpoint), [CASE], require_checkpoints=True)
        plan = requirements.normalize_plan(
            approved_plan(checkpoint={"index": 2, "after": [SAVE, RELOAD]}), [CASE], require_checkpoints=True,
        )
        self.assertIn("时点", requirements.render_plan(plan, "r1"))

    def test_a_passing_pre_save_assertion_cannot_prove_the_approved_after_save_result(self):
        steps = [STANDARD, SAVE, RELOAD, COMPACT]
        suite = suite_for(steps)
        run = self.run_for(suite, {"index": 3, "after": [SAVE, RELOAD]})
        proof = self.archive(suite, steps)
        with self.assertRaisesRegex(ValueError, f"{FEATURE}.*约定验收时点"):
            verification.require_feature_assertions(run, proof)
        # This same evidence matches the older, unpositioned predicate policy.
        legacy = run["baseline"]
        legacy.pop("assertion_checkpoint_checks")
        legacy["change_assertion_checks"] = True
        legacy["requirements"]["features"][0]["assertions"][0].pop("checkpoint")
        verification.require_feature_assertions(run, proof)

    def test_the_exact_occurrence_and_entire_preceding_action_sequence_are_checked(self):
        correct = [STANDARD, SAVE, RELOAD, STANDARD]
        cases = (
            (correct, {"index": 3, "after": [SAVE, RELOAD]}, True),
            (correct, {"index": 0, "after": []}, True),
            (correct, {"index": 3, "after": []}, False),
            (correct, {"index": 0, "after": [SAVE]}, False),
            (correct, {"index": 3, "after": [RELOAD, SAVE]}, False),
            ([SAVE, RESET, RELOAD, STANDARD], {"index": 3, "after": [SAVE, RELOAD]}, False),
            ([SAVE, RELOAD, COMPACT, STANDARD], {"index": 2, "after": [SAVE, RELOAD]}, False),
        )
        for steps, checkpoint, accepted in cases:
            with self.subTest(checkpoint=checkpoint, steps=steps):
                suite = suite_for(steps)
                if len(checkpoint["after"]) > checkpoint["index"]:
                    with self.assertRaises(ValueError):
                        self.run_for(suite, checkpoint)
                    continue
                run = self.run_for(suite, checkpoint)
                evidence = self.archive(suite, steps)
                if accepted:
                    verification.require_feature_assertions(run, evidence)
                else:
                    with self.assertRaisesRegex(ValueError, "约定验收时点"):
                        verification.require_feature_assertions(run, evidence)

    def test_checkpoint_context_ignores_only_index_movement(self):
        start = {"case": CASE, "step": STANDARD, "checkpoint": {"index": 2, "after": [SAVE, RELOAD]}}
        later = {**start, "checkpoint": {"index": 3, "after": [SAVE, RELOAD]}}
        before = {**start, "checkpoint": {"index": 0, "after": []}}
        self.assertEqual(observable_checkpoint(start), observable_checkpoint(later))
        self.assertNotEqual(observable_checkpoint(start), observable_checkpoint(before))
        self.assertIsNone(observable_checkpoint({"case": CASE, "step": STANDARD}))

    @unittest.skipUnless(os.environ.get("RTL_TEST_BROWSER") == "1", "opt in with RTL_TEST_BROWSER=1")
    def test_real_browser_rejects_old_result_at_the_approved_checkpoint(self):
        prototype = self.root / "prototype"
        prototype.mkdir()
        entry = prototype / "index.html"
        entry.write_text(
            '<!doctype html><meta charset="utf-8"><button id="save">保存</button><p id="result"></p>'
            '<script>const result=document.querySelector("#result");'
            'result.textContent=localStorage.layout || "标准";'
            'document.querySelector("#save").onclick=()=>{localStorage.layout="紧凑";};</script>',
            encoding="utf-8",
        )
        actual = suite_for([STANDARD, SAVE, RELOAD, COMPACT])
        wanted = suite_for([STANDARD, SAVE, RELOAD, STANDARD])
        old_suite, new_suite = self.root / "old.json", self.root / "new.json"
        old_suite.write_text(actual, encoding="utf-8")
        new_suite.write_text(wanted, encoding="utf-8")
        old_report, new_report = self.root / "old-proof.json", self.root / "new-proof.json"
        self.assertEqual(browser.run(entry, old_suite, old_report), 0)
        self.assertNotEqual(browser.run(entry, new_suite, new_report), 0)
        old = json.loads(old_report.read_text(encoding="utf-8"))
        new = json.loads(new_report.read_text(encoding="utf-8"))
        self.assertEqual(new["cases"][CASE]["status"], "fail")
        context = {**old, "url": old["entry"], "evidence": str(old_report),
                   "evidence_sha256": hashlib.sha256(old_report.read_bytes()).hexdigest()}
        run = self.run_for(actual, {"index": 3, "after": [SAVE, RELOAD]})
        with self.assertRaisesRegex(ValueError, "约定验收时点"):
            verification.require_feature_assertions(run, [({"cases": pack(old["cases"])}, context)])
