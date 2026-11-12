"""Resolve prior delivery artifacts from verified project history."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3

from .ledger_schema import project_rebind_rows, require_readable
from .requirements_history import _frozen_baseline, _history, _origin, _trace_fields, _trace_record
from .state import LoopError, get_operation, get_run, pack, safe_id


def history(db, project, project_id):
    return _history(db, {"project_root": str(project), "project_id": project_id})


@contextmanager
def read_ledger(path):
    ledger = Path(path)
    if not ledger.is_absolute() or ledger.name != "state.sqlite" or ledger.resolve() != ledger or not ledger.is_file():
        raise ValueError(f"原运行台账不可用，请先恢复：{ledger}")
    try:
        db = sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            require_readable(db)
            yield db
        finally:
            db.close()
    except sqlite3.Error as error:
        raise ValueError(f"无法读取原运行台账：{ledger}") from error


def _project_bindings(db, project_id):
    """Read the verified path chain backwards from the ledger's current binding."""
    registered = db.execute("SELECT root,output FROM projects WHERE id=?", (project_id,)).fetchone()
    if not registered:
        return None, set(), None
    current = (registered["root"], registered["output"])
    anchor = current
    previous = set()
    for row in project_rebind_rows(db, project_id):
        if (row["current_root"], row["current_output"]) != anchor:
            raise ValueError("项目迁移记录与原台账登记路径不连续")
        anchor = (row["previous_root"], row["previous_output"])
        previous.add(anchor)
    return current, previous, anchor


def _same_relocated_project_output(older, newer):
    """Recognize a pre-receipt move of the same project-relative output."""
    old_root, old_output = map(Path, older)
    new_root, new_output = map(Path, newer)
    return (
        old_root != new_root
        and old_output.is_relative_to(old_root) and new_output.is_relative_to(new_root)
        and old_output.relative_to(old_root) == new_output.relative_to(new_root)
    )


def previous_output_matches(db, prior_run, project, output):
    previous = (prior_run["project_root"], prior_run["output_path"])
    target = (str(project), str(output))
    if previous[1] == target[1]:
        return True
    current, moved, anchor = _project_bindings(db, prior_run["project_id"])
    return current == target and (previous in moved or _same_relocated_project_output(previous, anchor))


def _source_project_matches(db, run, project):
    if run["project_root"] == str(project):
        return True
    current, _, _ = _project_bindings(db, run["project_id"])
    return current is not None and current[0] == str(project)


def _delivery(db, record, project):
    from .delivery import _change_id
    from .frontend import COMMIT, profile

    run = get_run(db, record["run_id"])
    change_id = record["change_id"]
    operation_id = "delivery-" + change_id[4:]
    op = get_operation(db, run["id"], operation_id)
    if (
        run["status"] != "delivered" or run["project_id"] != record["project_id"]
        or not _source_project_matches(db, run, project) or _trace_record(run, change_id) != _trace_fields(record)
        or not op or op["kind"] != "delivery" or op["status"] != "done" or _change_id(op["id"]) != change_id
    ):
        raise ValueError(f"原交付回执与当前项目及需求历史不符：{change_id}")
    payload, receipt = op["payload"], op["receipt"] or {}
    output = Path(run["output_path"])
    candidate = run["candidate"]
    expected = payload.get("manifest")
    if (
        not isinstance(candidate, str) or not COMMIT.fullmatch(candidate)
        or payload.get("candidate") != candidate or receipt.get("candidate") != candidate
        or payload.get("operation_id") != operation_id or receipt.get("operation_id") != operation_id
        or receipt.get("change_id") != change_id or receipt.get("status") != "delivered"
        or not output.is_absolute() or payload.get("output") != str(output)
        or receipt.get("entry") != str(output / "index.html")
        or not isinstance(expected, dict) or "index.html" not in expected
    ):
        raise ValueError(f"原交付回执与已验收候选或落盘清单不符：{change_id}")
    if profile(run["baseline"]) == "react":
        build = receipt.get("build")
        if (
            not isinstance(build, dict) or payload.get("build") != build
            or build.get("source_commit") != candidate
            or build.get("artifact_manifest_sha256") != hashlib.sha256(pack(expected).encode()).hexdigest()
        ):
            raise ValueError(f"原 React 交付的构建摘要不符：{change_id}")
    return run, op


def find_record(records, run_id):
    matches = [record for record in records if record["run_id"] == run_id]
    if len(matches) > 1:
        raise ValueError(f"多份交付历史使用同一运行编号，无法确定源码来源：{run_id}")
    return next(iter(matches), None)


def profile_for_source(record, project):
    from .frontend import profile

    if not record["source_ledger"]:
        raise ValueError("旧交付未登记原运行台账，请使用原台账恢复后再继承源码")
    with read_ledger(record["source_ledger"]) as origin:
        run, _ = _delivery(origin, record, project)
        return profile(run["baseline"])


def latest_local_react_seed(db, project_id, *, before_run=None):
    from .frontend import profile
    from .state import unpack

    prior = ""
    values = [project_id]
    if before_run is not None:
        prior = " AND rowid < (SELECT rowid FROM runs WHERE id=?)"
        values.append(before_run)
    for row in db.execute(
        "SELECT id,candidate,baseline FROM runs WHERE project_id=? AND status='delivered'"
        + prior + " ORDER BY rowid DESC", values,
    ):
        if profile(unpack(row["baseline"])) == "react":
            if not row["candidate"]:
                raise ValueError("已验收的 React 源码候选缺失，请先恢复原运行")
            return {"source_run": row["id"], "candidate": row["candidate"]}
    return None


def select_seed(db, project, project_id, baseline, source_run):
    from .frontend import profile

    if source_run is not None:
        source_run = safe_id(source_run, "source run ID")
        exists = db.execute("SELECT 1 FROM runs WHERE id=? AND project_id=?", (source_run, project_id)).fetchone()
        if exists:
            previous = get_run(db, source_run)
            if (
                previous["status"] not in {"superseded", "delivered"}
                or not previous["candidate"] or profile(previous["baseline"]) != profile(baseline)
            ):
                raise LoopError("源码来源必须是同项目、同技术栈的已交付或已替代候选")
            return {"source_run": source_run, "candidate": previous["candidate"]}
        record = find_record(history(db, project, project_id), source_run)
        if record is None:
            raise LoopError(f"unknown run: {source_run}")
        return seed_for(record, project, baseline)
    local_source = project / "prototype-src"
    if profile(baseline) != "react" or local_source.exists() or local_source.is_symlink():
        return None
    if "requirements" in baseline:
        local = _origin(db)
        for record in history(db, project, project_id):
            if profile(_frozen_baseline(project, record)) != "react":
                continue
            if record["source_ledger"] == local:
                previous = get_run(db, record["run_id"])
                if not previous["candidate"]:
                    raise ValueError("已验收的 React 源码候选缺失，请先恢复原运行")
                return {"source_run": previous["id"], "candidate": previous["candidate"]}
            return seed_for(record, project, baseline)
    return latest_local_react_seed(db, project_id)


def seed_for(record, project, baseline):
    from .authorship import candidate_authors
    from .frontend import profile

    if not record["source_ledger"]:
        raise ValueError("旧交付未登记原运行台账，请使用原台账恢复后再继承源码")
    with read_ledger(record["source_ledger"]) as origin:
        run, operation = _delivery(origin, record, project)
        if profile(run["baseline"]) != profile(baseline):
            raise ValueError("源码来源必须是同项目、同技术栈的已交付或已替代候选")
        seed = {
            "source_ledger": record["source_ledger"], "source_run": run["id"],
            "source_change": record["change_id"], "candidate": run["candidate"],
            "source_authors": sorted(candidate_authors(origin, run["id"])),
        }
        _source_repository(origin, run, operation)
        return seed


def _source_repository(db, run, operation):
    from .artifacts import CANDIDATE_REF, rev
    from .delivery import _tree_manifest, manifest
    from .frontend import profile

    parent = Path(_origin(db)).parent / "projects" / safe_id(run["project_id"], "project ID") / "runs" / run["id"]
    repo = parent / "repository"
    metadata = repo / ".git"
    if (
        metadata.resolve() != metadata or not metadata.is_dir()
        or rev(repo, CANDIDATE_REF) != run["candidate"]
    ):
        raise ValueError(f"原运行的已验收源码仓库缺失或候选不符，请先恢复：{repo}")
    source = _tree_manifest(repo, run["candidate"])
    if profile(run["baseline"]) == "react":
        receipt = operation["receipt"]
        handoff = parent / "deliveries" / f"{operation['id']}-source"
        if receipt.get("source") != str(handoff) or handoff.resolve() != handoff or not handoff.is_dir():
            raise ValueError(f"原 React 源码交接缺失或路径不符，请先恢复：{handoff}")
        if (
            "package.json" not in source or not source.get("package-lock.json")
            or source["package-lock.json"] != receipt["build"].get("lockfile_sha256")
            or manifest(handoff) != source
        ):
            raise ValueError(f"原 React 源码交接与已验收提交不符：{handoff}")
    elif source != operation["payload"]["manifest"]:
        raise ValueError("原静态源码与已验收落盘清单不符")
    return repo


def seed_repository(db, run, seed):
    """Recheck a pinned origin when recovering an uninitialized new run."""
    project = Path(run["project_root"])
    records = history(db, project, run["project_id"])
    record = next((item for item in records if item["change_id"] == seed["source_change"]), None)
    if not record or (record["source_ledger"], record["run_id"]) != (seed["source_ledger"], seed["source_run"]):
        raise ValueError("继承的源码来源与已验证的需求变更历史不符")
    with read_ledger(record["source_ledger"]) as origin:
        previous, operation = _delivery(origin, record, project)
        if previous["candidate"] != seed["candidate"]:
            raise ValueError("原交付候选已改变，请先恢复")
        return _source_repository(origin, previous, operation)


def previous_manifest(db, run, output):
    """Find the newest verified delivery at this path or its recorded predecessor."""
    if "requirements" not in run["baseline"]:
        return None
    project = Path(run["project_root"])
    for record in history(db, project, run["project_id"]):
        ledger = record["source_ledger"]
        if ledger is None:
            continue
        if ledger == _origin(db):
            prior = get_run(db, record["run_id"])
            if prior["output_path"] == str(output):
                return None  # The local ledger also knows retained superseded output.
            if previous_output_matches(db, prior, project, output):
                _, op = _delivery(db, record, project)
                return op["payload"]["manifest"]
            continue
        with read_ledger(ledger) as origin:
            prior = get_run(origin, record["run_id"])
            if not previous_output_matches(origin, prior, project, output):
                continue
            _, op = _delivery(origin, record, project)
            return op["payload"]["manifest"]
    return None
