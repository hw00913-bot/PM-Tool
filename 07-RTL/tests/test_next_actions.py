import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, codex_host, contracts, delivery, dispatch, lifecycle, verification
from loop.state import Store, begin_operation, finish_operation

if __package__:
    from . import test_codex_host, test_p1_contracts
else:
    import test_codex_host
    import test_p1_contracts


PROJECT = Path(__file__).resolve().parents[1]


class NextActionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rtl-next-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data = self.root / "data store"
        self.project = self.root / "project"
        self.output = self.root / "public"
        self.seed = self.project / "prototype" / "index.html"
        self.seed.parent.mkdir(parents=True)
        self.seed.write_text("<h1>seed</h1>", encoding="utf-8")
        self.store = Store(self.data, reservation_root=self.root / "reservations")
        self.baseline = {
            "frontend": "static", "mandatory_cases": ["TC-final"],
            "suite_revision": "S1", "fixture_revision": "F1", "acceptance_browsers": ["chromium"],
        }
        self.create_run("R", "P")

    def create_run(self, run_id, project_id):
        self.store.create_run(
            self.project, self.output, {**self.baseline, "revision": run_id},
            run_id=run_id, project_id=project_id,
        )
        artifacts.initialize_repo(self.store, run_id, 1)

    def invoke_status(self, run_id="R", *, next_view=True, data=None):
        args = ["--data", str(data or self.data), "status", run_id]
        if next_view:
            args.append("--next")
        return subprocess.run(
            [sys.executable, "-m", "loop", *args],
            cwd=PROJECT, capture_output=True, text=True, timeout=10,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    def status(self, run_id="R", *, next_view=True):
        result = self.invoke_status(run_id, next_view=next_view)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["id"], run_id)
        if next_view:
            for action in parsed["next_actions"]:
                self.assertTrue(action["reason"])
                if action.get("argv"):
                    if action["argv"][0] == "python3":
                        self.assertEqual(action["argv"][:5], ["python3", "-m", "loop", "--data", str(self.data)])
                    else:
                        self.assertEqual(action["argv"][0], "git")
                    self.assertEqual(shlex.split(action["command"]), action["argv"])
        return parsed

    def task(self, task_id, *, edges=()):
        self.store.add_task("R", 1, {"id": task_id, "paths": [f"{task_id}.html"], "edges": list(edges)})

    def reserve(self, task_id, *, phase="implement"):
        attempt = dispatch.reserve(self.store, "R", 1, task_id, phase=phase)
        workspace = artifacts.make_worktree(self.store, "R", attempt["id"])
        return dispatch.bind_workspace(self.store, "R", 1, attempt["id"], workspace)

    def record_delivery_evidence(self, landed):
        verification.record(
            self.store, "R", 1, "delivery", landed["candidate"], "reviewer",
            {"TC-final": {"status": "pass", "actual": "Observed the accepted page"}},
            {
                "operation_id": landed["operation_id"], "path": landed["entry"], "url": landed["url"],
                "browser": "chromium", "suite_revision": "S1", "fixture_revision": "F1",
            },
        )

    def test_next_is_read_only_and_preserves_default_status(self):
        before_status = self.status(next_view=False)
        self.assertNotIn("next_actions", before_status)
        before_files = set(self.root.rglob("*"))
        before_bytes = self.store.path.read_bytes()
        next_status = self.status()
        self.assertEqual(next_status, self.status())
        self.assertEqual(next_status["status"], before_status["status"])
        self.assertEqual(next_status["epoch"], before_status["epoch"])
        self.assertEqual(self.store.path.read_bytes(), before_bytes)
        self.assertEqual(set(self.root.rglob("*")), before_files)
        absent = self.root / "missing-ledger"
        failure = self.invoke_status(data=absent)
        self.assertNotEqual(failure.returncode, 0)
        self.assertFalse(absent.exists())

    def test_takeover_uses_original_codex_attempt_and_requires_host_evidence(self):
        self.task("A")
        attempt = self.reserve("A")
        attempt_id, dispatch_id = attempt["id"], attempt["operation_id"]
        turn_id = f"codex-turn:{dispatch_id}"
        with self.store.transaction() as db:
            thread = f"codex-thread:{attempt_id}"
            begin_operation(db, "R", thread, "codex-thread", {
                "attempt_id": attempt_id, "workspace": attempt["workspace"], "socket": str(self.root / "host.sock"),
            })
            finish_operation(db, "R", thread, {"thread_id": "recorded-thread"})
            begin_operation(db, "R", turn_id, "codex-turn", {
                "attempt_id": attempt_id, "dispatch_id": dispatch_id, "thread_id": "recorded-thread",
                "marker": f"RTL dispatch: {dispatch_id}",
            })
        epoch = self.store.take_over("R", 1)["epoch"]
        view = self.status()
        self.assertEqual(view["epoch"], epoch)
        self.assertTrue(any(action.get("argv", [])[5:] == ["takeover", "R", "--epoch", str(epoch)]
                            for action in view["next_actions"]))
        matching = [action for action in view["next_actions"] if action.get("attempt_id") == attempt_id]
        self.assertTrue(any(action.get("argv", [])[5:] == ["codex-status", "R", attempt_id] for action in matching))
        self.assertTrue(any(action.get("checks") for action in matching))
        self.assertFalse(any("--stopped" in action.get("argv", []) for action in matching))

    def test_takeover_shows_registered_manual_worker(self):
        self.task("A")
        attempt = self.reserve("A")
        dispatch.start(self.store, "R", 1, attempt["id"], "worker-a", host_ref="manual-host-7")
        epoch = self.store.take_over("R", 1)["epoch"]

        before = self.store.path.read_bytes()
        view = self.status()
        self.assertEqual(view["epoch"], epoch)
        action = next(item for item in view["next_actions"] if item["kind"] == "check-worker")
        self.assertEqual(action["attempt_id"], attempt["id"])
        self.assertEqual(action["workspace"], attempt["workspace"])
        self.assertEqual(action["operation_id"], attempt["operation_id"])
        self.assertEqual(action["host_ref"], "manual-host-7")
        self.assertEqual(action["worker"], "worker-a")
        self.assertNotIn("argv", action)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_takeover_points_to_the_original_running_codex_turn(self):
        self.task("A")
        attempt = self.reserve("A")
        host = test_codex_host.FakeAppServer()
        with patch.object(codex_host, "CodexClient", host):
            started = codex_host.start(self.store, "R", 1, attempt["id"])
            epoch = self.store.take_over("R", 1)["epoch"]
            before = self.store.path.read_bytes()
            guidance = self.status()
            observed = codex_host.status(self.store, "R", attempt["id"])

        self.assertEqual(guidance["epoch"], epoch)
        action = next(item for item in guidance["next_actions"] if item["kind"] == "check-codex")
        self.assertEqual(action["argv"][5:], ["codex-status", "R", attempt["id"]])
        self.assertEqual(action["operation_id"], attempt["operation_id"])
        self.assertEqual(action["thread_id"], started["thread_id"])
        self.assertEqual(action["turn_id"], started["turn_id"])
        self.assertEqual(observed["turn_id"], started["turn_id"])
        self.assertEqual(observed["attempt_state"], "running")
        self.assertEqual(len(host.start_calls), 1)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_pause_requires_observed_stop_then_uses_current_epoch_and_pause_id(self):
        self.task("C")
        attempt = self.reserve("C")
        dispatch.start(self.store, "R", 1, attempt["id"], "worker", host_ref="recorded host")
        paused = lifecycle.pause_run(self.store, "R", 1, reason="requested pause")
        epoch = self.store.take_over("R", 1)["epoch"]
        stopping = self.status()["next_actions"]
        self.assertTrue(any(row.get("attempt_id") == attempt["id"] and row.get("checks") for row in stopping))
        self.assertFalse(any("resume-run" in row.get("argv", []) or "--stopped" in row.get("argv", []) for row in stopping))
        dispatch.pause(self.store, "R", epoch, attempt["id"], host_ref="recorded host")
        stopped = self.status()["next_actions"]
        expected = ["resume-run", "R", paused["pause_id"], "--epoch", str(epoch)]
        self.assertTrue(any(row.get("argv", [])[5:] == expected for row in stopped))

    def test_contract_replacement_points_to_affected_attempt_and_preserves_independent_task(self):
        for task_id, edges in (("A", ("ab",)), ("B", ("ab",)), ("C", ())):
            self.task(task_id, edges=edges)

        def activate(version):
            contracts.propose(self.store, "R", 1, "ab", version, "A", "B", {
                "behavior": version, "cases": ["TC-ab"],
            })
            for task_id in ("A", "B"):
                contracts.confirm(self.store, "R", 1, "ab", version, task_id)
            return contracts.activate(self.store, "R", 1, "ab", version)

        activate("v1")
        original = self.reserve("A", phase="design")
        dispatch.start(self.store, "R", 1, original["id"], "designer")
        dispatch.pause(self.store, "R", 1, original["id"])
        independent = self.reserve("C")
        dispatch.start(self.store, "R", 1, independent["id"], "worker-c")
        self.assertIn("A", activate("v2")["invalidated"])
        actions = self.status()["next_actions"]
        affected = [row for row in actions if row["kind"] == "contract-redispatch"]
        self.assertTrue(any(row.get("task_id") == "A" and row.get("attempt_id") == original["id"]
                            and row.get("version") == "v2" for row in affected))
        self.assertFalse(any(row.get("task_id") == "C" or row.get("attempt_id") == independent["id"] for row in affected))

    def test_old_landing_does_not_hide_contract_redispatch(self):
        fixture = test_p1_contracts.ContractIntegrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        fixture.integrate_v1()
        fixture.add_task("C")
        fixture.submit("C", "independent layout")
        fixture.promote(fixture.stage("C"))
        landed = delivery.land(fixture.store, fixture.run, fixture.epoch)
        activated = fixture.activate("v2")
        self.assertEqual(activated["invalidated"], ["A", "B"])

        before = fixture.store.path.read_bytes()
        result = self.invoke_status(fixture.run, data=fixture.store.data_root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actions = json.loads(result.stdout)["next_actions"]
        self.assertEqual(fixture.store.path.read_bytes(), before)
        review = next(item for item in actions if item["kind"] == "s4_review_landing")
        self.assertEqual(review["operation_id"], landed["operation_id"])
        self.assertNotIn("argv", review)
        self.assertEqual({item["kind"] for item in actions if item["kind"].startswith("s4_")},
                         {"s4_review_landing"})
        redispatch = {item["task_id"]: item for item in actions if item["kind"] == "contract-redispatch"}
        self.assertEqual(set(redispatch), {"A", "B"})
        for task_id, action in redispatch.items():
            self.assertEqual(action["version"], "v2")
            self.assertEqual(action["argv"][5:8], ["claim", fixture.run, task_id])

        with self.assertRaisesRegex(ValueError, "本轮仍有任务未集成"):
            delivery.land(fixture.store, fixture.run, fixture.epoch)
        replacement = dispatch.reserve(fixture.store, fixture.run, fixture.epoch, "A")
        self.assertEqual(replacement["input"]["contracts"], {"ab": "v2"})
        self.assertEqual(next(task["status"] for task in fixture.store.tasks(fixture.run) if task["id"] == "C"),
                         "integrated")

    def test_s4_missing_evidence_reports_actual_landing_and_required_case(self):
        landed = delivery.land(self.store, "R", 1)
        actions = self.status()["next_actions"]
        evidence = next(row for row in actions if row["kind"] == "s4_evidence")
        self.assertEqual(evidence["operation_id"], landed["operation_id"])
        self.assertEqual(evidence["entry"], landed["entry"])
        self.assertIn("TC-final", evidence["missing_cases"]["chromium"])
        self.assertTrue(evidence["checks"])
        self.assertFalse(any("finalize" in row.get("argv", []) for row in actions))

    def test_s4_pending_acceptance_prioritizes_known_evidence_gap(self):
        landed = delivery.land(self.store, "R", 1)
        self.record_delivery_evidence(landed)
        intent = delivery._acceptance_intent(self.store, "R", 1, landed["operation_id"], "master", None)
        with self.store.transaction() as db:
            db.execute("DELETE FROM evidence WHERE run_id=? AND kind='delivery'", ("R",))
        actions = self.status()["next_actions"]
        evidence = next(row for row in actions if row["kind"] == "s4_evidence")
        self.assertEqual(evidence["operation_id"], landed["operation_id"])
        self.assertEqual(evidence["missing_cases"]["chromium"], ["TC-final"])
        self.assertFalse(any("finalize" in row.get("argv", []) for row in actions))
        self.assertEqual(self.store.operation("R", intent["id"])["status"], "intent")

    def test_s4_interrupted_rollback_uses_original_delivery_and_confirmed_reason(self):
        accepted = delivery.land(self.store, "R", 1)
        self.record_delivery_evidence(accepted)
        delivery.finalize(self.store, "R", 1, accepted["operation_id"], "master")
        self.store.release_reservation("R")
        self.seed.write_text("<h1>Rejected replacement</h1>", encoding="utf-8")
        self.create_run("replacement", "P")
        landed = delivery.land(self.store, "replacement", 1, allow_replace=True)
        operation_id = landed["operation_id"]
        backup = Path(self.store.operation("replacement", operation_id)["payload"]["previous"])
        rename = Path.rename

        def interrupt(path, target):
            if path == backup and target == self.output:
                raise OSError("interrupted before restoring the previous entry")
            return rename(path, target)

        reason = "Recorded owner decision"
        with patch.object(Path, "rename", interrupt), self.assertRaisesRegex(OSError, "interrupted"):
            delivery.rollback_landing(self.store, "replacement", 1, operation_id, reason)
        self.assertFalse(self.output.exists())
        epoch = self.store.take_over("replacement", 1)["epoch"]
        actions = self.status("replacement")["next_actions"]
        rollback = next(row for row in actions
                        if row.get("argv", [])[5:8] == ["rollback-landing", "replacement", operation_id])
        argv = rollback["argv"]
        self.assertEqual(argv[argv.index("--reason") + 1], reason)
        self.assertEqual(argv[argv.index("--epoch") + 1], str(epoch))
        self.assertTrue(rollback["checks"])
        self.assertFalse(self.output.exists())
        pause = lifecycle.pause_run(self.store, "replacement", epoch, reason="Pause recovery")
        paused = self.status("replacement")
        self.assertEqual(paused["pause_id"], pause["pause_id"])
        self.assertFalse(any("rollback-landing" in row.get("argv", []) for row in paused["next_actions"]))
        self.assertFalse(self.output.exists())
