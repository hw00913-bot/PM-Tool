"""Validate the actual frozen suite files before admitting a new run."""

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path

from .assertions import ACTION_FIELDS, ASSERTION_FIELDS, matches_checkpoint, normalize_action, normalize_assertion
from .payload_types import Baseline, FrozenSuite
from .requirements import current_work_features
from .state import safe_id


ENGINES = frozenset({"chromium", "firefox", "webkit"})


def acceptance_browsers(baseline: Baseline) -> list[str]:
    values = baseline.get("acceptance_browsers", ["chromium"])
    if (
        not isinstance(values, list) or not values
        or any(not isinstance(value, str) or value not in ENGINES for value in values)
        or len(values) != len(set(values))
    ):
        raise ValueError("acceptance_browsers 须登记互不重复的 chromium、firefox 或 webkit")
    return sorted(values)


def _case_steps(item, path):
    steps = item.get("steps")
    case_id = item["id"]
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"冻结用例须有执行步骤：{path}/{case_id}")
    normalized = []
    for index, step in enumerate(steps):
        op = step.get("op") if isinstance(step, dict) else None
        try:
            if isinstance(op, str) and op in ACTION_FIELDS:
                normalized.append(normalize_action(step))
            else:
                normalized.append(normalize_assertion(step))
        except ValueError as error:
            raise ValueError(f"冻结用例步骤不符：{path}/{case_id}/{index}，{error}") from error
    if not any(step["op"] in ASSERTION_FIELDS for step in normalized):
        raise ValueError(f"冻结用例须有可观察断言：{path}/{case_id}")
    return normalized


def _read_suite(path: Path, raw: bytes, baseline: Baseline, declared_cases: list[str]) -> FrozenSuite:
    try:
        suite = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError(f"冻结用例文件须为 UTF-8 JSON：{path}") from error
    if not isinstance(suite, dict):
        raise ValueError(f"冻结用例文件须为对象：{path}")
    if any(suite.get(field) != baseline[field] for field in ("suite_revision", "fixture_revision")):
        raise ValueError(f"冻结用例文件的用例或数据修订与基线不符：{path}")
    engine = suite.get("engine", "chromium")
    if not isinstance(engine, str) or engine not in ENGINES:
        raise ValueError(f"冻结用例文件的浏览器引擎无效：{path}")
    if "viewport" in suite:
        viewport = suite["viewport"]
        if not isinstance(viewport, dict) or any(
            type(viewport.get(field)) is not int or viewport[field] < 1 for field in ("width", "height")
        ):
            raise ValueError(f"冻结用例文件的视口须有正整数宽高：{path}")
    items = suite.get("cases")
    if not isinstance(items, list) or not items:
        raise ValueError(f"冻结用例文件须含至少一个用例：{path}")
    cases = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"冻结用例须为对象：{path}")
        identifier = safe_id(item.get("id"), "case ID")
        if identifier in cases:
            raise ValueError(f"冻结用例编号重复：{path}/{identifier}")
        cases[identifier] = _case_steps(item, path)
    if cases.keys() != set(declared_cases):
        raise ValueError(f"冻结用例文件的实际编号与 suite_digests 不符：{path}")
    return {"engine": engine, "cases": cases}


def integration_case_suites(baseline: Baseline, risky_tasks) -> dict[str, set[str]] | None:
    """Find frozen suites that cover the checkpoint obligations of the staged risk tasks."""
    catalog = baseline.get("suite_catalog")
    plan = baseline.get("requirements")
    if catalog is None or plan is None:
        return None

    features = current_work_features(plan)
    obligations = {}
    for task in risky_tasks:
        cases = set(task["cases"])
        for feature_id in task["feature_ids"]:
            for binding in features[feature_id].get("assertions", ()):
                if binding["case"] in cases and "checkpoint" in binding:
                    obligations.setdefault(binding["case"], []).append(binding)
    if not obligations:
        return None
    return {
        case: {
            digest for digest, suite in catalog.items()
            if case in suite["cases"] and all(matches_checkpoint(binding, suite["cases"][case]) for binding in bindings)
        }
        for case, bindings in obligations.items()
    }


def _require_coverage(baseline: Baseline, catalog: dict[str, FrozenSuite]) -> None:
    obligations = {}
    for feature in current_work_features(baseline["requirements"]).values():
        for binding in feature.get("assertions", []):
            obligations.setdefault(binding["case"], []).append((feature["id"], binding))
    for engine in baseline["acceptance_browsers"]:
        engine_suites = [suite["cases"] for suite in catalog.values() if suite["engine"] == engine]
        covered = {case for cases in engine_suites for case in cases}
        missing = set(baseline["mandatory_cases"]) - covered
        if missing:
            raise ValueError(f"验收浏览器 {engine} 的冻结套件未覆盖必跑用例：{', '.join(sorted(missing))}")
        for case, bindings in obligations.items():
            if any(
                case in cases and all(matches_checkpoint(binding, cases[case]) for _, binding in bindings)
                for cases in engine_suites
            ):
                continue
            features = ", ".join(sorted({feature for feature, _ in bindings}))
            raise ValueError(f"验收浏览器 {engine} 的冻结用例 {case} 不含全部约定验收时点：{features}")


def freeze(baseline: Baseline, paths: Iterable[str | Path]) -> dict[str, FrozenSuite]:
    """Build the persisted case catalog exclusively from SHA-matching supplied bytes."""
    declared = baseline["suite_digests"]
    catalog: dict[str, FrozenSuite] = {}
    for supplied in paths:
        path = Path(supplied)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in declared:
            raise ValueError(f"冻结用例文件摘要未登记在 suite_digests：{path}")
        if digest in catalog:
            raise ValueError(f"重复提供了同一份冻结用例文件：{path}")
        catalog[digest] = _read_suite(path, raw, baseline, declared[digest])
    missing = declared.keys() - catalog.keys()
    if missing:
        raise ValueError(f"请使用 --suite 提供每份冻结用例文件，缺少摘要：{', '.join(sorted(missing))}")
    _require_coverage(baseline, catalog)
    return catalog
