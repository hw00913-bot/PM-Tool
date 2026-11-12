"""Evidence receipts and scope-aware verification gates."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from sqlite3 import Row
from typing import cast
import uuid

from . import frontend
from .artifacts import _clean, make_worktree, rev, run_directory
from .assertions import browser_case_plan, matches_checkpoint
from .authorship import attempt_authors
from .evidence_compat import INPUT_REVISIONS, browser_family, inputs_match, requires_report
from .payload_types import Baseline, CaseResults, EvidenceContext, EvidenceRecord, LandingIntent, OperationRecord, RunRecord
from .state import decode, get_attempt, get_operation, get_task, guard, pack, safe_id, unpack


KINDS = {"self", "integration", "contract", "delivery"}
REPORT_DIGEST_FIELDS = ("report_sha256", "report_digest", "evidence_sha256")


@dataclass
class CaseCoverage:
    outcomes: CaseResults
    failed_cases: list[str]
    evidence_ids: list[str]
    unavailable_evidence_ids: list[str]

    def missing_cases(self, cases: Iterable[str]) -> list[str]:
        return sorted({case for case in cases if not _case_passed(self.outcomes.get(case))})

    @property
    def has_pass(self) -> bool:
        return any(_case_passed(outcome) for outcome in self.outcomes.values())


def validate_input_baseline(baseline: Baseline) -> None:
    for field in INPUT_REVISIONS:
        if field in baseline and (not isinstance(baseline[field], str) or not baseline[field].strip()):
            raise ValueError(f"基线 {field} 必须是非空字符串")
    if not requires_report(baseline):
        return
    digests = baseline["suite_digests"]
    if not isinstance(digests, dict) or not digests:
        raise ValueError("suite_digests 必须是 SHA-256 到用例编号列表的非空映射")
    covered = set()
    for digest, cases in digests.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("suite_digests 须使用 64 位小写十六进制 SHA-256")
        if not isinstance(cases, list) or not cases:
            raise ValueError("每份冻结用例文件至少需要一个用例编号")
        normalized = {safe_id(case, "case ID") for case in cases}
        if len(normalized) != len(cases):
            raise ValueError("冻结用例文件的用例编号不得重复")
        covered.update(normalized)
    missing = set(baseline.get("mandatory_cases", [])) - covered
    if missing:
        raise ValueError(f"冻结用例文件未覆盖必跑用例：{', '.join(sorted(missing))}")


def require_frozen_cases(baseline: Baseline, cases: Iterable[str], *, label: str) -> None:
    digests = baseline.get("suite_digests")
    if digests is None:
        return
    known = {case for registered in digests.values() for case in registered}
    missing = set(cases) - known
    if missing:
        raise ValueError(f"{label}引用了本轮冻结用例文件未登记的用例：{', '.join(sorted(missing))}")


def _case_passed(value):
    if type(value) is bool:
        return value
    if isinstance(value, dict):
        return value.get("passed") is True or value.get("status") == "pass"
    return False


def _case_failed(value):
    if isinstance(value, dict):
        if "status" in value:
            return value["status"] == "fail"
        return value.get("passed") is False
    return value is False


def _report_digest_matches(context: EvidenceContext, digest: str) -> bool:
    return all(context.get(field) in (None, digest) for field in REPORT_DIGEST_FIELDS)


def _archive_binding_matches(context: EvidenceContext) -> bool:
    digest = context.get("evidence_sha256")
    return not digest or _report_digest_matches(context, digest)


def evidence_intact(context: EvidenceContext) -> bool:
    expected = context.get("evidence_sha256")
    if not expected:
        return True
    source = context.get("evidence")
    if not isinstance(source, str) or not _archive_binding_matches(context):
        return False
    path = Path(source)
    try:
        if path.is_symlink() or not path.is_file():
            return False
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() == expected
    except OSError:
        return False


def _suite_case_fingerprints(report_bytes: bytes, context: EvidenceContext, cases: Iterable[str]) -> dict[str, str]:
    """Identify each frozen case independently of whether it ran in a full suite or a slice."""
    try:
        report = json.loads(report_bytes)
        suite_input = report["suite_input"].encode("utf-8")
        if hashlib.sha256(suite_input).hexdigest() != context["suite_digest"]:
            return {}
        suite = json.loads(suite_input)
        if any(suite.get(field) != context.get(field) for field in INPUT_REVISIONS):
            return {}
        definitions = {case["id"]: case["steps"] for case in suite["cases"]}
        if set(definitions) != set(cases) or len(definitions) != len(suite["cases"]):
            return {}
        fixture = {
            "engine": suite.get("engine") or "chromium",
            "viewport": suite.get("viewport") or {"width": 1440, "height": 900},
        }
        return {
            case: hashlib.sha256(pack({**fixture, "steps": steps}).encode("utf-8")).hexdigest()
            for case, steps in definitions.items()
        }
    except (AttributeError, KeyError, TypeError, ValueError):
        return {}


def _case_fingerprints(context: EvidenceContext, cases: Iterable[str], intact: bool) -> dict[str, str]:
    recorded = context.get("case_fingerprints")
    if recorded is not None:
        return recorded
    if not intact or not context.get("evidence_sha256"):
        return {}
    try:
        contents = Path(context["evidence"]).read_bytes()
        if hashlib.sha256(contents).hexdigest() == context["evidence_sha256"]:
            return _suite_case_fingerprints(contents, context, cases)
    except OSError:
        pass
    return {}


def _case_identities(case: str, context: EvidenceContext, fingerprints: Mapping[str, str]):
    scope = (case, *(context.get(field) for field in INPUT_REVISIONS))
    digest = context.get("suite_digest")
    suite = (*scope, "suite", digest)
    if case in fingerprints:
        return ((*scope, "case", fingerprints[case]), suite)
    return (suite,)


def _eligible_case_outcomes(results: CaseResults, context: EvidenceContext, case_suites) -> CaseResults:
    """Apply frozen suite constraints to anchored cases and preserve supplemental results."""
    if case_suites is None:
        return results
    digest = context.get("suite_digest")
    return {case: outcome for case, outcome in results.items()
            if case not in case_suites or digest in case_suites[case]}


def recorded_case_failures(row, baseline: Baseline, context: EvidenceContext, *, case_suites=None):
    results = unpack(row["cases"])
    if not inputs_match(baseline, context, results) or not _archive_binding_matches(context):
        return {}
    eligible = _eligible_case_outcomes(results, context, case_suites)
    failed = [case for case, outcome in eligible.items() if _case_failed(outcome)]
    if not failed:
        return {}
    fingerprints = _case_fingerprints(context, results, evidence_intact(context))
    return {
        _case_identities(case, context, fingerprints)[0]: row["sequence"]
        for case in failed
    }


def integration_retests(db, run: RunRecord, task_ids, operation_id):
    """Carry failed checks to later stages for affected logical tasks until a successful promotion."""
    run_id, baseline = run["id"], run["baseline"]
    attempt_tasks = {
        row["id"]: row["task_id"]
        for row in db.execute("SELECT id,task_id FROM attempts WHERE run_id=?", (run_id,))
    }
    pending = {}
    settled = {task_id: set() for task_id in task_ids}
    for row in db.execute(
        "SELECT id,status,payload,receipt FROM operations WHERE run_id=? AND kind='integration'",
        (run_id,),
    ):
        if row["id"] == operation_id:
            continue
        payload = unpack(row["payload"])
        related = {attempt_tasks[attempt] for attempt in payload["attempt_ids"]} & task_ids
        if not related:
            continue
        if row["status"] == "done":
            receipt = unpack(row["receipt"]) or {}
            for task_id in related:
                settled[task_id].update(receipt.get("retested_failures", []))
        else:
            pending[row["id"]] = (payload, related)

    failures, proof_ids = {}, set()
    if not pending:
        return failures, proof_ids
    for row in db.execute(
        "SELECT rowid AS sequence,* FROM evidence WHERE run_id=? AND kind='integration' ORDER BY rowid",
        (run_id,),
    ):
        context = unpack(row["context"])
        operation = pending.get(context.get("operation_id"))
        if operation is None:
            continue
        payload, related = operation
        if row["target"] != payload["candidate"] or all(row["id"] in settled[task_id] for task_id in related):
            continue
        observed = recorded_case_failures(row, baseline, context)
        if not observed or row["actor"] in attempt_authors(db, payload["attempt_ids"]):
            continue
        failures.update(observed)
        proof_ids.add(row["id"])
    return failures, proof_ids


def assess_cases(
    rows, *, context_filter: Mapping[str, object] | None = None, excluded_actors=(),
    baseline: Baseline | None = None, required_actor=None, reject_failures=False, retests=None,
    browser_families=None, case_suites=None,
) -> CaseCoverage:
    """Assess ordered evidence for the actual gate and its read-only guidance."""
    matches: CaseResults = {}
    unresolved_failures = dict(retests or {})
    evidence_ids, unavailable = [], []
    baseline = baseline or {}
    for row in rows:
        if row["actor"] in excluded_actors or (required_actor is not None and row["actor"] != required_actor):
            continue
        context = row["context"]
        if browser_families is not None and browser_family(context.get("browser")) not in browser_families:
            continue
        if any(context.get(key) != value for key, value in (context_filter or {}).items()):
            continue
        results = row["cases"]
        if not inputs_match(baseline, context, results):
            continue
        evidence_ids.append(row["id"])
        if not _archive_binding_matches(context):
            unavailable.append(row["id"])
            continue
        eligible = _eligible_case_outcomes(results, context, case_suites)
        archived = not requires_report(baseline) or bool(context.get("evidence_sha256"))
        intact = archived and evidence_intact(context)
        if not intact:
            unavailable.append(row["id"])
        executed = _archived_assertion_report(context) if case_suites is not None and intact else None
        if reject_failures:
            fingerprints = _case_fingerprints(context, results, intact)
            for case, outcome in eligible.items():
                identities = _case_identities(case, context, fingerprints)
                if _case_failed(outcome):
                    identity = identities[0]
                    unresolved_failures[identity] = max(row["sequence"], unresolved_failures.get(identity, 0))
                elif intact and _case_passed(outcome) and (executed is None or case in executed):
                    for identity in identities:
                        if row["sequence"] > unresolved_failures.get(identity, row["sequence"]):
                            del unresolved_failures[identity]
        for case, outcome in eligible.items():
            trusted = intact and (executed is None or not _case_passed(outcome) or case in executed)
            matches[case] = outcome if trusted else False
    return CaseCoverage(matches, sorted({identity[0] for identity in unresolved_failures}), evidence_ids, unavailable)


def require_cases(
    db, run_id, kind, target, cases, *, context_filter: Mapping[str, object] | None = None, excluded_actors=(),
    require_any=False, baseline: Baseline | None = None, required_actor=None, reject_failures=False, retests=None,
    browser_families=None, case_suites=None,
) -> CaseResults:
    rows = db.execute(
        "SELECT rowid AS sequence,* FROM evidence WHERE run_id=? AND kind=? AND target=? ORDER BY rowid",
        (run_id, kind, target),
    )
    coverage = assess_cases(
        (decode(row, "context", "cases") for row in rows), context_filter=context_filter,
        excluded_actors=excluded_actors, baseline=baseline, required_actor=required_actor,
        reject_failures=reject_failures, retests=retests, browser_families=browser_families,
        case_suites=case_suites,
    )
    if coverage.failed_cases:
        raise ValueError(f"{kind} 验证仍有失败用例：{', '.join(coverage.failed_cases)}")
    missing = coverage.missing_cases(cases)
    if missing:
        raise ValueError(f"缺少通过的 {kind} 证据：{', '.join(missing)}")
    if require_any and not coverage.has_pass:
        raise ValueError(f"缺少通过的 {kind} 证据")
    return coverage.outcomes


def _archived_assertion_report(context: EvidenceContext):
    """Read the same independent browser report that was registered in the ledger."""
    expected = context.get("evidence_sha256")
    path = context.get("evidence")
    if not expected or not isinstance(path, str) or not _archive_binding_matches(context):
        return {}
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return {}
    try:
        contents = path.read_bytes()
        if hashlib.sha256(contents).hexdigest() != expected:
            return {}
        report = json.loads(contents)
    except (OSError, ValueError):
        return {}
    if not isinstance(report, dict):
        return {}
    keys = (*INPUT_REVISIONS, "path", "browser", "asset_digest", "suite_digest")
    if (
        report.get("protocol") != "file:" or report.get("entry") != context.get("url")
        or any(report.get(key) != context.get(key) for key in keys)
        or not isinstance(report.get("cases"), dict)
    ):
        return {}
    suite_input = report.get("suite_input")
    if not isinstance(suite_input, str):
        return {}
    try:
        if hashlib.sha256(suite_input.encode("utf-8")).hexdigest() != context["suite_digest"]:
            return {}
        suite = json.loads(suite_input)
        case_steps = {case["id"]: case["steps"] for case in suite["cases"]}
        if set(case_steps) != set(report["cases"]) or len(case_steps) != len(suite["cases"]):
            return {}
        passed = {}
        for case_id, proof in report["cases"].items():
            if not isinstance(proof, dict) or proof.get("status") != "pass" or proof.get("errors"):
                continue
            executed, assertions = browser_case_plan(case_steps[case_id])
            if executed == proof.get("executed") and assertions == proof.get("assertions"):
                passed[case_id] = (proof, case_steps[case_id])
    except (KeyError, TypeError, ValueError):
        return {}
    return passed


def require_feature_assertions(run: RunRecord, evidence_rows: Iterable[tuple[Row, EvidenceContext]]) -> None:
    """Verify each frozen feature assertion on the latest eligible report for its case."""
    from .requirements import current_work_features

    baseline = run["baseline"]
    require_checkpoints = baseline.get("assertion_checkpoint_checks")
    if not baseline.get("change_assertion_checks") and not require_checkpoints:
        return

    obligations = {}
    for feature_id, feature in current_work_features(baseline["requirements"]).items():
        if not feature.get("assertions"):
            raise ValueError(f"本轮功能缺少已确认的验收断言：{feature_id}")
        for binding in feature["assertions"]:
            if require_checkpoints and "checkpoint" not in binding:
                raise ValueError(f"功能 {feature_id} 的用例 {binding['case']} 缺少冻结验收时点")
            obligations.setdefault(binding["case"], []).append((feature_id, binding))

    latest = {}
    for row, context in evidence_rows:
        cases = unpack(row["cases"])
        if inputs_match(baseline, context, cases):
            for case_id in obligations.keys() & cases.keys():
                latest[case_id] = (cases[case_id], context)

    archived = {}
    for case_id, assertions in obligations.items():
        stored, context = latest.get(case_id, ({}, {}))
        reference = id(context)
        if reference not in archived:
            archived[reference] = _archived_assertion_report(context)
        proof, steps = archived[reference].get(case_id, ({}, []))
        if (
            not isinstance(stored, dict) or not isinstance(proof, dict)
            or stored.get("status") != "pass" or proof.get("status") != "pass"
            or proof.get("errors") or not isinstance(proof.get("executed"), list) or not proof["executed"]
            or proof["executed"] != stored.get("executed")
            or not isinstance(proof.get("assertions"), list) or proof["assertions"] != stored.get("assertions")
        ):
            raise ValueError(f"用例 {case_id} 缺少已执行断言的独立浏览器证据")
        for feature_id, binding in assertions:
            if "checkpoint" in binding:
                if not matches_checkpoint(binding, steps):
                    raise ValueError(f"功能 {feature_id} 的冻结断言未在 {case_id} 的约定验收时点执行")
            elif binding["step"] not in proof["assertions"]:
                raise ValueError(f"功能 {feature_id} 的冻结断言未在 {case_id} 中执行：{pack(binding['step'])}")


def _stage_operation(db, run_id, target, context: EvidenceContext) -> OperationRecord:
    operation_id = context.get("operation_id")
    if operation_id:
        operation = get_operation(db, run_id, operation_id)
    else:
        operations = [
            row["id"] for row in db.execute(
                "SELECT id,payload FROM operations WHERE run_id=? AND kind='integration'", (run_id,)
            ) if unpack(row["payload"])["candidate"] == target
        ]
        if len(operations) != 1:
            raise ValueError("请提供验证的暂存合入操作编号")
        operation = get_operation(db, run_id, operations[0])
    if not operation or operation["kind"] != "integration" or operation["payload"]["candidate"] != target:
        raise ValueError("验证结果与暂存候选不符")
    context["operation_id"] = operation["id"]
    path = Path(operation["payload"]["path"])
    _clean(path)
    if rev(path) != target:
        raise ValueError("被测暂存内容已变更")
    return operation


def _validate_cases(cases, has_artifact):
    if not isinstance(cases, dict) or not cases:
        raise ValueError("请提供至少一项可识别的用例结果")
    for identifier, outcome in cases.items():
        safe_id(identifier, "case ID")
        if type(outcome) is bool:
            if not has_artifact:
                raise ValueError(f"布尔用例结果须附原始证据文件：{identifier}")
            continue
        if not isinstance(outcome, dict):
            raise ValueError(f"无效用例结果：{identifier}")
        if type(outcome.get("passed")) is not bool and outcome.get("status") not in {
            "pass", "fail", "blocked", "not_run", "not-run"
        }:
            raise ValueError(f"用例缺少有效结果状态：{identifier}")
        if "passed" in outcome and "status" in outcome and outcome["passed"] != (outcome["status"] == "pass"):
            raise ValueError(f"用例结果状态互相矛盾：{identifier}")
        if not has_artifact and (
            not isinstance(outcome.get("actual"), str) or not outcome["actual"].strip()
        ):
            raise ValueError(f"用例须提供实际观察结果或原始证据文件：{identifier}")


def record_browser_report(
    store, run_id, epoch, kind, target, actor, report_path, context: EvidenceContext,
) -> EvidenceRecord:
    """Parse, validate and archive one snapshot of the browser's report."""
    report_path = Path(report_path).resolve(strict=True)
    if not report_path.is_file():
        raise ValueError("原始证据必须是可读的普通文件")
    contents = report_path.read_bytes()
    report = json.loads(contents)
    if not isinstance(report, dict) or not isinstance(context, dict):
        raise ValueError("Report and context must be JSON objects")
    if report.get("protocol") != "file:" or not isinstance(report.get("cases"), dict) or not report["cases"]:
        raise ValueError("Expected a file URL browser report with executed cases")
    report_fields = ("path", "entry", "browser", *INPUT_REVISIONS, "asset_digest", "suite_digest")
    if any(not isinstance(report.get(key), str) or not report[key] for key in report_fields):
        raise ValueError("Browser report is missing path, environment or input revisions")
    cases = {}
    for key, value in report["cases"].items():
        if not isinstance(value, dict) or value.get("status") not in {"pass", "fail"}:
            raise ValueError(f"Invalid browser case result: {key}")
        passed = value["status"] == "pass"
        if "passed" in value and value["passed"] is not passed:
            raise ValueError(f"Conflicting browser case result: {key}")
        executed, errors = value.get("executed", []), value.get("errors", [])
        if any(not isinstance(items, list) or any(not isinstance(item, str) for item in items)
               for items in (executed, errors)):
            raise ValueError(f"Invalid browser case observations: {key}")
        if passed and not executed:
            raise ValueError(f"Passing case has no executed steps: {key}")
        cases[key] = {**value, "passed": passed, "actual": "; ".join(executed + errors)}
    metadata = {key: report[key] for key in report_fields}
    digest = hashlib.sha256(contents).hexdigest()
    metadata.update(url=report["entry"], protocol="file:", report_sha256=digest, report_path=str(report_path))
    if "feature_routes" in report:
        metadata["feature_routes"] = report["feature_routes"]
    elif "feature_routes" in context:
        raise ValueError("验证上下文与浏览器报告冲突：feature_routes")
    reserved = {**metadata, "evidence": str(report_path), "report_digest": digest, "evidence_sha256": digest}
    for key, value in reserved.items():
        if key in context and context[key] != value:
            raise ValueError(f"验证上下文与浏览器报告冲突：{key}")
    context = cast(EvidenceContext, {**context, **metadata})
    return record(store, run_id, epoch, kind, target, actor, cases, context, _report_bytes=contents)


def record(
    store, run_id, epoch, kind, target, actor, cases: CaseResults, context: EvidenceContext,
    *, _report_bytes: bytes | None = None,
) -> EvidenceRecord:
    if kind not in KINDS:
        raise ValueError("未知的验证类型")
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("请记录验证执行者")
    if not isinstance(context, dict):
        raise ValueError("验证上下文须为对象")
    context = cast(EvidenceContext, dict(context))
    context.pop("case_fingerprints", None)
    for field in INPUT_REVISIONS:
        if not isinstance(context.get(field), str) or not context[field].strip():
            raise ValueError(f"验证上下文缺少 {field}")
    report_path, evidence_path = context.get("report_path"), context.get("evidence")
    if report_path and evidence_path and Path(report_path).resolve() != Path(evidence_path).resolve():
        raise ValueError("验证上下文指定了不同的原始证据文件")
    source = report_path or evidence_path
    _validate_cases(cases, bool(source))
    evidence_id = f"proof-{uuid.uuid4().hex}"
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        if not inputs_match(run["baseline"], context, cases):
            raise ValueError("验证报告与本轮冻结的用例、数据修订或用例文件摘要不符")
        if requires_report(run["baseline"]) and not source:
            raise ValueError("冻结用例的验证须附原始浏览器报告")
        react = frontend.profile(run["baseline"]) == "react"
        if kind == "self":
            attempt_id = safe_id(context.get("attempt_id"), "attempt ID")
            attempt = get_attempt(db, attempt_id)
            task = get_task(db, run_id, attempt["task_id"])
            if (
                attempt["run_id"] != run_id or attempt["worker"] != actor
                or task["attempt_id"] != attempt_id or attempt["state"] not in {"running", "submitted"}
            ):
                raise ValueError("自测报告与当前有效尝试不符")
            worktree = make_worktree(store, run_id, attempt_id)
            if not attempt["workspace"] or Path(attempt["workspace"]).resolve() != worktree.resolve():
                raise ValueError("自测工作区与已派发记录不符")
            verified_root = worktree
            _clean(worktree)
            if rev(worktree) != target:
                raise ValueError("自测报告与实际提交不符")
        elif kind in {"integration", "contract"}:
            operation = _stage_operation(db, run_id, target, context)
            verified_root = Path(operation["payload"]["path"])
            attempt_ids = set(operation["payload"]["attempt_ids"])
            if kind == "contract":
                for side in ("provider", "consumer"):
                    reference = context.get(side)
                    if not isinstance(reference, dict) or set(reference) != {"task", "commit"}:
                        raise ValueError(f"契约证据缺少 {side} 任务与提交")
                    task_id = safe_id(reference["task"], f"{side} task")
                    counterpart = db.execute(
                        "SELECT attempt_id FROM tasks WHERE run_id=? AND id=? AND status='integrated'",
                        (run_id, task_id),
                    ).fetchone()
                    if counterpart and counterpart["attempt_id"]:
                        attempt = get_attempt(db, counterpart["attempt_id"])
                        if (attempt["result"] or {}).get("commit") == reference["commit"]:
                            attempt_ids.add(attempt["id"])
                for name in ("edge", "version"):
                    safe_id(context.get(name), name)
            if actor in attempt_authors(db, attempt_ids):
                raise ValueError("独立验证者不能是本次变更的作者")
        else:
            from .delivery import check_landed, delivery_authors

            operation = get_operation(db, run_id, safe_id(context.get("operation_id"), "delivery operation ID"))
            if not operation or operation["kind"] != "delivery" or operation["payload"]["candidate"] != target:
                raise ValueError("报告与实际落盘候选不符")
            if run["candidate"] != target:
                raise ValueError("被测候选已失效")
            check_landed(cast(LandingIntent, operation["payload"]))
            verified_root = Path(operation["payload"]["output"])
            entry = verified_root / "index.html"
            if context.get("path") != str(entry) or context.get("url") != entry.as_uri():
                raise ValueError("交付验证须绑定实际落盘的 file:// 入口")
            if not isinstance(context.get("browser"), str) or not context["browser"].strip():
                raise ValueError("交付验证须记录实际浏览器")
            if actor in delivery_authors(db, run_id):
                raise ValueError("最终独立验证者不能是候选变更作者")
        if react:
            if not all(context.get(field) for field in ("path", "url", "asset_digest")):
                raise ValueError("React 验证证据须绑定实际构建入口和文件摘要")
            if kind != "delivery":
                build = frontend.get_build(store, run_id, target)
                verified_root = Path(build["path"])
                if context["asset_digest"] != build["asset_digest"]:
                    raise ValueError("React 验证报告与源码对应的构建产物不符")
        if context.get("path") or context.get("url"):
            actual_entry = (verified_root / "index.html").resolve()
            if context.get("path") != str(actual_entry) or context.get("url") != actual_entry.as_uri():
                raise ValueError("浏览器证据未在实际被测工作区取得")
        if context.get("asset_digest") is not None:
            from .browser import asset_digest

            if asset_digest(verified_root) != context["asset_digest"]:
                raise ValueError("验证时的文件摘要与实际被测目录不符")
        if source:
            data = _report_bytes
            if data is None:
                original = Path(source)
                if not original.is_file() or original.is_symlink():
                    raise ValueError("原始证据必须是可读的普通文件")
                data = original.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if not _report_digest_matches(context, digest):
                raise ValueError("原始报告在读取后发生变化")
            if kind in {"integration", "contract", "delivery"}:
                fingerprints = _suite_case_fingerprints(data, context, cases)
                if fingerprints:
                    context["case_fingerprints"] = fingerprints
            location = run_directory(store, run_id) / "evidence" / f"{evidence_id}.report"
            location.parent.mkdir(parents=True, exist_ok=True)
            with location.open("xb") as output:
                output.write(data)
            context["evidence"] = str(location)
            context["evidence_sha256"] = digest
        db.execute(
            "INSERT INTO evidence(id,run_id,kind,target,actor,cases,context) VALUES(?,?,?,?,?,?,?)",
            (evidence_id, run_id, kind, target, actor, pack(cases), pack(context)),
        )
    return {
        "id": evidence_id, "run_id": run_id, "kind": kind, "target": target,
        "actor": actor, "cases": cases, "context": context,
    }
