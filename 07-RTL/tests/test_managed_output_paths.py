import tempfile
import unittest
from pathlib import Path

from loop import artifacts, delivery
from loop.state import LoopError, Store, pack, unpack


class ManagedOutputPathsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        self.project = root / "project"
        self.project.mkdir()
        self.store = Store(root / "data", reservation_root=root / "reservations")
        self.baseline = {"revision": "rev"}

    def test_initialization_refuses_managed_paths_but_accepts_disjoint_subdirectory(self):
        for relative in (
            "docs", "docs/change-log.md", "docs/design-style.md", "docs/feature-map.md",
            "docs/需求台账.md", "memory", "memory/baselines", "memory/changes",
            "memory/changes/build", "memory/contracts", "memory/project.md",
            "memory/requirements-history.json", "memory/requirements-revisions.json",
        ):
            with self.subTest(path=relative):
                with self.assertRaisesRegex(LoopError, "RTL 管理路径重叠"):
                    self.store.create_run(self.project, self.project / relative, self.baseline)
        safe = self.project / "docs/export"
        run = self.store.create_run(self.project, safe, self.baseline, project_id="P", run_id="R")
        self.assertEqual(run["output_path"], str(safe))

    def test_rebind_rejects_a_managed_path_alias_and_keeps_original_binding(self):
        safe = self.project / "prototype"
        self.store.create_run(self.project, safe, self.baseline, project_id="P", run_id="R")
        memory = self.project / "memory"
        memory.mkdir()
        alias = self.project / "outside-changes"
        alias.mkdir()
        (memory / "changes").symlink_to(alias, target_is_directory=True)
        with self.assertRaisesRegex(LoopError, "RTL 管理路径重叠"):
            self.store.rebind_project("P", self.project, alias, epoch=1)
        self.assertEqual(self.store.run("R")["output_path"], str(safe))

    def test_legacy_overlapping_run_cannot_land_or_finalize(self):
        safe = self.project / "prototype"
        safe.mkdir()
        (safe / "index.html").write_text("known content", encoding="utf-8")
        self.store.create_run(self.project, safe, self.baseline, project_id="P", run_id="R")
        artifacts.initialize_repo(self.store, "R", 1)
        landed = delivery.land(self.store, "R", 1)
        protected = self.project / "memory/changes"
        protected.parent.mkdir()
        safe.rename(protected)
        operation_id = landed["operation_id"]
        with self.store.transaction() as db:
            db.execute("UPDATE runs SET output_path=? WHERE id='R'", (str(protected),))
            db.execute("UPDATE projects SET output=? WHERE id='P'", (str(protected),))
            row = db.execute("SELECT payload FROM operations WHERE id=?", (operation_id,)).fetchone()
            payload = {**unpack(row["payload"]), "output": str(protected)}
            db.execute("UPDATE operations SET payload=? WHERE id=?", (pack(payload), operation_id))

        before = delivery.manifest(protected)
        with self.assertRaisesRegex(LoopError, "RTL 管理路径重叠"):
            delivery.land(self.store, "R", 1)
        with self.assertRaisesRegex(LoopError, "RTL 管理路径重叠"):
            delivery.finalize(self.store, "R", 1, operation_id, "master")
        self.assertEqual(delivery.manifest(protected), before)
        self.assertEqual(self.store.run("R")["status"], "active")
        self.assertEqual(self.store.operation("R", operation_id)["status"], "intent")
        self.assertFalse((self.project / "docs/change-log.md").exists())
