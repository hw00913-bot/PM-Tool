"""Versioned shared edges and exact provider/consumer verification."""

import re

from .assertions import ACTION_FIELDS, ASSERTION_FIELDS
from .authorship import attempt_authors
from .contract_history import activation_id as _activation_id, first_activation_basis
from .dispatch import fence_in
from .state import begin_operation, finish_operation, get_operation, get_run, guard, pack, unpack
from .verification import recorded_case_failures, require_cases, require_frozen_cases

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def _identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def _version(db, run_id, edge, version):
    row = db.execute(
        "SELECT * FROM contracts WHERE run_id=? AND edge=? AND version=?",
        (run_id, edge, version),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown contract {edge}/{version}")
    return row


def _current_version(db, run_id, edge):
    row = db.execute(
        "SELECT version FROM contracts WHERE run_id=? AND edge=? AND active=1", (run_id, edge),
    ).fetchone()
    return row["version"] if row else None


def _record_activation(db, run_id, edge, version):
    operation_id = _activation_id(edge, version)
    if not get_operation(db, run_id, operation_id):
        identity = {"edge": edge, "version": version}
        begin_operation(db, run_id, operation_id, "contract-activation", identity)
        finish_operation(db, run_id, operation_id, identity)


def is_first_activation(db, run_id, edge, version):
    row = _version(db, run_id, edge, version)
    if not row["active"]:
        return False
    body = unpack(row["body"])
    if body.get("basis_version") is not None:
        return False
    prefix = _activation_id(edge, "")
    has_other_activation = db.execute(
        "SELECT 1 FROM operations WHERE run_id=? AND kind='contract-activation' AND status='done' "
        "AND substr(id, 1, ?) = ? AND id<>?",
        (run_id, len(prefix), prefix, _activation_id(edge, version)),
    ).fetchone() is not None
    return first_activation_basis(
        body,
        (unpack(other["body"]) for other in db.execute(
            "SELECT body FROM contracts WHERE run_id=? AND edge=? AND version<>?", (run_id, edge, version),
        )),
        has_other_activation=has_other_activation,
    )


def _check_proposal_basis(db, run_id, edge, version, row, body, current):
    if row["active"]:
        return
    if get_operation(db, run_id, _activation_id(edge, version)):
        raise ValueError(f"契约 {edge}/{version} 已退役；需要恢复该行为时请登记新版本并重新确认")
    # Older ledgers did not record which active contract each proposal reviewed.
    if body.get("basis_version") != current:
        raise ValueError(f"契约 {edge}/{version} 的提案依据已失效；请基于当前版本登记新提案并重新确认")


def _participant_specs(db, run_id, body):
    return {
        side: unpack(row["spec"]) if (row := db.execute(
            "SELECT spec FROM tasks WHERE run_id=? AND id=?", (run_id, body[side]),
        ).fetchone()) else {}
        for side in ("provider", "consumer")
    }


def _matches_case_anchors(steps, anchors):
    if not steps:
        return False
    ordered = sorted(anchors, key=lambda anchor: anchor["step"])
    for index, anchor in enumerate(ordered):
        next_marker = ordered[index + 1]["step"] if index + 1 < len(ordered) else len(steps)
        marker, interaction = anchor["step"], anchor["interaction"]
        if not 0 <= marker < interaction < min(next_marker, len(steps)):
            return False
        marker_step, interaction_step = steps[marker], steps[interaction]
        if marker_step != {"op": "visible", "selector": anchor["selector"]}:
            return False
        selector = interaction_step.get("selector")
        if (interaction_step.get("op") not in ACTION_FIELDS.keys() | ASSERTION_FIELDS.keys()
                or not selector or selector == anchor["selector"]):
            return False
    return True


def _contract_case_suites(run, edge, body, participants):
    """Require executable case anchors for both registered contract participants."""
    from .feature_map import _references, _source, _target, rows

    baseline = run["baseline"]
    catalog = baseline.get("suite_catalog")
    if catalog is None:
        return None
    cases = body["spec"]["cases"]
    bindings = body["spec"].get("case_anchors")
    if not isinstance(bindings, dict) or set(bindings) != set(cases):
        raise ValueError(f"契约 {edge} 须为每个用例登记提供方和使用方的 case_anchors")
    features = {
        feature["id"]: feature for feature in baseline.get("requirements", {}).get("features", [])
        if feature["status"] == "active"
    }
    map_rows = rows(_source(run).read_text(encoding="utf-8"))
    mapped = {feature: (scene, entry, edges) for feature, scene, entry, edges, _ in map_rows}
    if len(mapped) != len(map_rows):
        raise ValueError(f"契约 {edge} 的功能地图包含重复功能编号")
    allowed = {}
    for case in cases:
        label = f"契约 {edge} 用例 {case}"
        pair = bindings[case]
        if not isinstance(pair, dict) or set(pair) != {"provider", "consumer"}:
            raise ValueError(f"{label} 须登记双方实际功能和步骤")
        anchors = []
        for side in ("provider", "consumer"):
            item = pair[side]
            if (
                not isinstance(item, dict) or set(item) != {"feature_id", "step", "interaction"}
                or not isinstance(item["feature_id"], str)
                or type(item["step"]) is not int or type(item["interaction"]) is not int
            ):
                raise ValueError(f"{label} 的 {side} 须登记 feature_id、step 和 interaction")
            feature_id = item["feature_id"]
            feature = features.get(feature_id)
            scene, entry, edges = mapped.get(feature_id, ("", "", ""))
            if (
                not feature or feature_id not in participants[side].get("feature_ids", [])
                or feature["scene_id"] != scene or edge not in _references(edges)
            ):
                raise ValueError(f"{label} 的 {side} 功能不属于契约参与任务或功能地图：{feature_id}")
            _target(entry)
            selector = f'[data-rtl-feature~="{feature_id}"][data-rtl-scene="{scene}"]'
            anchors.append({**item, "selector": selector})
        if anchors[0]["feature_id"] == anchors[1]["feature_id"]:
            raise ValueError(f"{label} 须分别观察提供方和使用方的功能")
        digests = {
            digest for digest, suite in catalog.items()
            if _matches_case_anchors(suite["cases"].get(case), anchors)
        }
        if not digests:
            raise ValueError(f"{label} 的双方功能标记和交互不在同一份冻结用例步骤中")
        allowed[case] = digests
    return allowed


def propose(store, run_id, epoch, edge, version, provider, consumer, spec):
    for value, label in ((edge, "edge"), (version, "version"), (provider, "provider"), (consumer, "consumer")):
        _identifier(value, label)
    if provider == consumer:
        raise ValueError("A shared edge requires two distinct tasks")
    if not isinstance(spec, dict) or not isinstance(spec.get("behavior"), str) or not spec["behavior"].strip():
        raise ValueError("Contract requires a nonempty behavior description")
    cases = spec.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Contract requires at least one observable case")
    for case in cases:
        _identifier(case, "contract case")
    compatible = spec.get("compatible_versions", [])
    if not isinstance(compatible, list):
        raise ValueError("compatible_versions must be a list")
    for pair in compatible:
        if not isinstance(pair, dict) or set(pair) != {"provider", "consumer"}:
            raise ValueError("Each compatible pair names provider and consumer contract versions")
        for value in pair.values():
            _identifier(value, "compatible version")
    body = {"provider": provider, "consumer": consumer, "spec": spec, "approvals": []}
    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        body["basis_version"] = _current_version(db, run_id, edge)
        require_frozen_cases(run["baseline"], cases, label=f"契约 {edge}/{version}")
        participants = _participant_specs(db, run_id, body)
        for side, spec in participants.items():
            if edge not in spec.get("edges", []):
                raise ValueError(f"Task {body[side]} must declare edge {edge}")
        _contract_case_suites(run, edge, body, participants)
        previous = db.execute(
            "SELECT body FROM contracts WHERE run_id=? AND edge=? LIMIT 1", (run_id, edge)
        ).fetchone()
        if previous:
            prior = unpack(previous["body"])
            if (prior["provider"], prior["consumer"]) != (provider, consumer):
                raise ValueError("Use a new edge ID when the participants change")
        db.execute(
            "INSERT INTO contracts(run_id,edge,version,body,active) VALUES(?,?,?,?,0)",
            (run_id, edge, version, pack(body)),
        )
    return {"edge": edge, "version": version, **body}


def confirm(store, run_id, epoch, edge, version, task):
    with store.transaction() as db:
        guard(db, run_id, epoch)
        row = _version(db, run_id, edge, version)
        body = unpack(row["body"])
        _check_proposal_basis(db, run_id, edge, version, row, body, _current_version(db, run_id, edge))
        if task not in {body["provider"], body["consumer"]}:
            raise ValueError("Only a registered participant may confirm this contract")
        body["approvals"] = sorted(set(body["approvals"]) | {task})
        db.execute(
            "UPDATE contracts SET body=? WHERE run_id=? AND edge=? AND version=?",
            (pack(body), run_id, edge, version),
        )
    return {"edge": edge, "version": version, "approvals": body["approvals"]}


def _affected_tasks(rows, active_edges, starts):
    specs = {row["id"]: unpack(row["spec"]) for row in rows}
    affected = set(starts)
    while True:
        changed_edges = {edge for edge, body in active_edges.items() if body["provider"] in affected}
        more = {
            task for task, spec in specs.items()
            if affected.intersection(spec.get("deps", [])) or changed_edges.intersection(spec.get("edges", []))
        }
        if more <= affected:
            return affected
        affected.update(more)


def _active_bodies(db, run_id):
    return {
        row["edge"]: unpack(row["body"])
        for row in db.execute("SELECT edge,body FROM contracts WHERE run_id=? AND active=1", (run_id,))
    }


def validate_integratable(db, run_id, *, replacement=None, task_spec=None):
    """Check dependencies between tasks that must integrate together.

    An activation replacement contains the edge, its new body, and the tasks
    that will be returned to the pending integration graph.
    """
    active = _active_bodies(db, run_id)
    invalidated = set()
    if replacement is not None:
        edge, body, invalidated = replacement
        active[edge] = body
    pending = {
        row["id"]: unpack(row["spec"])
        for row in db.execute("SELECT id,status,spec FROM tasks WHERE run_id=?", (run_id,))
        if row["status"] != "integrated" or row["id"] in invalidated
    }
    if task_spec is not None:
        pending[task_spec["id"]] = task_spec

    hard = {task: set(spec.get("deps", [])) & pending.keys() for task, spec in pending.items()}
    required = {task: set(deps) for task, deps in hard.items()}
    for task, spec in pending.items():
        for edge in spec.get("edges", []):
            if edge not in active:
                continue
            body = active[edge]
            parties = {body["provider"], body["consumer"]} - {task}
            required[task].update(parties & pending.keys())

    # Contract parties may integrate together. A hard prerequisite must
    # already be integrated before its dependent can even start.
    for task, deps in hard.items():
        for dependency in sorted(deps):
            if _reaches(required, dependency, task):
                raise ValueError(
                    "Hard dependencies conflict with contract co-integration: "
                    f"{task} must wait for {dependency}, which requires {task} to integrate"
                )


def _reaches(graph, source, target):
    stack = [source]
    visited = set()
    while stack:
        task = stack.pop()
        if task == target:
            return True
        if task not in visited:
            visited.add(task)
            stack.extend(graph[task] - visited)
    return False


def _integrated_attempt(db, run_id, task):
    return db.execute(
        "SELECT a.* FROM attempts a JOIN tasks t ON t.attempt_id=a.id "
        "WHERE t.run_id=? AND t.id=? AND t.status='integrated'",
        (run_id, task),
    ).fetchone()


def _allows_pair(body, version, refs):
    return refs == {"provider": version, "consumer": version} or refs in body["spec"].get("compatible_versions", [])


def activate(store, run_id, epoch, edge, version):
    from .lifecycle import require_reconciled

    with store.transaction() as db:
        run = guard(db, run_id, epoch)
        row = _version(db, run_id, edge, version)
        body = unpack(row["body"])
        current = _current_version(db, run_id, edge)
        _check_proposal_basis(db, run_id, edge, version, row, body, current)
        if set(body["approvals"]) != {body["provider"], body["consumer"]}:
            raise ValueError("Both participating tasks must confirm this contract")
        require_frozen_cases(run["baseline"], body["spec"]["cases"], label=f"契约 {edge}/{version}")
        _contract_case_suites(run, edge, body, _participant_specs(db, run_id, body))
        if row["active"]:
            return {"edge": edge, "version": version, "affected": []}
        require_reconciled(store, db, run)
        rows = db.execute("SELECT * FROM tasks WHERE run_id=?", (run_id,)).fetchall()
        other_edges = _active_bodies(db, run_id)
        other_edges.pop(edge, None)
        starts = {task["id"] for task in rows if edge in unpack(task["spec"]).get("edges", [])}
        consumer = _integrated_attempt(db, run_id, body["consumer"])
        if consumer is not None and consumer["result"]:
            refs = {"provider": version, "consumer": unpack(consumer["input"])["contracts"].get(edge)}
            if _allows_pair(body, version, refs):
                starts.remove(body["consumer"])
        affected = _affected_tasks(rows, other_edges, starts)
        validate_integratable(db, run_id, replacement=(edge, body, affected))
        invalidated = []
        for task in rows:
            if task["id"] not in affected or not task["attempt_id"]:
                continue
            attempt = db.execute("SELECT * FROM attempts WHERE id=?", (task["attempt_id"],)).fetchone()
            if not attempt:
                raise ValueError("Task refers to a missing attempt")
            inputs = unpack(attempt["input"])
            if inputs.get("phase") == "design":
                if edge not in inputs["spec"].get("edges", []):
                    continue
                design_version = inputs.get("contracts", {}).get(edge)
                if design_version == version or (design_version is None and current is None):
                    continue
            if attempt["state"] in {"reserved", "running"}:
                raise ValueError(f"Pause or stop attempt {attempt['id']} for {task['id']} before activating {edge}")
            fence_in(db, run_id, attempt["id"])
            invalidated.append(task["id"])
        if current is not None:
            _record_activation(db, run_id, edge, current)
        db.execute("UPDATE contracts SET active=0 WHERE run_id=? AND edge=?", (run_id, edge))
        db.execute("UPDATE contracts SET active=1 WHERE run_id=? AND edge=? AND version=?", (run_id, edge, version))
        _record_activation(db, run_id, edge, version)
    return {"edge": edge, "version": version, "affected": sorted(affected), "invalidated": sorted(invalidated)}


def read_contracts(db, run_id):
    return [
        {"edge": row["edge"], "version": row["version"], "active": bool(row["active"]), **unpack(row["body"])}
        for row in db.execute("SELECT * FROM contracts WHERE run_id=? ORDER BY edge,version", (run_id,))
    ]


def list_contracts(store, run_id):
    with store.transaction(immediate=False) as db:
        return read_contracts(db, run_id)


def _contract_retests(db, run_id, baseline, expected, participant_attempts, case_suites):
    operation_id = expected.get("operation_id")
    if not operation_id:
        return {}, set()
    stage_operations, settled = {}, set()
    for row in db.execute(
        "SELECT id,status,payload,receipt FROM operations WHERE run_id=? AND kind='integration'",
        (run_id,),
    ):
        if row["status"] == "done":
            settled.update((unpack(row["receipt"]) or {}).get("retested_failures", []))
        elif row["id"] != operation_id:
            stage_operations[row["id"]] = unpack(row["payload"])
    if not stage_operations:
        return {}, set()

    pair = {field: expected[field] for field in ("edge", "version", "provider", "consumer")}
    failures, proof_ids = {}, set()
    for row in db.execute(
        "SELECT rowid AS sequence,* FROM evidence WHERE run_id=? AND kind='contract' ORDER BY rowid", (run_id,),
    ):
        context = unpack(row["context"])
        stage = stage_operations.get(context.get("operation_id"))
        if stage is None or row["target"] != stage["candidate"] or row["id"] in settled:
            continue
        if any(context.get(field) != value for field, value in pair.items()):
            continue
        observed = recorded_case_failures(row, baseline, context, case_suites=case_suites)
        if not observed or row["actor"] in attempt_authors(db, participant_attempts | set(stage["attempt_ids"])):
            continue
        failures.update(observed)
        proof_ids.add(row["id"])
    return failures, proof_ids


def check_pairs(db, run_id, staged):
    selected = {}
    for attempt_id in staged["attempt_ids"]:
        attempt = db.execute("SELECT * FROM attempts WHERE run_id=? AND id=?", (run_id, attempt_id)).fetchone()
        if not attempt:
            raise ValueError("Staged result refers to an unknown attempt")
        selected[attempt["task_id"]] = attempt
    run = get_run(db, run_id)
    baseline = run["baseline"]
    active = {
        row["edge"]: row
        for row in db.execute("SELECT * FROM contracts WHERE run_id=? AND active=1", (run_id,))
    }
    required = set(staged.get("required_edges", []))
    if "required_edges" not in staged:
        for attempt in selected.values():
            required.update(unpack(attempt["input"])["spec"].get("edges", []))
    unknown = required - active.keys()
    if unknown:
        raise ValueError(f"Missing active contracts for required edges: {', '.join(sorted(unknown))}")
    staged_attempts = set(staged["attempt_ids"])
    settled = set()
    for edge, row in active.items():
        body = unpack(row["body"])
        if edge not in required and not (set(selected) & {body["provider"], body["consumer"]}):
            continue
        expected = {"edge": edge, "version": row["version"]}
        for field in ("asset_digest", "operation_id"):
            if field in staged:
                expected[field] = staged[field]
        refs = {}
        participant_attempts = set()
        for side in ("provider", "consumer"):
            task_id = body[side]
            attempt = selected.get(task_id)
            if attempt is None:
                attempt = _integrated_attempt(db, run_id, task_id)
            if not attempt or not attempt["result"]:
                raise ValueError(f"Contract {edge} requires an actual submission from {task_id}")
            result = unpack(attempt["result"])
            commit = result.get("commit")
            if not commit:
                raise ValueError(f"Missing commit for {task_id}")
            expected[side] = {"task": task_id, "commit": commit}
            refs[side] = unpack(attempt["input"]).get("contracts", {}).get(edge)
            participant_attempts.add(attempt["id"])
        if not _allows_pair(body, row["version"], refs):
            raise ValueError(f"Unapproved version combination for contract {edge}")
        try:
            case_suites = _contract_case_suites(run, edge, body, _participant_specs(db, run_id, body))
            retests, proof_ids = _contract_retests(db, run_id, baseline, expected, participant_attempts, case_suites)
            require_cases(
                db, run_id, "contract", staged["candidate"], body["spec"]["cases"],
                context_filter=expected, baseline=baseline, reject_failures=True, retests=retests,
                excluded_actors=attempt_authors(db, participant_attempts | staged_attempts),
                case_suites=case_suites,
            )
            settled.update(proof_ids)
        except ValueError as exc:
            raise ValueError(f"Missing exact independent contract verification for {edge}/{row['version']}: {exc}") from exc
    return settled
