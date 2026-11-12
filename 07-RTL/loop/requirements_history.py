"""Publish the project requirements ledger and per-delivery feature links."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .ledger_schema import require_readable
from .requirements import CHANGE_LABELS, _cell, feature_anchor, render_plan
from .state import pack, safe_id, unpack


LEDGER = "docs/需求台账.md"
LEDGER_MARKER = "<!-- rtl:requirements-ledger:v1 -->"
TRACE_MARKER = "rtl:requirements-change:v1"
HISTORY_INDEX = "memory/requirements-history.json"
HISTORY_MARKER = "<!-- rtl:requirements-history:v1 -->"


def _ledger_target(run):
    from .delivery import _directory, _safe_project_child

    project = _directory(run["project_root"])
    path = _safe_project_child(project, LEDGER)
    if path.is_relative_to(Path(run["output_path"])):
        raise ValueError("需求台账须位于原型落盘目录之外")
    if path.parent.exists() and not path.parent.is_dir():
        raise ValueError(f"需求台账目录不是普通目录：{path.parent}")
    if path.exists() and not path.read_text(encoding="utf-8").startswith(LEDGER_MARKER + "\n"):
        raise ValueError(f"已有人工维护的需求台账，请迁移后再生成：{path}")
    return path


def check_ledger_destination(run):
    if "requirements" in run["baseline"]:
        _ledger_target(run)


def _trace_record(run, change_id):
    baseline = run["baseline"]
    plan = baseline["requirements"]
    feature_names = {item["id"]: item["name"] for item in plan["features"]}
    return {
        "change_id": change_id,
        "project_id": run["project_id"],
        "run_id": run["id"],
        "revision": baseline["revision"],
        "baseline_sha256": hashlib.sha256(pack(baseline).encode()).hexdigest(),
        "features": [
            {"id": change["feature_id"], "name": feature_names[change["feature_id"]], "kind": change["kind"]}
            for change in plan["changes"]
        ],
    }


def change_section(run, change_id):
    """Render approved changes and stable backlinks in the immutable CHG document."""
    plan = run["baseline"].get("requirements")
    if plan is None:
        return ""
    from .requirements_run import snapshot_relative_path

    record = _trace_record(run, change_id)
    snapshot = snapshot_relative_path(run["id"])
    canonical = run["baseline"].get("normative_changes")
    rows = [
        "",
        "## 本次功能变更",
        "",
        f"[冻结 S1 需求契约](../../{snapshot})",
        "",
        "| 功能 | 类型 | 变更前 | 变更后 |",
        "| --- | --- | --- | --- |",
    ]
    for feature, change in zip(record["features"], plan["changes"]):
        feature_id = feature["id"]
        link = f"[{feature_id}](../../{LEDGER}#{feature_anchor(feature_id)})"
        rule = canonical[feature_id] if canonical is not None else change
        rows.append(
            f"| {link} {_cell(feature['name'])} | {CHANGE_LABELS[feature['kind']]} "
            f"| {_cell(rule['before'])} | {_cell(rule['after'])} |"
        )
    if canonical is not None:
        rows.extend(["", "### S1 计划说明", ""])
        for change in plan["changes"]:
            rows.append(f"- `{change['feature_id']}` {_cell(change['before'])} → {_cell(change['after'])}")
    metadata = pack(record).replace("<", "\\u003c").replace(">", "\\u003e").replace("--", "\\u002d\\u002d")
    return "\n".join([*rows, "", f"<!-- {TRACE_MARKER} {metadata} -->", ""])


def _indexed_ids(project):
    """Locate legacy generated delivery links without relying on their display text."""
    from .delivery import _safe_project_child

    index = _safe_project_child(project, "docs/change-log.md")
    if not index.is_file():
        return []
    ids = {}
    for line in index.read_text(encoding="utf-8").splitlines():
        markers = set(re.findall(r"<!--\s*rtl:delivery-([a-f0-9]+)\s*-->", line))
        links = set(re.findall(r"\]\(\s*\.\./memory/changes/CHG-([a-f0-9]+)\.md\s*\)", line))
        if markers and links and markers != links:
            raise ValueError("变更索引的交付编号与明细链接不一致")
        for identifier in sorted(markers | links):
            ids.setdefault(f"CHG-{identifier}", None)
    return list(ids)


def _change_path(project, change_id):
    from .delivery import _safe_project_child

    return _safe_project_child(project, f"memory/changes/{change_id}.md")


def _checked_trace(item, project_id, change_id):
    if (
        not isinstance(item, dict) or item.get("project_id") != project_id
        or item.get("change_id") != change_id
        or not isinstance(change_id, str) or not re.fullmatch(r"CHG-[a-f0-9]+", change_id)
        or not isinstance(item.get("revision"), str) or not item["revision"].strip()
        or not isinstance(item.get("baseline_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", item["baseline_sha256"])
        or not isinstance(item.get("features"), list) or not item["features"]
    ):
        raise ValueError(f"需求变更记录无效：{change_id}")
    safe_id(item.get("run_id"), "run ID")
    seen = set()
    for feature in item["features"]:
        if not isinstance(feature, dict) or not isinstance(feature.get("name"), str):
            raise ValueError(f"需求变更记录的功能无效：{change_id}")
        identifier = safe_id(feature.get("id"), "feature ID")
        kind = feature.get("kind")
        if identifier in seen or not isinstance(kind, str) or kind not in CHANGE_LABELS:
            raise ValueError(f"需求变更记录的功能无效：{change_id}")
        seen.add(identifier)
    return item


def _read_indexed_trace(project, project_id, change_id):
    path = _change_path(project, change_id)
    if not path.is_file():
        raise ValueError(f"已登记的变更明细缺失，请先恢复：{path}")
    text = path.read_text(encoding="utf-8")
    markers = re.findall(rf"^\s*<!-- {re.escape(TRACE_MARKER)} (.+?) -->\s*$", text, re.MULTILINE)
    if not markers and TRACE_MARKER not in text and "## 本次功能变更" not in text:
        return None  # This legacy delivery predates requirements tracking.
    if len(markers) != 1:
        raise ValueError(f"已登记的需求变更明细无法核对：{path}")
    try:
        return _checked_trace(json.loads(markers[0]), project_id, change_id)
    except (TypeError, ValueError) as error:
        raise ValueError(f"已登记的需求变更明细无法核对：{path}") from error


def _index_target(project):
    from .delivery import _safe_project_child

    return _safe_project_child(project, HISTORY_INDEX)


def _project_memory(project):
    from .delivery import _safe_project_child

    return _safe_project_child(project, "memory/project.md")


def _known_ledger_history(project):
    from .delivery import _safe_project_child

    ledger = _safe_project_child(project, LEDGER)
    if not ledger.is_file():
        return set(), False
    text = ledger.read_text(encoding="utf-8")
    if not text.startswith(LEDGER_MARKER + "\n"):
        return set(), False
    _, _, history = text.partition("## 功能变更历史\n")
    links = re.findall(r"\[(CHG-[a-f0-9]+)\]\(\.\./memory/changes/\1\.md\)", history)
    missing = re.findall(r"`(CHG-[a-f0-9]+)` 明细文件缺失", history)
    return set(links + missing), "- 本轮状态 **已验收**" in text.splitlines()[:9]


def _origin(db):
    return next((row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"), "")


def _trace_fields(record):
    return {key: value for key, value in record.items() if key != "source_ledger"}


def _local_deliveries(db, project_id):
    rows = db.execute(
        "SELECT runs.id, runs.status AS run_status, runs.baseline, operations.id AS operation_id, "
        "operations.status, operations.receipt FROM runs JOIN operations ON operations.run_id=runs.id "
        "WHERE runs.project_id=? AND operations.kind='delivery' ORDER BY operations.rowid",
        (project_id,),
    )
    delivered, pending = {}, set()
    for row in rows:
        if row["run_status"] == "active" and row["status"] == "intent":
            pending.add(row["operation_id"].replace("delivery-", "CHG-", 1))
        if row["run_status"] != "delivered" or row["status"] != "done":
            continue
        baseline = unpack(row["baseline"])
        if "requirements" not in baseline:
            continue
        receipt = unpack(row["receipt"])
        change_id = safe_id(receipt["change_id"], "change ID")
        run = {"id": row["id"], "baseline": baseline, "project_id": project_id}
        delivered[change_id] = {**_trace_record(run, change_id), "source_ledger": _origin(db) or None}
    return delivered, pending


def _frozen_baseline(project, record):
    from .delivery import _safe_project_child
    from .requirements_run import snapshot_relative_path

    path = _safe_project_child(project, snapshot_relative_path(record["run_id"]))
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(baseline, dict) or not isinstance(baseline.get("requirements"), dict)
            or baseline.get("revision") != record["revision"]
            or hashlib.sha256(pack(baseline).encode()).hexdigest() != record["baseline_sha256"]
        ):
            raise ValueError("快照摘要与已验收记录不符")
    except (OSError, ValueError) as error:
        raise ValueError(f"无法核对另一份台账或当前台账的已交付需求契约：{path}") from error
    return baseline


def _check_external_receipt(record):
    origin = Path(record["source_ledger"])
    change_id = record["change_id"]
    try:
        if not origin.is_absolute() or origin.name != "state.sqlite" or origin.resolve() != origin or not origin.is_file():
            raise ValueError("原运行台账不可用")
        with closing(sqlite3.connect(origin.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            require_readable(db)
            row = db.execute(
                "SELECT runs.baseline, runs.status AS run_status, operations.status, operations.receipt "
                "FROM runs JOIN operations ON operations.run_id=runs.id "
                "WHERE runs.id=? AND runs.project_id=? AND operations.id=? AND operations.kind='delivery'",
                (record["run_id"], record["project_id"], "delivery-" + change_id[4:]),
            ).fetchone()
        if (
            not row or row["run_status"] != "delivered" or row["status"] != "done"
            or unpack(row["receipt"])["change_id"] != change_id
        ):
            raise ValueError("原交付尚未确认")
        run = {"id": record["run_id"], "project_id": record["project_id"], "baseline": unpack(row["baseline"])}
        if _trace_record(run, change_id) != _trace_fields(record):
            raise ValueError("原交付契约与历史索引不一致")
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as error:
        raise ValueError(f"无法核对原运行台账的交付回执 {change_id}：{origin}") from error


def _load_index(project, project_id):
    path = _index_target(project)
    if not path.is_file():
        memory = _project_memory(project)
        if memory.is_file() and HISTORY_MARKER in memory.read_text(encoding="utf-8"):
            raise ValueError(f"已登记的结构化需求变更历史缺失，请先恢复：{path}")
        return None
    try:
        index = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(index, dict) or index.get("version") != 1
            or index.get("project_id") != project_id or not isinstance(index.get("records"), list)
            or not index["records"]
            or index.get("records_sha256") != hashlib.sha256(pack(index["records"]).encode()).hexdigest()
        ):
            raise ValueError("索引格式或项目编号不符")
        records = {}
        for record in index["records"]:
            change_id = record.get("change_id") if isinstance(record, dict) else None
            _checked_trace(record, project_id, change_id)
            origin = record.get("source_ledger")
            if (
                change_id in records or "source_ledger" not in record
                or (origin is not None and (not isinstance(origin, str) or not Path(origin).is_absolute()))
            ):
                raise ValueError(f"历史索引含重复或无效的来源：{change_id}")
            records[change_id] = record
    except (TypeError, ValueError) as error:
        raise ValueError(f"无法读取结构化需求变更历史：{path}") from error
    return records


def _legacy_history(project, project_id, local, pending, *, repair_change=None):
    results = {}
    indexed = _indexed_ids(project)
    for change_id in indexed:
        if change_id in pending:
            continue
        if change_id == repair_change and change_id in local and not _change_path(project, change_id).exists():
            record = _trace_fields(local[change_id])
        else:
            record = _read_indexed_trace(project, project_id, change_id)
        if change_id in local:
            if record != _trace_fields(local[change_id]):
                raise ValueError(f"需求变更明细与原运行台账不一致：{change_id}")
            record = local[change_id]
        elif record is not None:
            record = {**record, "source_ledger": None}
        if record is not None:
            _check_history_snapshot(project, record, repair_change)
            results[change_id] = record

    # Detect a known requirements delivery whose index line was lost, while
    # ignoring files from legacy runs that never used requirements tracking.
    directory = project / "memory/changes"
    known = set(indexed) | local.keys() | pending
    for path in directory.glob("CHG-*.md"):
        if path.stem in known or not re.fullmatch(r"CHG-[a-f0-9]+", path.stem):
            continue
        if _read_indexed_trace(project, project_id, path.stem) is not None:
            raise ValueError(f"需求变更明细未在历史索引登记，请先恢复：{path}")
    if local.keys() - results.keys() and results.keys() - local.keys():
        raise ValueError("本地与跨台账需求变更历史缺少共同顺序，请先恢复变更索引")
    for change_id, record in local.items():
        if change_id not in results:
            if (
                (change_id != repair_change or _change_path(project, change_id).exists())
                and _read_indexed_trace(project, project_id, change_id) != _trace_fields(record)
            ):
                raise ValueError(f"需求变更明细与原运行台账不一致：{change_id}")
            _check_history_snapshot(project, record, repair_change)
            results[change_id] = record
    return results


def _check_history_snapshot(project, record, repair_change):
    if record["change_id"] == repair_change:
        from .delivery import _safe_project_child
        from .requirements_run import snapshot_relative_path

        path = _safe_project_child(project, snapshot_relative_path(record["run_id"]))
        if not path.exists():
            return
    _frozen_baseline(project, record)


def _history(db, run, current_change=None, *, repair_change=None):
    project = Path(run["project_root"])
    project_id = run["project_id"]
    local, pending = _local_deliveries(db, project_id)
    if repair_change is not None and repair_change not in local:
        raise ValueError("只能恢复本台账中已提交的交付记录")
    recorded = _load_index(project, project_id)
    current = {**_trace_record(run, current_change), "source_ledger": _origin(db)} if current_change else None
    if recorded is None:
        results = _legacy_history(project, project_id, local, pending, repair_change=repair_change)
    else:
        results = {}
        origin = _origin(db)
        for change_id, record in recorded.items():
            if change_id == current_change:
                if record != current:
                    raise ValueError(f"已登记的需求变更来源与本次交付不符：{change_id}")
            elif record["source_ledger"] == origin:
                if change_id in pending:
                    continue
                if local.get(change_id) != record:
                    raise ValueError(f"结构化历史与原运行台账不一致：{change_id}")
            elif record["source_ledger"] is not None:
                _check_external_receipt(record)
            elif change_id in local and _trace_fields(record) != _trace_fields(local[change_id]):
                raise ValueError(f"旧台账的变更与当前交付不一致：{change_id}")

            if (
                (change_id not in {current_change, repair_change} or _change_path(project, change_id).exists())
                and _read_indexed_trace(project, project_id, change_id) != _trace_fields(record)
            ):
                raise ValueError(f"需求变更明细与结构化历史不一致：{change_id}")
            _check_history_snapshot(project, record, repair_change)
            results[change_id] = record
        if local.keys() - results.keys():
            raise ValueError("结构化历史缺少本台账已交付的需求变更，请先恢复")

    ledger_ids, ledger_delivered = _known_ledger_history(project)
    known = set(results) | pending
    if current_change:
        known.add(current_change)
    missing = sorted(ledger_ids - known)
    if missing or (ledger_delivered and not known):
        raise ValueError(f"需求台账引用的已验收历史无法核对，请先恢复：{', '.join(missing) or '缺少交付记录'}")

    if current:
        if current_change in results and results[current_change] != current:
            raise ValueError(f"已登记的需求变更来源与本次交付不符：{current_change}")
        results[current_change] = current
    return list(reversed(results.values()))


def record_delivery_history(store, db, run, receipt):
    """Record a candidate history entry. Its originating SQLite receipt commits it."""
    if "requirements" not in run["baseline"]:
        return None
    from .delivery import _append_marker, _atomic_text

    project = Path(run["project_root"])
    path = _index_target(project)
    if path.is_relative_to(Path(run["output_path"])):
        raise ValueError("结构化需求变更历史须位于原型落盘目录之外")
    if _origin(db) != str(store.path):
        raise ValueError("需求变更历史须绑定本轮实际运行台账")
    records = _history(db, run, receipt["change_id"])
    current = next(record for record in records if record["change_id"] == receipt["change_id"])
    if (
        _change_path(project, current["change_id"]).exists()
        and _read_indexed_trace(project, run["project_id"], current["change_id"]) != _trace_fields(current)
    ):
        raise ValueError("当前需求变更明细与已冻结需求契约不一致")
    _frozen_baseline(project, current)
    ordered = list(reversed(records))
    payload = {
        "version": 1, "project_id": run["project_id"], "records": ordered,
        "records_sha256": hashlib.sha256(pack(ordered).encode()).hexdigest(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        _atomic_text(path, text)
    _append_marker(_project_memory(project), HISTORY_MARKER, HISTORY_MARKER)
    return path


def historical_baselines(db, project, project_id):
    """Resolve every verified S1 contract, newest first."""
    records = _history(db, {"project_root": str(project), "project_id": project_id})
    return [_frozen_baseline(project, record) for record in records]


def latest_baseline(db, project, project_id):
    """Resolve the latest verified S1 contract independently of the display index."""
    baselines = historical_baselines(db, project, project_id)
    return baselines[0] if baselines else None


def require_latest_delivery_for_repair(db, run, change_id):
    records = _history(db, run, repair_change=change_id)
    if not records or records[0]["change_id"] != change_id:
        raise ValueError("只可恢复项目最新的已提交需求交付，未改动现有台账")


def check_current_ledger(db, run):
    path = _ledger_target(run)
    if not path.is_file():
        raise ValueError("需求台账尚未落盘，请执行 prepare")
    if path.read_text(encoding="utf-8") != _ledger_text(db, run, path):
        raise ValueError("需求台账与本轮 S1 冻结快照或已登记任务不符，请执行 prepare")
    return path


def _history_text(project, plan, records):
    lines = ["## 功能变更历史", ""]
    if not records:
        return "\n".join([*lines, "当前没有已验收的功能变更记录。", ""])
    lines.extend(["| 功能 | 需求修订 | 类型 | 变更记录 |", "| --- | --- | --- | --- |"])
    anchored = {feature_anchor(item["id"]) for item in plan["features"]}
    for record in records:
        change_id = record["change_id"]
        change = (
            f"[{change_id}](../memory/changes/{change_id}.md)"
            if _change_path(project, change_id).is_file()
            else f"`{change_id}` 明细文件缺失"
        )
        for feature in record["features"]:
            feature_id = feature["id"]
            anchor = feature_anchor(feature_id)
            feature_cell = f"[{feature_id}](#{anchor})"
            if anchor not in anchored:
                feature_cell = f'<a id="{anchor}"></a>{feature_id}'
                anchored.add(anchor)
            lines.append(
                f"| {feature_cell} {_cell(feature['name'])} | {_cell(record['revision'])} "
                f"| {CHANGE_LABELS[feature['kind']]} | {change} |"
            )
    return "\n".join([*lines, ""])


def _ledger_text(db, run, path):
    from .requirements_run import snapshot_relative_path

    plan = run["baseline"]["requirements"]
    tasks = [
        unpack(row["spec"])
        for row in db.execute("SELECT spec FROM tasks WHERE run_id=? ORDER BY rowid", (run["id"],))
    ]
    heading, body = render_plan(plan, run["baseline"]["revision"], tasks=tasks or plan["tasks"]).rstrip().split("\n\n", 1)
    status = "已验收" if run["status"] == "delivered" else "已冻结，待交付"
    snapshot = snapshot_relative_path(run["id"])
    history = _history_text(path.parents[1], plan, _history(db, run))
    return (
        f"{LEDGER_MARKER}\n\n{heading}\n\n- 本轮状态 **{status}**\n"
        f"- [冻结 S1 需求契约](../{snapshot})\n{body}\n\n{history}"
    )


def write_ledger(store, db, run):
    """Render a project document from the approved plan and committed history."""
    if "requirements" not in run["baseline"]:
        return None
    from .delivery import _atomic_text

    path = _ledger_target(run)
    text = _ledger_text(db, run, path)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        _atomic_text(path, text)
    return path
