"""Verify exact local Codex tool completion before reusing an RTL worktree."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .state import LoopError


_TERMINAL_TOOL_STATES = {"completed", "failed", "declined", "interrupted", "cancelled"}
_SAFE_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_RUNNING_CELL = re.compile(r"^Script running with cell ID ([A-Za-z0-9:._-]+)", re.MULTILINE)
_RUNNING_SESSION = re.compile(r"^Process running with session ID ([0-9]+)", re.MULTILINE)


def _unproved(detail):
    return LoopError(f"无法确认 Codex 工具已停止：{detail}。请等待后重试，或用 fence --isolate-codex 隔离旧工作区")


def _rollout_path(session_root, value):
    if not isinstance(value, (str, Path)):
        raise _unproved("宿主未提供本机执行记录")
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(session_root):
        raise _unproved("执行记录不在本机 Codex 会话目录")
    if path.suffix != ".jsonl" or not path.is_file():
        raise _unproved("执行记录尚未落盘")
    return path


def _objects(output, depth=0):
    if depth > 8:
        raise _unproved("工具回执层级异常")
    if isinstance(output, str):
        stripped = output.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                pass
            else:
                yield from _objects(parsed, depth + 1)
                return
        yield output
    elif isinstance(output, list):
        for value in output:
            yield from _objects(value, depth + 1)
    elif isinstance(output, dict):
        if output.get("type") in {"input_text", "text"}:
            yield from _objects(output.get("text"), depth + 1)
        elif output.get("status") == "fulfilled" and "value" in output:
            yield from _objects(output["value"], depth + 1)
        elif "chunk_id" in output or "session_id" in output:
            yield output


def _read_tool_result(call, output, sessions, cells):
    name = call.get("name", "").split(".")[-1]
    arguments = call.get("arguments")
    if name == "wait" and isinstance(arguments, str):
        try:
            args = json.loads(arguments)
        except ValueError:
            args = None
        if isinstance(args, dict) and isinstance(args.get("cell_id"), str):
            cells.discard(args["cell_id"])
    for value in _objects(output):
        if isinstance(value, str):
            sessions.update(_RUNNING_SESSION.findall(value))
            cells.update(_RUNNING_CELL.findall(value))
        elif value.get("session_id") is not None and value.get("exit_code") is None:
            session = value["session_id"]
            if not isinstance(session, (str, int)) or not str(session).isdigit():
                raise _unproved("工具会话编号无法核对")
            sessions.add(str(session))


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            return int(parsed.timestamp())
    except (TypeError, ValueError):
        pass
    raise _unproved("工具或子 Agent 的事件时间无法核对")


def _read_turn_tools(path, thread_id, turn_id, workspace, terminal):
    calls, children = {}, {}
    spawn_requests, spawn_windows = {}, {}
    sessions, finished_sessions, cells, active_items, child_turns = set(), set(), set(), set(), set()
    started = ended = False
    current_turn = None
    try:
        with path.open(encoding="utf-8") as stream:
            meta = json.loads(next(stream, "{}"))
            identity = meta.get("payload", {}) if isinstance(meta, dict) else {}
            if meta.get("type") != "session_meta" or identity.get("id") != thread_id or identity.get("cwd") != workspace:
                raise _unproved("执行记录身份或工作区不符")
            for line in stream:
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
                    raise _unproved("执行记录格式不符")
                payload = row["payload"]
                kind = payload.get("type")
                if row.get("type") == "event_msg" and kind == "task_started":
                    current_turn = payload.get("turn_id")
                    if current_turn == turn_id:
                        started = True
                attribution = payload.get("internal_chat_message_metadata_passthrough")
                explicit = payload.get("turn_id") or (attribution or {}).get("turn_id")
                if (explicit or current_turn) != turn_id:
                    continue
                if row.get("type") == "event_msg" and kind in {"task_complete", "turn_aborted"}:
                    if terminal == "completed" and kind != "task_complete":
                        raise _unproved("已完成轮次与落盘终态不符")
                    ended = True
                if row.get("type") == "response_item" and kind in {"function_call", "custom_tool_call"}:
                    call_id = payload.get("call_id")
                    if not isinstance(call_id, str) or not call_id or call_id in calls:
                        raise _unproved("工具调用编号无法核对")
                    calls[call_id] = payload
                    if payload.get("name", "").split(".")[-1] == "spawn_agent":
                        spawn_requests[call_id] = _timestamp(row.get("timestamp"))
                if row.get("type") == "response_item" and kind in {"function_call_output", "custom_tool_call_output"}:
                    call = calls.pop(payload.get("call_id"), None)
                    if call:
                        _read_tool_result(call, payload.get("output"), sessions, cells)
                if row.get("type") != "event_msg" or kind not in {"item_started", "item_completed"}:
                    continue
                item = payload.get("item", {})
                item_type = item.get("type")
                if item_type == "SubAgentActivity":
                    child_id = item.get("agent_thread_id")
                    if not isinstance(child_id, str) or not _SAFE_THREAD_ID.fullmatch(child_id):
                        raise _unproved("子 Agent 身份无法核对")
                    if child_id == identity.get("parent_thread_id"):
                        continue
                    status = item.get("kind")
                    if status == "completed":
                        child_turn = str(item.get("id", "")).removeprefix("subagent-completed-")
                        if not _SAFE_THREAD_ID.fullmatch(child_turn) or child_turn == item.get("id"):
                            raise _unproved("子 Agent 缺少可核对的完成轮次")
                        child_turns.add((child_id, child_turn))
                        children.pop(child_id, None)
                        if child_id in spawn_windows:
                            begin, _ = spawn_windows[child_id]
                            spawn_windows[child_id] = (begin, _timestamp(row.get("timestamp")))
                    else:
                        children[child_id] = status
                        call_id = item.get("id")
                        if status == "started" and call_id in spawn_requests:
                            spawn_windows[child_id] = (spawn_requests[call_id], _timestamp(row.get("timestamp")))
                if item_type in {"CommandExecution", "FileChange", "McpToolCall", "DynamicToolCall"}:
                    item_id = item.get("id")
                    if not isinstance(item_id, str) or not item_id:
                        raise _unproved("工具记录缺少实际项目编号")
                    if kind == "item_started" or item.get("status") not in _TERMINAL_TOOL_STATES:
                        active_items.add(item_id)
                    else:
                        active_items.discard(item_id)
                        if item_type == "CommandExecution" and item.get("process_id") is not None:
                            finished_sessions.add(str(item["process_id"]))
    except (OSError, UnicodeError, ValueError, AttributeError, TypeError) as exc:
        raise _unproved("无法完整读取本机工具记录") from exc
    if not started or not ended:
        raise _unproved("缺少该轮次的持久终态")
    if calls or cells or active_items or children or sessions - finished_sessions:
        raise _unproved("该轮次仍有未结束的工具会话、调用或子 Agent")
    return {"closed_sessions": finished_sessions, "child_turns": child_turns, "spawn_windows": spawn_windows}


def _worktree_pids(workspace):
    lsof = shutil.which("lsof", path=os.defpath + ":/usr/sbin:/sbin")
    if not lsof:
        raise _unproved("本机缺少进程工作目录核验工具 lsof")
    try:
        scan = subprocess.run(
            [lsof, "-a", "-u", str(os.getuid()), "-d", "cwd", "-Fpn"],
            cwd=Path(workspace).parent, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _unproved("本机进程工作目录无法读取") from exc
    if scan.returncode not in {0, 1} or scan.stderr.strip():
        raise _unproved("本机进程检查未完整返回")
    if scan.returncode == 1 and scan.stdout.strip():
        raise _unproved("本机进程检查结果不一致")
    pids = set()
    current_pid = None
    for line in scan.stdout.splitlines():
        if line.startswith("p"):
            current_pid = line[1:]
        elif line.startswith("n/") and current_pid != str(os.getpid()):
            cwd = Path(line[1:]).resolve()
            if current_pid and current_pid.isdigit() and cwd.is_relative_to(workspace):
                pids.add(current_pid)
    return pids


def _process_identities(workspace):
    pids = _worktree_pids(workspace)
    if not pids:
        return []
    ps = shutil.which("ps", path=os.defpath)
    if not ps:
        raise _unproved("本机缺少进程身份核验工具 ps")
    environment = os.environ.copy()
    environment["TZ"] = "UTC"
    environment["LC_ALL"] = "C"
    try:
        report = subprocess.run(
            [ps, "-ww", "-o", "pid=,ppid=,lstart=,command=", "-p", ",".join(sorted(pids))],
            cwd=Path(workspace).parent, capture_output=True, text=True, timeout=5,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _unproved("本机进程身份无法读取") from exc
    if report.returncode not in {0, 1} or report.stderr.strip():
        raise _unproved("本机进程身份检查未完整返回")
    processes = []
    for line in report.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=7)
        if len(parts) != 8 or parts[0] not in pids or not parts[1].isdigit() or not parts[7].strip():
            raise _unproved("本机进程身份格式不符")
        pid, parent = parts[:2]
        try:
            started = datetime.strptime(" ".join(parts[2:7]), "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise _unproved("本机进程开始时间无法核对") from exc
        pids.remove(pid)
        processes.append({"pid": pid, "parent": parent, "started": int(started.timestamp()),
                          "command_sha256": hashlib.sha256(parts[7].strip().encode()).hexdigest()})
    # A short-lived process may exit between lsof and ps. Refuse an unclassified
    # PID if it still exists, including a reused PID.
    for pid in pids:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            continue
        except OSError as exc:
            raise _unproved("无法核对未分类的本机进程") from exc
        raise _unproved("有未分类的本机进程使用该工作区")
    return sorted(processes, key=lambda item: item["pid"])


def workspace_helper_baseline(workspace, peer_pid):
    helpers = _process_identities(workspace)
    if any(item["parent"] != str(peer_pid) for item in helpers):
        raise _unproved("派发前已有外部进程使用该工作区")
    return helpers


def _verify_worktree_processes(workspace, helpers, windows):
    # Completed subagents can leave their App Server MCP hosts resident. Only
    # recognize a copy of the pre-dispatch peer's known helper cohort, started
    # during a separately verified child turn. Some MCP hosts start lazily.
    def service(process):
        return process["parent"], process.get("command_sha256")

    cohort = Counter(service(process) for process in helpers if process.get("command_sha256"))
    budgets = [(start, end, cohort.copy()) for start, end in sorted(windows, key=lambda window: window[1])]
    for process in _process_identities(workspace):
        if process in helpers:
            continue
        key = service(process)
        for start, end, allowed in budgets:
            if start <= process["started"] <= end and allowed[key]:
                allowed[key] -= 1
                break
        else:
            raise _unproved("仍有本机进程使用该工作区")


def require_tools_drained(thread, turn, items, helpers=()):
    for item in items:
        if item.get("type") in {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall"}:
            if item.get("status") not in _TERMINAL_TOOL_STATES:
                raise _unproved("宿主仍有在途工具")
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    root = home / "sessions"
    workspace = thread["cwd"]
    path = _rollout_path(root, thread.get("path"))
    proof = _read_turn_tools(path, thread["id"], turn["id"], workspace, turn["status"])
    finished, children = proof["closed_sessions"], proof["child_turns"]
    windows = proof["spawn_windows"]
    checked = set()
    while children:
        child, child_turn = children.pop()
        if (child, child_turn) in checked:
            continue
        checked.add((child, child_turn))
        paths = list(root.glob(f"*/*/*/rollout-*-{child}.jsonl"))
        if len(paths) != 1:
            raise _unproved("子 Agent 执行记录无法唯一核对")
        child_proof = _read_turn_tools(
            _rollout_path(root, paths[0]), child, child_turn, workspace, "completed",
        )
        finished.update(child_proof["closed_sessions"])
        children.update(child_proof["child_turns"])
        windows.update(child_proof["spawn_windows"])
    _verify_worktree_processes(workspace, helpers, windows.values())
    return {"kind": "local-codex-tool-history", "closed_sessions": sorted(finished), "child_turns": len(checked)}
