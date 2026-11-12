#!/usr/bin/env python3
"""Verify candidate provenance and bytes, without running a browser or changing facts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from render_mermaid_candidate import CONTRACT, DERIVED_FILES, candidate_path, json_read, prepare_payload, require, sha


def _artifact(candidate, name):
    path = candidate / name
    require(path.is_file() and not path.is_symlink() and path.resolve() == path, "候选文件缺失或非本地普通文件：" + name)
    return path.read_bytes()


def verify_candidate(project, directory):
    root = Path(project).resolve()
    candidate = candidate_path(root, directory, new=False)
    manifest = json_read(_artifact(candidate, "candidate-manifest.json"))
    require(isinstance(manifest, dict) and manifest.get("contract") == CONTRACT, "未知候选合同")
    require(manifest.get("formal_delivery") is False, "候选不得宣称正式交付")
    diagram_id = manifest.get("diagram_id")
    require(isinstance(diagram_id, str) and re.fullmatch(r"(?:SC|FLOW)-[0-9]{3,}", diagram_id), "非法候选图 ID")
    source_hashes, expected_files = prepare_payload(root, diagram_id)
    require(manifest.get("source_files") == source_hashes, "来源内容已变化或来源清单被篡改；必须生成新候选")
    recorded = manifest.get("files")
    require(isinstance(recorded, dict) and set(recorded) == DERIVED_FILES, "候选派生文件清单不完整或含非授权文件")
    for name, expected in expected_files.items():
        raw = _artifact(candidate, name)
        require(sha(raw) == recorded[name], "候选文件摘要不匹配：" + name)
        require(raw == expected, "候选派生内容不再对应来源事实：" + name)
    render, browser = manifest.get("render"), manifest.get("browser")
    require(isinstance(render, dict) and isinstance(browser, dict), "缺少渲染/浏览器状态")
    render_state, browser_state = render.get("status"), browser.get("status")
    require(render_state in {"not_run", "passed", "blocked"} and browser_state in {"not_run", "passed", "failed"}, "非法渲染/浏览器状态")
    rendered = manifest.get("rendered_files", {})
    require(isinstance(rendered, dict) and set(rendered) <= {"index.html", "完整流程.svg", "verification.json"}, "非法渲染文件清单")
    for name, digest in rendered.items():
        require(sha(_artifact(candidate, name)) == digest, "渲染文件摘要不匹配：" + name)
    render_pass = False
    if render_state == "passed":
        require(browser_state == "passed" and set(rendered) == {"index.html", "完整流程.svg", "verification.json"}, "缺少真实渲染记录与绑定文件，不得声明渲染通过")
        report = json_read(_artifact(candidate, "verification.json"))
        require(isinstance(report, dict) and report.get("status") == "passed" and report.get("formal_delivery") is False,
                "渲染报告未通过或错误声明正式交付")
        require(report.get("contract") == "mermaid-candidate-verification@1" and report.get("diagram_id") == diagram_id and report.get("opened_via") == "file://", "渲染报告合同、图 ID 或打开方式不匹配")
        require(report.get("failed") == [] and isinstance(report.get("checks"), list) and report["checks"] and all(isinstance(c, dict) and c.get("ok") is True for c in report["checks"]), "渲染报告有失败或缺少检查证据")
        require(isinstance(report.get("geometry"), dict) and report["geometry"].get("status") == "pass" and report["geometry"].get("blockers") == [], "几何检查未通过")
        require(report.get("business_acceptance") == "not_claimed" and report.get("source_review") == "manual_required" and report.get("visual_review") == "not_performed", "报告不得跳过来源人工复核或声明视觉/业务验收")
        require(manifest.get("visual_review") == "not_performed" and manifest.get("business_acceptance") == "not_claimed", "自动候选不得宣称视觉/业务验收")
        render_pass = True
    elif render_state == "not_run":
        require(browser_state == "not_run" and not rendered, "未渲染状态与绑定产物不一致")
        require(not any((candidate / name).exists() for name in ("index.html", "完整流程.svg", "verification.json")), "准备阶段存在未绑定渲染产物")
    return {"contract": CONTRACT, "diagram_id": diagram_id, "status": "candidate_render_passed" if render_pass else "prepared_only" if render_state == "not_run" else "candidate_render_blocked",
            "integrity": "passed", "render_pass": render_pass, "browser": browser_state, "formal_delivery": False,
            "business_acceptance": "not_claimed", "visual_review": "not_performed", "source_review": "manual_required"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir")
    parser.add_argument("candidate_dir")
    args = parser.parse_args()
    try:
        result = verify_candidate(args.project_dir, args.candidate_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] == "candidate_render_blocked" else 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "rejected", "error": str(error), "formal_delivery": False, "render_pass": False}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
