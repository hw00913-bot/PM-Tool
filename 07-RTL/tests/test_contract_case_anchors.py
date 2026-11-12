from argparse import Namespace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from loop import artifacts, browser, contracts, dispatch, requirements, requirements_run, suite_catalog
from loop.__main__ import record_evidence
from loop.assertions import browser_case_plan
from loop.state import Store


def marker(side):
    return {
        "op": "visible", "selector": f'[data-rtl-feature~="FUNC-{side}"][data-rtl-scene="SCN-{side}"]',
    }


class ContractCaseAnchorsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        project = self.root / "project"
        docs = project / "docs"
        docs.mkdir(parents=True)
        prototype = project / "prototype"
        prototype.mkdir()
        (prototype / "index.html").write_text("<h1>Prototype</h1>", encoding="utf-8")
        (docs / "feature-map.md").write_text(
            "| 功能 | 场景 | 入口 | 契约 | 必跑用例 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| FUNC-A | SCN-A | index.html#/provider | ab | TC-A |\n"
            "| FUNC-B | SCN-B | index.html#/consumer | ab | TC-B |\n"
            "| FUNC-C | SCN-C | index.html#/layout | - | TC-C |\n", encoding="utf-8",
        )
        self.inputs = {"fixture_revision": "f1", "suite_revision": "s1"}
        self.suites = {
            side: self._suite(side, {f"TC-{side}": [marker(side)]}) for side in "ABC"
        }
        self.suites["joint"] = self._suite("joint", {"TC-AB": [
            marker("A"), {"op": "click", "selector": "#disable"},
            marker("B"), {"op": "text", "selector": "#result", "value": "Disabled"},
        ]})
        self.suites["unrelated"] = self._suite("unrelated", {"TC-AB": [
            marker("C"), {"op": "text", "selector": "#layout", "value": "Compact"},
        ]})
        features, scenes, tasks, changes = [], [], [], []
        for side in "ABC":
            features.append({"id": f"FUNC-{side}", "scene_id": f"SCN-{side}", "name": side,
                             "requirement_ids": ["REQ-1"], "rule": "Expected result", "cases": [f"TC-{side}"]})
            scenes.append({"id": f"SCN-{side}", "requirement_ids": ["REQ-1"], "name": side, "actor": "Operator",
                           "trigger": "Start", "outcome": "Done", "preconditions": "Ready",
                           "main_flow": ["Open", "Check"], "alternatives": "Stop"})
            tasks.append({"id": side, "name": side, "input": "Scenario", "deliverable": "Implementation",
                          "done": "Self tested", "feature_ids": [f"FUNC-{side}"], "paths": [f"{side}.html"],
                          "cases": [f"TC-{side}"], "edges": ["ab"] if side != "C" else []})
            changes.append({"feature_id": f"FUNC-{side}", "kind": "add", "before": "", "after": "New feature"})
        mandatory = [f"TC-{side}" for side in "ABC"]
        plan = requirements.normalize_plan({
            "approval": "Verified test plan", "requirements": [{
                "id": "REQ-1", "name": "Shared operation", "source": "Fixture", "goal": "Shared category status",
                "scope": "Local test", "rule": "Disabled category blocks action", "acceptance": "No task added",
            }],
            "scenes": scenes, "features": features, "tasks": tasks, "changes": changes,
        }, mandatory)
        baseline = {"revision": "R1", **self.inputs, "mandatory_cases": mandatory, "requirements": plan,
                    "acceptance_browsers": ["chromium"], "suite_digests": {
                        hashlib.sha256(path.read_bytes()).hexdigest():
                        [item["id"] for item in json.loads(path.read_bytes())["cases"]]
                        for path in self.suites.values()
                    }}
        baseline["suite_catalog"] = suite_catalog.freeze(baseline, self.suites.values())
        self.store = Store(self.root / "runtime", reservation_root=self.root / "reservations")
        self.run = self.store.create_run(project, self.root / "output", baseline, run_id="R")["id"]
        artifacts.initialize_repo(self.store, self.run, 1)
        requirements_run.prepare(self.store, self.run, 1)

    def _suite(self, name, cases):
        path = self.root / f"{name}-suite.json"
        path.write_text(json.dumps({**self.inputs, "cases": [
            {"id": case, "steps": steps} for case, steps in cases.items()
        ]}), encoding="utf-8")
        return path

    @staticmethod
    def _anchors(case):
        return {case: {
            "provider": {"feature_id": "FUNC-A", "step": 0, "interaction": 1},
            "consumer": {"feature_id": "FUNC-B", "step": 2, "interaction": 3},
        }}

    def _propose(self, version, case, anchors):
        return contracts.propose(self.store, self.run, 1, "ab", version, "A", "B", {
            "behavior": "Disabled category prevents creation", "cases": [case], "case_anchors": anchors,
        })

    def _evidence(self, name, path, suite, target, kind, actor, *, browser_label="chromium 157",
                  execute_first_only=False, failure=False, **context):
        raw = suite.read_text(encoding="utf-8")
        entry = Path(path) / "index.html"
        outcomes = {}
        for case in json.loads(raw)["cases"]:
            executed, assertions = browser_case_plan(case["steps"])
            outcomes[case["id"]] = {"status": "fail", "executed": [], "errors": ["Expected behavior failed"]} if failure else {
                "status": "pass", "executed": executed[:1] if execute_first_only else executed,
                "assertions": assertions, "errors": [],
            }
        report = self.root / f"{name}.json"
        report.write_text(json.dumps({
            **self.inputs, "protocol": "file:", "path": str(entry), "entry": entry.as_uri(),
            "browser": browser_label, "asset_digest": browser.asset_digest(path),
            "suite_digest": hashlib.sha256(raw.encode()).hexdigest(), "suite_input": raw, "cases": outcomes,
        }), encoding="utf-8")
        context_path = self.root / f"{name}-context.json"
        context_path.write_text(json.dumps(context), encoding="utf-8")
        return record_evidence(self.store, Namespace(
            report=report, context=context_path, run=self.run, epoch=1, kind=kind, target=target, actor=actor,
        ))

    def _submit(self, side):
        actor = f"scene-{side}"
        attempt = dispatch.reserve(self.store, self.run, 1, side)["id"]
        path = artifacts.make_worktree(self.store, self.run, attempt)
        dispatch.bind_workspace(self.store, self.run, 1, attempt, path)
        dispatch.start(self.store, self.run, 1, attempt, actor)
        name = f"{side}.html"
        (path / name).write_text(f"<h1>{side}</h1>", encoding="utf-8")
        artifacts.git(path, "add", name)
        artifacts.git(path, "commit", "--quiet", "-m", name)
        submitted = artifacts.capture_submission(self.store, self.run, attempt, actor)
        self._evidence(f"self-{side}", path, self.suites[side], submitted["commit"], "self", actor,
                       attempt_id=attempt)
        dispatch.submit(self.store, self.run, 1, attempt, submitted)
        return attempt, submitted["commit"]

    def test_unrelated_cannot_be_declared_for_pair(self):
        with self.assertRaisesRegex(ValueError, r"TC-C.*冻结用例步骤"):
            self._propose("v1", "TC-C", self._anchors("TC-C"))
        anchors = self._anchors("TC-C")
        anchors["TC-C"]["provider"]["feature_id"] = "FUNC-C"
        with self.assertRaisesRegex(ValueError, r"provider.*FUNC-C"):
            self._propose("v1", "TC-C", anchors)

    def test_valid_supplemental_contract_case_requires_real_frozen_anchors(self):
        self._propose("v1", "TC-AB", self._anchors("TC-AB"))
        for side in "AB":
            contracts.confirm(self.store, self.run, 1, "ab", "v1", side)
        contracts.activate(self.store, self.run, 1, "ab", "v1")
        provider, provider_commit = self._submit("A")
        consumer, consumer_commit = self._submit("B")
        staged = artifacts.stage(self.store, self.run, 1, [provider, consumer])
        context = {"operation_id": staged["operation_id"], "edge": "ab", "version": "v1",
                   "provider": {"task": "A", "commit": provider_commit},
                   "consumer": {"task": "B", "commit": consumer_commit}}
        self._evidence("c-only", staged["path"], self.suites["unrelated"], staged["candidate"], "contract", "reviewer",
                       **context)
        with self.assertRaisesRegex(ValueError, r"contract.*TC-AB"):
            artifacts.promote(self.store, self.run, 1, staged["operation_id"])
        self._evidence("c-only-failed", staged["path"], self.suites["unrelated"], staged["candidate"],
                       "contract", "reviewer", failure=True, **context)
        with self.assertRaisesRegex(ValueError, r"冻结的用例"):
            self._evidence(
                "wrong-browser", staged["path"], self.suites["joint"], staged["candidate"], "contract",
                "reviewer", browser_label="firefox 148", **context,
            )
        self._evidence(
            "missing-steps", staged["path"], self.suites["joint"], staged["candidate"], "contract",
            "reviewer", execute_first_only=True, **context,
        )
        with self.assertRaisesRegex(ValueError, r"contract.*TC-AB"):
            artifacts.promote(self.store, self.run, 1, staged["operation_id"])
        self._evidence("true-joint", staged["path"], self.suites["joint"], staged["candidate"], "contract", "reviewer",
                       **context)
        receipt = artifacts.promote(self.store, self.run, 1, staged["operation_id"])
        self.assertEqual(receipt["candidate"], staged["candidate"])

    def test_restage_carries_only_failures_eligible_for_the_contract_case(self):
        self._propose("v1", "TC-AB", self._anchors("TC-AB"))
        for side in "AB":
            contracts.confirm(self.store, self.run, 1, "ab", "v1", side)
        contracts.activate(self.store, self.run, 1, "ab", "v1")
        provider, provider_commit = self._submit("A")
        consumer, consumer_commit = self._submit("B")
        original = artifacts.stage(self.store, self.run, 1, [provider, consumer])
        context = {"edge": "ab", "version": "v1", "provider": {"task": "A", "commit": provider_commit},
                   "consumer": {"task": "B", "commit": consumer_commit}}
        self._evidence("original-wrong-failure", original["path"], self.suites["unrelated"], original["candidate"],
                       "contract", "reviewer", failure=True, operation_id=original["operation_id"], **context)
        actual_failure = self._evidence("original-joint-failure", original["path"], self.suites["joint"],
                                        original["candidate"], "contract", "reviewer", failure=True,
                                        operation_id=original["operation_id"], **context)
        staged = artifacts.stage(self.store, self.run, 1, [provider, consumer])
        self._evidence("restage-wrong-pass", staged["path"], self.suites["unrelated"], staged["candidate"],
                       "contract", "reviewer", operation_id=staged["operation_id"], **context)
        with self.assertRaisesRegex(ValueError, r"contract.*仍有失败用例.*TC-AB"):
            artifacts.promote(self.store, self.run, 1, staged["operation_id"])
        self._evidence("restage-joint-pass", staged["path"], self.suites["joint"], staged["candidate"],
                       "contract", "reviewer", operation_id=staged["operation_id"], **context)
        receipt = artifacts.promote(self.store, self.run, 1, staged["operation_id"])
        self.assertEqual(receipt["retested_failures"], [actual_failure["id"]])
