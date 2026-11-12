"""Describe possible next commands from a single read-only ledger snapshot."""

from shlex import join

from .next_delivery import delivery_actions
from .next_recovery import contract_actions, recovery_actions


def _delivery_allows_recovery_guidance(snapshot, actions):
    # An obsolete landing needs review while the current tasks continue. An
    # unresolved acceptance or rollback still takes priority over those tasks.
    return all(action["kind"] == "s4_review_landing" for action in actions) and not any(
        op["status"] == "intent" and op["kind"] in {"acceptance", "landing-rollback"}
        for op in snapshot["operations"]
    )


def summarize(snapshot, data_root):
    run_id = snapshot["id"]
    active = snapshot["status"] == "active"
    pause = snapshot["pause"]
    prefix = ["python3", "-m", "loop", "--data", str(data_root)]
    if active and pause:
        actions = recovery_actions(snapshot, prefix)
    else:
        actions = delivery_actions(snapshot, prefix)
        if active and _delivery_allows_recovery_guidance(snapshot, actions):
            actions = recovery_actions(snapshot, prefix) + contract_actions(snapshot, prefix) + actions

    if active and not actions:
        actions = [{
            "kind": "requirements_check",
            "reason": "台账没有待对账的恢复或交付操作。继续实施前可核对当前需求、冻结包及任务登记。",
            "argv": [*prefix, "requirements-check", run_id],
        }]
    if active:
        actions.append({
            "kind": "takeover",
            "reason": "新主控可用台账中的当前代次登记接管。",
            "checks": ["确认确需更换主控并核对目标运行。接管后使用返回的新代次重新查询引导。"],
            "argv": [*prefix, "takeover", run_id, "--epoch", str(snapshot["epoch"])],
        })
    return {
        "id": run_id,
        "epoch": snapshot["epoch"],
        "status": snapshot["status"],
        "candidate": snapshot["candidate"],
        **({"pause_id": pause["pause_id"]} if pause else {}),
        "next_actions": [
            {**action, "command": join(action["argv"])} if action.get("argv") else action
            for action in actions
        ],
    }
