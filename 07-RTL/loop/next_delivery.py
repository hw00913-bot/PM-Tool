"""Describe S4 next steps using the read-only ledger snapshot."""

from pathlib import Path

from .delivery import confirmed_human_approval
from .evidence_compat import delivery_browsers, delivery_revisions, inputs_match
from .verification import assess_cases


def _evidence_status(snapshot, landing, entry, acceptance):
    baseline = snapshot["baseline"]
    candidate = landing["payload"]["candidate"]
    excluded = snapshot.get("delivery_authors", ())
    identity = {"operation_id": landing["id"], "path": str(entry), "url": entry.as_uri()}
    rows = [
        row for row in snapshot.get("evidence", [])
        if row["kind"] == "delivery" and row["target"] == candidate and row["actor"] not in excluded
        and all(row["context"].get(key) == value for key, value in identity.items())
        and inputs_match(baseline, row["context"], row["cases"])
    ]
    previous = acceptance["payload"].get("context") if acceptance else None
    pinned = previous if isinstance(previous, dict) and previous else (rows[0]["context"] if rows else {})
    browsers = baseline.get("acceptance_browsers") or pinned.get("browser_families")
    if rows:
        try:
            revisions = delivery_revisions(baseline, pinned)
            rows = [row for row in rows if all(row["context"].get(key) == value for key, value in revisions.items())]
            if rows:
                browsers = delivery_browsers(baseline, pinned, rows[0]["context"])
        except KeyError:
            rows = []
    cases = baseline.get("mandatory_cases", [])
    missing, failures = {}, {}
    missing_browsers = []
    registered_ids, unavailable_ids = [], []
    for browser in browsers or ():
        coverage = assess_cases(rows, baseline=baseline, reject_failures=True, browser_families={browser})
        registered_ids.extend(coverage.evidence_ids)
        unavailable_ids.extend(coverage.unavailable_evidence_ids)
        missing[browser] = coverage.missing_cases(cases)
        failures[browser] = coverage.failed_cases
        if not coverage.has_pass:
            missing_browsers.append(browser)
    return {
        "browser_families": list(browsers or ()),
        "required_cases": sorted(cases),
        "missing_browsers": missing_browsers,
        "missing_cases": missing,
        "failed_cases": failures,
        "registered_evidence_ids": registered_ids,
        "unavailable_evidence_ids": unavailable_ids,
    }


def _rollback_review(landing):
    payload = landing["payload"]
    return {
        "kind": "s4_rollback_review",
        "operation_id": landing["id"],
        "candidate": payload.get("candidate"),
        "reason": "替换交付存在原目录记录。确认驳回后，可以核对是否恢复上一份已验收交付。",
        "checks": [
            "核对原意图的实际待验目录、上一份已验收回执和可用备份的摘要。",
            "确认驳回决定、回退原因、当前路径占用及待验收意图，再登记落盘回退。",
        ],
    }


def _landing_retry(pending, details, prefix, run_id, epoch):
    payload = pending["payload"]
    action = {
        "kind": "s4_land_retry",
        **details,
        "reason": "落盘意图已登记。确认原落盘尚未完成时，可按同一候选重试。",
        "checks": ["实际目录已与候选清单匹配时，从该入口补独立验收证据。"],
    }
    if payload.get("staging") and payload.get("previous"):
        action["checks"].insert(0, f"核对实际目录 {payload['output']}、暂存 {payload['staging']} 和原备份 {payload['previous']}。")
        action["argv"] = [*prefix, "land", run_id, "--epoch", epoch]
    else:
        action["checks"].insert(0, "先核对原落盘意图的暂存和备份路径。")
    return action


def delivery_actions(snapshot, prefix):
    """Return S4 advice without inspecting the worktree or changing the ledger."""
    run_id, epoch, state = snapshot["id"], str(snapshot["epoch"]), snapshot["status"]
    operations = snapshot["operations"]
    landings = [item for item in operations if item["kind"] == "delivery"]
    rollback = next((item for item in reversed(operations)
                     if item["kind"] == "landing-rollback" and item["status"] == "intent"), None)
    if rollback and state in {"active", "superseded"}:
        payload = rollback.get("payload") or {}
        operation_id, reason = payload.get("delivery_operation"), payload.get("reason")
        paths = [payload.get(field) for field in ("output", "backup", "archive")]
        action = {
            "kind": "s4_rollback_resume",
            "rollback_id": rollback["id"],
            "reason": f"回退意图 {rollback['id']} 尚待完成，沿用原交付编号和已登记原因。",
            "checks": [],
        }
        if operation_id:
            action["operation_id"] = operation_id
        if all(paths):
            output, backup, archive = paths
            action["checks"].append(f"核对实际目录 {output}、上一版备份 {backup} 和驳回归档 {archive} 的摘要及路径占用。")
        else:
            action["checks"].append("先在原回退意图核对实际目录、上一版备份及驳回归档路径。")
        if operation_id and reason and all(paths):
            action["argv"] = [*prefix, "rollback-landing", run_id, operation_id, "--reason", reason, "--epoch", epoch]
        elif not operation_id or not reason:
            action["checks"].append("先核对原交付编号及已登记回退原因。")
        return [action]

    if state == "delivered":
        delivery = next((item for item in reversed(landings) if item["status"] == "done"), None)
        if not delivery:
            return []
        receipt = delivery.get("receipt") or {}
        action = {
            "kind": "s4_delivery_replay",
            "operation_id": delivery["id"],
            "reason": "台账已登记交付。原回执对应的需求台账、变更记录或冻结快照需要补写时，核对后可重放。",
            "checks": ["确认该轮仍是项目的最新交付，实际入口、目录和摘要符合原回执。"],
        }
        if receipt.get("actor"):
            action["argv"] = [*prefix, "finalize", run_id, delivery["id"], "--actor", receipt["actor"], "--epoch", epoch]
        else:
            action["checks"].append("先核对原回执的验收裁决者。")
        return [action]

    if state == "superseded":
        supersede = next((item["receipt"] for item in reversed(operations)
                          if item["kind"] == "supersede" and item["status"] == "done"), None) or {}
        retained = supersede.get("retained_output_manifest")
        if retained is None:
            return []
        rolled_back = {item["payload"].get("delivery_operation") for item in operations
                       if item["kind"] == "landing-rollback" and item["status"] == "done"}
        for landing in reversed(landings):
            payload = landing["payload"]
            if (landing["status"] == "superseded" and landing["id"] not in rolled_back
                    and payload.get("manifest") == retained and payload.get("previous_manifest") is not None):
                return [_rollback_review(landing)]
        return []
    if state != "active":
        return []

    candidate, output = snapshot.get("candidate"), snapshot.get("output_path")
    pending = next((item for item in reversed(landings) if item["status"] == "intent"), None)
    usable_output = isinstance(output, str) and Path(output).is_absolute()
    if pending and (not candidate or not usable_output or pending["payload"].get("candidate") != candidate
                    or pending["payload"].get("output") != output):
        return [{
            "kind": "s4_review_landing",
            "operation_id": pending["id"],
            "reason": "台账中的当前候选或落盘路径无法与原交付意图对应。",
            "checks": ["核对当前 Git 候选、原待验目录和原备份，确认原交付处置后再登记下一步。"],
        }]
    if pending and any(task["status"] != "integrated" for task in snapshot["tasks"]):
        # A replacement already moved the old output. Recover that move before
        # proceeding with the new integration, as required by promotion.
        if pending["payload"].get("previous_manifest") is not None and not Path(output).exists():
            return [_landing_retry(pending, {
                "operation_id": pending["id"], "candidate": candidate, "entry": str(Path(output) / "index.html"),
            }, prefix, run_id, epoch)]
        return [{
            "kind": "s4_review_landing",
            "operation_id": pending["id"],
            "candidate": candidate,
            "reason": "已有任务需要重新集成，请核对原落盘意图。",
            "checks": ["按当前任务及契约完成回派和集成，再核对上一份落盘的处置。"],
        }]
    if not pending:
        if not candidate or any(item["status"] != "integrated" for item in snapshot["tasks"]):
            return []
        plan = snapshot["baseline"].get("requirements")
        if plan is not None:
            prepared = any(item["kind"] == "requirements-s1" and item["status"] == "done" for item in operations)
            registered = {item["id"]: item["spec"] for item in snapshot["tasks"]}
            if not prepared or any(registered.get(task["id"]) != task for task in plan.get("tasks", [])):
                return []
        current_landing = next((item for item in reversed(landings) if item["payload"].get("candidate") == candidate
                                and item["payload"].get("output") == output), None)
        if current_landing and any(item["kind"] == "landing-rollback"
                                   and item["payload"].get("delivery_operation") == current_landing["id"]
                                   and item["status"] == "done" for item in operations):
            return [{
                "kind": "s4_new_candidate",
                "operation_id": current_landing["id"],
                "candidate": candidate,
                "reason": "当前候选已完成驳回回退，需先形成新的候选。",
                "checks": ["按已确认的驳回依据回派并重新集成，核对新候选和验证范围。"],
            }]
        action = {
            "kind": "s4_land",
            "candidate": candidate,
            "reason": "台账中的任务均已集成，可核对候选和落盘路径。",
            "checks": [
                "核对实际 Git 候选和构建清单（React 适用）。",
                "有既有内容时先核对原回执和替换授权。",
            ],
        }
        if plan is not None:
            action["checks"].append("用 requirements-check 核对 S1 冻结文件和四步交接回执。")
        if usable_output:
            action["checks"].insert(1, f"核对已确认的输出目录 {output}。")
            action["argv"] = [*prefix, "land", run_id, "--epoch", epoch]
        else:
            action["checks"].insert(1, "先核对台账中的输出绝对路径。")
        return [action]

    operation_id = pending["id"]
    payload = pending["payload"]
    entry = Path(payload["output"]) / "index.html"
    acceptance_id = operation_id.replace("delivery-", "accept-", 1)
    acceptance = next((item for item in reversed(operations)
                       if item["kind"] == "acceptance" and item["status"] == "intent"
                       and (item["id"] == acceptance_id or item["id"].startswith(acceptance_id + "-")
                            or (item["payload"].get("receipt") or {}).get("operation_id") == operation_id)), None)
    evidence = _evidence_status(snapshot, pending, entry, acceptance)
    details = {"operation_id": operation_id, "candidate": candidate, "entry": str(entry)}
    author_error = snapshot.get("delivery_author_error")
    needs_evidence = (author_error or not evidence["registered_evidence_ids"] or evidence["missing_browsers"]
                      or any(evidence["missing_cases"].values())
                      or any(evidence["failed_cases"].values()))
    actions = []
    if needs_evidence or not acceptance:
        checks = [
            f"核对实际落盘入口 {entry}、候选清单与本轮功能地图。",
            "使用对应浏览器的冻结套件复跑缺失或失败用例，并核对验收断言和实际路由。",
            "确认验证者独立于候选作者，并核对原始报告及成品摘要。",
        ]
        if not evidence["browser_families"]:
            checks.append("台账尚无可定位的验收浏览器，请先核对原验收要求。")
        if author_error:
            checks.append(f"先核对候选作者及继承来源：{author_error}")
        if evidence["unavailable_evidence_ids"]:
            ids = "、".join(evidence["unavailable_evidence_ids"])
            checks.append(f"先恢复或重跑无法核验原始归档的证据：{ids}。")
        if not acceptance:
            checks.append("验收裁决者须由实际负责人登记；需要人工验收时核对确认依据。")
        actions.append({
            "kind": "s4_evidence",
            **details, **evidence,
            "reason": "核对待验交付的落盘状态和已登记的浏览器用例证据。",
            "checks": checks,
        })
    if acceptance:
        receipt = acceptance["payload"].get("receipt") or {}
        action = {
            "kind": "s4_acceptance_resume",
            **details,
            "acceptance_id": acceptance["id"],
            "reason": "已登记验收意图。核对原候选和证据后重试同一交付操作，可恢复未完成的回写。",
            "checks": [
                "核对实际目录、原验收依据、归档报告及变更记忆是否匹配原意图。",
                "证据缺失时对同一候选和冻结用例补证；处置有变化时先核对原意图的回写状态。",
            ],
        }
        if snapshot["baseline"].get("human_acceptance") and not confirmed_human_approval(receipt.get("approval")):
            action["reason"] = "原验收意图缺少已确认的人工验收依据。"
            action["checks"].insert(0, "先核对原意图的回写状态。尚未回写时用 cancel-acceptance 撤销，再登记已确认的依据。")
        elif needs_evidence:
            action["checks"].append("先为原候选补齐台账中缺失或失败的验收证据。")
        elif (receipt.get("actor") and receipt.get("operation_id") == operation_id
              and receipt.get("candidate") == candidate and receipt.get("entry") == str(entry)
              and isinstance(receipt.get("evidence_ids"), list) and isinstance(acceptance["payload"].get("context"), dict)):
            action["argv"] = [*prefix, "finalize", run_id, operation_id, "--actor", receipt["actor"], "--epoch", epoch]
        else:
            action["checks"].append("先核对原验收意图的交付编号、裁决者和证据绑定。")
        actions.append(action)
    elif not evidence["registered_evidence_ids"]:
        actions.append(_landing_retry(pending, details, prefix, run_id, epoch))
    if not acceptance and payload.get("previous_manifest") is not None:
        actions.append(_rollback_review(pending))
    return actions
