from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, delivery, dispatch, lifecycle, verification
from loop.state import LoopError, Store, begin_operation


class LandingRollbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.output = self.root / "public"
        self.source = self.project / "prototype"
        self.source.mkdir(parents=True)
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")
        self.baseline = {
            "frontend": "static", "mandatory_cases": ["final"],
            "suite_revision": "S1", "fixture_revision": "F1",
        }
        self.old = self._place("old", "accepted", replace=False)
        verification.record(
            self.store, "old", 1, "delivery", self.old["candidate"], "reviewer",
            {"final": {"status": "pass", "actual": "Observed the accepted page"}},
            {
                "operation_id": self.old["operation_id"], "path": self.old["entry"], "url": self.old["url"],
                "browser": "chromium", "suite_revision": "S1", "fixture_revision": "F1",
            },
        )
        self.old_receipt = delivery.finalize(self.store, "old", 1, self.old["operation_id"], "master")
        self.store.release_reservation("old")
        self.new = self._place("new", "rejected", replace=True)
        self.operation = self.new["operation_id"]
        self.backup = Path(self.store.operation("new", self.operation)["payload"]["previous"])

    def _place(self, run_id, text, *, replace):
        (self.source / "index.html").write_text(f"<h1>{text}</h1>")
        self.store.create_run(
            self.project, self.output, {**self.baseline, "revision": run_id},
            run_id=run_id, project_id="P",
        )
        artifacts.initialize_repo(self.store, run_id, 1)
        return delivery.land(self.store, run_id, 1, allow_replace=replace)

    def _rollback(self, epoch=1):
        return delivery.rollback_landing(self.store, "new", epoch, self.operation, "Rejected by owner")

    def _assert_recovered(self, receipt):
        self.assertEqual(receipt["status"], "rolled_back")
        self.assertEqual((self.output / "index.html").read_text(), "<h1>accepted</h1>")
        self.assertEqual((Path(receipt["rejected_archive"]) / "index.html").read_text(), "<h1>rejected</h1>")
        self.assertEqual(self.store.operation("new", self.operation)["status"], "superseded")
        self.assertEqual(
            delivery.finalize(self.store, "old", 1, self.old["operation_id"], "master"), self.old_receipt,
        )

    def test_rollback_after_rejection_and_supersede_restores_old_entry(self):
        lifecycle.supersede(self.store, "new", 1, reason="rejected", approval="owner decision")
        self.store.release_reservation("new")
        receipt = self._rollback()
        self._assert_recovered(receipt)
        self.assertEqual(receipt, self._rollback())

    def test_interruption_after_moving_rejected_output_recovers_under_new_epoch(self):
        original_rename = Path.rename
        stage_id = "merge-pending-probe"
        with self.store.transaction() as db:
            begin_operation(db, "new", stage_id, "integration", {"parent": self.new["candidate"]})

        def interrupted(path, target):
            if path == self.backup and target == self.output:
                raise OSError("interrupted before restoring the previous entry")
            return original_rename(path, target)

        with patch.object(Path, "rename", interrupted), self.assertRaisesRegex(OSError, "interrupted"):
            self._rollback()
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ValueError, "rollback-landing"):
            delivery.land(self.store, "new", 1)
        with self.assertRaisesRegex(ValueError, "rollback-landing"):
            artifacts.promote(self.store, "new", 1, stage_id)
        epoch = self.store.take_over("new", 1)["epoch"]
        receipt = self._rollback(epoch)
        self._assert_recovered(receipt)
        with self.assertRaisesRegex(ValueError, "已驳回"):
            delivery.land(self.store, "new", epoch, allow_replace=True)

    def test_unknown_output_and_tampered_backup_never_move_directories(self):
        live = self.output / "index.html"
        live.write_text("unknown output")
        with self.assertRaisesRegex(ValueError, "实际落盘"):
            self._rollback()
        self.assertEqual(live.read_text(), "unknown output")
        live.write_text("<h1>rejected</h1>")
        (self.backup / "index.html").write_text("unknown backup")
        with self.assertRaisesRegex(ValueError, "备份"):
            self._rollback()
        self.assertEqual(live.read_text(), "<h1>rejected</h1>")
        self.assertIsNone(self.store.operation("new", f"rollback-{self.operation}"))

    def test_interrupted_terminal_rollback_holds_path_across_ledgers(self):
        lifecycle.supersede(self.store, "new", 1, reason="rejected", approval="owner decision")
        self.store.release_reservation("new")
        original_rename = Path.rename

        def interrupted(path, target):
            if path == self.backup and target == self.output:
                raise OSError("interrupted with official entry missing")
            return original_rename(path, target)

        with patch.object(Path, "rename", interrupted), self.assertRaisesRegex(OSError, "interrupted"):
            self._rollback()
        self.assertFalse(self.output.exists())
        peer = Store(self.root / "peer-data", reservation_root=self.store.reservation_root)
        with self.assertRaisesRegex(LoopError, "reserved by active run new"):
            peer.create_run(self.project, self.output, self.baseline, project_id="P", run_id="peer")
        another = self.root / "other-project"
        another.mkdir()
        peer.create_run(another, another / "prototype", self.baseline, project_id="OTHER", run_id="peer")
        with self.assertRaisesRegex(LoopError, "reserved by active run new"):
            peer.rebind_project("OTHER", self.project, self.output, epoch=1)
        self._assert_recovered(self._rollback())
        peer.rebind_project("OTHER", self.project, self.output, epoch=1)

    def test_initial_imported_static_seed_is_not_a_prior_accepted_delivery(self):
        project = self.root / "seed-project"
        output = project / "prototype"
        output.mkdir(parents=True)
        (output / "index.html").write_text("<h1>Imported seed</h1>")
        self.store.create_run(project, output, self.baseline, run_id="seed", project_id="SEED")
        artifacts.initialize_repo(self.store, "seed", 1)
        self.store.add_task("seed", 1, {"id": "A", "paths": ["index.html"], "cases": ["TC-A"]})
        item = dispatch.reserve(self.store, "seed", 1, "A")
        workspace = artifacts.make_worktree(self.store, "seed", item["id"])
        dispatch.bind_workspace(self.store, "seed", 1, item["id"], workspace)
        dispatch.start(self.store, "seed", 1, item["id"], "worker")
        (workspace / "index.html").write_text("<h1>Pending</h1>")
        artifacts.git(workspace, "add", "--", "index.html")
        artifacts.git(workspace, "commit", "--quiet", "-m", "pending")
        result = artifacts.capture_submission(self.store, "seed", item["id"], "worker")
        dispatch.submit(self.store, "seed", 1, item["id"], result)
        verification.record(
            self.store, "seed", 1, "self", result["commit"], "worker",
            {"TC-A": {"status": "pass", "actual": "Observed the pending page"}},
            {"attempt_id": item["id"], "suite_revision": "S1", "fixture_revision": "F1"},
        )
        staged = artifacts.stage(self.store, "seed", 1, [item["id"]])
        artifacts.promote(self.store, "seed", 1, staged["operation_id"])
        landed = delivery.land(self.store, "seed", 1, allow_replace=True)
        with self.assertRaisesRegex(ValueError, "找不到原入口对应的已验收交付"):
            delivery.rollback_landing(self.store, "seed", 1, landed["operation_id"], "rejected")
        self.assertEqual((output / "index.html").read_text(), "<h1>Pending</h1>")
        backup = Path(self.store.operation("seed", landed["operation_id"])["payload"]["previous"])
        self.assertEqual((backup / "index.html").read_text(), "<h1>Imported seed</h1>")
