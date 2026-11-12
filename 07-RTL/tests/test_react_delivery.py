import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, delivery, frontend, verification
from loop.browser import asset_digest
from loop.state import Store


class ReactDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "原型项目"
        self.source = self.project / "prototype-src"
        self.source.mkdir(parents=True)
        (self.source / "package.json").write_text('{"name":"prototype"}', encoding="utf-8")
        self.lockfile = '{"lockfileVersion":3}'
        (self.source / "package-lock.json").write_text(self.lockfile, encoding="utf-8")
        self.output = self.project / "prototype"
        self.store = Store(self.root / "runtime", reservation_root=self.root / "reservations")
        self.baseline = {"revision": "R1", "mandatory_cases": ["final"], "frontend": "react"}

    def _candidate(self, label):
        (self.source / "scene.jsx").write_text(f"export default '{label}'\n", encoding="utf-8")
        run = self.store.create_run(self.project, self.output, self.baseline)["id"]
        commit = artifacts.initialize_repo(self.store, run, 1, source=self.source)
        directory = artifacts.run_directory(self.store, run) / "builds" / commit
        dist = directory / "dist"
        dist.mkdir(parents=True)
        entry = dist / "index.html"
        entry.write_text(f"<html>{label}</html>", encoding="utf-8")
        receipt = {
            "source_commit": commit,
            "manifest": delivery.manifest(dist),
            "lockfile_sha256": hashlib.sha256(self.lockfile.encode()).hexdigest(),
        }
        (directory / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        return run, {**receipt, "path": str(dist), "entry": str(entry)}

    def _mark_old_cache_hit(self, build):
        receipt_path = Path(build["path"]).parent / "receipt.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["commands"] = [list(frontend.BUILD)]
        receipt["dependency_install"] = {"mode": "cache-hit", "key": "0" * 64}
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        return receipt_path

    def _rebuild(self, run, build, label, epoch=1):
        def npm(command, directory, env, timeout):
            if command == frontend.BUILD:
                dist = directory / "dist"
                dist.mkdir()
                (dist / "index.html").write_text(f"<html>{label}</html>", encoding="utf-8")

        with patch.object(frontend, "_run_npm", side_effect=npm):
            return frontend.build(self.store, run, build["source_commit"], epoch=epoch)

    def _accept(self, run, landed, epoch=1):
        verification.record(
            self.store, run, epoch, "delivery", landed["candidate"], "reviewer",
            {"final": {"passed": True, "actual": "Observed the exact staged local page"}},
            {
                "fixture_revision": "fixture1", "suite_revision": "suite1",
                "operation_id": landed["operation_id"], "path": landed["entry"],
                "url": landed["url"], "browser": "TestBrowser", "asset_digest": asset_digest(self.output),
            },
        )
        return delivery.finalize(self.store, run, epoch, landed["operation_id"], "master")

    def test_lands_build_and_archives_exact_source_on_acceptance(self):
        run, build = self._candidate("one")
        with patch("loop.frontend.get_build", return_value=build):
            landed = delivery.land(self.store, run, 1)
            self.assertEqual(landed, delivery.land(self.store, run, 1))
        self.assertEqual(delivery.manifest(self.output), build["manifest"])
        self.assertFalse((self.output / "scene.jsx").exists())
        self.assertEqual(landed["build"]["source_commit"], build["source_commit"])
        with patch.object(delivery, "_write_memory", side_effect=RuntimeError("interrupted before memory")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                self._accept(run, landed)
        self.assertEqual(self.store.run(run)["status"], "active")
        epoch = self.store.take_over(run, 1)["epoch"]
        receipt = delivery.finalize(self.store, run, epoch, landed["operation_id"], "successor")
        handoff = Path(receipt["source"])
        self.assertEqual(receipt["build"], landed["build"])
        self.assertEqual((handoff / "scene.jsx").read_text(), "export default 'one'\n")
        self.assertEqual((handoff / "package-lock.json").read_text(), self.lockfile)
        self.assertFalse((handoff / "index.html").exists())
        self.assertEqual(receipt, delivery.finalize(self.store, run, epoch, landed["operation_id"], "successor"))
        (handoff / "scene.jsx").write_text("altered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "源码交接内容"):
            delivery.finalize(self.store, run, epoch, landed["operation_id"], "successor")

    def test_replacement_requires_permission_and_previous_known_output(self):
        run, build = self._candidate("one")
        with patch("loop.frontend.get_build", return_value=build):
            self._accept(run, delivery.land(self.store, run, 1))
        successor, next_build = self._candidate("two")
        with patch("loop.frontend.get_build", return_value=next_build):
            with self.assertRaisesRegex(ValueError, "未确认"):
                delivery.land(self.store, successor, 1)
            protected = self.output / "personal-note.txt"
            protected.write_text("retain", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未确认"):
                delivery.land(self.store, successor, 1, allow_replace=True)
            self.assertEqual(protected.read_text(), "retain")
            protected.unlink()
            landed = delivery.land(self.store, successor, 1, allow_replace=True)
        self.assertEqual(delivery.manifest(self.output), next_build["manifest"])
        operation = self.store.operation(successor, landed["operation_id"])
        self.assertEqual(delivery.manifest(Path(operation["payload"]["previous"])), build["manifest"])

    def test_changed_build_cannot_satisfy_a_pending_delivery(self):
        run, build = self._candidate("one")
        with patch("loop.frontend.get_build", return_value=build):
            delivery._prepare(self.store, run, 1, allow_replace=False)
        changed = dict(build)
        Path(changed["entry"]).write_text("<html>unverified</html>", encoding="utf-8")
        changed["manifest"] = delivery.manifest(changed["path"])
        with patch("loop.frontend.get_build", return_value=changed):
            with self.assertRaisesRegex(ValueError, "构建记录"):
                delivery.land(self.store, run, 1)
        self.assertFalse(self.output.exists())

    def test_old_cache_hit_blocks_landing_and_finalization_after_different_rebuild(self):
        run, build = self._candidate("old cache result")
        receipt_path = self._mark_old_cache_hit(build)
        original_receipt = receipt_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "缺少依赖缓存完整性证明"):
            delivery.land(self.store, run, 1)
        self.assertFalse(self.output.exists())

        # Reproduce a landing that was already pending when the tool was upgraded.
        with patch.object(frontend, "get_build", return_value=build):
            landed = delivery.land(self.store, run, 1)
        with self.assertRaisesRegex(ValueError, "缺少依赖缓存完整性证明"):
            self._accept(run, landed)
        trusted = self._rebuild(run, build, "cleanly rebuilt")
        self.assertNotEqual(trusted["path"], build["path"])
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        self.assertEqual(Path(build["entry"]).read_text(), "<html>old cache result</html>")
        with self.assertRaisesRegex(ValueError, "构建记录与已登记的交付意图不符"):
            delivery.finalize(self.store, run, 1, landed["operation_id"], "master")
        self.assertEqual((self.output / "index.html").read_text(), "<html>old cache result</html>")
        self.assertEqual(self.store.run(run)["status"], "active")

    def test_failed_legacy_rebuild_preserves_history_and_same_bytes_allow_recovery(self):
        run, build = self._candidate("unchanged")
        receipt_path = self._mark_old_cache_hit(build)
        original_receipt = receipt_path.read_bytes()
        with patch.object(frontend, "get_build", return_value=build):
            landed = delivery.land(self.store, run, 1)
        with patch.object(frontend, "_run_npm", side_effect=ValueError("npm ci failed")):
            with self.assertRaisesRegex(ValueError, "npm ci failed"):
                frontend.build(self.store, run, build["source_commit"], epoch=1)
        self.assertEqual(receipt_path.read_bytes(), original_receipt)
        with self.assertRaisesRegex(ValueError, "缺少依赖缓存完整性证明"):
            frontend.get_build(self.store, run, build["source_commit"])
        epoch = self.store.take_over(run, 1)["epoch"]
        with self.assertRaisesRegex(ValueError, "stale controller epoch"):
            self._rebuild(run, build, "unchanged", epoch=1)
        with self.assertRaisesRegex(ValueError, "缺少依赖缓存完整性证明"):
            frontend.get_build(self.store, run, build["source_commit"])
        self._rebuild(run, build, "unchanged", epoch=epoch)
        self.assertEqual(landed, delivery.land(self.store, run, epoch))
        receipt = self._accept(run, landed, epoch=epoch)
        self.assertEqual(receipt["status"], "delivered")
        self.assertEqual(receipt_path.read_bytes(), original_receipt)

    def test_completed_legacy_delivery_can_still_be_replayed(self):
        run, build = self._candidate("history")
        with patch.object(frontend, "get_build", return_value=build):
            landed = delivery.land(self.store, run, 1)
            receipt = self._accept(run, landed)
        self._mark_old_cache_hit(build)
        self.assertEqual(frontend.get_build(self.store, run, build["source_commit"])["path"], build["path"])
        self.assertEqual(receipt, delivery.finalize(self.store, run, 1, landed["operation_id"], "reader"))
