from pathlib import Path
import unittest
from unittest.mock import patch

import test_codex_host

from loop import artifacts, dispatch
from loop.state import LoopError


class CodexSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_codex_host.CodexHostTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.claim = self.fixture.claim
        self.invoke = self.fixture.invoke
        self.attempt = self.claim("A")
        self.attempt_id = self.attempt["id"]
        self.workspace = Path(self.attempt["workspace"])
        self.execution = self.invoke("codex-start", "R", self.attempt_id, "--epoch", "1")
        self.worker = self.execution["worker"]
        self.thread = self.fixture.host.threads[self.execution["thread_id"]]

    def submit(self):
        return self.invoke("submit", "R", self.attempt_id, "--epoch", "1", "--actor", self.worker)

    def complete(self):
        self.thread["turns"][0]["status"] = "completed"
        self.thread["status"] = {"type": "idle"}

    def test_live_turn_cannot_submit_pause_fence_or_release_slot(self):
        self.claim("B")
        (self.workspace / "A.html").write_text("<h1>A</h1>")
        old_head = artifacts.rev(self.workspace)
        for operation in (
            self.submit,
            lambda: self.invoke("pause", "R", self.attempt_id, "--epoch", "1", "--stopped"),
            lambda: self.invoke("fence", "R", self.attempt_id, "--epoch", "1", "--stopped"),
            lambda: self.invoke("supersede", "R", "--epoch", "1", "--stopped", "--reason", "test", "--approval", "test"),
        ):
            with self.assertRaises(LoopError):
                operation()
        self.assertEqual(artifacts.rev(self.workspace), old_head)
        with self.assertRaisesRegex(LoopError, "both execution slots"):
            self.claim("C")
        interrupted = self.invoke("codex-interrupt", "R", self.attempt_id, "--epoch", "1")
        self.assertEqual(interrupted["turn_status"], "interrupted")
        with self.assertRaises(LoopError):
            self.submit()
        self.invoke("pause", "R", self.attempt_id, "--epoch", "1", "--stopped")
        self.assertEqual(self.claim("C")["state"], "reserved")

    def test_completed_turn_captures_worktree_and_retry_reuses_commit(self):
        file = self.workspace / "A.html"
        file.write_text("<h1>Partially staged</h1>")
        artifacts.git(self.workspace, "add", "--", "A.html")
        file.write_text("<h1>Final</h1>")
        self.complete()
        item = self.submit()
        result = item["result"]
        self.assertEqual(item["state"], "submitted")
        self.assertEqual(result["paths"], ["A.html"])
        self.assertEqual(result["codex_execution"]["turn_id"], self.execution["turn_id"])
        self.assertEqual(result["codex_execution"]["turn_status"], "completed")
        self.assertEqual(artifacts.git(self.workspace, "show", f"{result['commit']}:A.html"), b"<h1>Final</h1>")
        self.assertEqual(artifacts.git(self.workspace, "status", "--porcelain"), b"")
        self.assertEqual(self.submit()["result"], result)

    def test_terminal_turn_with_a_live_tool_cannot_commit_or_release_slot(self):
        self.claim("B")
        file = self.workspace / "A.html"
        file.write_text("intermediate")
        self.complete()
        self.fixture.host.tools_drained = False
        head = artifacts.rev(self.workspace)
        with self.assertRaisesRegex(LoopError, "工具正在写入"):
            self.submit()
        self.assertEqual(artifacts.rev(self.workspace), head)
        with self.assertRaisesRegex(LoopError, "both execution slots"):
            self.claim("C")
        file.write_text("final")
        self.fixture.host.tools_drained = True
        commit = self.submit()["result"]["commit"]
        self.assertEqual(artifacts.git(self.workspace, "show", f"{commit}:A.html"), b"final")
        self.assertEqual(self.claim("C")["state"], "reserved")

    def test_completed_turn_rejects_unscoped_changes_before_staging(self):
        (self.workspace / "A.html").write_text("<h1>A</h1>")
        (self.workspace / "B.html").write_text("<h1>B</h1>")
        self.complete()
        head = artifacts.rev(self.workspace)
        with self.assertRaisesRegex(ValueError, "授权范围.*B.html"):
            self.submit()
        self.assertEqual(artifacts.rev(self.workspace), head)
        self.assertEqual(artifacts.git(self.workspace, "diff", "--cached", "--name-only"), b"")
        self.assertEqual(self.fixture.store.attempt(self.attempt_id)["state"], "running")

    def test_retry_recovers_commit_written_before_ledger_receipt(self):
        (self.workspace / "A.html").write_text("<h1>A</h1>")
        self.complete()
        base = artifacts.rev(self.workspace)
        with patch.object(dispatch, "_validate_result", side_effect=LoopError("injected ledger interruption")):
            with self.assertRaisesRegex(LoopError, "injected ledger interruption"):
                self.submit()
        commit = artifacts.rev(self.workspace)
        self.assertNotEqual(commit, base)
        self.assertEqual(self.fixture.store.attempt(self.attempt_id)["state"], "running")
        self.assertEqual(self.submit()["result"]["commit"], commit)

    def test_completed_turn_rejects_symlink_without_committing(self):
        (self.workspace / "A.html").symlink_to(self.workspace / "index.html")
        self.complete()
        head = artifacts.rev(self.workspace)
        with self.assertRaisesRegex(ValueError, "不可交付"):
            self.submit()
        self.assertEqual(artifacts.rev(self.workspace), head)
