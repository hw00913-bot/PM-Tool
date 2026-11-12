"""Managed Git workspaces and candidate promotion."""

from pathlib import Path, PurePosixPath
import os
import shutil
import subprocess
import tempfile
import uuid

from .state import begin_operation, finish_operation, get_attempt, get_operation, guard, path_in_scope, safe_id, unpack
from . import frontend


CANDIDATE_REF = "refs/rtl/candidate"


def git(path, *args, input_bytes=None):
    command = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
        "-c", "user.name=RTL Loop", "-c", "user.email=rtl-loop@localhost",
        "-C", os.fspath(path), *args,
    ]
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    result = subprocess.run(command, input=input_bytes, capture_output=True, env=env)
    if result.returncode:
        message = result.stderr.decode(errors="replace").strip()
        raise ValueError(message or f"Git 操作失败：{args[0]}")
    return result.stdout


def run_directory(store, run_id):
    run = store.run(run_id)
    if not run:
        raise ValueError("找不到运行")
    for value in (run["project_id"], run_id):
        if not value or PurePosixPath(value).name != value or value in {".", ".."}:
            raise ValueError("运行编号或项目编号无效")
    return Path(store.root) / "projects" / run["project_id"] / "runs" / run_id


def repo_path(store, run_id):
    return run_directory(store, run_id) / "repository"


def rev(path, ref="HEAD"):
    return git(path, "rev-parse", "--verify", ref).decode().strip()


def _check_source(source):
    if source.is_symlink() or not source.is_dir():
        raise ValueError("原型来源必须是实际目录")
    for path in source.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"原型来源存在链接或非常规文件：{path}")
        if path.name.casefold() == ".git":
            raise ValueError("原型资产目录不能包含 Git 元数据")


def _seed_repository(store, db, run, staging, source):
    seed = get_operation(db, run["id"], "seed-from")
    if seed:
        previous = seed["payload"]
        if previous.get("source_ledger"):
            from .delivery_origin import seed_repository

            origin = seed_repository(db, run, previous)
            candidate = previous["candidate"]
            object_format = git(origin, "rev-parse", "--show-object-format").decode().strip()
            git(staging, "init", "--quiet", "--initial-branch=rtl-seed", f"--object-format={object_format}")
            git(staging, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", os.fspath(origin), candidate)
            git(staging, "checkout", "--quiet", "-B", "rtl-seed", candidate)
            if rev(staging) != candidate:
                raise ValueError("继承的 Git 候选与原交付不符")
            return candidate
        frontend.export_source(repo_path(store, previous["source_run"]), previous["candidate"], staging)
        return None
    project = Path(run["project_root"])
    if frontend.profile(run["baseline"]) == "static":
        source = Path(source) if source is not None else project / "prototype"
    else:
        explicit_source = source is not None
        source = Path(source) if source is not None else project / "prototype-src"
        if source.is_symlink() or explicit_source and not source.is_dir():
            raise ValueError("React 原型源码须为实际目录")
        if not source.exists():
            from .delivery_origin import latest_local_react_seed

            # Runs admitted before seed provenance was pinned can still be initialized.
            previous = latest_local_react_seed(db, run["project_id"], before_run=run["id"])
            if previous:
                begin_operation(db, run["id"], "seed-from", "seed-from", previous)
                frontend.export_source(repo_path(store, previous["source_run"]), previous["candidate"], staging)
                finish_operation(db, run["id"], "seed-from", previous)
                return None
            source = Path(__file__).resolve().parents[1] / "templates" / "react"
        if not (source / "package.json").is_file() or not (source / "package-lock.json").is_file():
            raise ValueError("React 原型源码须包含 package.json 和 package-lock.json")
        for path in source.rglob("*"):
            if path.name in {"node_modules", "dist", ".umi", ".umi-production"}:
                raise ValueError(f"请提供干净的 React 源码目录，移除生成物后重试：{path}")
    if source.exists():
        _check_source(source)
        shutil.copytree(source, staging, dirs_exist_ok=True)


def _matches_pending_integration(db, run, actual):
    for row in db.execute(
        "SELECT payload FROM operations WHERE run_id=? AND kind='integration' AND status='intent'",
        (run["id"],),
    ):
        payload = unpack(row["payload"])
        if payload["parent"] == run["candidate"] and payload["candidate"] == actual:
            return True
    return False


def initialize_repo(store, run_id, epoch, source=None, *, allow_pending_integration=False):
    """Import prototype assets without creating Git metadata in the user's project."""
    repo = repo_path(store, run_id)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        if run["candidate"]:
            actual = rev(repo, CANDIDATE_REF)
            if actual != run["candidate"]:
                if not allow_pending_integration or not _matches_pending_integration(db, run, actual):
                    raise ValueError("候选引用与台账不符，请先恢复")
            return run["candidate"]
        repo.parent.mkdir(parents=True, exist_ok=True)
        if not repo.exists():
            with tempfile.TemporaryDirectory(prefix="seed-", dir=repo.parent) as temporary:
                staging = Path(temporary) / "repository"
                staging.mkdir()
                inherited = _seed_repository(store, db, run, staging, source)
                if not inherited:
                    git(staging, "init", "--quiet", "--initial-branch=rtl-seed")
                    git(staging, "add", "-f", "--all")
                    git(staging, "commit", "--quiet", "--allow-empty", "-m", "RTL prototype baseline")
                git(staging, "update-ref", CANDIDATE_REF, inherited or rev(staging))
                staging.rename(repo)
        candidate = rev(repo, CANDIDATE_REF)
        seed = get_operation(db, run_id, "seed-from")
        if seed and seed["payload"].get("source_ledger") and candidate != seed["payload"]["candidate"]:
            raise ValueError("初始化的 Git 候选与原交付不符，请先恢复")
        db.execute("UPDATE runs SET candidate = ? WHERE id = ?", (candidate, run_id))
        return candidate


def make_worktree(store, run_id, attempt_id, base=None):
    attempt = store.attempt(attempt_id)
    if not attempt or attempt["run_id"] != run_id:
        raise ValueError("尝试不属于此运行")
    base = base or attempt["input"]["candidate"]
    if base != attempt["input"]["candidate"]:
        raise ValueError("工作区基础与尝试不符")
    path = run_directory(store, run_id) / "workspaces" / attempt_id
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        git(repo_path(store, run_id), "worktree", "add", "--detach", os.fspath(path), base)
    actual = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    if Path(actual).resolve() != (repo_path(store, run_id) / ".git").resolve():
        raise ValueError("工作区不属于运行仓库")
    return path


def _clean(path):
    if git(path, "status", "--porcelain", "--untracked-files=all", "--ignored=matching"):
        raise ValueError("工作区存在未提交文件；请先提交供验证的变更")


def _scope_paths(path, base, head, allowed):
    git(path, "merge-base", "--is-ancestor", base, head)
    names = git(path, "diff", "--name-only", "-z", "--no-renames", base, head)
    changed = [name.decode() for name in names.split(b"\x00") if name]
    if not changed:
        raise ValueError("尝试没有可交接的已提交变更")
    for name in changed:
        if not any(scope == "." or name == scope or name.startswith(scope.rstrip("/") + "/") for scope in allowed):
            raise ValueError(f"修改超出任务授权范围：{name}")
    return changed


def assert_plain_tree(path, head, *, require_entry=False):
    paths = set()
    for record in git(path, "ls-tree", "-rz", head).split(b"\x00"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, kind, _ = metadata.split()
        name = raw_name.decode()
        parts = name.split("/")
        if (
            kind != b"blob" or mode not in {b"100644", b"100755"}
            or any(part.casefold() in {"", ".", "..", ".git"} for part in parts)
            or "\\" in name or any(ord(character) < 32 for character in name)
        ):
            raise ValueError(f"候选含不可交付的路径或文件：{name}")
        folded = name.casefold()
        if folded in paths:
            raise ValueError(f"候选含大小写冲突路径：{name}")
        paths.add(folded)
    if require_entry and "index.html" not in {
        record.split(b"\t", 1)[1].decode()
        for record in git(path, "ls-tree", "-z", head).split(b"\x00") if record
    }:
        raise ValueError("候选缺少原型入口 index.html")


def _integrated_attempt(db, run_id, attempt_ids):
    for row in db.execute(
        "SELECT payload FROM operations WHERE run_id=? AND kind='integration' AND status='done' ORDER BY rowid DESC",
        (run_id,),
    ):
        integrated = unpack(row["payload"])
        for previous in integrated["attempt_ids"]:
            if previous in attempt_ids:
                return previous, integrated["candidate"]
    return None


def _original_paths(db, attempt_id):
    from .authorship import prior_attempt

    attempt = get_attempt(db, attempt_id)
    if not attempt["result"].get("revalidation_of"):
        return attempt["result"]["paths"]
    with prior_attempt(db, attempt) as (origin, previous):
        return _original_paths(origin, previous["id"])


def _source_task(db, source_run, task_id):
    source_task = db.execute(
        "SELECT attempt_id FROM tasks WHERE run_id=? AND id=? AND status='integrated'",
        (source_run, task_id),
    ).fetchone()
    return _integrated_attempt(db, source_run, {source_task["attempt_id"]}) if source_task else None


def _revalidation_source(db, run_id, attempt):
    from .delivery_origin import read_ledger

    source_run = run_id
    history = {
        row["id"] for row in db.execute(
            "SELECT id FROM attempts WHERE run_id=? AND task_id=? AND state='fenced' AND result IS NOT NULL",
            (run_id, attempt["task_id"]),
        )
    }
    integrated = _integrated_attempt(db, source_run, history)
    if integrated is None:
        seed = get_operation(db, run_id, "seed-from")
        if seed and seed["status"] == "done":
            source = seed["payload"]
            source_run = source["source_run"]
            if source.get("source_ledger"):
                with read_ledger(source["source_ledger"]) as origin:
                    inherited = _source_task(origin, source_run, attempt["task_id"])
                    if inherited is not None:
                        previous, candidate = inherited
                        return source_run, previous, candidate, _check_revalidation_paths(
                            _original_paths(origin, previous), attempt,
                        ), source["source_ledger"]
            else:
                integrated = _source_task(db, source_run, attempt["task_id"])
    if integrated is None:
        raise ValueError("仅复验须有曾成功合入、已失效或已继承的本任务结果")
    previous, candidate = integrated
    return source_run, previous, candidate, _check_revalidation_paths(_original_paths(db, previous), attempt), None


def _check_revalidation_paths(paths, attempt):
    if not paths or not all(path_in_scope(path, attempt["input"]["spec"]["paths"]) for path in paths):
        raise ValueError("仅复验的旧任务源码超出本轮授权范围")
    return paths


def _scoped_tree(repo, commit, paths):
    return git(repo, "ls-tree", "-rz", commit, "--", *(f":(literal){path}" for path in paths))


def _changed_paths(repo, parent, candidate, paths):
    return git(repo, "diff", "--name-only", parent, candidate, "--", *(f":(literal){path}" for path in paths))


def _commit_codex_changes(workspace, base, allowed):
    status = git(workspace, "status", "--porcelain=v1", "-z", "--no-renames",
                 "--untracked-files=all", "--ignored=matching")
    if not status:
        return
    head = rev(workspace)
    if head != base:
        _scope_paths(workspace, base, head, allowed)
    if git(workspace, "ls-files", "--unmerged", "-z"):
        raise ValueError("工作区存在未解决的合并冲突")
    unstaged = []
    for record in filter(None, status.split(b"\x00")):
        code, name = record[:2], record[3:].decode()
        if code == b"!!":
            raise ValueError(f"请先移除工作区的忽略文件或生成物：{name}")
        if not path_in_scope(name, allowed):
            raise ValueError(f"修改超出任务授权范围：{name}")
        path = workspace / name
        if any(parent.is_symlink() for parent in path.parents if parent != workspace and parent.is_relative_to(workspace)):
            raise ValueError(f"修改路径经过符号链接：{name}")
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError(f"工作区含不可交付的文件：{name}")
        if code[1:] != b" ":
            unstaged.append(f":(literal){name}")
    if unstaged:
        git(workspace, "add", "--all", "--", *unstaged)
    tree = git(workspace, "write-tree").decode().strip()
    assert_plain_tree(workspace, tree)
    changed = git(workspace, "diff", "--cached", "--name-only", "-z", "--no-renames")
    for name in (value.decode() for value in changed.split(b"\x00") if value):
        if not path_in_scope(name, allowed):
            raise ValueError(f"修改超出任务授权范围：{name}")
    if changed:
        git(workspace, "commit", "--quiet", "-m", "RTL capture of Codex scene changes")


def capture_submission(store, run_id, attempt_id, actor, *, revalidate=False, commit_changes=False):
    attempt = store.attempt(attempt_id)
    if not attempt or attempt["run_id"] != run_id or attempt["worker"] != actor:
        raise ValueError("提交者或尝试身份不符")
    workspace = make_worktree(store, run_id, attempt_id)
    if not attempt["workspace"] or Path(attempt["workspace"]).resolve() != workspace.resolve():
        raise ValueError("尝试尚未绑定此工作区")
    base = attempt["input"]["candidate"]
    if commit_changes and not revalidate:
        _commit_codex_changes(workspace, base, attempt["input"]["spec"]["paths"])
    _clean(workspace)
    head = rev(workspace)
    if revalidate:
        if attempt["input"]["phase"] != "implement" or head != base:
            raise ValueError("仅复验须使用原样的实现尝试基础版本")
        with store.transaction(immediate=False) as db:
            source_run, previous, integrated, paths, origin = _revalidation_source(db, run_id, attempt)
        if source_run == run_id or origin:
            git(workspace, "merge-base", "--is-ancestor", integrated, base)
        old_tree = _scoped_tree(workspace if origin else repo_path(store, source_run), integrated, paths)
        if _scoped_tree(workspace, base, paths) != old_tree:
            raise ValueError("仅复验的基础版本已改变该任务此前合入的源码")
        assert_plain_tree(workspace, head)
        result = {"commit": head, "base": base, "actor": actor, "paths": [], "revalidation_of": previous}
        if origin:
            result["revalidation_ledger"] = origin
        return result
    paths = _scope_paths(workspace, base, head, attempt["input"]["spec"]["paths"])
    assert_plain_tree(workspace, head)
    return {"commit": head, "base": base, "actor": actor, "paths": paths}


def _load_submission(db, run_id, attempt_id):
    row = db.execute(
        "SELECT a.*, t.status AS task_status, t.attempt_id AS current_attempt "
        "FROM attempts a JOIN tasks t ON t.run_id=a.run_id AND t.id=a.task_id "
        "WHERE a.run_id=? AND a.id=?", (run_id, attempt_id),
    ).fetchone()
    if not row or row["state"] != "submitted" or row["task_status"] != "submitted" or row["current_attempt"] != attempt_id:
        raise ValueError(f"尝试不是当前可合入结果：{attempt_id}")
    attempt = {**dict(row), "input": unpack(row["input"]), "result": unpack(row["result"])}
    execution = attempt["result"].get("codex_execution") or {}
    if not execution.get("tool_proof") and get_operation(db, run_id, f"codex-thread:{attempt_id}"):
        from .codex_host import require_quiescent

        require_quiescent(db, run_id, attempt, completed=True)
        workspace = Path(attempt["workspace"])
        _clean(workspace)
        if rev(workspace) != attempt["result"]["commit"]:
            raise ValueError("旧版 Codex 提交与工具结束后的工作区不符，请重新派发和验证")
    return attempt


def _require_self_evidence(store, db, run, attempt):
    from .verification import require_cases

    commit = attempt["result"]["commit"]
    context = {"attempt_id": attempt["id"]}
    if frontend.profile(run["baseline"]) == "react":
        context["asset_digest"] = frontend.get_build(store, run["id"], commit)["asset_digest"]
    require_cases(
        db, run["id"], "self", commit, attempt["input"]["spec"].get("cases", []),
        context_filter=context, required_actor=attempt["worker"], require_any=True, baseline=run["baseline"],
    )


def stage(store, run_id, epoch, attempt_ids, *, required_cases=(), required_edges=()):
    from .dispatch import ensure_current_inputs, require_bound_requirements
    from .verification import integration_retests, require_frozen_cases

    if not attempt_ids or len(set(attempt_ids)) != len(attempt_ids):
        raise ValueError("请提供至少一个且互不重复的尝试编号")
    repo = repo_path(store, run_id)
    operation_id = f"merge-{uuid.uuid4().hex}"
    path = run_directory(store, run_id) / "integration" / operation_id
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        parent = run["candidate"]
        if not parent or rev(repo, CANDIDATE_REF) != parent:
            raise ValueError("实际候选与台账不符，请先恢复")
        static = frontend.profile(run["baseline"]) == "static"
        submissions = [_load_submission(db, run_id, attempt_id) for attempt_id in attempt_ids]
        task_ids = {entry["task_id"] for entry in submissions}
        retests, _ = integration_retests(db, run, task_ids, operation_id)
        cases = sorted({safe_id(case, "case ID") for case in required_cases})
        require_frozen_cases(run["baseline"], cases, label="暂存合入")
        edges = {safe_id(edge, "contract edge") for edge in required_edges}
        revalidated_paths = set()
        for entry in submissions:
            require_bound_requirements(db, run, entry)
            ensure_current_inputs(db, run_id, entry)
            spec = entry["input"]["spec"]
            edges.update(spec["edges"])
            revalidate = "revalidation_of" in entry["result"]
            result = capture_submission(store, run_id, entry["id"], entry["worker"], revalidate=revalidate)
            if any(entry["result"].get(key) != value for key, value in result.items()) or (
                entry["result"].get("revalidation_ledger") != result.get("revalidation_ledger")
            ):
                raise ValueError("提交工作区的实际内容已改变")
            if revalidate:
                if result["base"] != parent:
                    raise ValueError("仅复验的基础版本已改变，请对当前候选重新领取和复验")
                _, _, _, paths, _ = _revalidation_source(db, run_id, entry)
                revalidated_paths.update(paths)
            _require_self_evidence(store, db, run, entry)
        active = {
            row["edge"]: row for row in db.execute(
                "SELECT edge,version,body FROM contracts WHERE run_id=? AND active=1", (run_id,),
            )
        }
        if not edges <= active.keys():
            raise ValueError(f"受影响契约尚未激活：{', '.join(sorted(edges - active.keys()))}")
        for edge in sorted(edges):
            row = active[edge]
            require_frozen_cases(
                run["baseline"], unpack(row["body"])["spec"]["cases"], label=f"契约 {edge}/{row['version']}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        git(repo, "worktree", "add", "--detach", os.fspath(path), parent)
        try:
            for entry in submissions:
                result = entry["result"]
                if result.get("revalidation_of"):
                    continue
                patch = git(repo, "diff", "--binary", "--no-ext-diff", result["base"], result["commit"])
                git(path, "apply", "--index", "--3way", input_bytes=patch)
            if any(not entry["result"].get("revalidation_of") for entry in submissions):
                git(path, "commit", "--quiet", "-m", f"RTL stage {operation_id}")
            _clean(path)
            candidate = rev(path)
            if revalidated_paths and _changed_paths(repo, parent, candidate, sorted(revalidated_paths)):
                raise ValueError("本次合入修改了仅复验任务的源码，请对实际改动重新提交和验证")
            assert_plain_tree(path, candidate, require_entry=static)
            payload = {
                "operation_id": operation_id, "parent": parent, "candidate": candidate,
                "path": str(path), "attempt_ids": list(attempt_ids),
                "required_cases": cases, "required_edges": sorted(edges),
            }
            if retests:
                payload["retest_cases"] = sorted({identity[0] for identity in retests})
            begin_operation(db, run_id, operation_id, "integration", payload)
        except Exception:
            git(repo, "worktree", "remove", "--force", os.fspath(path))
            raise
    return payload


def promote(store, run_id, epoch, operation_id):
    """Move the candidate reference exactly once, then record the durable receipt."""
    from .authorship import attempt_authors
    from .contracts import check_pairs
    from .delivery import require_complete_landing
    from .dispatch import ensure_current_inputs, require_bound_requirements
    from .lifecycle import require_no_integration_rollback
    from .verification import integration_retests, require_cases

    repo = repo_path(store, run_id)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        row = db.execute(
            "SELECT * FROM operations WHERE run_id=? AND id=? AND kind='integration'",
            (run_id, operation_id),
        ).fetchone()
        if not row or row["status"] == "superseded":
            raise ValueError("找不到有效的合入意图")
        if row["receipt"] is not None:
            return unpack(row["receipt"])
        require_no_integration_rollback(db, run_id)
        require_complete_landing(db, run)
        payload = unpack(row["payload"])
        if db.execute(
            "SELECT 1 FROM operations WHERE run_id=? AND kind='acceptance' AND status='intent'",
            (run_id,),
        ).fetchone():
            raise ValueError("请先完成或恢复已登记的验收回写")
        parent, candidate = payload["parent"], payload["candidate"]
        actual = rev(repo, CANDIDATE_REF)
        if run["candidate"] != parent or actual not in {parent, candidate}:
            raise ValueError("集成父版本已经变化，请重新暂存并复验")
        submissions = [_load_submission(db, run_id, attempt_id) for attempt_id in payload["attempt_ids"]]
        for entry in submissions:
            require_bound_requirements(db, run, entry)
            ensure_current_inputs(db, run_id, entry)
            _require_self_evidence(store, db, run, entry)
        path = Path(payload["path"])
        if rev(path) != candidate:
            raise ValueError("待合入结果与记录不符")
        no_source_changes = all(entry["result"].get("revalidation_of") for entry in submissions)
        staged_parent = candidate if no_source_changes else rev(path, "HEAD^")
        if staged_parent != parent:
            raise ValueError("待合入结果与记录不符")
        _clean(path)
        static = frontend.profile(run["baseline"]) == "static"
        assert_plain_tree(path, candidate, require_entry=static)
        context = {"operation_id": operation_id}
        if not static:
            context["asset_digest"] = frontend.get_build(store, run_id, candidate)["asset_digest"]
        risky = [
            item for item in submissions
            if item["input"]["spec"].get("risk") or item["result"].get("revalidation_of")
        ]
        required_cases = set(payload.get("required_cases", []))
        required_cases.update(case for item in risky for case in item["input"]["spec"].get("cases", []))
        task_ids = {entry["task_id"] for entry in submissions}
        retests, proof_ids = integration_retests(db, run, task_ids, operation_id)
        require_cases(
            db, run_id, "integration", candidate, required_cases,
            context_filter=context, excluded_actors=attempt_authors(db, payload["attempt_ids"]),
            require_any=bool(risky or required_cases), baseline=run["baseline"],
            reject_failures=True, retests=retests,
        )
        proof_ids.update(check_pairs(db, run_id, {**payload, **context}))
        if actual == parent and candidate != parent:
            git(repo, "update-ref", CANDIDATE_REF, candidate, parent)
        db.execute("UPDATE runs SET candidate=? WHERE id=?", (candidate, run_id))
        for entry in submissions:
            db.execute("UPDATE tasks SET status='integrated' WHERE run_id=? AND id=?", (run_id, entry["task_id"]))
        receipt = {"operation_id": operation_id, "parent": parent, "candidate": candidate}
        if proof_ids:
            receipt["retested_failures"] = sorted(proof_ids)
        finish_operation(db, run_id, operation_id, receipt)
        return receipt
