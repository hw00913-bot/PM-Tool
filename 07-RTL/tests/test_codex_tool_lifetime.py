import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop.codex_tools import _read_turn_tools, _verify_worktree_processes
from loop.state import LoopError


class CodexToolLifetimeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "rollout-thread.jsonl"
        self.workspace = str(Path(folder.name))
        self.rows = [
            {"type": "session_meta", "payload": {"id": "thread", "cwd": self.workspace}},
            self.event("task_started"),
        ]

    def event(self, kind, **fields):
        return {"type": "event_msg", "payload": {"type": kind, "turn_id": "turn", **fields}}

    def response(self, kind, **fields):
        return {"type": "response_item", "payload": {"type": kind, "call_id": "call-1", **fields}}

    def inspect(self, terminal="completed"):
        self.path.write_text("".join(json.dumps(row) + "\n" for row in self.rows), encoding="utf-8")
        return _read_turn_tools(self.path, "thread", "turn", self.workspace, terminal)

    def test_completed_reply_requires_the_exact_async_session_to_end(self):
        self.rows.extend([
            self.response("custom_tool_call", name="exec"),
            self.response("custom_tool_call_output", output=[
                {"type": "input_text", "text": "Script completed\nOutput:\n"},
                {"type": "input_text", "text": '{"chunk_id":"chunk","session_id":24341}'},
            ]),
            self.event("task_complete"),
        ])
        with self.assertRaisesRegex(LoopError, "未结束的工具会话"):
            self.inspect()
        wrong = self.event("item_completed", item={
            "type": "CommandExecution", "id": "cmd", "process_id": "24342", "status": "completed",
        })
        self.rows.append(wrong)
        with self.assertRaisesRegex(LoopError, "未结束的工具会话"):
            self.inspect()
        self.rows.append(self.event("item_completed", item={
            "type": "CommandExecution", "id": "cmd2", "process_id": "24341", "status": "completed",
        }))
        self.assertEqual(self.inspect()["closed_sessions"], {"24341", "24342"})

    def test_inferred_crash_interruption_lacks_persisted_exit_proof(self):
        self.rows.append(self.response("custom_tool_call", name="exec"))
        with self.assertRaisesRegex(LoopError, "持久终态"):
            self.inspect(terminal="interrupted")
        self.rows.append(self.event("turn_aborted"))
        with self.assertRaisesRegex(LoopError, "未结束的工具会话"):
            self.inspect(terminal="interrupted")

    def test_subagent_service_copies_are_bounded_to_the_known_peer_cohort(self):
        original = {"pid": "101", "parent": "100", "started": 1, "command_sha256": "helper"}
        child = {**original, "pid": "102", "started": 11}
        with patch("loop.codex_tools._process_identities", return_value=[original, child]):
            _verify_worktree_processes(self.workspace, [original], [(10, 12)])
        for unexpected in (
            {**child, "started": 13},
            {**child, "parent": "999"},
            {**child, "command_sha256": "shell"},
            {**child, "pid": "103"},
        ):
            with patch("loop.codex_tools._process_identities", return_value=[original, child, unexpected]):
                with self.assertRaisesRegex(LoopError, "仍有本机进程"):
                    _verify_worktree_processes(self.workspace, [original], [(10, 12)])
