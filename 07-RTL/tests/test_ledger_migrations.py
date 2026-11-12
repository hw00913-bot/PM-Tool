from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from loop import artifacts, browser, delivery
from loop.ledger_schema import CURRENT_VERSION
from loop.state import Store


ROOT = Path(__file__).resolve().parents[1]
CLI = (
    "import sys; from functools import partial; from loop import __main__ as cli; "
    "cli.Store = partial(cli.Store, reservation_root=sys.argv.pop(1)); "
    "raise SystemExit(cli.main())"
)


class LedgerMigrationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rtl-ledger-upgrade-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.data = self.root / "data"
        self.reservations = self.root / "reservations"
        self.project = self.root / "project"
        self.output = self.root / "published"
        seed = self.project / "prototype" / "index.html"
        seed.parent.mkdir(parents=True)
        seed.write_text("<!doctype html><h1>Legacy prototype</h1>", encoding="utf-8")
        self.suite = self._json_file("suite.json", {
            "fixture_revision": "old-fixtures",
            "suite_revision": "old-suite",
            "cases": [{"id": "TC-C", "steps": [{"op": "text", "selector": "h1", "value": "Legacy prototype"}]}],
        })
        self.suite_digest = hashlib.sha256(self.suite.read_bytes()).hexdigest()
        self.store = Store(self.data, reservation_root=self.reservations)
        self.store.create_run(
            self.project, self.output, {
                "revision": "legacy-R1",
                "mandatory_cases": ["TC-C"],
                "fixture_revision": "old-fixtures",
                "suite_revision": "old-suite",
                "suite_digests": {self.suite_digest: ["TC-C"]},
                "human_acceptance": True,
            },
            project_id="P", run_id="legacy",
        )
        self.candidate = artifacts.initialize_repo(self.store, "legacy", 1)

    def _json_file(self, name, data):
        target = self.root / name
        target.write_text(json.dumps(data), encoding="utf-8")
        return target

    @contextmanager
    def _raw(self, *, write=False):
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(f"{self.store.path.as_uri()}?mode={mode}", uri=True)) as db, db:
            yield db

    def _cli(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, "-c", CLI, str(self.reservations), "--data", str(self.data), *map(str, args)],
            cwd=ROOT, text=True, capture_output=True, timeout=40,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect:
            return result.stderr
        return json.loads(result.stdout) if result.stdout.strip() else None

    def _downgrade(self, *, without_rebinds=True):
        with self._raw(write=True) as db:
            db.execute("PRAGMA user_version = 0")
            if without_rebinds:
                db.execute("DROP TABLE project_rebinds")

    def _version(self):
        with self._raw() as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    def _table_exists(self, name):
        with self._raw() as db:
            return db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
            ).fetchone() is not None

    def _preserved_payloads(self):
        with self._raw() as db:
            return (
                db.execute("SELECT baseline FROM runs").fetchall(),
                db.execute("SELECT id,cases,context FROM evidence ORDER BY rowid").fetchall(),
                db.execute("SELECT id,payload,receipt FROM operations ORDER BY rowid").fetchall(),
            )

    def test_new_ledger_has_current_schema_and_supports_cli_reads(self):
        self.assertGreater(CURRENT_VERSION, 0)
        self.assertEqual(self._version(), CURRENT_VERSION)
        self.assertTrue(self._table_exists("project_rebinds"))
        self.assertEqual(self._cli("status", "legacy")["candidate"], self.candidate)
        with self._raw() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_unversioned_complete_ledger_is_read_only_until_first_registration(self):
        self._downgrade(without_rebinds=False)
        former = self.store.path.read_bytes()
        payloads = self._preserved_payloads()
        self.assertEqual([run["id"] for run in self._cli("runs")], ["legacy"])
        self.assertEqual(self._cli("status", "legacy", "--next")["candidate"], self.candidate)
        self.assertEqual(self._version(), 0)
        self.assertEqual(self.store.path.read_bytes(), former)
        self.assertEqual(self._cli("register-ledger")["active"], ["legacy"])
        self.assertEqual(self._version(), CURRENT_VERSION)
        self.assertEqual(self._preserved_payloads(), payloads)
        upgraded = self.store.path.read_bytes()
        self._cli("register-ledger")
        self.assertEqual(self.store.path.read_bytes(), upgraded)

    def test_legacy_pause_resumes_original_attempt_after_takeover(self):
        self.store.add_task("legacy", 1, {"id": "C", "paths": ["index.html"], "cases": ["TC-C"]})
        attempt = self._cli("claim", "legacy", "C", "--epoch", 1)
        self._cli("start", "legacy", attempt["id"], "--worker", "worker", "--host-ref", "host", "--epoch", 1)
        edited = Path(attempt["workspace"]) / "index.html"
        edited.write_text("<h1>Retained in-flight work</h1>", encoding="utf-8")
        pause = self._cli("pause-run", "legacy", "--reason", "test interruption", "--epoch", 1)
        self._cli("pause", "legacy", attempt["id"], "--stopped", "--host-ref", "host", "--epoch", 1)
        self._downgrade()
        before = self.store.path.read_bytes()
        next_actions = self._cli("status", "legacy", "--next")["next_actions"]
        self.assertTrue(any(item.get("pause_id") == pause["pause_id"] for item in next_actions))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertFalse(self._table_exists("project_rebinds"))
        epoch = self._cli("takeover", "legacy", "--epoch", 1)["epoch"]
        self.assertEqual(epoch, 2)
        self.assertEqual(self._version(), CURRENT_VERSION)
        self.assertTrue(self._table_exists("project_rebinds"))
        resumed = self._cli("resume-run", "legacy", pause["pause_id"], "--epoch", epoch)
        self.assertEqual(resumed["resumed_epoch"], epoch)
        recovered = self._cli("claim", "legacy", "C", "--epoch", epoch)
        self.assertEqual((recovered["id"], recovered["workspace"]), (attempt["id"], attempt["workspace"]))
        self.assertEqual(edited.read_text(encoding="utf-8"), "<h1>Retained in-flight work</h1>")
        started = self._cli(
            "start", "legacy", recovered["id"], "--worker", "worker", "--host-ref", "host", "--epoch", epoch,
        )
        self.assertEqual(started["state"], "running")

    def test_two_writers_upgrade_old_schema_and_keep_both_updates(self):
        other_project = self.root / "other-project"
        self.store.create_run(
            other_project, other_project / "prototype", {"revision": "other-R1"},
            project_id="Q", run_id="other",
        )
        self._downgrade()
        ready = Barrier(2, timeout=10)

        def take_over(run_id):
            writer = Store(self.data, reserve=False, reservation_root=self.reservations)
            ready.wait()
            return writer.take_over(run_id, 1)["epoch"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(take_over, ["legacy", "other"])), [2, 2])
        self.assertEqual(self._version(), CURRENT_VERSION)
        with self._raw() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='project_rebinds'",
            ).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT id,epoch FROM runs ORDER BY id").fetchall(),
                             [("legacy", 2), ("other", 2)])
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def _report(self, name, entry):
        entry = Path(entry)
        if os.environ.get("RTL_TEST_BROWSER") == "1":
            report = self.root / name
            self._cli("browser", entry, self.suite, report)
            return report
        return self._json_file(name, {
            "protocol": "file:",
            "path": str(entry),
            "entry": entry.as_uri(),
            "browser": "chromium 1",
            "fixture_revision": "old-fixtures",
            "suite_revision": "old-suite",
            "asset_digest": browser.asset_digest(entry.parent),
            "suite_digest": self.suite_digest,
            "cases": {"TC-C": {"status": "pass", "executed": ["text h1"], "errors": []}},
        })

    def _integrate_scene(self):
        feature_map = self.project / "docs" / "feature-map.md"
        feature_map.parent.mkdir()
        feature_map.write_text(
            "| 功能 | 场景 | 入口 | 契约 | 必跑用例 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| FUNC-C | C | index.html | - | TC-C |\n",
            encoding="utf-8",
        )
        self.store.add_task("legacy", 1, {"id": "C", "paths": ["layout.js"], "cases": ["TC-C"]})
        attempt = self._cli("claim", "legacy", "C", "--epoch", 1)
        workspace = Path(attempt["workspace"])
        self._cli("start", "legacy", attempt["id"], "--worker", "worker", "--epoch", 1)
        (workspace / "layout.js").write_text("// integrated scenario C\n", encoding="utf-8")
        artifacts.git(workspace, "add", "layout.js")
        artifacts.git(workspace, "commit", "--quiet", "-m", "Legacy scene candidate")
        submitted = self._cli("submit", "legacy", attempt["id"], "--actor", "worker", "--epoch", 1)
        self._cli(
            "evidence", "legacy", "self", submitted["result"]["commit"], "--epoch", 1, "--actor", "worker",
            "--context", self._json_file("self-context.json", {"attempt_id": attempt["id"]}),
            "--report", self._report("self-report.json", workspace / "index.html"),
        )
        staged = self._cli("stage", "legacy", attempt["id"], "--epoch", 1)
        self._cli("promote", "legacy", staged["operation_id"], "--epoch", 1)
        return staged["candidate"]

    def test_pending_legacy_acceptance_reuses_evidence_and_approval_after_upgrade(self):
        candidate = self._integrate_scene()
        landed = self._cli("land", "legacy", "--epoch", 1)
        operation = landed["operation_id"]
        proof = self._cli(
            "evidence", "legacy", "delivery", landed["candidate"], "--epoch", 1, "--actor", "reviewer",
            "--context", self._json_file("context.json", {"operation_id": operation}),
            "--report", self._report("delivery-report.json", landed["entry"]),
        )
        with patch.object(delivery, "_write_memory", side_effect=RuntimeError("simulated interruption")):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                delivery.finalize(self.store, "legacy", 1, operation, "original-master", "original-approval")
        pending = next(row for row in self.store.operations("legacy") if row["kind"] == "acceptance")
        self.assertEqual(pending["status"], "intent")
        self._downgrade()
        former = self.store.path.read_bytes()
        payloads = self._preserved_payloads()
        status = self._cli("status", "legacy")
        self.assertNotIn("suite_catalog", status["baseline"])
        self.assertEqual(next(row for row in status["operations"] if row["id"] == pending["id"])["status"], "intent")
        self.assertEqual(self.store.path.read_bytes(), former)
        self._cli("register-ledger")
        self.assertEqual(self._preserved_payloads(), payloads)
        epoch = self._cli("takeover", "legacy", "--epoch", 1)["epoch"]
        rejected = self._cli(
            "finalize", "legacy", operation, "--actor", "successor", "--approval", "different", "--epoch", epoch,
            expect=1,
        )
        self.assertIn("人工验收依据", rejected)
        receipt = self._cli("finalize", "legacy", operation, "--actor", "successor", "--epoch", epoch)
        self.assertEqual(receipt["status"], "delivered")
        self.assertEqual(receipt["operation_id"], operation)
        self.assertEqual(receipt["candidate"], candidate)
        self.assertEqual(receipt["actor"], "original-master")
        self.assertEqual(receipt["approval"], "original-approval")
        self.assertEqual(receipt["evidence_ids"], [proof["id"]])
        self.assertEqual(self._cli("status", "legacy")["status"], "delivered")
        self.assertEqual(self._cli("finalize", "legacy", operation, "--actor", "successor", "--epoch", epoch), receipt)
        self.assertEqual((self.output / "index.html").read_bytes(),
                         (self.project / "prototype" / "index.html").read_bytes())

    def test_unknown_future_version_is_rejected_without_writing(self):
        with self._raw(write=True) as db:
            db.execute(f"PRAGMA user_version = {CURRENT_VERSION + 1}")
        before = self.store.path.read_bytes()
        self._cli("status", "legacy", expect=1)
        self._cli("register-ledger", expect=1)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_malformed_core_schema_remains_untouched(self):
        self._downgrade()
        with self._raw(write=True) as db:
            db.execute("ALTER TABLE runs RENAME COLUMN epoch TO wrong_epoch")
        before = self.store.path.read_bytes()
        self._cli("register-ledger", expect=1)
        self.assertEqual(self._version(), 0)
        self.assertFalse(self._table_exists("project_rebinds"))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_failed_index_restoration_rolls_back_additive_schema(self):
        self._downgrade()
        with self._raw(write=True) as db:
            db.execute("DROP INDEX active_contract")
            db.executemany(
                "INSERT INTO contracts(run_id,edge,version,body,active) VALUES ('legacy','ab',?,'{}',1)",
                [("v1",), ("v2",)],
            )
        before = self.store.path.read_bytes()
        self._cli("register-ledger", expect=1)
        self.assertEqual(self._version(), 0)
        self.assertFalse(self._table_exists("project_rebinds"))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_same_named_nonunique_index_cannot_mark_damaged_ledger_as_upgraded(self):
        self._downgrade()
        with self._raw(write=True) as db:
            db.execute("DROP INDEX active_contract")
            db.execute("CREATE INDEX active_contract ON contracts(run_id, edge) WHERE active=1")
            db.executemany(
                "INSERT INTO contracts(run_id,edge,version,body,active) VALUES ('legacy','ab',?,'{}',1)",
                [("v1",), ("v2",)],
            )
        before = self.store.path.read_bytes()
        for command in (("register-ledger",), ("claim", "legacy", "A", "--epoch", 1)):
            self.assertIn("唯一活动索引 active_contract", self._cli(*command, expect=1))
        self.assertEqual(self._version(), 0)
        self.assertFalse(self._table_exists("project_rebinds"))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_current_schema_rejects_same_named_unique_index_with_the_wrong_predicate(self):
        with self._raw(write=True) as db:
            db.execute("DROP INDEX active_project")
            db.execute("CREATE UNIQUE INDEX active_project ON runs(project_id) WHERE status='delivered'")
        before = self.store.path.read_bytes()
        self.assertIn("唯一活动索引 active_project", self._cli("register-ledger", expect=1))
        self.assertEqual(self._version(), CURRENT_VERSION)
        self.assertEqual(self.store.path.read_bytes(), before)
