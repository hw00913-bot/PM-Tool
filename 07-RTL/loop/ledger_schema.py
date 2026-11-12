"""Versioned SQLite layout for the RTL ledger.

Schema upgrades leave operation, evidence and baseline JSON as originally stored.
"""

from contextlib import closing, contextmanager
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile


class LedgerSchemaError(ValueError):
    """The SQLite file does not have a supported RTL ledger layout."""


class _LedgerReadConnection(sqlite3.Connection):
    """Keep ledger identity separate from the path used for a private read."""


def _file_stamp(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@contextmanager
def _readonly_path(path):
    with path.open("rb") as stream:
        header = stream.read(20)
    # SQLite file header write/read versions are both 2 for a WAL ledger.
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x02\x02":
        yield path
        return

    wal = path.with_name(path.name + "-wal")
    with tempfile.TemporaryDirectory(prefix="rtl-wal-read-") as folder:
        snapshot = Path(folder) / path.name
        snapshot_wal = snapshot.with_name(wal.name)
        for _ in range(3):
            before = (_file_stamp(path), _file_stamp(wal))
            try:
                shutil.copyfile(path, snapshot)
                if before[1] is None:
                    snapshot_wal.unlink(missing_ok=True)
                else:
                    shutil.copyfile(wal, snapshot_wal)
            except FileNotFoundError:
                continue
            if before == (_file_stamp(path), _file_stamp(wal)):
                # SQLite can rebuild its WAL index here without touching the source.
                yield snapshot
                return
        raise LedgerSchemaError("RTL WAL 台账读取期间发生变化，请重试")


@contextmanager
def readonly_connection(path, *, timeout=15):
    """Open a read-only ledger without creating WAL sidecars at its original path."""
    path = Path(path)
    with _readonly_path(path) as source:
        with closing(sqlite3.connect(
            source.as_uri() + "?mode=ro", uri=True, timeout=timeout, isolation_level=None,
            factory=_LedgerReadConnection,
        )) as db:
            db.ledger_path = str(path)
            yield db


_VERSION_1 = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        root TEXT NOT NULL UNIQUE,
        output TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS project_rebinds (
        sequence INTEGER PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        previous_root TEXT NOT NULL,
        previous_output TEXT NOT NULL,
        current_root TEXT NOT NULL,
        current_output TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id),
        project_root TEXT NOT NULL,
        output_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        epoch INTEGER NOT NULL DEFAULT 1,
        candidate TEXT,
        baseline TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS active_project
        ON runs(project_id) WHERE status = 'active'
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        run_id TEXT NOT NULL REFERENCES runs(id),
        id TEXT NOT NULL,
        spec TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempt_id TEXT,
        PRIMARY KEY (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attempts (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        state TEXT NOT NULL,
        input TEXT NOT NULL,
        workspace TEXT,
        result TEXT,
        worker TEXT,
        operation_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (run_id, task_id) REFERENCES tasks(run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(id),
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'intent',
        payload TEXT NOT NULL,
        receipt TEXT,
        PRIMARY KEY (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        id TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES runs(id),
        kind TEXT NOT NULL,
        target TEXT NOT NULL,
        actor TEXT NOT NULL,
        cases TEXT NOT NULL,
        context TEXT NOT NULL,
        PRIMARY KEY (run_id, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contracts (
        run_id TEXT NOT NULL REFERENCES runs(id),
        edge TEXT NOT NULL,
        version TEXT NOT NULL,
        body TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (run_id, edge, version)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS active_contract
        ON contracts(run_id, edge) WHERE active = 1
    """,
)


_VERSION_0_COLUMNS = {
    "projects": ("id", "root", "output"),
    "project_rebinds": ("sequence", "project_id", "previous_root", "previous_output", "current_root", "current_output"),
    "runs": ("id", "project_id", "project_root", "output_path", "status", "epoch", "candidate", "baseline", "created_at"),
    "tasks": ("run_id", "id", "spec", "status", "attempt_id"),
    "attempts": ("id", "run_id", "task_id", "state", "input", "workspace", "result", "worker", "operation_id", "created_at"),
    "operations": ("id", "run_id", "kind", "status", "payload", "receipt"),
    "evidence": ("id", "run_id", "kind", "target", "actor", "cases", "context"),
    "contracts": ("run_id", "edge", "version", "body", "active"),
}

_ACTIVE_INDEXES = (
    ("runs", "active_project", ("project_id",), r"(?i:status)\s*=\s*'active'"),
    ("contracts", "active_contract", ("run_id", "edge"), r"(?i:active)\s*=\s*1"),
)


def _verify_unversioned(db):
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'"
    )}
    if not tables:
        return
    for name, columns in _VERSION_0_COLUMNS.items():
        if name == "project_rebinds" and name not in tables:
            continue
        actual = tuple(row[1] for row in db.execute(f"PRAGMA table_info({name})"))
        if actual != columns:
            raise LedgerSchemaError(f"无法升级 RTL 台账，旧版 {name} 表结构不匹配")


def _upgrade_to_v1(db):
    _verify_unversioned(db)
    # Run individual statements so SQLite cannot implicitly commit the caller's transaction.
    for statement in _VERSION_1:
        db.execute(statement)


def _verify_active_indexes(db):
    for table, name, columns, predicate in _ACTIVE_INDEXES:
        index = next((row for row in db.execute(f"PRAGMA index_list({table})") if row[1] == name), None)
        indexed = tuple((row[2], row[3], row[4]) for row in db.execute(f"PRAGMA index_xinfo({name})") if row[5])
        definition = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND name=?", (table, name),
        ).fetchone()
        where = re.search(r"\bwhere\b(.*)", definition[0] or "", re.IGNORECASE | re.DOTALL) if definition else None
        if (
            not index or index[2] != 1 or index[4] != 1
            or indexed != tuple((column, 0, "BINARY") for column in columns)
            or not where or not re.fullmatch(rf"\s*\(?\s*{predicate}\s*\)?\s*;?\s*", where[1])
        ):
            raise LedgerSchemaError(f"RTL 台账缺少有效的唯一活动索引 {name}，请核对台账结构和活动记录")


_MIGRATIONS = (_upgrade_to_v1,)
CURRENT_VERSION = len(_MIGRATIONS)


def require_readable(db):
    """Permit the known unversioned format without changing the database."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if not 0 <= version <= CURRENT_VERSION:
        raise LedgerSchemaError(f"不支持 RTL 台账 Schema 版本 {version}，当前支持 0 至 {CURRENT_VERSION}")
    return version


def ensure_current(db):
    """Apply upgrades inside the caller's SQLite write transaction."""
    for version in range(require_readable(db), CURRENT_VERSION):
        _MIGRATIONS[version](db)
        db.execute(f"PRAGMA user_version = {version + 1}")
    _verify_active_indexes(db)


def project_rebind_rows(db, project_id):
    """Return path history when it exists in the original ledger layout."""
    if require_readable(db) == 0 and not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_rebinds'"
    ).fetchone():
        return ()
    return db.execute(
        "SELECT previous_root,previous_output,current_root,current_output FROM project_rebinds "
        "WHERE project_id=? ORDER BY sequence DESC", (project_id,),
    )
