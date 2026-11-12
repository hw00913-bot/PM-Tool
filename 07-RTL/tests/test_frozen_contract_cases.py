import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from loop import artifacts, contracts, dispatch, verification
from loop.state import Store, pack, unpack


class FrozenContractCases(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        project = self.root / "project"
        prototype = project / "prototype"
        prototype.mkdir(parents=True)
        (prototype / "index.html").write_text("<h1>RTL</h1>", encoding="utf-8")
        self.store = Store(self.root / "data", reservation_root=self.root / "reservations")
        self.run = "run"
        self.epoch = 1
        self.revisions = {"fixture_revision": "f1", "suite_revision": "s1"}
        self.self_digest = self.suite_digest("TC-A")
        self.store.create_run(project, self.root / "output", {
            **self.revisions, "mandatory_cases": ["TC-A"],
            "suite_digests": {
                self.self_digest: ["TC-A"],
                self.suite_digest("TC-ab"): ["TC-ab"],
                self.suite_digest("TC-extra"): ["TC-extra"],
            },
        }, run_id=self.run)
        artifacts.initialize_repo(self.store, self.run, self.epoch)
        for task in ("A", "B"):
            self.store.add_task(self.run, self.epoch, {
                "id": task, "paths": [f"{task}.html"], "cases": ["TC-A"], "edges": ["ab"],
            })

    def suite_digest(self, case):
        suite = {
            **self.revisions,
            "cases": [{"id": case, "steps": [{"op": "text", "selector": "h1", "equals": "RTL"}]}],
        }
        return hashlib.sha256(json.dumps(suite).encode()).hexdigest()

    def propose(self, version, *cases):
        return contracts.propose(self.store, self.run, self.epoch, "ab", version, "A", "B", {
            "behavior": "Shared category query", "cases": list(cases),
        })

    def activate(self, version):
        for party in ("A", "B"):
            contracts.confirm(self.store, self.run, self.epoch, "ab", version, party)
        return contracts.activate(self.store, self.run, self.epoch, "ab", version)

    def simulate_legacy_contract_cases(self, version, *cases):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT body FROM contracts WHERE run_id=? AND edge='ab' AND version=?", (self.run, version),
            ).fetchone()
            body = unpack(row["body"])
            body["spec"]["cases"] = list(cases)
            db.execute(
                "UPDATE contracts SET body=? WHERE run_id=? AND edge='ab' AND version=?",
                (pack(body), self.run, version),
            )

    def submit(self):
        attempt = dispatch.reserve(self.store, self.run, self.epoch, "A")["id"]
        workspace = artifacts.make_worktree(self.store, self.run, attempt)
        dispatch.bind_workspace(self.store, self.run, self.epoch, attempt, workspace)
        dispatch.start(self.store, self.run, self.epoch, attempt, "author-A")
        (workspace / "A.html").write_text("<h1>RTL</h1>", encoding="utf-8")
        artifacts.git(workspace, "add", "A.html")
        artifacts.git(workspace, "commit", "-m", "Implement A")
        result = artifacts.capture_submission(self.store, self.run, attempt, "author-A")
        cases = {"TC-A": {"status": "pass", "actual": "RTL heading was visible"}}
        report = self.root / "self-report.json"
        report.write_text(json.dumps({**self.revisions, "cases": cases}), encoding="utf-8")
        verification.record(self.store, self.run, self.epoch, "self", result["commit"], "author-A", cases, {
            **self.revisions, "suite_digest": self.self_digest, "attempt_id": attempt, "report_path": str(report),
        })
        dispatch.submit(self.store, self.run, self.epoch, attempt, result)
        return attempt

    def assert_no_stage(self):
        self.assertFalse(any(item["kind"] == "integration" for item in self.store.operations(self.run)))
        self.assertFalse((artifacts.run_directory(self.store, self.run) / "integration").exists())

    def test_proposal_rejects_unknown_case_and_accepts_nonmandatory_registered_case(self):
        with self.assertRaisesRegex(ValueError, r"ab/v1.*未登记.*TC-unknown"):
            self.propose("v1", "TC-ab", "TC-unknown")
        self.assertEqual(contracts.list_contracts(self.store, self.run), [])
        self.propose("v1", "TC-A", "TC-ab")
        self.assertEqual(self.activate("v1")["affected"], ["A", "B"])

    def test_activation_rechecks_old_draft_and_preserves_previous_version(self):
        self.propose("v1", "TC-ab")
        self.activate("v1")
        self.propose("v2", "TC-ab")
        self.simulate_legacy_contract_cases("v2", "TC-unknown")
        with self.assertRaisesRegex(ValueError, r"ab/v2.*未登记.*TC-unknown"):
            self.activate("v2")
        versions = contracts.list_contracts(self.store, self.run)
        self.assertEqual([(row["version"], row["active"]) for row in versions], [("v1", True), ("v2", False)])

    def test_stage_rejects_unknown_explicit_case_and_accepts_registered_supplemental_case(self):
        self.propose("v1", "TC-ab")
        self.activate("v1")
        attempt = self.submit()
        with self.assertRaisesRegex(ValueError, r"暂存合入.*未登记.*TC-unknown"):
            artifacts.stage(self.store, self.run, self.epoch, [attempt], required_cases=["TC-unknown"])
        self.assert_no_stage()
        staged = artifacts.stage(self.store, self.run, self.epoch, [attempt], required_cases=["TC-extra"])
        self.assertEqual(staged["required_cases"], ["TC-extra"])
        self.assertEqual(staged["required_edges"], ["ab"])

    def test_stage_rejects_required_active_contract_with_legacy_unknown_case(self):
        self.propose("v1", "TC-ab")
        self.activate("v1")
        attempt = self.submit()
        self.simulate_legacy_contract_cases("v1", "TC-unknown")
        with self.assertRaisesRegex(ValueError, r"ab/v1.*未登记.*TC-unknown"):
            artifacts.stage(self.store, self.run, self.epoch, [attempt])
        self.assert_no_stage()
