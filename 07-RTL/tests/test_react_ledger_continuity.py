"""React continuation through a verified prior delivery in another ledger."""

import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from loop import __main__ as cli
from loop import artifacts, delivery, dispatch, lifecycle, requirements_run, verification
from loop.browser import asset_digest
from loop.state import Store
from test_requirements_history import plan_for


class ReactLedgerContinuityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.source = self.project / "prototype-src"
        self.source.mkdir(parents=True)
        (self.source / "package.json").write_text('{}', encoding="utf-8")
        (self.source / "package-lock.json").write_text('{}', encoding="utf-8")
        self.output = self.project / "prototype"
        self.old = Store(self.root / "old", reservation_root=self.root / "reservations")
        self.new = Store(self.root / "new", reservation_root=self.root / "reservations")
        self.context = {"suite_revision": "suite", "fixture_revision": "fixture"}
        self.cases = {"TC-B02": {"passed": True, "actual": "Verified the exact test build"}}
        mock = patch("loop.frontend.get_build", side_effect=self._build)
        mock.start()
        self.addCleanup(mock.stop)

    def _build(self, store, run, commit):
        repo = artifacts.repo_path(store, run)
        dist = self.root / "builds" / commit
        dist.mkdir(parents=True, exist_ok=True)
        entry = dist / "index.html"
        label = artifacts.git(repo, "show", f"{commit}:scene.jsx").decode()
        entry.write_text(f"<html>{label}</html>", encoding="utf-8")
        return {
            "source_commit": commit, "path": str(dist), "entry": str(entry),
            "manifest": delivery.manifest(dist), "asset_digest": asset_digest(dist),
            "lockfile_sha256": hashlib.sha256(b'{}').hexdigest(),
        }

    def _baseline(self, run):
        plan = plan_for(kind="add" if run == "first" else "verify")
        plan["tasks"][0]["paths"] = ["scene.jsx"]
        return {"revision": run, "requirements": plan, "mandatory_cases": ["TC-B02"]}

    def _init(self, store, run, **options):
        baseline = self._baseline(run)
        baseline["frontend"] = cli.select_frontend(store, self.project, "P", baseline, None, options.get("source_run"))
        self.assertEqual(baseline["frontend"], "react")
        store.create_run(self.project, self.output, baseline, project_id="P", run_id=run, **options)
        commit = artifacts.initialize_repo(store, run, 1)
        requirements_run.prepare(store, run, 1)
        return commit

    def _prove(self, store, run, kind, candidate, actor, **identity):
        if kind == "delivery":
            root = self.output
            identity["browser"] = "TestBrowser"
        else:
            root = Path(self._build(store, run, candidate)["path"])
        entry = root / "index.html"
        return verification.record(store, run, 1, kind, candidate, actor, self.cases, {
            **self.context, **identity, "path": str(entry), "url": entry.as_uri(), "asset_digest": asset_digest(root),
        })

    def _submit(self, store, run, worker, label=None):
        attempt = dispatch.reserve(store, run, 1, "TASK-OUTBOUND")["id"]
        worktree = artifacts.make_worktree(store, run, attempt)
        dispatch.bind_workspace(store, run, 1, attempt, worktree)
        dispatch.start(store, run, 1, attempt, worker)
        if label:
            (worktree / "scene.jsx").write_text(label, encoding="utf-8")
            artifacts.git(worktree, "add", "scene.jsx")
            artifacts.git(worktree, "commit", "--quiet", "-m", "Implement scene")
        result = artifacts.capture_submission(store, run, attempt, worker, revalidate=label is None)
        self._prove(store, run, "self", result["commit"], worker, attempt_id=attempt)
        dispatch.submit(store, run, 1, attempt, result)
        return attempt

    def _first_delivery(self):
        self._init(self.old, "first")
        attempt = self._submit(self.old, "first", "original-author", "original scene")
        staged = artifacts.stage(self.old, "first", 1, [attempt])
        artifacts.promote(self.old, "first", 1, staged["operation_id"])
        landed = delivery.land(self.old, "first", 1)
        self._prove(self.old, "first", "delivery", landed["candidate"], "reviewer", operation_id=landed["operation_id"])
        accepted = delivery.finalize(self.old, "first", 1, landed["operation_id"], "master")
        shutil.rmtree(self.source)
        return attempt, accepted

    def test_legacy_automatic_seed_retains_original_author_even_for_preupgrade_runs(self):
        legacy = {"revision": "legacy", "frontend": "react", "mandatory_cases": ["TC-B02"]}
        self.old.create_run(self.project, self.output, legacy, project_id="P", run_id="legacy")
        artifacts.initialize_repo(self.old, "legacy", 1)
        self.old.add_task("legacy", 1, {
            "id": "TASK-OUTBOUND", "paths": ["scene.jsx"], "cases": ["TC-B02"],
        })
        attempt = self._submit(self.old, "legacy", "original-author", "original scene")
        staged = artifacts.stage(self.old, "legacy", 1, [attempt])
        artifacts.promote(self.old, "legacy", 1, staged["operation_id"])
        landed = delivery.land(self.old, "legacy", 1)
        self._prove(self.old, "legacy", "delivery", landed["candidate"], "reviewer", operation_id=landed["operation_id"])
        accepted = delivery.finalize(self.old, "legacy", 1, landed["operation_id"], "master")
        shutil.rmtree(self.source)

        baseline = self._baseline("upgraded")
        baseline["requirements"] = plan_for(kind="add")
        baseline["requirements"]["tasks"][0]["paths"] = ["unrelated.jsx"]
        baseline["frontend"] = "react"
        self.old.create_run(self.project, self.output, baseline, project_id="P", run_id="upgraded")
        artifacts.initialize_repo(self.old, "upgraded", 1)
        requirements_run.prepare(self.old, "upgraded", 1)
        attempt = dispatch.reserve(self.old, "upgraded", 1, "TASK-OUTBOUND")["id"]
        worktree = artifacts.make_worktree(self.old, "upgraded", attempt)
        dispatch.bind_workspace(self.old, "upgraded", 1, attempt, worktree)
        dispatch.start(self.old, "upgraded", 1, attempt, "new-worker")
        (worktree / "unrelated.jsx").write_text("new independent file", encoding="utf-8")
        artifacts.git(worktree, "add", "unrelated.jsx")
        artifacts.git(worktree, "commit", "--quiet", "-m", "Separate addition")
        result = artifacts.capture_submission(self.old, "upgraded", attempt, "new-worker")
        self._prove(self.old, "upgraded", "self", result["commit"], "new-worker", attempt_id=attempt)
        dispatch.submit(self.old, "upgraded", 1, attempt, result)
        staged = artifacts.stage(self.old, "upgraded", 1, [attempt])
        artifacts.promote(self.old, "upgraded", 1, staged["operation_id"])
        landed = delivery.land(self.old, "upgraded", 1, allow_replace=True)

        def report(actor):
            return self._prove(self.old, "upgraded", "delivery", landed["candidate"], actor,
                               operation_id=landed["operation_id"])

        with self.assertRaisesRegex(ValueError, "作者"):
            report("original-author")
        seed = self.old.operation("upgraded", "seed-from")
        self.assertEqual(seed["payload"], {"source_run": "legacy", "candidate": accepted["candidate"]})
        self.assertIn("original scene", (self.output / "index.html").read_text())
        # Model a run whose seed was imported by the previous installed RTL.
        with self.old.transaction() as db:
            db.execute("DELETE FROM operations WHERE run_id='upgraded' AND id='seed-from'")
        with self.assertRaisesRegex(ValueError, "作者"):
            report("original-author")
        report("independent")
        self.assertEqual(delivery.finalize(self.old, "upgraded", 1, landed["operation_id"], "master")["status"], "delivered")

    def test_explicit_foreign_seed_preserves_candidate_ancestry_and_authorship(self):
        previous, accepted = self._first_delivery()
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        self.new.create_run(unrelated, unrelated / "prototype", {}, project_id="unrelated", run_id="first")
        candidate = self._init(self.new, "next", source_run="first")
        self.assertEqual(candidate, accepted["candidate"])
        self.assertEqual(artifacts.git(artifacts.repo_path(self.new, "next"), "show", f"{candidate}:scene.jsx"),
                         b"original scene")
        attempt = self._submit(self.new, "next", "revalidator")
        result = self.new.attempt(attempt)["result"]
        self.assertEqual((result["revalidation_of"], result["revalidation_ledger"]), (previous, str(self.old.path)))
        staged = artifacts.stage(self.new, "next", 1, [attempt])
        with self.assertRaisesRegex(ValueError, "作者"):
            self._prove(self.new, "next", "integration", candidate, "original-author",
                        operation_id=staged["operation_id"])
        self._prove(self.new, "next", "integration", candidate, "independent", operation_id=staged["operation_id"])
        artifacts.promote(self.new, "next", 1, staged["operation_id"])
        landed = delivery.land(self.new, "next", 1)
        with self.assertRaisesRegex(ValueError, "作者"):
            self._prove(self.new, "next", "delivery", candidate, "original-author", operation_id=landed["operation_id"])
        self._prove(self.new, "next", "delivery", candidate, "independent", operation_id=landed["operation_id"])
        self.assertEqual(delivery.finalize(self.new, "next", 1, landed["operation_id"], "master")["status"], "delivered")

    def test_automatic_foreign_seed_allows_only_exact_previous_output_replacement(self):
        _, accepted = self._first_delivery()
        self.assertEqual(self._init(self.new, "next"), accepted["candidate"])
        attempt = self._submit(self.new, "next", "successor", "updated scene")
        staged = artifacts.stage(self.new, "next", 1, [attempt])
        artifacts.promote(self.new, "next", 1, staged["operation_id"])
        note = self.output / "keep.txt"
        note.write_text("user content", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未确认"):
            delivery.land(self.new, "next", 1, allow_replace=True)
        self.assertEqual(note.read_text(encoding="utf-8"), "user content")
        note.unlink()
        with self.assertRaisesRegex(ValueError, "未确认"):
            delivery.land(self.new, "next", 1)
        landed = delivery.land(self.new, "next", 1, allow_replace=True)
        op = self.new.operation("next", landed["operation_id"])
        self.assertIn("original scene", (Path(op["payload"]["previous"]) / "index.html").read_text())
        self.assertIn("updated scene", (self.output / "index.html").read_text())

        lifecycle.supersede(self.new, "next", 1, reason="updated requirement", approval="confirmed")
        self._init(self.new, "latest", source_run="next")
        attempt = self._submit(self.new, "latest", "successor", "superseding scene")
        staged = artifacts.stage(self.new, "latest", 1, [attempt])
        artifacts.promote(self.new, "latest", 1, staged["operation_id"])
        delivery.land(self.new, "latest", 1, allow_replace=True)
        self.assertIn("superseding scene", (self.output / "index.html").read_text())

    def _move_project(self, name, *, output_name="prototype"):
        destination = self.root / name
        relocated_output = destination / self.output.relative_to(self.project)
        self.project.rename(destination)
        self.project = destination
        self.output = destination / output_name
        if relocated_output != self.output:
            relocated_output.rename(self.output)

    def _integrate_moved_project_change(self, store):
        attempt = self._submit(store, "next", "successor", "updated scene")
        staged = artifacts.stage(store, "next", 1, [attempt])
        return artifacts.promote(store, "next", 1, staged["operation_id"])

    def _record_preupgrade_binding(self):
        # Model an explicitly approved rebind written by the old schema.
        with self.old.transaction() as db:
            db.execute("DROP TABLE IF EXISTS project_rebinds")
            db.execute("PRAGMA user_version = 0")
            db.execute("UPDATE projects SET root=?,output=? WHERE id='P'", (str(self.project), str(self.output)))

    def test_recorded_project_migrations_allow_foreign_seed_and_exact_replacement(self):
        _, accepted = self._first_delivery()
        historical = self.old.run("first")
        archived = self.old.operation("first", accepted["operation_id"])
        self._move_project("relocated", output_name="preview")
        self.old.rebind_project("P", self.project, self.output)
        self._move_project("final", output_name="handoff")
        self.old.rebind_project("P", self.project, self.output)

        self.assertEqual(self._init(self.new, "next", source_run="first"), accepted["candidate"])
        self._integrate_moved_project_change(self.new)
        marker = self.output / "keep.txt"
        marker.write_text("user-added content", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未确认"):
            delivery.land(self.new, "next", 1, allow_replace=True)
        self.assertTrue(marker.is_file())
        marker.unlink()
        with self.assertRaisesRegex(ValueError, "未确认"):
            delivery.land(self.new, "next", 1)
        landed = delivery.land(self.new, "next", 1, allow_replace=True)
        op = self.new.operation("next", landed["operation_id"])
        self.assertEqual(delivery.manifest(Path(op["payload"]["previous"])), archived["payload"]["manifest"])
        self.assertIn("updated scene", (self.output / "index.html").read_text())
        lifecycle.supersede(self.new, "next", 1, reason="rejected", approval="owner decision")
        self.new.release_reservation("next")
        rollback = delivery.rollback_landing(self.new, "next", 1, landed["operation_id"], "Rejected candidate")
        self.assertEqual(delivery.manifest(self.output), archived["payload"]["manifest"])
        self.assertEqual(delivery.manifest(Path(rollback["rejected_archive"])), op["payload"]["manifest"])
        self.assertEqual(self.old.run("first"), historical)
        self.assertEqual(self.old.operation("first", accepted["operation_id"]), archived)

    def test_preupgrade_move_preserves_same_relative_output_replacement_in_local_ledger(self):
        _, accepted = self._first_delivery()
        self._move_project("relocated")
        self._record_preupgrade_binding()
        self._init(self.old, "next")
        self._integrate_moved_project_change(self.old)
        landed = delivery.land(self.old, "next", 1, allow_replace=True)
        op = self.old.operation("next", landed["operation_id"])
        expected = self.old.operation("first", accepted["operation_id"])["payload"]["manifest"]
        self.assertEqual(delivery.manifest(Path(op["payload"]["previous"])), expected)

    def test_preupgrade_binding_can_reuse_source_but_cannot_translate_unknown_output(self):
        _, accepted = self._first_delivery()
        self._move_project("relocated", output_name="different-output")
        self._record_preupgrade_binding()
        self.assertEqual(self._init(self.new, "next"), accepted["candidate"])
        self._integrate_moved_project_change(self.new)
        with self.assertRaisesRegex(ValueError, "未确认"):
            delivery.land(self.new, "next", 1, allow_replace=True)
        self.assertIn("original scene", (self.output / "index.html").read_text())

    def test_unrecorded_move_cannot_use_old_ledger_as_foreign_source(self):
        self._first_delivery()
        self._move_project("relocated")
        with self.assertRaisesRegex(ValueError, "原交付回执与当前项目"):
            self._init(self.new, "next", source_run="first")
        self.assertEqual(self.new.runs(), [])

    def test_missing_foreign_source_stops_before_creating_run(self):
        _, accepted = self._first_delivery()
        shutil.rmtree(accepted["source"])
        with self.assertRaisesRegex(ValueError, "原 React 源码交接缺失"):
            self._init(self.new, "next")
        self.assertEqual(self.new.runs(), [])

    def test_explicit_react_profile_inherits_latest_react_even_after_static_delivery(self):
        _, accepted = self._first_delivery()
        baseline = self._baseline("static")
        baseline["frontend"] = "static"
        baseline["requirements"]["tasks"][0]["paths"] = ["index.html"]
        static_output = self.project / "prototype-static"
        self.new.create_run(self.project, static_output, baseline, project_id="P", run_id="static")
        artifacts.initialize_repo(self.new, "static", 1)
        requirements_run.prepare(self.new, "static", 1)
        attempt = dispatch.reserve(self.new, "static", 1, "TASK-OUTBOUND")["id"]
        worktree = artifacts.make_worktree(self.new, "static", attempt)
        dispatch.bind_workspace(self.new, "static", 1, attempt, worktree)
        dispatch.start(self.new, "static", 1, attempt, "static-author")
        (worktree / "index.html").write_text("<html>static scene</html>", encoding="utf-8")
        artifacts.git(worktree, "add", "index.html")
        artifacts.git(worktree, "commit", "--quiet", "-m", "Implement static scene")
        result = artifacts.capture_submission(self.new, "static", attempt, "static-author")
        verification.record(self.new, "static", 1, "self", result["commit"], "static-author", self.cases,
                            {**self.context, "attempt_id": attempt})
        dispatch.submit(self.new, "static", 1, attempt, result)
        staged = artifacts.stage(self.new, "static", 1, [attempt])
        artifacts.promote(self.new, "static", 1, staged["operation_id"])
        landed = delivery.land(self.new, "static", 1)
        verification.record(self.new, "static", 1, "delivery", landed["candidate"], "reviewer", self.cases, {
            **self.context, "operation_id": landed["operation_id"], "path": landed["entry"], "url": landed["url"],
            "browser": "TestBrowser",
        })
        delivery.finalize(self.new, "static", 1, landed["operation_id"], "master")

        third = Store(self.root / "third", reservation_root=self.root / "reservations")
        successor = {**self._baseline("react-again"), "frontend": "react"}
        third.create_run(self.project, self.output, successor, project_id="P", run_id="react-again")
        self.assertEqual(artifacts.initialize_repo(third, "react-again", 1), accepted["candidate"])
