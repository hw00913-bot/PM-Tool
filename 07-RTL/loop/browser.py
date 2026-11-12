"""Run browser checks against one stable local prototype directory."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from .assertions import browser_case_plan
from .feature_map import feature_entry_plan, route_plan
from .state import pack

ROOT = Path(__file__).resolve().parents[1]


def asset_digest(directory):
    directory = Path(directory)
    files = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"Invalid prototype asset: {relative}")
        if path.is_file():
            with path.open("rb") as asset:
                files[relative.as_posix()] = hashlib.file_digest(asset, "sha256").hexdigest()
    return hashlib.sha256(pack(files).encode()).hexdigest()


def _record_assertions(report, suite_bytes):
    suite = json.loads(suite_bytes)
    for case in suite["cases"]:
        outcome = report["cases"].get(case["id"])
        if not outcome or outcome["status"] != "pass":
            continue
        executed, assertions = browser_case_plan(case["steps"])
        if outcome["executed"] != executed:
            raise ValueError(f"Browser report does not match the executed steps: {case['id']}")
        outcome["assertions"] = assertions


def run(entry, suite, report, *, feature_map=None, feature_entries=False):
    entry, suite = (Path(item).resolve(strict=True) for item in (entry, suite))
    report = Path(report).resolve()
    if not entry.is_file() or entry.name != "index.html":
        raise ValueError("Verify the prototype's actual index.html")
    if report.is_relative_to(entry.parent) or suite.is_relative_to(entry.parent):
        raise ValueError("Verification inputs and reports belong outside the prototype")
    if report.exists():
        raise ValueError("Use a new path for the browser report")
    if feature_entries and not feature_map:
        raise ValueError("Feature entry verification requires a feature map")
    map_file = Path(feature_map).resolve(strict=True) if feature_map else None
    if map_file and map_file.is_relative_to(entry.parent):
        raise ValueError("The feature map belongs outside the prototype")
    map_bytes = map_file.read_bytes() if map_file else None
    routes = None
    if map_bytes is not None:
        routes = (
            feature_entry_plan(map_bytes, allow_empty=True)
            if feature_entries else list(route_plan(map_bytes, allow_empty=True))
        )
    before = asset_digest(entry.parent)
    input_bytes = suite.read_bytes()
    with tempfile.TemporaryDirectory(prefix="rtl-browser-") as folder:
        temporary = Path(folder)
        fixed_suite = temporary / "suite.json"
        raw = temporary / "report.json"
        fixed_suite.write_bytes(input_bytes)
        command = ["node", str(ROOT / "scripts" / "browser-check.cjs"), str(entry), str(fixed_suite), str(raw)]
        if routes is not None:
            plan = temporary / "routes.json"
            plan.write_text(json.dumps(routes, ensure_ascii=False), encoding="utf-8")
            command.append(str(plan))
        result = subprocess.run(command, check=False)
        if not raw.is_file():
            return result.returncode or 2
        value = json.loads(raw.read_text(encoding="utf-8"))
    if asset_digest(entry.parent) != before:
        raise ValueError("Prototype changed during verification. Run the suite again.")
    value["asset_digest"] = before
    value["suite_digest"] = hashlib.sha256(input_bytes).hexdigest()
    value["suite_input"] = input_bytes.decode("utf-8")
    _record_assertions(value, input_bytes)
    if map_bytes is not None:
        if map_file.read_bytes() != map_bytes:
            raise ValueError("Feature map changed during verification. Run the suite again.")
        feature_routes = {
            "map_sha256": hashlib.sha256(map_bytes).hexdigest(),
            "routes": value.pop("route_checks"),
        }
        if not routes:
            feature_routes["entry_check"] = value.pop("entry_check")
        value["feature_routes"] = feature_routes
    with report.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return result.returncode
