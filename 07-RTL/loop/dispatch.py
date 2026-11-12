"""Two-slot dispatch and attempt fencing for the local RTL Loop."""

from pathlib import Path
from uuid import uuid4

from .state import (
    LoopError,
    active_run_pause,
    begin_operation,
    decode,
    finish_operation,
    get_attempt,
    get_operation,
    get_task,
    guard,
    overlaps,
    pack,
    path_in_scope,
    scopes_overlap,
    unpack,
)

SLOT_LIMIT = 2
RUNNING_STATES = ("reserved", "running")
ELIGIBLE_TASKS = ("pending", "planned", "paused")
DISPATCH_STAGES = ("S2", "S3")


def active_contracts(db, run_id, edges):
    versions = {
        row["edge"]: row["version"]
        for row in db.execute(
            "SELECT edge, version FROM contracts WHERE run_id = ? AND active = 1", (run_id,)
        )
    }
    return {edge: versions[edge] for edge in edges if edge in versions}


def current_attempt(db, run_id, attempt_id):
    attempt = get_attempt(db, attempt_id)
    if attempt["run_id"] != run_id:
        raise LoopError(f"attempt belongs to another run: {attempt_id}")
    task = get_task(db, run_id, attempt["task_id"])
    if task["attempt_id"] != attempt_id or attempt["state"] == "fenced":
        raise LoopError(f"attempt is no longer authoritative: {attempt_id}")
    return attempt, task


def ensure_current_inputs(db, run_id, attempt):
    data = attempt["input"]
    current = active_contracts(db, run_id, data["spec"]["edges"])
    if data["phase"] == "design":
        if not _design_contracts_match(db, run_id, data, current):
            raise LoopError(f"contract versions changed for design attempt {attempt['id']}; reconcile or fence it")
        return
    if current != data["contracts"]:
        raise LoopError(f"contract versions changed for attempt {attempt['id']}; reconcile or fence it")
    if "design_handoff" in data:
        task = {"id": attempt["task_id"], "spec": data["spec"]}
        expected = _design_handoff(db, run_id, task, data.get("requirements"), current)
        if data["design_handoff"] != expected:
            raise LoopError(f"尝试 {attempt['id']} 的设计交接依据已失效，请隔离并重新派发")
    for dependency in data["spec"]["deps"]:
        if get_task(db, run_id, dependency)["status"] != "integrated":
            raise LoopError(f"dependency {dependency} is no longer integrated")


def attempt_stage(attempt):
    """Read the dispatch stage, including attempts recorded before stage tracking."""
    return attempt["input"].get("dispatch_stage", "S2")


def require_workspace_available(db, run_id, attempt_id):
    if get_operation(db, run_id, f"codex-isolation:{attempt_id}"):
        raise LoopError("该 Codex 工作区已进入隔离，请完成原尝试的 fence")


def require_paused_safe(db, run_id, attempt):
    from .codex_host import require_quiescent

    operation_id = f"codex-stop:{attempt['operation_id']}"
    recorded = get_operation(db, run_id, operation_id)
    if recorded and recorded["status"] == "done":
        return
    observed = require_quiescent(db, run_id, attempt, stopped=True)
    if observed:
        begin_operation(db, run_id, operation_id, "codex-stop", {"attempt_id": attempt["id"]})
        finish_operation(db, run_id, operation_id, observed)


def _readiness(db, run_id, task, phase, stage, scopes, requirements):
    if task["status"] not in ELIGIBLE_TASKS:
        return f"task is {task['status']}"
    if phase == "design" and task["status"] == "planned":
        prior_design = _latest_design(db, run_id, task, requirements)
        current = active_contracts(db, run_id, task["spec"]["edges"])
        if not prior_design or _design_contracts_match(db, run_id, prior_design[1], current):
            return "design already completed"
    if task["status"] == "paused":
        existing = get_attempt(db, task["attempt_id"])
        if existing["input"]["phase"] != phase:
            return f"paused {existing['input']['phase']} attempt must be resumed or fenced first"
        recorded_stage = attempt_stage(existing)
        if stage is not None and stage != recorded_stage:
            return f"paused attempt is bound to dispatch stage {recorded_stage}"
        try:
            require_workspace_available(db, run_id, existing["id"])
            ensure_current_inputs(db, run_id, existing)
        except LoopError as exc:
            return str(exc)
    if phase == "design":
        return None
    spec = task["spec"]
    for dependency in spec["deps"]:
        row = db.execute(
            "SELECT status FROM tasks WHERE run_id = ? AND id = ?", (run_id, dependency)
        ).fetchone()
        if row is None or row["status"] != "integrated":
            return f"waiting for integrated task {dependency}"
    missing = set(spec["edges"]) - active_contracts(db, run_id, spec["edges"]).keys()
    if missing:
        return f"waiting for active contracts: {', '.join(sorted(missing))}"
    for attempt_id, paths in scopes:
        if attempt_id != task["attempt_id"] and scopes_overlap(spec["paths"], paths):
            return f"write scope is held by attempt {attempt_id}"
    return None


def _active_scopes(db, run_id):
    for row in db.execute(
        "SELECT id, input FROM attempts WHERE run_id = ? AND state IN ('reserved', 'running', 'paused')",
        (run_id,),
    ):
        data = unpack(row["input"])
        if data["phase"] == "implement":
            yield row["id"], data["spec"]["paths"]


def _requirements_binding(run_id, readiness):
    from .requirements_run import snapshot_relative_path

    if readiness is None:
        return None
    return {
        "revision": readiness["revision"], "plan_sha256": readiness["plan_sha256"],
        "snapshot": snapshot_relative_path(run_id),
    }


def _check_attempt_binding(attempt, expected):
    if attempt["input"].get("requirements") != expected:
        raise LoopError(f"尝试绑定的 S1 冻结契约与本轮不符：{attempt['id']}")


def require_bound_requirements(db, run, attempt):
    """Recheck the frozen S1 files and the exact binding captured at reservation."""
    from .requirements_run import require_prepared

    readiness = require_prepared(db, run)
    _check_attempt_binding(attempt, _requirements_binding(run["id"], readiness))


def _design_summary(result):
    summary = result.get("summary", {key: value for key, value in result.items()
                                    if key not in {"actor", "codex_execution"}})
    if isinstance(summary, dict) and any(
        (value.strip() if isinstance(value, str) else value) not in (None, "", [], {})
        for value in summary.values()
    ):
        return summary
    return None


def _design_contracts_match(db, run_id, design_inputs, current):
    from .contracts import is_first_activation

    bound = design_inputs.get("contracts", {})
    if any(current.get(edge) != version for edge, version in bound.items()):
        return False
    unbound = set(design_inputs["spec"].get("edges", [])) - bound.keys()
    return all(is_first_activation(db, run_id, edge, current[edge]) for edge in unbound & current.keys())


def _latest_design(db, run_id, task, requirements):
    for row in db.execute(
        "SELECT id, input, result FROM attempts WHERE run_id=? AND task_id=? AND state='completed' ORDER BY rowid DESC",
        (run_id, task["id"]),
    ):
        prior = unpack(row["input"])
        if prior["phase"] == "design" and prior["spec"] == task["spec"] and prior.get("requirements") == requirements:
            summary = _design_summary(unpack(row["result"]) or {})
            if summary is not None:
                return row["id"], prior, summary
    return None


def _design_handoff(db, run_id, task, requirements, current):
    design = _latest_design(db, run_id, task, requirements)
    if design and _design_contracts_match(db, run_id, design[1], current):
        return {"attempt_id": design[0], "summary": design[2]}
    return None


def reserve(store, run_id, epoch, task_id=None, *, phase="implement", stage=None):
    """Register a dispatch before creating a worktree or launching a worker."""
    from .lifecycle import require_reconciled
    from .requirements_run import require_prepared

    if phase not in {"design", "implement"}:
        raise LoopError("phase must be design or implement")
    if stage is not None and stage not in DISPATCH_STAGES:
        raise LoopError("dispatch stage must be S2 or S3")
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        require_reconciled(store, db, run)
        requirements = _requirements_binding(run_id, require_prepared(db, run))
        if not run["candidate"]:
            raise LoopError("initialize the prototype base before dispatch")
        slots = db.execute(
            "SELECT COUNT(*) FROM attempts WHERE run_id = ? AND state IN ('reserved', 'running')",
            (run_id,),
        ).fetchone()[0]
        if slots >= SLOT_LIMIT:
            raise LoopError("both execution slots are in use")
        if task_id is not None:
            tasks = [get_task(db, run_id, task_id)]
        else:
            tasks = [
                decode(row, "spec")
                for row in db.execute("SELECT * FROM tasks WHERE run_id = ? ORDER BY rowid", (run_id,))
            ]
        scopes = list(_active_scopes(db, run_id))
        blocked = []
        for task in tasks:
            reason = _readiness(db, run_id, task, phase, stage, scopes, requirements)
            if reason is None:
                break
            blocked.append(f"{task['id']}: {reason}")
        else:
            detail = "; ".join(blocked) or "no registered tasks"
            raise LoopError(f"no ready {phase} task: {detail}")
        if task["status"] == "paused":
            attempt_id = task["attempt_id"]
            previous = get_attempt(db, attempt_id)
            _check_attempt_binding(previous, requirements)
            require_paused_safe(db, run_id, previous)
            dispatch_stage = attempt_stage(previous)
            operation_id = f"resume:{attempt_id}:{uuid4().hex}"
            db.execute(
                "UPDATE attempts SET state = 'reserved', operation_id = ? WHERE id = ?",
                (operation_id, attempt_id),
            )
            kind = "resume"
        else:
            attempt_id = uuid4().hex
            operation_id = f"dispatch:{attempt_id}"
            dispatch_stage = stage if stage is not None else "S2"
            data = {
                "phase": phase,
                "dispatch_stage": dispatch_stage,
                "candidate": run["candidate"],
                "spec": task["spec"],
                "contracts": active_contracts(db, run_id, task["spec"]["edges"]),
            }
            if requirements is not None:
                data["requirements"] = requirements
            if phase == "implement":
                design = _design_handoff(db, run_id, task, requirements, data["contracts"])
                if design is not None:
                    data["design_handoff"] = design
            db.execute(
                "INSERT INTO attempts (id, run_id, task_id, state, input, operation_id) "
                "VALUES (?, ?, ?, 'reserved', ?, ?)",
                (attempt_id, run_id, task["id"], pack(data), operation_id),
            )
            kind = "dispatch"
        db.execute(
            "UPDATE tasks SET status = ?, attempt_id = ? WHERE run_id = ? AND id = ?",
            ("designing" if phase == "design" else "running", attempt_id, run_id, task["id"]),
        )
        begin_operation(db, run_id, operation_id, kind, {
            "attempt_id": attempt_id, "task_id": task["id"], "dispatch_stage": dispatch_stage,
        })
        return get_attempt(db, attempt_id)


def bind_workspace(store, run_id, epoch, attempt_id, workspace):
    path = Path(workspace).expanduser().resolve()
    if path == store.data_root or not path.is_relative_to(store.data_root):
        raise LoopError("attempt workspace must be inside the Loop data directory")
    if not path.is_dir():
        raise LoopError(f"attempt workspace does not exist: {path}")
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        attempt, _ = current_attempt(db, run_id, attempt_id)
        require_workspace_available(db, run_id, attempt_id)
        require_bound_requirements(db, run, attempt)
        if attempt["workspace"] == str(path):
            return attempt
        if attempt["state"] != "reserved" or attempt["workspace"] is not None:
            raise LoopError("workspace can only be bound once before the worker starts")
        for row in db.execute("SELECT workspace FROM attempts WHERE workspace IS NOT NULL"):
            if overlaps(path, row["workspace"]):
                raise LoopError(f"workspace overlaps another attempt: {path}")
        db.execute("UPDATE attempts SET workspace = ? WHERE id = ?", (str(path), attempt_id))
        return get_attempt(db, attempt_id)


def start(store, run_id, epoch, attempt_id, worker, *, host_ref=None, codex_managed=False):
    """Record an observed worker start, including a start found during takeover."""
    from .lifecycle import _host_ref

    if not isinstance(worker, str) or not worker.strip():
        raise LoopError("worker identity is required")
    host_ref = _host_ref(host_ref)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        attempt, _ = current_attempt(db, run_id, attempt_id)
        require_workspace_available(db, run_id, attempt_id)
        require_bound_requirements(db, run, attempt)
        binding = get_operation(db, run_id, f"codex-thread:{attempt_id}")
        if binding and not codex_managed:
            raise LoopError("该尝试已交给 Codex，请用 codex-start 核对原派发")
        if codex_managed:
            thread_id = ((binding or {}).get("receipt") or {}).get("thread_id")
            if not thread_id or worker != f"codex:{thread_id}" or host_ref != worker:
                raise LoopError("Codex 执行者与已登记线程不符")
        if attempt["state"] == "running" and attempt["worker"] == worker:
            if host_ref:
                operation = get_operation(db, run_id, attempt["operation_id"])
                receipt = (operation or {}).get("receipt") or {}
                if receipt.get("host_ref") != host_ref:
                    raise LoopError("宿主引用与已登记的启动回执不符")
            return attempt
        if attempt["state"] != "reserved" or not attempt["workspace"]:
            raise LoopError("attempt must be reserved and have a bound workspace")
        if attempt["worker"] not in {None, worker}:
            raise LoopError("a resumed attempt must retain its original worker identity")
        ensure_current_inputs(db, run_id, attempt)
        db.execute("UPDATE attempts SET state = 'running', worker = ? WHERE id = ?", (worker, attempt_id))
        receipt = {"attempt_id": attempt_id, "worker": worker}
        if host_ref:
            receipt["host_ref"] = host_ref
        finish_operation(db, run_id, attempt["operation_id"], receipt)
        return get_attempt(db, attempt_id)


def pause(store, run_id, epoch, attempt_id, *, host_ref=None):
    """Record a pause already acknowledged by the worker or its host."""
    from .lifecycle import acknowledge_run_pause

    with store.transaction() as db:
        guard(db, run_id, epoch, allow_paused=True)
        attempt, task = current_attempt(db, run_id, attempt_id)
        if attempt["state"] == "paused":
            require_paused_safe(db, run_id, attempt)
            return attempt
        paused_run = active_run_pause(db, run_id)
        if attempt["state"] != "running" and not (paused_run and attempt["state"] == "reserved"):
            raise LoopError("only a running attempt can acknowledge a pause")
        require_paused_safe(db, run_id, attempt)
        acknowledge_run_pause(db, run_id, attempt, host_ref)
        if attempt["state"] == "reserved":
            finish_operation(db, run_id, attempt["operation_id"], {
                "attempt_id": attempt_id, "paused_before_start": True,
            })
        db.execute("UPDATE attempts SET state = 'paused' WHERE id = ?", (attempt_id,))
        db.execute("UPDATE tasks SET status = 'paused' WHERE run_id = ? AND id = ?", (run_id, task["id"]))
        return get_attempt(db, attempt_id)

def fence_in(db, run_id, attempt_id, *, stopped=False):
    """Revoke one attempt without erasing its prior result."""
    from .codex_host import require_quiescent

    attempt = get_attempt(db, attempt_id)
    if attempt["run_id"] != run_id:
        raise LoopError(f"attempt belongs to another run: {attempt_id}")
    if attempt["state"] == "fenced":
        return attempt
    if attempt["state"] in RUNNING_STATES and not stopped:
        raise LoopError("confirm the worker is stopped or isolated before fencing a live attempt")
    if not (attempt["result"] or {}).get("codex_execution", {}).get("tool_proof"):
        require_quiescent(db, run_id, attempt, stopped=stopped, isolated=True)
    if attempt["state"] == "reserved":
        finish_operation(db, run_id, attempt["operation_id"], {"attempt_id": attempt_id, "fenced": True})
    db.execute("UPDATE attempts SET state = 'fenced' WHERE id = ?", (attempt_id,))
    db.execute(
        "UPDATE tasks SET status = ?, attempt_id = NULL WHERE run_id = ? AND id = ? AND attempt_id = ?",
        ("pending" if attempt["input"]["phase"] == "design" else "planned", run_id, attempt["task_id"], attempt_id),
    )
    return get_attempt(db, attempt_id)


def fence(store, run_id, epoch, attempt_id, *, stopped=False, host_ref=None):
    from .contracts import _affected_tasks, validate_integratable
    from .lifecycle import acknowledge_run_pause, require_reconciled

    with store.transaction() as db:
        run = guard(db, run_id, epoch, allow_paused=True)
        paused = active_run_pause(db, run_id)
        if not paused:
            require_reconciled(store, db, run)
        previous = get_attempt(db, attempt_id)
        if previous["run_id"] != run_id:
            raise LoopError(f"attempt belongs to another run: {attempt_id}")
        task = get_task(db, run_id, previous["task_id"])
        was_integrated = task["status"] == "integrated" and task["attempt_id"] == attempt_id
        if paused:
            if previous["state"] in RUNNING_STATES and stopped:
                acknowledge_run_pause(db, run_id, previous, host_ref, stopped=False)
            elif previous["state"] not in {"paused", "fenced"}:
                raise LoopError("整轮暂停期间只能隔离在途或已暂停的尝试")
        attempt = fence_in(db, run_id, attempt_id, stopped=stopped)
        if was_integrated:
            rows = db.execute("SELECT * FROM tasks WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
            affected = _affected_tasks(rows, {}, {task["id"]}) - {task["id"]}
            for row in rows:
                if row["id"] not in affected or not row["attempt_id"]:
                    continue
                dependent = get_attempt(db, row["attempt_id"])
                if dependent["input"]["phase"] != "implement":
                    continue
                if dependent["state"] in RUNNING_STATES:
                    raise LoopError(f"先暂停依赖任务 {row['id']} 的在途尝试 {dependent['id']}")
                fence_in(db, run_id, dependent["id"])
        if not paused:
            validate_integratable(db, run_id)
        return attempt


def _validate_result(attempt, result):
    if not isinstance(result, dict):
        raise LoopError("attempt result must be an object")
    if attempt["input"]["phase"] == "design":
        if _design_summary(result) is None:
            raise LoopError("设计尝试须提交非空的设计交接摘要")
        return
    if not isinstance(result.get("commit"), str) or not result["commit"]:
        raise LoopError("implementation requires a captured commit")
    if result.get("base") != attempt["input"]["candidate"] or result.get("actor") != attempt["worker"]:
        raise LoopError("result base or actor does not match the attempt")
    paths = result.get("paths")
    if not isinstance(paths, list):
        raise LoopError("implementation result must include its actual changed paths")
    prior = result.get("revalidation_of")
    if "revalidation_ledger" in result and (
        not prior or not isinstance(result["revalidation_ledger"], str) or not result["revalidation_ledger"]
    ):
        raise LoopError("revalidation ledger requires a prior integrated attempt")
    if prior is not None:
        if not isinstance(prior, str) or not prior or paths or result["commit"] != result["base"]:
            raise LoopError("revalidation requires an unchanged base and a prior integrated attempt")
    elif not paths:
        raise LoopError("implementation requires changed paths or an explicit revalidation")
    for path in paths:
        if not path_in_scope(path, attempt["input"]["spec"]["paths"]):
            raise LoopError(f"result contains a path outside the task scope: {path}")


def submit(store, run_id, epoch, attempt_id, result, *, capture=None):
    from .codex_host import require_quiescent

    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        attempt, task = current_attempt(db, run_id, attempt_id)
        require_bound_requirements(db, run, attempt)
        if attempt["state"] in {"submitted", "completed"}:
            if capture:
                result = {**result, **capture(False)}
            if {key: value for key, value in attempt["result"].items() if key != "codex_execution"} != result:
                raise LoopError("attempt already has a different result")
            return attempt
        if attempt["state"] != "running":
            raise LoopError("only the running attempt may submit a result")
        require_workspace_available(db, run_id, attempt_id)
        ensure_current_inputs(db, run_id, attempt)
        execution = require_quiescent(db, run_id, attempt, completed=True)
        if capture:
            result = {**result, **capture(execution is not None)}
        _validate_result(attempt, result)
        if execution:
            result = {**result, "codex_execution": execution}
        elif "codex_execution" in result:
            raise LoopError("Codex 执行回执须由 RTL 核对宿主后登记")
        design = attempt["input"]["phase"] == "design"
        db.execute(
            "UPDATE attempts SET state = ?, result = ? WHERE id = ?",
            ("completed" if design else "submitted", pack(result), attempt_id),
        )
        db.execute(
            "UPDATE tasks SET status = ? WHERE run_id = ? AND id = ?",
            ("planned" if design else "submitted", run_id, task["id"]),
        )
        return get_attempt(db, attempt_id)
