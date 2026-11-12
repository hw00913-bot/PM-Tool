"""Author lineage used by independent verification gates."""

from contextlib import contextmanager
from pathlib import Path

from .state import get_attempt, get_operation, get_run, safe_id, unpack


@contextmanager
def prior_attempt(db, attempt):
    """Open the preceding attempt in the ledger pinned by the source seed."""
    result = attempt["result"]
    identifier = result["revalidation_of"]
    ledger = result.get("revalidation_ledger")
    if not ledger:
        yield db, get_attempt(db, identifier)
        return
    from .delivery_origin import read_ledger

    seed = get_operation(db, attempt["run_id"], "seed-from")
    if not seed or seed["status"] != "done" or seed["payload"].get("source_ledger") != ledger:
        raise ValueError("仅复验的旧尝试台账与本轮继承来源不符")
    with read_ledger(ledger) as origin:
        previous = get_attempt(origin, identifier)
        if previous["run_id"] != seed["payload"]["source_run"] or previous["task_id"] != attempt["task_id"]:
            raise ValueError("仅复验的旧尝试与本轮继承任务不符")
        yield origin, previous


def attempt_authors(db, attempt_ids):
    """Keep source authors when a later worker revalidates unchanged work."""
    pending = list(attempt_ids)
    visited = set()
    authors = set()
    while pending:
        attempt_id = pending.pop()
        if attempt_id in visited:
            continue
        visited.add(attempt_id)
        attempt = get_attempt(db, attempt_id)
        result = attempt["result"] or {}
        authors.update(actor for actor in (attempt["worker"], result.get("actor")) if actor)
        previous = result.get("revalidation_of")
        if previous and result.get("revalidation_ledger"):
            with prior_attempt(db, attempt) as (origin, inherited):
                authors.update(attempt_authors(origin, [inherited["id"]]))
        elif previous:
            pending.append(previous)
    return authors


def _verified_legacy_source(db, run_id):
    """Recover authorship for a React seed imported before provenance was recorded."""
    from .artifacts import rev
    from .delivery_origin import latest_local_react_seed
    from .frontend import profile
    from .requirements_history import _origin

    run = get_run(db, run_id)
    if profile(run["baseline"]) != "react":
        return None
    project_id = safe_id(run["project_id"], "project ID")
    source = latest_local_react_seed(db, project_id, before_run=run_id)
    if not source:
        return None
    root = Path(_origin(db)).parent / "projects" / project_id / "runs"
    current_repo = root / safe_id(run_id, "run ID") / "repository"
    prior_repo = root / safe_id(source["source_run"], "source run ID") / "repository"
    initial_tree = rev(current_repo, "refs/heads/rtl-seed^{tree}")
    prior_tree = rev(prior_repo, f"{source['candidate']}^{{tree}}")
    return source["source_run"] if initial_tree == prior_tree else None


def candidate_authors(db, run_id):
    """Collect the promoted attempts behind the candidate and its known seed."""
    attempts = set()
    authors = set()
    visited = set()
    while run_id and run_id not in visited:
        visited.add(run_id)
        for row in db.execute(
            "SELECT payload FROM operations WHERE run_id=? AND kind='integration' AND status='done'",
            (run_id,),
        ):
            attempts.update(unpack(row["payload"])["attempt_ids"])
        attempts.update(
            row["attempt_id"] for row in db.execute(
                "SELECT attempt_id FROM tasks WHERE run_id=? AND status='integrated' AND attempt_id IS NOT NULL",
                (run_id,),
            )
        )
        seed = get_operation(db, run_id, "seed-from")
        if not seed:
            run_id = _verified_legacy_source(db, run_id)
            continue
        if seed["status"] != "done":
            raise ValueError("源码继承尚未完成，无法确认候选作者")
        source = seed["payload"]
        if source.get("source_ledger"):
            authors.update(source["source_authors"])
            break
        run_id = source["source_run"]
    return authors | attempt_authors(db, attempts)
