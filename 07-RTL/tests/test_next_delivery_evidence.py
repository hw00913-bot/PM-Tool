"""Regression checks for S4 advice and the actual gate on the same evidence."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, browser, delivery, dispatch, verification
from loop.state import Store, pack


ROOT = Path(__file__).resolve().parents[1]
REVISIONS = {"suite_revision": "S1", "fixture_revision": "F1"}


class NextDeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="rtl-next-s4-")
        self.addCleanup(work.cleanup)
        self.root = Path(work.name).resolve()
        self.project = self.root / "project"
        self.output = self.root / "output"
        seed = self.project / "prototype"
        seed.mkdir(parents=True)
        (seed / "index.html").write_text("<h1>isolated</h1>", encoding="utf-8")
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")

    def _suite(self, *cases):
        return json.dumps({
            **REVISIONS,
            "cases": [
                {"id": case, "steps": [{"op": "text", "selector": "h1", "value": "isolated"}]}
                for case in cases
            ],
        })

    def _run(self, *suites):
        self.store.create_run(self.project, self.output, {
            "revision": "R1", "mandatory_cases": ["TC-A"], **REVISIONS,
            "suite_digests": {
                hashlib.sha256(suite.encode()).hexdigest(): [case["id"] for case in json.loads(suite)["cases"]]
                for suite in suites
            },
        }, project_id="P", run_id="R")
        artifacts.initialize_repo(self.store, "R", 1)

    def _report(self, landed, suite, outcomes, name):
        path = self.root / name
        path.write_text(json.dumps({
            **REVISIONS, "protocol": "file:", "path": landed["entry"], "entry": landed["url"],
            "browser": "chromium 1", "asset_digest": browser.asset_digest(self.output),
            "suite_digest": hashlib.sha256(suite.encode()).hexdigest(), "suite_input": suite,
            "cases": {case: {"status": outcome, "executed": ["text h1"]} for case, outcome in outcomes.items()},
        }), encoding="utf-8")
        return verification.record_browser_report(
            self.store, "R", 1, "delivery", landed["candidate"], "reviewer", path,
            {"operation_id": landed["operation_id"]},
        )

    def _status(self, run_id="R"):
        before = self.store.path.read_bytes()
        entries = set(self.root.rglob("*"))
        result = subprocess.run(
            [sys.executable, "-m", "loop", "--data", str(self.store.data_root), "status", run_id, "--next"],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.store.path.read_bytes())
        self.assertEqual(entries, set(self.root.rglob("*")))
        return json.loads(result.stdout)

    def test_missing_archive_suppresses_finalization_and_identifies_the_evidence(self):
        suite = self._suite("TC-A")
        self._run(suite)
        landed = delivery.land(self.store, "R", 1)
        proof = self._report(landed, suite, {"TC-A": "pass"}, "pass.json")
        operation = landed["operation_id"]
        intent = delivery._acceptance_intent(self.store, "R", 1, operation, "master", "approved")
        path = Path(proof["context"]["evidence"])
        contents = path.read_bytes()
        path.unlink()
        actions = self._status()["next_actions"]
        evidence = next(action for action in actions if action["kind"] == "s4_evidence")
        self.assertEqual(evidence["missing_cases"], {"chromium": ["TC-A"]})
        self.assertEqual(evidence["unavailable_evidence_ids"], [proof["id"]])
        self.assertFalse(any("finalize" in action.get("argv", ()) for action in actions))
        with self.assertRaisesRegex(ValueError, "缺少通过的 delivery 证据"):
            delivery.finalize(self.store, "R", 1, operation, "master")
        self.assertEqual(self.store.operation("R", intent["id"])["status"], "intent")

        path.write_bytes(contents)
        actions = self._status()["next_actions"]
        resume = next(action for action in actions if action["kind"] == "s4_acceptance_resume")
        self.assertEqual(resume["argv"][5:], ["finalize", "R", operation, "--actor", "master", "--epoch", "1"])
        self.assertEqual(delivery.finalize(self.store, "R", 1, operation, "master")["approval"], "approved")

    def test_legacy_equivalent_retest_uses_archived_case_fingerprint_after_takeover(self):
        first, second = self._suite("TC-A"), self._suite("TC-A", "TC-B")
        self._run(first, second)
        landed = delivery.land(self.store, "R", 1)
        failure = self._report(landed, first, {"TC-A": "fail"}, "failure.json")
        passing = self._report(landed, second, {"TC-A": "pass", "TC-B": "pass"}, "pass.json")
        operation = landed["operation_id"]
        intent = delivery._acceptance_intent(self.store, "R", 1, operation, "old-master", "approved")
        with closing(sqlite3.connect(self.store.path)) as db, db:
            for row in (failure, passing):
                context = dict(row["context"])
                del context["case_fingerprints"]
                db.execute("UPDATE evidence SET context=? WHERE id=?", (pack(context), row["id"]))
            db.execute("DROP TABLE project_rebinds")
            db.execute("PRAGMA user_version=0")

        self.assertEqual(self._status()["epoch"], 1)
        with self.store.transaction(immediate=False) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
        epoch = self.store.take_over("R", 1)["epoch"]
        actions = self._status()["next_actions"]
        resume = next(action for action in actions if action["kind"] == "s4_acceptance_resume")
        self.assertEqual(resume["acceptance_id"], intent["id"])
        self.assertEqual(resume["argv"][5:], ["finalize", "R", operation, "--actor", "old-master", "--epoch", "2"])
        receipt = delivery.finalize(self.store, "R", epoch, operation, "successor")
        self.assertEqual((receipt["actor"], receipt["approval"]), ("old-master", "approved"))

    def test_candidate_author_cannot_satisfy_independent_evidence(self):
        suite = self._suite("TC-A")
        self._run(suite)
        self.store.add_task("R", 1, {"id": "A", "paths": ["index.html"]})
        attempt = dispatch.reserve(self.store, "R", 1, "A")
        workspace = artifacts.make_worktree(self.store, "R", attempt["id"])
        dispatch.bind_workspace(self.store, "R", 1, attempt["id"], workspace)
        dispatch.start(self.store, "R", 1, attempt["id"], "author")
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET status='integrated' WHERE run_id='R' AND id='A'")
            db.execute("UPDATE attempts SET state='completed' WHERE id=?", (attempt["id"],))
        landed = delivery.land(self.store, "R", 1)
        proof = self._report(landed, suite, {"TC-A": "pass"}, "pass.json")
        operation = landed["operation_id"]
        delivery._acceptance_intent(self.store, "R", 1, operation, "master", "approved")
        with self.store.transaction() as db:
            db.execute("UPDATE evidence SET actor='author' WHERE id=?", (proof["id"],))
        actions = self._status()["next_actions"]
        self.assertFalse(any("finalize" in action.get("argv", ()) for action in actions))
        with self.assertRaisesRegex(ValueError, "缺少实际落盘路径的独立验证"):
            delivery.finalize(self.store, "R", 1, operation, "master")

    def test_interrupted_replacement_can_recover_before_new_tasks_integrate(self):
        suite = self._suite("TC-A")
        self._run(suite)
        original = delivery.land(self.store, "R", 1)
        self._report(original, suite, {"TC-A": "pass"}, "accepted.json")
        delivery.finalize(self.store, "R", 1, original["operation_id"], "master")
        self.store.release_reservation("R")
        (self.project / "prototype" / "index.html").write_text("<h1>replacement</h1>", encoding="utf-8")
        self.store.create_run(self.project, self.output, {
            "revision": "R2", "mandatory_cases": ["TC-A"],
        }, project_id="P", run_id="R2")
        artifacts.initialize_repo(self.store, "R2", 1)

        rename = Path.rename

        def interrupt(path, target):
            if path.name.startswith(".rtl-stage-") and target == self.output:
                raise OSError("interrupted after backup")
            return rename(path, target)

        with patch.object(Path, "rename", interrupt), self.assertRaisesRegex(OSError, "interrupted"):
            delivery.land(self.store, "R2", 1, allow_replace=True)
        self.assertFalse(self.output.exists())
        self.store.add_task("R2", 1, {"id": "new-task", "paths": ["new.html"]})
        actions = self._status("R2")["next_actions"]
        retry = next(action for action in actions if action["kind"] == "s4_land_retry")
        self.assertEqual(retry["argv"][5:], ["land", "R2", "--epoch", "1"])
        recovered = delivery.land(self.store, "R2", 1)
        self.assertEqual(recovered["operation_id"], retry["operation_id"])
        self.assertEqual((self.output / "index.html").read_text(), "<h1>replacement</h1>")
