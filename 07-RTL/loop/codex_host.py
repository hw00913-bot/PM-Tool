"""Bind each RTL attempt to a persistent Codex thread and reconcile its turns."""

from pathlib import Path
import time

from . import artifacts, dispatch
from .codex_client import CodexClient, CodexHistoryPending, default_socket
from .skills import read_skill
from .state import (
    LoopError, active_run_pause, begin_operation, finish_operation, get_attempt, get_operation, guard, pack, unpack,
)

TERMINAL = {"completed", "failed", "interrupted"}
TURN_STATES = TERMINAL | {"inProgress"}


def _thread_operation(attempt_id):
    return f"codex-thread:{attempt_id}"


def _turn_operation(dispatch_id):
    return f"codex-turn:{dispatch_id}"


def _isolation_operation(attempt_id):
    return f"codex-isolation:{attempt_id}"


def _socket_path(socket):
    if socket is None:
        return None
    path = Path(socket).expanduser()
    if not path.is_absolute():
        raise LoopError("Codex App Server socket must be an absolute path")
    return str(path.resolve())


def _launch_context(store, db, run_id, epoch, attempt_id):
    run = guard(db, run_id, epoch)
    attempt, _ = dispatch.current_attempt(db, run_id, attempt_id)
    if get_operation(db, run_id, _isolation_operation(attempt_id)):
        raise LoopError("Codex 工作区已进入隔离，须完成 fence 后重新领取")
    if attempt["state"] not in {"reserved", "running"} or not attempt["workspace"]:
        raise LoopError("Codex 需要已领取且已绑定工作区的有效尝试")
    dispatch.require_bound_requirements(db, run, attempt)
    dispatch.ensure_current_inputs(db, run_id, attempt)
    workspace = Path(attempt["workspace"])
    expected = artifacts.run_directory(store, run_id) / "workspaces" / attempt_id
    if workspace != expected or workspace.resolve() != workspace or not workspace.is_dir():
        raise LoopError("Codex 工作区与已登记的隔离尝试不符")
    git_dir = artifacts.git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    if Path(git_dir).resolve() != (artifacts.repo_path(store, run_id) / ".git").resolve():
        raise LoopError("Codex 工作区不属于本轮仓库")
    return run, attempt, workspace


def _host_identity(thread_id):
    return f"codex:{thread_id}"


def _validate_worker(attempt, thread_id=None):
    expected = _host_identity(thread_id) if thread_id is not None else None
    if attempt["worker"] is not None and attempt["worker"] != expected:
        raise LoopError("该尝试已登记其他执行者。请核对原尝试后再派发")


def _thread_id(binding):
    return (binding.get("receipt") or {}).get("thread_id") if binding else None


def _turn_id(turn):
    return (turn.get("receipt") or {}).get("turn_id") if turn else None


def _thread_snapshot(client, thread_id, workspace, **options):
    thread = client.read_thread(thread_id, **options)
    if not isinstance(thread, dict) or thread.get("id") != thread_id:
        raise LoopError("Codex 返回的线程身份不符")
    cwd = thread.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or Path(cwd).resolve() != Path(workspace):
        raise LoopError("Codex 线程的工作区与 RTL 尝试不符")
    if not isinstance(thread.get("turns"), list):
        raise LoopError("Codex 未返回可供对账的执行记录")
    return thread


def _observe_turn(client, binding, operation, *, include_items=False):
    thread_id = _thread_id(binding)
    if operation["payload"]["thread_id"] != thread_id:
        raise LoopError("Codex 派发所记录的线程与尝试不符")
    workspace = binding["payload"]["workspace"]
    known_id = _turn_id(operation)
    if known_id:
        thread = _thread_snapshot(client, thread_id, workspace, turn_id=known_id, include_items=include_items)
    else:
        # Marker recovery needs the user-message items until the exact turn ID is known.
        thread = _thread_snapshot(client, thread_id, workspace)
    return thread, _find_turn(thread, operation)


def _find_turn(thread, operation):
    if operation is None:
        return None
    known_id = _turn_id(operation)
    if known_id:
        return next((turn for turn in thread["turns"] if turn.get("id") == known_id), None)
    marker = operation["payload"]["marker"]
    candidates = [
        turn for turn in thread["turns"]
        if any(
            item.get("type") == "userMessage"
            and any(
                piece.get("type") == "text" and (piece.get("text") or "").splitlines()[:1] == [marker]
                for piece in item.get("content", [])
            )
            for item in turn.get("items", [])
        )
    ]
    if len(candidates) > 1:
        raise LoopError("Codex 中存在多条匹配的执行记录，请先核对原派发")
    return candidates[0] if candidates else None


def _validate_turn(turn):
    if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
        raise LoopError("Codex 未返回可核对的执行 ID")
    if turn.get("status") not in TURN_STATES:
        raise LoopError("Codex 未返回可核对的执行状态")


def _final_response(turn):
    if not turn or turn["status"] not in TERMINAL:
        return None
    for item in reversed(turn.get("items", [])):
        if item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"}:
            if isinstance(item.get("text"), str) and item["text"].strip():
                return item["text"]
    return None


def _result(attempt, binding, operation=None, observed=None):
    thread_id = _thread_id(binding)
    state = "unbound"
    turn_status = "unknown"
    if binding:
        state = "ready" if thread_id else "thread-unknown"
        if thread_id:
            turn_status = "not-started"
    if operation:
        state = "turn-unknown"
        turn_status = "unknown"
    if observed is not None:
        _validate_turn(observed)
        state = "observed"
        turn_status = observed["status"]
    return {
        "attempt_id": attempt["id"],
        "dispatch_id": operation["payload"]["dispatch_id"] if operation else attempt["operation_id"],
        "thread_id": thread_id,
        "turn_id": observed["id"] if observed else _turn_id(operation),
        "turn_status": turn_status,
        "final_response": _final_response(observed),
        "worker": _host_identity(thread_id) if thread_id else None,
        "host_ref": _host_identity(thread_id) if thread_id else None,
        "state": state,
    }


def _latest_turn(db, run_id, attempt_id):
    for row in db.execute(
        "SELECT id, payload FROM operations WHERE run_id=? AND kind='codex-turn' ORDER BY rowid DESC",
        (run_id,),
    ):
        if unpack(row["payload"])["attempt_id"] == attempt_id:
            return get_operation(db, run_id, row["id"])
    return None


def _read_bindings(db, run_id, attempt_id):
    attempt = get_attempt(db, attempt_id)
    if attempt["run_id"] != run_id:
        raise LoopError(f"attempt belongs to another run: {attempt_id}")
    thread = get_operation(db, run_id, _thread_operation(attempt_id))
    turn = get_operation(db, run_id, _turn_operation(attempt["operation_id"]))
    return attempt, thread, turn


def _require_terminal_tools(client, binding, operation, *, completed=False):
    thread, observed = _observe_turn(client, binding, operation)
    _validate_turn(observed)
    status = observed["status"]
    if status not in TERMINAL or (thread.get("status") or {}).get("type") == "active":
        raise LoopError("Codex 执行仍在运行。请等待结束或中断并确认实际状态")
    if completed and status != "completed":
        raise LoopError("当前 Codex 执行未成功结束，无法收件")
    return observed, client.require_tools_drained(thread, observed, operation["payload"].get("workspace_helpers", ()))


def require_quiescent(db, run_id, attempt, *, completed=False, stopped=False, isolated=False):
    """Confirm host state before collecting a result or releasing its execution slot."""
    _, binding, operation = _read_bindings(db, run_id, attempt["id"])
    if binding is None:
        return None
    quarantine = get_operation(db, run_id, _isolation_operation(attempt["id"]))
    if quarantine:
        receipt = quarantine.get("receipt")
        if isolated and not completed and quarantine["status"] == "done" and receipt:
            original = Path(receipt["workspace"])
            location = Path(receipt["quarantine"])
            if (not original.exists() and not original.is_symlink() and location.is_dir()
                    and location.resolve() == location and _worktree_identity(location) == receipt["identity"]):
                return receipt
        raise LoopError("Codex 工作区正在隔离，请完成 fence --isolate-codex")
    if operation is None and not completed:
        operation = _latest_turn(db, run_id, attempt["id"])
    thread_id = _thread_id(binding)
    if operation is None:
        if completed:
            raise LoopError("当前派发尚无已成功结束的 Codex 执行，无法收件")
        if not thread_id and not stopped:
            raise LoopError("Codex 线程创建状态未确认，请核对宿主后登记停工")
        # Thread creation alone cannot dispatch work. No turn intent exists for this attempt.
        return {
            "thread_id": thread_id, "turn_id": None, "dispatch_id": attempt["operation_id"],
            "turn_status": "not-started",
        }
    if not thread_id:
        raise LoopError("Codex 线程身份未确认，无法确认执行停止")
    if completed and attempt["worker"] != _host_identity(thread_id):
        raise LoopError("当前执行者与登记的 Codex 线程不符，无法收件")
    with CodexClient(binding["payload"]["socket"], timeout=4) as client:
        observed, tool_proof = _require_terminal_tools(client, binding, operation, completed=completed)
    return {
        "thread_id": thread_id, "turn_id": observed["id"],
        "dispatch_id": operation["payload"]["dispatch_id"], "turn_status": observed["status"],
        "tool_proof": tool_proof,
    }


def _managed_worktree(repo, workspace):
    if workspace.resolve() != workspace or not workspace.is_dir():
        raise LoopError("找不到实际的 Codex 受管工作区")
    common = Path(artifacts.git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip())
    top = Path(artifacts.git(workspace, "rev-parse", "--show-toplevel").decode().strip())
    if common.resolve() != (repo / ".git").resolve() or top.resolve() != workspace:
        raise LoopError("Codex 工作区不属于本轮仓库")


def _worktree_identity(workspace):
    stat = workspace.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino}


def isolate_workspace(store, run_id, epoch, attempt_id, reason, *, host_ref=None):
    """Permanently retire the exact worktree before fencing an uncertain execution."""
    from .lifecycle import _host_ref, require_reconciled

    if not isinstance(reason, str) or not reason.strip():
        raise LoopError("隔离 Codex 工作区须提供原因")
    host_ref = _host_ref(host_ref)
    op_id = _isolation_operation(attempt_id)
    run_root = artifacts.run_directory(store, run_id)
    repo = artifacts.repo_path(store, run_id)
    source = run_root / "workspaces" / attempt_id
    destination = run_root / "isolated-workspaces" / attempt_id
    if any(path.resolve() != path for path in (source, destination)):
        raise LoopError("Codex 隔离路径不能包含符号链接")
    with store.transaction() as db:
        run = guard(db, run_id, epoch, allow_paused=True)
        paused = active_run_pause(db, run_id)
        attempt, binding, _ = _read_bindings(db, run_id, attempt_id)
        if not binding or attempt["workspace"] != str(source) or binding["payload"]["workspace"] != str(source):
            raise LoopError("只能隔离已登记的 Codex 工作区")
        thread_id = _thread_id(binding)
        payload = {
            "attempt_id": attempt_id,
            "host_ref": _host_identity(thread_id) if thread_id else f"codex-workspace:{attempt_id}",
            "workspace": str(source), "quarantine": str(destination), "reason": reason.strip(),
        }
        if host_ref and host_ref != payload["host_ref"]:
            raise LoopError("停工确认的宿主引用与已登记的 Codex 宿主不符")
        previous = get_operation(db, run_id, op_id)
        if not paused:
            require_reconciled(store, db, run)
        if not previous:
            dispatch.current_attempt(db, run_id, attempt_id)
            execution = (attempt["result"] or {}).get("codex_execution") or {}
            legacy_result = attempt["state"] in {"submitted", "completed"} and not execution.get("tool_proof") and not paused
            if attempt["state"] not in {"reserved", "running", "paused"} and not legacy_result:
                raise LoopError("只能隔离当前在途或暂停的 Codex 工作区")
            if destination.exists():
                raise LoopError("Codex 隔离目标已存在，须先核对工作区")
            _managed_worktree(repo, source)
            payload["identity"] = _worktree_identity(source)
        else:
            payload["identity"] = previous["payload"]["identity"]
        begin_operation(db, run_id, op_id, "codex-isolation", payload)

    with store.transaction() as db:
        run = guard(db, run_id, epoch, allow_paused=True)
        paused = active_run_pause(db, run_id)
        previous = get_operation(db, run_id, op_id)
        attempt = get_attempt(db, attempt_id)
        if not paused:
            require_reconciled(store, db, run)
        elif attempt["state"] in {"submitted", "completed"}:
            raise LoopError("整轮暂停期间只能隔离在途或已暂停的尝试")
        if source.exists() and destination.exists():
            raise LoopError("原工作区和隔离工作区同时存在，须先核对")
        if source.exists():
            if previous["status"] == "done":
                raise LoopError("已隔离的原工作区重新出现，须先核对")
            _managed_worktree(repo, source)
            if _worktree_identity(source) != payload["identity"]:
                raise LoopError("原 Codex 工作区身份已改变")
            destination.parent.mkdir(parents=True, exist_ok=True)
            artifacts.git(repo, "worktree", "move", str(source), str(destination))
        _managed_worktree(repo, destination)
        if _worktree_identity(destination) != payload["identity"]:
            raise LoopError("隔离后的 Codex 工作区身份不符")
        listing = artifacts.git(repo, "worktree", "list", "--porcelain", "-z").split(b"\x00")
        if f"worktree {destination}".encode() not in listing and previous["status"] != "done":
            artifacts.git(repo, "worktree", "repair", str(destination))
            listing = artifacts.git(repo, "worktree", "list", "--porcelain", "-z").split(b"\x00")
        if source.exists() or f"worktree {destination}".encode() not in listing:
            raise LoopError("Codex 工作区隔离尚未确认")
        receipt = finish_operation(db, run_id, op_id, {**payload, "state": "isolated"})["receipt"]
        outstanding = [_thread_operation(attempt_id)]
        turn = _latest_turn(db, run_id, attempt_id)
        if turn:
            outstanding.append(turn["id"])
        for pending in outstanding:
            db.execute(
                "UPDATE operations SET status='superseded', receipt=? WHERE run_id=? AND id=? AND status='intent'",
                (pack({"state": "isolated", "isolation_operation": op_id}), run_id, pending),
            )
        return receipt


def _ensure_thread(store, client, run_id, epoch, attempt_id, socket):
    operation_id = _thread_operation(attempt_id)
    with store.transaction() as db:
        _, attempt, workspace = _launch_context(store, db, run_id, epoch, attempt_id)
        current = get_operation(db, run_id, operation_id)
        if current:
            if current["payload"]["socket"] != socket:
                raise LoopError("Codex socket 与该尝试已登记的宿主不符")
            thread_id = _thread_id(current)
            if not thread_id:
                raise LoopError("Codex 线程创建结果未确认。请核对宿主并通过 codex-attach 登记原线程")
            _validate_worker(attempt, thread_id)
            return current
        _validate_worker(attempt)
        begin_operation(db, run_id, operation_id, "codex-thread", {
            "attempt_id": attempt_id, "workspace": str(workspace), "socket": socket,
        })

    # The committed intent survives a process interruption or an ambiguous reply.
    with store.transaction() as db:
        _, attempt, workspace = _launch_context(store, db, run_id, epoch, attempt_id)
        current = get_operation(db, run_id, operation_id)
        if _thread_id(current):
            _validate_worker(attempt, _thread_id(current))
            return current
        _validate_worker(attempt)
        thread_id = client.start_thread(workspace)
        if not isinstance(thread_id, str) or not thread_id:
            raise LoopError("Codex 未返回线程 ID")
        return finish_operation(db, run_id, operation_id, {"thread_id": thread_id})


def attach_thread(store, run_id, epoch, attempt_id, thread_id):
    """Recover the unused thread created by an unresolved thread/start."""
    if not isinstance(thread_id, str) or not thread_id.strip() or thread_id != thread_id.strip():
        raise LoopError("请提供已在原 Codex 宿主核对的线程 ID")
    with store.transaction() as db:
        _, attempt, workspace = _launch_context(store, db, run_id, epoch, attempt_id)
        _, binding, operation = _read_bindings(db, run_id, attempt_id)
        if not binding:
            raise LoopError("该尝试没有可核对的 Codex 线程创建意图")
        existing = _thread_id(binding)
        if existing:
            if existing != thread_id:
                raise LoopError("该尝试已登记不同的 Codex 线程")
            return _result(attempt, binding, operation)
        if attempt["state"] != "reserved" or binding["status"] != "intent" or _latest_turn(db, run_id, attempt_id):
            raise LoopError("仅可补登尚未发出执行的 Codex 线程")
        _validate_worker(attempt)
        for row in db.execute("SELECT receipt FROM operations WHERE kind='codex-thread' AND status='done'"):
            if unpack(row["receipt"])["thread_id"] == thread_id:
                raise LoopError("该 Codex 线程已被另一个尝试登记")
        with CodexClient(binding["payload"]["socket"], timeout=4) as client:
            thread = _thread_snapshot(client, thread_id, workspace, newest_only=True, include_items=False)
        if thread["turns"] or (thread.get("status") or {}).get("type") != "idle":
            raise LoopError("只能补登工作区相符、空闲且未运行过的 Codex 线程")
        if thread.get("ephemeral") is not False:
            raise LoopError("该 Codex 线程未设置为持久")
        binding = finish_operation(db, run_id, binding["id"], {"thread_id": thread_id})
        return _result(attempt, binding)


def _bound_contracts(db, run_id, bindings):
    result = {}
    for edge, version in bindings.items():
        row = db.execute(
            "SELECT body FROM contracts WHERE run_id=? AND edge=? AND version=?",
            (run_id, edge, version),
        ).fetchone()
        if row is None:
            raise LoopError(f"找不到尝试绑定的契约 {edge}/{version}")
        body = unpack(row["body"])
        result[edge] = {
            "version": version, "provider": body["provider"],
            "consumer": body["consumer"], "spec": body["spec"],
        }
    return result


def _prompt(run, attempt, workspace, marker, skill, contracts):
    spec = attempt["input"]["spec"]
    plan = run["baseline"].get("requirements") or {}
    scoped = [item for item in plan.get("features", []) if item["id"] in spec.get("feature_ids", [])]
    features = [item for item in scoped if item.get("status") != "removed"]
    removals = [
        {key: item[key] for key in ("id", "name", "scene_id", "cases", "assertions") if key in item}
        for item in scoped if item.get("status") == "removed"
    ]
    requirement_ids = {item for feature in features for item in feature.get("requirement_ids", [])}
    requirements = [item for item in plan.get("requirements", []) if item["id"] in requirement_ids]
    scenes = [item for item in plan.get("scenes", []) if any(feature.get("scene_id") == item["id"] for feature in features)]
    style = skill.get("style_skill")
    design = attempt["input"].get("design_handoff")
    prompt = (
        f"{marker}\n\n"
        f"执行 RTL 场景任务 {attempt['task_id']}，尝试 {attempt['id']}，"
        f"派发阶段 {dispatch.attempt_stage(attempt)}，阶段 {attempt['input']['phase']}。\n"
        f"工作区：{workspace}。只在该目录和任务授权文件范围内工作。"
        "按任务阶段提供实际结果。将测试结果、实际修改路径和阻塞项交回 Master。"
        "请保留工作区源码变更，不运行 Git 暂存或提交。"
        "结束前等待本任务启动的所有工具进程和子 Agent 收尾，切勿留下后台或脱离会话的写入。"
        "下方 Skill 中的 Git 提交、RTL 构建和台账操作由 Master 代办。"
        "不要操作其他工作区或发布结果。遇到权限限制时报告实际状态。\n\n"
        f"任务：{pack(spec)}\n"
        f"候选：{attempt['input']['candidate']}\n"
        f"已绑定契约：{pack(contracts)}\n"
        f"相关需求：{pack(requirements)}\n"
        f"相关场景：{pack(scenes)}\n"
        f"现行功能：{pack(features)}\n"
        f"本任务须移除的功能和冻结验证：{pack(removals)}\n\n"
        f"{skill['content']}"
    )
    if style:
        prompt += f"\n\n本次适用的设计风格：\n{style['content']}"
    if design:
        prompt += f"\n\n已登记的设计交接：{pack(design)}"
    return prompt


def _remember_turn(store, run_id, epoch, attempt_id, binding, operation, turn):
    _validate_turn(turn)
    worker = _host_identity(_thread_id(binding))
    with store.transaction() as db:
        _, attempt, _ = _launch_context(store, db, run_id, epoch, attempt_id)
        if attempt["operation_id"] != operation["payload"]["dispatch_id"]:
            raise LoopError("当前尝试的派发已改变，请核对台账")
        _validate_worker(attempt, _thread_id(binding))
        finish_operation(db, run_id, operation["id"], {"turn_id": turn["id"]})
    # A crash between these transactions is recoverable from the stored Codex turn.
    attempt = dispatch.start(store, run_id, epoch, attempt_id, worker, host_ref=worker, codex_managed=True)
    return _result(attempt, binding, operation, turn)


def _start_or_recover_turn(store, client, run_id, epoch, attempt_id, binding, skill, prepare):
    thread_id = _thread_id(binding)
    with store.transaction() as db:
        run, attempt, workspace = _launch_context(store, db, run_id, epoch, attempt_id)
        _validate_worker(attempt, thread_id)
        dispatch_id = attempt["operation_id"]
        operation_id = _turn_operation(dispatch_id)
        operation = get_operation(db, run_id, operation_id)
        previous = _latest_turn(db, run_id, attempt_id) if not operation else None
        contracts = {} if operation else _bound_contracts(db, run_id, attempt["input"]["contracts"])

    if operation:
        _, observed = _observe_turn(client, binding, operation)
        if observed is None and operation["status"] == "intent":
            # The server can acknowledge turn/start before publishing its userMessage in history.
            for delay in (0.2, 0.4, 0.8, 1.2, 1.6):
                time.sleep(delay)
                _, observed = _observe_turn(client, binding, operation)
                if observed is not None:
                    break
        if observed is None:
            raise LoopError("Codex 执行状态未确认。已保留原派发意图，请先核对原线程")
        return _remember_turn(store, run_id, epoch, attempt_id, binding, operation, observed)

    if previous:
        # In particular, a pre-upgrade paused attempt cannot inherit a live
        # writer as if it were part of the next turn's idle host services.
        _require_terminal_tools(client, binding, previous)
    thread = _thread_snapshot(client, thread_id, workspace, newest_only=True, include_items=False)
    if any(turn.get("status") not in TERMINAL for turn in thread["turns"]) or (
        (thread.get("status") or {}).get("type") == "active"
    ):
        raise LoopError("该 Codex 线程仍有未结束的执行。请先核对并停止原执行")
    if (thread.get("status") or {}).get("type") == "notLoaded":
        client.resume_thread(thread_id)

    workspace_helpers = client.workspace_helper_baseline(workspace)
    if skill is None:
        skill = prepare() if prepare else read_skill(
            "scene-delivery", "scene", dispatch.attempt_stage(attempt), task=attempt["input"]["spec"],
        )
    marker = f"RTL dispatch: {dispatch_id}"
    prompt = _prompt(run, attempt, workspace, marker, skill, contracts)
    with store.transaction() as db:
        _, current, _ = _launch_context(store, db, run_id, epoch, attempt_id)
        _validate_worker(current, thread_id)
        if current["operation_id"] != dispatch_id:
            raise LoopError("当前尝试的派发已改变，请核对台账")
        if get_operation(db, run_id, operation_id):
            raise LoopError("该 Codex 执行意图已由另一调用登记，请重试以对账")
        payload = {
            "attempt_id": attempt_id, "dispatch_id": dispatch_id, "thread_id": thread_id,
            "marker": marker, "skill_digest": skill["digest"], "workspace_helpers": workspace_helpers,
        }
        if skill.get("operation_id"):
            payload["skill_operation"] = skill["operation_id"]
        if style := skill.get("style_skill"):
            payload["style_skill_digest"] = style["digest"]
            if style.get("operation_id"):
                payload["style_skill_operation"] = style["operation_id"]
        operation = begin_operation(db, run_id, operation_id, "codex-turn", payload)

    with store.transaction() as db:
        _, current, _ = _launch_context(store, db, run_id, epoch, attempt_id)
        _validate_worker(current, thread_id)
        if current["operation_id"] != dispatch_id:
            raise LoopError("当前尝试的派发已改变，请核对台账")
        # Keep other controller mutations out of the short RPC; the intent is already committed.
        turn = client.start_turn(thread_id, workspace, prompt)
        _validate_turn(turn)
        finish_operation(db, run_id, operation_id, {"turn_id": turn["id"]})
    return _remember_turn(store, run_id, epoch, attempt_id, binding, operation, turn)


def start(store, run_id, epoch, attempt_id, socket=None, *, skill=None, prepare=None):
    """Start a prepared attempt, or reconcile its original Codex dispatch."""
    socket = _socket_path(socket)
    with store.transaction(immediate=False) as db:
        _, attempt, _ = _launch_context(store, db, run_id, epoch, attempt_id)
        current = get_operation(db, run_id, _thread_operation(attempt_id))
        if current:
            recorded = current["payload"]["socket"]
            if socket is not None and socket != recorded:
                raise LoopError("Codex socket 与该尝试已登记的宿主不符")
            socket = recorded
        elif socket is None:
            socket = default_socket()
    with CodexClient(socket) as client:
        binding = _ensure_thread(store, client, run_id, epoch, attempt["id"], socket)
        return _start_or_recover_turn(store, client, run_id, epoch, attempt["id"], binding, skill, prepare)


def status(store, run_id, attempt_id):
    """Read the bound thread and observed turn without changing the ledger."""
    with store.transaction(immediate=False) as db:
        attempt, binding, operation = _read_bindings(db, run_id, attempt_id)
        isolation = get_operation(db, run_id, _isolation_operation(attempt_id))
        previous = _latest_turn(db, run_id, attempt_id) if operation is None else None
    result = _result(attempt, binding, operation)
    result["attempt_state"] = attempt["state"]
    if isolation:
        result["state"] = "isolated" if isolation["status"] == "done" else "isolating"
        result["isolation"] = isolation["receipt"] or isolation["payload"]
        return result
    target = operation or previous
    if target and _thread_id(binding):
        try:
            with CodexClient(binding["payload"]["socket"]) as client:
                _, observed = _observe_turn(client, binding, target, include_items=True)
        except CodexHistoryPending:
            observation = {**_result(attempt, binding, target), "state": "history-pending"}
        else:
            observation = _result(attempt, binding, target, observed)
        if previous:
            result["previous"] = observation
        else:
            result.update(observation)
    return result


def interrupt(store, run_id, epoch, attempt_id):
    """Request an interrupt for the exact observed turn, leaving RTL slots unchanged."""
    with store.transaction(immediate=False) as db:
        guard(db, run_id, epoch, allow_paused=True)
        dispatch.current_attempt(db, run_id, attempt_id)
        attempt, binding, operation = _read_bindings(db, run_id, attempt_id)
        allowed = {"reserved", "running", "paused", "submitted", "completed"}
        if attempt["state"] not in allowed:
            raise LoopError("只能中断尚有效的当前 Codex 尝试")
        operation = operation or _latest_turn(db, run_id, attempt_id)
    thread_id = _thread_id(binding)
    if not thread_id or not operation:
        raise LoopError("Codex 执行身份尚未确认，无法中断")
    with CodexClient(binding["payload"]["socket"]) as client:
        _, observed = _observe_turn(client, binding, operation)
        _validate_turn(observed)
        if observed["status"] not in TERMINAL:
            with store.transaction() as db:
                guard(db, run_id, epoch, allow_paused=True)
                current, _ = dispatch.current_attempt(db, run_id, attempt_id)
                if current["operation_id"] != attempt["operation_id"] or current["state"] not in allowed:
                    raise LoopError("当前尝试已改变，请重新核对要中断的执行")
                client.interrupt_turn(thread_id, observed["id"])
            _, observed = _observe_turn(client, binding, operation)
        return _result(attempt, binding, operation, observed)
