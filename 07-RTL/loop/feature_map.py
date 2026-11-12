"""Check feature-map references and verify the actual delivered routes."""

import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import frontend
from .artifacts import git, repo_path
from .assertions import ASSERTION_FIELDS, browser_case_plan, matches_checkpoint, normalize_assertion
from .state import pack, relative_path, safe_id
from .verification import _report_digest_matches

HEADINGS = ("功能", "场景", "入口", "契约", "必跑用例")


def _columns(line):
    return [item.strip().strip("`") for item in line.strip().strip("|").split("|")]


def _references(value):
    return [] if value in {"", "-", "—"} else [item.strip().strip("`") for item in value.replace("，", ",").split(",")]


def rows(text, *, allow_empty=False):
    table = []
    found = False
    has_separator = False
    for line in text.splitlines():
        if not found:
            found = line.strip().startswith("|") and tuple(_columns(line)) == HEADINGS
            continue
        if not line.strip().startswith("|"):
            break
        fields = _columns(line)
        if len(fields) != len(HEADINGS):
            raise ValueError("功能地图表格列数与表头不一致")
        if all(item and not set(item) - set(":- ") for item in fields):
            has_separator = has_separator or all("-" in item for item in fields)
            continue
        table.append(fields)
    if not table and not (allow_empty and found and has_separator):
        raise ValueError("功能地图缺少「功能 | 场景 | 入口 | 契约 | 必跑用例」数据表")
    return table


def _source(run):
    source = Path(run["project_root"]) / "docs" / "feature-map.md"
    if source.resolve() != source or not source.is_file():
        raise ValueError("请在独立原型项目的 docs/feature-map.md 登记功能与验收关联")
    return source


def _target(entry):
    url = urlsplit(entry)
    if url.scheme or url.netloc or url.query or any(ord(char) < 32 for char in entry):
        raise ValueError(f"功能地图入口须为本地页面：{entry}")
    path = relative_path(unquote(url.path))
    if path.suffix.lower() not in {".html", ".htm"}:
        raise ValueError(f"功能地图入口须为 HTML 页面：{entry}")
    return path, unquote(url.fragment)


def route_plan(content, *, allow_empty=False):
    """Build one browser check per distinct file and fragment in this map."""
    routes = {}
    for feature, _, entry, _, _ in rows(content.decode("utf-8"), allow_empty=allow_empty):
        _target(entry)
        routes.setdefault(entry, []).append(feature)
    return routes


def feature_entry_plan(content, *, allow_empty=False):
    """Keep each feature's business scene and entry together for browser verification."""
    routes, seen = {}, set()
    for feature, scene_id, entry, _, _ in rows(content.decode("utf-8"), allow_empty=allow_empty):
        safe_id(feature, "feature ID")
        safe_id(scene_id, "scene ID")
        _target(entry)
        if feature in seen:
            raise ValueError(f"功能地图重复登记功能点：{feature}")
        seen.add(feature)
        routes.setdefault(entry, []).append({"id": feature, "scene_id": scene_id})
    return routes


def _observed_route(root, entry, url):
    if not isinstance(url, str):
        return False
    file_path, fragment = _target(entry)
    observed = urlsplit(url)
    return (
        observed.scheme == "file" and not observed.netloc and not observed.query
        and unquote(observed.path) == str(root / file_path)
        and (not fragment or unquote(observed.fragment) == fragment)
    )


def _confirmed_result(root, route, result):
    if not isinstance(result, dict) or result.get("status") != "pass" or result.get("errors") != []:
        return False
    identity = result.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("value"), str) or not identity["value"].strip():
        return False
    if not _observed_route(root, route, result.get("url")):
        return False

    _, fragment = _target(route)
    kind, value = identity.get("kind"), identity["value"]
    if not fragment:
        return kind == "document"
    if kind == "document-anchor":
        return not fragment.startswith("/") and value == fragment
    if kind == "route-marker":
        return value in {fragment, "#" + fragment}
    if kind != "navigation-heading":
        return False

    # Earlier staged reports recorded only the verified heading. New reports also
    # retain the active link and label used to identify that heading.
    if "href" not in identity and "label" not in identity:
        return True
    label = identity.get("label")
    return (
        isinstance(label, str) and bool(label.strip()) and label in value
        and _observed_route(root, route, identity.get("href"))
    )


def _confirmed_feature_entry(root, entry, route_url, feature, result):
    return (
        isinstance(result, dict)
        and result.get("status") == "pass"
        and result.get("errors") == []
        and result.get("entry") == entry
        and result.get("url") == route_url
        and _observed_route(root, entry, route_url)
        and result.get("identity") == {
            "kind": "feature-marker", "feature_id": feature["id"], "scene_id": feature["scene_id"],
        }
    )


def _require_feature_entries(root, expected, results):
    failed = []
    for entry, features in expected.items():
        route = results[entry]
        observed = route.get("features")
        expected_ids = {feature["id"] for feature in features}
        if not isinstance(observed, dict) or set(observed) != expected_ids:
            failed.extend(sorted(expected_ids))
            continue
        failed.extend(
            feature["id"] for feature in features
            if not _confirmed_feature_entry(root, entry, route["url"], feature, observed[feature["id"]])
        )
    if failed:
        raise ValueError(f"功能点实际入口校验失败：{', '.join(failed)}")


def _archived_report(context):
    try:
        report_path = Path(context["evidence"])
        if report_path.is_symlink():
            return None
        raw = report_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != context["evidence_sha256"] or not _report_digest_matches(context, digest):
            return None
        report = json.loads(raw)
        if not isinstance(report, dict) or report.get("asset_digest") != context["asset_digest"]:
            return None
        return report
    except (OSError, KeyError, TypeError, ValueError):
        return None


def _frozen_route_cases(baseline, evidence, map_digest, assets):
    """Read observations from the exact archived suite and matching map."""
    for _, context in evidence:
        routes = context.get("feature_routes")
        if not isinstance(routes, dict) or routes.get("map_sha256") != map_digest or context.get("asset_digest") != assets:
            continue
        report = _archived_report(context)
        if report is None or report.get("feature_routes") != routes:
            continue
        suite_input = report.get("suite_input")
        digest = context.get("suite_digest")
        approved = baseline["suite_digests"].get(digest)
        if not approved or not isinstance(suite_input, str):
            continue
        if report.get("suite_digest") != digest or hashlib.sha256(suite_input.encode()).hexdigest() != digest:
            continue
        try:
            suite_cases = json.loads(suite_input)["cases"]
            results = report["cases"]
            for case in suite_cases:
                identifier, steps = case["id"], case["steps"]
                result = results[identifier]
                if identifier not in approved or result["status"] != "pass" or result["errors"]:
                    continue
                locations = result["step_locations"]
                executed, _ = browser_case_plan(steps)
                if not isinstance(locations, list) or len(locations) != len(steps) or result["executed"] != executed:
                    continue
                yield identifier, steps, locations
        except (KeyError, TypeError, ValueError):
            continue


def _assertion_at_route(root, entry, route_url, location, *, react_root_feature=None):
    if not isinstance(location, dict):
        return False
    fragment = unquote(urlsplit(route_url).fragment)
    file_path, configured_fragment = _target(entry)
    root_features = location.get("root_features")
    # Umi renders the hash root on a bare file URL. Verify the marker in
    # the assertion page before treating that URL as the mapped root.
    if (
        configured_fragment == fragment == "/" and react_root_feature
        and isinstance(root_features, list) and react_root_feature in root_features
        and isinstance(location.get("before"), str) and location.get("before") == location.get("after")
    ):
        actual = location["before"]
        return not urlsplit(actual).fragment and _observed_route(root, file_path.as_posix(), actual)
    return all(
        _observed_route(root, entry, location.get(moment))
        and unquote(urlsplit(location[moment]).fragment) == fragment
        for moment in ("before", "after")
    )


def _matches_route_assertion(binding, steps, index):
    if binding.get("checkpoint") is not None:
        return index == binding["checkpoint"]["index"] and matches_checkpoint(binding, steps)
    try:
        return normalize_assertion(steps[index], exact=False) == binding["step"]
    except ValueError:
        return False


def _case_proves_route(root, route, route_url, required, steps, locations, *, react_root_feature=None):
    indices = [
        index for index, (step, location) in enumerate(zip(steps, locations))
        if step["op"] in ASSERTION_FIELDS and _assertion_at_route(
            root, route, route_url, location, react_root_feature=react_root_feature,
        )
    ]
    if required:
        return all(any(_matches_route_assertion(binding, steps, index) for index in indices)
                   for binding in required)
    return any("[data-rtl-feature" not in steps[index].get("selector", "") for index in indices)


def _require_route_behaviors(run, root, content, results, evidence, map_digest, assets):
    baseline = run["baseline"]
    if "requirements" not in baseline or not baseline.get("suite_digests"):
        return
    observed = {}
    for identifier, steps, locations in _frozen_route_cases(baseline, evidence, map_digest, assets):
        observed.setdefault(identifier, []).append((steps, locations))
    features = {item["id"]: item for item in baseline["requirements"]["features"]}
    react = frontend.profile(baseline) == "react"
    failed = []
    for feature, _, route, _, cases in rows(content.decode("utf-8"), allow_empty=True):
        bindings = features[feature].get("assertions", [])
        for case in _references(cases):
            required = [binding for binding in bindings if binding["case"] == case]
            if not any(_case_proves_route(root, route, results[route]["url"], required, steps, locations,
                                          react_root_feature=feature if react else None)
                       for steps, locations in observed.get(case, [])):
                failed.append(f"{feature}/{case} @ {route}")
    if failed:
        raise ValueError(f"缺少绑定功能地图实际入口的业务断言证据：{', '.join(failed)}")


def _allows_empty_map(baseline):
    plan = baseline.get("requirements")
    return plan is not None and not any(feature["status"] == "active" for feature in plan["features"])


def require_live_routes(run, entry, manifest, evidence, *, task_specs=None):
    """Require independent browser evidence for every actual route in the current map."""
    baseline = run["baseline"]
    strict_entries = baseline.get("feature_entry_checks")
    if not baseline.get("feature_route_checks") and not strict_entries:
        return None
    content = _source(run).read_bytes()
    if task_specs is not None or "requirements" in baseline:
        _check_references(content, task_specs or {}, manifest, baseline)
    make_plan = feature_entry_plan if strict_entries else route_plan
    expected = make_plan(content, allow_empty=_allows_empty_map(baseline))
    map_digest = hashlib.sha256(content).hexdigest()
    assets = hashlib.sha256(pack(manifest).encode()).hexdigest()
    for row, context in reversed(evidence):
        observed = context.get("feature_routes")
        if not isinstance(observed, dict) or observed.get("map_sha256") != map_digest:
            continue
        if context.get("asset_digest") != assets:
            continue
        archive = _archived_report(context)
        if archive is None or archive.get("feature_routes") != observed:
            raise ValueError("功能地图的落盘浏览器报告缺失或发生变化，请重新实走")
        results = observed.get("routes")
        if not isinstance(results, dict) or set(results) != set(expected):
            raise ValueError("落盘浏览器报告未覆盖当前功能地图全部入口")
        failed = [
            route for route, result in results.items()
            if not _confirmed_result(entry.parent, route, result)
        ]
        if failed:
            raise ValueError(f"功能地图的实际路由校验失败：{', '.join(failed)}")
        if not expected and not _confirmed_result(entry.parent, entry.name, observed.get("entry_check")):
            raise ValueError("无活动功能时仍须实测实际落盘入口")
        if strict_entries:
            _require_feature_entries(entry.parent, expected, results)
            _require_route_behaviors(run, entry.parent, content, results, evidence, map_digest, assets)
        return {"map_sha256": map_digest, "evidence_id": row["id"], "routes": sorted(expected)}
    raise ValueError("缺少当前功能地图及落盘成品的独立浏览器路由证据")


def _check_entry(feature, entry, files):
    file_path, _ = _target(entry)
    if files is not None and file_path.as_posix() not in files:
        raise ValueError(f"功能入口不在候选版本中：{feature}/{entry}")


def _check_planned_references(map_rows, tasks, files, plan):
    from .requirements import current_work_features

    active = {feature["id"]: feature for feature in plan["features"] if feature["status"] == "active"}
    current = current_work_features(plan)
    task_edges = {}
    for task_id, spec in tasks.items():
        features = spec.get("feature_ids", [])
        if not features or not set(features) <= current.keys():
            raise ValueError(f"任务缺少有效的台账功能点关联：{task_id}")
        for feature_id in features:
            task_edges.setdefault(feature_id, set()).update(spec["edges"])

    seen, covered = set(), set()
    for feature_id, scene_id, entry, edges, cases in map_rows:
        feature = active.get(feature_id)
        if not feature:
            raise ValueError(f"功能地图引用了未冻结或已移除的功能点：{feature_id}")
        if feature_id in seen:
            raise ValueError(f"功能地图重复登记功能点：{feature_id}")
        if scene_id != feature["scene_id"]:
            raise ValueError(
                f"功能地图的业务场景与冻结台账不一致：{feature_id}/{scene_id}，"
                f"应填写业务场景编号 {feature['scene_id']}，旧项目请先更新功能地图"
            )
        if feature_id not in task_edges:
            raise ValueError(f"功能地图的功能点没有关联的已登记任务：{feature_id}")
        _check_entry(feature_id, entry, files)
        if not set(_references(edges)) <= task_edges[feature_id]:
            raise ValueError(f"功能引用了关联任务未声明的契约：{feature_id}")
        mapped = set(_references(cases))
        if mapped != set(feature["cases"]):
            raise ValueError(f"功能地图的必跑用例与冻结台账不一致：{feature_id}")
        seen.add(feature_id)
        covered.update(mapped)

    missing = active.keys() - seen
    if missing:
        raise ValueError(f"功能地图缺少已冻结功能点：{', '.join(sorted(missing))}")
    return {"features": sorted(seen), "cases": sorted(covered)}


def _check_references(content, tasks, files, baseline):
    map_rows = rows(content.decode("utf-8"), allow_empty=_allows_empty_map(baseline))
    if "requirements" in baseline:
        return _check_planned_references(map_rows, tasks, files, baseline["requirements"])

    mandatory = set(baseline["mandatory_cases"])
    seen, covered, referenced_tasks = set(), set(), set()
    for feature, task, entry, edges, cases in map_rows:
        safe_id(feature, "feature ID")
        if feature in seen or task not in tasks:
            raise ValueError(f"功能编号重复或场景不存在：{feature}/{task}")
        seen.add(feature)
        referenced_tasks.add(task)
        _check_entry(feature, entry, files)
        if not set(_references(edges)).issubset(tasks[task]["edges"]):
            raise ValueError(f"功能引用了场景未声明的契约：{feature}")
        mapped = set(_references(cases))
        if not mapped or not mapped.issubset(mandatory):
            raise ValueError(f"功能缺少本轮必跑用例关联：{feature}")
        covered.update(mapped)
    missing_tasks, missing_cases = set(tasks) - referenced_tasks, mandatory - covered
    if missing_tasks or missing_cases:
        raise ValueError(f"功能地图缺少场景或必跑用例：{', '.join(sorted(missing_tasks | missing_cases))}")
    return {"features": sorted(seen), "cases": sorted(covered)}


def check_planned_references(run, task_specs):
    """Check an existing S1 map before the source tree has been built."""
    if "requirements" not in run["baseline"]:
        return None
    planned = Path(run["project_root"]) / "docs" / "feature-map.md"
    if not planned.exists() and not planned.is_symlink():
        return None
    source = _source(run)
    return {
        "path": str(source),
        **_check_references(source.read_bytes(), task_specs, None, run["baseline"]),
    }


def check(store, run_id):
    run = store.run(run_id)
    source = _source(run)
    tasks = {task["id"]: task["spec"] for task in store.tasks(run_id)}
    candidate = run["candidate"]
    if not candidate:
        raise ValueError("尚无可核对功能地图的候选")
    if frontend.profile(run["baseline"]) == "react":
        files = frontend.get_build(store, run_id, candidate)["manifest"]
    else:
        files = {
            raw.decode("utf-8")
            for raw in git(repo_path(store, run_id), "ls-tree", "-rz", "--name-only", candidate).split(b"\0")
            if raw
        }
    content = source.read_bytes()
    return {
        "path": str(source), "candidate": candidate,
        **_check_references(content, tasks, files, run["baseline"]),
        "map_sha256": hashlib.sha256(content).hexdigest(),
        "routes": sorted(route_plan(content, allow_empty=_allows_empty_map(run["baseline"]))),
    }
