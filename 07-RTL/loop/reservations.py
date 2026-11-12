"""Persistent project/output reservations shared by all ledgers of this OS user."""

import fcntl
import os
from pathlib import Path
import pwd
import sqlite3
import stat
import sys
from contextlib import closing, contextmanager

from .ledger_schema import LedgerSchemaError, require_readable
from .state import LoopError, overlaps


SCHEMA = """
CREATE TABLE IF NOT EXISTS reservations (
    ledger TEXT NOT NULL,
    run_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    device TEXT NOT NULL,
    inode TEXT NOT NULL,
    project_root TEXT,
    output_path TEXT,
    next_project_root TEXT,
    next_output_path TEXT,
    PRIMARY KEY (ledger, run_id)
)
"""


def reservation_root():
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ("Library/Application Support/RTL" if sys.platform == "darwin" else ".local/state/rtl")


def _identity(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise OSError(f"not a regular ledger file: {path}")
    return str(info.st_dev), str(info.st_ino)


@contextmanager
def reservations(root):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / "reservations.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(sqlite3.connect(root / "reservations.sqlite", isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous = FULL")
            db.execute(SCHEMA)
            yield _Reservations(db)


class _Reservations:
    def __init__(self, db):
        self.db = db

    def _row(self, ledger, run_id):
        return self.db.execute(
            "SELECT * FROM reservations WHERE ledger=? AND run_id=?", (str(ledger), run_id),
        ).fetchone()

    def _delete(self, row):
        self.db.execute(
            "DELETE FROM reservations WHERE ledger=? AND run_id=?", (row["ledger"], row["run_id"]),
        )

    def _settle(self, row):
        ledger = Path(row["ledger"])
        label = f"{row['run_id']} in {ledger.parent}"
        try:
            if _identity(ledger) != (row["device"], row["inode"]):
                raise OSError("ledger identity changed")
            with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                require_readable(db)
                run = db.execute(
                    "SELECT project_id, project_root, output_path, status FROM runs WHERE id=?",
                    (row["run_id"],),
                ).fetchone()
                rolling_back = (
                    run is not None and run["status"] == "superseded" and db.execute(
                        "SELECT 1 FROM operations WHERE run_id=? AND kind='landing-rollback' AND status='intent'",
                        (row["run_id"],),
                    ).fetchone() is not None
                )
        except (OSError, sqlite3.Error, LedgerSchemaError) as error:
            raise LoopError(f"cannot verify reservation for run {label}: {error}") from error

        current = (row["project_root"], row["output_path"])
        proposed = (row["next_project_root"], row["next_output_path"])
        if run is None and current[0] is None and proposed[0] is not None:
            # An initial reservation was committed before the ledger's INSERT.
            # Holding the process lock proves no create/rebind can still be in progress.
            self._delete(row)
            return None
        if run is None or run["project_id"] != row["project_id"]:
            raise LoopError(f"reservation no longer matches its ledger: {label}")
        actual = (run["project_root"], run["output_path"])
        if actual not in (current, proposed):
            raise LoopError(f"reservation paths no longer match their ledger: {label}")
        if run["status"] in {"delivered", "superseded"} and not rolling_back:
            self._delete(row)
            return None
        if run["status"] != "active" and not rolling_back:
            raise LoopError(f"cannot verify reservation for run {label}: {run['status']}")
        if proposed[0] is not None:
            self.db.execute(
                "UPDATE reservations SET project_root=?, output_path=?, "
                "next_project_root=NULL, next_output_path=NULL WHERE ledger=? AND run_id=?",
                (*actual, row["ledger"], row["run_id"]),
            )
            return self._row(ledger, row["run_id"])
        return row

    def settle(self, ledger, run_id):
        row = self._row(ledger, run_id)
        return self._settle(row) if row else None

    @staticmethod
    def _overlaps(row, ledger, project_id, paths):
        if project_id is not None and project_id == row["project_id"]:
            return True
        occupied = tuple(
            row[column] for column in (
                "project_root", "output_path", "next_project_root", "next_output_path",
            ) if row[column] is not None
        )
        if any(overlaps(path, held) for path in paths for held in occupied):
            return True
        if row["ledger"] == str(ledger):
            return False
        peer_data = Path(row["ledger"]).parent
        return any(overlaps(path, peer_data) for path in (*paths, ledger.parent)) or any(
            overlaps(ledger.parent, held) for held in occupied
        )

    def check(self, ledger, run_id, project_id, paths):
        for row in self.db.execute("SELECT * FROM reservations").fetchall():
            if (row["ledger"], row["run_id"]) == (str(ledger), run_id):
                continue
            if not self._overlaps(row, ledger, project_id, paths):
                continue
            settled = self._settle(row)
            if settled and self._overlaps(settled, ledger, project_id, paths):
                data = Path(row["ledger"]).parent
                raise LoopError(f"project/output reserved by active run {row['run_id']} in {data} (ledger paths are also protected)")

    def _insert(self, ledger, run_id, project_id, paths, *, pending):
        device, inode = _identity(ledger)
        pair = (None, None)
        current, proposed = (pair, paths) if pending else (paths, pair)
        self.db.execute(
            "INSERT INTO reservations(ledger,run_id,project_id,device,inode,project_root,output_path,"
            "next_project_root,next_output_path) VALUES(?,?,?,?,?,?,?,?,?)",
            (str(ledger), run_id, project_id, device, inode, *current, *proposed),
        )

    def adopt(self, ledger, run):
        run_id, project_id = run["id"], run["project_id"]
        paths = (run["project_root"], run["output_path"])
        self.check(ledger, run_id, project_id, paths)
        if self.settle(ledger, run_id) is None:
            self._insert(ledger, run_id, project_id, paths, pending=False)

    def prepare(self, ledger, run_id, project_id, paths):
        self.check(ledger, run_id, project_id, paths)
        existing = self.settle(ledger, run_id)
        if existing is None:
            self._insert(ledger, run_id, project_id, paths, pending=True)
        else:
            self.db.execute(
                "UPDATE reservations SET next_project_root=?, next_output_path=? WHERE ledger=? AND run_id=?",
                (*paths, str(ledger), run_id),
            )
