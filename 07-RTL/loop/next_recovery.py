"""Read-only recovery and contract guidance from a single status snapshot."""

from .contract_history import activation_id, first_activation_basis
from .dispatch import SLOT_LIMIT
from .state import scopes_overlap


def _action(snapshot, prefix, kind, reason, *, command=(), write=False, checks=(), **references):
    result = {"kind": kind, "reason": reason, **references}
    if checks:
        result["checks"] = list(checks)
    if command:
        result["argv"] = [*prefix, command[0], snapshot["id"], *command[1:]]
        if write:
            result["argv"].extend(("--epoch", str(snapshot["epoch"])))
    return result


def _attempt_refs(attempt):
    refs = {"attempt_id": attempt["id"], "task_id": attempt["task_id"]}
    if attempt.get("operation_id"):
        refs["operation_id"] = attempt["operation_id"]
    return refs


def _operations_by_attempt(snapshot, kind):
    return {
        op["payload"]["attempt_id"]: op for op in snapshot["operations"]
        if op["kind"] == kind and op["status"] != "superseded" and op["payload"].get("attempt_id")
    }


def _host_check(snapshot, prefix, attempt, bindings, *, stopping=False):
    refs = _attempt_refs(attempt)
    if attempt.get("host_ref"):
        refs["host_ref"] = attempt["host_ref"]
    if attempt.get("workspace"):
        refs["workspace"] = attempt["workspace"]
    if attempt["id"] in bindings:
        binding = bindings[attempt["id"]]
        receipt = binding.get("receipt") or {}
        if socket := binding["payload"].get("socket"):
            refs["socket"] = socket
        if thread := receipt.get("thread_id"):
            refs["thread_id"] = thread
        turns = [op for op in snapshot["operations"]
                 if op["kind"] == "codex-turn" and op["payload"].get("attempt_id") == attempt["id"]]
        if turns:
            op = next((item for item in reversed(turns) if item["payload"].get("dispatch_id") ==
                       attempt.get("operation_id")), turns[-1])
            refs["turn_operation_id"] = op["id"]
            if turn := (op.get("receipt") or {}).get("turn_id"):
                refs["turn_id"] = turn
        checks = ["核对原宿主中的线程、轮次和工作区，再对照本次派发意图。"]
        if not thread:
            checks.append("从原宿主查找同工作区的空闲持久线程，取得真实线程编号后才可补登记。")
        if stopping:
            checks.append("取得原轮次终态和实际工具停工证据，或确认已完成工作区隔离。")
        return _action(
            snapshot, prefix, "check-codex", "台账缺少已确认的宿主停工证据。" if stopping
            else "核对原 Codex 尝试的实际执行与回执。",
            command=("codex-status", attempt["id"]), checks=checks, **refs,
        )
    checks = ["在原宿主核对该尝试、执行者、宿主引用和工作区。"]
    if stopping:
        checks.append("取得实际停工或隔离证据后，再登记暂停。")
    else:
        checks.append("确认原执行是否已启动。取得原执行者和真实宿主回执后，再补登记或准备原尝试。")
    return _action(
        snapshot, prefix, "check-worker", "原宿主的状态仍待核对。", checks=checks, **refs,
    )


def _isolation_retry(snapshot, prefix, attempt, operation):
    reason = operation["payload"].get("reason")
    return _action(
        snapshot, prefix, "retry-codex-isolation", "原 Codex 尝试已有隔离操作，请核对并补齐原操作。",
        command=("fence", attempt["id"], "--isolate-codex", reason) if reason else (), write=True,
        checks=("核对原工作区、隔离目录和已有回执，沿用已登记的隔离决定。",),
        **{**_attempt_refs(attempt), "operation_id": operation["id"]},
    )


def _claim_blockers(snapshot, task, phase, *, attempt=None):
    if not snapshot.get("candidate"):
        return ["先核对本轮基础候选。"]
    for op in snapshot["operations"]:
        if op["status"] == "intent" and op["kind"] in {"integration-rollback", "landing-rollback", "acceptance"}:
            return [f"先核对原操作 {op['id']} 的实际结果和待补回执。"]
    if snapshot["baseline"].get("requirements") is not None and not any(
        op["kind"] == "requirements-s1" and op["status"] == "done" for op in snapshot["operations"]
    ):
        return ["先用 requirements-check 核对 S1 冻结快照与四步交接回执。"]
    if sum(item["state"] in {"reserved", "running"} for item in snapshot["attempts"]) >= SLOT_LIMIT:
        return ["先核对在途尝试。确认并释放一个执行位后再领取。"]
    if phase != "implement":
        return []
    tasks = {item["id"]: item for item in snapshot["tasks"]}
    pending = [dep for dep in task["spec"].get("deps", []) if tasks.get(dep, {}).get("status") != "integrated"]
    edges = {row["edge"] for row in snapshot["contracts"] if row["active"]}
    missing = sorted(set(task["spec"].get("edges", [])) - edges)
    scope_holders = [
        item["id"] for item in snapshot["attempts"]
        if item["id"] != (attempt or {}).get("id") and item["state"] in {"reserved", "running", "paused"}
        and item["input"].get("phase") == "implement"
        and scopes_overlap(task["spec"].get("paths", []), item["input"].get("spec", {}).get("paths", []))
    ]
    checks = []
    if pending:
        checks.append("先核对尚未合入的依赖任务 " + "、".join(pending) + "。")
    if missing:
        checks.append("先核对尚未激活的契约边 " + "、".join(missing) + "。")
    if scope_holders:
        checks.append("先核对同一写入范围仍被占用的尝试 " + "、".join(scope_holders) + "。")
    return checks


def _design_replacements(snapshot):
    """Identify edges for which a pre-activation design lacks a current binding."""
    activations = {
        op["id"] for op in snapshot["operations"]
        if op["kind"] == "contract-activation" and op["status"] == "done"
    }
    replacements = {}
    for row in snapshot["contracts"]:
        if not row["active"]:
            continue
        edge, version = row["edge"], row["version"]
        initial = first_activation_basis(
            row,
            (other for other in snapshot["contracts"] if other["edge"] == edge and other["version"] != version),
            has_other_activation=any(
                value.startswith(activation_id(edge, "")) and value != activation_id(edge, version)
                for value in activations
            ),
        )
        if not initial:
            replacements[edge] = version
    return replacements


def _binding_changes(attempt, current, replacements):
    inputs = attempt["input"]
    bound = inputs.get("contracts", {})
    relevant = set(bound)
    if inputs.get("phase") == "implement":
        relevant.update(inputs.get("spec", {}).get("edges", []))
    changed = {edge: current.get(edge) for edge in sorted(relevant) if current.get(edge) != bound.get(edge)}
    if inputs.get("phase") == "design":
        unbound = set(inputs.get("spec", {}).get("edges", [])) - bound.keys()
        changed.update({edge: replacements[edge] for edge in sorted(unbound & replacements.keys())})
    return changed


def _stale_attempt(snapshot, prefix, attempt, changes):
    refs = {**_attempt_refs(attempt), "contracts": changes}
    if len(changes) == 1:
        refs["edge"], refs["version"] = next(iter(changes.items()))
    return _action(
        snapshot, prefix, "check-stale-attempt", "原尝试的契约依据与当前激活状态需要对账。",
        checks=("核对当前契约与原尝试。取得停工或隔离证据后处理原尝试，再按当前输入重派。",),
        **refs,
    )


def _resume_attempt(snapshot, prefix, task, attempt, bindings, operations, current, replacements):
    phase = attempt["input"].get("phase")
    stage = attempt["input"].get("dispatch_stage", "S2")
    if changes := _binding_changes(attempt, current, replacements):
        return _stale_attempt(snapshot, prefix, attempt, changes)
    has_codex_turn = any(op["kind"] == "codex-turn" and op["payload"].get("attempt_id") == attempt["id"]
                         for op in snapshot["operations"])
    if (attempt["id"] in bindings and has_codex_turn
            and operations.get(f"codex-stop:{attempt.get('operation_id')}", {}).get("status") != "done"):
        return _host_check(snapshot, prefix, attempt, bindings, stopping=True)
    if phase not in {"design", "implement"} or stage not in {"S2", "S3"}:
        return _action(snapshot, prefix, "check-resume", "原尝试的阶段信息待核对。",
                       checks=("核对原派发的阶段和执行类型。",), **_attempt_refs(attempt))
    checks = _claim_blockers(snapshot, task, phase, attempt=attempt)
    command = ("claim", task["id"], "--phase", phase, "--stage", stage) if not checks else ()
    return _action(
        snapshot, prefix, "resume-attempt", "暂停尝试可按原任务和阶段核对后重新领取。",
        command=command, write=True,
        checks=checks or ("核对原停工依据、冻结输入、上游批准和实际工作区，继续使用同一尝试。",),
        **_attempt_refs(attempt),
    )


def _paused_actions(snapshot, prefix, pause, bindings, isolations, operations):
    outstanding = set(pause["outstanding"])
    turns = _operations_by_attempt(snapshot, "codex-turn")
    pending = []
    for attempt in pause["attempts"]:
        if attempt["id"] in outstanding:
            if attempt["id"] in isolations:
                pending.append(_isolation_retry(snapshot, prefix, attempt, isolations[attempt["id"]]))
            else:
                pending.append(_host_check(snapshot, prefix, attempt, bindings, stopping=True))
        elif (
            attempt.get("host_state") == "previously_paused" and attempt["id"] in bindings and attempt["id"] in turns
            and operations.get(f"codex-stop:{attempt.get('operation_id')}", {}).get("status") != "done"
        ):
            pending.append(_host_check(snapshot, prefix, attempt, bindings, stopping=True))
    if pending:
        return [{**action, "pause_id": pause["pause_id"]} for action in pending]
    if outstanding:
        return [_action(
            snapshot, prefix, "check-pause", "整轮暂停仍有待核对的尝试。",
            checks=("从原暂停记录核对缺少停工回执的尝试 " + "、".join(sorted(outstanding)) + "。",),
            pause_id=pause["pause_id"], operation_id=pause["pause_id"],
        )]
    return [_action(
        snapshot, prefix, "resume-run", "原暂停记录已齐备停工或隔离回执。",
        command=("resume-run", pause["pause_id"]), write=True,
        checks=("收到继续请求后，核对原检查点、当前代次和实际产物。",),
        pause_id=pause["pause_id"], operation_id=pause["pause_id"],
    )]


def _integration_checks(snapshot):
    repository = snapshot.get("repository")
    if not repository:
        return []
    operations = [op for op in snapshot["operations"] if op["status"] == "intent"]
    pending_rollbacks = {
        op["payload"].get("integration_operation"): op for op in operations if op["kind"] == "integration-rollback"
    }
    actions = []
    for op in operations:
        if op["kind"] != "integration" or op["payload"].get("parent") != snapshot.get("candidate"):
            continue
        payload = op["payload"]
        rollback = pending_rollbacks.get(op["id"])
        action = {
            "kind": "check-integration", "operation_id": op["id"],
            "reason": "原合入已有撤销意图，需核对实际 Git 状态。" if rollback else "合入意图尚未结算。",
            "checks": ["对照原父版本、暂存候选和实际 Git 引用，再决定如何处理原合入操作。"],
            "argv": ["git", "-C", str(repository), "rev-parse", "--verify", "refs/rtl/candidate"],
        }
        action.update({key: payload[key] for key in ("parent", "candidate") if key in payload})
        if rollback:
            action["rollback_id"] = rollback["id"]
        actions.append(action)
    return actions


def recovery_actions(snapshot, prefix):
    if snapshot["status"] != "active":
        return []
    bindings = _operations_by_attempt(snapshot, "codex-thread")
    isolations = _operations_by_attempt(snapshot, "codex-isolation")
    operations = {op["id"]: op for op in snapshot["operations"]}
    pause = snapshot.get("pause")
    if pause:
        actions = _paused_actions(snapshot, prefix, pause, bindings, isolations, operations)
    else:
        tasks = {task["id"]: task for task in snapshot["tasks"]}
        current = {row["edge"]: row["version"] for row in snapshot["contracts"] if row["active"]}
        replacements = _design_replacements(snapshot)
        actions = _integration_checks(snapshot)
        for attempt in snapshot["attempts"]:
            task = tasks.get(attempt["task_id"])
            if not task or task.get("attempt_id") != attempt["id"]:
                continue
            if attempt["id"] in isolations and attempt["state"] in {"reserved", "running", "paused"}:
                actions.append(_isolation_retry(snapshot, prefix, attempt, isolations[attempt["id"]]))
            elif attempt["state"] == "paused":
                actions.append(_resume_attempt(snapshot, prefix, task, attempt, bindings, operations, current, replacements))
            elif attempt["state"] == "running" and attempt["id"] in bindings:
                actions.append(_host_check(snapshot, prefix, attempt, bindings))
            elif attempt["state"] == "reserved":
                if attempt.get("workspace") or attempt["id"] in bindings:
                    actions.append(_host_check(snapshot, prefix, attempt, bindings))
                elif not _binding_changes(attempt, current, replacements):
                    actions.append(_action(
                        snapshot, prefix, "prepare-attempt", "原尝试已预留，工作区尚未登记。",
                        command=("prepare-attempt", attempt["id"]), write=True,
                        checks=("核对原派发、冻结输入和原宿主，确认尚未启动再准备原工作区。",),
                        **_attempt_refs(attempt),
                    ))
    return actions


def _redispatch_action(snapshot, prefix, task, attempt, changed):
    phase = attempt["input"].get("phase")
    stage = attempt["input"].get("dispatch_stage", "S2")
    checks = _claim_blockers(snapshot, task, phase)
    if phase not in {"implement", "design"} or stage not in {"S2", "S3"}:
        checks.append("核对历史派发阶段和执行类型。")
    refs = {**_attempt_refs(attempt), "contracts": changed}
    if len(changed) == 1:
        refs["edge"], refs["version"] = next(iter(changed.items()))
    return _action(
        snapshot, prefix, "contract-redispatch", "原尝试已撤销，任务涉及现行契约或上游依赖的改版。",
        command=("claim", task["id"], "--phase", phase, "--stage", stage) if not checks else (),
        write=True, checks=checks or ("核对冻结输入、上游批准和实际候选，再按现行契约确认需重跑的证据。",), **refs,
    )


def contract_actions(snapshot, prefix):
    if snapshot["status"] != "active" or snapshot.get("pause"):
        return []
    contracts = [item for item in snapshot["contracts"] if item["active"]]
    current = {item["edge"]: item["version"] for item in contracts}
    latest = {attempt["task_id"]: attempt for attempt in snapshot["attempts"]}
    replacements = _design_replacements(snapshot)
    attempts = {attempt["id"]: attempt for attempt in snapshot["attempts"]}
    actions = []
    for task in snapshot["tasks"]:
        attempt = attempts.get(task.get("attempt_id"))
        # Integrated consumers may intentionally retain an older compatible binding.
        if task["status"] == "integrated" or not attempt or attempt["state"] not in {"reserved", "running", "submitted"}:
            continue
        if changed := _binding_changes(attempt, current, replacements):
            actions.append(_stale_attempt(snapshot, prefix, attempt, changed))
    fenced = {
        task["id"]: latest[task["id"]] for task in snapshot["tasks"]
        if task["status"] in {"pending", "planned"} and not task.get("attempt_id") and task["id"] in latest
        and latest[task["id"]]["state"] == "fenced"
    }
    impacted = {}
    for task_id, attempt in fenced.items():
        if changes := _binding_changes(attempt, current, replacements):
            impacted[task_id] = changes
    while True:
        expanded = False
        for task_id, attempt in fenced.items():
            if task_id in impacted:
                continue
            spec = attempt["input"].get("spec", {})
            predecessors = set(spec.get("deps", []))
            predecessors.update(row["provider"] for row in contracts if row["edge"] in spec.get("edges", []))
            changes = {edge: version for dep in sorted(predecessors & impacted.keys())
                       for edge, version in impacted[dep].items()}
            if changes:
                impacted[task_id] = changes
                expanded = True
        if not expanded:
            break
    actions.extend(_redispatch_action(snapshot, prefix, task, fenced[task["id"]], impacted[task["id"]])
                   for task in snapshot["tasks"] if task["id"] in impacted)
    return actions
