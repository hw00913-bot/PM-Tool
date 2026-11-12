from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop import codex_client
from loop.codex_client import CodexClient, CodexHistoryPending, CodexProtocolError


PROXY = r"""
import base64
import hashlib
import json
import socket
import sys

workspace, mode, fd = sys.argv[1:4]
peer = socket.socket(fileno=int(fd))
reader = peer.makefile("rb")
assert reader.readline() == b"GET / HTTP/1.1\r\n"
headers = {}
for line in iter(reader.readline, b"\r\n"):
    name, value = line.decode("ascii").split(":", 1)
    headers[name.lower()] = value.strip()
guid = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + guid).encode()).digest())
peer.sendall(b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
             b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")

def receive_frame():
    first, second = reader.read(2)
    assert first & 0x80 and second & 0x80
    length = second & 0x7f
    if length in (126, 127):
        length = int.from_bytes(reader.read(2 if length == 126 else 8), "big")
    mask = reader.read(4)
    payload = bytes(value ^ mask[i % 4] for i, value in enumerate(reader.read(length)))
    return first & 0x0f, payload

def receive():
    opcode, payload = receive_frame()
    assert opcode == 1
    return json.loads(payload)

def send_frame(opcode, payload, final=True):
    length = len(payload)
    head = bytes(((0x80 if final else 0) | opcode,))
    if length < 126:
        head += bytes((length,))
    else:
        head += bytes((126,)) + length.to_bytes(2, "big")
    peer.sendall(head + payload)

def send(message):
    send_frame(1, json.dumps(message).encode())

def request(method):
    message = receive()
    assert message["method"] == method, message
    return message

def reply(message, result):
    send({"id": message["id"], "result": result})

initial = request("initialize")
assert initial["params"]["clientInfo"]["name"] == "rtl"
response = json.dumps({"id": initial["id"], "result": {}}).encode()
send_frame(1, response[:10], final=False)
send_frame(9, b"ping")
assert receive_frame() == (10, b"ping")
send_frame(0, response[10:])
assert receive() == {"method": "initialized"}
thread = request("thread/start")
assert thread["params"] == {
    "cwd": workspace, "ephemeral": False, "approvalPolicy": "never", "sandbox": "workspace-write",
}
reply(thread, {"thread": {"id": "thread-1"}})
if mode in ("rollout-once", "rollout-pending"):
    for _ in range(4 if mode == "rollout-pending" else 1):
        meta = request("thread/read")
        send({"id": meta["id"], "error": {"code": -32602, "message": "rollout at /tmp/thread.jsonl is empty"}})
    if mode == "rollout-pending":
        sys.exit(0)
    meta = request("thread/read")
    reply(meta, {"thread": {"id": "thread-1", "status": {"type": "active"}}})
    page = request("thread/turns/list")
    assert page["params"]["itemsView"] == "notLoaded"
    reply(page, {"data": [{"id": "turn-1", "status": "inProgress"}], "nextCursor": "older"})
    sys.exit(0)

if mode in ("turns-once", "turns-pending"):
    meta = request("thread/read")
    reply(meta, {"thread": {"id": "thread-1", "status": {"type": "active"}}})
    for _ in range(4 if mode == "turns-pending" else 1):
        page = request("thread/turns/list")
        assert "cursor" not in page["params"]
        send({"id": page["id"], "error": {"code": -32602, "message": "list_turns is not supported yet"}})
    if mode == "turns-once":
        page = request("thread/turns/list")
        reply(page, {"data": [{"id": "turn-1", "status": "inProgress"}], "nextCursor": "older"})
    sys.exit(0)

if mode in ("empty", "unavailable"):
    meta = request("thread/read")
    reply(meta, {"thread": {"id": "thread-1", "status": {"type": "idle"}}})
    page = request("thread/turns/list")
    message = "thread thread-1 is not materialized yet; thread/turns/list is unavailable before first user message"
    if mode == "unavailable":
        message = "thread history unavailable"
    send({"id": page["id"], "error": {"code": -32602, "message": message}})
    sys.exit(0)

turn = request("turn/start")
assert turn["params"]["threadId"] == "thread-1"
assert turn["params"]["cwd"] == workspace
assert turn["params"]["approvalPolicy"] == "never"
assert turn["params"]["sandboxPolicy"] == {
    "type": "workspaceWrite", "writableRoots": [workspace], "networkAccess": False,
    "excludeTmpdirEnvVar": True, "excludeSlashTmp": True,
}
assert turn["params"]["input"] == [{"type": "text", "text": "rtl marker"}]
send({"id": turn["id"], "method": "item/commandExecution/requestApproval", "params": {}})
denial = receive()
assert denial["id"] == turn["id"] and "error" in denial and "result" not in denial
send({"method": "turn/started", "params": {"turn": {"id": "turn-1"}}})
reply(turn, {"turn": {"id": "turn-1", "status": "inProgress", "items": []}})

meta = request("thread/read")
assert meta["params"] == {"threadId": "thread-1"}
reply(meta, {"thread": {"id": "thread-1", "status": {"type": "active"}}})
first = request("thread/turns/list")
if mode in ("target-active", "target-completed", "newest"):
    assert first["params"]["itemsView"] == "notLoaded" and "cursor" not in first["params"]
    if mode == "newest":
        assert first["params"]["limit"] == 1
    status = "completed" if mode == "target-completed" else "inProgress"
    reply(first, {"data": [{"id": "turn-1", "status": status, "items": []}], "nextCursor": "page-2"})
    if mode == "target-completed":
        items = request("thread/items/list")
        assert items["params"] == {"threadId": "thread-1", "turnId": "turn-1", "limit": 10, "sortDirection": "desc"}
        reply(items, {"data": [
            {"turnId": "turn-1", "item": {"type": "agentMessage", "phase": "commentary", "text": "progress"}},
            {"turnId": "turn-1", "item": {"type": "agentMessage", "phase": "final_answer", "text": "done"}},
        ], "nextCursor": "older-items"})
    if mode == "target-active":
        interrupt = request("turn/interrupt")
        assert interrupt["params"] == {"threadId": "thread-1", "turnId": "turn-1"}
        reply(interrupt, {})
    sys.exit(0)

assert first["params"]["itemsView"] == "full" and "cursor" not in first["params"]
reply(first, {"data": [{"id": "turn-1", "status": "inProgress", "items": [
    {"type": "userMessage", "content": [{"type": "text", "text": "rtl marker"}]},
]}], "nextCursor": "page-2"})
second = request("thread/turns/list")
assert second["params"]["cursor"] == "page-2"
reply(second, {"data": [{"id": "older-turn", "status": "completed", "items": []}], "nextCursor": None})

interrupt = request("turn/interrupt")
assert interrupt["params"] == {"threadId": "thread-1", "turnId": "turn-1"}
reply(interrupt, {})

if mode == "timeout":
    request("thread/resume")
    reader.read()
"""


class CodexClientTests(unittest.TestCase):
    def mock_socket(self, workspace, mode="normal"):
        client, peer = socket.socketpair()
        self.addCleanup(client.close)
        with peer:
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", PROXY, str(workspace), mode, str(peer.fileno())],
                pass_fds=(peer.fileno(),), stderr=subprocess.PIPE,
            )

        def close_peer():
            client.close()
            with process:
                _, errors = process.communicate(timeout=2)
            self.assertEqual(process.returncode, 0, errors.decode())

        self.addCleanup(close_peer)

        test = self

        class ConnectedSocket:
            def connect(self, address):
                test.assertEqual(address, str(workspace / "codex.sock"))

            def __getattr__(self, name):
                return getattr(client, name)

        return patch.object(codex_client.network, "socket", return_value=ConnectedSocket())

    def test_transport_scopes_turns_denies_approvals_and_recovers_paginated_history(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace):
                with CodexClient(workspace / "codex.sock") as client:
                    thread_id = client.start_thread(workspace)
                    turn = client.start_turn(thread_id, workspace, "rtl marker")
                    thread = client.read_thread(thread_id)
                    self.assertEqual(turn["id"], "turn-1")
                    self.assertEqual(thread["status"]["type"], "active")
                    self.assertEqual([item["id"] for item in thread["turns"]], ["turn-1", "older-turn"])
                    self.assertEqual(thread["turns"][0]["items"][0]["content"][0]["text"], "rtl marker")
                    client.interrupt_turn(thread_id, turn["id"])

    def test_unanswered_request_has_a_deadline_and_closes_connection(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="timeout"):
                with CodexClient(workspace / "codex.sock", timeout=0.25) as client:
                    thread_id = client.start_thread(workspace)
                    turn = client.start_turn(thread_id, workspace, "rtl marker")
                    client.read_thread(thread_id)
                    client.interrupt_turn(thread_id, turn["id"])
                    with self.assertRaisesRegex(CodexProtocolError, "timed out during thread/resume"):
                        client.resume_thread(thread_id)

    def test_unmaterialized_thread_can_be_read_before_first_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="empty"):
                with CodexClient(workspace / "codex.sock") as client:
                    thread = client.read_thread(client.start_thread(workspace))
                    self.assertEqual(thread, {"id": "thread-1", "status": {"type": "idle"}, "turns": []})

    def test_unavailable_history_is_not_reported_as_an_empty_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="unavailable"):
                with CodexClient(workspace / "codex.sock") as client:
                    with self.assertRaisesRegex(CodexProtocolError, "thread history unavailable"):
                        client.read_thread(client.start_thread(workspace))

    def test_known_turn_is_found_and_interrupted_without_reading_old_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="target-active"):
                with CodexClient(workspace / "codex.sock") as client:
                    thread_id = client.start_thread(workspace)
                    turn_id = client.start_turn(thread_id, workspace, "rtl marker")["id"]
                    thread = client.read_thread(thread_id, turn_id=turn_id, include_items=False)
                    self.assertEqual(thread["turns"], [{"id": turn_id, "status": "inProgress", "items": []}])
                    client.interrupt_turn(thread_id, turn_id)

    def test_known_completed_turn_reads_only_its_final_reply(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="target-completed"):
                with CodexClient(workspace / "codex.sock") as client:
                    thread_id = client.start_thread(workspace)
                    turn_id = client.start_turn(thread_id, workspace, "rtl marker")["id"]
                    thread = client.read_thread(thread_id, turn_id=turn_id)
                    self.assertEqual(thread["turns"][0]["items"], [
                        {"type": "agentMessage", "phase": "final_answer", "text": "done"},
                    ])

    def test_latest_turn_check_fetches_one_page_without_item_bodies(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="newest"):
                with CodexClient(workspace / "codex.sock") as client:
                    thread_id = client.start_thread(workspace)
                    client.start_turn(thread_id, workspace, "rtl marker")
                    thread = client.read_thread(thread_id, newest_only=True, include_items=False)
                    self.assertEqual([turn["status"] for turn in thread["turns"]], ["inProgress"])

    def test_empty_rollout_retries_before_reading_turn_history(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="rollout-once"), patch.object(codex_client.time, "sleep"):
                with CodexClient(workspace / "codex.sock") as client:
                    thread = client.read_thread(client.start_thread(workspace), turn_id="turn-1")
                    self.assertEqual(thread["turns"], [{"id": "turn-1", "status": "inProgress"}])

    def test_still_empty_rollout_reports_pending_after_bounded_retries(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder).resolve()
            with self.mock_socket(workspace, mode="rollout-pending"), patch.object(codex_client.time, "sleep") as sleep:
                with CodexClient(workspace / "codex.sock") as client:
                    with self.assertRaisesRegex(CodexHistoryPending, "rollout .* is empty"):
                        client.read_thread(client.start_thread(workspace), turn_id="turn-1")
                self.assertEqual(sleep.call_count, 3)

    def test_first_turn_history_unavailable_is_retried_or_reported_pending(self):
        for mode in ("turns-once", "turns-pending"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                workspace = Path(folder).resolve()
                with self.mock_socket(workspace, mode=mode), patch.object(codex_client.time, "sleep") as sleep:
                    with CodexClient(workspace / "codex.sock") as client:
                        thread_id = client.start_thread(workspace)
                        if mode == "turns-once":
                            thread = client.read_thread(thread_id, turn_id="turn-1")
                            self.assertEqual(thread["turns"], [{"id": "turn-1", "status": "inProgress"}])
                        else:
                            with self.assertRaisesRegex(CodexHistoryPending, "list_turns is not supported yet"):
                                client.read_thread(thread_id, turn_id="turn-1")
                    self.assertEqual(sleep.call_count, 1 if mode == "turns-once" else 3)

    def test_rejects_relative_socket(self):
        with self.assertRaisesRegex(CodexProtocolError, "absolute"):
            CodexClient("relative.sock")


if __name__ == "__main__":
    unittest.main()
