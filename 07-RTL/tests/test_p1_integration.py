from argparse import Namespace
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop import artifacts, browser, delivery, dispatch, lifecycle, verification
from loop.__main__ import record_evidence
from loop.state import Store


class ImpactedIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        project = root / "project"
        (project / "prototype").mkdir(parents=True)
        (project / "prototype" / "index.html").write_text("<html>seed</html>", encoding="utf-8")
        self.store = Store(root / "data", reservation_root=root / "reservations")
        self.run = self.store.create_run(
            project, root / "output", {"revision": "r1", "mandatory_cases": ["A", "C", "Shared"]},
        )["id"]
        artifacts.initialize_repo(self.store, self.run, 1)
        self.context = {"fixture_revision": "fixture1", "suite_revision": "suite1"}

    def _register(self, task, **spec):
        self.store.add_task(
            self.run, 1, {"id": task, "paths": [f"{task}.html"], "cases": [task], **spec},
        )

    def _claim(self, task, actor):
        attempt = dispatch.reserve(self.store, self.run, 1, task)["id"]
        worktree = artifacts.make_worktree(self.store, self.run, attempt)
        dispatch.bind_workspace(self.store, self.run, 1, attempt, worktree)
        dispatch.start(self.store, self.run, 1, attempt, actor)
        return attempt, worktree

    def _submit(self, task, *, change=None, revalidate=False, actor=None):
        actor = actor or f"author-{task}"
        attempt, worktree = self._claim(task, actor)
        if change is not None:
            path = worktree / f"{task}.html"
            path.write_text(change, encoding="utf-8")
            artifacts.git(worktree, "add", path.name)
            artifacts.git(worktree, "commit", "--quiet", "-m", task)
        result = artifacts.capture_submission(self.store, self.run, attempt, actor, revalidate=revalidate)
        verification.record(
            self.store, self.run, 1, "self", result["commit"], actor,
            {task: {"status": "pass", "actual": "Observed expected user result"}},
            {**self.context, "attempt_id": attempt},
        )
        dispatch.submit(self.store, self.run, 1, attempt, result)
        return attempt

    def _prove(self, staged, outcomes, actor="reviewer", *, report=None):
        context = {**self.context, "operation_id": staged["operation_id"]}
        if report is not None:
            context["evidence"] = str(report)
        return verification.record(
            self.store, self.run, 1, "integration", staged["candidate"], actor,
            {case: {"status": outcome, "actual": f"Observed {outcome}"} for case, outcome in outcomes.items()},
            context,
        )

    def _promote(self, staged):
        return artifacts.promote(self.store, self.run, 1, staged["operation_id"])

    def test_low_risk_integration_failure_blocks_until_the_same_case_passes(self):
        self._register("C", risk=False)
        attempt = self._submit("C", change="save layout")
        staged = artifacts.stage(self.store, self.run, 1, [attempt])
        self.assertEqual(staged["required_cases"], [])
        self._prove(staged, {"C": "fail"})
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(staged)
        repository = artifacts.repo_path(self.store, self.run)
        self.assertEqual(artifacts.rev(repository, artifacts.CANDIDATE_REF), staged["parent"])
        self.assertEqual(self.store.run(self.run)["candidate"], staged["parent"])
        operation = self.store.operation(self.run, staged["operation_id"])
        self.assertEqual(operation["status"], "intent")
        self.assertIsNone(operation["receipt"])
        self.assertEqual(self.store.tasks(self.run)[0]["status"], "submitted")

        for outcomes in ({"A": "pass"}, {"C": "blocked"}, {"C": "not_run"}):
            with self.subTest(outcomes=outcomes):
                self._prove(staged, outcomes)
                with self.assertRaisesRegex(ValueError, r"integration.*C"):
                    self._promote(staged)

        self._prove(staged, {"C": "pass"})
        receipt = self._promote(staged)
        self.assertEqual(receipt["candidate"], staged["candidate"])
        self.assertEqual(self._promote(staged), receipt)

    def test_supplemental_failure_blocks_even_with_passing_required_case(self):
        self._register("A", risk=True)
        attempt = self._submit("A", change="new category")
        staged = artifacts.stage(self.store, self.run, 1, [attempt])
        self._prove(staged, {"A": "pass", "C": "fail"})
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(staged)
        self._prove(staged, {"C": "pass"})
        self.assertEqual(self._promote(staged)["candidate"], staged["candidate"])

    def test_missing_failure_archive_and_damaged_retest_cannot_clear_failure(self):
        self._register("C", risk=False)
        attempt = self._submit("C", change="save layout")
        staged = artifacts.stage(self.store, self.run, 1, [attempt])
        report = self.store.data_root.parent / "report.json"
        report.write_text('{"cases":{"C":{"status":"fail"}}}', encoding="utf-8")
        failure = self._prove(staged, {"C": "fail"}, report=report)
        Path(failure["context"]["evidence"]).unlink()
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(staged)

        report.write_text('{"cases":{"C":{"status":"pass"}}}', encoding="utf-8")
        retest = self._prove(staged, {"C": "pass"}, report=report)
        Path(retest["context"]["evidence"]).write_text("changed archive", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(staged)

        self._prove(staged, {"C": "pass"}, report=report)
        self.assertEqual(self._promote(staged)["candidate"], staged["candidate"])

    def test_failed_task_requires_fresh_evidence_after_restage_and_replacement(self):
        self._register("C", risk=False)
        attempt = self._submit("C", change="save layout")
        failed = artifacts.stage(self.store, self.run, 1, [attempt], required_cases=["C"])
        self._prove(failed, {"C": "fail"})
        self._prove(failed, {"C": "pass"})
        restaged = artifacts.stage(self.store, self.run, 1, [attempt])
        repo = artifacts.repo_path(self.store, self.run)
        self.assertEqual(artifacts.rev(repo, failed["candidate"] + "^{tree}"),
                         artifacts.rev(repo, restaged["candidate"] + "^{tree}"))
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(restaged)
        self.assertEqual(restaged["retest_cases"], ["C"])

        dispatch.fence(self.store, self.run, 1, attempt)
        replacement = self._submit("C", change="persist compact layout")
        repaired = artifacts.stage(self.store, self.run, 1, [replacement])
        self.assertEqual(repaired["retest_cases"], ["C"])
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(repaired)
        self._prove(repaired, {"C": "pass"})
        self.assertEqual(self._promote(repaired)["candidate"], repaired["candidate"])

        dispatch.fence(self.store, self.run, 1, replacement)
        next_attempt = self._submit("C", change="change layout label")
        later = artifacts.stage(self.store, self.run, 1, [next_attempt])
        self.assertNotIn("retest_cases", later)
        self.assertEqual(self._promote(later)["candidate"], later["candidate"])

    def test_preexisting_stage_pass_does_not_clear_a_new_related_failure(self):
        self._register("C", risk=False)
        self._register("A", risk=False)
        attempt = self._submit("C", change="save layout")
        first = artifacts.stage(self.store, self.run, 1, [attempt])
        second = artifacts.stage(self.store, self.run, 1, [attempt])
        self._prove(second, {"C": "pass"})
        self._prove(first, {"C": "fail"})
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(second)

        unrelated = self._submit("A", change="category labels")
        staged = artifacts.stage(self.store, self.run, 1, [unrelated])
        self.assertNotIn("retest_cases", staged)
        self.assertEqual(self._promote(staged)["candidate"], staged["candidate"])

        successor = artifacts.stage(self.store, self.run, 1, [attempt])
        self._prove(successor, {"C": "pass"})
        self.assertEqual(self._promote(successor)["candidate"], successor["candidate"])

    def test_different_same_id_assertion_cannot_clear_failure_but_equivalent_slice_can(self):
        contexts = self.context
        standard = {"id": "C", "steps": [{"op": "text", "selector": "h1", "value": "Standard"}]}
        compact = {"id": "C", "steps": [{"op": "text", "selector": "h1", "value": "Compact"}]}
        auxiliary = {"id": "A", "steps": [{"op": "visible", "selector": "h1"}]}
        def suite(*cases):
            return json.dumps({**contexts, "cases": cases})

        approved = suite(standard)
        weaker = suite(compact)
        same_case_full_suite = suite(standard, auxiliary)
        originals = (approved, weaker, same_case_full_suite)
        frozen = {
            hashlib.sha256(raw.encode()).hexdigest(): [case["id"] for case in json.loads(raw)["cases"]]
            for raw in originals
        }

        prior = self.store.run(self.run)
        lifecycle.supersede(self.store, self.run, 1, reason="freeze fixture", approval="confirmed")
        self.run = self.store.create_run(prior["project_root"], prior["output_path"], {
            "revision": "frozen", "mandatory_cases": ["C"], **contexts, "suite_digests": frozen,
        })["id"]
        artifacts.initialize_repo(self.store, self.run, 1)

        def record(raw, path, *, target, name, outcomes, **identity):
            entry = Path(path) / "index.html"
            target_report = self.store.data_root.parent / f"{name}.json"
            target_report.write_text(json.dumps({
                **contexts, "protocol": "file:", "path": str(entry), "entry": entry.as_uri(),
                "browser": "Fixture browser 1", "asset_digest": browser.asset_digest(path),
                "suite_digest": hashlib.sha256(raw.encode()).hexdigest(), "suite_input": raw,
                "cases": {
                    case: {"status": outcome, "executed": ["text h1"], "errors": []}
                    for case, outcome in outcomes.items()
                },
            }), encoding="utf-8")
            context = self.store.data_root.parent / f"{name}-context.json"
            context.write_text(json.dumps(identity), encoding="utf-8")
            return record_evidence(self.store, Namespace(
                report=target_report, context=context, run=self.run, epoch=1, target=target,
                kind="self" if "attempt_id" in identity else "integration",
                actor="author-C" if "attempt_id" in identity else "reviewer",
            ))

        self._register("C", risk=False)
        attempt, worktree = self._claim("C", "author-C")
        (worktree / "C.html").write_text("save layout", encoding="utf-8")
        artifacts.git(worktree, "add", "C.html")
        artifacts.git(worktree, "commit", "--quiet", "-m", "layout")
        submission = artifacts.capture_submission(self.store, self.run, attempt, "author-C")
        record(approved, worktree, target=submission["commit"], name="self-proof",
               outcomes={"C": "pass"}, attempt_id=attempt)
        dispatch.submit(self.store, self.run, 1, attempt, submission)

        stage = artifacts.stage(self.store, self.run, 1, [attempt])
        bound = {"target": stage["candidate"], "operation_id": stage["operation_id"]}
        failure = record(approved, stage["path"], name="failed", outcomes={"C": "fail"}, **bound)
        Path(failure["context"]["evidence"]).unlink()
        record(weaker, stage["path"], name="different", outcomes={"C": "pass"}, **bound)
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(stage)
        next_stage = artifacts.stage(self.store, self.run, 1, [attempt])
        next_bound = {"target": next_stage["candidate"], "operation_id": next_stage["operation_id"]}
        record(weaker, next_stage["path"], name="different-retry", outcomes={"C": "pass"}, **next_bound)
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(next_stage)
        record(same_case_full_suite, next_stage["path"], name="matching",
               outcomes={"C": "pass", "A": "pass"}, **next_bound)
        self.assertEqual(self._promote(next_stage)["candidate"], next_stage["candidate"])

    def test_shared_task_cannot_promote_when_an_existing_scene_fails_required_replay(self):
        self._register("C")
        scene = self._submit("C", change="save layout")
        self._promote(artifacts.stage(self.store, self.run, 1, [scene]))

        self._register("Shared", risk=True)
        shared = self._submit("Shared", change="new shared defaults")
        stage = artifacts.stage(self.store, self.run, 1, [shared], required_cases=["C"])
        self.assertEqual(stage["required_cases"], ["C"])
        self._prove(stage, {"Shared": "pass", "C": "fail"})
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(stage)
        self.assertEqual(self.store.run(self.run)["candidate"], stage["parent"])
        self._prove(stage, {"C": "pass"})
        self.assertEqual(self._promote(stage)["candidate"], stage["candidate"])

    def test_revalidation_can_integrate_after_upstream_changed_without_new_source_commit(self):
        self._register("A")
        self._register("C", deps=["A"])
        old_a = self._submit("A", change="v1")
        self._promote(artifacts.stage(self.store, self.run, 1, [old_a]))
        old_c = self._submit("C", change="save layout")
        self._promote(artifacts.stage(self.store, self.run, 1, [old_c]))
        dispatch.fence(self.store, self.run, 1, old_a)
        self.assertEqual(self.store.attempt(old_c)["state"], "fenced")

        new_a = self._submit("A", change="v2")
        updated = self._promote(artifacts.stage(self.store, self.run, 1, [new_a]))
        new_c = self._submit("C", revalidate=True)
        result = self.store.attempt(new_c)["result"]
        self.assertEqual(result["revalidation_of"], old_c)
        self.assertEqual(result["paths"], [])
        stage = artifacts.stage(self.store, self.run, 1, [new_c])
        self.assertEqual(stage["candidate"], updated["candidate"])
        with self.assertRaisesRegex(ValueError, r"integration.*C"):
            self._promote(stage)
        self._prove(stage, {"C": "pass"})
        self.assertEqual(self._promote(stage)["candidate"], updated["candidate"])
        self.assertEqual({task["status"] for task in self.store.tasks(self.run)}, {"integrated"})

    def test_revalidation_can_share_candidate_with_an_unrelated_patch(self):
        self._register("C")
        prior = self._submit("C", change="save layout")
        self._promote(artifacts.stage(self.store, self.run, 1, [prior]))
        dispatch.fence(self.store, self.run, 1, prior)
        revalidated = self._submit("C", revalidate=True)
        self._register("A")
        patch = self._submit("A", change="unrelated page")
        staged = artifacts.stage(self.store, self.run, 1, [revalidated, patch])
        self.assertNotEqual(staged["candidate"], staged["parent"])
        self._prove(staged, {"C": "pass"})
        self._promote(staged)
        self.assertEqual((Path(staged["path"]) / "C.html").read_text(encoding="utf-8"), "save layout")

    def test_revalidation_must_be_replayed_if_the_parent_advances(self):
        self._register("C")
        prior = self._submit("C", change="save layout")
        self._promote(artifacts.stage(self.store, self.run, 1, [prior]))
        dispatch.fence(self.store, self.run, 1, prior)
        revalidated = self._submit("C", revalidate=True)

        self._register("A")
        patch = self._submit("A", change="unrelated page")
        self._promote(artifacts.stage(self.store, self.run, 1, [patch]))
        with self.assertRaisesRegex(ValueError, "基础版本已改变"):
            artifacts.stage(self.store, self.run, 1, [revalidated])

    def test_revalidation_reuses_an_integrated_task_in_a_confirmed_source_run(self):
        self._register("C")
        prior = self._submit("C", change="unchanged layout rule")
        self._promote(artifacts.stage(self.store, self.run, 1, [prior]))
        old = self.store.run(self.run)
        lifecycle.supersede(self.store, self.run, 1, reason="change A's rule", approval="confirmed")
        self.run = self.store.create_run(
            old["project_root"], old["output_path"], {"revision": "r2", "mandatory_cases": ["C"]},
            source_run=old["id"],
        )["id"]
        base = artifacts.initialize_repo(self.store, self.run, 1)
        self._register("C")
        current = self._submit("C", revalidate=True)
        self.assertEqual(self.store.attempt(current)["result"]["revalidation_of"], prior)
        staged = artifacts.stage(self.store, self.run, 1, [current])
        self.assertEqual(staged["candidate"], base)
        self._prove(staged, {"C": "pass"})
        self._promote(staged)
        self.assertEqual(self.store.tasks(self.run)[0]["status"], "integrated")

    def test_first_implementation_cannot_be_submitted_as_revalidation(self):
        self._register("C")
        attempt, _ = self._claim("C", "author-C")
        with self.assertRaisesRegex(ValueError, "曾成功合入"):
            artifacts.capture_submission(self.store, self.run, attempt, "author-C", revalidate=True)

    def test_original_author_cannot_review_revalidated_and_seeded_source(self):
        self._register("C")
        previous = self._submit("C", change="save layout")
        self._promote(artifacts.stage(self.store, self.run, 1, [previous]))
        for actor in ("first-recheck", "second-recheck"):
            dispatch.fence(self.store, self.run, 1, previous)
            previous = self._submit("C", revalidate=True, actor=actor)
            staged = artifacts.stage(self.store, self.run, 1, [previous])
            with self.assertRaisesRegex(ValueError, "作者"):
                self._prove(staged, {"C": "pass"}, actor="author-C")
            with patch.object(verification, "attempt_authors", return_value={actor}):
                self._prove(staged, {"C": "pass"}, actor="author-C")
            with self.assertRaisesRegex(ValueError, "integration"):
                self._promote(staged)
            self._prove(staged, {"C": "pass"})
            self._promote(staged)

        old = self.store.run(self.run)
        lifecycle.supersede(self.store, self.run, 1, reason="change another scene", approval="confirmed")
        self.run = self.store.create_run(
            old["project_root"], old["output_path"], {"revision": "r2", "mandatory_cases": ["C"]},
            source_run=old["id"],
        )["id"]
        artifacts.initialize_repo(self.store, self.run, 1)
        landed = delivery.land(self.store, self.run, 1)
        self.assertEqual((Path(landed["entry"]).parent / "C.html").read_text(), "save layout")
        context = {
            **self.context, "operation_id": landed["operation_id"], "path": landed["entry"],
            "url": landed["url"], "browser": "TestBrowser",
        }
        cases = {"C": {"status": "pass", "actual": "Layout saved"}}
        def report(actor):
            verification.record(self.store, self.run, 1, "delivery", landed["candidate"], actor, cases, context)

        with self.assertRaisesRegex(ValueError, "作者"):
            report("author-C")
        with patch.object(delivery, "delivery_authors", return_value=set()):
            report("author-C")
        with self.assertRaisesRegex(ValueError, "独立验证"):
            delivery.finalize(self.store, self.run, 1, landed["operation_id"], "master")
        report("reviewer")
        delivery.finalize(self.store, self.run, 1, landed["operation_id"], "master")
        self.assertEqual(self.store.run(self.run)["status"], "delivered")
