"""Protect operation recovery and retire a run without losing its history."""

from datetime import datetime, timezone
from uuid import uuid4

from .state import (
    LoopError, active_run_pause, begin_operation, finish_operation,
    get_attempt, get_operation, get_run, guard, safe_id, unpack,
)


def require_no_integration_rollback(db, run_id):
    pending = db.execute(
        "SELECT payload FROM operations WHERE run_id=? AND kind='integration-rollback' AND status='intent'",
        (run_id,),
    ).fetchone()
    if pending:
        original = unpack(pending["payload"])["integration_operation"]
        raise LoopError(f"合入撤销尚未完成，请重试 rollback-integration：{original}")


def require_reconciled(store, db, run):
    """Refuse mutations that could invalidate an operation before its receipt exists."""
    from .artifacts import CANDIDATE_REF, repo_path, rev
    from .delivery import require_no_landing_rollback

    require_no_integration_rollback(db, run["id"])
    require_no_landing_rollback(db, run["id"])
    if db.execute(
        "SELECT 1 FROM operations WHERE run_id=? AND kind='acceptance' AND status='intent'",
        (run["id"],),
    ).fetchone():
        raise LoopError("请先完成或撤销已登记的验收意图")
    if run["candidate"] and rev(repo_path(store, run["id"]), CANDIDATE_REF) != run["candidate"]:
        raise LoopError("Git 候选与台账不符，请先用原合入操作补记回执，或撤销未完成的合入")


def _rollback_integration_state(db, run, repo, operation_id):
    from .artifacts import CANDIDATE_REF, rev

    operation = get_operation(db, run["id"], operation_id)
    if not operation or operation["kind"] != "integration" or operation["status"] != "intent":
        raise LoopError("只能撤销原先已登记但尚未完成的合入")
    parent = operation["payload"]["parent"]
    candidate = operation["payload"]["candidate"]
    if candidate == parent or parent != run["candidate"]:
        raise LoopError("合入父版本或候选与当前台账不符，无法撤销")
    return parent, candidate, rev(repo, CANDIDATE_REF)


def rollback_integration(store, run_id, epoch, operation_id, *, reason):
    """Restore the recorded parent when an unfinished promotion cannot be completed."""
    from .artifacts import CANDIDATE_REF, git, repo_path, rev

    operation_id = safe_id(operation_id, "integration operation ID")
    if not isinstance(reason, str) or not reason.strip():
        raise LoopError("撤销未完成的合入须登记原因")
    reason = reason.strip()
    rollback_id = f"rollback-{operation_id}"
    discarded_ref = f"refs/rtl/rollback/{operation_id}"
    repo = repo_path(store, run_id)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        existing = get_operation(db, run_id, rollback_id)
        if existing and existing["payload"].get("reason") != reason:
            raise LoopError("该合入已有不同的撤销决定")
        if existing and existing["status"] == "done":
            return existing["receipt"]
        if not existing:
            require_no_integration_rollback(db, run_id)
        parent, candidate, actual = _rollback_integration_state(db, run, repo, operation_id)
        if actual != candidate and not (existing and actual == parent):
            raise LoopError("实际 Git 候选不属于这次未完成的合入，无法撤销")
        decision = {
            "integration_operation": operation_id, "parent": parent, "discarded_candidate": candidate,
            "reason": reason,
        }
        begin_operation(db, run_id, rollback_id, "integration-rollback", decision)

    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        rollback = get_operation(db, run_id, rollback_id)
        if rollback["status"] == "done":
            return rollback["receipt"]
        parent, candidate, actual = _rollback_integration_state(db, run, repo, operation_id)
        if rollback["status"] != "intent" or actual not in {parent, candidate}:
            raise LoopError("实际 Git 候选与已登记的撤销意图不符")
        if actual == candidate:
            update = f"create {discarded_ref} {candidate}\nupdate {CANDIDATE_REF} {parent} {candidate}\n"
            git(repo, "update-ref", "--stdin", input_bytes=update.encode())
        elif rev(repo, discarded_ref) != candidate:
            raise LoopError("撤销归档与已登记的候选不符")
        db.execute(
            "UPDATE operations SET status='superseded' WHERE run_id=? AND id=?",
            (run_id, operation_id),
        )
        receipt = {
            **decision, "operation_id": rollback_id, "candidate": parent,
            "discarded_ref": discarded_ref, "status": "rolled_back",
        }
        finish_operation(db, run_id, rollback_id, receipt)
        return receipt


def supersede(store, run_id, epoch, *, reason, approval, stopped=False):
    """Release the active slot after a confirmed baseline change or abandonment."""
    from .codex_host import require_quiescent
    from .delivery import _directory, _directory_state
    from .dispatch import require_paused_safe

    if not all(isinstance(value, str) and value.strip() for value in (reason, approval)):
        raise LoopError("替代旧轮须记录变更原因及用户确认依据")
    decision = {"reason": reason.strip(), "approval": approval.strip()}
    with store.transaction() as db:
        run = get_run(db, run_id)
        previous = get_operation(db, run_id, "supersede")
        if previous and previous["status"] == "done":
            if previous["payload"] != decision:
                raise LoopError("旧轮已有不同的替代决定")
            return previous["receipt"]
        run = guard(db, run_id, epoch, allow_paused=True)
        require_reconciled(store, db, run)
        pause = run_pause_status(db, run_id)
        if pause and pause["outstanding"]:
            raise LoopError("替代暂停中的旧轮前，须先确认所有在途宿主已停止或隔离")
        workers = [
            row["id"] for row in db.execute(
                "SELECT id FROM attempts WHERE run_id=? AND state IN ('reserved','running') ORDER BY id",
                (run_id,),
            )
        ]
        if workers and not stopped:
            raise LoopError("先确认旧轮在途尝试已停止或隔离，再登记 --stopped")
        for attempt_id in workers:
            require_quiescent(db, run_id, get_attempt(db, attempt_id), stopped=stopped)
        for row in db.execute("SELECT id FROM attempts WHERE run_id=? AND state='paused'", (run_id,)):
            require_paused_safe(db, run_id, get_attempt(db, row["id"]))
        for row in db.execute(
            "SELECT a.id FROM attempts a JOIN tasks t ON t.attempt_id=a.id "
            "WHERE a.run_id=? AND a.state IN ('submitted','completed')", (run_id,),
        ):
            attempt = get_attempt(db, row["id"])
            execution = (attempt["result"] or {}).get("codex_execution") or {}
            if not execution.get("tool_proof"):
                require_quiescent(db, run_id, attempt, stopped=stopped)
        pending = db.execute(
            "SELECT payload FROM operations WHERE run_id=? AND kind='delivery' AND status='intent'",
            (run_id,),
        ).fetchone()
        receipt = {
            **decision, "run_id": run_id, "status": "superseded", "candidate": run["candidate"],
            "output": run["output_path"], "stopped_attempts": workers,
        }
        if pending:
            payload = unpack(pending["payload"])
            actual = _directory_state(_directory(payload["output"]))
            if actual not in (payload["previous_manifest"], payload["manifest"]):
                raise LoopError("落盘目录与待验交付不符，请先核对原操作及备份")
            if actual is not None:
                receipt["retained_output_manifest"] = actual

        begin_operation(db, run_id, "supersede", "supersede", decision)
        db.execute(
            "UPDATE operations SET status='superseded' WHERE run_id=? AND status='intent' AND id<>'supersede'",
            (run_id,),
        )
        db.execute("UPDATE runs SET status='superseded' WHERE id=?", (run_id,))
        finish_operation(db, run_id, "supersede", receipt)
        return receipt


def _host_ref(value):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise LoopError("宿主引用须为非空字符串")
    return value.strip() if value is not None else None


def _start_host_ref(db, run_id, attempt):
    # A newly reserved resume has its own operation, but its most recent
    # observed start still belongs to the same worker until the host starts it.
    for row in db.execute(
        "SELECT receipt FROM operations WHERE run_id=? AND kind IN ('dispatch','resume') "
        "AND status='done' ORDER BY rowid DESC",
        (run_id,),
    ):
        receipt = unpack(row["receipt"])
        if receipt and receipt.get("attempt_id") == attempt["id"] and receipt.get("host_ref"):
            return receipt["host_ref"]
    return None


def _pause_ack_id(pause_id, attempt_id):
    return f"{pause_id}:attempt:{attempt_id}"


def acknowledge_run_pause(db, run_id, attempt, host_ref, *, stopped=True):
    """Record observed host status as an immutable receipt in the current pause."""
    pause = active_run_pause(db, run_id)
    if pause is None or not any(item["id"] == attempt["id"] for item in pause["payload"]["attempts"]):
        return
    host_ref = _host_ref(host_ref)
    expected = _start_host_ref(db, run_id, attempt)
    if expected and host_ref and expected != host_ref:
        raise LoopError("停工确认的宿主引用与已登记的启动引用不符")
    if not host_ref and not expected:
        raise LoopError("整轮暂停的停工确认须登记实际宿主引用 --host-ref")
    operation_id = _pause_ack_id(pause["id"], attempt["id"])
    payload = {
        "pause_id": pause["id"], "attempt_id": attempt["id"],
        "worker": attempt["worker"], "host_ref": host_ref or expected,
        "state": "stopped" if stopped else "isolated",
    }
    begin_operation(db, run_id, operation_id, "run-pause-ack", payload)
    finish_operation(db, run_id, operation_id, payload)


def run_pause_status(db, run_id, pause=None):
    pause = pause or active_run_pause(db, run_id)
    if pause is None:
        return None
    attempts = []
    for original in pause["payload"]["attempts"]:
        current = get_attempt(db, original["id"])
        acknowledgement = get_operation(db, run_id, _pause_ack_id(pause["id"], original["id"]))
        receipt = acknowledgement["receipt"] if acknowledgement else None
        if receipt:
            host_state = receipt["state"]
        elif original["state"] == "paused":
            host_state = "previously_paused"
        else:
            host_state = "unknown"
        attempts.append({
            **original, "state": current["state"], "host_state": host_state,
            "host_ref": (receipt or {}).get("host_ref", original["host_ref"]),
        })
    outstanding = [attempt["id"] for attempt in attempts if attempt["host_state"] == "unknown"]
    return {
        **pause["payload"], "pause_id": pause["id"], "attempts": attempts,
        "state": "stopping" if outstanding else "paused", "outstanding": outstanding,
    }


def pause_run(store, run_id, epoch, *, reason, checkpoint=None):
    """Persist the run's write gate before asking the host to stop any workers."""
    if not isinstance(reason, str) or not reason.strip():
        raise LoopError("整轮暂停须记录原因")
    if checkpoint is not None and not isinstance(checkpoint, dict):
        raise LoopError("暂停检查点须为 JSON 对象")
    with store.transaction() as db:
        run = guard(db, run_id, epoch, allow_paused=True)
        active = active_run_pause(db, run_id)
        if active:
            if active["payload"]["reason"] != reason.strip() or active["payload"]["checkpoint"] != (checkpoint or {}):
                raise LoopError(f"本轮已有暂停决定，请继续核对原暂停：{active['id']}")
            return run_pause_status(db, run_id, active)
        attempts = []
        for row in db.execute(
            "SELECT id,task_id,state,workspace,worker,operation_id FROM attempts "
            "WHERE run_id=? AND state IN ('reserved','running','paused') ORDER BY rowid",
            (run_id,),
        ):
            item = dict(row)
            item["host_ref"] = _start_host_ref(db, run_id, item)
            attempts.append(item)
        operations = [
            row["id"] for row in db.execute(
                "SELECT id FROM operations WHERE run_id=? AND status='intent' ORDER BY rowid", (run_id,),
            )
        ]
        payload = {
            "run_id": run_id, "reason": reason.strip(), "checkpoint": checkpoint or {},
            "requested_epoch": epoch, "requested_at": datetime.now(timezone.utc).isoformat(),
            "candidate": run["candidate"],
            "baseline_revision": run["baseline"].get("revision"),
            "project_root": run["project_root"], "output_path": run["output_path"],
            "attempts": attempts, "pending_operations": operations,
        }
        operation = begin_operation(db, run_id, f"pause-run-{uuid4().hex}", "run-pause", payload)
        return run_pause_status(db, run_id, operation)


def resume_run(store, run_id, epoch, pause_id):
    """Lift the write gate only after the operator confirms the prior pause."""
    from .dispatch import require_paused_safe

    with store.transaction() as db:
        guard(db, run_id, epoch, allow_paused=True)
        active = active_run_pause(db, run_id)
        if active is None:
            previous = get_operation(db, run_id, pause_id)
            if previous and previous["kind"] == "run-pause" and previous["status"] == "done":
                return previous["receipt"]
            raise LoopError("找不到本轮有效的暂停记录")
        if active["id"] != pause_id:
            raise LoopError(f"当前暂停记录为 {active['id']}，请核对后继续")
        paused = run_pause_status(db, run_id, active)
        if paused["outstanding"]:
            pending = ", ".join(paused["outstanding"])
            raise LoopError(f"尚未确认以下尝试的宿主已停止或隔离：{pending}")
        for attempt in paused["attempts"]:
            if attempt["state"] == "paused":
                require_paused_safe(db, run_id, get_attempt(db, attempt["id"]))
        receipt = {
            **paused, "state": "resumed", "resumed_epoch": epoch,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
        finish_operation(db, run_id, pause_id, receipt)
        return receipt
