"""Reconciled placement of a local file:// prototype and durable acceptance."""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
from typing import Any, cast
from uuid import uuid4

from .artifacts import CANDIDATE_REF, assert_plain_tree, git, repo_path, rev, run_directory
from .evidence_compat import browser_family, delivery_browsers, delivery_revisions
from .managed_paths import check_output_path
from .payload_types import (
    AcceptanceContext, AcceptanceInputs, AcceptanceIntent, AcceptanceReceipt, Baseline, BuildReceipt,
    DeliveryReceipt, LandingIntent, LandingReceipt, OperationRecord, RunRecord,
)
from .state import begin_operation, finish_operation, get_operation, get_run, guard, pack, unpack
from .verification import evidence_intact, inputs_match, require_cases


def manifest(directory):
    """Hash every delivered regular file, including hidden files."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"交付目录不存在或不是普通目录：{directory}")
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f"交付目录含链接或非常规文件：{path}")
        if path.is_file():
            with path.open("rb") as stream:
                files[path.relative_to(directory).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return files


def _directory_state(directory):
    if directory.is_symlink():
        raise ValueError(f"交付路径是符号链接：{directory}")
    return manifest(directory) if directory.exists() else None


def _directory(path):
    value = Path(path)
    if not value.is_absolute() or value.resolve() != value:
        raise ValueError(f"落盘路径与已确认的实际路径不符：{value}")
    return value


def _confirmed_output(run: RunRecord, payload: LandingIntent | None = None) -> Path:
    output = _directory(run["output_path"] if payload is None else payload["output"])
    if str(output) != run["output_path"]:
        raise ValueError("交付意图中的落盘路径与已确认的实际路径不符")
    check_output_path(_directory(run["project_root"]), output)
    return output


def _tree_manifest(repo, candidate):
    assert_plain_tree(repo, candidate)
    files = {}
    for record in git(repo, "ls-tree", "-rz", candidate).split(b"\x00"):
        if record:
            metadata, raw_name = record.split(b"\t", 1)
            blob = metadata.split()[2].decode()
            files[raw_name.decode()] = hashlib.sha256(git(repo, "cat-file", "blob", blob)).hexdigest()
    return files


def _extract(repo, candidate, staging):
    raw = git(repo, "archive", "--format=tar", candidate)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(staging, filter="data")


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def check_landed(payload: LandingIntent) -> Path:
    output = _directory(payload["output"])
    if _directory_state(output) != payload["manifest"]:
        raise ValueError("实际落盘内容与候选清单不符，请核对并复验")
    if not (output / "index.html").is_file():
        raise ValueError("实际落盘目录缺少 index.html")
    return output / "index.html"


def delivery_authors(db, run_id):
    from .authorship import candidate_authors

    return candidate_authors(db, run_id)


def _require_integrated(db, run_id):
    row = db.execute(
        "SELECT id FROM tasks WHERE run_id=? AND status!='integrated' LIMIT 1", (run_id,)
    ).fetchone()
    if row:
        raise ValueError(f"本轮仍有任务未集成：{row['id']}")


def _build_receipt(build: dict[str, Any]) -> BuildReceipt:
    return {
        "source_commit": build["source_commit"],
        "lockfile_sha256": build["lockfile_sha256"],
        "artifact_manifest_sha256": hashlib.sha256(pack(build["manifest"]).encode("utf-8")).hexdigest(),
    }


def _require_landing_build(store, run: RunRecord, payload: LandingIntent) -> dict[str, Any] | None:
    from .frontend import get_build, profile

    if "build" not in payload:
        if profile(run["baseline"]) == "react":
            raise ValueError("React 交付意图缺少构建记录，请先核对原交付意图")
        return None
    build = get_build(store, run["id"], payload["candidate"])
    if _build_receipt(build) != payload["build"] or build["manifest"] != payload["manifest"]:
        raise ValueError("构建记录与已登记的交付意图不符，请核对原落盘内容后重新生成候选并验收")
    return build


def _previous_delivery_manifest(db, run, output, current):
    from .delivery_origin import previous_manifest, previous_output_matches

    rows = db.execute(
        "SELECT o.kind,o.payload,o.receipt,r.project_id,r.project_root,r.output_path "
        "FROM operations o JOIN runs r ON r.id=o.run_id "
        "WHERE r.project_id=? AND r.id!=? AND o.kind IN ('delivery', 'supersede', 'landing-rollback') "
        "AND o.status='done' ORDER BY o.rowid DESC",
        (run["project_id"], run["id"]),
    )
    local = None
    for row in rows:
        if row["kind"] == "delivery":
            previous = unpack(row["payload"])
            manifest_key = "manifest"
        elif row["kind"] == "landing-rollback":
            previous = unpack(row["receipt"])
            manifest_key = "restored_manifest"
        else:
            previous = unpack(row["receipt"])
            manifest_key = "retained_output_manifest"
        if (
            previous.get("output") == row["output_path"] and manifest_key in previous
            and previous_output_matches(db, row, run["project_root"], output)
        ):
            local = previous[manifest_key]
            if row["kind"] == "supersede" and local == current:
                return local
            break
    foreign = previous_manifest(db, run, output)
    return foreign if foreign is not None else local


def _landing_needs_recovery(run: RunRecord, payload: LandingIntent) -> bool:
    return payload["previous_manifest"] is not None and not _confirmed_output(run, payload).exists()


def require_complete_landing(db, run):
    """Keep the candidate fixed during an interrupted landing or rollback."""
    require_no_landing_rollback(db, run["id"])
    pending = db.execute(
        "SELECT payload FROM operations WHERE run_id=? AND kind='delivery' AND status='intent'",
        (run["id"],),
    ).fetchone()
    if pending and _landing_needs_recovery(run, unpack(pending["payload"])):
        raise ValueError("落盘替换尚未完成，请核对原操作并先重试 land")


def require_no_landing_rollback(db, run_id):
    pending = db.execute(
        "SELECT payload FROM operations WHERE run_id=? AND kind='landing-rollback' AND status='intent'",
        (run_id,),
    ).fetchone()
    if pending:
        original = unpack(pending["payload"])["delivery_operation"]
        raise ValueError(f"交付回退尚未完成，请重试 rollback-landing：{original}")


def _prepare(store, run_id, epoch, allow_replace) -> OperationRecord:
    from .frontend import get_build, profile
    from .requirements_run import require_prepared

    repo = repo_path(store, run_id)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        require_no_landing_rollback(db, run_id)
        output = _confirmed_output(run)
        require_prepared(db, run)
        candidate = run["candidate"]
        if not candidate or rev(repo, CANDIDATE_REF) != candidate:
            raise ValueError("候选与台账不符，请先恢复")
        identity = hashlib.sha256(f"{run_id}\0{candidate}\0{output}".encode()).hexdigest()[:24]
        operation_id = f"delivery-{identity}"
        known = get_operation(db, run_id, operation_id)
        if known and get_operation(db, run_id, f"rollback-{operation_id}"):
            raise ValueError("该候选已驳回并回退，请先生成新的候选")
        if known and known["status"] == "intent" and _landing_needs_recovery(run, cast(LandingIntent, known["payload"])):
            return known
        _require_integrated(db, run_id)
        react = profile(run["baseline"]) == "react"
        assert_plain_tree(repo, candidate, require_entry=not react)
        if known:
            return known
        stale = db.execute(
            "SELECT id,payload FROM operations WHERE run_id=? AND kind='delivery' AND status='intent'",
            (run_id,),
        ).fetchone()
        original = _directory_state(output)
        build = get_build(store, run_id, candidate) if react else None
        expected = build["manifest"] if build is not None else _tree_manifest(repo, candidate)
        known_previous = False
        if stale:
            prior = unpack(stale["payload"])
            if prior["output"] != str(output) or original not in (prior["manifest"], prior["previous_manifest"]):
                raise ValueError("上一份待验交付内容与实际路径不符，请先核对")
            db.execute(
                "UPDATE operations SET status='superseded' WHERE run_id=? AND id=?",
                (run_id, stale["id"]),
            )
            known_previous = True
        if original and original != expected and not known_previous:
            previous_manifest = _previous_delivery_manifest(db, run, output, original)
            imported_seed = _tree_manifest(repo, rev(repo, "refs/heads/rtl-seed")) if not react else None
            if not allow_replace or original not in (previous_manifest, imported_seed):
                raise ValueError("落盘目录有未确认的既有内容；请保留原目录并选择新落盘位置")
        payload: LandingIntent = {
            "operation_id": operation_id, "candidate": candidate, "output": str(output),
            "manifest": expected, "previous_manifest": original,
            "staging": str(output.parent / f".rtl-stage-{identity}"),
            "previous": str(output.parent / f".rtl-before-{identity}"),
        }
        if build is not None:
            payload["build"] = _build_receipt(build)
        return begin_operation(db, run_id, operation_id, "delivery", payload)


def land(store, run_id, epoch, allow_replace=False) -> LandingReceipt:
    """Place the complete candidate directory; acceptance remains pending."""
    operation = _prepare(store, run_id, epoch, allow_replace)
    payload = cast(LandingIntent, operation["payload"])
    stage, previous = (_directory(payload[field]) for field in ("staging", "previous"))
    repo = repo_path(store, run_id)
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        require_no_landing_rollback(db, run_id)
        output = _confirmed_output(run, payload)
        if run["candidate"] != payload["candidate"] or rev(repo, CANDIDATE_REF) != payload["candidate"]:
            raise ValueError("交付意图中的候选已失效")
        build = _require_landing_build(store, run, payload)
        current = _directory_state(output)
        if current != payload["manifest"]:
            before = _directory_state(previous)
            original = payload["previous_manifest"]
            if current is not None and current != original:
                raise ValueError("落盘目录已变化，保留现有内容并停止")
            if before is not None and (before != original or current is not None):
                raise ValueError("原交付目录备份与意图不符")
            if current is None and original is not None and before != original:
                raise ValueError("原交付目录消失且没有匹配的备份")
            output.parent.mkdir(parents=True, exist_ok=True)
            existing_stage = _directory_state(stage)
            if existing_stage is None:
                with tempfile.TemporaryDirectory(prefix=".rtl-export-", dir=output.parent) as temporary:
                    prepared = Path(temporary) / "content"
                    if build is not None:
                        shutil.copytree(build["path"], prepared, symlinks=True)
                    else:
                        prepared.mkdir()
                        _extract(repo, payload["candidate"], prepared)
                    if manifest(prepared) != payload["manifest"]:
                        raise ValueError("暂存导出的文件与候选清单不符")
                    prepared.rename(stage)
                    _sync_directory(output.parent)
            elif existing_stage != payload["manifest"]:
                raise ValueError("暂存目录包含无法确认的内容")
            if current is not None:
                if _directory_state(output) != original or _directory_state(previous) is not None:
                    raise ValueError("原交付目录在提升前发生变化")
                output.rename(previous)
                _sync_directory(output.parent)
            elif output.exists() or output.is_symlink():
                raise ValueError("实际落盘路径在提升前发生变化")
            stage.rename(output)
            _sync_directory(output.parent)
        entry = check_landed(payload)
    result: LandingReceipt = {
        "operation_id": operation["id"], "candidate": payload["candidate"],
        "output": str(output), "entry": str(entry), "url": entry.as_uri(),
        "status": "pending_verification",
    }
    if "build" in payload:
        result["build"] = payload["build"]
    return result


def _accepted_landing_manifest(db, run, output):
    from .delivery_origin import _delivery, history, previous_output_matches, read_ledger
    from .requirements_history import _origin

    project = Path(run["project_root"])
    if "requirements" in run["baseline"]:
        local = _origin(db)
        for record in history(db, project, run["project_id"]):
            ledger = record["source_ledger"]
            if not ledger:
                continue
            if ledger == local:
                prior, operation = _delivery(db, record, project)
                if previous_output_matches(db, prior, project, output):
                    return operation["payload"]["manifest"]
            else:
                with read_ledger(ledger) as origin:
                    prior, operation = _delivery(origin, record, project)
                    if previous_output_matches(origin, prior, project, output):
                        return operation["payload"]["manifest"]
    for row in db.execute(
        "SELECT o.payload,o.receipt,r.project_id,r.project_root,r.output_path "
        "FROM operations o JOIN runs r ON r.id=o.run_id "
        "WHERE r.project_id=? AND r.status='delivered' AND o.kind='delivery' AND o.status='done' "
        "ORDER BY o.rowid DESC", (run["project_id"],),
    ):
        if previous_output_matches(db, row, project, output):
            payload, receipt = unpack(row["payload"]), unpack(row["receipt"])
            if payload["output"] != row["output_path"] or receipt["status"] != "delivered":
                raise ValueError("上一份已验收交付的台账与路径不符")
            return payload["manifest"]
    raise ValueError("找不到原入口对应的已验收交付，不能回退")


def _landing_backup(db, run, output, accepted):
    for row in db.execute(
        "SELECT o.id,o.payload FROM operations o JOIN runs r ON r.id=o.run_id "
        "WHERE r.project_id=? AND o.kind='delivery' ORDER BY o.rowid DESC", (run["project_id"],),
    ):
        payload = unpack(row["payload"])
        if payload["output"] != str(output) or payload["previous_manifest"] != accepted:
            continue
        suffix = row["id"].removeprefix("delivery-")
        expected = output.parent / f".rtl-before-{suffix}"
        if not re.fullmatch("[a-f0-9]{24}", suffix) or _directory(payload["previous"]) != expected:
            raise ValueError("原交付备份位置与登记路径不符")
        if _directory_state(expected) == accepted:
            return expected
    raise ValueError("没有与上一份已验收交付一致的备份")


def _rollback_landing_run(db, run_id, epoch):
    run = get_run(db, run_id)
    if run["status"] != "superseded":
        return guard(db, run_id, epoch, allow_paused=True)
    if type(epoch) is not int or epoch != run["epoch"]:
        raise ValueError(f"stale controller epoch for {run_id}; current epoch is {run['epoch']}")
    return run


def rollback_landing(store, run_id, epoch, operation_id, reason):
    """Restore the prior accepted entry and retain the rejected landing."""
    from .reservations import reservations

    if not isinstance(operation_id, str) or not re.fullmatch("delivery-[a-f0-9]{24}", operation_id):
        raise ValueError("请提供待回退的原交付操作编号")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("回退待验交付须登记原因")
    reason = reason.strip()
    rollback_id = f"rollback-{operation_id}"
    initial = store.run(run_id)
    with reservations(store.reservation_root) as registry:
        registry.check(store.path, run_id, initial["project_id"], (initial["project_root"], initial["output_path"]))
        with store.transaction() as db:
            run = _rollback_landing_run(db, run_id, epoch)
            registered = db.execute("SELECT root,output FROM projects WHERE id=?", (run["project_id"],)).fetchone()
            if not registered or (registered["root"], registered["output"]) != (run["project_root"], run["output_path"]):
                raise ValueError("原型路径已迁移，请在登记位置核对交付")
            landing = get_operation(db, run_id, operation_id)
            if not landing or landing["kind"] != "delivery" or landing["status"] == "done":
                raise ValueError("只能回退本轮尚未验收的交付")
            output = _confirmed_output(run, landing["payload"])
            existing = get_operation(db, run_id, rollback_id)
            if existing and existing["payload"]["reason"] != reason:
                raise ValueError("该交付已登记其他回退原因")
            if existing and existing["status"] == "done":
                return existing["receipt"]
            if not existing:
                require_no_landing_rollback(db, run_id)
                if run["status"] == "active" and landing["status"] != "intent":
                    raise ValueError("只能回退本轮当前待验交付")
                if run["status"] == "superseded":
                    supersede = get_operation(db, run_id, "supersede")
                    retained = (supersede or {}).get("receipt") or {}
                    if landing["payload"]["manifest"] != retained.get("retained_output_manifest"):
                        raise ValueError("该交付不是旧轮替代时实际保留的候选")
                acceptance = _latest_acceptance(db, run_id, operation_id)
                if acceptance and acceptance["status"] == "intent":
                    raise ValueError("请先撤销尚未完成的验收意图")
                accepted = _accepted_landing_manifest(db, run, output)
                rejected = landing["payload"]["manifest"]
                archive = output.parent / f".rtl-rejected-{operation_id.removeprefix('delivery-')}"
                if accepted == rejected or _directory_state(output) != rejected or _directory_state(archive) is not None:
                    raise ValueError("实际落盘或驳回归档目录与候选不符，保留现状")
                backup = _landing_backup(db, run, output, accepted)
                decision = {
                    "delivery_operation": operation_id, "reason": reason, "output": str(output),
                    "archive": str(archive), "backup": str(backup),
                    "rejected_manifest": rejected, "restored_manifest": accepted,
                }
                if run["status"] == "superseded":
                    registry.prepare(
                        store.path, run_id, run["project_id"], (run["project_root"], run["output_path"]),
                    )
                begin_operation(db, run_id, rollback_id, "landing-rollback", decision)

        with store.transaction() as db:
            run = _rollback_landing_run(db, run_id, epoch)
            operation = get_operation(db, run_id, rollback_id)
            decision = operation["payload"]
            output = _confirmed_output(run, decision)
            archive, backup = (_directory(decision[key]) for key in ("archive", "backup"))
            rejected, accepted = decision["rejected_manifest"], decision["restored_manifest"]
            if _accepted_landing_manifest(db, run, output) != accepted:
                raise ValueError("最新已验收交付已改变，保留现状")
            current = _directory_state(output)
            archived = _directory_state(archive)
            previous = _directory_state(backup)
            if (current, archived, previous) == (rejected, None, accepted):
                output.rename(archive)
                _sync_directory(output.parent)
                current, archived = _directory_state(output), _directory_state(archive)
            if (current, archived, previous) == (None, rejected, accepted):
                if _directory_state(backup) != accepted:
                    raise ValueError("上一份交付备份已改变，保留现状")
                backup.rename(output)
                _sync_directory(output.parent)
            if (_directory_state(output), _directory_state(archive), _directory_state(backup)) != (accepted, rejected, None):
                raise ValueError("实际目录与交付回退意图不符，保留现状")
            db.execute("UPDATE operations SET status='superseded' WHERE run_id=? AND id=?", (run_id, operation_id))
            receipt = {
                "operation_id": rollback_id, "delivery_operation_id": operation_id, "reason": reason,
                "status": "rolled_back", "output": str(output), "entry": str(output / "index.html"),
                "rejected_archive": str(archive), "restored_manifest": accepted,
            }
            finish_operation(db, run_id, rollback_id, receipt)
        registry.settle(store.path, run_id)
        return receipt


def _source_handoff_path(store, run_id, operation_id):
    return run_directory(store, run_id) / "deliveries" / f"{operation_id}-source"


def _ensure_source_handoff(store, run_id, receipt: AcceptanceReceipt) -> None:
    if "source" not in receipt:
        return
    path = _source_handoff_path(store, run_id, receipt["operation_id"])
    if _directory(receipt["source"]) != path:
        raise ValueError("源码交接路径与交付记录不符")
    repo = repo_path(store, run_id)
    expected = _tree_manifest(repo, receipt["candidate"])
    if "package.json" not in expected or expected.get("package-lock.json") != receipt["build"]["lockfile_sha256"]:
        raise ValueError("源码交接的依赖锁与构建记录不符")
    if any("node_modules" in PurePosixPath(name).parts for name in expected):
        raise ValueError("源码交接含已安装依赖")
    current = _directory_state(path)
    if current is not None:
        if current != expected:
            raise ValueError("源码交接内容与已验收提交不符")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rtl-source-", dir=path.parent) as temporary:
        staging = Path(temporary) / "source"
        staging.mkdir()
        _extract(repo, receipt["candidate"], staging)
        if manifest(staging) != expected:
            raise ValueError("暂存源码与已验收提交不符")
        staging.rename(path)
        _sync_directory(path.parent)


def _safe_project_child(project, relative):
    path = project / relative
    if path.resolve() != path:
        raise ValueError(f"变更记忆路径经过链接：{path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"变更记忆目标不是普通文件：{path}")
    return path


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        if path.exists():
            os.fchmod(file.fileno(), path.stat().st_mode & 0o777)
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    try:
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _append_marker(path, marker, content):
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker not in previous:
        _atomic_text(path, previous.rstrip() + ("\n\n" if previous else "") + content + "\n")


def _change_id(operation_id):
    return operation_id.replace("delivery-", "CHG-", 1)


def _change_marker(operation_id):
    return f"<!-- rtl:{operation_id} -->"


def _change_content(run: RunRecord, change_id: str, receipt: AcceptanceReceipt) -> str:
    from .requirements_history import change_section

    entry = receipt["entry"]
    records = ", ".join(receipt["evidence_ids"])
    approval = receipt["approval"] or "Master 依据本轮门禁裁决"
    content = (
        f"# {change_id}\n\n"
        f"- 运行编号：`{run['id']}`\n"
        f"- 候选版本：`{receipt['candidate']}`\n"
        f"- 需求修订：`{run['baseline'].get('revision', '未登记')}`\n"
        f"- 实际入口：`{entry}`\n"
        f"- 验收依据：{approval}\n"
        f"- 验证证据：{records}\n"
    )
    if "source" in receipt:
        content += (
            f"- React 源码交接：`{receipt['source']}`\n"
            f"- 构建产物清单摘要：`{receipt['build']['artifact_manifest_sha256']}`\n"
            f"- 依赖锁摘要：`{receipt['build']['lockfile_sha256']}`\n"
        )
    return content + change_section(run, change_id)


def _reconcile_text(path, content, prior_contents, mismatch):
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return
        if existing not in prior_contents:
            raise ValueError(mismatch)
    _atomic_text(path, content)


def _memory_paths(run, change_id):
    project = _directory(run["project_root"])
    project_info = _safe_project_child(project, "memory/project.md")
    if project_info.exists():
        recorded = re.findall(r"^Project-ID: (\S+)\s*$", project_info.read_text(encoding="utf-8"), re.M)
        if recorded and any(value != run["project_id"] for value in recorded):
            raise ValueError("项目记忆编号与运行不符")
    return (
        project_info,
        _safe_project_child(project, f"memory/changes/{change_id}.md"),
        _safe_project_child(project, "docs/change-log.md"),
    )


def _memory_change(run: RunRecord, change_id: str, receipt: AcceptanceReceipt, prior_evidence_ids):
    current = _change_content(run, change_id, receipt)
    previous = (
        _change_content(run, change_id, {**receipt, "evidence_ids": ids})
        for ids in prior_evidence_ids
    )
    return current, previous


def _check_memory_destination(run: RunRecord, operation_id: str, receipt: AcceptanceReceipt, prior_evidence_ids):
    change_id = _change_id(operation_id)
    _, change, _ = _memory_paths(run, change_id)
    if change.exists():
        current, previous = _memory_change(run, change_id, receipt, prior_evidence_ids)
        existing = change.read_text(encoding="utf-8")
        if existing != current and existing not in previous:
            raise ValueError("既有变更记忆与此次交付记录不一致")


def _write_memory(run: RunRecord, operation_id: str, receipt: AcceptanceReceipt, prior_evidence_ids=()) -> str:
    change_id = _change_id(operation_id)
    project_info, change, index = _memory_paths(run, change_id)
    marker = f"Project-ID: {run['project_id']}"
    if project_info.exists():
        _append_marker(project_info, marker, marker)
    else:
        _atomic_text(project_info, f"# 项目记忆\n\n{marker}\n")
    current, previous = _memory_change(run, change_id, receipt, prior_evidence_ids)
    _reconcile_text(change, current, previous, "既有变更记忆与此次交付记录不一致")
    if not index.exists():
        _atomic_text(index, "# 变更记录\n")
    marker = _change_marker(operation_id)
    _append_marker(index, marker, f"- [{change_id}](../memory/changes/{change_id}.md) 已验收 · `{receipt['candidate'][:12]}` {marker}")
    return change_id


def _archive_content(receipt: DeliveryReceipt, payload: LandingIntent, baseline: Baseline) -> str:
    return json.dumps(
        {"receipt": receipt, "manifest": payload["manifest"], "baseline": baseline},
        ensure_ascii=False, indent=2, sort_keys=True,
    ) + "\n"


def _delivery_context(
    db, run: RunRecord, candidate: str, operation_id: str, entry: Path,
    authors: set[str], manifest: dict[str, str], previous: AcceptanceContext | None = None,
) -> tuple[AcceptanceContext, list[str]]:
    from .feature_map import require_live_routes
    from .verification import require_feature_assertions

    run_id, baseline = run["id"], run["baseline"]
    rows = []
    for row in db.execute(
        "SELECT * FROM evidence WHERE run_id=? AND kind='delivery' AND target=? ORDER BY rowid",
        (run_id, candidate),
    ):
        context = unpack(row["context"])
        if (
            row["actor"] not in authors and context.get("operation_id") == operation_id
            and context.get("path") == str(entry) and context.get("url") == entry.as_uri()
            and inputs_match(baseline, context, unpack(row["cases"]))
        ):
            rows.append((row, context))
    if not rows:
        raise ValueError("缺少实际落盘路径的独立验证")
    pinned = previous or rows[0][1]
    context_filter: AcceptanceInputs = {
        "operation_id": operation_id, "path": str(entry), "url": entry.as_uri(),
        **delivery_revisions(baseline, pinned),
    }
    matching_inputs = [
        (row, context) for row, context in rows
        if all(context.get(key) == value for key, value in context_filter.items())
    ]
    if not matching_inputs:
        raise ValueError("缺少使用原验收输入修订的独立验证")
    approved_browsers = delivery_browsers(baseline, pinned, matching_inputs[0][1])
    task_specs = None
    if baseline.get("feature_route_checks"):
        task_specs = {
            row["id"]: unpack(row["spec"])
            for row in db.execute("SELECT id,spec FROM tasks WHERE run_id=?", (run_id,))
        }
    map_digest = None
    for family in approved_browsers:
        current_browser = [
            (row, context) for row, context in matching_inputs
            if browser_family(context["browser"]) == family
        ]
        try:
            require_cases(
                db, run_id, "delivery", candidate, baseline.get("mandatory_cases", []),
                context_filter=context_filter, excluded_actors=authors, require_any=True, baseline=baseline,
                reject_failures=True, browser_families={family},
            )
            if baseline.get("change_assertion_checks"):
                require_feature_assertions(run, current_browser)
            if task_specs is not None:
                routes = require_live_routes(run, entry, manifest, current_browser, task_specs=task_specs)
                observed_map = routes["map_sha256"]
                if map_digest and observed_map != map_digest:
                    raise ValueError("验收浏览器之间的功能地图摘要不一致，请重新验证")
                map_digest = observed_map
                if previous and previous.get("feature_map_sha256", map_digest) != map_digest:
                    raise ValueError("已登记验收对应的功能地图已改变，请先核对原验收意图")
        except ValueError as exc:
            raise ValueError(f"{family} {exc}") from exc
    accepted_context: AcceptanceContext = {**context_filter, "browser_families": approved_browsers}
    if map_digest is not None:
        accepted_context["feature_map_sha256"] = map_digest
    evidence_ids = [
        row["id"] for row, context in matching_inputs
        if browser_family(context["browser"]) in approved_browsers and evidence_intact(context)
    ]
    return accepted_context, evidence_ids


def _latest_acceptance(db, run_id, delivery_id):
    prefix = delivery_id.replace("delivery-", "accept-", 1)
    row = db.execute(
        "SELECT id FROM operations WHERE run_id=? AND kind='acceptance' "
        "AND (id=? OR id LIKE ?) ORDER BY rowid DESC LIMIT 1",
        (run_id, prefix, prefix + "-%"),
    ).fetchone()
    return get_operation(db, run_id, row["id"]) if row else None


def cancel_acceptance(store, run_id, epoch, operation_id, reason):
    """Withdraw an approval before any accepted project or archive output exists."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("请记录撤销验收意图的原因")
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        delivery = get_operation(db, run_id, operation_id)
        if not delivery or delivery["kind"] != "delivery" or delivery["status"] != "intent":
            raise ValueError("找不到待验收的交付操作")
        acceptance = _latest_acceptance(db, run_id, operation_id)
        if not acceptance:
            raise ValueError("该交付操作没有已登记的验收意图")
        if acceptance["status"] == "superseded":
            if acceptance["receipt"]["reason"] != reason:
                raise ValueError("该验收意图已登记了其他撤销原因")
            return acceptance["receipt"]
        if acceptance["status"] != "intent":
            raise ValueError("仅能撤销尚未完成的验收意图")
        project = _directory(run["project_root"])
        change = _safe_project_child(project, f"memory/changes/{_change_id(operation_id)}.md")
        index = _safe_project_child(project, "docs/change-log.md")
        archive = run_directory(store, run_id) / "deliveries" / f"{operation_id}.json"
        if (
            change.exists() or archive.exists() or archive.is_symlink()
            or (index.exists() and _change_marker(operation_id) in index.read_text(encoding="utf-8"))
        ):
            raise ValueError("验收已部分回写，请恢复原验收意图")
        receipt = {
            "operation_id": acceptance["id"], "delivery_operation_id": operation_id,
            "status": "superseded", "reason": reason,
        }
        db.execute(
            "UPDATE operations SET status='superseded', receipt=? WHERE run_id=? AND id=?",
            (pack(receipt), run_id, acceptance["id"]),
        )
        return receipt


def _check_approval_basis(approval: str | None, receipt: AcceptanceReceipt, *, pending=False) -> None:
    if approval is None or approval == receipt.get("approval"):
        return
    message = "传入的人工验收依据与已登记的验收决定不符"
    if pending:
        message += "。尚未回写时可用 cancel-acceptance 撤销后重新登记，已部分回写时请恢复原验收意图"
    raise ValueError(message)


def _acceptance_intent(store, run_id, epoch, operation_id, actor, approval) -> OperationRecord:
    from .requirements_history import check_ledger_destination
    from .requirements_run import require_prepared

    repo = repo_path(store, run_id)
    with store.transaction() as db:
        operation = get_operation(db, run_id, operation_id)
        if not operation or operation["kind"] != "delivery" or operation["status"] == "superseded":
            raise ValueError("找不到当前交付意图")
        payload = cast(LandingIntent, operation["payload"])
        _confirmed_output(get_run(db, run_id), payload)
        if operation["status"] == "done":
            receipt = cast(DeliveryReceipt, operation["receipt"])
            _check_approval_basis(approval, receipt)
            check_landed(payload)
            _ensure_source_handoff(store, run_id, receipt)
            return operation
        run = guard(db, run_id, epoch)
        require_no_landing_rollback(db, run_id)
        require_prepared(db, run)
        check_ledger_destination(run)
        candidate = payload["candidate"]
        if run["candidate"] != candidate or rev(repo, CANDIDATE_REF) != candidate:
            raise ValueError("实际候选已变化，不能登记交付")
        _require_integrated(db, run_id)
        _require_landing_build(store, run, payload)
        entry = check_landed(payload)
        baseline = run["baseline"]
        latest = _latest_acceptance(db, run_id, operation_id)
        existing = latest if latest and latest["status"] == "intent" else None
        if existing:
            _check_approval_basis(approval, existing["payload"]["receipt"], pending=True)
        if baseline.get("human_acceptance") and not (approval or existing):
            raise ValueError("本轮要求人工验收，请登记已确认的依据")
        context_filter, evidence_ids = _delivery_context(
            db, run, candidate, operation_id, entry, delivery_authors(db, run_id), payload["manifest"],
            existing["payload"]["context"] if existing else None,
        )
        if existing:
            approved = cast(AcceptanceIntent, existing["payload"])
            old_evidence_ids = approved["receipt"]["evidence_ids"]
            if old_evidence_ids == evidence_ids and approved["context"] == context_filter:
                return existing
            history = approved.get("previous_evidence_ids", [])
            if old_evidence_ids != evidence_ids and old_evidence_ids not in history:
                history = [*history, old_evidence_ids]
            approved = {
                "receipt": {**approved["receipt"], "evidence_ids": evidence_ids},
                "context": context_filter,
                "previous_evidence_ids": history,
            }
            db.execute(
                "UPDATE operations SET payload=? WHERE run_id=? AND id=?",
                (pack(approved), run_id, existing["id"]),
            )
            return {**existing, "payload": approved}
        pending_receipt: AcceptanceReceipt = {
            "operation_id": operation_id, "candidate": candidate, "entry": str(entry),
            "actor": actor, "approval": approval, "evidence_ids": evidence_ids, "status": "delivered",
        }
        if "feature_map_sha256" in context_filter:
            pending_receipt["feature_map_sha256"] = context_filter["feature_map_sha256"]
        if "acceptance_browsers" in baseline:
            pending_receipt["acceptance_browsers"] = context_filter["browser_families"]
        if "build" in payload:
            pending_receipt["build"] = payload["build"]
            pending_receipt["source"] = str(_source_handoff_path(store, run_id, operation_id))
        acceptance_id = operation_id.replace("delivery-", "accept-", 1)
        if latest:
            acceptance_id += f"-{uuid4().hex}"
        return begin_operation(
            db, run_id, acceptance_id, "acceptance", {"receipt": pending_receipt, "context": context_filter},
        )


def _commit_delivery(store, run_id, epoch, operation_id, actor, approval) -> DeliveryReceipt:
    from .requirements_history import check_ledger_destination, record_delivery_history
    from .requirements_run import require_prepared

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("请登记验收裁决者")
    if approval is not None and (not isinstance(approval, str) or not approval.strip()):
        raise ValueError("人工验收依据不能为空")
    authorization = _acceptance_intent(store, run_id, epoch, operation_id, actor, approval)
    if authorization["kind"] == "delivery":
        return cast(DeliveryReceipt, authorization["receipt"])
    with store.transaction() as db:
        operation = get_operation(db, run_id, operation_id)
        payload = cast(LandingIntent, operation["payload"])
        _confirmed_output(get_run(db, run_id), payload)
        if operation["status"] == "done":
            existing = cast(DeliveryReceipt, operation["receipt"])
            _check_approval_basis(approval, existing)
            check_landed(payload)
            _ensure_source_handoff(store, run_id, existing)
            return existing
        run = guard(db, run_id, epoch)
        require_no_landing_rollback(db, run_id)
        require_prepared(db, run)
        check_ledger_destination(run)
        authorization = get_operation(db, run_id, authorization["id"])
        if authorization["status"] != "intent":
            raise ValueError("验收意图已撤销，请重新核对本轮状态")
        approved = cast(AcceptanceIntent, authorization["payload"])
        pending_receipt = approved["receipt"]
        candidate = pending_receipt["candidate"]
        if run["candidate"] != candidate or rev(repo_path(store, run_id), CANDIDATE_REF) != candidate:
            raise ValueError("实际候选已变化，不能登记交付")
        _require_integrated(db, run_id)
        _require_landing_build(store, run, payload)
        entry = check_landed(payload)
        context, evidence_ids = _delivery_context(
            db, run, candidate, operation_id, entry, delivery_authors(db, run_id), payload["manifest"],
            approved["context"],
        )
        if context != approved["context"] or evidence_ids != pending_receipt["evidence_ids"]:
            raise ValueError("验收证据在回写前发生变化，请重试原交付操作")
        _ensure_source_handoff(store, run_id, pending_receipt)
        prior_ids = approved.get("previous_evidence_ids", ())
        traced = "requirements" in run["baseline"]
        if traced:
            _check_memory_destination(run, operation_id, pending_receipt, prior_ids)
            change_id = _change_id(operation_id)
        else:
            change_id = _write_memory(run, operation_id, pending_receipt, prior_ids)
        receipt: DeliveryReceipt = {**pending_receipt, "change_id": change_id}
        archive = run_directory(store, run_id) / "deliveries" / f"{operation_id}.json"
        if archive.resolve() != archive:
            raise ValueError("交付归档路径与已登记的交付位置不符")
        baseline = run["baseline"]
        _reconcile_text(
            archive,
            _archive_content(receipt, payload, baseline),
            (_archive_content({**receipt, "evidence_ids": ids}, payload, baseline) for ids in prior_ids),
            "交付归档与已登记的验收决定不符",
        )
        check_landed(payload)
        if traced:
            record_delivery_history(store, db, run, receipt)
        finish_operation(db, run_id, operation_id, receipt)
        finish_operation(db, run_id, authorization["id"], receipt)
        db.execute("UPDATE runs SET status='delivered' WHERE id=?", (run_id,))
        return receipt


def _publish_delivered_requirements(store, run_id, operation_id, receipt: DeliveryReceipt) -> None:
    from .requirements_history import check_ledger_destination, require_latest_delivery_for_repair, write_ledger
    from .requirements_run import _snapshot, _snapshot_target

    with store.transaction() as db:
        run = get_run(db, run_id)
        operation = get_operation(db, run_id, operation_id)
        if run["status"] != "delivered" or operation["status"] != "done" or operation["receipt"] != receipt:
            raise ValueError("需求台账只能从本轮已提交的交付回执恢复")
        registered = db.execute("SELECT root,output FROM projects WHERE id=?", (run["project_id"],)).fetchone()
        if not registered or (registered["root"], registered["output"]) != (run["project_root"], run["output_path"]):
            raise ValueError("项目路径已迁移，不能在原位置恢复需求台账")
        _confirmed_output(run, operation["payload"])
        require_latest_delivery_for_repair(db, run, receipt["change_id"])
        check_landed(operation["payload"])
        check_ledger_destination(run)
        approval = _latest_acceptance(db, run_id, operation_id)
        prior_ids = approval["payload"].get("previous_evidence_ids", ()) if approval else ()
        _check_memory_destination(run, operation_id, receipt, prior_ids)
        _reconcile_text(_snapshot_target(run), _snapshot(run), (), "已冻结的 S1 快照与原交付不符")
        _write_memory(run, operation_id, receipt, prior_ids)
        write_ledger(store, db, run)
        check_landed(operation["payload"])


def finalize(store, run_id, epoch, operation_id, actor, approval=None) -> DeliveryReceipt:
    """Commit the accepted delivery before publishing its human-readable trace."""
    run = store.run(run_id)
    _confirmed_output(run)
    if "requirements" not in run["baseline"]:
        return _commit_delivery(store, run_id, epoch, operation_id, actor, approval)

    from .reservations import reservations

    # Another ledger cannot initialize a newer run between this terminal commit
    # and publication, or while we repair the latest already committed trace.
    with reservations(store.reservation_root) as registry:
        registry.check(store.path, run_id, run["project_id"], (run["project_root"], run["output_path"]))
        receipt = _commit_delivery(store, run_id, epoch, operation_id, actor, approval)
        _publish_delivered_requirements(store, run_id, operation_id, receipt)
        return receipt
