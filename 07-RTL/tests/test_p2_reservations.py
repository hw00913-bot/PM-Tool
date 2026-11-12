from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from loop.lifecycle import supersede
from loop.reservations import reservations
from loop.state import LoopError, Store, pack


class LedgerReservationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.registry = self.root / "registry"
        self.one = self.store("one")
        self.two = self.store("two")
        self.project = self.root / "project"
        self.output = self.root / "export"

    def store(self, name, *, reserve=True):
        return Store(self.root / name, reserve=reserve, reservation_root=self.registry)

    def start(self, store, project=None, output=None, *, project_id=None, run_id=None):
        return store.create_run(
            project or self.project, output or self.output, {},
            project_id=project_id, run_id=run_id,
        )

    def test_rejects_same_project_id_and_path_overlap_across_ledgers(self):
        self.project.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        self.start(self.one, project_id="P", run_id="owner")

        conflicts = [
            (alias, self.root / "other-export", "Q"),
            (self.root / "other-project", self.output / "nested", "Q"),
            (self.output / "nested", self.root / "other-export", "Q"),
            (self.root / "other-project", self.root / "other-export", "P"),
        ]
        for project, output, project_id in conflicts:
            with self.subTest(project=project, output=output, project_id=project_id):
                with self.assertRaisesRegex(LoopError, "owner.*one"):
                    self.start(self.two, project, output, project_id=project_id)

        unrelated = self.start(
            self.two, self.root / "unrelated", self.root / "unrelated-export", project_id="Q",
        )
        self.assertEqual(unrelated["status"], "active")

    def test_rejects_runtime_under_an_active_project_output_or_peer_runtime_before_writes(self):
        self.start(self.one, project_id="P", run_id="owner")
        for data in (self.project / "data", self.output / "data", self.one.data_root / "nested"):
            with self.subTest(data=data):
                with self.assertRaisesRegex(LoopError, "reserved by active run owner"):
                    Store(data, reservation_root=self.registry)
                self.assertFalse(data.exists())

        nested_owner = Store(self.root / "outer" / "owner-data", reservation_root=self.registry)
        self.start(nested_owner, self.root / "other-project", self.root / "other-export", run_id="nested-owner")
        with self.assertRaisesRegex(LoopError, "reserved by active run nested-owner"):
            Store(nested_owner.data_root.parent, reservation_root=self.registry)
        self.assertFalse((nested_owner.data_root.parent / "state.sqlite").exists())
        self.assertEqual(self.store("one").run("owner")["status"], "active")

    def test_rejects_project_or_output_overlapping_an_active_peer_runtime(self):
        self.start(self.one, project_id="P", run_id="owner")
        for project, output in (
            (self.one.data_root / "project", self.root / "other-export"),
            (self.root / "other-project", self.one.data_root / "export"),
        ):
            with self.subTest(project=project, output=output):
                with self.assertRaisesRegex(LoopError, "reserved by active run owner"):
                    self.start(self.two, project, output, project_id="Q")
        self.assertEqual(self.two.runs(), [])

    def test_read_only_store_does_not_create_runtime_or_migrate_schema(self):
        missing = self.store("missing", reserve=False)
        self.assertEqual(missing.runs(), [])
        with self.assertRaisesRegex(LoopError, "no RTL ledger"):
            missing.run("none")
        self.assertFalse(missing.data_root.exists())

        existing = self.root / "uninitialized"
        existing.mkdir()
        db = existing / "state.sqlite"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE unrelated(id INTEGER)")
        original = db.read_bytes()
        self.store("uninitialized", reserve=False)
        self.assertEqual(db.read_bytes(), original)

    def test_only_one_simultaneous_start_claims_the_project(self):
        barrier = threading.Barrier(2)

        def start(store):
            barrier.wait()
            try:
                return self.start(store)["status"]
            except LoopError as error:
                self.assertIn("reserved by active run", str(error))
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(start, [self.one, self.two]))
        self.assertCountEqual(results, ["active", "blocked"])

    def test_terminal_run_releases_scope_and_missing_ledger_fails_closed(self):
        owner = self.start(self.one, project_id="P", run_id="owner")
        displaced = self.one.path.with_suffix(".saved")
        self.one.path.rename(displaced)
        try:
            with self.assertRaisesRegex(LoopError, "cannot verify reservation.*owner"):
                self.start(self.two, project_id="P")
        finally:
            displaced.rename(self.one.path)
        supersede(self.one, owner["id"], 1, reason="change", approval="confirmed")
        successor = self.start(self.two, project_id="P", run_id="successor")
        self.assertEqual(successor["status"], "active")

    def test_releases_exact_terminal_owner_before_its_ledger_is_removed(self):
        missing_registry = self.root / "missing-registry"
        Store(self.one.data_root, reserve=False, reservation_root=missing_registry).release_reservation("owner")
        self.assertFalse(missing_registry.exists())
        self.start(self.one, project_id="P", run_id="owner")
        self.start(self.two, self.root / "other", self.root / "other-output", project_id="Q", run_id="owner")
        self.one.release_reservation("owner")
        with self.assertRaisesRegex(LoopError, "reserved by active run owner"):
            self.start(self.store("third"), project_id="P")

        supersede(self.one, "owner", 1, reason="change", approval="confirmed")
        self.one.release_reservation("owner")
        self.one.release_reservation("owner")
        self.one.path.unlink()
        third = self.store("third")
        self.assertEqual(self.start(third, project_id="P")["status"], "active")
        with self.assertRaisesRegex(LoopError, "reserved by active run owner"):
            self.start(third, self.root / "other", self.root / "other-output", project_id="Q")

    def test_unreadable_registry_prevents_local_active_run_creation(self):
        (self.registry / "reservations.sqlite").write_text("not a database")
        with self.assertRaises(sqlite3.DatabaseError):
            self.start(self.one, project_id="P")
        self.assertEqual(self.one.runs(), [])

    def test_adopts_an_active_legacy_run_before_writing(self):
        with self.one.transaction() as db:
            db.execute("INSERT INTO projects(id,root,output) VALUES('P',?,?)",
                       (str(self.project), str(self.output)))
            db.execute(
                "INSERT INTO runs(id,project_id,project_root,output_path,baseline) VALUES('legacy','P',?,?,?)",
                (str(self.project), str(self.output), pack({})),
            )
        readonly = self.store("one", reserve=False)
        self.assertEqual(readonly.run("legacy")["status"], "active")
        with reservations(self.registry) as claims:
            self.assertIsNone(claims.settle(self.one.path, "legacy"))
        self.store("one")
        with self.assertRaisesRegex(LoopError, "reserved by active run legacy"):
            self.start(self.two, project_id="P")

    def test_rebind_checks_conflicts_and_transfers_active_scope(self):
        old = self.start(self.one, project_id="P")
        occupied = self.root / "other-project"
        occupied_output = self.root / "other-output"
        self.start(self.two, occupied, occupied_output, project_id="Q", run_id="other")
        with self.assertRaisesRegex(LoopError, "reserved by active run other"):
            self.one.rebind_project("P", self.root / "target", occupied_output / "subdir", epoch=1)
        self.assertEqual(self.one.run(old["id"])["output_path"], str(self.output))

        target = self.root / "target"
        self.one.rebind_project("P", target, target / "prototype", epoch=1)
        third = self.store("third")
        self.assertEqual(self.start(third, project_id="Q2")["status"], "active")
        with self.assertRaisesRegex(LoopError, "reserved by active run"):
            self.start(third, target, target / "prototype", project_id="Q3")

    def test_recovers_pending_first_reservation_after_process_exit(self):
        snippet = """
from pathlib import Path
import os
import sys

from loop.reservations import reservations

registry, ledger, project, output = map(Path, sys.argv[1:])
with reservations(registry) as claims:
    claims.prepare(ledger, 'interrupted', 'P', (str(project), str(output)))
    os._exit(0)
"""
        subprocess.run(
            [sys.executable, "-c", snippet, str(self.registry), str(self.one.path),
             str(self.project), str(self.output)],
            cwd=Path(__file__).resolve().parents[1], check=True, timeout=15,
        )
        self.assertEqual(self.one.runs(), [])
        self.assertEqual(self.start(self.two, project_id="P")["status"], "active")

    def test_recovers_both_outcomes_of_an_interrupted_rebind(self):
        run = self.start(self.one, project_id="P", run_id="owner")
        target = self.root / "target"
        proposed = (str(target), str(target / "prototype"))
        with reservations(self.registry) as claims:
            claims.prepare(self.one.path, run["id"], "P", proposed)
        transient = self.start(self.two, target, target / "prototype", project_id="Q")
        supersede(self.two, transient["id"], 1, reason="change", approval="confirmed")

        with reservations(self.registry) as claims:
            claims.prepare(self.one.path, run["id"], "P", proposed)
            with self.one.transaction() as db:
                db.execute("UPDATE projects SET root=?, output=? WHERE id='P'", proposed)
                db.execute("UPDATE runs SET project_root=?, output_path=? WHERE id='owner'", proposed)
        with self.assertRaisesRegex(LoopError, "reserved by active run owner"):
            self.start(self.store("third"), target, target / "prototype", project_id="R")
        self.assertEqual(self.start(self.store("third"), project_id="R")["status"], "active")
