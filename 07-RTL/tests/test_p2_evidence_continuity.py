import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, dispatch, requirements, requirements_history, requirements_run, verification
from loop import __main__ as cli
from loop.state import Store, pack, unpack


class PromotionEvidenceTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.project = self.root / "project"
        prototype = self.project / "prototype"
        prototype.mkdir(parents=True)
        (prototype / "index.html").write_text("<h1>Standard</h1>", encoding="utf-8")
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")
        self.run = "R"
        self.case = "TC-C01"
        reference = Path(__file__).resolve().parents[1] / (
            "skills/requirement-architecture/references/requirements-example.json"
        )
        plan = requirements.normalize_plan(json.loads(reference.read_text(encoding="utf-8")), [self.case])
        self.context = {
            "suite_revision": "s1", "fixture_revision": "f1",
            "suite_digest": hashlib.sha256(b"frozen case").hexdigest(),
        }
        self.store.create_run(self.project, prototype, {
            "revision": "rev", "requirements": plan, "mandatory_cases": [self.case],
            "suite_revision": "s1", "fixture_revision": "f1",
            "suite_digests": {self.context["suite_digest"]: [self.case]},
        }, project_id="P", run_id=self.run)
        self.parent = artifacts.initialize_repo(self.store, self.run, 1)
        requirements_run.prepare(self.store, self.run, 1)
        self.snapshot = self.project / requirements_run.snapshot_relative_path(self.run)
        self.ledger = self.project / requirements_history.LEDGER
        self.attempt = dispatch.reserve(self.store, self.run, 1, "C")["id"]
        workspace = artifacts.make_worktree(self.store, self.run, self.attempt)
        dispatch.bind_workspace(self.store, self.run, 1, self.attempt, workspace)
        dispatch.start(self.store, self.run, 1, self.attempt, "scene")
        (workspace / "index.html").write_text("<h1>Compact</h1>", encoding="utf-8")
        artifacts.git(workspace, "add", "index.html")
        artifacts.git(workspace, "commit", "-qm", "Restore layout preference")
        self.result = artifacts.capture_submission(self.store, self.run, self.attempt, "scene")
        self.report = self.root / "self-report.json"
        self.report.write_text('{"case": "TC-C01", "status": "pass"}\n', encoding="utf-8")
        proof = self._record_self()
        self.archived = Path(proof["context"]["evidence"])
        self.proof_id = proof["id"]
        dispatch.submit(self.store, self.run, 1, self.attempt, self.result)
        self.repo = artifacts.repo_path(self.store, self.run)

    def _record_self(self):
        return verification.record(
            self.store, self.run, 1, "self", self.result["commit"], "scene",
            {self.case: {"passed": True, "actual": "Saved layout survives reload"}},
            {**self.context, "attempt_id": self.attempt, "evidence": str(self.report)},
        )

    def _stage(self):
        return artifacts.stage(self.store, self.run, 1, [self.attempt])

    def _promote(self, staged):
        return artifacts.promote(self.store, self.run, 1, staged["operation_id"])

    def _prepare_cli(self):
        parser = cli.build_parser()
        args = parser.parse_args(["--data", str(self.store.data_root), "prepare", self.run, "--epoch", "1"])
        with patch.object(cli, "Store", return_value=self.store):
            return cli.invoke(args, parser)

    def test_stage_and_promote_require_s1_snapshot_ledger_and_attempt_binding(self):
        self.snapshot.unlink()
        with self.assertRaisesRegex(ValueError, "S1 冻结基线文件缺失"):
            self._stage()
        self._prepare_cli()
        staged = self._stage()
        self.ledger.unlink()
        with self.assertRaisesRegex(ValueError, "需求台账尚未落盘"):
            self._promote(staged)
        self._prepare_cli()

        original = self.store.attempt(self.attempt)["input"]
        stale = {**original, "requirements": {**original["requirements"], "revision": "other"}}
        with self.store.transaction() as db:
            db.execute("UPDATE attempts SET input=? WHERE id=?", (pack(stale), self.attempt))
        with self.assertRaisesRegex(ValueError, "尝试绑定的 S1 冻结契约与本轮不符"):
            self._promote(staged)
        self.assertEqual(artifacts.rev(self.repo, artifacts.CANDIDATE_REF), self.parent)
        with self.store.transaction() as db:
            db.execute("UPDATE attempts SET input=? WHERE id=?", (pack(original), self.attempt))
        self.assertEqual(self._promote(staged)["candidate"], staged["candidate"])

    def test_promotion_rechecks_archived_self_evidence_and_its_actor(self):
        staged = self._stage()
        self.archived.write_text("altered report", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "缺少通过的 self 证据"):
            self._promote(staged)
        self.archived.write_bytes(self.report.read_bytes())
        with self.store.transaction() as db:
            db.execute("UPDATE evidence SET actor='another-worker' WHERE id=?", (self.proof_id,))
        with self.assertRaisesRegex(ValueError, "缺少通过的 self 证据"):
            self._promote(staged)
        self.assertEqual(self.store.run(self.run)["candidate"], self.parent)
        self.assertEqual(artifacts.rev(self.repo, artifacts.CANDIDATE_REF), self.parent)
        self._record_self()
        self.assertEqual(self._promote(staged)["candidate"], staged["candidate"])

    def test_old_report_with_conflicting_archive_digest_cannot_authorize_promotion(self):
        staged = self._stage()
        with self.store.transaction() as db:
            context = unpack(db.execute("SELECT context FROM evidence WHERE id=?", (self.proof_id,)).fetchone()[0])
            context["report_sha256"] = hashlib.sha256(b"different report parsed by CLI").hexdigest()
            db.execute("UPDATE evidence SET context=? WHERE id=?", (pack(context), self.proof_id))
        with self.assertRaisesRegex(ValueError, "缺少通过的 self 证据"):
            self._promote(staged)
        self._record_self()
        self.assertEqual(self._promote(staged)["candidate"], staged["candidate"])

    def test_recovery_rechecks_evidence_and_repairs_s1_after_git_ref_advanced(self):
        staged = self._stage()
        with patch.object(artifacts, "finish_operation", side_effect=RuntimeError("interrupted after ref moved")):
            with self.assertRaisesRegex(RuntimeError, "interrupted after ref moved"):
                self._promote(staged)
        self.assertEqual(self.store.run(self.run)["candidate"], self.parent)
        self.assertEqual(artifacts.rev(self.repo, artifacts.CANDIDATE_REF), staged["candidate"])
        self.snapshot.unlink()
        self.ledger.unlink()
        self.archived.unlink()

        with self.assertRaisesRegex(ValueError, "候选引用与台账不符"):
            artifacts.initialize_repo(self.store, self.run, 1)
        with self.assertRaisesRegex(ValueError, "S1 冻结基线文件缺失"):
            self._promote(staged)
        self.assertEqual(self._prepare_cli(), self.parent)
        self.assertTrue(self.snapshot.is_file())
        self.assertTrue(self.ledger.is_file())
        self.assertEqual(self.store.operation(self.run, staged["operation_id"])["status"], "intent")
        with self.assertRaisesRegex(ValueError, "缺少通过的 self 证据"):
            self._promote(staged)
        self._record_self()
        receipt = self._promote(staged)
        self.assertEqual(receipt["candidate"], staged["candidate"])
        self.assertEqual(self._promote(staged), receipt)

    def test_prepare_cannot_bypass_a_ref_move_without_a_matching_intent(self):
        staged = self._stage()
        artifacts.git(self.repo, "update-ref", artifacts.CANDIDATE_REF, self.result["commit"], self.parent)
        self.snapshot.unlink()
        with self.assertRaisesRegex(ValueError, "候选引用与台账不符"):
            self._prepare_cli()
        self.assertFalse(self.snapshot.exists())
        self.assertEqual(self.store.operation(self.run, staged["operation_id"])["status"], "intent")
