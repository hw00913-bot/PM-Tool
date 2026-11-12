import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loop import artifacts, contracts, dispatch, verification
from loop.state import Store


class ContractIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        project = root / "project"
        prototype = project / "prototype"
        prototype.mkdir(parents=True)
        (prototype / "index.html").write_text("prototype", encoding="utf-8")
        self.store = Store(root / "data", reservation_root=root / "reservations")
        self.run = self.store.create_run(
            project, root / "output", {"fixture_revision": "f1", "suite_revision": "s1"}, run_id="run"
        )["id"]
        self.epoch = 1
        self.context = {"fixture_revision": "f1", "suite_revision": "s1"}
        self.submissions = {}
        artifacts.initialize_repo(self.store, self.run, self.epoch)

    def add_task(self, task, *, edges=(), deps=()):
        self.store.add_task(self.run, self.epoch, {
            "id": task, "paths": [f"{task}.txt"], "cases": [f"TC-{task}"],
            "edges": list(edges), "deps": list(deps),
        })

    def activate(self, version="v1", *, edge="ab", provider="A", consumer="B", compatible=None):
        spec = {"behavior": "shared contract", "cases": [f"TC-{edge}"]}
        if compatible is not None:
            spec["compatible_versions"] = [{"provider": version, "consumer": compatible}]
        contracts.propose(self.store, self.run, self.epoch, edge, version, provider, consumer, spec)
        for task in (provider, consumer):
            contracts.confirm(self.store, self.run, self.epoch, edge, version, task)
        return contracts.activate(self.store, self.run, self.epoch, edge, version)

    def start(self, task, actor=None):
        attempt = dispatch.reserve(self.store, self.run, self.epoch, task)["id"]
        workspace = artifacts.make_worktree(self.store, self.run, attempt)
        dispatch.bind_workspace(self.store, self.run, self.epoch, attempt, workspace)
        dispatch.start(self.store, self.run, self.epoch, attempt, actor or task)
        return attempt, workspace

    def record(self, kind, sha, actor, case, **context):
        verification.record(
            self.store, self.run, self.epoch, kind, sha, actor,
            {case: {"status": "pass", "actual": "Synthetic contract fixture"}},
            {**self.context, **context},
        )

    def submit(self, task, content=None, *, revalidate=False, actor=None):
        actor = actor or task
        attempt, workspace = self.start(task, actor)
        if not revalidate:
            path = workspace / f"{task}.txt"
            path.write_text(content, encoding="utf-8")
            artifacts.git(workspace, "add", path.name)
            artifacts.git(workspace, "commit", "-m", f"Implement {task} {content}")
        result = artifacts.capture_submission(self.store, self.run, attempt, actor, revalidate=revalidate)
        self.record("self", result["commit"], actor, f"TC-{task}", attempt_id=attempt)
        dispatch.submit(self.store, self.run, self.epoch, attempt, result)
        self.submissions[task] = attempt, result["commit"]

    def stage(self, *tasks):
        return artifacts.stage(self.store, self.run, self.epoch, [self.submissions[task][0] for task in tasks])

    def contract_proof(self, staged, version, actor="reviewer"):
        self.record(
            "contract", staged["candidate"], actor, "TC-ab",
            operation_id=staged["operation_id"], edge="ab", version=version,
            provider={"task": "A", "commit": self.submissions["A"][1]},
            consumer={"task": "B", "commit": self.submissions["B"][1]},
        )

    def promote(self, staged):
        return artifacts.promote(self.store, self.run, self.epoch, staged["operation_id"])

    def integrate_v1(self):
        for task in ("A", "B"):
            self.add_task(task, edges=["ab"])
        self.activate()
        for task in ("A", "B"):
            self.submit(task, "v1")
        staged = self.stage("A", "B")
        self.contract_proof(staged, "v1")
        self.promote(staged)

    def test_upgrade_preserves_only_the_consumer_version_it_actually_allows(self):
        self.integrate_v1()
        original_consumer = self.submissions["B"][0]
        for version in ("v2", "v3"):
            activation = self.activate(version, compatible="v1")
            self.assertEqual(activation["invalidated"], ["A"])
            self.submit("A", version)
            staged = self.stage("A")
            self.contract_proof(staged, version)
            self.promote(staged)
        activation = self.activate("v4", compatible="v3")
        self.assertEqual(activation["invalidated"], ["A", "B"])
        self.assertEqual(self.store.attempt(original_consumer)["state"], "fenced")
        for task in ("A", "B"):
            self.submit(task, "v4")
        staged = self.stage("A", "B")
        self.contract_proof(staged, "v4")
        self.promote(staged)

    def test_unchanged_consumer_can_revalidate_against_the_new_contract_pair(self):
        self.integrate_v1()
        original_consumer = self.submissions["B"][0]
        self.activate("v2")
        self.submit("A", "v2")
        self.submit("B", revalidate=True, actor="recheck-B")
        unchanged = self.store.attempt(self.submissions["B"][0])["result"]
        self.assertEqual(unchanged["revalidation_of"], original_consumer)
        self.assertEqual(unchanged["paths"], [])
        staged = self.stage("A", "B")
        self.record("integration", staged["candidate"], "reviewer", "TC-B", operation_id=staged["operation_id"])
        with self.assertRaisesRegex(ValueError, "Missing exact independent contract verification for ab/v2"):
            self.promote(staged)
        with self.assertRaisesRegex(ValueError, "作者"):
            self.contract_proof(staged, "v2", actor="B")
        with patch.object(verification, "attempt_authors", return_value={"A", "recheck-B"}):
            self.contract_proof(staged, "v2", actor="B")
        with self.assertRaisesRegex(ValueError, "Missing exact independent contract verification for ab/v2"):
            self.promote(staged)
        self.contract_proof(staged, "v2")
        self.promote(staged)

        self.add_task("S", edges=["ab"], deps=["A"])
        self.submit("S", "shared infrastructure")
        shared = self.stage("S")
        with self.assertRaisesRegex(ValueError, "作者"):
            self.contract_proof(shared, "v2", actor="B")
        self.contract_proof(shared, "v2")
        self.promote(shared)

    def test_supplemental_contract_failure_survives_restage_of_same_pair(self):
        for task in ("A", "B"):
            self.add_task(task, edges=["ab"])
        self.activate()
        for task in ("A", "B"):
            self.submit(task, "v1")
        original = self.stage("A", "B")
        context = {
            **self.context, "operation_id": original["operation_id"], "edge": "ab", "version": "v1",
            "provider": {"task": "A", "commit": self.submissions["A"][1]},
            "consumer": {"task": "B", "commit": self.submissions["B"][1]},
        }
        failure = verification.record(
            self.store, self.run, self.epoch, "contract", original["candidate"], "reviewer",
            {"TC-ab": {"status": "pass", "actual": "Expected provider/consumer result"},
             "TC-C": {"status": "fail", "actual": "Layout no longer persists"}},
            context,
        )
        with self.assertRaisesRegex(ValueError, r"contract.*TC-C"):
            self.promote(original)
        restaged = self.stage("A", "B")
        self.contract_proof(restaged, "v1")
        with self.assertRaisesRegex(ValueError, r"contract.*TC-C"):
            self.promote(restaged)
        self.record("contract", restaged["candidate"], "reviewer", "TC-C",
                    **{**context, "operation_id": restaged["operation_id"]})
        self.assertEqual(self.promote(restaged)["retested_failures"], [failure["id"]])

    def test_activation_rejects_a_hard_dependency_across_two_shared_edges(self):
        self.add_task("A", edges=["ab"])
        self.add_task("B", edges=["ab", "bc"])
        self.add_task("C", edges=["bc"], deps=["A"])
        self.activate()
        with self.assertRaisesRegex(ValueError, "C must wait for A"):
            self.activate(edge="bc", provider="B", consumer="C")
        active = [row["edge"] for row in contracts.list_contracts(self.store, self.run) if row["active"]]
        self.assertEqual(active, ["ab"])

    def test_shared_task_cannot_gate_a_party_it_needs_to_co_integrate(self):
        self.add_task("A", edges=["ab"], deps=["S"])
        self.add_task("B", edges=["ab"])
        self.add_task("S", edges=["ab"])
        with self.assertRaisesRegex(ValueError, "A must wait for S"):
            self.activate()
        self.assertFalse(any(row["active"] for row in contracts.list_contracts(self.store, self.run)))

    def test_late_task_registration_rejects_a_mixed_dependency_cycle(self):
        self.add_task("A", edges=["ab"], deps=["C"])
        self.add_task("B", edges=["ab"])
        self.activate()
        with self.assertRaisesRegex(ValueError, "contract co-integration"):
            self.add_task("C", deps=["B"])
        self.assertEqual({task["id"] for task in self.store.tasks(self.run)}, {"A", "B"})

    def test_shared_task_requires_fresh_exact_contract_evidence(self):
        self.integrate_v1()
        self.add_task("S", edges=["ab"], deps=["A"])
        self.submit("S", "shared infrastructure")
        staged = self.stage("S")
        with self.assertRaisesRegex(ValueError, "Missing exact independent contract verification for ab/v1"):
            self.promote(staged)
        legacy_stage = {field: value for field, value in staged.items() if field != "required_edges"}
        with self.store.transaction(immediate=False) as db:
            with self.assertRaisesRegex(ValueError, "Missing exact independent contract verification for ab/v1"):
                contracts.check_pairs(db, self.run, legacy_stage)
        self.contract_proof(staged, "v1")
        self.promote(staged)

    def test_activation_reaches_tasks_declaring_a_downstream_edge(self):
        self.add_task("A", edges=["ab"])
        self.add_task("B", edges=["ab", "bc"])
        self.add_task("C", edges=["bc"])
        self.add_task("S", edges=["bc"])
        self.activate()
        self.activate(edge="bc", provider="B", consumer="C")
        attempt, _ = self.start("S")
        with self.assertRaisesRegex(ValueError, "Pause or stop .* for S"):
            self.activate("v2")
        self.assertEqual(self.store.attempt(attempt)["state"], "running")
        dispatch.pause(self.store, self.run, self.epoch, attempt)
        activation = contracts.activate(self.store, self.run, self.epoch, "ab", "v2")
        self.assertEqual(activation["affected"], ["A", "B", "C", "S"])
        self.assertEqual(activation["invalidated"], ["S"])

    def test_activation_accounts_for_shared_tasks_using_the_modified_edge(self):
        self.integrate_v1()
        self.add_task("S", edges=["ab"])
        attempt, _ = self.start("S")
        with self.assertRaisesRegex(ValueError, "Pause or stop .* for S"):
            self.activate("v2")
        self.assertEqual(self.store.attempt(attempt)["state"], "running")
        dispatch.pause(self.store, self.run, self.epoch, attempt)
        activation = contracts.activate(self.store, self.run, self.epoch, "ab", "v2")
        self.assertEqual(activation["invalidated"], ["A", "B", "S"])
        self.assertEqual(self.store.attempt(attempt)["state"], "fenced")
