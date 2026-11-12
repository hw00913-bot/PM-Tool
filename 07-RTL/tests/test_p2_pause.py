import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop import __main__ as cli
from loop import artifacts, delivery, dispatch, lifecycle
from loop.__main__ import status
from loop.state import LoopError, Store


class RunPauseTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.store = self.open_store()
        self.project = self.root / "project"
        (self.project / "prototype").mkdir(parents=True)
        (self.project / "prototype" / "index.html").write_text("<html>seed</html>")
        self.run = self.store.create_run(
            self.project, self.project / "prototype", {"revision": "R1", "mandatory_cases": ["TC-C"]},
            run_id="R",
        )["id"]
        artifacts.initialize_repo(self.store, self.run, 1)

    def open_store(self, *, reserve=True):
        return Store(self.root / "data", reserve=reserve, reservation_root=self.root / "reservations")

    def reserve(self, name="C"):
        self.store.add_task(self.run, 1, {"id": name, "paths": [f"{name}.html"]})
        attempt = dispatch.reserve(self.store, self.run, 1, name)
        workspace = artifacts.make_worktree(self.store, self.run, attempt["id"])
        return dispatch.bind_workspace(self.store, self.run, 1, attempt["id"], workspace)

    def test_pause_intent_blocks_progress_across_sessions_until_host_stops_and_user_resumes(self):
        landed = delivery.land(self.store, self.run, 1)
        attempt = self.reserve()
        worker = "actual-worker"
        host_ref = "host-handle"
        dispatch.start(self.store, self.run, 1, attempt["id"], worker, host_ref=host_ref)
        checkpoint = {"next": "Continue the same layout worktree"}
        paused = lifecycle.pause_run(self.store, self.run, 1, reason="user requested pause", checkpoint=checkpoint)
        self.assertEqual(paused["state"], "stopping")
        self.assertEqual(paused["outstanding"], [attempt["id"]])
        self.assertEqual(paused["attempts"][0]["host_ref"], host_ref)
        self.assertEqual(lifecycle.pause_run(
            self.store, self.run, 1, reason="user requested pause", checkpoint=checkpoint,
        ), paused)

        second = self.open_store()
        self.assertEqual(second.run(self.run)["status"], "active")
        self.assertEqual(status(self.open_store(reserve=False), self.run)["pause"], paused)
        for action in (
            lambda: dispatch.reserve(second, self.run, 1, "C"),
            lambda: dispatch.start(second, self.run, 1, attempt["id"], worker),
            lambda: artifacts.promote(second, self.run, 1, "old-stage"),
            lambda: delivery.land(second, self.run, 1),
            lambda: delivery.finalize(second, self.run, 1, landed["operation_id"], "master"),
            lambda: second.add_task(self.run, 1, {"id": "new", "paths": ["new.html"]}),
        ):
            with self.assertRaisesRegex(LoopError, "resume-run"):
                action()

        epoch = second.take_over(self.run, 1)["epoch"]
        self.assertEqual(epoch, 2)
        with self.assertRaisesRegex(LoopError, "尚未确认"):
            lifecycle.resume_run(second, self.run, epoch, paused["pause_id"])
        with self.assertRaisesRegex(LoopError, "stale controller"):
            dispatch.pause(self.store, self.run, 1, attempt["id"], host_ref=host_ref)
        with self.assertRaisesRegex(LoopError, "宿主引用"):
            dispatch.pause(second, self.run, epoch, attempt["id"], host_ref="wrong-host")
        dispatch.pause(second, self.run, epoch, attempt["id"], host_ref=host_ref)
        confirmed = status(second, self.run)["pause"]
        self.assertEqual(confirmed["state"], "paused")
        self.assertEqual(confirmed["outstanding"], [])
        self.assertEqual(confirmed["attempts"][0]["host_state"], "stopped")
        self.assertEqual(confirmed["checkpoint"], checkpoint)
        with self.assertRaisesRegex(LoopError, "resume-run"):
            dispatch.reserve(second, self.run, epoch, "C")

        resumed = lifecycle.resume_run(second, self.run, epoch, paused["pause_id"])
        self.assertEqual(resumed["resumed_epoch"], epoch)
        self.assertEqual(resumed["state"], "resumed")
        self.assertIsNone(status(second, self.run)["pause"])
        next_attempt = dispatch.reserve(second, self.run, epoch, "C")
        self.assertEqual(next_attempt["id"], attempt["id"])
        self.assertEqual(next_attempt["workspace"], attempt["workspace"])
        with self.assertRaisesRegex(LoopError, "original worker"):
            dispatch.start(second, self.run, epoch, attempt["id"], "another-worker")
        started = dispatch.start(second, self.run, epoch, attempt["id"], worker, host_ref=host_ref)
        self.assertEqual(started["worker"], worker)

    def test_paused_recovery_skill_records_read_without_resuming_or_granting_other_actions(self):
        attempt = self.reserve()
        host_ref = "observed-host"
        dispatch.start(self.store, self.run, 1, attempt["id"], "worker", host_ref=host_ref)
        paused = lifecycle.pause_run(self.store, self.run, 1, reason="operator requested pause")
        parser = cli.build_parser()

        def invoke(store, *args):
            options = parser.parse_args(["--data", str(store.data_root), *args])
            with patch.object(cli, "Store", return_value=store):
                return cli.invoke(options, parser)

        def load(store, epoch, skill="loop-recovery", role="master", stage="S2"):
            return invoke(
                store, "skill", skill, "--role", role, "--stage", stage,
                "--run", self.run, "--epoch", str(epoch),
            )

        stopping = status(self.store, self.run)
        first = load(self.store, 1)
        after_first = status(self.store, self.run)
        self.assertEqual(stopping["pause"], paused)
        self.assertEqual(stopping["attempts"], after_first["attempts"])
        self.assertEqual(stopping["candidate"], after_first["candidate"])
        self.assertEqual(stopping["pause"], after_first["pause"])

        successor = self.open_store()
        epoch = invoke(successor, "takeover", self.run, "--epoch", "1")["epoch"]
        with self.assertRaisesRegex(LoopError, "stale controller epoch"):
            load(self.store, 1)
        dispatch.pause(successor, self.run, epoch, attempt["id"], host_ref=host_ref)
        stopped = status(successor, self.run)
        second = load(successor, epoch)
        self.assertEqual(stopped["pause"]["state"], "paused")

        with self.assertRaisesRegex(LoopError, "resume-run"):
            load(successor, epoch, skill="loop-control")
        with self.assertRaisesRegex(ValueError, "does not permit"):
            load(successor, epoch, role="verifier")
        with self.assertRaisesRegex(ValueError, "does not permit"):
            load(successor, epoch, stage="S5")
        with self.assertRaisesRegex(LoopError, "resume-run"):
            invoke(successor, "claim", self.run, "C", "--epoch", str(epoch))

        after_second = status(successor, self.run)
        self.assertEqual(stopped["attempts"], after_second["attempts"])
        self.assertEqual(stopped["candidate"], after_second["candidate"])
        self.assertEqual(stopped["pause"], after_second["pause"])
        receipts = [op for op in after_second["operations"] if op["kind"] == "skill"]
        self.assertEqual([op["id"] for op in receipts], [first["operation_id"], second["operation_id"]])
        self.assertTrue(all(op["receipt"] == {"loaded": True} for op in receipts))
        self.assertTrue(all(op["payload"]["id"] == "loop-recovery" for op in receipts))

    def test_reserved_worker_stop_is_observed_before_retaining_its_attempt(self):
        attempt = self.reserve()
        paused = lifecycle.pause_run(self.store, self.run, 1, reason="stop before host start receipt")
        self.assertEqual(paused["outstanding"], [attempt["id"]])
        with self.assertRaisesRegex(LoopError, "host-ref"):
            dispatch.pause(self.store, self.run, 1, attempt["id"])
        dispatch.pause(self.store, self.run, 1, attempt["id"], host_ref="observed host queue")
        previous_dispatch = self.store.operation(self.run, attempt["operation_id"])
        self.assertEqual(previous_dispatch["status"], "done")
        self.assertTrue(previous_dispatch["receipt"]["paused_before_start"])
        lifecycle.resume_run(self.store, self.run, 1, paused["pause_id"])
        next_attempt = dispatch.reserve(self.store, self.run, 1, "C")
        self.assertEqual(next_attempt["id"], attempt["id"])

    def test_pause_can_confirm_host_isolation_and_only_new_attempt_can_start(self):
        attempt = self.reserve()
        dispatch.start(self.store, self.run, 1, attempt["id"], "worker", host_ref="host-handle")
        paused = lifecycle.pause_run(self.store, self.run, 1, reason="worker cannot resume")
        with self.assertRaisesRegex(LoopError, "整轮暂停"):
            dispatch.fence(self.store, self.run, 1, attempt["id"])
        dispatch.fence(self.store, self.run, 1, attempt["id"], stopped=True, host_ref="host-handle")
        confirmed = status(self.store, self.run)["pause"]
        self.assertEqual(confirmed["attempts"][0]["host_state"], "isolated")
        self.assertEqual(confirmed["attempts"][0]["state"], "fenced")
        lifecycle.resume_run(self.store, self.run, 1, paused["pause_id"])
        next_attempt = dispatch.reserve(self.store, self.run, 1, "C")
        self.assertNotEqual(next_attempt["id"], attempt["id"])

    def test_resume_token_replays_only_for_same_pause(self):
        first = lifecycle.pause_run(self.store, self.run, 1, reason="first")
        self.assertEqual(first["state"], "paused")
        closed = lifecycle.resume_run(self.store, self.run, 1, first["pause_id"])
        self.assertEqual(lifecycle.resume_run(self.store, self.run, 1, first["pause_id"]), closed)
        second = lifecycle.pause_run(self.store, self.run, 1, reason="second")
        with self.assertRaisesRegex(LoopError, "当前暂停记录"):
            lifecycle.resume_run(self.store, self.run, 1, first["pause_id"])
        self.assertEqual(status(self.store, self.run)["pause"]["pause_id"], second["pause_id"])


    def test_paused_run_can_isolate_host_before_reconciling_pending_git_promotion(self):
        attempt = self.reserve()
        dispatch.start(self.store, self.run, 1, attempt["id"], "worker", host_ref="original-host")
        repo = artifacts.repo_path(self.store, self.run)
        parent = self.store.run(self.run)["candidate"]
        artifacts.git(repo, "commit", "--allow-empty", "-qm", "interrupted promotion")
        artifacts.git(repo, "update-ref", artifacts.CANDIDATE_REF, artifacts.rev(repo), parent)
        paused = lifecycle.pause_run(self.store, self.run, 1, reason="stop during promotion recovery")
        dispatch.fence(self.store, self.run, 1, attempt["id"], stopped=True, host_ref="original-host")
        self.assertEqual(status(self.store, self.run)["pause"]["outstanding"], [])
        lifecycle.resume_run(self.store, self.run, 1, paused["pause_id"])
        with self.assertRaisesRegex(LoopError, "原合入操作"):
            dispatch.reserve(self.store, self.run, 1, "C")

    def test_second_pause_before_resume_start_preserves_observed_host_reference(self):
        attempt = self.reserve()
        dispatch.start(self.store, self.run, 1, attempt["id"], "worker", host_ref="observed-host")
        first = lifecycle.pause_run(self.store, self.run, 1, reason="first")
        dispatch.pause(self.store, self.run, 1, attempt["id"], host_ref="observed-host")
        lifecycle.resume_run(self.store, self.run, 1, first["pause_id"])
        dispatch.reserve(self.store, self.run, 1, "C")
        second = lifecycle.pause_run(self.store, self.run, 1, reason="pause before next start")
        self.assertEqual(second["attempts"][0]["host_ref"], "observed-host")
        with self.assertRaisesRegex(LoopError, "宿主引用"):
            dispatch.pause(self.store, self.run, 1, attempt["id"], host_ref="unknown-host")
        dispatch.pause(self.store, self.run, 1, attempt["id"], host_ref="observed-host")
        self.assertEqual(status(self.store, self.run)["pause"]["outstanding"], [])


    def test_confirmed_paused_run_can_be_superseded_without_restarting_workers(self):
        attempt = self.reserve()
        dispatch.start(self.store, self.run, 1, attempt["id"], "worker", host_ref="host-handle")
        pause = lifecycle.pause_run(self.store, self.run, 1, reason="waiting for revised baseline")
        with self.assertRaisesRegex(LoopError, "须先确认"):
            lifecycle.supersede(self.store, self.run, 1, reason="change", approval="confirmed", stopped=True)
        dispatch.pause(self.store, self.run, 1, attempt["id"], host_ref="host-handle")
        receipt = lifecycle.supersede(self.store, self.run, 1, reason="change", approval="confirmed")
        self.assertEqual(receipt["status"], "superseded")
        self.assertEqual(self.store.operation(self.run, pause["pause_id"])["status"], "superseded")
