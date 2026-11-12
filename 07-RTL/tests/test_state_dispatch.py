import tempfile
import unittest
from pathlib import Path

from loop import artifacts, dispatch
from loop.state import LoopError, Store, begin_operation, finish_operation, guard


class StateDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.store = Store(self.root / "state", reservation_root=self.root / "reservations")
        self.project = self.root / "project"
        self.output = self.project / "prototype"
        self.run = self.store.create_run(
            self.project, self.output,
            {"revision": "R1", "mandatory_cases": ["TC-1"], "human_acceptance": False},
            project_id="P", run_id="R",
        )
        artifacts.initialize_repo(self.store, "R", 1)

    def task(self, name, *, paths=None, edges=(), deps=()):
        return self.store.add_task(
            "R", 1, {"id": name, "paths": paths or [f"scenes/{name}"], "edges": list(edges), "deps": list(deps)}
        )

    def start(self, attempt, *, epoch=1, actor=None):
        workspace = self.store.root / "workspaces" / attempt["id"]
        workspace.mkdir(parents=True, exist_ok=True)
        dispatch.bind_workspace(self.store, "R", epoch, attempt["id"], workspace)
        return dispatch.start(self.store, "R", epoch, attempt["id"], actor or attempt["task_id"])

    def result(self, attempt, **overrides):
        result = {
            "base": attempt["input"]["candidate"],
            "actor": attempt["worker"],
            "commit": "b" * 40,
            "paths": [f"scenes/{attempt['task_id']}/index.html"],
        }
        return {**result, **overrides}

    def test_live_output_is_reserved_and_overlap_alias_is_detected(self):
        alias = self.root / "alias"
        self.project.mkdir()
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(LoopError, "overlaps active run"):
            self.store.create_run(self.root / "other", alias / "prototype" / "child", {}, project_id="Q")
        with self.assertRaisesRegex(LoopError, "active run"):
            self.store.create_run(self.project, self.output, {}, project_id="P")
        with self.assertRaisesRegex(LoopError, "overlaps active run"):
            self.store.create_run(self.root / "other", self.project / "PROTOTYPE", {}, project_id="Q")

    def test_rebind_reserves_new_path_and_retains_historical_paths(self):
        self.project.mkdir()
        relocated = self.root / "relocated"
        self.project.rename(relocated)
        with self.assertRaisesRegex(LoopError, "rebind"):
            self.store.create_run(relocated, relocated / "prototype", {}, project_id="P")
        self.store.rebind_project("P", relocated, relocated / "prototype", epoch=1)
        self.assertEqual(self.store.run("R")["project_root"], str(relocated))
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET status = 'delivered' WHERE id = 'R'")
        next_path = self.root / "next"
        self.store.rebind_project("P", next_path, next_path / "prototype")
        self.assertEqual(self.store.run("R")["project_root"], str(relocated))
        self.assertEqual(self.store.create_run(next_path, next_path / "prototype", {})["project_id"], "P")

    def test_dispatch_receipt_is_reconciled_after_takeover(self):
        self.task("A")
        attempt = dispatch.reserve(self.store, "R", 1, "A")
        operation = self.store.operation("R", attempt["operation_id"])
        self.assertEqual(operation["status"], "intent")
        self.assertEqual(self.store.take_over("R", 1)["epoch"], 2)
        with self.assertRaisesRegex(LoopError, "stale controller"):
            self.start(attempt)
        adopted = self.start(attempt, epoch=2)
        self.assertEqual(adopted["id"], attempt["id"])
        self.assertEqual(dispatch.start(self.store, "R", 2, attempt["id"], "A"), adopted)
        receipt = self.store.operation("R", attempt["operation_id"])
        self.assertEqual(receipt["receipt"], {"attempt_id": attempt["id"], "worker": "A"})
        self.assertEqual(len(self.store.attempts("R")), 1)
        self.assertEqual(dispatch.submit(self.store, "R", 2, attempt["id"], self.result(adopted))["state"], "submitted")

    def test_confirmed_pause_frees_slot_but_keeps_identity_and_write_scope(self):
        for task_id in ("A", "B", "C"):
            self.task(task_id)
        self.task("D", paths=["scenes/B/components"])
        a = self.start(dispatch.reserve(self.store, "R", 1, "A"))
        b = self.start(dispatch.reserve(self.store, "R", 1, "B"))
        with self.assertRaisesRegex(LoopError, "both execution slots"):
            dispatch.reserve(self.store, "R", 1, "C")
        dispatch.pause(self.store, "R", 1, b["id"])
        with self.assertRaisesRegex(LoopError, "write scope"):
            dispatch.reserve(self.store, "R", 1, "D")
        c = self.start(dispatch.reserve(self.store, "R", 1, "C"))
        dispatch.submit(self.store, "R", 1, a["id"], self.result(a))
        resumed = dispatch.reserve(self.store, "R", 1, "B")
        self.assertEqual(resumed["id"], b["id"])
        self.assertNotEqual(resumed["operation_id"], b["operation_id"])
        self.assertEqual(self.start(resumed)["worker"], b["worker"])
        self.assertEqual(self.store.attempt(c["id"])["state"], "running")

    def test_design_can_start_before_active_contract_and_hard_dependency(self):
        self.task("B", deps=("A",), edges=("A-B",))
        with self.assertRaisesRegex(LoopError, "waiting for integrated task A"):
            dispatch.reserve(self.store, "R", 1, "B")
        design = self.start(dispatch.reserve(self.store, "R", 1, "B", phase="design"))
        dispatch.submit(self.store, "R", 1, design["id"], {"proposal": "A-B"})
        self.assertEqual(self.store.tasks("R")[0]["status"], "planned")
        self.task("A")
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET status = 'integrated' WHERE run_id = 'R' AND id = 'A'")
        with self.assertRaisesRegex(LoopError, "waiting for active contracts"):
            dispatch.reserve(self.store, "R", 1, "B")
        with self.store.transaction() as db:
            db.execute("INSERT INTO contracts VALUES ('R', 'A-B', 'v1', '{}', 1)")
        implementation = dispatch.reserve(self.store, "R", 1, "B")
        self.assertEqual(implementation["input"]["contracts"], {"A-B": "v1"})
        self.assertNotEqual(implementation["id"], design["id"])

    def test_confirmed_fence_preserves_history_and_rejects_late_attempt(self):
        self.task("B")
        before = self.start(dispatch.reserve(self.store, "R", 1, "B"))
        with self.assertRaisesRegex(LoopError, "stopped or isolated"):
            dispatch.fence(self.store, "R", 1, before["id"])
        dispatch.fence(self.store, "R", 1, before["id"], stopped=True)
        current = self.start(dispatch.reserve(self.store, "R", 1, "B"))
        self.assertNotEqual(current["id"], before["id"])
        with self.assertRaisesRegex(LoopError, "no longer authoritative"):
            dispatch.submit(self.store, "R", 1, before["id"], self.result(before))
        with self.assertRaisesRegex(LoopError, "outside the task scope"):
            dispatch.submit(self.store, "R", 1, current["id"], self.result(current, paths=["scenes/BB/extra"]))
        self.assertEqual(self.store.attempt(current["id"])["state"], "running")

    def test_reject_changed_contract_during_submission(self):
        self.task("B", edges=("A-B",))
        with self.store.transaction() as db:
            db.execute("INSERT INTO contracts VALUES ('R', 'A-B', 'v1', '{}', 1)")
        attempt = self.start(dispatch.reserve(self.store, "R", 1, "B"))
        with self.store.transaction() as db:
            db.execute("UPDATE contracts SET active=0 WHERE run_id = 'R'")
            db.execute("INSERT INTO contracts VALUES ('R', 'A-B', 'v2', '{}', 1)")
        with self.assertRaisesRegex(LoopError, "contract versions changed"):
            dispatch.submit(self.store, "R", 1, attempt["id"], self.result(attempt))

    def test_operation_identity_cannot_be_reused_with_another_outcome(self):
        with self.store.transaction() as db:
            guard(db, "R", 1)
            begin_operation(db, "R", "move-1", "delivery", {"candidate": "a"})
            finish_operation(db, "R", "move-1", {"result": "a"})
            self.assertEqual(
                finish_operation(db, "R", "move-1", {"result": "a"})["status"],
                "done",
            )
            with self.assertRaisesRegex(LoopError, "different receipt"):
                finish_operation(db, "R", "move-1", {"result": "b"})
            with self.assertRaisesRegex(LoopError, "different intent"):
                begin_operation(db, "R", "move-1", "delivery", {"candidate": "b"})

    def test_task_dependency_cycle_and_bad_path_are_rejected(self):
        self.task("A", deps=("B",))
        with self.assertRaisesRegex(LoopError, "cyclic"):
            self.task("B", deps=("A",))
        with self.assertRaisesRegex(LoopError, "prototype-relative path"):
            self.task("C", paths=["assets/../outside"])


if __name__ == "__main__":
    unittest.main()
