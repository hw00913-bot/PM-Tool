import json
from pathlib import Path
import tempfile
import unittest

from loop import artifacts, dispatch, requirements, requirements_run
from loop.state import LoopError, Store


EXAMPLE = Path(__file__).resolve().parents[1] / "skills/requirement-architecture/references/requirements-example.json"


class BoundS1AttemptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        project = root / "project"
        self.store = Store(root / "data", reservation_root=root / "reservations")
        baseline = {
            "revision": "r1",
            "mandatory_cases": ["TC-C01"],
            "requirements": requirements.normalize_plan(json.loads(EXAMPLE.read_text()), ["TC-C01"]),
        }
        self.run = self.store.create_run(project, project / "prototype", baseline, run_id="R")
        artifacts.initialize_repo(self.store, "R", 1)
        prepared = requirements_run.prepare(self.store, "R", 1)
        self.snapshot = Path(prepared["baseline"])
        self.snapshot_content = self.snapshot.read_bytes()
        self.attempt = dispatch.reserve(self.store, "R", 1, "C")
        self.worktree = artifacts.make_worktree(self.store, "R", self.attempt["id"])
        dispatch.bind_workspace(self.store, "R", 1, self.attempt["id"], self.worktree)

    def test_missing_snapshot_blocks_start_and_submit_until_restored(self):
        attempt = self.attempt["id"]
        self.snapshot.unlink()
        with self.assertRaisesRegex(LoopError, "S1 冻结基线"):
            dispatch.start(self.store, "R", 1, attempt, "author")
        self.assertEqual(self.store.attempt(attempt)["state"], "reserved")
        requirements_run.prepare(self.store, "R", 1)
        dispatch.start(self.store, "R", 1, attempt, "author")
        (self.worktree / "index.html").write_text("<h1>layout</h1>", encoding="utf-8")
        artifacts.git(self.worktree, "add", "index.html")
        artifacts.git(self.worktree, "commit", "--quiet", "-m", "layout change")
        result = artifacts.capture_submission(self.store, "R", attempt, "author")
        self.snapshot.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(LoopError, "S1 冻结基线"):
            dispatch.submit(self.store, "R", 1, attempt, result)
        self.assertEqual(self.store.attempt(attempt)["state"], "running")
        self.snapshot.write_bytes(self.snapshot_content)
        self.assertEqual(dispatch.submit(self.store, "R", 1, attempt, result)["state"], "submitted")

    def test_attempt_with_different_s1_binding_does_not_start(self):
        attempt = self.attempt
        attempt["input"]["requirements"]["plan_sha256"] = "a" * 64
        with self.store.transaction() as db:
            db.execute("UPDATE attempts SET input=? WHERE id=?", (json.dumps(attempt["input"]), attempt["id"]))
        with self.assertRaisesRegex(LoopError, "尝试绑定的 S1 冻结契约"):
            dispatch.start(self.store, "R", 1, attempt["id"], "author")
        self.assertEqual(self.store.attempt(attempt["id"])["state"], "reserved")
