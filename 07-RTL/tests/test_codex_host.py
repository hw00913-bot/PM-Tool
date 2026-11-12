from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import __main__ as cli
from loop import artifacts, codex_host, contracts, dispatch
from loop.codex_client import CodexHistoryPending
from loop.state import LoopError, Store


class FakeAppServer:
    """Persist remote threads independently of an RTL client's lifetime."""

    def __init__(self):
        self.threads = {}
        self.start_calls = []
        self.interrupt_calls = []
        self.on_turn_start = None
        self.drop_thread_reply = False
        self.drop_turn_reply = None
        self.history_pending = False
        self.tools_drained = True

    def __call__(self, socket=None, *, timeout=20):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        return False

    def start_thread(self, workspace):
        identifier = f"thread-{len(self.threads) + 1}"
        self.threads[identifier] = {
            "id": identifier,
            "cwd": str(workspace),
            "ephemeral": False,
            "status": {"type": "idle"},
            "turns": [],
        }
        if self.drop_thread_reply:
            self.drop_thread_reply = False
            raise LoopError("simulated loss of thread/start reply")
        return identifier

    def start_turn(self, thread_id, workspace, prompt):
        thread = self.threads[thread_id]
        assert thread["cwd"] == str(workspace)
        if self.on_turn_start is not None:
            self.on_turn_start(thread_id)
        self.start_calls.append((thread_id, prompt))
        drop, self.drop_turn_reply = self.drop_turn_reply, None
        if drop == "before":
            raise LoopError("simulated disconnect before server execution")
        turn = {
            "id": f"turn-{len(thread['turns']) + 1}",
            "status": "inProgress",
            "items": [{"type": "userMessage", "id": "prompt", "content": [{"type": "text", "text": prompt}]}],
        }
        thread["turns"].append(turn)
        thread["status"] = {"type": "active", "activeFlags": []}
        if drop == "after":
            raise LoopError("simulated loss of turn/start reply")
        return deepcopy(turn)

    def read_thread(self, thread_id, *, turn_id=None, newest_only=False, include_items=True):
        if self.history_pending:
            raise CodexHistoryPending("simulated rollout materialization")
        thread = deepcopy(self.threads[thread_id])
        if turn_id:
            thread["turns"] = [turn for turn in thread["turns"] if turn["id"] == turn_id]
        elif newest_only:
            thread["turns"] = thread["turns"][-1:]
        if not include_items:
            for turn in thread["turns"]:
                turn["items"] = []
        return thread

    def resume_thread(self, thread_id):
        return self.read_thread(thread_id)

    def workspace_helper_baseline(self, workspace):
        return []

    def require_tools_drained(self, thread, turn, helpers=()):
        if not self.tools_drained:
            raise LoopError("测试宿主仍有工具正在写入")
        return {"kind": "fixture-tool-exit", "turn_id": turn["id"]}

    def interrupt_turn(self, thread_id, turn_id):
        self.interrupt_calls.append((thread_id, turn_id))
        thread = self.threads[thread_id]
        turn = next(item for item in thread["turns"] if item["id"] == turn_id)
        turn["status"] = "interrupted"
        thread["status"] = {"type": "idle"}
        return {}


class CodexHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        project = self.root / "project"
        output = project / "prototype"
        output.mkdir(parents=True)
        (output / "index.html").write_text("<title>Codex host fixture</title>", encoding="utf-8")
        self.store = Store(self.root / "state", reservation_root=self.root / "reservations")
        self.store.create_run(project, output, {"revision": "R1"}, project_id="P", run_id="R")
        artifacts.initialize_repo(self.store, "R", 1)
        for task in ("A", "B", "C"):
            self.store.add_task("R", 1, {"id": task, "paths": [f"{task}.html"], "cases": [f"TC-{task}"]})
        self.host = FakeAppServer()
        transport = patch.object(codex_host, "CodexClient", self.host)
        transport.start()
        self.addCleanup(transport.stop)

    def invoke(self, *args, store=None):
        target = store if store is not None else self.store
        parser = cli.build_parser()
        options = parser.parse_args(["--data", str(target.data_root), *args])
        with patch.object(cli, "Store", return_value=target):
            return cli.invoke(options, parser)

    def claim(self, task, *, epoch=1):
        return self.invoke("claim", "R", task, "--epoch", str(epoch))

    def test_lost_turn_reply_takeover_reconciles_same_thread_and_turn(self):
        attempt = self.claim("A")
        thread_operation = f"codex-thread:{attempt['id']}"
        turn_operation = f"codex-turn:{attempt['operation_id']}"

        def verify_durable_binding(thread_id):
            thread_record = self.store.operation("R", thread_operation)
            self.assertEqual(thread_record["receipt"]["thread_id"], thread_id)
            self.assertEqual(self.store.operation("R", turn_operation)["status"], "intent")

        self.host.on_turn_start = verify_durable_binding
        self.host.drop_turn_reply = "after"
        with self.assertRaises(LoopError):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "1")

        thread_record = self.store.operation("R", thread_operation)
        turn_record = self.store.operation("R", turn_operation)
        self.assertEqual(thread_record["receipt"]["thread_id"], "thread-1")
        self.assertEqual(turn_record["status"], "intent")
        self.assertEqual(self.store.attempt(attempt["id"])["state"], "reserved")
        self.assertEqual(len(self.host.start_calls), 1)
        self.assertEqual(self.host.start_calls[0][1].splitlines()[0], f"RTL dispatch: {attempt['operation_id']}")

        successor = Store(self.store.data_root, reserve=False, reservation_root=self.store.reservation_root)
        self.assertEqual(successor.take_over("R", 1)["epoch"], 2)
        with self.assertRaisesRegex(LoopError, "stale controller epoch"):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "1", store=successor)
        with self.assertRaisesRegex(LoopError, "Codex"):
            self.invoke("start", "R", attempt["id"], "--epoch", "2", "--worker", "manual", store=successor)
        self.assertEqual(successor.attempt(attempt["id"])["state"], "reserved")

        observed = self.invoke("codex-start", "R", attempt["id"], "--epoch", "2", store=successor)
        self.assertEqual(observed["attempt_id"], attempt["id"])
        self.assertEqual(observed["thread_id"], "thread-1")
        self.assertEqual(observed["turn_id"], "turn-1")
        registered = successor.attempt(attempt["id"])
        self.assertEqual(registered["state"], "running")
        self.assertEqual(registered["worker"], observed["worker"])
        dispatch_record = successor.operation("R", attempt["operation_id"])
        self.assertEqual(dispatch_record["receipt"]["host_ref"], observed["host_ref"])
        self.assertEqual(
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "2", store=successor)["turn_id"],
            "turn-1",
        )
        self.assertEqual(len(self.host.threads), 1)
        self.assertEqual(len(self.host.start_calls), 1)

        prior = successor.path.read_bytes()
        queried = self.invoke("codex-status", "R", attempt["id"], store=successor)
        self.assertEqual((queried["thread_id"], queried["turn_id"]), ("thread-1", "turn-1"))
        self.assertEqual(successor.path.read_bytes(), prior)

    def test_unresolved_dispatch_can_be_permanently_isolated_and_replaced(self):
        attempt = self.claim("A")
        workspace = Path(attempt["workspace"])
        (workspace / "A.html").write_text("old writes", encoding="utf-8")
        self.host.drop_turn_reply = "before"
        with self.assertRaisesRegex(LoopError, "simulated disconnect"):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "1")
        self.store.take_over("R", 1)
        args = ("fence", "R", attempt["id"], "--isolate-codex", "检查原轮次后隔离", "--epoch", "2")
        with self.assertRaisesRegex(LoopError, "宿主引用"):
            self.invoke(*args, "--host-ref", "another-host")
        self.assertTrue(workspace.exists())
        with patch.object(codex_host, "finish_operation", side_effect=LoopError("injected receipt interruption")):
            with self.assertRaisesRegex(LoopError, "injected receipt interruption"):
                self.invoke(*args)
        quarantine = workspace.parent.parent / "isolated-workspaces" / attempt["id"]
        self.assertFalse(workspace.exists())
        self.assertEqual((quarantine / "A.html").read_text(encoding="utf-8"), "old writes")
        with self.assertRaisesRegex(LoopError, "隔离"):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "2")
        fenced = self.invoke(*args)
        self.assertEqual(fenced["state"], "fenced")
        self.assertEqual(self.invoke(*args)["state"], "fenced")
        closed = self.store.operation("R", f"codex-turn:{attempt['operation_id']}")
        self.assertEqual(closed["status"], "superseded")
        self.assertEqual(closed["receipt"]["isolation_operation"], f"codex-isolation:{attempt['id']}")
        replacement = self.claim("A", epoch=2)
        self.assertNotEqual(replacement["id"], attempt["id"])
        self.assertFalse((Path(replacement["workspace"]) / "A.html").exists())

    def test_interruption_preserves_slot_until_pause_and_resume_reuses_thread(self):
        a = self.claim("A")
        self.claim("B")
        first = self.invoke("codex-start", "R", a["id"], "--epoch", "1")
        self.invoke("codex-interrupt", "R", a["id"], "--epoch", "1")
        observed = self.invoke("codex-status", "R", a["id"])
        self.assertEqual(observed["turn_status"], "interrupted")
        self.assertEqual(self.host.interrupt_calls, [(first["thread_id"], first["turn_id"])])
        self.assertEqual(self.store.attempt(a["id"])["state"], "running")
        with self.assertRaisesRegex(LoopError, "both execution slots"):
            self.claim("C")

        self.invoke("pause", "R", a["id"], "--epoch", "1", "--stopped", "--host-ref", first["host_ref"])
        resumed = self.claim("A")
        self.assertEqual(resumed["id"], a["id"])
        self.assertNotEqual(resumed["operation_id"], a["operation_id"])
        next_turn = self.invoke("codex-start", "R", resumed["id"], "--epoch", "1")
        self.assertEqual(next_turn["thread_id"], first["thread_id"])
        self.assertNotEqual(next_turn["turn_id"], first["turn_id"])
        self.assertEqual(next_turn["host_ref"], first["host_ref"])

        self.invoke("codex-interrupt", "R", a["id"], "--epoch", "1")
        self.invoke("fence", "R", a["id"], "--epoch", "1", "--stopped", "--host-ref", first["host_ref"])
        replacement = self.claim("A")
        self.assertNotEqual(replacement["id"], a["id"])
        next_thread = self.invoke("codex-start", "R", replacement["id"], "--epoch", "1")
        self.assertNotEqual(next_thread["thread_id"], first["thread_id"])
        self.assertEqual(len(self.host.threads), 2)
        self.assertEqual(len(self.host.start_calls), 3)

    def test_unobserved_turn_stays_unresolved_and_is_never_sent_twice(self):
        attempt = self.claim("A")
        self.host.drop_turn_reply = "before"
        with self.assertRaises(LoopError):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "1")

        thread = self.host.threads["thread-1"]
        thread["turns"].append({
            "id": "unrelated-turn",
            "status": "inProgress",
            "items": [{"type": "userMessage", "id": "other", "content": [
                {"type": "text", "text": "RTL dispatch: another-operation"},
            ]}],
        })
        self.store.take_over("R", 1)
        queried = self.invoke("codex-status", "R", attempt["id"])
        self.assertIsNone(queried["turn_id"])
        self.assertEqual(queried["turn_status"], "unknown")
        with patch.object(codex_host.time, "sleep"), self.assertRaises(LoopError):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "2")
        with self.assertRaises(LoopError):
            self.invoke("codex-interrupt", "R", attempt["id"], "--epoch", "2")
        self.assertEqual(len(self.host.threads), 1)
        self.assertEqual(len(self.host.start_calls), 1)
        self.assertEqual(self.host.interrupt_calls, [])
        self.assertEqual(self.store.attempt(attempt["id"])["state"], "reserved")

    def test_start_sends_bound_contract_and_status_returns_final_response(self):
        for task in ("provider", "consumer"):
            self.store.add_task("R", 1, {
                "id": task, "paths": [f"{task}.html"], "cases": ["TC-CATALOG"], "edges": ["catalog"],
            })
        spec = {"behavior": "Use the exact confirmed catalog behavior", "cases": ["TC-CATALOG"]}
        contracts.propose(self.store, "R", 1, "catalog", "v1", "provider", "consumer", spec)
        for task in ("provider", "consumer"):
            contracts.confirm(self.store, "R", 1, "catalog", "v1", task)
        contracts.activate(self.store, "R", 1, "catalog", "v1")
        attempt = self.claim("consumer")
        launched = self.invoke("codex-start", "R", attempt["id"], "--epoch", "1")
        prompt = self.host.start_calls[-1][1]
        self.assertIn(spec["behavior"], prompt)
        self.assertIn('"version":"v1"', prompt)
        self.assertIsNone(launched["final_response"])
        turn = self.host.threads[launched["thread_id"]]["turns"][0]
        turn["status"] = "completed"
        turn["items"].append({"type": "agentMessage", "phase": "final_answer", "text": "Scoped result"})
        observed = self.invoke("codex-status", "R", attempt["id"])
        self.assertEqual(observed["final_response"], "Scoped result")

    def test_unobserved_thread_creation_is_never_repeated_for_the_attempt(self):
        attempt = self.claim("A")
        self.host.drop_thread_reply = True
        with self.assertRaises(LoopError):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "1")
        self.store.take_over("R", 1)
        with self.assertRaises(LoopError):
            self.invoke("codex-start", "R", attempt["id"], "--epoch", "2")
        self.assertEqual(len(self.host.threads), 1)
        self.assertEqual(self.host.start_calls, [])
        self.assertEqual(self.store.operation("R", f"codex-thread:{attempt['id']}")["status"], "intent")
        self.assertEqual(self.store.attempt(attempt["id"])["state"], "reserved")
        self.host.threads["unrelated"] = {
            "id": "unrelated", "cwd": str(self.root), "status": {"type": "idle"}, "turns": [],
        }
        with self.assertRaisesRegex(LoopError, "工作区"):
            self.invoke("codex-attach", "R", attempt["id"], "unrelated", "--epoch", "2")
        self.host.threads["thread-1"]["status"] = {"type": "active"}
        with self.assertRaisesRegex(LoopError, "空闲"):
            self.invoke("codex-attach", "R", attempt["id"], "thread-1", "--epoch", "2")
        self.host.threads["thread-1"]["status"] = {"type": "idle"}
        self.host.threads["thread-1"]["ephemeral"] = True
        with self.assertRaisesRegex(LoopError, "持久"):
            self.invoke("codex-attach", "R", attempt["id"], "thread-1", "--epoch", "2")
        self.host.threads["thread-1"]["ephemeral"] = False
        recovered = self.invoke("codex-attach", "R", attempt["id"], "thread-1", "--epoch", "2")
        self.assertEqual(recovered["thread_id"], "thread-1")
        self.assertEqual(recovered["turn_status"], "not-started")
        launched = self.invoke("codex-start", "R", attempt["id"], "--epoch", "2")
        self.assertEqual(launched["thread_id"], "thread-1")
        self.assertEqual(len(self.host.threads), 2)
        self.assertEqual(len(self.host.start_calls), 1)

    def test_status_distinguishes_new_dispatch_from_previous_result(self):
        attempt = self.claim("A")
        first = self.invoke("codex-start", "R", attempt["id"], "--epoch", "1")
        self.host.history_pending = True
        pending = self.invoke("codex-status", "R", attempt["id"])
        self.assertEqual((pending["state"], pending["turn_status"]), ("history-pending", "unknown"))
        self.host.history_pending = False
        thread = self.host.threads[first["thread_id"]]
        thread["turns"][0]["status"] = "completed"
        thread["turns"][0]["items"].append({"type": "agentMessage", "phase": "final_answer", "text": "Prior result"})
        thread["status"] = {"type": "idle"}
        self.invoke("pause", "R", attempt["id"], "--epoch", "1", "--stopped")
        resumed = self.claim("A")
        current = self.invoke("codex-status", "R", attempt["id"])
        self.assertEqual(current["dispatch_id"], resumed["operation_id"])
        self.assertEqual((current["attempt_state"], current["turn_status"]), ("reserved", "not-started"))
        self.assertIsNone(current["final_response"])
        self.assertEqual(current["previous"]["dispatch_id"], first["dispatch_id"])
        self.assertEqual(current["previous"]["turn_status"], "completed")
        self.assertEqual(current["previous"]["final_response"], "Prior result")


if __name__ == "__main__":
    unittest.main()
