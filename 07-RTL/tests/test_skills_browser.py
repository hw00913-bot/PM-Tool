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
