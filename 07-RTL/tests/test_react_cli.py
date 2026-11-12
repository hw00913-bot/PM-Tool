"""Real npm, Umi and browser coverage for the new default RTL profile."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from loop.browser import asset_digest


from s1_workflow_fixture import record_s1


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get("RTL_TEST_REACT_BROWSER") == "1", "opt in with RTL_TEST_REACT_BROWSER=1")
class ReactLoopSmoke(unittest.TestCase):
    def test_default_react_candidate_builds_verifies_and_hands_off(self):
        with tempfile.TemporaryDirectory(prefix="rtl-react-cli-") as folder:
            root = Path(folder).resolve()
            data, project = root / "data", root / "project"
            docs = project / "docs"
            docs.mkdir(parents=True)

            def write_json(name, value):
                path = root / name
                path.write_text(json.dumps(value), encoding="utf-8")
                return path

            runner = (
                "import sys; from functools import partial; from loop import __main__ as cli; "
                "cli.Store = partial(cli.Store, reservation_root=sys.argv.pop(1)); "
                "raise SystemExit(cli.main())"
            )

            def cli(*arguments, success=True):
                result = subprocess.run(
                    [sys.executable, "-c", runner, str(root / "reservations"), "--data", str(data), *map(str, arguments)],
                    cwd=ROOT, capture_output=True, text=True, timeout=1240,
                )
                diagnostic = result.stdout + result.stderr
                if result.returncode and arguments[0] == "browser":
                    report = Path(arguments[-1])
                    if report.is_file():
                        diagnostic += report.read_text(encoding="utf-8")
                self.assertEqual(result.returncode == 0, success, diagnostic)
                return json.loads(result.stdout) if success and result.stdout else diagnostic

            (docs / "feature-map.md").write_text(
                "| 功能 | 场景 | 入口 | 契约 | 必跑用例 |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| FUNC-C01 | SCN-C | index.html#/ | - | TC-REACT |\n",
                encoding="utf-8",
            )
            plan = json.loads(
                (ROOT / "skills/requirement-architecture/references/requirements-example.json").read_text()
            )
            plan["features"][0]["cases"] = ["TC-REACT"]
            plan["features"][0]["assertions"] = [
                {"case": "TC-REACT", "step": {"op": "text", "selector": "#rtl-review-value", "value": "compact"}},
            ]
            plan["tasks"][0].update(id="R", paths=["src/pages/Review.jsx"], cases=["TC-REACT"], risk=True)
            suite = write_json("suite.json", {
                "suite_revision": "react-suite", "fixture_revision": "react-fixture",
                "cases": [{"id": "TC-REACT", "steps": [
                    {"op": "text", "selector": "h1", "value": "工作台试点"},
                    {"op": "css", "selector": "#rtl-review-input", "property": "font-size", "value": "13px"},
                    {"op": "css", "selector": "#rtl-review-input", "property": "height", "value": "34px"},
                    {"op": "css", "selector": "#rtl-review-save", "property": "font-size", "value": "13px"},
                    {"op": "css", "selector": "#rtl-review-save", "property": "height", "value": "34px"},
                    {"op": "css", "selector": "#rtl-review-save", "property": "background-color", "value": "rgb(22, 119, 255)"},
                    {"op": "css", "selector": "#rtl-small-button", "property": "height", "value": "28px"},
                    {"op": "css", "selector": ".prototype-card", "property": "border-radius", "value": "8px"},
                    {"op": "css", "selector": "#rtl-theme-table th", "property": "height", "value": "44px"},
                    {"op": "css", "selector": "#rtl-theme-table td", "property": "height", "value": "52px"},
                    {"op": "css", "selector": "#rtl-theme-table td", "property": "font-size", "value": "13px"},
                    {"op": "click", "selector": "#rtl-theme-modal"},
                    {"op": "visible", "selector": ".ant-modal-content"},
                    {"op": "css", "selector": ".ant-modal-content", "property": "border-radius", "value": "10px"},
                    {"op": "click", "selector": ".ant-modal-confirm-btns button"},
                    {"op": "fill", "selector": "#rtl-review-input", "value": "compact"},
                    {"op": "click", "selector": "#rtl-review-save"},
                    {"op": "reload"},
                    {"op": "text", "selector": "#rtl-review-value", "value": "compact"},
                    {"op": "click", "selector": "#rtl-guide-link"},
                    {"op": "visible", "selector": "h1:has-text('使用说明')"},
                ]}],
            })

            steps = json.loads(suite.read_text())["cases"][0]["steps"]
            binding = plan["features"][0]["assertions"][0]
            index = steps.index(binding["step"])
            binding["checkpoint"] = {
                "index": index,
                "after": [step for step in steps[:index] if step["op"] in {"click", "fill", "reload"}],
            }
            baseline = write_json("baseline.json", {
                "revision": "react-smoke", "mandatory_cases": ["TC-REACT"],
                "suite_revision": "react-suite", "fixture_revision": "react-fixture",
                "suite_digests": {hashlib.sha256(suite.read_bytes()).hexdigest(): ["TC-REACT"]},
                "requirements": plan,
            })

            def prove(kind, target, actor, context, entry):
                report = root / f"{kind}-report.json"
                route_map = ("--feature-map", docs / "feature-map.md", "--feature-entries") if kind == "delivery" else ()
                cli("browser", entry, suite, report, *route_map)
                return cli(
                    "evidence", "react-smoke", kind, target, "--actor", actor,
                    "--context", write_json(f"{kind}-context.json", context), "--report", report, "--epoch", 1,
                )

            record = record_s1(cli, root, project, baseline)
            run = cli("init", "--project", project, "--baseline", baseline, "--s1-record", record,
                      "--suite", suite, "--run-id", "react-smoke")
            self.assertEqual(run["baseline"]["frontend"], "react")
            self.assertEqual(cli("requirements-check", "react-smoke")["registered_tasks"], ["R"])
            attempt = cli("claim", "react-smoke", "R", "--epoch", 1)
            cli("start", "react-smoke", attempt["id"], "--worker", "author", "--epoch", 1)
            workspace = Path(attempt["workspace"])
            page = workspace / "src/pages/Review.jsx"
            text = page.read_text(encoding="utf-8")
            self.assertIn("<h1>工作台</h1>", text)
            text = text.replace("{ Button, Card, Form, Input }", "{ App, Button, Card, Form, Input, Table }")
            text = text.replace("export default function Review() {", "export default function Review() {\n  const { modal } = App.useApp();")
            text = text.replace("<h1>工作台</h1>", """<h1 data-rtl-scene="SCN-C" data-rtl-feature="FUNC-C01">工作台试点</h1>
      <Table id="rtl-theme-table" columns={[{ title: '名称', dataIndex: 'name' }]}
        dataSource={[{ key: 'one', name: '样例' }]} pagination={false} />
      <Button id="rtl-small-button" size="small">小按钮</Button>
      <Button id="rtl-theme-modal" onClick={() => modal.info({ title: '样式检查', content: '主题弹层' })}>检查弹窗</Button>""")
            page.write_text(text, encoding="utf-8")
            subprocess.run(["git", "add", "--", "src/pages/Review.jsx"], cwd=workspace, check=True)
            subprocess.run(["git", "-c", "user.name=RTL smoke", "-c", "user.email=rtl@example.invalid",
                            "commit", "-qm", "React smoke review page"], cwd=workspace, check=True)
            submitted = cli("submit", "react-smoke", attempt["id"], "--actor", "author", "--epoch", 1)
            self_build = cli("build", "react-smoke", "--attempt", attempt["id"], "--epoch", 1)
            self.assertEqual(self_build["source_commit"], submitted["result"]["commit"])
            self_mode = self_build["dependency_install"]["mode"]
            self.assertIn(self_mode, ("installed", "uncached"))
            self.assertFalse((workspace / "node_modules").exists())
            prove("self", self_build["source_commit"], "author", {"attempt_id": attempt["id"]}, self_build["entry"])

            staged = cli("stage", "react-smoke", attempt["id"], "--epoch", 1)
            self.assertIn("缺少已登记的 React 构建", cli("promote", "react-smoke", staged["operation_id"], "--epoch", 1, success=False))
            staged_build = cli("build", "react-smoke", "--operation", staged["operation_id"], "--epoch", 1)
            self.assertEqual(staged_build["lockfile_sha256"], self_build["lockfile_sha256"])
            if self_mode == "installed":
                self.assertEqual(staged_build["dependency_install"]["mode"], "cache-hit")
                self.assertEqual(staged_build["commands"], [["npm", "run", "build"]])
            else:
                self.assertEqual(staged_build["dependency_install"]["mode"], "uncached")
                self.assertEqual(staged_build["commands"][0][:2], ["npm", "ci"])
            proof = prove("integration", staged["candidate"], "verifier",
                          {"operation_id": staged["operation_id"]}, staged_build["entry"])
            self.assertEqual(proof["context"]["asset_digest"], staged_build["asset_digest"])
            cli("promote", "react-smoke", staged["operation_id"], "--epoch", 1)

            delivery = cli("land", "react-smoke", "--epoch", 1)
            final_entry = project / "prototype" / "index.html"
            self.assertEqual(Path(delivery["entry"]), final_entry)
            self.assertEqual(asset_digest(final_entry.parent), staged_build["asset_digest"])
            prove("delivery", staged["candidate"], "verifier", {"operation_id": delivery["operation_id"]}, final_entry)
            accepted = cli("finalize", "react-smoke", delivery["operation_id"], "--actor", "master", "--epoch", 1)
            self.assertEqual(accepted["status"], "delivered")
            source = Path(accepted["source"])
            self.assertEqual((source / "src/pages/Review.jsx").read_bytes(), page.read_bytes())
            self.assertTrue((source / "package-lock.json").is_file())
            self.assertFalse((source / "node_modules").exists())
            self.assertFalse((final_entry.parent / "package.json").exists())

            successor = json.loads(baseline.read_text())
            successor["requirements"]["changes"][0].update(kind="verify", before="原有保存回读规则", after="原规则复验")
            successor_path = write_json("successor.json", successor)
            successor_record = record_s1(cli, root, project, successor_path, name="successor")
            continued = cli("init", "--project", project, "--baseline", successor_path, "--s1-record",
                            successor_record, "--suite", suite, "--run-id", "react-next")
            self.assertEqual(continued["baseline"]["frontend"], "react")
            continued_path = data / "projects" / continued["project_id"] / "runs/react-next/repository/src/pages/Review.jsx"
            self.assertEqual(continued_path.read_bytes(), page.read_bytes())
