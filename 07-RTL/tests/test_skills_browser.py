import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from loop.skills import read_skill


PROJECT = Path(__file__).resolve().parents[1]


class SkillTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.skill = self.root / "loop-control" / "SKILL.md"
        self.skill.parent.mkdir()
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({
            "skills": [{
                "id": "loop-control",
                "path": "loop-control/SKILL.md",
                "roles": ["master"],
                "stages": ["S0", "S1"],
            }],
        }), encoding="utf-8")
        self.write_skill("Initial dispatch rule.")

    def write_skill(self, rule):
        self.skill.write_text(
            f"---\nname: loop-control\ndescription: Route work.\n---\n{rule}\n",
            encoding="utf-8",
        )

    def test_reads_current_skill_at_each_invocation(self):
        first = read_skill("loop-control", "master", "S0", self.root)
        self.write_skill("Revised dispatch rule.")
        second = read_skill("loop-control", "master", "S0", self.root)
        self.assertIn("Initial dispatch rule.", first["content"])
        self.assertIn("Revised dispatch rule.", second["content"])
        self.assertNotEqual(first["digest"], second["digest"])

    def test_refuses_unauthorized_role_and_stage(self):
        for role, stage in [("scene", "S0"), ("master", "S3")]:
            with self.subTest(role=role, stage=stage):
                with self.assertRaisesRegex(ValueError, "does not permit"):
                    read_skill("loop-control", role, stage, self.root)

    def test_scene_and_verifier_have_separate_registered_skills(self):
        self.assertEqual(read_skill("scene-delivery", "scene", "S2")["role"], "scene")
        self.assertEqual(read_skill("prototype-verification", "verifier", "S3")["role"], "verifier")
        for skill_id, role in (("prototype-verification", "scene"), ("scene-delivery", "verifier")):
            with self.subTest(skill=skill_id, role=role):
                with self.assertRaisesRegex(ValueError, "does not permit"):
                    read_skill(skill_id, role, "S3")

    def test_registry_cannot_redirect_to_another_file(self):
        registry = json.loads(self.registry.read_text(encoding="utf-8"))
        registry["skills"][0]["path"] = "../another-project/SKILL.md"
        self.registry.write_text(json.dumps(registry), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "registered local file"):
            read_skill("loop-control", "master", "S0", self.root)


@unittest.skipUnless(shutil.which("node"), "Node is required for browser loader tests")
class BrowserLoaderTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory(prefix="rtl-browser-loader-")
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.runner = self.root / "scripts" / "browser-check.cjs"
        self.runner.parent.mkdir()
        shutil.copyfile(PROJECT / "scripts" / self.runner.name, self.runner)
        self.entry = self.root / "index.html"
        self.entry.write_text("<h1>RTL</h1>", encoding="utf-8")
        self.suite = self.root / "suite.json"
        self.suite.write_text(json.dumps({
            "fixture_revision": "fixture-v1",
            "suite_revision": "suite-v1",
            "cases": [{"id": "check-heading", "steps": [{"op": "visible", "selector": "h1"}]}],
        }), encoding="utf-8")
        self.report = self.root / "report.json"
        self.tool = self.root / "tools" / "browser-verification"
        self.tool.mkdir(parents=True)
        source = PROJECT / "tools" / "browser-verification"
        for filename in ("package.json", "package-lock.json"):
            shutil.copyfile(source / filename, self.tool / filename)
        self.lock = json.loads((self.tool / "package-lock.json").read_text(encoding="utf-8"))["packages"]
        self.modules = self.tool / "node_modules"

    def write_module(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def write_package(self, root, name, contents, *, version=None):
        package = root / name
        self.write_module(package / "index.js", contents)
        self.write_module(package / "package.json", json.dumps({
            "name": name,
            "version": version or self.lock[f"node_modules/{name}"]["version"],
            "main": "./index.js",
            "exports": {".": "./index.js", "./package.json": "./package.json"},
        }))

    def install_bundled(self, contents="throw new Error('bundled module selected');\n"):
        self.write_package(self.modules, "playwright", contents)
        self.write_package(self.modules, "playwright-core", "module.exports = {};\n")

    def invoke(self, **overrides):
        env = os.environ.copy()
        for name in ("RTL_PLAYWRIGHT_MODULE", "RTL_BROWSER_EXECUTABLE", "NODE_PATH"):
            env.pop(name, None)
        env.update(overrides)
        return subprocess.run(
            [shutil.which("node"), str(self.runner), str(self.entry), str(self.suite), str(self.report)],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=10,
        )

    def assert_reinstall_hint(self, execution):
        self.assertEqual(execution.returncode, 2)
        self.assertIn("npm ci --prefix tools/browser-verification --ignore-scripts", execution.stderr)
        self.assertFalse(self.report.exists())

    def test_default_loads_the_tool_local_module(self):
        self.install_bundled()
        execution = self.invoke()
        self.assertEqual(execution.returncode, 2)
        self.assertIn("bundled module selected", execution.stderr)

    def test_explicit_module_takes_priority_and_receives_the_browser_override(self):
        self.install_bundled()
        override = self.root / "override" / "playwright.cjs"
        self.write_module(override, "module.exports.chromium = { launch({ executablePath }) {\n"
                                   "  throw new Error('override selected ' + executablePath);\n"
                                   "} };\n")
        execution = self.invoke(RTL_PLAYWRIGHT_MODULE=str(override), RTL_BROWSER_EXECUTABLE="/test/browser")
        self.assertEqual(execution.returncode, 2)
        self.assertIn("override selected /test/browser", execution.stderr)

    def test_relative_module_override_is_resolved_from_callers_directory(self):
        self.write_module(self.root / "override" / "playwright.cjs",
                          "throw new Error('relative override selected');\n")
        execution = self.invoke(RTL_PLAYWRIGHT_MODULE="./override/playwright.cjs")
        self.assertEqual(execution.returncode, 2)
        self.assertIn("relative override selected", execution.stderr)

    def test_missing_install_ignores_ancestor_module_and_path_cli(self):
        self.write_module(self.root / "node_modules" / "playwright" / "index.js",
                          "throw new Error('ancestor module selected');\n")
        cli = self.root / "ambient" / "bin" / "playwright-cli"
        self.write_module(cli, "")
        self.write_module(self.root / "ambient" / "node_modules" / "playwright" / "index.js",
                          "throw new Error('CLI module selected');\n")
        execution = self.invoke(PATH=str(cli.parent))
        self.assert_reinstall_hint(execution)

    def test_stale_install_rejects_either_version_mismatch(self):
        for name in ("playwright", "playwright-core"):
            with self.subTest(name=name):
                self.install_bundled()
                self.write_package(self.modules, name, "throw new Error('stale package selected');\n",
                                   version="0.0.0")
                execution = self.invoke()
                self.assert_reinstall_hint(execution)
                expected = self.lock[f"node_modules/{name}"]["version"]
                self.assertIn(f"{name} version 0.0.0", execution.stderr)
                self.assertIn(f"lockfile version {expected}", execution.stderr)
                self.assertNotIn("stale package selected", execution.stderr)

    def test_missing_local_core_does_not_borrow_ancestor_core(self):
        self.write_package(self.modules, "playwright", "require('playwright-core');\n")
        self.write_package(self.root / "node_modules", "playwright-core",
                           "throw new Error('ancestor core selected');\n")
        execution = self.invoke()
        self.assert_reinstall_hint(execution)
        self.assertIn("playwright-core", execution.stderr)
        self.assertNotIn("ancestor core selected", execution.stderr)

    def test_shadowed_core_is_rejected_even_with_the_expected_version(self):
        self.install_bundled("require('playwright-core');\n")
        self.write_package(self.modules / "playwright" / "node_modules", "playwright-core",
                           "throw new Error('nested core selected');\n")
        execution = self.invoke()
        self.assert_reinstall_hint(execution)
        self.assertIn("playwright-core", execution.stderr)
        self.assertNotIn("nested core selected", execution.stderr)

    def test_incomplete_install_directory_prints_the_install_command(self):
        (self.modules / "playwright").mkdir(parents=True)
        self.assert_reinstall_hint(self.invoke())

    def test_broken_install_keeps_the_module_error(self):
        self.install_bundled("require('./missing-core');\n")
        execution = self.invoke()
        self.assert_reinstall_hint(execution)
        self.assertIn("Cannot find module './missing-core'", execution.stderr)

    def test_missing_managed_browser_prints_the_locked_cli_install_command(self):
        self.install_bundled(
            "const path = require('node:path');\n"
            "const missing = path.join(__dirname, 'missing-chromium');\n"
            "module.exports.chromium = { executablePath() { return missing; }, launch() {\n"
            "  throw new Error(\"browserType.launch: Executable doesn't exist at \" + missing);\n"
            "} };\n"
        )
        execution = self.invoke()
        self.assertEqual(execution.returncode, 2)
        self.assertIn("tools/browser-verification/node_modules/.bin/playwright install chromium", execution.stderr)
        self.assertFalse(self.report.exists())


@unittest.skipUnless(os.environ.get("RTL_TEST_BROWSER") == "1", "opt in with RTL_TEST_BROWSER=1")
class BrowserTests(unittest.TestCase):
    def test_file_entry_loads_relative_script_and_preserves_saved_value_after_reload(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required when RTL_TEST_BROWSER=1")
        with tempfile.TemporaryDirectory(prefix="rtl-browser-test-") as directory:
            root = Path(directory)
            entry = root / "index.html"
            entry.write_text(
                '<!doctype html><meta charset="utf-8"><title>File prototype</title>'
                '<label>Preference <input id="preference"></label>'
                '<button id="save">Save</button><output id="result"></output>'
                '<script src="preferences.js"></script>',
                encoding="utf-8",
            )
            (root / "preferences.js").write_text(
                "const preference = document.querySelector('#preference');\n"
                "const result = document.querySelector('#result');\n"
                "result.textContent = localStorage.getItem('preference') || 'Empty';\n"
                "document.querySelector('#save').addEventListener('click', () => {\n"
                "  localStorage.setItem('preference', preference.value);\n"
                "  result.textContent = preference.value;\n"
                "});\n",
                encoding="utf-8",
            )
            suite = root / "suite.json"
            suite.write_text(json.dumps({
                "fixture_revision": "layout-fixture-v1",
                "suite_revision": "layout-suite-v1",
                "cases": [{
                    "id": "C-save-reload",
                    "steps": [
                        {"op": "text", "selector": "#result", "value": "Empty"},
                        {"op": "fill", "selector": "#preference", "value": "Compact"},
                        {"op": "click", "selector": "#save"},
                        {"op": "text", "selector": "#result", "value": "Compact"},
                        {"op": "reload"},
                        {"op": "text", "selector": "#result", "value": "Compact"},
                    ],
                }],
            }), encoding="utf-8")
            report = root / "report.json"
            execution = subprocess.run(
                [node, str(PROJECT / "scripts" / "browser-check.cjs"), str(entry), str(suite), str(report)],
                text=True, capture_output=True, timeout=35,
            )
            detail = report.read_text(encoding="utf-8") if report.is_file() else execution.stderr
            self.assertEqual(execution.returncode, 0, detail)
            evidence = json.loads(detail)
            self.assertEqual(evidence["protocol"], "file:")
            self.assertEqual(evidence["path"], str(entry.resolve()))
            self.assertEqual(evidence["fixture_revision"], "layout-fixture-v1")
            self.assertEqual(evidence["suite_revision"], "layout-suite-v1")
            result = evidence["cases"]["C-save-reload"]
            self.assertEqual(result["status"], "pass")
            self.assertIn("reload", result["executed"])
            self.assertEqual(result["errors"], [])
