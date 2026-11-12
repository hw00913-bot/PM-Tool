from argparse import Namespace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, browser, delivery, verification
from loop.__main__ import record_evidence
from loop.state import Store


class AcceptanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        prototype = self.project / "prototype"
        prototype.mkdir(parents=True)
        (prototype / "index.html").write_text("<h1>Prototype</h1>", encoding="utf-8")
        self.output = self.root / "output"
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")
        self.epoch = 1

    def _land(self, **baseline):
        self.run = self.store.create_run(
            self.project, self.output,
            {"revision": "R1", "mandatory_cases": ["A", "B"], "human_acceptance": True, **baseline},
        )["id"]
        artifacts.initialize_repo(self.store, self.run, self.epoch)
        self.landed = delivery.land(self.store, self.run, self.epoch)

    @staticmethod
    def _suite_digest(case_ids, expected="Prototype"):
        suite = {
            "fixture_revision": "fixtures-v1",
            "suite_revision": "suite-v1",
            "cases": [
                {"id": case_id, "steps": [{"op": "text", "selector": "h1", "value": expected}]}
                for case_id in case_ids
            ],
        }
        return hashlib.sha256(json.dumps(suite).encode()).hexdigest()

    def _report(self, name, case_ids=("A", "B"), *, context_fields=None, **overrides):
        context = self.root / "context.json"
        context.write_text(json.dumps({"operation_id": self.landed["operation_id"], **(context_fields or {})}),
                           encoding="utf-8")
        report = self.root / f"{name}.json"
        report.write_text(json.dumps({
            "protocol": "file:",
            "path": self.landed["entry"],
            "entry": self.landed["url"],
            "browser": "TestBrowser 1",
            "fixture_revision": "fixtures-v1",
            "suite_revision": "suite-v1",
            "asset_digest": browser.asset_digest(self.output),
            "suite_digest": self._suite_digest(case_ids),
            "cases": {case: {"status": "pass", "executed": ["text h1"], "errors": []} for case in case_ids},
            **overrides,
        }), encoding="utf-8")
        args = Namespace(
            report=report, context=context, run=self.run, epoch=self.epoch,
            kind="delivery", target=self.landed["candidate"], actor="reviewer",
        )
        return record_evidence(self.store, args)

    def _finalize(self, actor="master", approval=None):
        return delivery.finalize(
            self.store, self.run, self.epoch, self.landed["operation_id"], actor, approval,
        )

    def test_browser_report_rejects_conflicting_archive_context(self):
        self._land()
        other = self.root / "other.json"
        other.write_text('{"cases": {"B": {"status": "fail"}}}', encoding="utf-8")
        wrong_digest = hashlib.sha256(other.read_bytes()).hexdigest()
        for field, value in (("evidence", str(other)), ("report_path", str(other)), ("report_digest", wrong_digest),
                             ("evidence_sha256", wrong_digest), ("feature_routes", {})):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, f"报告冲突：{field}"):
                self._report("accepted", context_fields={field: value})
        with self.store.transaction(immediate=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM evidence WHERE run_id=?", (self.run,)).fetchone()[0], 0)
        proof = self._report("accepted")
        original = (self.root / "accepted.json").read_bytes()
        self.assertEqual(Path(proof["context"]["evidence"]).read_bytes(), original)
        self.assertEqual(proof["context"]["report_sha256"], proof["context"]["evidence_sha256"])

    def test_browser_report_archives_the_bytes_that_were_parsed(self):
        self._land()
        original_bytes = []
        record = verification.record

        def replace_after_parse(*args, **kwargs):
            report = self.root / "racing-report.json"
            original_bytes.append(report.read_bytes())
            report.write_text('{"cases": {"B": {"status": "fail"}}}', encoding="utf-8")
            return record(*args, **kwargs)

        with patch.object(verification, "record", side_effect=replace_after_parse):
            proof = self._report("racing-report")
        archive = Path(proof["context"]["evidence"])
        self.assertEqual(archive.read_bytes(), original_bytes[0])
        self.assertEqual(proof["context"]["evidence_sha256"], hashlib.sha256(original_bytes[0]).hexdigest())

    def test_frozen_digest_allows_registered_slices_and_rejects_changed_input(self):
        all_digest = self._suite_digest(("A", "B"))
        a_digest = self._suite_digest(("A",))
        b_digest = self._suite_digest(("B",))
        baseline = {
            "fixture_revision": "fixtures-v1",
            "suite_revision": "suite-v1",
            "suite_digests": {all_digest: ["A", "B"], a_digest: ["A"], b_digest: ["B"]},
        }
        verification.validate_input_baseline({**baseline, "mandatory_cases": ["A", "B"]})
        with self.assertRaisesRegex(ValueError, "未覆盖"):
            verification.validate_input_baseline({**baseline, "mandatory_cases": ["C"]})
        self._land(**baseline)
        with self.assertRaisesRegex(ValueError, "冻结"):
            self._report("changed-assertion", suite_digest=self._suite_digest(("A", "B"), expected="Different"))
        with self.assertRaisesRegex(ValueError, "冻结"):
            self._report("wrong-fixtures", fixture_revision="fixtures-v0")
        with self.assertRaisesRegex(ValueError, "冻结"):
            self._report("wrong-cases", ("A",), suite_digest=b_digest)
        proof_a = self._report("slice-a", ("A",))
        with self.assertRaisesRegex(ValueError, "缺少通过的"):
            self._finalize(approval="approved")
        proof_b = self._report("slice-b", ("B",))
        receipt = self._finalize(approval="approved")
        self.assertEqual(receipt["evidence_ids"], [proof_a["id"], proof_b["id"]])

    def test_known_supplemental_delivery_failure_requires_passing_retest(self):
        digests = {
            self._suite_digest(("A", "B")): ["A", "B"],
            self._suite_digest(("C",)): ["C"],
        }
        self._land(acceptance_browsers=["chromium"], suite_digests=digests,
                   suite_revision="suite-v1", fixture_revision="fixtures-v1")
        passing = self._report("mandatory", browser="chromium 1")
        failed = self._report("supplemental-failure", ("C",), browser="chromium 1",
                              cases={"C": {"status": "fail", "executed": [], "errors": ["Text differs"]}})
        with self.assertRaisesRegex(ValueError, r"chromium.*delivery.*C"):
            self._finalize(approval="approved")
        self._report("outside-acceptance-scope", ("C",), browser="firefox 1")
        with self.assertRaisesRegex(ValueError, r"chromium.*delivery.*C"):
            self._finalize(approval="approved")
        recovered = self._report("supplemental-retest", ("C",), browser="chromium 2")
        self.assertEqual(self._finalize(approval="approved")["evidence_ids"],
                         [passing["id"], failed["id"], recovered["id"]])

    def test_each_approved_browser_requires_its_own_passing_evidence(self):
        self._land(acceptance_browsers=["chromium", "firefox"])
        first = self._report("chromium-pass", browser="chromium 1")
        with self.assertRaisesRegex(ValueError, r"firefox.*缺少通过的"):
            self._finalize(approval="approved")
        second = self._report("firefox-pass", browser="firefox 2")
        self.assertEqual(self._finalize(approval="approved")["evidence_ids"], [first["id"], second["id"]])

    def test_legacy_delivery_keeps_first_browser_family_and_allows_new_version(self):
        self._land()
        self._report("first-failure", browser="TestBrowser 1", cases={
            "A": {"status": "fail", "executed": [], "errors": ["Text differs"]},
            "B": {"status": "pass", "executed": ["text h1"], "errors": []},
        })
        self._report("other-browser", browser="OtherBrowser 1")
        with self.assertRaisesRegex(ValueError, r"testbrowser.*delivery.*A"):
            self._finalize(approval="approved")
        self._report("same-engine-retest", browser="TestBrowser 2")
        self.assertEqual(self._finalize(approval="approved")["status"], "delivered")

    def test_replacement_evidence_repairs_prior_memory_and_archive_after_interrupt(self):
        self._land()
        first = self._report("first")
        with patch.object(delivery, "finish_operation", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._finalize(approval="user approval")
        self.assertEqual(self.store.run(self.run)["status"], "active")
        operation_id = self.landed["operation_id"]
        archive = artifacts.run_directory(self.store, self.run) / "deliveries" / f"{operation_id}.json"
        memory = self.project / "memory" / "changes" / f"{operation_id.replace('delivery-', 'CHG-', 1)}.md"
        self.assertIn(first["id"], archive.read_text())
        self.assertIn(first["id"], memory.read_text())
        with self.assertRaisesRegex(ValueError, "部分回写"):
            delivery.cancel_acceptance(self.store, self.run, self.epoch, operation_id, "new failure")

        Path(first["context"]["evidence"]).unlink()
        self.epoch = self.store.take_over(self.run, self.epoch)["epoch"]
        with self.assertRaisesRegex(ValueError, "缺少通过的"):
            self._finalize("successor")
        replacement = self._report("replacement", browser="TestBrowser 2")
        acceptance = next(item for item in self.store.operations(self.run) if item["kind"] == "acceptance")
        archive_before = archive.read_bytes()
        memory_before = memory.read_bytes()
        with self.assertRaisesRegex(ValueError, "人工验收依据与已登记的验收决定不符"):
            self._finalize("successor", approval="replacement approval")
        self.assertEqual(self.store.operation(self.run, acceptance["id"]), acceptance)
        self.assertEqual(archive.read_bytes(), archive_before)
        self.assertEqual(memory.read_bytes(), memory_before)

        receipt = self._finalize("successor", approval="user approval")
        self.assertEqual(receipt["evidence_ids"], [replacement["id"]])
        self.assertEqual(receipt["actor"], "master")
        self.assertEqual(receipt["approval"], "user approval")
        self.assertEqual(json.loads(archive.read_text())["receipt"], receipt)
        self.assertIn(replacement["id"], memory.read_text())
        self.assertNotIn(first["id"], memory.read_text())
        index = self.project / "docs" / "change-log.md"
        self.assertEqual(index.read_text().count(f"<!-- rtl:{operation_id} -->"), 1)
        self.assertEqual(receipt, self._finalize("successor"))

    def test_cancel_untouched_acceptance_keeps_history_and_requires_new_approval(self):
        self._land()
        self._report("first")
        with patch.object(delivery, "_write_memory", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._finalize(approval="withdrawn")
        operation_id = self.landed["operation_id"]
        original = next(item for item in self.store.operations(self.run) if item["kind"] == "acceptance")
        self.assertEqual(original["status"], "intent")
        with self.assertRaisesRegex(ValueError, "人工验收依据与已登记的验收决定不符"):
            self._finalize(approval="approved again")
        self.assertEqual(self.store.operation(self.run, original["id"]), original)
        self.assertFalse((self.project / "memory" / "changes").exists())

        canceled = delivery.cancel_acceptance(self.store, self.run, self.epoch, operation_id, "reopen review")
        self.assertEqual(canceled, delivery.cancel_acceptance(self.store, self.run, self.epoch, operation_id, "reopen review"))
        self.assertEqual(self.store.operation(self.run, canceled["operation_id"])["status"], "superseded")
        with self.assertRaisesRegex(ValueError, "人工验收"):
            self._finalize()
        receipt = self._finalize(approval="approved again")
        self.assertEqual(receipt["approval"], "approved again")
        acceptance = [item for item in self.store.operations(self.run) if item["kind"] == "acceptance"]
        self.assertEqual([item["status"] for item in acceptance], ["superseded", "done"])
        self.assertEqual(acceptance[0]["payload"]["receipt"]["approval"], "withdrawn")
        self.assertEqual(self._finalize(approval="approved again"), receipt)
        self.assertEqual(self._finalize(), receipt)
        with self.assertRaisesRegex(ValueError, "人工验收依据与已登记的验收决定不符"):
            self._finalize(approval="later approval")

    def test_cannot_add_approval_to_a_pending_automatic_decision(self):
        self._land(human_acceptance=False)
        self._report("first")
        with patch.object(delivery, "_write_memory", side_effect=RuntimeError("interrupted")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._finalize()
        original = next(item for item in self.store.operations(self.run) if item["kind"] == "acceptance")
        with self.assertRaisesRegex(ValueError, "人工验收依据与已登记的验收决定不符"):
            self._finalize(approval="new basis")
        self.assertEqual(self.store.operation(self.run, original["id"]), original)
        self.assertIsNone(self._finalize()["approval"])
