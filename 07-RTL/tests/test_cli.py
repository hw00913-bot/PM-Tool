import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


from s1_workflow_fixture import record_s1


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RTL_TEST_BROWSER") == "1", "opt in with RTL_TEST_BROWSER=1")
class LocalLoopSmoke(unittest.TestCase):
    def test_c_from_dispatch_to_verified_file_delivery(self):
        with tempfile.TemporaryDirectory(prefix="rtl-cli-") as folder:
            root = Path(folder).resolve()
            data = root / "state"
            project = root / "prototype-project"
            docs = project / "docs"
            docs.mkdir(parents=True)
            map_file = docs / "feature-map.md"
            map_text = ("| 功能 | 场景 | 入口 | 契约 | 必跑用例 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| FUNC-C01 | SCN-C | index.html | - | TC-C01 |\n")
            map_file.write_text(map_text, encoding="utf-8")
            baseline = root / "baseline.json"
            plan = json.loads(
                (ROOT / "skills/requirement-architecture/references/requirements-example.json").read_text()
            )
            plan["tasks"][0]["paths"].append("wrong/index.html")
            suite = root / "suite.json"
            suite.write_text(json.dumps({
                "fixture_revision": "c1", "suite_revision": "s1",
                "cases": [{"id": "TC-C01", "steps": [
                    {"op": "fill", "selector": "#layout", "value": "compact"},
                    {"op": "click", "selector": "#save"},
                    {"op": "reload"},
                    {"op": "text", "selector": "#result", "value": "compact"},
                ]}],
            }))

            baseline.write_text(json.dumps({
                "revision": "pilot-c", "mandatory_cases": ["TC-C01"],
                "fixture_revision": "c1", "suite_revision": "s1",
                "suite_digests": {hashlib.sha256(suite.read_bytes()).hexdigest(): ["TC-C01"]},
                "requirements": plan,
            }))

            runner = (
                "import sys; from functools import partial; from loop import __main__ as cli; "
                "cli.Store = partial(cli.Store, reservation_root=sys.argv.pop(1)); "
                "raise SystemExit(cli.main())"
            )

            def cli(*args, expect=0):
                completed = subprocess.run(
                    [sys.executable, "-c", runner, str(root / "reservations"), "--data", str(data), *map(str, args)],
                    cwd=ROOT, capture_output=True, text=True, timeout=35,
                )
                self.assertEqual(completed.returncode, expect, completed.stdout + completed.stderr)
                return json.loads(completed.stdout) if completed.stdout and expect == 0 else completed.stderr

            record = record_s1(cli, root, project, baseline)
            initialized = cli("init", "--project", project, "--baseline", baseline, "--s1-record", record,
                              "--suite", suite, "--frontend", "static", "--run-id", "smoke")
            self.assertEqual(initialized["epoch"], 1)
            self.assertEqual([row["step"] for row in initialized["baseline"]["s1_review"]["steps"]],
                             ["analysis", "scenes", "features", "tasks"])
            self.assertEqual(cli("requirements-check", "smoke")["registered_tasks"], ["C"])
            claim = cli("claim", "smoke", "C", "--epoch", 1)
            epoch = cli("takeover", "smoke", "--epoch", 1)["epoch"]
            recovered = cli("prepare-attempt", "smoke", "--epoch", epoch, claim["id"])
            self.assertEqual(recovered["workspace"], claim["workspace"])
            cli("start", "smoke", "--epoch", epoch, claim["id"], "--worker", "scene-c")
            workspace = Path(claim["workspace"])
            (workspace / "index.html").write_text(
                '<!doctype html><meta charset="utf-8"><title>Layout</title>'
                '<main data-rtl-scene="SCN-C" data-rtl-feature="FUNC-C01">'
                '<input id="layout"><button id="save">Save</button><output id="result"></output></main>'
                '<script src="layout.js"></script>'
            )
            (workspace / "layout.js").write_text(
                "const result = document.querySelector('#result');"
                "result.textContent = localStorage.getItem('layout') || 'default';"
                "document.querySelector('#save').onclick = () => {"
                "localStorage.setItem('layout', document.querySelector('#layout').value); };"
            )
            (workspace / "wrong").mkdir()
            (workspace / "wrong/index.html").write_text(
                '<main data-rtl-scene="SCN-C" data-rtl-feature="FUNC-C01">Layout placeholder</main>'
            )
            subprocess.run(["git", "add", "--", "index.html", "layout.js", "wrong/index.html"],
                           cwd=workspace, check=True)
            subprocess.run(
                ["git", "-c", "user.name=RTL smoke", "-c", "user.email=rtl@example.invalid",
                 "commit", "-qm", "layout preference"],
                cwd=workspace, check=True,
            )
            submitted = cli("submit", "smoke", "--epoch", epoch, claim["id"], "--actor", "scene-c")
            context = root / "self.json"
            context.write_text(json.dumps({"attempt_id": claim["id"]}))
            report = root / "self-report.json"
            cli("browser", workspace / "index.html", suite, report)
            cli("evidence", "smoke", "--epoch", epoch, "self", submitted["result"]["commit"],
                "--actor", "scene-c", "--context", context, "--report", report)
            staged = cli("stage", "smoke", "--epoch", epoch, claim["id"])
            cli("promote", "smoke", "--epoch", epoch, staged["operation_id"])
            landed = cli("land", "smoke", "--epoch", epoch)
            self.assertEqual(landed["status"], "pending_verification")
            failure = cli("finalize", "smoke", "--epoch", epoch, landed["operation_id"],
                          "--actor", "master", expect=1)
            self.assertIn("独立验证", failure)
            delivery_context = root / "delivery.json"
            delivery_context.write_text(json.dumps({"operation_id": landed["operation_id"]}))
            business_report = root / "business-only-report.json"
            cli("browser", landed["entry"], suite, business_report)
            cli("evidence", "smoke", "--epoch", epoch, "delivery", landed["candidate"],
                "--actor", "independent", "--context", delivery_context, "--report", business_report)
            missing_routes = cli("finalize", "smoke", "--epoch", epoch, landed["operation_id"],
                                 "--actor", "master", expect=1)
            self.assertIn("独立浏览器路由证据", missing_routes)
            map_file.write_text(map_text.replace("| index.html |", "| wrong/index.html |"), encoding="utf-8")
            wrong_report = root / "wrong-route-report.json"
            cli("browser", landed["entry"], suite, wrong_report, "--feature-map", map_file, "--feature-entries")
            observed = json.loads(wrong_report.read_text())
            self.assertEqual(observed["cases"]["TC-C01"]["status"], "pass")
            self.assertEqual(
                observed["feature_routes"]["routes"]["wrong/index.html"]["features"]["FUNC-C01"]["status"], "pass",
            )
            cli("evidence", "smoke", "--epoch", epoch, "delivery", landed["candidate"],
                "--actor", "independent", "--context", delivery_context, "--report", wrong_report)
            wrong_route = cli("finalize", "smoke", "--epoch", epoch, landed["operation_id"],
                              "--actor", "master", expect=1)
            self.assertIn("缺少绑定功能地图实际入口的业务断言证据", wrong_route)
            map_file.write_text(map_text, encoding="utf-8")
            final_report = root / "final-report.json"
            cli("browser", landed["entry"], suite, final_report, "--feature-map", docs / "feature-map.md",
                "--feature-entries")
            cli("evidence", "smoke", "--epoch", epoch, "delivery", landed["candidate"],
                "--actor", "independent", "--context", delivery_context, "--report", final_report)
            receipt = cli("finalize", "smoke", "--epoch", epoch, landed["operation_id"], "--actor", "master")
            replay = cli("finalize", "smoke", "--epoch", epoch, landed["operation_id"], "--actor", "master")
            self.assertEqual(receipt, replay)
            self.assertEqual(receipt["candidate"], staged["candidate"])
            self.assertEqual(receipt["entry"], str(project / "prototype" / "index.html"))
            self.assertEqual((docs / "change-log.md").read_text().count("<!-- rtl:"), 1)
            change_id = receipt["change_id"]
            ledger = (docs / "需求台账.md").read_text()
            self.assertIn(f"../memory/changes/{change_id}.md", ledger)
            change = (project / "memory/changes" / f"{change_id}.md").read_text()
            self.assertIn("../../docs/需求台账.md#func-c01", change)
