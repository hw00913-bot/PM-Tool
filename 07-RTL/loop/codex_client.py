"""Unix WebSocket client for a persistent local Codex App Server."""

import base64
import hashlib
import json
import os
from pathlib import Path
import socket as network
import struct
import sys
import time

from .state import LoopError


_MAX_MESSAGE = 16 * 1024 * 1024
_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class CodexProtocolError(LoopError):
    """The Codex App Server failed to handle a request."""


class CodexHistoryPending(CodexProtocolError):
    """The server has not made startup history readable yet."""


def default_socket():
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
    return str((home / "app-server-control" / "app-server-control.sock").resolve())


class CodexClient:
    """Open a WebSocket connection over the persistent local Codex Unix socket."""

    def __init__(self, socket=None, *, timeout=20):
        if socket is not None:
            socket = Path(socket).expanduser()
            if not socket.is_absolute():
                raise CodexProtocolError("Codex App Server socket must be an absolute path")
            socket = str(socket.resolve())
        self.socket = socket
        self.timeout = timeout
        self._connection = None
        self._request_id = 0
        self._buffer = bytearray()

    def __enter__(self):
        address = self.socket or default_socket()
        try:
            self._connection = network.socket(network.AF_UNIX, network.SOCK_STREAM)
            self._connection.settimeout(self.timeout)
            self._connection.connect(address)
        except OSError as exc:
            self.close()
            raise CodexProtocolError(f"Could not connect to Codex App Server at {address}: {exc}") from exc
        try:
            self._upgrade()
            self._request("initialize", {"clientInfo": {"name": "rtl", "title": "RTL", "version": "1"}})
            self._send({"method": "initialized"}, time.monotonic() + self.timeout, "initialized")
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def close(self):
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self._buffer.clear()

    def _io(self, action, deadline, method, *args):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CodexProtocolError(f"Codex App Server timed out during {method}")
        self._connection.settimeout(remaining)
        try:
            return action(*args)
        except TimeoutError as exc:
            raise CodexProtocolError(f"Codex App Server timed out during {method}") from exc
        except OSError as exc:
            raise CodexProtocolError(f"Codex App Server disconnected during {method}") from exc

    def _fill(self, deadline, method):
        data = self._io(self._connection.recv, deadline, method, 65536)
        if not data:
            raise CodexProtocolError(f"Codex App Server closed the connection during {method}")
        self._buffer.extend(data)

    def _upgrade(self):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        expected = base64.b64encode(hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        request = (
            "GET / HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        deadline = time.monotonic() + self.timeout
        self._io(self._connection.sendall, deadline, "WebSocket upgrade", request.encode("ascii"))
        while (end := self._buffer.find(b"\r\n\r\n")) < 0:
            self._fill(deadline, "WebSocket upgrade")
            if len(self._buffer) > 65536:
                raise CodexProtocolError("Codex App Server WebSocket upgrade headers are too large")
        lines = bytes(self._buffer[:end]).decode("iso-8859-1").split("\r\n")
        del self._buffer[:end + 4]
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if not separator:
                raise CodexProtocolError("Codex App Server sent invalid WebSocket upgrade headers")
            headers[name.lower()] = value.strip()
        if (
            lines[0].split()[0:2] != ["HTTP/1.1", "101"]
            or headers.get("upgrade", "").lower() != "websocket"
            or "upgrade" not in {x.strip().lower() for x in headers.get("connection", "").split(",")}
            or headers.get("sec-websocket-accept") != expected
        ):
            raise CodexProtocolError("Codex App Server refused the Unix WebSocket upgrade")

    def _read_exact(self, length, deadline, method):
        while len(self._buffer) < length:
            self._fill(deadline, method)
        data = bytes(self._buffer[:length])
        del self._buffer[:length]
        return data

    def _send_frame(self, opcode, payload, deadline, method):
        length = len(payload)
        if length > _MAX_MESSAGE:
            raise CodexProtocolError(f"Codex App Server request exceeded the size limit during {method}")
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        mask = os.urandom(4)
        encoded = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        self._io(self._connection.sendall, deadline, method, header + mask + encoded)

    def _send(self, message, deadline, method):
        self._send_frame(1, json.dumps(message, ensure_ascii=False).encode("utf-8"), deadline, method)

    def _receive(self, deadline, method):
        fragments = None
        while time.monotonic() < deadline:
            first, second = self._read_exact(2, deadline, method)
            final, opcode, length = bool(first & 0x80), first & 0x0f, second & 0x7f
            if first & 0x70 or second & 0x80:
                raise CodexProtocolError(f"Codex App Server sent an invalid WebSocket frame during {method}")
            if length in (126, 127):
                width = 2 if length == 126 else 8
                length = int.from_bytes(self._read_exact(width, deadline, method), "big")
            if length > _MAX_MESSAGE or (opcode >= 8 and (length > 125 or not final)):
                raise CodexProtocolError(f"Codex App Server sent an oversized or invalid frame during {method}")
            payload = self._read_exact(length, deadline, method)
            if opcode == 8:
                raise CodexProtocolError(f"Codex App Server closed the connection during {method}")
            if opcode == 9:
                self._send_frame(10, payload, deadline, method)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and fragments is None:
                fragments = bytearray(payload)
            elif opcode == 0 and fragments is not None:
                fragments.extend(payload)
            else:
                raise CodexProtocolError(f"Codex App Server sent an unexpected WebSocket frame during {method}")
            if len(fragments) > _MAX_MESSAGE:
                raise CodexProtocolError(f"Codex App Server response exceeded the size limit during {method}")
            if not final:
                continue
            try:
                message = json.loads(fragments)
            except (UnicodeError, ValueError) as exc:
                raise CodexProtocolError(f"Codex App Server sent invalid JSON during {method}") from exc
            if not isinstance(message, dict):
                raise CodexProtocolError(f"Codex App Server sent an invalid message during {method}")
            return message
        raise CodexProtocolError(f"Codex App Server timed out during {method}")

    def _request(self, method, params, *, deadline=None):
        if self._connection is None:
            raise CodexProtocolError("Codex App Server is not connected")
        self._request_id += 1
        request_id = self._request_id
        if deadline is None:
            deadline = time.monotonic() + self.timeout
        self._send({"id": request_id, "method": method, "params": params}, deadline, method)
        while True:
            message = self._receive(deadline, method)
            if "method" in message:
                if "id" in message:
                    # This client has no interactive approval or tool-request handler.
                    self._send({
                        "id": message["id"],
                        "error": {"code": -32601, "message": "RTL does not handle Codex server requests"},
                    }, deadline, method)
                continue
            if message.get("id") != request_id:
                raise CodexProtocolError(f"Codex App Server sent an unexpected response during {method}")
            if "error" in message:
                error = message["error"]
                detail = error.get("message", "") if isinstance(error, dict) else ""
                raise CodexProtocolError(f"Codex App Server {method} failed: {detail}".rstrip(": "))
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexProtocolError(f"Codex App Server returned an invalid result for {method}")
            return result

    @staticmethod
    def _workspace(workspace):
        path = Path(workspace).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise CodexProtocolError("Codex requires an existing absolute RTL workspace")
        return str(path.resolve())

    def start_thread(self, workspace):
        result = self._request("thread/start", {
            "cwd": self._workspace(workspace),
            "ephemeral": False,
            "approvalPolicy": "never",
            "sandbox": "workspace-write",
        })
        thread = result.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise CodexProtocolError("Codex App Server did not return a thread ID")
        return thread["id"]

    def start_turn(self, thread_id, workspace, prompt):
        cwd = self._workspace(workspace)
        result = self._request("turn/start", {
            "threadId": thread_id,
            "cwd": cwd,
            "input": [{"type": "text", "text": prompt}],
            "approvalPolicy": "never",
            "sandboxPolicy": {
                "type": "workspaceWrite", "writableRoots": [cwd], "networkAccess": False,
                "excludeTmpdirEnvVar": True, "excludeSlashTmp": True,
            },
        })
        turn = result.get("turn")
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
            raise CodexProtocolError("Codex App Server did not return a turn ID")
        return turn

    def _history_request(self, method, params, *, deadline):
        delays = iter((0.06, 0.15, 0.35))
        while True:
            try:
                return self._request(method, params, deadline=deadline)
            except CodexProtocolError as exc:
                prefix = f"Codex App Server {method} failed: "
                detail = str(exc).removeprefix(prefix)
                empty_rollout = method == "thread/read" and "rollout at " in detail and detail.endswith(" is empty")
                unready_turns = method == "thread/turns/list" and detail == "list_turns is not supported yet"
                if detail == str(exc) or not (empty_rollout or unready_turns):
                    raise
                delay = next(delays, None)
                if delay is None or time.monotonic() + delay >= deadline:
                    raise CodexHistoryPending(str(exc)) from exc
                time.sleep(delay)

    def _pages(self, method, params, deadline):
        params = dict(params)
        cursor = None
        seen = set()
        while True:
            if cursor is not None:
                params["cursor"] = cursor
            request = self._history_request if method == "thread/turns/list" and cursor is None else self._request
            page = request(method, params, deadline=deadline)
            data = page.get("data")
            if not isinstance(data, list):
                raise CodexProtocolError(f"Codex App Server returned invalid data for {method}")
            yield data
            cursor = page.get("nextCursor")
            if cursor is None:
                return
            if not isinstance(cursor, str) or not cursor or cursor in seen or len(seen) >= 99:
                raise CodexProtocolError(f"Codex App Server returned invalid or excessive pagination for {method}")
            seen.add(cursor)

    def _final_items(self, thread_id, turn_id, deadline):
        params = {"threadId": thread_id, "turnId": turn_id, "limit": 10, "sortDirection": "desc"}
        for data in self._pages("thread/items/list", params, deadline):
            if any(
                not isinstance(entry, dict) or entry.get("turnId") != turn_id
                or not isinstance(entry.get("item"), dict) for entry in data
            ):
                raise CodexProtocolError("Codex App Server returned mismatched turn items")
            for entry in data:
                item = entry["item"]
                if (
                    item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"}
                    and isinstance(item.get("text"), str) and item["text"].strip()
                ):
                    return [item]
        return []

    def workspace_helper_baseline(self, workspace):
        """Record only exact App Server children already present before turn/start."""
        from .codex_tools import workspace_helper_baseline

        try:
            if sys.platform == "darwin":
                # Darwin's LOCAL_PEERPID at SOL_LOCAL identifies the Unix peer.
                (pid,) = struct.unpack("i", self._connection.getsockopt(0, 2, struct.calcsize("i")))
            elif hasattr(network, "SO_PEERCRED"):
                pid, _, _ = struct.unpack("3i", self._connection.getsockopt(
                    network.SOL_SOCKET, network.SO_PEERCRED, struct.calcsize("3i"),
                ))
            else:
                raise OSError("Unix peer process identity is unavailable")
        except (OSError, struct.error) as exc:
            raise CodexProtocolError("无法核对本机 Codex App Server 的进程身份") from exc
        if pid <= 0:
            raise CodexProtocolError("Codex App Server 进程身份无效")
        return workspace_helper_baseline(self._workspace(workspace), pid)

    def require_tools_drained(self, thread, turn, helpers=()):
        """Check all exact-turn tool items and the locally persisted completion evidence."""
        from .codex_tools import require_tools_drained

        params = {"threadId": thread["id"], "turnId": turn["id"], "limit": 50, "sortDirection": "desc"}
        items = []
        for data in self._pages("thread/items/list", params, time.monotonic() + self.timeout):
            if any(
                not isinstance(entry, dict) or entry.get("turnId") != turn["id"]
                or not isinstance(entry.get("item"), dict) for entry in data
            ):
                raise CodexProtocolError("Codex App Server returned mismatched turn items")
            items.extend(entry["item"] for entry in data)
        return require_tools_drained(thread, turn, items, helpers)

    def read_thread(self, thread_id, *, turn_id=None, newest_only=False, include_items=True):
        """Read turns newest first. A known ID needs only its own lifecycle and optional final reply."""
        deadline = time.monotonic() + self.timeout
        result = self._history_request("thread/read", {"threadId": thread_id}, deadline=deadline)
        thread = result.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise CodexProtocolError("Codex App Server returned mismatched thread metadata")
        params = {
            "threadId": thread_id, "limit": 1 if newest_only else 10, "sortDirection": "desc",
            "itemsView": "full" if turn_id is None and include_items else "notLoaded",
        }
        turns = []
        page_read = False
        try:
            for data in self._pages("thread/turns/list", params, deadline):
                page_read = True
                if any(not isinstance(turn, dict) for turn in data):
                    raise CodexProtocolError("Codex App Server returned invalid turn history")
                if turn_id is None:
                    turns.extend(data)
                    if newest_only:
                        break
                    continue
                matches = [turn for turn in data if turn.get("id") == turn_id]
                if len(matches) > 1:
                    raise CodexProtocolError("Codex App Server returned duplicate turn IDs")
                if matches:
                    turn = matches[0]
                    if include_items and turn.get("status") in {"completed", "failed", "interrupted"}:
                        turn = {**turn, "items": self._final_items(thread_id, turn_id, deadline)}
                    turns.append(turn)
                    break
        except CodexProtocolError as exc:
            empty = (
                f"Codex App Server thread/turns/list failed: thread {thread_id} is not materialized yet; "
                "thread/turns/list is unavailable before first user message"
            )
            if page_read or str(exc) != empty:
                raise
        return {**thread, "turns": turns}

    def resume_thread(self, thread_id):
        """Reload a persisted thread so a later turn can continue it."""
        result = self._request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        thread = result.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise CodexProtocolError("Codex App Server resumed a different thread")
        return thread

    def interrupt_turn(self, thread_id, turn_id):
        self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
