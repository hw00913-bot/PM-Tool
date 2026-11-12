"""SQLite state and durable operation receipts for the local RTL Loop."""

import json
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath
from typing import cast
from uuid import uuid4

from . import ledger_schema
from .payload_types import Baseline, OperationRecord, RunRecord


class LoopError(ValueError):
    """A blocked or invalid Loop operation."""


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unpack(value):
    return json.loads(value) if value is not None else None


def decode(row, *fields):
    result = dict(row)
    for field in fields:
        result[field] = unpack(result[field])
    return result


def safe_id(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise LoopError(f"{label} must be a portable identifier")
    return value


def relative_path(value):
    if not isinstance(value, str) or any(c in value for c in "\\\x00*?[]"):
        raise LoopError(f"invalid prototype-relative path: {value!r}")
    raw = value.rstrip("/")
    parts = raw.split("/")
    if not raw or any(part.casefold() in {"", ".", "..", ".git"} for part in parts):
        raise LoopError(f"invalid prototype-relative path: {value!r}")
    return PurePosixPath(raw)


def path_in_scope(path, scopes):
    candidate = relative_path(path)
    for scope in scopes:
        if candidate.is_relative_to(relative_path(scope)):
            return True
    return False


def scopes_overlap(left, right):
    for left_path in left:
        for right_path in right:
            if overlaps(relative_path(left_path), relative_path(right_path)):
                return True
    return False


def canonical_directory(value, label):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise LoopError(f"{label} must be an absolute path")
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise LoopError(f"{label} is not a directory: {path}")
    return path


def location_key(value):
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in Path(value).parts)


def overlaps(left, right):
    left, right = location_key(left), location_key(right)
    return left == right[:len(left)] or right == left[:len(right)]


RUN_QUERY = "SELECT runs.* FROM runs"


def get_run(db, run_id) -> RunRecord:
    row = db.execute(RUN_QUERY + " WHERE runs.id = ?", (run_id,)).fetchone()
    if row is None:
        raise LoopError(f"unknown run: {run_id}")
    return cast(RunRecord, decode(row, "baseline"))


def active_run_pause(db, run_id):
    row = db.execute(
        "SELECT id FROM operations WHERE run_id=? AND kind='run-pause' AND status='intent'",
        (run_id,),
    ).fetchone()
    return get_operation(db, run_id, row["id"]) if row else None


def guard(db, run_id, epoch, *, allow_paused=False) -> RunRecord:
    run = get_run(db, run_id)
    if run["status"] != "active":
        raise LoopError(f"run is {run['status']}: {run_id}")
    if type(epoch) is not int or run["epoch"] != epoch:
        raise LoopError(f"stale controller epoch for {run_id}; current epoch is {run['epoch']}")
    if not allow_paused:
        pause = active_run_pause(db, run_id)
        if pause:
            raise LoopError(f"本轮已请求暂停，请先核对停工并执行 resume-run：{pause['id']}")
    return run


def get_operation(db, run_id, operation_id) -> OperationRecord | None:
    row = db.execute(
        "SELECT * FROM operations WHERE run_id = ? AND id = ?", (run_id, operation_id)
    ).fetchone()
    return cast(OperationRecord, decode(row, "payload", "receipt")) if row else None


def begin_operation(db, run_id, operation_id, kind, payload):
    current = get_operation(db, run_id, operation_id)
    if current:
        if current["kind"] != kind or current["payload"] != payload:
            raise LoopError(f"operation {operation_id} already records a different intent")
        return current
    db.execute(
        "INSERT INTO operations (id, run_id, kind, payload) VALUES (?, ?, ?, ?)",
        (operation_id, run_id, kind, pack(payload)),
    )
    return get_operation(db, run_id, operation_id)


def finish_operation(db, run_id, operation_id, receipt):
    current = get_operation(db, run_id, operation_id)
    if current is None:
        raise LoopError(f"operation intent is missing: {operation_id}")
    if current["status"] == "done":
        if current["receipt"] != receipt:
            raise LoopError(f"operation {operation_id} already has a different receipt")
        return current
    db.execute(
        "UPDATE operations SET status = 'done', receipt = ? WHERE run_id = ? AND id = ?",
        (pack(receipt), run_id, operation_id),
    )
    return get_operation(db, run_id, operation_id)


def get_attempt(db, attempt_id):
    row = db.execute("SELECT * FROM attempts WHERE id = ?", (attempt_id,)).fetchone()
    if row is None:
        raise LoopError(f"unknown attempt: {attempt_id}")
    return decode(row, "input", "result")


def get_task(db, run_id, task_id):
    row = db.execute(
        "SELECT * FROM tasks WHERE run_id = ? AND id = ?", (run_id, task_id)
    ).fetchone()
    if row is None:
        raise LoopError(f"unknown task in {run_id}: {task_id}")
    return decode(row, "spec")


def normalize_task(spec):
    if not isinstance(spec, dict):
        raise LoopError("task spec must be an object")
    result = dict(spec)
    safe_id(result.get("id"), "task ID")
    paths = result.get("paths")
    if not isinstance(paths, list) or not paths:
        raise LoopError("task paths must be a nonempty list")
    result["paths"] = list(dict.fromkeys(str(relative_path(path)) for path in paths))
    for field in ("deps", "edges", "cases"):
        values = result.get(field, [])
        if not isinstance(values, list):
            raise LoopError(f"task {field} must be a list of identifiers")
        result[field] = list(dict.fromkeys(safe_id(value, field) for value in values))
    if type(result.get("risk", False)) is not bool:
        raise LoopError("task risk must be true or false")
    result.setdefault("risk", False)
    return result


def check_task_cycle(db, run_id, spec):
    dependencies = {
        row["id"]: unpack(row["spec"])["deps"]
        for row in db.execute("SELECT id, spec FROM tasks WHERE run_id = ?", (run_id,))
    }
    pending = list(spec["deps"])
    visited = set()
    while pending:
        task_id = pending.pop()
        if task_id == spec["id"]:
            raise LoopError(f"cyclic task dependency: {task_id}")
        if task_id not in visited:
            visited.add(task_id)
            pending.extend(dependencies.get(task_id, ()))


def check_paths(db, data_root, project, output, project_id, *, continuing=None):
    project_key, output_key = location_key(project), location_key(output)
    if output_key == project_key[:len(output_key)]:
        raise LoopError("output must be a dedicated prototype directory, not the project root or its parent")
    if overlaps(data_root, project) or overlaps(data_root, output):
        raise LoopError("Loop data must be outside the original project and output")
    for row in db.execute(RUN_QUERY + " WHERE runs.status = 'active'"):
        if row["id"] == continuing:
            continue
        if row["project_id"] == project_id:
            raise LoopError(f"project has active run {row['id']}; resume that run")
        roots = (row["project_root"], row["output_path"])
        if any(overlaps(new, existing) for new in (project, output) for existing in roots):
            raise LoopError(f"project/output overlaps active run {row['id']}")


class Store:
    def __init__(self, data_root, *, reserve=True, reservation_root=None):
        from .reservations import reservation_root as default_reservation_root, reservations

        root = Path(reservation_root).expanduser() if reservation_root is not None else default_reservation_root()
        self.reservation_root = root.resolve()
        self.data_root = Path(data_root).expanduser().resolve()
        self.root = self.data_root
        self.path = self.data_root / "state.sqlite"
        if not reserve:
            return
        self._check_registry_paths(self.data_root)
        with reservations(self.reservation_root) as registry:
            registry.check(self.path, None, None, ())
            self._preflight_legacy_reservations(registry)
            self.data_root.mkdir(parents=True, exist_ok=True)
            with self.transaction(create=True):
                pass
            self._adopt_reservations(registry)

    def _check_registry_paths(self, *paths):
        if any(overlaps(self.reservation_root, path) for path in paths):
            raise LoopError("project, output, and ledger paths must stay outside the RTL reservation directory")

    def _preflight_legacy_reservations(self, registry):
        if not self.path.is_file() or not self.path.stat().st_size:
            return
        with self.transaction(immediate=False) as db:
            if ledger_schema.require_readable(db) != 0 or not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone():
                return
            for run in db.execute(
                "SELECT id,project_id,project_root,output_path FROM runs WHERE status='active'"
            ):
                paths = (run["project_root"], run["output_path"])
                self._check_registry_paths(*paths)
                registry.check(self.path, run["id"], run["project_id"], paths)

    def _adopt_reservations(self, registry):
        query = RUN_QUERY + " WHERE status = 'active'"
        with self.transaction(immediate=False) as db:
            active = db.execute(query).fetchall()
        for run in active:
            self._check_registry_paths(run["project_root"], run["output_path"])
            registry.adopt(self.path, run)

    def release_reservation(self, run_id):
        """Release this ledger's exact reservation after a terminal commit."""
        from .reservations import reservations

        try:
            (self.reservation_root / "reservations.sqlite").stat()
        except FileNotFoundError:
            return
        if self.run(run_id)["status"] not in {"delivered", "superseded"}:
            return
        with reservations(self.reservation_root) as registry:
            registry.settle(self.path, run_id)

    @contextmanager
    def transaction(self, *, immediate=True, create=False):
        if not create and not self.path.exists():
            raise LoopError(f"no RTL ledger at {self.data_root}")
        mode = "rwc" if create else ("rw" if immediate else "ro")
        connection = (
            ledger_schema.readonly_connection(self.path) if mode == "ro" else closing(sqlite3.connect(
                self.path.as_uri() + f"?mode={mode}", uri=True, timeout=15, isolation_level=None,
            ))
        )
        with connection as db:
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA foreign_keys = ON")
                db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                if immediate:
                    ledger_schema.ensure_current(db)
                else:
                    ledger_schema.require_readable(db)
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def create_run(
        self, project_root, output_path, baseline: Baseline, *, project_id=None, run_id=None, source_run=None,
    ) -> RunRecord:
        from .managed_paths import check_output_path
        from .requirements_run import validate_revision
        from .reservations import reservations
        from .revision_registry import record_revision, validate_registry

        project = canonical_directory(project_root, "project path")
        output = canonical_directory(output_path, "output path")
        check_output_path(project, output)
        self._check_registry_paths(project, output)
        if not isinstance(baseline, dict):
            raise LoopError("baseline must be an object")
        run_id = safe_id(run_id or uuid4().hex, "run ID")
        with reservations(self.reservation_root) as registry:
            with self.transaction() as db:
                existing = next((row for row in db.execute("SELECT * FROM projects")
                                 if location_key(row["root"]) == location_key(project)), None)
                if existing is not None and project_id not in {None, existing["id"]}:
                    raise LoopError(f"project path is already registered as {existing['id']}")
                project_id = safe_id(project_id or (existing["id"] if existing else uuid4().hex), "project ID")
                registered = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
                if registered is not None and location_key(registered["root"]) != location_key(project):
                    raise LoopError(f"project ID {project_id} belongs to another path; rebind or fork explicitly")
                if registered is not None and location_key(registered["output"]) != location_key(output):
                    raise LoopError(f"project ID {project_id} uses another output; rebind explicitly")
                check_paths(db, self.data_root, project, output, project_id)
                if db.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
                    raise LoopError(f"run ID already exists: {run_id}")
                baseline = validate_revision(db, project_id, baseline, project=project)
                validate_registry(project, project_id, baseline, run_id=run_id, ledger=self.path)
                from .delivery_origin import select_seed

                seed = select_seed(db, project, project_id, baseline, source_run)
                registry.prepare(self.path, run_id, project_id, (str(project), str(output)))
                record_revision(project, project_id, baseline, run_id, ledger=self.path)
                if registered is None:
                    db.execute(
                        "INSERT INTO projects (id, root, output) VALUES (?, ?, ?)",
                        (project_id, str(project), str(output)),
                    )
                db.execute(
                    "INSERT INTO runs (id, project_id, project_root, output_path, baseline) VALUES (?, ?, ?, ?, ?)",
                    (run_id, project_id, str(project), str(output), pack(baseline)),
                )
                if seed:
                    begin_operation(db, run_id, "seed-from", "seed-from", seed)
                    finish_operation(db, run_id, "seed-from", seed)
                result = get_run(db, run_id)
            registry.settle(self.path, run_id)
            return result

    def run(self, run_id) -> RunRecord:
        with self.transaction(immediate=False) as db:
            return get_run(db, run_id)

    def runs(self) -> list[RunRecord]:
        if not self.path.exists():
            return []
        with self.transaction(immediate=False) as db:
            return [cast(RunRecord, decode(row, "baseline")) for row in db.execute(RUN_QUERY + " ORDER BY runs.rowid")]

    def take_over(self, run_id, epoch):
        with self.transaction() as db:
            guard(db, run_id, epoch, allow_paused=True)
            db.execute("UPDATE runs SET epoch = epoch + 1 WHERE id = ?", (run_id,))
            return get_run(db, run_id)

    def rebind_project(self, project_id, project_root, output_path, *, epoch=None):
        """Confirm a new path explicitly while preserving completed runs' original paths."""
        from .lifecycle import require_reconciled
        from .managed_paths import check_output_path
        from .reservations import reservations

        project = canonical_directory(project_root, "project path")
        output = canonical_directory(output_path, "output path")
        check_output_path(project, output)
        self._check_registry_paths(project, output)
        with reservations(self.reservation_root) as registry:
            with self.transaction() as db:
                registered = db.execute("SELECT root,output FROM projects WHERE id = ?", (project_id,)).fetchone()
                if registered is None:
                    raise LoopError(f"unknown project: {project_id}")
                for row in db.execute("SELECT * FROM projects WHERE id <> ?", (project_id,)):
                    if location_key(row["root"]) == location_key(project):
                        raise LoopError(f"project path is already registered as {row['id']}")
                active = db.execute(
                    "SELECT id FROM runs WHERE project_id = ? AND status = 'active'", (project_id,)
                ).fetchone()
                run_id = active["id"] if active else None
                if run_id is not None:
                    require_reconciled(self, db, guard(db, run_id, epoch))
                    if db.execute(
                        "SELECT 1 FROM operations WHERE run_id = ? AND kind = 'delivery' AND status = 'intent'",
                        (run_id,),
                    ).fetchone():
                        raise LoopError("reconcile the pending delivery before changing its path")
                check_paths(db, self.data_root, project, output, project_id, continuing=run_id)
                paths = (str(project), str(output))
                if run_id is None:
                    registry.check(self.path, None, project_id, paths)
                else:
                    registry.prepare(self.path, run_id, project_id, paths)
                previous = (registered["root"], registered["output"])
                if previous != paths:
                    db.execute(
                        "INSERT INTO project_rebinds(project_id,previous_root,previous_output,current_root,current_output) "
                        "VALUES (?,?,?,?,?)",
                        (project_id, *previous, *paths),
                    )
                    db.execute("UPDATE projects SET root = ?, output = ? WHERE id = ?", (*paths, project_id))
                if run_id is not None:
                    db.execute(
                        "UPDATE runs SET project_root = ?, output_path = ? WHERE id = ?",
                        (str(project), str(output), run_id),
                    )
            if run_id is not None:
                registry.settle(self.path, run_id)
            return {"project_id": project_id, "project_root": str(project), "output_path": str(output)}

    def add_task(self, run_id, epoch, spec):
        from .contracts import validate_integratable
        from .lifecycle import require_reconciled

        spec = normalize_task(spec)
        with self.transaction() as db:
            run = guard(db, run_id, epoch)
            plan = run["baseline"].get("requirements")
            if plan is not None:
                from .requirements import normalize_plan_task

                spec = normalize_plan_task(spec, plan)
                approved = next((item for item in plan["tasks"] if item["id"] == spec["id"]), None)
                if approved is not None and approved != spec:
                    raise LoopError(f"任务 {spec['id']} 与已确认的 S1 任务包不符")
            existing = db.execute(
                "SELECT spec FROM tasks WHERE run_id = ? AND id = ?", (run_id, spec["id"])
            ).fetchone()
            if existing is not None:
                if unpack(existing["spec"]) != spec:
                    raise LoopError(f"task already registered with another spec: {spec['id']}")
                return get_task(db, run_id, spec["id"])
            require_reconciled(self, db, run)
            check_task_cycle(db, run_id, spec)
            validate_integratable(db, run_id, task_spec=spec)
            refresh = False
            if plan is not None:
                from .requirements_run import S1_OPERATION, require_prepared

                preparation = get_operation(db, run_id, S1_OPERATION)
                refresh = preparation is not None and preparation["status"] == "done"
                if refresh:
                    require_prepared(db, run)
            db.execute(
                "INSERT INTO tasks (run_id, id, spec) VALUES (?, ?, ?)",
                (run_id, spec["id"], pack(spec)),
            )
            if refresh:
                from .requirements_history import write_ledger

                write_ledger(self, db, run)
            return get_task(db, run_id, spec["id"])

    def tasks(self, run_id):
        with self.transaction(immediate=False) as db:
            get_run(db, run_id)
            return [
                decode(row, "spec")
                for row in db.execute("SELECT * FROM tasks WHERE run_id = ? ORDER BY rowid", (run_id,))
            ]

    def attempt(self, attempt_id):
        with self.transaction(immediate=False) as db:
            return get_attempt(db, attempt_id)

    def attempts(self, run_id):
        with self.transaction(immediate=False) as db:
            get_run(db, run_id)
            return [
                decode(row, "input", "result")
                for row in db.execute("SELECT * FROM attempts WHERE run_id = ? ORDER BY rowid", (run_id,))
            ]

    def operation(self, run_id, operation_id):
        with self.transaction(immediate=False) as db:
            return get_operation(db, run_id, operation_id)

    def operations(self, run_id):
        with self.transaction(immediate=False) as db:
            get_run(db, run_id)
            return [
                decode(row, "payload", "receipt")
                for row in db.execute("SELECT * FROM operations WHERE run_id = ? ORDER BY rowid", (run_id,))
            ]
