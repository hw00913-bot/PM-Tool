"""Read an approved 04 package using the existing prototype import verifier."""

import json
import os
from pathlib import Path
import subprocess
import sys

from .state import LoopError


ROOT = Path(__file__).resolve().parents[1]
LIVE_PROVENANCE = {"registered-delivery", "owner-state-only"}
BINDING_KEYS = {
    "package", "manifest_sha256", "baseline_id", "baseline_fingerprint", "project",
    "project_version", "approval_provenance", "g3_fingerprint",
}
FACT_ENTRYPOINTS = (
    "normalized_user_stories", "scenario_index", "sequence_index", "interface_index",
    "project_facts", "business_rules", "feature_list", "page_index", "traceability", "open_items",
)
CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from loop.upstream_analysis import _emit_verified_package
_emit_verified_package(*sys.argv[2:])
"""


def _verifier_scripts():
    configured = os.environ.get("RTL_ANALYSIS_VERIFIER_ROOT")
    root = Path(configured).expanduser() if configured is not None else ROOT.parent / "02_LOOP_PROTOTYPE"
    if not root.is_absolute():
        raise LoopError("RTL_ANALYSIS_VERIFIER_ROOT 须是受信任的 02 工具绝对路径")
    scripts = root.resolve() / "orchestrator" / "scripts"
    if any(not (scripts / name).is_file() for name in ("import_analysis.py", "project_versions.py")):
        raise LoopError(f"找不到 02 需求包核验器，请检查 RTL_ANALYSIS_VERIFIER_ROOT：{scripts}")
    return scripts


def _read_verified(package, *, include_facts):
    scripts = _verifier_scripts()
    try:
        package = Path(package).expanduser().resolve(strict=True)
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-c", CHILD, str(ROOT), str(scripts), str(package),
             str(int(include_facts))],
            capture_output=True, text=True, encoding="utf-8", timeout=45,
        )
    except (OSError, RuntimeError, TypeError, ValueError, subprocess.TimeoutExpired) as exc:
        raise LoopError(f"无法核验 04 冻结包：{exc}") from exc
    if result.returncode:
        reason = result.stderr.strip() or "02 核验器未返回有效结果"
        raise LoopError(f"04 冻结包核验失败：{reason}")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise LoopError("02 核验器未返回 JSON") from exc
    if not isinstance(data, dict) or set(data) != {"binding", "facts"} or not all(
        isinstance(item, dict) for item in data.values()
    ):
        raise LoopError("02 核验结果格式无效")
    return data


def inspect(package, *, include_facts=True):
    """Inspect a package without modifying it or its upstream registry."""
    return _read_verified(package, include_facts=include_facts)


def _require_live(binding):
    if not isinstance(binding, dict) or set(binding) != BINDING_KEYS:
        raise LoopError("04 冻结包来源绑定无效")
    provenance = binding["approval_provenance"]
    if (
        not isinstance(binding["project_version"], dict)
        or not isinstance(provenance, str)
        or provenance not in LIVE_PROVENANCE
    ):
        raise LoopError("04 冻结包缺少可核对的当前版本批准，不能作为 RTL 上游依据")


def bind(package):
    """Verify and return the receipt to record with an explicit RTL S1 approval."""
    binding = _read_verified(package, include_facts=False)["binding"]
    _require_live(binding)
    return binding


def require_current(binding, *, include_facts=True):
    """Recheck both the frozen bytes and the approval in the live upstream registry."""
    _require_live(binding)
    data = _read_verified(binding["package"], include_facts=include_facts)
    current = data["binding"]
    if current["approval_provenance"] not in LIVE_PROVENANCE:
        raise LoopError("04 冻结包的上游批准来源已失效")
    if current["approval_provenance"] == "registered-delivery" and binding["approval_provenance"] == "owner-state-only":
        # A subsequent registration strengthens the same frozen snapshot's provenance.
        current = {**current, "approval_provenance": "owner-state-only"}
    if current != binding:
        raise LoopError("04 冻结包身份与登记来源不一致，请重新导入并确认 S1")
    return {**data, "binding": binding}


def _check_current_gate_bindings(verifier, package):
    reference = package.handoff.get("project_version")
    if not reference:
        return
    from project_versions import _plain_path, read, version

    ledger = read(_plain_path(reference["registry"]))
    approved = version(ledger, reference["version_id"])["stages"].get("analysis")
    if not approved or approved.get("snapshot_id") != reference["snapshot_id"]:
        raise LoopError("上游分析批准在核对期间已失效")
    state = verifier.read_json(_plain_path(Path(approved["root"]) / "runtime" / "loop-state.json"))
    reviewed = state.get("stages", {}).get("S8", {}).get("reviewed_fingerprint")
    if reviewed is not None:
        g3 = package.handoff["gates"]["G3"].get("approval_snapshot") or {}
        if reviewed != g3.get("fingerprint"):
            raise LoopError("上游 S8 核验指纹与 G3 冻结快照不一致")
    # The existing 02 verifier compares the historical gate fields. Newer 04
    # packages also carry the approved scope and API source fingerprints.
    fields = {
        "G1": ("scenario_fingerprint",),
        "G2": ("interface_sources_fingerprint",),
        "G3": ("approval_snapshot",),
    }
    for gate, fingerprints in fields.items():
        actual = state["gates"][gate]
        claimed = package.handoff["gates"][gate]
        checks = ("status", "approved_by", "approved_at", "note", *fingerprints)
        if gate == "G2" and ("interface_fingerprint" in claimed or "interface_sources_fingerprint" in actual):
            checks += ("interface_fingerprint",)
        if any(actual.get(field) != claimed.get(field) for field in checks):
            raise LoopError(f"{gate} 交接确认依据与上游当前批准不一致")


def _facts(verifier, package):
    handoff = package.handoff
    payloads = {}
    for entry in FACT_ENTRYPOINTS:
        if entry in handoff["entrypoints"]:
            path = verifier.entrypoint_path(handoff, entry)
            payloads[path] = verifier.load_json_bytes(package.payloads[path], path)
    for entry, array, directory in (("scenario_index", "scenarios", "scenarios/"),
                                    ("sequence_index", "sequences", "sequences/")):
        index = payloads[verifier.entrypoint_path(handoff, entry)]
        rows = index.get(array, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise LoopError(f"04 冻结 {array} 索引格式无效")
        for row in rows:
            if "file" not in row:
                continue
            path = verifier.safe_archive_path(row["file"])
            if not path.startswith(directory) or not path.endswith(".json") or path not in package.payloads:
                raise LoopError(f"04 冻结 {array} 明细未纳入对应目录的清单：{path}")
            payloads[path] = verifier.load_json_bytes(package.payloads[path], path)
    return {"handoff": handoff, "payloads": payloads}


def _emit_verified_package(scripts, source, include_facts):
    sys.path.insert(0, scripts)
    import import_analysis as verifier

    try:
        package = verifier.verify_package(Path(source))
        _check_current_gate_bindings(verifier, package)
        handoff = package.handoff
        g3 = handoff["gates"]["G3"].get("approval_snapshot") or {}
        binding = {
            "package": package.source,
            "manifest_sha256": package.manifest_sha256,
            "baseline_id": package.baseline_id,
            "baseline_fingerprint": package.fingerprint,
            "project": handoff["project"],
            "project_version": handoff.get("project_version"),
            "approval_provenance": package.approval_provenance,
            "g3_fingerprint": g3.get("fingerprint"),
        }
        facts = _facts(verifier, package) if include_facts == "1" else {}
    except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps({"binding": binding, "facts": facts}, ensure_ascii=False))
