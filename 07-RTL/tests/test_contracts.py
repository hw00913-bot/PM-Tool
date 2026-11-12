import tempfile
from pathlib import Path
import unittest

from loop import artifacts, contracts, dispatch
from loop.state import Store


class ContractActivation(unittest.TestCase):
    def test_breaking_change_preserves_independent_task_and_invalidates_old_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            store = Store(root / "state", reservation_root=root / "reservations")
            store.create_run(root / "project", root / "project" / "prototype", {}, run_id="run")
            artifacts.initialize_repo(store, "run", 1)
            for task in "ABC":
                store.add_task("run", 1, {
                    "id": task, "paths": [task], "edges": ["category"] if task != "C" else [],
                })

            def activate(version):
                contracts.propose(store, "run", 1, "category", version, "A", "B",
                                  {"behavior": "Category state", "cases": ["TC-category"]})
                for party in "AB":
                    contracts.confirm(store, "run", 1, "category", version, party)
                return contracts.activate(store, "run", 1, "category", version)

            activate("v1")
            b = dispatch.reserve(store, "run", 1, "B")
            c = dispatch.reserve(store, "run", 1, "C")
            for attempt in (b, c):
                workspace = store.root / attempt["id"]
                workspace.mkdir()
                dispatch.bind_workspace(store, "run", 1, attempt["id"], workspace)
                dispatch.start(store, "run", 1, attempt["id"], attempt["task_id"])
            dispatch.pause(store, "run", 1, b["id"])
            change = activate("v2")
            self.assertEqual(change["affected"], ["A", "B"])
            self.assertEqual(change["invalidated"], ["B"])
            self.assertEqual(store.attempt(b["id"])["state"], "fenced")
            self.assertEqual(store.attempt(c["id"])["state"], "running")
            with self.assertRaises(ValueError):
                dispatch.submit(store, "run", 1, b["id"], {"commit": "late"})
            new_b = dispatch.reserve(store, "run", 1, "B")
            self.assertNotEqual(new_b["id"], b["id"])
            self.assertEqual(new_b["input"]["contracts"], {"category": "v2"})
