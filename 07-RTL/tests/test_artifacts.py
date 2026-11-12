import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop import artifacts, delivery, dispatch, verification
from loop.state import Store


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        (self.project / "prototype").mkdir(parents=True)
        (self.project / "prototype" / "index.html").write_text("<html>seed</html>", encoding="utf-8")
        self.output = self.root / "output"
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")
        self.run = self.store.create_run(
            self.project, self.output,
            {"revision": "R1", "mandatory_cases": ["final"], "human_acceptance": True},
        )["id"]
        self.epoch = 1
        self.baseline = artifacts.initialize_repo(self.store, self.run, self.epoch)
        self.context = {"fixture_revision": "fixture1", "suite_revision": "suite1"}

    def _task(self, name="A", path="index.html", risk=False):
        self.store.add_task(
            self.run, self.epoch, {"id": name, "paths": [path], "risk": risk, "cases": [name]}
        )
        attempt = dispatch.reserve(self.store, self.run, self.epoch, name)["id"]
        worktree = artifacts.make_worktree(self.store, self.run, attempt)
        dispatch.bind_workspace(self.store, self.run, self.epoch, attempt, worktree)
        actor = f"author-{name}"
        dispatch.start(self.store, self.run, self.epoch, attempt, actor)
        (worktree / path).write_text(f"<html>change-{name}</html>", encoding="utf-8")
        artifacts.git(worktree, "add", path)
        artifacts.git(worktree, "commit", "--quiet", "-m", name)
        result = artifacts.capture_submission(self.store, self.run, attempt, actor)
        verification.record(
            self.store, self.run, self.epoch, "self", result["commit"], actor,
            {name: {"passed": True, "actual": "Observed after isolated test"}},
            {**self.context, "attempt_id": attempt},
        )
        dispatch.submit(self.store, self.run, self.epoch, attempt, {**result, "summary": "local test"})
        return attempt

    def _report(self, landed, actor="reviewer", **context):
        return verification.record(
            self.store, self.run, self.epoch, "delivery", landed["candidate"], actor,
            {"final": {"passed": True, "actual": "Observed from local file path"}},
            {
                **self.context, "operation_id": landed["operation_id"], "path": landed["entry"],
                "url": landed["url"], "browser": "TestBrowser", **context,
            },
        )

    def test_exact_merge_requires_nonauthor_proof_and_reconciles_ref_already_updated(self):
        attempt = self._task(risk=True)
        staged = artifacts.stage(self.store, self.run, self.epoch, [attempt])
        with self.assertRaisesRegex(ValueError, "integration"):
            artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        cases = {"A": {"status": "pass", "actual": "Independent replay"}}
        context = {**self.context, "operation_id": staged["operation_id"]}
        with self.assertRaisesRegex(ValueError, "作者"):
            verification.record(
                self.store, self.run, self.epoch, "integration", staged["candidate"], "author-A",
                cases, context,
            )
        verification.record(
            self.store, self.run, self.epoch, "integration", staged["candidate"], "reviewer",
            cases, context,
        )
        repo = artifacts.repo_path(self.store, self.run)
        artifacts.git(repo, "update-ref", artifacts.CANDIDATE_REF, staged["candidate"], self.baseline)
        self.epoch = self.store.take_over(self.run, self.epoch)["epoch"]
        receipt = artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        self.assertEqual(receipt["candidate"], staged["candidate"])
        self.assertEqual(receipt, artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"]))
        self.assertEqual(self.store.tasks(self.run)[0]["status"], "integrated")

    def test_delivery_recovers_memory_write_and_preserves_human_decision(self):
        attempt = self._task()
        staged = artifacts.stage(self.store, self.run, self.epoch, [attempt])
        artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        landed = delivery.land(self.store, self.run, self.epoch)
        self.assertEqual(landed, delivery.land(self.store, self.run, self.epoch))
        self.assertEqual(Path(landed["entry"]).read_text(), "<html>change-A</html>")
        with self.assertRaisesRegex(ValueError, "独立验证"):
            delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "master", "owner said yes")
        with self.assertRaisesRegex(ValueError, "file://"):
            self._report(landed, url="http://localhost/index.html")
        with self.assertRaisesRegex(ValueError, "作者"):
            self._report(landed, actor="author-A")
        self._report(landed)
        with self.assertRaisesRegex(ValueError, "人工验收"):
            delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "master")
        write_memory = delivery._write_memory

        def crash(*args):
            write_memory(*args)
            raise RuntimeError("interrupted before receipt")

        with patch.object(delivery, "_write_memory", crash), self.assertRaisesRegex(RuntimeError, "interrupted"):
            delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "master", "owner said yes")
        self.assertEqual(self.store.run(self.run)["status"], "active")
        self.epoch = self.store.take_over(self.run, self.epoch)["epoch"]
        receipt = delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "successor")
        self.assertEqual(receipt["actor"], "master")
        self.assertEqual(receipt["approval"], "owner said yes")
        self.assertEqual(self.store.run(self.run)["status"], "delivered")
        self.assertEqual(receipt, delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "successor"))
        self.assertEqual((self.project / "docs/change-log.md").read_text().count(receipt["change_id"]), 2)
        self.assertEqual(len(list((self.project / "memory/changes").glob("*.md"))), 1)

    def test_superseded_delivery_proof_cannot_authorize_new_candidate(self):
        a = self._task()
        staged = artifacts.stage(self.store, self.run, self.epoch, [a])
        artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        first = delivery.land(self.store, self.run, self.epoch)
        self._report(first)
        b = self._task(name="B", path="other.html")
        staged = artifacts.stage(self.store, self.run, self.epoch, [b])
        artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        with self.assertRaisesRegex(ValueError, "候选"):
            delivery.finalize(self.store, self.run, self.epoch, first["operation_id"], "master", "yes")
        second = delivery.land(self.store, self.run, self.epoch)
        self.assertNotEqual(first["candidate"], second["candidate"])
        with self.assertRaisesRegex(ValueError, "独立验证"):
            delivery.finalize(self.store, self.run, self.epoch, second["operation_id"], "master", "yes")
        self._report(second)
        self.assertEqual(
            delivery.finalize(self.store, self.run, self.epoch, second["operation_id"], "master", "yes")["candidate"],
            second["candidate"],
        )

    def test_advanced_parent_requires_restage_and_retains_the_other_change(self):
        a = self._task()
        b = self._task(name="B", path="other.html")
        staged_a = artifacts.stage(self.store, self.run, self.epoch, [a])
        staged_b = artifacts.stage(self.store, self.run, self.epoch, [b])
        artifacts.promote(self.store, self.run, self.epoch, staged_b["operation_id"])
        with self.assertRaisesRegex(ValueError, "父版本"):
            artifacts.promote(self.store, self.run, self.epoch, staged_a["operation_id"])
        restaged = artifacts.stage(self.store, self.run, self.epoch, [a])
        self.assertEqual(restaged["parent"], staged_b["candidate"])
        self.assertNotEqual(restaged["candidate"], staged_a["candidate"])
        self.assertEqual((Path(restaged["path"]) / "other.html").read_text(), "<html>change-B</html>")
        artifacts.promote(self.store, self.run, self.epoch, restaged["operation_id"])

    def test_tampered_archived_proof_blocks_acceptance(self):
        attempt = self._task()
        staged = artifacts.stage(self.store, self.run, self.epoch, [attempt])
        artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        landed = delivery.land(self.store, self.run, self.epoch)
        report = self.root / "proof.txt"
        report.write_text("first proof", encoding="utf-8")
        evidence = self._report(landed, report_path=str(report))
        Path(evidence["context"]["evidence"]).write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "delivery"):
            delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "master", "yes")
        self._report(landed)
        self.assertEqual(
            delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "master", "yes")["status"],
            "delivered",
        )

    def test_new_unmerged_task_blocks_already_verified_candidate(self):
        attempt = self._task()
        staged = artifacts.stage(self.store, self.run, self.epoch, [attempt])
        artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])
        landed = delivery.land(self.store, self.run, self.epoch)
        self._report(landed)
        self.store.add_task(self.run, self.epoch, {"id": "B", "paths": ["new.html"]})
        with self.assertRaisesRegex(ValueError, "未集成"):
            delivery.finalize(self.store, self.run, self.epoch, landed["operation_id"], "master", "yes")

    def test_unknown_output_is_preserved(self):
        self.output.mkdir()
        protected = self.output / "owner.txt"
        protected.write_text("retain me", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未确认"):
            delivery.land(self.store, self.run, self.epoch, allow_replace=True)
        self.assertEqual(protected.read_text(), "retain me")
        self.assertFalse((self.output / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
