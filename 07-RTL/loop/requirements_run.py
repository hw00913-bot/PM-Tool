"""Bind the approved S1 requirements and task plan to a run."""

import hashlib
import json
from pathlib import Path

from .requirements_revision import validate_revision
from .state import LoopError, begin_operation, finish_operation, get_operation, get_run, guard, pack, safe_id, unpack


S1_OPERATION = "requirements-s1"


def snapshot_relative_path(run_id):
    return f"memory/baselines/rtl-s1-{safe_id(run_id, 'run ID')}.json"


def _snapshot(run):
    return json.dumps(run["baseline"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _snapshot_target(run):
    from .delivery import _safe_project_child

    path = _safe_project_child(Path(run["project_root"]), snapshot_relative_path(run["id"]))
    if path.is_relative_to(Path(run["output_path"])):
        raise LoopError("S1 冻结基线须位于原型落盘目录之外")
    return path


def check_snapshot_destination(run, *, unused_run=False):
    path = _snapshot_target(run)
    if path.parent.exists() and not path.parent.is_dir():
        raise LoopError(f"S1 冻结基线目录不是普通目录：{path.parent}")
    if path.exists() and (unused_run or not path.is_file() or path.read_bytes() != _snapshot(run).encode()):
        raise LoopError(f"已有 S1 冻结基线与本轮不符：{path}")


def _binding(baseline):
    plan = baseline["requirements"]
    expected = {
        "revision": baseline["revision"],
        "plan_sha256": hashlib.sha256(pack(plan).encode()).hexdigest(),
        "tasks": sorted(task["id"] for task in plan["tasks"]),
    }
    origin = _analysis_origin(baseline)
    if origin is not None:
        expected["analysis_origin"] = origin
    return expected


def _analysis_origin(baseline):
    review = baseline.get("s1_review") or {}
    return (review.get("confirmation") or {}).get("analysis_origin")


def require_current_analysis(run):
    origin = _analysis_origin(run["baseline"])
    if origin is not None and run["status"] == "active":
        from .upstream_analysis import require_current

        require_current(origin, include_facts=False)


def require_prepared(db, run):
    baseline = run["baseline"]
    plan = baseline.get("requirements")
    if plan is None:
        return None
    operation = get_operation(db, run["id"], S1_OPERATION)
    expected = _binding(baseline)
    if not operation or operation["status"] != "done" or operation["receipt"] != expected:
        raise LoopError("S1 需求契约及四步交接尚未登记完成，请执行 prepare")
    registered = {
        row["id"]: unpack(row["spec"])
        for row in db.execute("SELECT id,spec FROM tasks WHERE run_id=?", (run["id"],))
    }
    missing = [task["id"] for task in plan["tasks"] if registered.get(task["id"]) != task]
    if missing:
        raise LoopError(f"S1 已确认的任务包缺失或不一致：{', '.join(sorted(missing))}")
    snapshot = _snapshot_target(run)
    if not snapshot.is_file() or snapshot.read_bytes() != _snapshot(run).encode():
        raise LoopError("S1 冻结基线文件缺失或与已确认台账不符，请核对后执行 prepare")
    require_current_analysis(run)
    from .feature_map import check_planned_references
    from .requirements_history import check_current_ledger

    ledger = check_current_ledger(db, run)
    planned_map = check_planned_references(run, registered)
    return {
        **expected, "baseline": str(snapshot), "ledger": str(ledger),
        "registered_tasks": sorted(registered), "planned_map": planned_map,
    }


def prepare(store, run_id, epoch):
    from .delivery import _reconcile_text
    from .requirements_history import check_ledger_destination, write_ledger

    run = store.run(run_id)
    plan = run["baseline"].get("requirements")
    if plan is None:
        return None
    require_current_analysis(run)
    check_ledger_destination(run)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        expected = _binding(run["baseline"])
        begin_operation(db, run_id, S1_OPERATION, "requirements-s1", expected)
    for task in plan["tasks"]:
        store.add_task(run_id, epoch, task)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        _reconcile_text(_snapshot_target(run), _snapshot(run), (), "已落盘的 S1 冻结基线与原决定不符")
        write_ledger(store, db, run)
        finish_operation(db, run_id, S1_OPERATION, expected)
        return require_prepared(db, run)


def check(store, run_id):
    with store.transaction(immediate=False) as db:
        run = get_run(db, run_id)
        return require_prepared(db, run) or {"status": "legacy", "revision": run["baseline"].get("revision")}
