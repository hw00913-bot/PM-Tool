from pathlib import Path
import tempfile
import unittest

from loop import artifacts, contracts, dispatch
from loop.state import Store, pack, unpack


class CurrentContractBasisTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.store = Store(self.root / "state", reservation_root=self.root / "reservations")
        project = self.root / "project"
        self.store.create_run(project, project / "prototype", {}, run_id="R")
        artifacts.initialize_repo(self.store, "R", 1)
        for task in ("A", "B", "C"):
            self.store.add_task("R", 1, {
                "id": task, "paths": [f"{task}.txt"], "edges": ["ab"] if task != "C" else [],
            })

    def propose(self, version):
        return contracts.propose(self.store, "R", 1, "ab", version, "A", "B", {
            "behavior": f"Category behavior {version}", "cases": ["TC-ab"],
        })

    def confirm(self, version):
        for task in ("A", "B"):
            contracts.confirm(self.store, "R", 1, "ab", version, task)

    def activate(self, version):
        return contracts.activate(self.store, "R", 1, "ab", version)

    def choose(self, version):
        self.propose(version)
        self.confirm(version)
        return self.activate(version)

    def design(self, task="A"):
        attempt = dispatch.reserve(self.store, "R", 1, task, phase="design")
        workspace = artifacts.make_worktree(self.store, "R", attempt["id"])
        dispatch.bind_workspace(self.store, "R", 1, attempt["id"], workspace)
        return dispatch.start(self.store, "R", 1, attempt["id"], "designer")

    def test_retired_contract_and_stale_parallel_proposal_cannot_become_current(self):
        self.choose("initial")
        self.assertEqual(self.propose("second")["basis_version"], "initial")
        self.propose("parallel")
        self.confirm("second")
        self.confirm("parallel")
        self.activate("second")

        with self.assertRaisesRegex(ValueError, "已退役"):
            self.activate("initial")
        with self.assertRaisesRegex(ValueError, "已退役"):
            contracts.confirm(self.store, "R", 1, "ab", "initial", "A")
        with self.assertRaisesRegex(ValueError, "提案依据已失效"):
            self.activate("parallel")
        with self.assertRaisesRegex(ValueError, "提案依据已失效"):
            contracts.confirm(self.store, "R", 1, "ab", "parallel", "A")
        self.assertEqual(self.activate("second")["affected"], [])
        versions = contracts.list_contracts(self.store, "R")
        self.assertEqual([row["version"] for row in versions if row["active"]], ["second"])
        self.assertEqual(set(next(row["approvals"] for row in versions if row["version"] == "initial")), {"A", "B"})
        activation = self.store.operation("R", "contract-activation:ab:initial")
        self.assertEqual(activation["status"], "done")
        self.assertEqual(self.choose("newly-proposed")["version"], "newly-proposed")
        self.assertEqual(dispatch.reserve(self.store, "R", 1, "A")["input"]["contracts"], {"ab": "newly-proposed"})

    def test_legacy_active_contract_remains_current_but_unverifiable_older_row_cannot_activate(self):
        self.choose("old")
        self.choose("current")
        with self.store.transaction() as db:
            for row in db.execute("SELECT version,body FROM contracts WHERE run_id='R'"):
                body = unpack(row["body"])
                body.pop("basis_version")
                db.execute("UPDATE contracts SET body=? WHERE run_id='R' AND version=?", (pack(body), row["version"]))
            db.execute("DELETE FROM operations WHERE kind='contract-activation'")
        self.assertEqual(self.activate("current")["affected"], [])
        with self.assertRaisesRegex(ValueError, "提案依据"):
            self.activate("old")
        self.assertEqual(self.choose("new")["version"], "new")

    def test_bound_design_must_pause_and_is_fenced_by_contract_change(self):
        self.choose("v1")
        design = self.design()
        self.assertEqual(design["input"]["contracts"], {"ab": "v1"})
        self.propose("v2")
        self.confirm("v2")
        with self.assertRaisesRegex(ValueError, "Pause or stop"):
            self.activate("v2")
        dispatch.pause(self.store, "R", 1, design["id"])
        activated = self.activate("v2")
        self.assertIn("A", activated["invalidated"])
        self.assertEqual(self.store.attempt(design["id"])["state"], "fenced")
        with self.assertRaisesRegex(ValueError, "authoritative"):
            dispatch.submit(self.store, "R", 1, design["id"], {"proposal": "v1 design"})
        self.assertEqual(self.design()["input"]["contracts"], {"ab": "v2"})

    def test_unbound_design_can_finish_after_first_activation_and_still_be_handed_off(self):
        design = self.design()
        self.assertEqual(design["input"]["contracts"], {})
        self.choose("v1")
        summary = {"proposal": "Before first freeze"}
        dispatch.submit(self.store, "R", 1, design["id"], {"summary": summary})
        claim = dispatch.reserve(self.store, "R", 1, "A")
        self.assertEqual(claim["input"]["contracts"], {"ab": "v1"})
        self.assertEqual(claim["input"]["design_handoff"], {"attempt_id": design["id"], "summary": summary})

    def test_replacement_fences_unbound_design_that_survived_first_activation(self):
        design = self.design()
        self.choose("v1")
        self.propose("v2")
        self.confirm("v2")
        with self.assertRaisesRegex(ValueError, "Pause or stop"):
            self.activate("v2")
        dispatch.pause(self.store, "R", 1, design["id"])
        self.assertIn("A", self.activate("v2")["invalidated"])
        self.assertEqual(self.store.attempt(design["id"])["state"], "fenced")
        self.assertEqual(self.design()["input"]["contracts"], {"ab": "v2"})

    def test_historical_unbound_design_is_usable_only_on_first_activation(self):
        design = self.design()
        summary = {"proposal": "Before first freeze"}
        dispatch.submit(self.store, "R", 1, design["id"], {"summary": summary})
        self.choose("v1")
        original = dispatch.reserve(self.store, "R", 1, "A")
        self.assertEqual(original["input"]["design_handoff"], {"attempt_id": design["id"], "summary": summary})
        dispatch.fence(self.store, "R", 1, original["id"], stopped=True)
        self.choose("v2")
        self.assertEqual(self.store.attempt(design["id"])["state"], "completed")
        replacement = dispatch.reserve(self.store, "R", 1, "A")
        self.assertNotIn("design_handoff", replacement["input"])
        dispatch.fence(self.store, "R", 1, replacement["id"], stopped=True)
        self.assertEqual(self.design()["input"]["contracts"], {"ab": "v2"})

    def test_legacy_unbound_design_requires_proof_of_first_activation(self):
        design = self.design()
        dispatch.submit(self.store, "R", 1, design["id"], {"proposal": "Before legacy freeze"})
        self.choose("v1")
        with self.store.transaction() as db:
            row = db.execute("SELECT body FROM contracts WHERE run_id='R' AND edge='ab' AND version='v1'").fetchone()
            body = unpack(row["body"])
            body.pop("basis_version")
            db.execute("UPDATE contracts SET body=? WHERE run_id='R' AND edge='ab' AND version='v1'", (pack(body),))
            db.execute("DELETE FROM operations WHERE kind='contract-activation'")
        first = dispatch.reserve(self.store, "R", 1, "A")
        self.assertIn("design_handoff", first["input"])
        dispatch.fence(self.store, "R", 1, first["id"], stopped=True)
        self.propose("unverified-old-row")
        known_draft = dispatch.reserve(self.store, "R", 1, "A")
        self.assertIn("design_handoff", known_draft["input"])
        dispatch.fence(self.store, "R", 1, known_draft["id"], stopped=True)
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT body FROM contracts WHERE run_id='R' AND edge='ab' AND version='unverified-old-row'",
            ).fetchone()
            body = unpack(row["body"])
            body.pop("basis_version")
            db.execute("UPDATE contracts SET body=? WHERE run_id='R' AND edge='ab' AND version='unverified-old-row'", (pack(body),))
        claim = dispatch.reserve(self.store, "R", 1, "A")
        self.assertEqual(claim["input"]["contracts"], {"ab": "v1"})
        self.assertNotIn("design_handoff", claim["input"])

    def test_historical_bound_design_is_never_sent_to_implementation_on_new_contract(self):
        self.choose("v1")
        design = self.design()
        dispatch.submit(self.store, "R", 1, design["id"], {"summary": {"proposal": "Old v1 design"}})
        old_implementation = dispatch.reserve(self.store, "R", 1, "A")
        self.assertIn("design_handoff", old_implementation["input"])
        dispatch.fence(self.store, "R", 1, old_implementation["id"], stopped=True)
        self.choose("v2")
        self.assertEqual(self.store.attempt(design["id"])["state"], "completed")
        fresh = dispatch.reserve(self.store, "R", 1, "A")
        self.assertEqual(fresh["input"]["contracts"], {"ab": "v2"})
        self.assertNotIn("design_handoff", fresh["input"])
        # An implementation already reserved by the older CLI can still carry it.
        legacy = {**fresh["input"], "design_handoff": old_implementation["input"]["design_handoff"]}
        with self.store.transaction() as db:
            db.execute("UPDATE attempts SET input=? WHERE id=?", (pack(legacy), fresh["id"]))
        workspace = artifacts.make_worktree(self.store, "R", fresh["id"])
        dispatch.bind_workspace(self.store, "R", 1, fresh["id"], workspace)
        with self.assertRaisesRegex(ValueError, "设计交接依据已失效"):
            dispatch.start(self.store, "R", 1, fresh["id"], "implementer")
        dispatch.fence(self.store, "R", 1, fresh["id"], stopped=True)
        self.assertEqual(self.design()["input"]["contracts"], {"ab": "v2"})
