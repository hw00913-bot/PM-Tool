"""CLI for a Master Agent to operate a local RTL run."""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

from . import artifacts, browser, contracts, delivery, dispatch, feature_map, frontend, lifecycle, requirements, requirements_run, s1_review, verification
from .skills import REQUIREMENT_STEPS, read_skill
from .state import LoopError, Store, begin_operation, decode, finish_operation, get_operation, get_run, get_task, guard, location_key, overlaps, safe_id, unpack

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def baseline(path, *, require_assertions=False, require_checkpoints=False, require_change_proof=False):
    value = read_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("revision"), str) or not value["revision"].strip():
        raise ValueError("Baseline requires a revision")
    value["revision"] = value["revision"].strip()
    cases = value.get("mandatory_cases")
    if not isinstance(cases, list) or not cases or any(not isinstance(case, str) or not case.strip() for case in cases):
        raise ValueError("Baseline requires a nonempty list of mandatory case IDs")
    if len(cases) != len(set(cases)):
        raise ValueError("Mandatory case IDs must be unique")
    if type(value.get("human_acceptance", False)) is not bool:
        raise ValueError("human_acceptance must be a boolean")
    value.setdefault("human_acceptance", False)
    if not {"suite_digests", "suite_revision", "fixture_revision"} <= value.keys():
        raise ValueError("新轮基线须冻结 suite_digests、suite_revision 和 fixture_revision")
    verification.validate_input_baseline(value)
    if "requirements" in value:
        value["requirements"] = requirements.normalize_plan(
            value["requirements"], cases, require_assertions=require_assertions, require_checkpoints=require_checkpoints,
        )
    if require_assertions:
        value["change_assertion_checks"] = True
    if require_checkpoints:
        value["assertion_checkpoint_checks"] = True
    if require_change_proof:
        value["change_proof_checks"] = True
    return value


def must_be_absolute(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("Use a confirmed absolute path")
    return path.resolve()


def separated_paths(data, project, output):
    if overlaps(ROOT, project) or overlaps(ROOT, output) or overlaps(ROOT, data):
        raise ValueError("Loop source, runtime data, and prototype must be separate")
    if overlaps(data, project) or overlaps(data, output):
        raise ValueError("Runtime data cannot overlap the project or delivery")
    project_key, output_key = location_key(project), location_key(output)
    if output_key == project_key[:len(output_key)]:
        raise ValueError("Delivery cannot replace or contain the project root")
    from .managed_paths import check_output_path

    check_output_path(project, output)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=must_be_absolute, help="persistent runtime root outside the prototype and tool")
    sub = parser.add_subparsers(dest="action", required=True)

    def command(name, *, mutates=True, run=True):
        item = sub.add_parser(name)
        if run:
            item.add_argument("run")
        if mutates:
            item.add_argument("--epoch", type=int, required=True)
        return item

    init = command("init", mutates=False, run=False)
    init.add_argument("--project", type=must_be_absolute, required=True)
    init.add_argument("--output", type=must_be_absolute)
    init.add_argument("--baseline", required=True)
    init.add_argument("--suite", action="append", required=True, help="frozen suite JSON, repeat for each digest")
    init.add_argument("--s1-record", type=must_be_absolute, required=True, help="recorded S1 steps and approval")
    init.add_argument("--frontend", choices=("react", "static"))
    init.add_argument("--project-id")
    init.add_argument("--run-id")
    init.add_argument("--from-run", help="reuse the same project's delivered or superseded candidate as source")
    s1_confirm = command("s1-confirm", mutates=False, run=False)
    s1_confirm.add_argument("--project", type=must_be_absolute, required=True)
    s1_confirm.add_argument("--contract", required=True)
    s1_confirm.add_argument("--approval", required=True)
    s1_confirm.add_argument("--s1-record", type=must_be_absolute, required=True)
    s1_confirm.add_argument("--analysis-package", type=must_be_absolute,
                            help="bind a verified 04 frozen package to this S1 review")
    analysis_import = command("import-analysis", mutates=False, run=False)
    analysis_import.add_argument("package", type=must_be_absolute, help="04 frozen ZIP or extracted package directory")
    s1_handoff = command("s1-handoff", mutates=False, run=False)
    s1_handoff.add_argument("--s1-record", type=must_be_absolute, required=True)
    s1_handoff.add_argument("--step", choices=s1_review.STEPS[1:], required=True)
    s1_handoff.add_argument("--artifact", required=True, help="JSON draft containing the completed handoff")
    command("prepare")
    command("status", mutates=False).add_argument("--next", action="store_true", help="show read-only next-action guidance")
    command("map-check", mutates=False)
    command("requirements-check", mutates=False)
    command("runs", mutates=False, run=False)
    command("register-ledger", mutates=False, run=False)
    rebind = command("rebind", mutates=False, run=False)
    rebind.add_argument("project_id")
    rebind.add_argument("--project", type=must_be_absolute, required=True)
    rebind.add_argument("--output", type=must_be_absolute)
    rebind.add_argument("--epoch", type=int)
    command("prepare-attempt").add_argument("attempt")
    command("takeover")
    pause_run = command("pause-run")
    pause_run.add_argument("--reason", required=True)
    pause_run.add_argument("--checkpoint", help="JSON object with the verified work and first action on resume")
    command("resume-run").add_argument("pause_id")
    supersede = command("supersede")
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--approval", required=True, help="confirmed user decision")
    supersede.add_argument("--stopped", action="store_true", help="all old workers are stopped or isolated")
    command("task-add").add_argument("spec")
    claim = command("claim")
    claim.add_argument("task", nargs="?")
    claim.add_argument("--phase", choices=("design", "implement"), default="implement")
    claim.add_argument("--stage", choices=dispatch.DISPATCH_STAGES,
                       help="dispatch stage for a new attempt (default S2); resumed attempts retain their stage")
    start = command("start")
    start.add_argument("attempt")
    start.add_argument("--worker", required=True)
    start.add_argument("--host-ref", help="observed worker handle in the Agent host")
    codex_start = command("codex-start")
    codex_start.add_argument("attempt")
    codex_start.add_argument("--socket", type=must_be_absolute, help="local Codex App Server socket")
    codex_attach = command("codex-attach")
    codex_attach.add_argument("attempt")
    codex_attach.add_argument("thread")
    command("codex-status", mutates=False).add_argument("attempt")
    command("codex-interrupt").add_argument("attempt")
    pause = command("pause")
    pause.add_argument("attempt")
    pause.add_argument("--stopped", action="store_true", required=True)
    pause.add_argument("--host-ref", help="host handle of the worker confirmed stopped")
    fence = command("fence")
    fence.add_argument("attempt")
    fence.add_argument("--stopped", action="store_true")
    fence.add_argument("--host-ref", help="host handle of the worker confirmed isolated")
    fence.add_argument("--isolate-codex", metavar="REASON", help="move and permanently fence an old Codex workspace")
    submit = command("submit")
    submit.add_argument("attempt")
    submit.add_argument("--actor", required=True)
    submit.add_argument("--result", help="JSON summary, required and nonempty for design attempts")
    submit.add_argument("--revalidate", action="store_true", help="reverify an unchanged, previously integrated implementation")
    proposed = command("contract-propose")
    for field in ("edge", "version", "provider", "consumer", "spec"):
        proposed.add_argument(field)
    confirmed = command("contract-confirm")
    for field in ("edge", "version", "task"):
        confirmed.add_argument(field)
    activate = command("contract-activate")
    activate.add_argument("edge")
    activate.add_argument("version")
    stage = command("stage")
    stage.add_argument("attempts", nargs="+")
    stage.add_argument("--require-case", action="append", default=[], help="additional required independent integration case")
    stage.add_argument("--require-edge", action="append", default=[], help="additional active contract to verify")
    build = command("build")
    target = build.add_mutually_exclusive_group()
    target.add_argument("--attempt")
    target.add_argument("--operation")
    evidence = command("evidence")
    evidence.add_argument("kind", choices=("self", "integration", "contract", "delivery"))
    evidence.add_argument("target")
    evidence.add_argument("--actor", required=True)
    evidence.add_argument("--context")
    evidence.add_argument("--report", required=True, help="browser verification report JSON")
    command("promote").add_argument("operation")
    rollback = command("rollback-integration")
    rollback.add_argument("operation", help="unfinished integration operation ID")
    rollback.add_argument("--reason", required=True)
    land = command("land")
    land.add_argument("--allow-replace", action="store_true")
    landing_rollback = command("rollback-landing")
    landing_rollback.add_argument("operation", help="rejected delivery operation ID")
    landing_rollback.add_argument("--reason", required=True)
    finalize = command("finalize")
    finalize.add_argument("operation")
    finalize.add_argument("--actor", required=True)
    finalize.add_argument("--approval", help="recorded user approval reference when required")
    cancel = command("cancel-acceptance")
    cancel.add_argument("operation", help="delivery operation ID")
    cancel.add_argument("--reason", required=True)
    skill = command("skill", mutates=False, run=False)
    skill.add_argument("skill_id")
    skill.add_argument("--role", required=True)
    skill.add_argument("--stage", required=True)
    skill.add_argument("--run")
    skill.add_argument("--epoch", type=int)
    skill.add_argument("--step", choices=sorted(REQUIREMENT_STEPS))
    skill.add_argument("--purpose", help="select additional reference sections for this activity")
    skill.add_argument("--task", help="use the registered task in the current run to select references")
    skill.add_argument("--s1-record", type=must_be_absolute, help="record the step before starting a run")
    browser = command("browser", mutates=False, run=False)
    browser.add_argument("entry")
    browser.add_argument("suite")
    browser.add_argument("report")
    browser.add_argument("--feature-map", type=must_be_absolute)
    browser.add_argument("--feature-entries", action="store_true", help="record mapped feature entries and where case steps execute")
    return parser


def status(store, run_id, *, next_only=False):
    with store.transaction(immediate=False) as db:
        run = get_run(db, run_id)
        pause = lifecycle.run_pause_status(db, run_id)
        operations = [
            {"id": row["id"], "kind": row["kind"], "status": row["status"],
             "payload": unpack(row["payload"]), "receipt": unpack(row["receipt"]) if row["receipt"] else None}
            for row in db.execute("SELECT * FROM operations WHERE run_id=? ORDER BY rowid", (run_id,))
        ]
        attempts = [
            {"id": row["id"], "task_id": row["task_id"], "state": row["state"],
             "workspace": row["workspace"], "worker": row["worker"], "input": unpack(row["input"]),
             "result": unpack(row["result"]) if row["result"] else None,
             **({"operation_id": row["operation_id"]} if next_only else {})}
            for row in db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY rowid", (run_id,))
        ]
        tasks = [decode(row, "spec") for row in db.execute(
            "SELECT * FROM tasks WHERE run_id=? ORDER BY rowid", (run_id,),
        )]
        snapshot = {**run, "pause": pause, "tasks": tasks, "attempts": attempts,
                    "contracts": contracts.read_contracts(db, run_id), "operations": operations}
        if next_only:
            snapshot["evidence"] = [decode(row, "cases", "context") for row in db.execute(
                "SELECT rowid AS sequence,id,kind,target,actor,cases,context FROM evidence "
                "WHERE run_id=? AND kind='delivery' ORDER BY rowid", (run_id,),
            )]
            if (run["status"] == "active" and not pause and all(task["status"] == "integrated" for task in tasks)
                    and any(op["kind"] == "delivery" and op["status"] == "intent" for op in operations)):
                try:
                    snapshot["delivery_authors"] = delivery.delivery_authors(db, run_id)
                except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError) as error:
                    snapshot["delivery_author_error"] = str(error)
    if next_only:
        from .next_actions import summarize

        project_id = safe_id(run["project_id"], "project ID")
        snapshot["repository"] = str(store.root / "projects" / project_id / "runs" / safe_id(run_id, "run ID") / "repository")
        return summarize(snapshot, store.data_root)
    return snapshot


def record_skill_load(db, run_id, skill, *, attempt_id=None):
    event = "skill-" + uuid.uuid4().hex
    payload = {key: value for key, value in skill.items() if key != "content"}
    if attempt_id is not None:
        payload["attempt_id"] = attempt_id
    begin_operation(db, run_id, event, "skill", payload)
    finish_operation(db, run_id, event, {"loaded": True})
    return {**skill, "operation_id": event}


def claim(store, args):
    item = dispatch.reserve(store, args.run, args.epoch, args.task, phase=args.phase, stage=args.stage)
    if item is None:
        return {"state": "waiting"}
    return prepare_attempt(store, args.run, args.epoch, item["id"])


def scene_style(run, attempt):
    style = read_skill("prototype-design-style", "scene", dispatch.attempt_stage(attempt), task=attempt["input"]["spec"])
    path = Path(run["project_root"]) / "docs" / "design-style.md"
    if not path.exists() and not path.is_symlink():
        return style
    if path.resolve() != path or not path.is_file():
        raise ValueError(f"项目风格基线须为项目内普通文件：{path}")
    content = path.read_bytes()
    style["project_style"] = {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}
    style["content"] += "\n\n# 项目风格基线\n\n" + content.decode("utf-8")
    return style


def prepare_attempt(store, run_id, epoch, attempt_id):
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        attempt = store.attempt(attempt_id)
        if attempt["run_id"] != run_id or attempt["state"] not in {"reserved", "running"}:
            raise ValueError("Only a reserved or running attempt can prepare a workspace")
        dispatch.require_workspace_available(db, run_id, attempt_id)
        dispatch.require_bound_requirements(db, run, attempt)
        dispatch.ensure_current_inputs(db, run_id, attempt)
        try:
            skill = read_skill(
                "scene-delivery", "scene", dispatch.attempt_stage(attempt), task=attempt["input"]["spec"],
            )
            style = scene_style(run, attempt)
        except ValueError as error:
            raise LoopError(
                f"尝试 {attempt_id} 的场景 Skill 加载失败。修复后用 "
                f"prepare-attempt {run_id} {attempt_id} --epoch {epoch} 继续。{error}"
            ) from error
        workspace = artifacts.make_worktree(store, run_id, attempt_id)
    dispatch.bind_workspace(store, run_id, epoch, attempt_id, str(workspace))
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        current, _ = dispatch.current_attempt(db, run_id, attempt_id)
        if current["state"] not in {"reserved", "running"}:
            raise ValueError("Only a reserved or running attempt can return scene instructions")
        dispatch.require_workspace_available(db, run_id, attempt_id)
        dispatch.require_bound_requirements(db, run, current)
        dispatch.ensure_current_inputs(db, run_id, current)
        skill = record_skill_load(db, run_id, skill, attempt_id=attempt_id)
        style = record_skill_load(db, run_id, style, attempt_id=attempt_id)
    return {**current, "skill": skill, "style_skill": style}


def prepare_codex_attempt(store, run_id, epoch, attempt_id):
    prepared = prepare_attempt(store, run_id, epoch, attempt_id)
    return {**prepared["skill"], "style_skill": prepared["style_skill"]}


def submit(store, args):
    with store.transaction(immediate=False) as db:
        run = guard(db, args.run, args.epoch)
        attempt = store.attempt(args.attempt)
        if attempt["run_id"] != args.run or attempt["state"] not in {"running", "submitted", "completed"}:
            raise ValueError("Attempt is not running in this run")
        dispatch.require_bound_requirements(db, run, attempt)
    if attempt["worker"] != args.actor:
        raise ValueError("Submission actor must match the worker that started this attempt")
    summary = read_json(args.result) if args.result else {}
    if not isinstance(summary, dict):
        raise ValueError("Result must be a JSON object")
    result = {"summary": summary, "actor": args.actor}
    capture = None
    if attempt["input"].get("phase") == "design":
        if args.revalidate:
            raise ValueError("只有实现尝试可以登记仅复验结果")
    else:
        def capture(commit_changes):
            return artifacts.capture_submission(
                store, args.run, args.attempt, args.actor, revalidate=args.revalidate, commit_changes=commit_changes,
            )

    return dispatch.submit(store, args.run, args.epoch, args.attempt, result, capture=capture)


def record_evidence(store, args):
    context = read_json(args.context) if args.context else {}
    return verification.record_browser_report(
        store, args.run, args.epoch, args.kind, args.target, args.actor, args.report, context,
    )


def select_frontend(store, project, project_id, baseline, selected, source_run=None):
    from .delivery_origin import find_record, history, profile_for_source
    from .requirements_history import latest_baseline

    if "frontend" in baseline:
        recorded = frontend.profile(baseline)
        if selected and selected != recorded:
            raise ValueError("--frontend 与需求基线的技术栈不一致")
        return recorded
    if selected:
        return selected
    local = [item for item in store.runs() if item["project_id"] == project_id or
             Path(item["project_root"]) == project]
    if source_run is not None:
        previous = next((item for item in local if item["id"] == source_run), None)
        if previous:
            return frontend.profile(previous["baseline"])
    elif local and local[-1]["status"] != "delivered":
        return frontend.profile(local[-1]["baseline"])
    if project_id:
        with store.transaction(immediate=False) as db:
            if source_run is not None:
                record = find_record(history(db, project, project_id), source_run)
                if record:
                    return profile_for_source(record, project)
            previous = latest_baseline(db, project, project_id)
        if previous is not None:
            return frontend.profile(previous)
    if local:
        return frontend.profile(local[-1]["baseline"])
    if (project / "prototype-src").exists() or not (project / "prototype" / "index.html").is_file():
        return "react"
    return "static"


def build_candidate(store, args):
    attempt = operation = None
    with store.transaction() as db:
        run = guard(db, args.run, args.epoch)
        if args.attempt:
            attempt, _ = dispatch.current_attempt(db, args.run, args.attempt)
            if attempt["state"] not in {"running", "submitted"} or attempt["input"]["phase"] != "implement":
                raise ValueError("只能构建有效的实现尝试")
            dispatch.ensure_current_inputs(db, args.run, attempt)
        elif args.operation:
            operation = get_operation(db, args.run, args.operation)
            if not operation or operation["kind"] != "integration":
                raise ValueError("请提供有效的暂存合入操作编号")
    if attempt:
        commit = artifacts.capture_submission(
            store, args.run, args.attempt, attempt["worker"],
            revalidate=bool((attempt["result"] or {}).get("revalidation_of")),
        )["commit"]
        if attempt["state"] == "submitted" and commit != attempt["result"]["commit"]:
            raise ValueError("已提交工作区的源码版本发生变化")
    elif operation:
        staged = operation["payload"]
        path = Path(staged["path"])
        artifacts._clean(path)
        commit = artifacts.rev(path)
        if commit != staged["candidate"]:
            raise ValueError("待合入源码与台账不符")
    else:
        commit = run["candidate"]
        if not commit or artifacts.rev(artifacts.repo_path(store, args.run), artifacts.CANDIDATE_REF) != commit:
            raise ValueError("实际候选与台账不符，请先恢复")
    return frontend.build(store, args.run, commit, epoch=args.epoch)


def release_after_terminal(store, run_id, receipt):
    try:
        store.release_reservation(run_id)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise LoopError(f"轮次 {run_id} 已记录为 {receipt['status']}，占用待释放，请重试原命令：{error}") from error
    return receipt


def invoke(args, parser):
    if args.action == "browser":
        return browser.run(
            args.entry, args.suite, args.report, feature_map=args.feature_map, feature_entries=args.feature_entries,
        )
    if args.action == "import-analysis":
        from . import analysis_mapping, upstream_analysis

        bundle = upstream_analysis.inspect(args.package)
        origin = bundle["binding"]
        projection = analysis_mapping.project(bundle) if origin["project_version"] else None
        return {"analysis_origin": origin, "projection": projection}
    if args.action == "s1-confirm":
        origin = None
        if args.analysis_package is not None:
            from . import upstream_analysis

            origin = upstream_analysis.bind(args.analysis_package)
        return s1_review.confirm(
            args.s1_record, args.project, read_json(args.contract), args.approval, analysis_origin=origin,
        )
    if args.action == "s1-handoff":
        return s1_review.record_handoff(args.s1_record, args.step, read_json(args.artifact))
    if args.action == "skill":
        if args.s1_record and (args.run is not None or args.step is None):
            raise ValueError("--s1-record 仅用于新轮启动前登记一个具体的 S1 步骤")
        if args.task is not None and args.run is None:
            raise ValueError("--task 须关联 --run 和当前 --epoch")
        if args.run is None:
            skill = read_skill(args.skill_id, args.role, args.stage, step=args.step, purpose=args.purpose)
            if args.s1_record:
                s1_review.record_step(args.s1_record, skill)
            return skill
    data = args.data or os.environ.get("RTL_DATA_DIR")
    if data is None:
        parser.error("Set --data or RTL_DATA_DIR to the absolute persistent runtime directory")
    data = Path(data).expanduser()
    if not data.is_absolute():
        raise ValueError("Runtime data path must be absolute")
    data = data.resolve()
    if overlaps(data, ROOT):
        raise ValueError("Runtime data must stay outside Loop source")
    if args.action == "init":
        project = args.project
        output = args.output or (project / "prototype").resolve()
        separated_paths(data, project, output)
        project_baseline = baseline(
            args.baseline, require_assertions=True, require_checkpoints=True, require_change_proof=True,
        )
        if "requirements" not in project_baseline:
            raise ValueError("新轮基线须登记已确认的 S1 需求契约和四步交接（requirements）")
        from .suite_catalog import acceptance_browsers, freeze

        project_baseline["s1_handoff_checks"] = True
        project_baseline["acceptance_browsers"] = acceptance_browsers(project_baseline)
        project_baseline["s1_review"] = s1_review.for_init(args.s1_record, project, project_baseline)
        project_baseline.pop("analysis_origin", None)
        project_baseline.pop("analysis_lineage", None)
        project_baseline["suite_catalog"] = freeze(project_baseline, args.suite)
        project_baseline["feature_route_checks"] = True
        project_baseline["feature_entry_checks"] = True
        project_baseline["normative_change_checks"] = True
        project_baseline.pop("normative_changes", None)
        from .requirements_history import check_ledger_destination

        run_paths = {"project_root": str(project), "output_path": str(output), "baseline": project_baseline}
        check_ledger_destination(run_paths)
        planned_tasks = {item["id"]: item for item in project_baseline["requirements"]["tasks"]}
        feature_map.check_planned_references(run_paths, planned_tasks)
        memory = project / "memory" / "project.md"
        if memory.resolve() != memory:
            raise ValueError("Project memory path cannot traverse a symbolic link")
        ids = re.findall(r"^Project-ID: (\S+)\s*$", memory.read_text(encoding="utf-8"), re.M) if memory.is_file() else []
        if len(set(ids)) > 1 or ids and args.project_id and args.project_id != ids[0]:
            raise ValueError("Project identity conflicts with its existing memory. Resolve it before starting.")
        project_id = args.project_id or (ids[0] if ids else None)
        store = Store(data)
        project_baseline["frontend"] = select_frontend(
            store, project, project_id, project_baseline, args.frontend, args.from_run,
        )
        # A retry before SQLite admission must reuse the S1 reservation identity.
        planned_id = args.run_id or project_baseline["s1_review"]["confirmation"]["review_id"]
        requirements_run.check_snapshot_destination({
            "id": planned_id, "project_root": str(project), "output_path": str(output), "baseline": project_baseline,
        }, unused_run=True)
        run = store.create_run(
            project, output, project_baseline, project_id=project_id, run_id=planned_id, source_run=args.from_run,
        )
        artifacts.initialize_repo(store, run["id"], run["epoch"])
        requirements_run.prepare(store, run["id"], run["epoch"])
        return store.run(run["id"])
    if args.action == "register-ledger" and not (data / "state.sqlite").is_file():
        raise ValueError("找不到待登记的台账，请提供原有台账目录")
    read_only = {"status", "runs", "map-check", "requirements-check", "supersede", "codex-status"}
    store = Store(data, reserve=args.action not in read_only)
    if args.action == "skill":
        if args.epoch is None:
            parser.error("A recorded Skill invocation requires --epoch")
        with store.transaction() as db:
            guard(db, args.run, args.epoch, allow_paused=args.skill_id in {"loop-recovery", "statechart"})
            task = None if args.task is None else get_task(db, args.run, safe_id(args.task, "task ID"))["spec"]
            result = read_skill(args.skill_id, args.role, args.stage, step=args.step, purpose=args.purpose, task=task)
            return record_skill_load(db, args.run, result)
    if args.action == "runs":
        return store.runs()
    if args.action == "register-ledger":
        return {"data": str(store.data_root), "active": [run["id"] for run in store.runs() if run["status"] == "active"]}
    if args.action == "rebind":
        output = args.output or (args.project / "prototype").resolve()
        separated_paths(data, args.project, output)
        return store.rebind_project(args.project_id, args.project, output, epoch=args.epoch)
    run_id = args.run
    if args.action == "status":
        return status(store, run_id, next_only=args.next)
    if args.action == "map-check":
        return feature_map.check(store, run_id)
    if args.action == "requirements-check":
        return requirements_run.check(store, run_id)
    if args.action.startswith("codex-"):
        from . import codex_host

        if args.action == "codex-status":
            return codex_host.status(store, run_id, args.attempt)
        if args.action == "codex-interrupt":
            return codex_host.interrupt(store, run_id, args.epoch, args.attempt)
        if args.action == "codex-attach":
            return codex_host.attach_thread(store, run_id, args.epoch, args.attempt, args.thread)
        return codex_host.start(
            store, run_id, args.epoch, args.attempt, socket=args.socket,
            prepare=lambda: prepare_codex_attempt(store, run_id, args.epoch, args.attempt),
        )
    epoch = args.epoch
    match args.action:
        case "prepare":
            requirements_run.require_current_analysis(store.run(run_id))
            candidate = artifacts.initialize_repo(store, run_id, epoch, allow_pending_integration=True)
            requirements_run.prepare(store, run_id, epoch)
            return candidate
        case "prepare-attempt":
            return prepare_attempt(store, run_id, epoch, args.attempt)
        case "takeover":
            return store.take_over(run_id, epoch)
        case "pause-run":
            checkpoint = read_json(args.checkpoint) if args.checkpoint is not None else None
            return lifecycle.pause_run(store, run_id, epoch, reason=args.reason, checkpoint=checkpoint)
        case "resume-run":
            return lifecycle.resume_run(store, run_id, epoch, args.pause_id)
        case "supersede":
            receipt = lifecycle.supersede(
                store, run_id, epoch, reason=args.reason, approval=args.approval, stopped=args.stopped,
            )
            return release_after_terminal(store, run_id, receipt)
        case "task-add":
            return store.add_task(run_id, epoch, read_json(args.spec))
        case "claim":
            return claim(store, args)
        case "start":
            return dispatch.start(store, run_id, epoch, args.attempt, args.worker, host_ref=args.host_ref)
        case "pause":
            return dispatch.pause(store, run_id, epoch, args.attempt, host_ref=args.host_ref)
        case "fence":
            if args.isolate_codex is not None:
                from .codex_host import isolate_workspace

                isolation = isolate_workspace(
                    store, run_id, epoch, args.attempt, args.isolate_codex, host_ref=args.host_ref,
                )
                fenced = dispatch.fence(
                    store, run_id, epoch, args.attempt, stopped=True,
                    host_ref=args.host_ref or isolation.get("host_ref"),
                )
                return {**fenced, "isolation": isolation}
            return dispatch.fence(store, run_id, epoch, args.attempt, stopped=args.stopped, host_ref=args.host_ref)
        case "submit":
            return submit(store, args)
        case "contract-propose":
            return contracts.propose(store, run_id, epoch, args.edge, args.version, args.provider, args.consumer, read_json(args.spec))
        case "contract-confirm":
            return contracts.confirm(store, run_id, epoch, args.edge, args.version, args.task)
        case "contract-activate":
            return contracts.activate(store, run_id, epoch, args.edge, args.version)
        case "stage":
            return artifacts.stage(
                store, run_id, epoch, args.attempts,
                required_cases=args.require_case, required_edges=args.require_edge,
            )
        case "build":
            return build_candidate(store, args)
        case "evidence":
            return record_evidence(store, args)
        case "promote":
            return artifacts.promote(store, run_id, epoch, args.operation)
        case "rollback-integration":
            return lifecycle.rollback_integration(store, run_id, epoch, args.operation, reason=args.reason)
        case "land":
            with store.transaction(immediate=False) as db:
                guard(db, run_id, epoch)
            feature_map.check(store, run_id)
            return delivery.land(store, run_id, epoch, allow_replace=args.allow_replace)
        case "rollback-landing":
            return delivery.rollback_landing(store, run_id, epoch, args.operation, args.reason)
        case "cancel-acceptance":
            return delivery.cancel_acceptance(store, run_id, epoch, args.operation, args.reason)
        case "finalize":
            with store.transaction(immediate=False) as db:
                run = get_run(db, run_id)
                if run["status"] == "active":
                    guard(db, run_id, epoch)
            if run["status"] == "active":
                feature_map.check(store, run_id)
            receipt = delivery.finalize(store, run_id, epoch, args.operation, args.actor, approval=args.approval)
            return release_after_terminal(store, run_id, receipt)
        case _:
            raise ValueError("Unknown action")


def main():
    parser = build_parser()
    try:
        args = parser.parse_args()
        result = invoke(args, parser)
        if args.action == "browser":
            return result
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (LoopError, ValueError, OSError, RuntimeError, sqlite3.Error, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"RTL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
