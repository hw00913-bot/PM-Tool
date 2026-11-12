import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop.__main__ import baseline, build_parser, invoke
from loop import __main__ as cli
from loop import artifacts, delivery, dispatch, lifecycle, verification
from loop.state import LoopError, Store, begin_operation


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        self.store = Store(root / "data", reservation_root=root / "reservations")
        self.project = root / "project"
        self.output = self.project / "prototype"
        self.output.mkdir(parents=True)
        (self.output / "index.html").write_text("accepted seed")
        self.baseline = {"revision": "old", "mandatory_cases": ["TC-1"]}
        self.store.create_run(self.project, self.output, self.baseline, project_id="P", run_id="old")
        self.candidate = artifacts.initialize_repo(self.store, "old", 1)

    def replace(self, *, stopped=False):
        return lifecycle.supersede(
            self.store, "old", 1, reason="confirmed change", approval="user decision", stopped=stopped,
        )

    def submit_change(self, task, path, content):
        self.store.add_task("old", 1, {"id": task, "paths": [path], "cases": ["TC-1"]})
        attempt = dispatch.reserve(self.store, "old", 1, task)["id"]
        workspace = artifacts.make_worktree(self.store, "old", attempt)
        dispatch.bind_workspace(self.store, "old", 1, attempt, workspace)
        dispatch.start(self.store, "old", 1, attempt, task)
        (workspace / path).write_text(content)
        artifacts.git(workspace, "add", path)
        artifacts.git(workspace, "commit", "-qm", task)
        result = artifacts.capture_submission(self.store, "old", attempt, task)
        verification.record(
            self.store, "old", 1, "self", result["commit"], task,
            {"TC-1": {"status": "pass", "actual": "Expected fixture observed"}},
            {"attempt_id": attempt, "suite_revision": "s1", "fixture_revision": "f1"},
        )
        dispatch.submit(self.store, "old", 1, attempt, result)
        return artifacts.stage(self.store, "old", 1, [attempt])

    def rollback_cli(self, operation, *, reason="recover lost staged worktree", epoch=1):
        parser = build_parser()
        args = parser.parse_args([
            "--data", str(self.store.data_root), "rollback-integration", "old", operation,
            "--reason", reason, "--epoch", str(epoch),
        ])
        with patch.object(cli, "Store", return_value=self.store):
            return invoke(args, parser)

    def test_partial_landing_blocks_candidate_advance_until_original_land_recovers(self):
        first = self.submit_change("A", "index.html", "candidate A")
        artifacts.promote(self.store, "old", 1, first["operation_id"])
        rename = Path.rename

        def interrupted(path, destination):
            if path.name.startswith(".rtl-stage-") and Path(destination) == self.output:
                raise OSError("interrupted after the original moved to backup")
            return rename(path, destination)

        with patch.object(Path, "rename", interrupted):
            with self.assertRaisesRegex(OSError, "interrupted after the original moved"):
                delivery.land(self.store, "old", 1, allow_replace=True)
        landing = next(op for op in self.store.operations("old") if op["kind"] == "delivery")
        self.assertFalse(self.output.exists())
        backup = Path(landing["payload"]["previous"])
        self.assertEqual((backup / "index.html").read_text(), "accepted seed")

        second = self.submit_change("B", "additional.html", "candidate B")
        with self.assertRaisesRegex(ValueError, "落盘替换尚未完成"):
            artifacts.promote(self.store, "old", 1, second["operation_id"])
        self.assertEqual(self.store.run("old")["candidate"], first["candidate"])
        repo = artifacts.repo_path(self.store, "old")
        self.assertEqual(artifacts.rev(repo, artifacts.CANDIDATE_REF), first["candidate"])
        self.assertFalse(self.output.exists())

        recovered = delivery.land(self.store, "old", 1)
        self.assertEqual(recovered["operation_id"], landing["id"])
        self.assertEqual((self.output / "index.html").read_text(), "candidate A")
        artifacts.promote(self.store, "old", 1, second["operation_id"])
        newest = delivery.land(self.store, "old", 1)
        self.assertEqual(newest["candidate"], second["candidate"])
        self.assertEqual((self.output / "additional.html").read_text(), "candidate B")

    def test_rollback_recovers_git_ahead_when_the_original_stage_is_unusable(self):
        first = self.submit_change("A", "index.html", "candidate A")
        operation = first["operation_id"]
        with self.assertRaisesRegex(LoopError, "实际 Git 候选不属于"):
            self.rollback_cli(operation)
        with patch.object(artifacts, "finish_operation", side_effect=RuntimeError("promotion receipt interrupted")):
            with self.assertRaisesRegex(RuntimeError, "promotion receipt interrupted"):
                artifacts.promote(self.store, "old", 1, operation)
        repo = artifacts.repo_path(self.store, "old")
        self.assertEqual(artifacts.rev(repo, artifacts.CANDIDATE_REF), first["candidate"])
        self.assertEqual(self.store.run("old")["candidate"], self.candidate)
        unrelated = self.store.attempt(first["attempt_ids"][0])["result"]["commit"]
        artifacts.git(repo, "update-ref", artifacts.CANDIDATE_REF, unrelated, first["candidate"])
        with self.assertRaisesRegex(LoopError, "实际 Git 候选不属于"):
            self.rollback_cli(operation)
        self.assertEqual(artifacts.rev(repo, artifacts.CANDIDATE_REF), unrelated)
        artifacts.git(repo, "update-ref", artifacts.CANDIDATE_REF, first["candidate"], unrelated)
        artifacts.git(repo, "worktree", "remove", first["path"])
        with self.assertRaises(ValueError):
            artifacts.promote(self.store, "old", 1, operation)
        with self.assertRaisesRegex(LoopError, "原合入操作"):
            dispatch.fence(self.store, "old", 1, first["attempt_ids"][0])

        with patch.object(lifecycle, "finish_operation", side_effect=RuntimeError("rollback receipt interrupted")):
            with self.assertRaisesRegex(RuntimeError, "rollback receipt interrupted"):
                self.rollback_cli(operation)
        self.assertEqual(artifacts.rev(repo, artifacts.CANDIDATE_REF), self.candidate)
        rollback_id = f"rollback-{operation}"
        discarded_ref = f"refs/rtl/rollback/{operation}"
        self.assertEqual(artifacts.rev(repo, discarded_ref), first["candidate"])
        self.assertEqual(self.store.operation("old", rollback_id)["status"], "intent")
        self.assertEqual(self.store.operation("old", operation)["status"], "intent")
        with self.assertRaisesRegex(LoopError, "撤销尚未完成"):
            artifacts.promote(self.store, "old", 1, operation)
        epoch = self.store.take_over("old", 1)["epoch"]
        with self.assertRaisesRegex(LoopError, "stale controller epoch"):
            self.rollback_cli(operation)
        with self.assertRaisesRegex(LoopError, "不同的撤销决定"):
            self.rollback_cli(operation, reason="different decision", epoch=epoch)
        receipt = self.rollback_cli(operation, epoch=epoch)
        self.assertEqual(self.rollback_cli(operation, epoch=epoch), receipt)
        self.assertEqual(receipt["candidate"], self.candidate)
        self.assertEqual(receipt["discarded_candidate"], first["candidate"])
        self.assertEqual(receipt["discarded_ref"], discarded_ref)
        self.assertEqual(artifacts.rev(repo, discarded_ref), first["candidate"])
        self.assertEqual(self.store.operation("old", operation)["status"], "superseded")
        self.assertEqual(self.store.tasks("old")[0]["status"], "submitted")
        with self.assertRaisesRegex(ValueError, "有效的合入意图"):
            artifacts.promote(self.store, "old", epoch, operation)
        with self.store.transaction(immediate=False) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)
        new = artifacts.stage(self.store, "old", epoch, first["attempt_ids"])
        self.assertEqual(
            artifacts.promote(self.store, "old", epoch, new["operation_id"])["candidate"], new["candidate"],
        )
        with self.assertRaisesRegex(LoopError, "尚未完成的合入"):
            self.rollback_cli(new["operation_id"], epoch=epoch)

    def test_new_cli_baseline_requires_frozen_suite_and_fixture_revisions(self):
        path = self.project / "baseline.json"
        path.write_text(json.dumps(self.baseline))
        with self.assertRaisesRegex(ValueError, "suite_digests"):
            baseline(path)
        path.write_text(json.dumps({
            **self.baseline, "suite_revision": "s1", "fixture_revision": "f1",
            "suite_digests": {hashlib.sha256(b"approved suite").hexdigest(): ["TC-1"]},
        }))
        self.assertEqual(baseline(path)["suite_revision"], "s1")

    def test_supersede_requires_stopping_workers_and_can_seed_next_run(self):
        self.store.add_task("old", 1, {"id": "A", "paths": ["A"]})
        attempt = dispatch.reserve(self.store, "old", 1, "A")
        with self.assertRaisesRegex(LoopError, "stopped"):
            self.replace()
        receipt = self.replace(stopped=True)
        self.assertEqual(self.replace(), receipt)
        self.assertEqual(receipt["stopped_attempts"], [attempt["id"]])
        self.assertEqual(self.store.run("old")["candidate"], self.candidate)
        self.assertEqual(self.store.operation("old", attempt["operation_id"])["status"], "superseded")
        with self.assertRaisesRegex(LoopError, "superseded"):
            self.store.take_over("old", 1)
        self.store.create_run(
            self.project, self.output, {**self.baseline, "revision": "new"},
            project_id="P", run_id="next", source_run="old",
        )
        (self.output / "index.html").write_text("new unrelated disk content")
        new_candidate = artifacts.initialize_repo(self.store, "next", 1)
        self.assertEqual(
            artifacts.git(artifacts.repo_path(self.store, "next"), "show", f"{new_candidate}:index.html"),
            b"accepted seed",
        )
        self.assertEqual(self.store.operation("next", "seed-from")["receipt"]["candidate"], self.candidate)

    def test_unreconciled_git_and_acceptance_block_new_task_and_supersede(self):
        repo = artifacts.repo_path(self.store, "old")
        artifacts.git(repo, "commit", "--allow-empty", "-qm", "interrupted promotion")
        ahead = artifacts.rev(repo)
        artifacts.git(repo, "update-ref", artifacts.CANDIDATE_REF, ahead, self.candidate)
        with self.assertRaisesRegex(LoopError, "原合入操作"):
            self.store.add_task("old", 1, {"id": "A", "paths": ["A"]})
        with self.assertRaisesRegex(LoopError, "原合入操作"):
            self.replace()
        artifacts.git(repo, "update-ref", artifacts.CANDIDATE_REF, self.candidate, ahead)
        with self.store.transaction() as db:
            begin_operation(db, "old", "accept-test", "acceptance", {})
        with self.assertRaisesRegex(LoopError, "验收意图"):
            self.store.add_task("old", 1, {"id": "A", "paths": ["A"]})
        with self.assertRaisesRegex(LoopError, "验收意图"):
            self.replace()
        self.assertEqual(self.store.tasks("old"), [])

    def test_replacement_preserves_pending_landing_and_requires_authorized_replacement(self):
        landed = delivery.land(self.store, "old", 1)
        receipt = self.replace()
        self.assertEqual(receipt["retained_output_manifest"], delivery.manifest(self.output))
        self.assertEqual(self.store.operation("old", landed["operation_id"])["status"], "superseded")
        self.assertEqual((self.output / "index.html").read_text(), "accepted seed")
