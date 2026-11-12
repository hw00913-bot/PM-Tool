from concurrent.futures import ThreadPoolExecutor
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from loop import artifacts, browser, frontend, lifecycle, npm_dependencies
from loop.state import Store


class FrontendTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        root = Path(work.name).resolve()
        source = root / "source"
        source.mkdir()
        self.package = {
            "name": "rtl-test", "version": "1.0.0", "scripts": {"build": "umi build"},
            "dependencies": {"fixture": "^1.0.0"},
        }
        (source / "package.json").write_text(json.dumps(self.package), encoding="utf-8")
        (source / "package-lock.json").write_text(self._lockfile(), encoding="utf-8")
        (source / "view.js").write_text("original", encoding="utf-8")
        project = root / "project"
        self.store = Store(root / "runtime", reservation_root=root / "reservations")
        self.run_id = self.store.create_run(
            project, project / "prototype",
            {"frontend": "react", "revision": "R1", "mandatory_cases": ["final"]},
        )["id"]
        self.commit = artifacts.initialize_repo(self.store, self.run_id, 1, source=source)
        self.repo = artifacts.repo_path(self.store, self.run_id)
        self.runtime = {
            "node": ["/tools/node", "v24.14.0", "darwin", "arm64", "137", "10"],
            "npm": "11.9.0", "config_sha256": "standard-config", "omit": [],
        }
        cache_mocks = (
            patch.object(npm_dependencies, "COPY_ON_WRITE", True),
            patch.object(npm_dependencies, "_runtime_identity", return_value=self.runtime),
            patch.object(
                npm_dependencies, "_copy_modules",
                side_effect=lambda source, destination: shutil.copytree(source, destination, symlinks=True),
            ),
        )
        for cache_mock in cache_mocks:
            cache_mock.start()
            self.addCleanup(cache_mock.stop)

    def _lockfile(self, version="1.0.0"):
        return json.dumps({
            "name": self.package["name"], "version": self.package["version"], "lockfileVersion": 3,
            "packages": {
                "": {key: self.package[key] for key in ("name", "version", "dependencies")},
                "node_modules/fixture": {
                    "version": version,
                    "resolved": f"https://registry.npmjs.org/fixture/-/fixture-{version}.tgz",
                    "integrity": "sha512-fakefixture==",
                },
            },
        })

    def _commit(self, filename, contents):
        (self.repo / filename).write_text(contents, encoding="utf-8")
        artifacts.git(self.repo, "add", "--", filename)
        artifacts.git(self.repo, "commit", "--quiet", "-m", f"change {filename}")
        return artifacts.rev(self.repo)

    def _optional_lockfile(self, **selectors):
        lock = json.loads(self._lockfile())
        lock["packages"]["node_modules/optional-fixture"] = {
            "version": "1.0.0", "optional": True, **selectors,
            "resolved": "https://registry.npmjs.org/optional-fixture/-/optional-fixture-1.0.0.tgz",
            "integrity": "sha512-optionalfixture==",
        }
        return json.dumps(lock)

    def _npm(self, command, directory, env, timeout):
        self.assertNotEqual(directory, self.repo)
        cache = os.environ.get("npm_config_cache", os.environ.get("NPM_CONFIG_CACHE", str(self.store.root / "npm-cache")))
        self.assertEqual(env["npm_config_cache"], cache)
        if command == frontend.INSTALL:
            self.assertEqual(timeout, 900)
            modules = directory / "node_modules"
            package = modules / "fixture"
            package.mkdir(parents=True)
            lock = json.loads((directory / "package-lock.json").read_text())
            version = lock["packages"]["node_modules/fixture"]["version"]
            (package / "package.json").write_text(json.dumps({
                "name": "fixture", "version": version, "bin": "cli.js",
            }), encoding="utf-8")
            (package / "cli.js").write_text("installed dependency", encoding="utf-8")
            (package / "cli.js").chmod(0o755)
            (modules / ".bin").mkdir()
            (modules / ".bin" / "fixture").symlink_to("../fixture/cli.js")
        else:
            self.assertEqual(command, frontend.BUILD)
            self.assertEqual(timeout, 300)
            dist = directory / "dist"
            dist.mkdir()
            (dist / "index.html").write_text('<script src="./umi.js"></script>', encoding="utf-8")
            binary = directory / "node_modules" / ".bin" / "fixture"
            self.assertTrue(binary.is_symlink())
            self.assertTrue(os.access(binary, os.X_OK))
            self.assertEqual(binary.read_text(), "installed dependency")
            view = (directory / "view.js").read_text()
            (dist / "umi.js").write_text(f"console.log({json.dumps(view)})", encoding="utf-8")

    def test_build_isolates_the_exact_commit_and_reuses_verified_output(self):
        (self.repo / "view.js").write_text("uncommitted edit", encoding="utf-8")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            built = frontend.build(self.store, self.run_id, self.commit)
            self.assertEqual(built, frontend.get_build(self.store, self.run_id, self.commit))
            self.assertEqual(built, frontend.build(self.store, self.run_id, self.commit))
            self.assertEqual(runner.call_count, 2)
        self.assertEqual(built["source_commit"], self.commit)
        self.assertEqual(Path(built["entry"]).name, "index.html")
        self.assertEqual(built["asset_digest"], browser.asset_digest(built["path"]))
        self.assertEqual(set(built["manifest"]), {"index.html", "umi.js"})
        self.assertEqual((Path(built["path"]) / "umi.js").read_text(), 'console.log("original")')
        self.assertFalse((self.repo / "node_modules").exists())
        self.assertFalse((self.repo / "dist").exists())
        self.assertEqual((self.repo / "view.js").read_text(), "uncommitted edit")

    def test_reuses_dependencies_and_rebuilds_changed_source(self):
        successor = self._commit("view.js", "changed view")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            first = frontend.build(self.store, self.run_id, self.commit)
            second = frontend.build(self.store, self.run_id, successor)
        self.assertEqual(first["dependency_install"]["mode"], "installed")
        self.assertEqual(second["dependency_install"]["mode"], "cache-hit")
        self.assertEqual(first["dependency_install"]["tree_sha256"], second["dependency_install"]["tree_sha256"])
        self.assertEqual(first["lockfile_sha256"], second["lockfile_sha256"])
        self.assertNotEqual(first["asset_digest"], second["asset_digest"])
        self.assertEqual(second["commands"], [list(frontend.BUILD)])
        self.assertEqual(
            [call.args[0] for call in runner.call_args_list],
            [frontend.INSTALL, frontend.BUILD, frontend.BUILD],
        )

    def test_tampered_dependency_bytes_are_reinstalled_before_build(self):
        successor = self._commit("view.js", "changed view")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            first = frontend.build(self.store, self.run_id, self.commit)["dependency_install"]
            snapshot = self.store.root / "npm-dependencies" / first["key"] / "node_modules"
            dependency = snapshot / "fixture" / "cli.js"
            dependency.write_text("tampered dependency!", encoding="utf-8")
            self.assertEqual(dependency.stat().st_size, len("installed dependency"))
            repaired = frontend.build(self.store, self.run_id, successor)["dependency_install"]
        self.assertEqual(first, repaired)
        self.assertEqual(dependency.read_text(), "installed dependency")
        self.assertEqual(
            [call.args[0] for call in runner.call_args_list],
            [frontend.INSTALL, frontend.BUILD, frontend.INSTALL, frontend.BUILD],
        )

    def test_changed_lockfile_reinstalls_dependencies(self):
        successor = self._commit("package-lock.json", self._lockfile("1.1.0"))
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            first = frontend.build(self.store, self.run_id, self.commit)
            second = frontend.build(self.store, self.run_id, successor)
        self.assertEqual(first["dependency_install"]["mode"], "installed")
        self.assertEqual(second["dependency_install"]["mode"], "installed")
        self.assertNotEqual(first["lockfile_sha256"], second["lockfile_sha256"])
        self.assertEqual([call.args[0] for call in runner.call_args_list].count(frontend.INSTALL), 2)

    def test_runtime_change_reinstalls_for_the_same_lockfile(self):
        successor = self._commit("view.js", "next runtime")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            first = frontend.build(self.store, self.run_id, self.commit)
            with patch.object(npm_dependencies, "_runtime_identity", return_value={**self.runtime, "npm": "12.0.0"}):
                second = frontend.build(self.store, self.run_id, successor)
        self.assertEqual(first["lockfile_sha256"], second["lockfile_sha256"])
        self.assertEqual(second["dependency_install"]["mode"], "installed")
        self.assertNotEqual(first["dependency_install"]["key"], second["dependency_install"]["key"])
        self.assertEqual([call.args[0] for call in runner.call_args_list].count(frontend.INSTALL), 2)

    def test_parallel_cold_builds_install_once_and_isolate_mutable_dependencies(self):
        successor = self._commit("view.js", "parallel view")
        builds_started = Barrier(2, timeout=20)
        mutation_finished = Barrier(2, timeout=20)

        def npm_with_mutation(command, directory, env, timeout):
            self._npm(command, directory, env, timeout)
            if command == frontend.BUILD:
                binary = directory / "node_modules" / ".bin" / "fixture"
                builds_started.wait()
                mutates = (directory / "view.js").read_text() == "parallel view"
                if mutates:
                    binary.write_text("build-local mutation", encoding="utf-8")
                mutation_finished.wait()
                self.assertEqual(binary.read_text(), "build-local mutation" if mutates else "installed dependency")

        with patch.object(frontend, "_run_npm", side_effect=npm_with_mutation) as runner:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(frontend.build, self.store, self.run_id, commit)
                    for commit in (self.commit, successor)
                ]
                builds = [future.result(timeout=30) for future in futures]
        self.assertCountEqual([build["dependency_install"]["mode"] for build in builds], ["installed", "cache-hit"])
        self.assertEqual([call.args[0] for call in runner.call_args_list].count(frontend.INSTALL), 1)

        third = self._commit("view.js", "later view")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            self.assertEqual(frontend.build(self.store, self.run_id, third)["dependency_install"]["mode"], "cache-hit")
        self.assertEqual([call.args[0] for call in runner.call_args_list], [frontend.BUILD])

    def test_failed_install_does_not_seed_a_partial_cache(self):
        def npm_with_failed_install(command, directory, env, timeout):
            self.assertEqual(command, frontend.INSTALL)
            (directory / "node_modules").mkdir()
            raise ValueError("interrupted npm ci")

        with patch.object(frontend, "_run_npm", side_effect=npm_with_failed_install):
            with self.assertRaisesRegex(ValueError, "interrupted npm ci"):
                frontend.build(self.store, self.run_id, self.commit)
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            recovered = frontend.build(self.store, self.run_id, self.commit)
        self.assertEqual(recovered["dependency_install"]["mode"], "installed")
        self.assertEqual([call.args[0] for call in runner.call_args_list], [frontend.INSTALL, frontend.BUILD])

    def test_missing_applicable_optional_dependency_does_not_seed_a_snapshot(self):
        first = self._commit("package-lock.json", self._optional_lockfile(os=["darwin"], cpu=["arm64"]))
        second = self._commit("view.js", "optional download recovered")
        third = self._commit("view.js", "complete optional snapshot")

        def npm_with_optional(command, directory, env, timeout):
            self._npm(command, directory, env, timeout)
            if command == frontend.INSTALL:
                package = directory / "node_modules" / "optional-fixture"
                package.mkdir()
                (package / "package.json").write_text('{"name":"optional-fixture","version":"1.0.0"}', encoding="utf-8")

        with patch.object(frontend, "_run_npm", side_effect=self._npm):
            self.assertEqual(frontend.build(self.store, self.run_id, first)["dependency_install"]["mode"], "uncached")
        with patch.object(frontend, "_run_npm", side_effect=npm_with_optional) as runner:
            self.assertEqual(frontend.build(self.store, self.run_id, second)["dependency_install"]["mode"], "installed")
            self.assertEqual(frontend.build(self.store, self.run_id, third)["dependency_install"]["mode"], "cache-hit")
        self.assertEqual(
            [call.args[0] for call in runner.call_args_list], [frontend.INSTALL, frontend.BUILD, frontend.BUILD],
        )

    def test_excluded_platform_optional_dependency_keeps_cache_eligible(self):
        first = self._commit("package-lock.json", self._optional_lockfile(os=["!darwin"]))
        second = self._commit("view.js", "same host platform")
        with patch.object(frontend, "_run_npm", side_effect=self._npm):
            self.assertEqual(frontend.build(self.store, self.run_id, first)["dependency_install"]["mode"], "installed")
            self.assertEqual(frontend.build(self.store, self.run_id, second)["dependency_install"]["mode"], "cache-hit")

    def test_explicitly_omitted_optional_dependency_keeps_cache_eligible(self):
        first = self._commit("package-lock.json", self._optional_lockfile())
        second = self._commit("view.js", "same omit policy")
        with (
            patch.object(npm_dependencies, "_runtime_identity", return_value={**self.runtime, "omit": ["optional"]}),
            patch.object(frontend, "_run_npm", side_effect=self._npm),
        ):
            self.assertEqual(frontend.build(self.store, self.run_id, first)["dependency_install"]["mode"], "installed")
            self.assertEqual(frontend.build(self.store, self.run_id, second)["dependency_install"]["mode"], "cache-hit")

    def test_project_install_hook_runs_for_each_source_commit(self):
        self.package["scripts"]["postinstall"] = "node view.js"
        first = self._commit("package.json", json.dumps(self.package))
        second = self._commit("view.js", "changed install hook input")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            builds = [frontend.build(self.store, self.run_id, commit) for commit in (first, second)]
        self.assertEqual([build["dependency_install"]["mode"] for build in builds], ["uncached", "uncached"])
        self.assertEqual([call.args[0] for call in runner.call_args_list].count(frontend.INSTALL), 2)

    def test_unreviewed_dependency_install_hook_bypasses_the_cache(self):
        lock = json.loads(self._lockfile())
        lock["packages"]["node_modules/fixture"]["hasInstallScript"] = True
        first = self._commit("package-lock.json", json.dumps(lock))
        second = self._commit("view.js", "unreviewed hook input")
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            builds = [frontend.build(self.store, self.run_id, commit) for commit in (first, second)]
        self.assertEqual([build["dependency_install"]["mode"] for build in builds], ["uncached", "uncached"])
        self.assertEqual([call.args[0] for call in runner.call_args_list].count(frontend.INSTALL), 2)

    def test_installed_dependency_hook_missing_from_lock_prevents_cache_publication(self):
        successor = self._commit("view.js", "unmarked hook input")

        def npm_with_unmarked_hook(command, directory, env, timeout):
            self._npm(command, directory, env, timeout)
            if command == frontend.INSTALL:
                path = directory / "node_modules" / "fixture" / "package.json"
                package = json.loads(path.read_text())
                package["scripts"] = {"postinstall": "node cli.js"}
                path.write_text(json.dumps(package), encoding="utf-8")

        with patch.object(frontend, "_run_npm", side_effect=npm_with_unmarked_hook) as runner:
            builds = [frontend.build(self.store, self.run_id, commit) for commit in (self.commit, successor)]
        self.assertEqual([build["dependency_install"]["mode"] for build in builds], ["uncached", "uncached"])
        self.assertEqual([call.args[0] for call in runner.call_args_list].count(frontend.INSTALL), 2)

    def test_symlinked_cache_root_uses_an_isolated_install(self):
        target = self.store.root / "other"
        target.mkdir()
        (self.store.root / "npm-dependencies").symlink_to(target, target_is_directory=True)
        with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
            built = frontend.build(self.store, self.run_id, self.commit)
        self.assertEqual(built["dependency_install"]["mode"], "uncached")
        self.assertEqual([call.args[0] for call in runner.call_args_list], [frontend.INSTALL, frontend.BUILD])
        self.assertEqual(list(target.iterdir()), [])

    def test_failed_clone_clears_the_partial_copy_and_runs_npm_ci(self):
        successor = self._commit("view.js", "failed clone")
        with patch.object(frontend, "_run_npm", side_effect=self._npm):
            first = frontend.build(self.store, self.run_id, self.commit)
        self.assertEqual(first["dependency_install"]["mode"], "installed")

        def fail_after_partial_copy(source, destination):
            (destination / "fixture").mkdir(parents=True)
            raise OSError("copy-on-write unavailable")

        with patch.object(npm_dependencies, "_copy_modules", side_effect=fail_after_partial_copy):
            with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
                second = frontend.build(self.store, self.run_id, successor)
        self.assertEqual(second["dependency_install"]["mode"], "uncached")
        self.assertEqual([call.args[0] for call in runner.call_args_list], [frontend.INSTALL, frontend.BUILD])

        third = self._commit("view.js", "use snapshot again")
        with patch.object(frontend, "_run_npm", side_effect=self._npm):
            self.assertEqual(frontend.build(self.store, self.run_id, third)["dependency_install"]["mode"], "cache-hit")

    def test_platform_without_copy_on_write_runs_npm_ci(self):
        with patch.object(npm_dependencies, "COPY_ON_WRITE", False):
            with patch.object(frontend, "_run_npm", side_effect=self._npm) as runner:
                built = frontend.build(self.store, self.run_id, self.commit)
        self.assertEqual(built["dependency_install"]["mode"], "uncached")
        self.assertEqual([call.args[0] for call in runner.call_args_list], [frontend.INSTALL, frontend.BUILD])
        self.assertFalse((self.store.root / "npm-dependencies").exists())

    def test_lookup_detects_changed_output_and_unbuilt_commit(self):
        with patch.object(frontend, "_run_npm", side_effect=self._npm):
            built = frontend.build(self.store, self.run_id, self.commit)
        (Path(built["path"]) / "umi.js").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "清单"):
            frontend.get_build(self.store, self.run_id, self.commit)
        (self.repo / "view.js").write_text("another candidate", encoding="utf-8")
        artifacts.git(self.repo, "add", "view.js")
        artifacts.git(self.repo, "commit", "--quiet", "-m", "another candidate")
        with self.assertRaisesRegex(ValueError, "缺少已登记"):
            frontend.get_build(self.store, self.run_id, artifacts.rev(self.repo))

    def test_build_rejects_a_module_entry_and_does_not_publish_it(self):
        def npm_with_module_entry(command, directory, env, timeout):
            self._npm(command, directory, env, timeout)
            if command == frontend.BUILD:
                (directory / "dist" / "index.html").write_text(
                    '<script type="module" src="./umi.js"></script>', encoding="utf-8",
                )

        with patch.object(frontend, "_run_npm", side_effect=npm_with_module_entry):
            with self.assertRaisesRegex(ValueError, "ES 模块"):
                frontend.build(self.store, self.run_id, self.commit)
        with self.assertRaisesRegex(ValueError, "缺少已登记"):
            frontend.get_build(self.store, self.run_id, self.commit)

    def test_paused_run_does_not_publish_an_in_flight_build(self):
        def npm_with_pause(command, directory, env, timeout):
            self._npm(command, directory, env, timeout)
            if command == frontend.BUILD:
                lifecycle.pause_run(self.store, self.run_id, 1, reason="stop before publication")

        with patch.object(frontend, "_run_npm", side_effect=npm_with_pause):
            with self.assertRaisesRegex(ValueError, "resume-run"):
                frontend.build(self.store, self.run_id, self.commit, epoch=1)
        with self.assertRaisesRegex(ValueError, "缺少已登记"):
            frontend.get_build(self.store, self.run_id, self.commit)

    def test_source_export_refuses_git_archive_omissions(self):
        (self.repo / ".gitattributes").write_text("view.js export-ignore\n", encoding="utf-8")
        artifacts.git(self.repo, "add", ".gitattributes")
        artifacts.git(self.repo, "commit", "--quiet", "-m", "omit tracked file from archive")
        with tempfile.TemporaryDirectory() as destination:
            with self.assertRaisesRegex(ValueError, "导出的源码与 Git 提交不一致"):
                frontend.export_source(self.repo, artifacts.rev(self.repo), destination)
