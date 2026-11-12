from __future__ import annotations

import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET
from test_sources import prototype_script


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

from swimlane_layout import audit_swimlane_layout, build_swimlane_layout
from source_index import inspect_source_index
from analysis_contract import WORKFLOW_VERSION, PAGE_STRUCTURE, page_errors, page_html_errors, sequence_html_errors, svg_roots
from render_atlases import sequence_svg
from native_diagrams import page_structure_svg
from feature_contract import GRANULARITY_VERSION
from plantuml_renderer import (
    compile_plantuml_activity,
    compile_plantuml_business_flow,
    resolve_plantuml_runtime,
    render_plantuml_svg,
    PlantUMLRenderError,
)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def stable_atlas_html(value: str) -> str:
    # Only the generated header timestamp varies across identical input runs.
    return re.sub(r"( · 自动汇编于 )\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", r"\1<generated-time>", value, count=1)


class WorkflowTest(unittest.TestCase):
    def run_script(self, name: str, *args: object, expected: int = 0) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(SCRIPTS / name), *(str(arg) for arg in args)]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        self.assertEqual(expected, result.returncode, msg=f"{command}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
        if name == 'loop.py' and args[0] == 'init' and expected == 0:
            # This legacy full-diagram suite explicitly requests both views.
            self.run_script('loop.py', 'request-diagrams', args[1], '--business-flow', '--interaction-sequence',
                            '--by', 'test-fixture', '--reason', 'full diagram regression fixture')
        return result

    def advance(self, root: Path) -> None:
        self.run_script("loop.py", "start", root)
        self.run_script("loop.py", "complete", root)

    def fake_plantuml(self, path: Path) -> Path:
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "source = sys.stdin.buffer.read().decode()\n"
            "assert '@startmindmap' not in source and 'participant_' not in source, 'PlantUML is only for business flows'\n"
            "sys.stdout.write('<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 640 480\"><text x=\"20\" y=\"30\">PlantUML test</text></svg>')\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def test_full_loop_and_incremental_invalidation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="requirement-analysis-loop-") as temp:
            root = Path(temp) / "demo"
            fake_plantuml = self.fake_plantuml(Path(temp) / "fake-plantuml")
            self.run_script("loop.py", "init", root, "--name", "云呼叫中心测试", "--version", "1.0.0")

            (root / "inputs" / "user-stories.md").write_text(
                "# 用户故事\n\n## 故事 1\n作为运营人员，我希望创建外呼任务，以便批量触达客户。\n",
                encoding="utf-8",
            )
            (root / "inputs" / "interface-documents" / "vendor-api.md").write_text(
                "# 供应商接口\n\n## 创建任务\nPOST /tasks\n",
                encoding="utf-8",
            )
            wiki_snapshot = root / "inputs" / "wiki-context" / "tasks-loop-context.md"
            wiki_snapshot.parent.mkdir()
            wiki_snapshot.write_text("# 已有任务知识\n\n运营人员可创建外呼任务。当前接口须独立核对。\n", encoding="utf-8")
            write_json(root / "inputs" / "source-index.json", {
                "schema_version": "1.0",
                "sources": [{
                    "id": "SRC-001", "type": "llm_wiki", "title": "外呼任务知识",
                    "snapshot_path": "inputs/wiki-context/tasks-loop-context.md",
                    "wiki_ref": "wiki/tasks", "raw_ref": "inputs/interface-documents/vendor-api.md",
                    "version": "1.0", "received_at": "2026-09-06T10:00:00+08:00",
                    "status": "verified", "coverage": ["任务创建的历史背景；接口由 S4 独立核对"], "issue_ids": [],
                }],
            })
            self.advance(root)

            write_json(
                root / "memory" / "normalized-user-stories.json",
                {
                    "schema_version": "1.0",
                    "user_stories": [
                        {
                            "id": "US-001",
                            "title": "创建外呼任务",
                            "role": "运营人员",
                            "trigger": "需要批量触达客户",
                            "goal": "配置并提交外呼任务",
                            "business_value": "提高触达效率",
                            "preconditions": ["已登录"],
                            "acceptance_criteria": [{"id": "AC-001", "type": "main", "statement": "任务创建成功"}],
                            "source_refs": ["inputs/user-stories.md#故事-1", "inputs/source-index.json#SRC-001"],
                            "status": "in_scope",
                        }
                    ],
                },
            )
            self.advance(root)

            write_json(
                root / "scenarios" / "scenario-index.json",
                {
                    "schema_version": "1.0",
                    "scenarios": [
                        {
                            "id": "SC-001",
                            "name": "创建外呼任务",
                            "file": "scenarios/SC-001.json",
                            "priority": "P0",
                            "scope": "in_scope",
                            "status": "confirmed",
                            "source_story_ids": ["US-001"],
                            "ui_surface": "required",
                        }
                    ],
                },
            )
            self.run_script("render_documents.py", root, "--kind", "scenarios")
            self.advance(root)
            self.run_script("loop.py", "approve", root, "G1", "--by", "测试用户", "--note", "场景范围确认")

            write_json(
                root / "scenarios" / "SC-001.json",
                {
                    "schema_version": "1.0",
                    "id": "SC-001",
                    "name": "创建外呼任务",
                    "summary": "运营人员配置并提交一批外呼任务。",
                    "actors": ["运营人员", "外呼中台", "呼叫供应商"],
                    "trigger": "运营人员需要批量触达客户",
                    "preconditions": ["运营人员已登录"],
                    "main_flow": [
                        {"step": 1, "actor": "运营人员", "action": "填写任务信息", "system_response": "校验输入", "rule_ids": ["BR-001"]},
                        {"step": 2, "actor": "运营人员", "action": "提交任务", "system_response": "创建任务并返回成功", "rule_ids": []},
                    ],
                    "alternate_flows": [{"id": "ALT-001", "condition": "号码格式错误", "steps": ["提示错误并保留已填内容"]}],
                    "postconditions": ["任务进入待执行状态"],
                    "acceptance_points": ["提交成功后可查看任务"],
                    "source_story_ids": ["US-001"],
                    "flow_diagram": {
                        "lanes": [
                            {"id": "operator", "name": "运营人员", "kind": "actor"},
                            {"id": "console", "name": "外呼中台", "kind": "system"},
                            {"id": "vendor", "name": "呼叫供应商", "kind": "external_system"},
                        ],
                        "nodes": [
                            {"id": "N-START", "lane_id": "operator", "type": "start", "order": 1, "label": "开始创建任务"},
                            {"id": "N-001", "lane_id": "operator", "type": "action", "order": 2, "label": "填写任务信息", "step_ref": 1},
                            {"id": "N-002", "lane_id": "console", "type": "action", "order": 3, "label": "校验任务信息"},
                            {"id": "N-003", "lane_id": "console", "type": "decision", "order": 4, "label": "校验通过？"},
                            {"id": "N-004", "lane_id": "operator", "type": "action", "order": 5, "label": "提交任务", "step_ref": 2},
                            {"id": "N-005", "lane_id": "console", "type": "action", "order": 6, "label": "创建平台任务"},
                            {"id": "N-006", "lane_id": "vendor", "type": "action", "order": 7, "label": "创建供应商任务"},
                            {"id": "N-007", "lane_id": "console", "type": "action", "order": 8, "label": "保存供应商任务号"},
                            {"id": "N-SUCCESS", "lane_id": "operator", "type": "end", "order": 9, "label": "任务创建成功"},
                            {"id": "N-ERROR", "lane_id": "console", "type": "action", "order": 5, "label": "生成校验错误"},
                            {"id": "N-FAIL", "lane_id": "operator", "type": "end", "order": 6, "label": "保留输入并提示错误"},
                        ],
                        "edges": [
                            {"from": "N-START", "to": "N-001"},
                            {"from": "N-001", "to": "N-002"},
                            {"from": "N-002", "to": "N-003"},
                            {"from": "N-003", "to": "N-004", "label": "是"},
                            {"from": "N-004", "to": "N-005"},
                            {"from": "N-005", "to": "N-006"},
                            {"from": "N-006", "to": "N-007"},
                            {"from": "N-007", "to": "N-SUCCESS"},
                            {"from": "N-003", "to": "N-ERROR", "label": "否", "type": "failure", "path_id": "ALT-001"},
                            {"from": "N-ERROR", "to": "N-FAIL", "type": "failure"},
                        ],
                    },
                },
            )
            write_json(
                root / "memory" / "project-facts.json",
                {
                    "schema_version": "1.0",
                    "project": {"name": "云呼叫中心测试", "version": "1.0.0", "objective": "支持运营创建外呼任务"},
                    "actors": [{"name": "运营人员", "description": "配置和查看任务"}],
                    "systems": [{"name": "外呼中台", "description": "编排任务"}, {"name": "呼叫供应商", "description": "执行呼叫"}],
                    "scope": {"in": ["创建外呼任务"], "out": ["实时坐席通话"]},
                    "product_structure": [{"name": "任务管理", "description": "创建和查看任务"}],
                    "state_models": [{"id": "STATE-001", "name": "任务状态", "states": ["草稿", "待执行"], "transitions": ["草稿 → 待执行：提交成功"]}],
                    "assumptions": [],
                },
            )
            write_json(
                root / "memory" / "business-rules.json",
                {
                    "schema_version": "1.0",
                    "business_rules": [{"id": "BR-001", "name": "号码校验", "statement": "提交前校验号码格式", "source_scenario_ids": ["SC-001"]}],
                    "business_flows": [{"id": "FLOW-001", "name": "任务创建流程", "scenario_ids": ["SC-001"], "steps": ["填写任务", "提交任务", "供应商创建成功"]}],
                },
            )
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--scenario-renderer", "native")
            scenario_html = (root / "outputs" / "business-scenario-atlas.html").read_text(encoding="utf-8")
            self.assertIn('data-diagram-type="swimlane"', scenario_html)
            self.assertIn('data-lane-count="3"', scenario_html)
            self.advance(root)

            write_json(
                root / "interfaces" / "interface-index.json",
                {
                    "schema_version": "1.0",
                    "interfaces": [
                        {
                            "id": "API-001",
                            "name": "创建呼叫任务",
                            "provider": "呼叫供应商",
                            "source_scenario_ids": ["SC-001"],
                            "used_by_sequence_ids": [],
                            "verification_status": "verified",
                            "source_refs": ["inputs/interface-documents/vendor-api.md#创建任务"],
                            "request_fields": ["name", "numbers"],
                            "response_fields": ["task_id"],
                            "auth": "API Key",
                            "timeout": "5s",
                            "idempotency": "client_request_id",
                            "rate_limit": "100/min",
                            "errors": ["400", "429", "500"],
                            "notes": [],
                        }
                    ],
                },
            )
            self.run_script("render_documents.py", root, "--kind", "interfaces")
            self.advance(root)
            self.run_script("loop.py", "approve", root, "G2", "--by", "测试用户", "--note", "接口责任边界确认")

            write_json(
                root / "sequences" / "sequence-index.json",
                {
                    "schema_version": "1.0",
                    "sequences": [
                        {"id": "SEQ-001", "scenario_id": "SC-001", "name": "创建外呼任务时序", "file": "sequences/SEQ-001.json", "status": "confirmed"}
                    ],
                },
            )
            write_json(
                root / "sequences" / "SEQ-001.json",
                {
                    "schema_version": "1.0",
                    "id": "SEQ-001",
                    "scenario_id": "SC-001",
                    "name": "创建外呼任务时序",
                    "participants": [
                        {"id": "operator", "name": "运营人员", "kind": "actor"},
                        {"id": "console", "name": "外呼中台", "kind": "system"},
                        {"id": "vendor", "name": "呼叫供应商", "kind": "external_system"},
                    ],
                    "messages": [
                        {"step": 1, "from": "operator", "to": "console", "message": "提交任务", "type": "request", "api_id": None, "condition": None},
                        {"step": 2, "from": "console", "to": "vendor", "message": "创建供应商任务", "type": "external_call", "api_id": "API-001", "condition": None},
                        {"step": 3, "from": "vendor", "to": "console", "message": "返回任务编号", "type": "response", "api_id": "API-001", "condition": None},
                        {"step": 4, "from": "console", "to": "operator", "message": "显示创建成功", "type": "response", "api_id": None, "condition": None},
                    ],
                    "notes": ["供应商失败时返回可重试错误"],
                },
            )
            self.run_script("render_atlases.py", root, "--kind", "sequences", "--sequence-renderer", "native")
            self.run_script("loop.py", "start", root)
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("used_by_sequence_ids", rejected.stderr)
            api_path = root / "interfaces/interface-index.json"
            api_data = json.loads(api_path.read_text())
            api_data["interfaces"][0]["used_by_sequence_ids"] = ["SEQ-001"]
            api_data["interfaces"][0]["auth"] = "changed without G2"
            write_json(api_path, api_data)
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("G2", rejected.stderr)
            api_data["interfaces"][0]["auth"] = "API Key"
            write_json(api_path, api_data)
            self.run_script("render_documents.py", root, "--kind", "interfaces")
            sequence_atlas_path = root / "outputs/interaction-sequence-atlas.html"
            native_atlas = sequence_atlas_path.read_text()
            sequence_atlas_path.write_text(native_atlas.replace('data-renderer="native"', 'data-renderer="plantuml"'))
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("PlantUML", rejected.stderr)
            sequence_atlas_path.write_text(native_atlas)
            self.run_script("loop.py", "complete", root)

            write_json(
                root / "planning" / "page-index.json",
                {
                    "schema_version": "1.0",
                    "pages": [
                        {
                            "id": "PAGE-001",
                            "name": "新建任务页",
                            "menu_path": ["任务管理", "新建任务"],
                            "roles": ["运营人员"],
                            "function_ids": [],
                            "sequence_ids": ["SEQ-001"],
                            "scenario_ids": ["SC-001"],
                            "states": ["initial", "submitting", "success", "error"],
                            "fields": [{"name": "任务名称", "description": "必填"}],
                            "actions": [{"id": "ACTION-001", "name": "提交", "source_function_ids": [], "source_scenario_ids": ["SC-001"], "permission": "task:create"}],
                            "acceptance_points": ["错误时保留已填内容"],
                        }
                    ],
                },
            )
            self.assertEqual([], json.loads((root / "memory/feature-list.json").read_text())["features"])
            self.run_script("render_documents.py", root, "--kind", "pages")
            self.run_script("render_atlases.py", root, "--kind", "pages", "--plantuml-command", fake_plantuml)
            self.advance(root)
            self.run_script("loop.py", "start", root)
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("function_ids", rejected.stderr)

            write_json(
                root / "memory" / "feature-list.json",
                {
                    "schema_version": "1.0",
                    "granularity_version": GRANULARITY_VERSION,
                    "features": [
                        {
                            "id": "FUNC-001",
                            "version": "1.0.0",
                            "module_l1": "任务中心",
                            "module_l2": "外呼任务",
                            "module_l3": "任务创建",
                            "name": "创建外呼任务",
                            "description": "运营人员填写任务信息并提交给呼叫供应商。",
                            "notes": None,
                            "change_type": "new",
                            "boundary": {
                                "business_object": "外呼任务", "primary_action": "创建外呼任务",
                                "system_boundary": "本方外呼中台创建任务；不实现供应商内部拨号",
                                "initiator": "运营人员", "trigger": "提交外呼任务",
                                "primary_outcome": "创建可被供应商受理的外呼任务",
                                "completion_result": "任务已创建并返回受理结果，失败时反馈失败",
                                "included_scope": ["校验任务输入", "提交任务并反馈受理结果"],
                                "excluded_scope": ["供应商内部拨号"],
                                "business_rules": ["任务提交遵循已确认权限与参数规则"],
                                "exception_paths": ["供应商返回失败时不展示成功"],
                                "dependencies": ["呼叫供应商创建任务能力"], "assumptions": [],
                                "split_rationale": "提交形成任务受理结果，字段和技术调用不另拆",
                            },
                            "change_detail": {"before": None, "after": "1.0.0 支持任务创建", "impact": ["新增任务创建及受理反馈"]},
                            "source_story_ids": ["US-001"],
                            "source_acceptance_ids": ["AC-001"],
                            "source_scenario_ids": ["SC-001"],
                        }
                    ],
                },
            )
            page_path = root / "planning/page-index.json"
            page_data = json.loads(page_path.read_text())
            page_data["pages"][0]["function_ids"] = ["FUNC-001"]
            page_data["pages"][0]["actions"][0]["source_function_ids"] = ["FUNC-001"]
            write_json(page_path, page_data)
            self.run_script("render_documents.py", root, "--kind", "baseline")
            self.run_script("render_documents.py", root, "--kind", "pages")
            feature_markdown = (root / "outputs" / "feature-list.md").read_text(encoding="utf-8")
            self.assertIn("FUNC-001", feature_markdown)
            self.assertIn("| 新增 |", feature_markdown)
            self.assertIn("供应商内部拨号", feature_markdown)
            self.assertIn("单一可验收业务操作", feature_markdown)
            self.run_script("loop.py", "complete", root)

            self.run_script("loop.py", "start", root)
            self.run_script("render_documents.py", root)
            fake_plantuml = self.fake_plantuml(Path(temp) / "fake-plantuml")
            self.run_script("render_atlases.py", root, "--plantuml-command", fake_plantuml)
            scenario_html = (root / "outputs" / "business-scenario-atlas.html").read_text(encoding="utf-8")
            baseline_markdown = (root / "outputs" / "product-structure-and-business-process-baseline.md").read_text(encoding="utf-8")
            self.assertIn('id="FLOW-001"', scenario_html)
            self.assertIn('data-diagram-type="business-process"', scenario_html)
            self.assertIn('data-flow-id="FLOW-001"', scenario_html)
            self.assertIn("business-scenario-atlas.html#FLOW-001", baseline_markdown)
            failed = self.run_script("verify_analysis.py", root, expected=1)
            self.assertIn("missing_trace_link", (root / "runtime" / "verification-report.json").read_text(encoding="utf-8"))
            self.run_script("sync_traceability.py", root)
            self.run_script("verify_analysis.py", root)
            verification = json.loads((root / "runtime" / "verification-report.json").read_text(encoding="utf-8"))
            self.assertEqual({"total": 1, "new": 1, "iteration": 0, "with_page": 1}, verification["summary"]["features"])
            self.assertEqual({"required": 1, "valid": 1}, verification["summary"]["business_process_diagrams"])
            self.run_script("loop.py", "complete", root)
            self.run_script("loop.py", "approve", root, "G3", "--by", "测试用户", "--note", "冻结基线")

            self.run_script("loop.py", "start", root)
            native_atlas = sequence_atlas_path.read_text()
            sequence_atlas_path.write_text(native_atlas.replace('data-renderer="native"', 'data-renderer="plantuml"'))
            rejected = self.run_script("package_delivery.py", root, expected=2)
            self.assertIn("PlantUML", rejected.stderr)
            sequence_atlas_path.write_text(native_atlas)
            self.run_script("package_delivery.py", root)
            self.run_script("loop.py", "complete", root)
            state = json.loads((root / "runtime" / "loop-state.json").read_text(encoding="utf-8"))
            self.assertEqual("complete", state["run"]["status"])
            self.assertEqual("0.16.0", state["engine"]["version"])
            self.assertEqual("stories-flows-interfaces-sequences-pages-features@1", state["workflow_version"])
            manifest = json.loads((root / "deliverables" / "delivery-manifest.json").read_text(encoding="utf-8"))
            self.assertTrue((root / manifest["package_file"]).is_file())
            manifest_paths = {item["path"] for item in manifest["files"]}
            self.assertIn("memory/feature-list.json", manifest_paths)
            self.assertIn("outputs/feature-list.md", manifest_paths)
            self.assertIn("outputs/page-structure-atlas.html", manifest_paths)
            completed_stages = [event["stage"] for event in state["history"] if event["event"] in {"stage_completed", "stage_completed_waiting_gate", "loop_completed"}]
            self.assertEqual([f"S{index}" for index in range(10)], completed_stages)
            self.assertIn("prototype-handoff.json", manifest_paths)
            handoff = json.loads((root / "deliverables" / "prototype-handoff.json").read_text(encoding="utf-8"))
            self.assertEqual({"name": "requirement-analysis-to-prototype", "version": "1.0"}, handoff["contract"])
            self.assertEqual("pass", handoff["verification"]["status"])
            self.assertEqual(["FUNC-001"], handoff["id_catalog"]["feature_ids"])
            self.assertEqual(["PAGE-001"], handoff["id_catalog"]["page_ids"])
            self.assertEqual(["ACTION-001"], handoff["id_catalog"]["action_ids"])
            self.assertEqual("memory/feature-list.json", handoff["entrypoints"]["feature_list"]["path"])
            self.assertEqual("provenance_only", handoff["entrypoints"]["source_index"]["role"])
            self.assertEqual("requirement-analysis-loop", handoff["consumer_rules"]["llm_wiki_consumed_by"])
            self.assertIs(True, handoff["consumer_rules"]["raw_inputs_are_audit_only"])
            self.assertEqual({"total": 1, "llm_wiki": 1}, verification["summary"]["input_sources"])
            self.assertEqual("compare", handoff["diagrams"]["business_processes"]["renderer_mode"])
            self.assertEqual("compare", handoff["diagrams"]["business_scenarios"]["renderer_mode"])
            self.assertEqual(1, handoff["diagrams"]["business_scenarios"]["count"])
            self.assertEqual("native", handoff["diagrams"]["interaction_sequences"]["renderer_mode"])
            self.assertEqual("native", handoff["diagrams"]["page_structure"]["renderer_mode"])
            self.assertEqual(handoff["baseline"]["fingerprint"], manifest["baseline_fingerprint"])
            with zipfile.ZipFile(root / manifest["package_file"], "r") as archive:
                self.assertIn("delivery-manifest.json", archive.namelist())
                self.assertIn("prototype-handoff.json", archive.namelist())
                zipped_handoff = json.loads(archive.read("prototype-handoff.json"))
                self.assertEqual(handoff, zipped_handoff)
                self.assertEqual(wiki_snapshot.read_bytes(), archive.read("inputs/wiki-context/tasks-loop-context.md"))
            consumer = prototype_script("orchestrator/scripts/import_analysis.py", SKILL_DIR)
            inspected = subprocess.run(
                [sys.executable, str(consumer), "inspect", str(root / manifest["package_file"])],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, inspected.returncode, msg=inspected.stdout + inspected.stderr)
            self.assertIn(handoff["baseline"]["id"], inspected.stdout)

            self.run_script("loop.py", "invalidate", root, "--id", "FUNC-001", "--reason", "功能说明调整")
            state = json.loads((root / "runtime" / "loop-state.json").read_text(encoding="utf-8"))
            self.assertEqual("S7", state["run"]["current_stage"])
            self.assertEqual("stale", state["stages"]["S7"]["status"])

            self.run_script("loop.py", "invalidate", root, "--id", "API-001", "--reason", "供应商接口版本更新")
            state = json.loads((root / "runtime" / "loop-state.json").read_text(encoding="utf-8"))
            self.assertEqual("S4", state["run"]["current_stage"])
            self.assertEqual("stale", state["stages"]["S5"]["status"])
            self.assertEqual("stale", state["stages"]["S4"]["status"])
            self.assertEqual("passed", state["stages"]["S3"]["status"])
            self.assertEqual("pending", state["gates"]["G2"]["status"])
            self.assertEqual("pending", state["gates"]["G3"]["status"])

            self.run_script("loop.py", "invalidate", root, "--from-stage", "S3", "--reason", "业务场景图升级为泳道图")
            state = json.loads((root / "runtime" / "loop-state.json").read_text(encoding="utf-8"))
            self.assertEqual("S3", state["run"]["current_stage"])
            self.assertEqual("approved", state["gates"]["G1"]["status"])
            self.assertEqual("stale", state["stages"]["S3"]["status"])

            self.run_script("loop.py", "invalidate", root, "--id", "SRC-001", "--reason", "Wiki 查询输出更新")
            state = json.loads((root / "runtime" / "loop-state.json").read_text(encoding="utf-8"))
            self.assertEqual("S1", state["run"]["current_stage"])
            self.assertEqual("pending", state["gates"]["G1"]["status"])
            self.assertEqual("pending", state["gates"]["G3"]["status"])

    def test_wiki_input_registration_and_partial_source_gates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="analysis-wiki-input-") as temp:
            root = Path(temp) / "analysis"
            self.run_script("loop.py", "init", root, "--name", "Wiki 边界测试")
            (root / "inputs" / "user-stories.md").write_text("# 用户故事\n\n用户希望查看账户状态。\n", encoding="utf-8")
            # No Wiki is a valid input mode; legacy projects may omit the index.
            (root / "inputs" / "source-index.json").unlink()
            self.assertEqual(([], []), inspect_source_index(root))
            snapshot = root / "inputs" / "wiki-context" / "account.md"
            snapshot.parent.mkdir()
            snapshot.write_text("# Wiki\n\n部分账户状态待核对。\n", encoding="utf-8")
            self.run_script("loop.py", "start", root)
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("未登记", rejected.stderr)
            source = {
                "id": "SRC-001", "type": "llm_wiki", "title": "账户历史知识",
                "snapshot_path": "inputs/wiki-context/account.md", "wiki_ref": "wiki/accounts",
                "raw_ref": None, "version": "unknown", "received_at": "unknown",
                "status": "partial", "coverage": ["已有状态；未覆盖本期新增状态"], "issue_ids": [],
            }
            write_json(root / "inputs" / "source-index.json", {"schema_version": "1.0", "sources": [source]})
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("issue_ids", rejected.stderr)
            source["issue_ids"] = ["ISSUE-001"]
            write_json(root / "inputs" / "source-index.json", {"schema_version": "1.0", "sources": [source]})
            rejected = self.run_script("loop.py", "complete", root, expected=2)
            self.assertIn("来源问题不存在", rejected.stderr)
            write_json(root / "runtime" / "open-items.json", {"schema_version": "1.0", "items": [{
                "id": "ISSUE-001", "severity": "high", "status": "open",
                "summary": "本期新增状态需业务确认", "related_ids": ["SRC-001"],
            }]})
            self.run_script("loop.py", "complete", root)
            # Intake may progress with an explicit gap; S8/G3 must not silently accept it.
            self.run_script("sync_traceability.py", root)
            self.run_script("verify_analysis.py", root, expected=1)
            report = json.loads((root / "runtime" / "verification-report.json").read_text())
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("blocking_open_item", codes)
            self.assertNotIn("invalid_source_index", codes)
            self.assertNotIn("unknown_issue_ref", codes)
            self.assertNotIn("unknown_trace_endpoint", codes)

            for key, value in (("status", []), ("snapshot_path", "../outside.md"), ("coverage", []), ("issue_ids", "ISSUE-001")):
                with self.subTest(key=key):
                    invalid = {**source, key: value}
                    write_json(root / "inputs" / "source-index.json", {"schema_version": "1.0", "sources": [invalid]})
                    self.assertTrue(inspect_source_index(root)[1])

    def test_legacy_stage_state_cannot_be_silently_relabelled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="analysis-legacy-order-") as temp:
            root = Path(temp)
            self.run_script("loop.py", "init", root, "--name", "旧版状态隔离测试")
            path = root / "runtime/loop-state.json"
            state = json.loads(path.read_text())
            state.pop("workflow_version")
            state["engine"]["version"] = "0.9.0"
            state["stages"]["S4"]["name"] = "交互时序分析"
            state["stages"]["S5"]["name"] = "外部接口核验"
            write_json(path, state)
            before = path.read_bytes()
            status = self.run_script("loop.py", "status", root)
            self.assertIn("S4 交互时序分析", status.stdout)
            self.assertIn("旧版", status.stdout)
            for command in (("start", root), ("complete", root),
                            ("invalidate", root, "--id", "API-001", "--reason", "test")):
                self.assertIn("未改写状态", self.run_script("loop.py", *command, expected=2).stderr)
                self.assertEqual(before, path.read_bytes())
            self.run_script("package_delivery.py", root, expected=2)
            self.assertEqual(before, path.read_bytes())

    def test_incremental_order_respects_changes_not_semantic_backlinks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="analysis-incremental-order-") as temp:
            root = Path(temp)
            self.run_script("loop.py", "init", root, "--name", "增量顺序隔离测试")
            path = root / "runtime/loop-state.json"
            completed = json.loads(path.read_text())
            completed["run"].update({"current_stage": "S9", "status": "complete"})
            for stage in completed["stages"].values():
                stage["status"] = "passed"
            for gate in completed["gates"].values():
                gate["status"] = "approved"
            write_json(root / "runtime/traceability.json", {"links": [
                {"from": "API-001", "to": "SEQ-001", "type": "constrains_sequence"},
                {"from": "SEQ-001", "to": "API-001", "type": "calls"},
                {"from": "SEQ-001", "to": "PAGE-001", "type": "informs_page"},
                {"from": "FUNC-001", "to": "PAGE-001", "type": "surfaced_by"},
            ]})
            for item_id, expected_stage in (("FLOW-001", "S3"), ("API-001", "S4"),
                                            ("SEQ-001", "S5"), ("PAGE-001", "S6"), ("FUNC-001", "S7")):
                with self.subTest(item=item_id):
                    write_json(path, completed)
                    self.run_script("loop.py", "invalidate", root, "--id", item_id, "--reason", "test")
                    state = json.loads(path.read_text())
                    self.assertEqual(expected_stage, state["run"]["current_stage"])
                    if expected_stage in {"S5", "S6", "S7"}:
                        self.assertEqual("approved", state["gates"]["G2"]["status"])
            write_json(path, completed)
            self.run_script("loop.py", "invalidate", root, "--id", "API-001", "--reason", "first")
            self.run_script("loop.py", "invalidate", root, "--id", "FUNC-001", "--reason", "second")
            self.assertEqual("S4", json.loads(path.read_text())["run"]["current_stage"])

    def test_native_sequence_renderer_policy_and_complete_labels(self) -> None:
        with tempfile.TemporaryDirectory(prefix="analysis-native-sequences-") as temp:
            root = Path(temp) / "analysis"
            self.run_script("loop.py", "init", root, "--name", "原生图测试")
            detail = {
                "id": "SEQ-001", "name": "复杂消息", "scenario_id": "SC-001",
                "participants": [{"id": "user", "name": "用户"},
                                 {"id": "system", "name": "充值系统的完整长名称" * 5}],
                "messages": [
                    {"step": 1, "from": "user", "to": "system", "type": "request", "api_id": "API-001",
                     "message": "保留完整请求文本" * 18, "condition": "只有账户有效且订单校验通过" * 6},
                    {"step": 2, "from": "system", "to": "system", "type": "event", "message": "幂等检查及本地记账" * 14},
                    {"step": 3, "from": "system", "to": "user", "type": "response", "message": "返回已确认结果"},
                ],
            }
            write_json(root / "sequences/sequence-index.json", {"sequences": [
                {"id": "SEQ-001", "file": "sequences/SEQ-001.json", "scenario_id": "SC-001"},
            ]})
            write_json(root / "sequences/SEQ-001.json", detail)
            self.run_script("render_atlases.py", root, "--kind", "sequences", "--plantuml-command", root / "missing-engine")
            self.assertFalse((root / "runtime/plantuml").exists())
            self.assertEqual([], sequence_html_errors(root))
            path = root / "outputs/interaction-sequence-atlas.html"
            html = path.read_text()
            svg = svg_roots(html)[0]
            ns = {"s": "http://www.w3.org/2000/svg"}
            groups = [node for node in svg.iter() if node.get("data-message-index")]
            for message, group in zip(detail["messages"], groups):
                visible = "".join(group.find("s:text", ns).itertext())
                self.assertIn(message["message"], visible)
                if message.get("condition"):
                    self.assertIn(message["condition"], visible)
                    self.assertIn(message["api_id"], visible)
                    self.assertTrue(any(message["api_id"] in (span.text or "") for span in group.findall("s:text/s:tspan", ns)))
            self.assertIn("充值系统的完整长名称" * 5, "".join(svg.itertext()))
            request = groups[0].find("s:line", ns)
            response = groups[2].find("s:line", ns)
            self.assertLess(float(request.get("x1")), float(request.get("x2")))
            self.assertGreater(float(response.get("x1")), float(response.get("x2")))
            self.assertEqual(response.get("y1"), response.get("y2"))
            self.assertEqual("6 4", response.get("stroke-dasharray"))
            self.assertIsNotNone(groups[1].find("s:path", ns))
            other = {**detail, "id": "SEQ-002"}
            self.assertNotEqual(svg.find("s:defs/s:marker", ns).get("id"),
                                ET.fromstring(sequence_svg(other)).find("s:defs/s:marker", ns).get("id"))
            for renderer in ("plantuml", "compare"):
                self.run_script("render_atlases.py", root, "--kind", "sequences", "--sequence-renderer", renderer, expected=2)
                self.assertEqual(html, path.read_text())
            for diagram_type in ("sequence", "page-structure"):
                with self.assertRaises(PlantUMLRenderError):
                    render_plantuml_svg("unused", detail, None, diagram_type=diagram_type)
            path.write_text(html.replace('data-renderer="native"', 'data-renderer="plantuml"'))
            self.assertTrue(sequence_html_errors(root))
            path.write_text(html.replace('data-message-index="2"', 'data-message-index="999"'))
            self.assertTrue(sequence_html_errors(root))
            path.write_text(html)
            detail["messages"][0]["condition"] = "更改条件但尚未更新图"
            write_json(root / "sequences/SEQ-001.json", detail)
            self.assertTrue(sequence_html_errors(root))

    def test_page_structure_before_features_and_empty_ui(self) -> None:
        with tempfile.TemporaryDirectory(prefix="analysis-page-order-") as temp:
            root = Path(temp) / "analysis"
            self.run_script("loop.py", "init", root, "--name", "页面层级测试")
            write_json(root / "scenarios/scenario-index.json", {"scenarios": [
                {"id": "SC-001", "scope": "in_scope", "ui_surface": "required"},
            ]})
            write_json(root / "sequences/sequence-index.json", {"sequences": [
                {"id": "SEQ-001", "scenario_id": "SC-001"},
            ]})
            pages = [{"id": f"PAGE-{index:03}", "name": name, "menu_path": path,
                      "scenario_ids": ["SC-001"], "sequence_ids": ["SEQ-001"], "function_ids": [], "actions": []}
                     for index, (name, path) in enumerate([
                         ("账户总览", ["账户中心"]), ("充值", ["账户中心", "交易管理"]),
                         ("支付结果", []), ("渠道配置", ["管理中心", "交易管理"]),
                     ], start=1)]
            write_json(root / "planning/page-index.json", {"pages": pages})
            self.assertEqual([], page_errors(root))
            self.assertTrue(page_errors(root, final=True))
            source = page_structure_svg(pages, '测试 <script>alert("x")</script> & 中文')
            ET.fromstring(source)
            self.assertNotIn("<script>", source)
            self.assertIn("&lt;script&gt;", source)
            self.assertEqual(2, source.count("交易管理"))
            fake = Path(temp) / "missing-plantuml"
            self.run_script("render_atlases.py", root, "--kind", "pages", "--plantuml-command", fake)
            self.assertEqual([], page_html_errors(root))
            before = (root / PAGE_STRUCTURE).read_bytes()
            (root / PAGE_STRUCTURE).write_bytes(before.replace(b'data-renderer="native"', b'data-renderer="plantuml"'))
            self.assertTrue(page_html_errors(root))
            (root / PAGE_STRUCTURE).write_bytes(before)
            pages[0]["sequence_ids"] = ["SEQ-999"]
            write_json(root / "planning/page-index.json", {"pages": pages})
            self.assertTrue(page_html_errors(root))
            self.run_script("render_atlases.py", root, "--kind", "pages", "--plantuml-command", fake, expected=2)
            self.assertEqual(before, (root / PAGE_STRUCTURE).read_bytes())
            write_json(root / "scenarios/scenario-index.json", {"scenarios": [
                {"id": "SC-001", "scope": "in_scope", "ui_surface": "not_required"},
            ]})
            write_json(root / "planning/page-index.json", {"pages": []})
            write_json(root / "memory/feature-list.json", {"features": [
                {"id": "FUNC-001", "source_scenario_ids": ["SC-001"]},
            ]})
            self.assertEqual([], page_errors(root, final=True))
            self.run_script("render_atlases.py", root, "--kind", "pages", "--plantuml-command", fake)
            self.assertEqual([], page_html_errors(root))
            self.assertIn("本期没有 UI 页面", (root / PAGE_STRUCTURE).read_text())
            self.assertFalse((root / "runtime/plantuml").exists())

    def test_recharge_flow_is_a_branched_swimlane(self) -> None:
        with tempfile.TemporaryDirectory(prefix="recharge-swimlane-") as temp:
            root = Path(temp) / "recharge"
            self.run_script("loop.py", "init", root, "--name", "账户充值")
            write_json(
                root / "scenarios" / "scenario-index.json",
                {
                    "schema_version": "1.0",
                    "scenarios": [
                        {
                            "id": "SC-001",
                            "name": "账户充值",
                            "file": "scenarios/SC-001.json",
                            "priority": "P0",
                            "scope": "in_scope",
                            "status": "confirmed",
                            "source_story_ids": ["US-001"],
                            "ui_surface": "required",
                        }
                    ],
                },
            )
            recharge = {
                "schema_version": "1.0",
                "id": "SC-001",
                "name": "账户充值",
                "summary": "用户通过第三方支付渠道给账户充值。",
                "actors": ["用户", "充值系统", "支付渠道"],
                "trigger": "账户余额不足",
                "preconditions": ["用户已登录"],
                "main_flow": [
                    {"step": 1, "actor": "用户", "action": "选择金额并确认", "system_response": "创建充值订单", "rule_ids": []}
                ],
                "alternate_flows": [{"id": "ALT-001", "condition": "支付失败", "steps": ["提示失败"]}],
                "postconditions": ["支付成功时余额增加"],
                "acceptance_points": ["成功和失败结果均明确"],
                "source_story_ids": ["US-001"],
                "flow_diagram": {
                    "lanes": [
                        {"id": "customer", "name": "用户", "kind": "actor"},
                        {"id": "recharge", "name": "充值系统", "kind": "system"},
                        {"id": "payment", "name": "支付渠道", "kind": "external_system"},
                    ],
                    "nodes": [
                        {"id": "N-START", "lane_id": "customer", "type": "start", "order": 1, "label": "发起充值"},
                        {"id": "N-001", "lane_id": "customer", "type": "action", "order": 2, "label": "选择金额并确认", "step_ref": 1},
                        {"id": "N-002", "lane_id": "recharge", "type": "action", "order": 3, "label": "创建充值订单"},
                        {"id": "N-003", "lane_id": "payment", "type": "action", "order": 4, "label": "执行支付"},
                        {"id": "N-004", "lane_id": "recharge", "type": "decision", "order": 5, "label": "支付成功？"},
                        {"id": "N-005", "lane_id": "recharge", "type": "action", "order": 6, "label": "增加账户余额"},
                        {"id": "N-SUCCESS", "lane_id": "customer", "type": "end", "order": 7, "label": "充值成功"},
                        {"id": "N-FAIL", "lane_id": "customer", "type": "end", "order": 6, "label": "提示支付失败"},
                    ],
                    "edges": [
                        {"from": "N-START", "to": "N-001"},
                        {"from": "N-001", "to": "N-002"},
                        {"from": "N-002", "to": "N-003"},
                        {"from": "N-003", "to": "N-004"},
                        {"from": "N-004", "to": "N-005", "label": "成功"},
                        {"from": "N-005", "to": "N-SUCCESS"},
                        {"from": "N-004", "to": "N-FAIL", "label": "失败", "type": "failure", "path_id": "ALT-001"},
                    ],
                },
            }
            write_json(root / "scenarios" / "SC-001.json", recharge)
            write_json(
                root / "memory" / "business-rules.json",
                {
                    "schema_version": "1.0",
                    "business_rules": [],
                    "business_flows": [
                        {
                            "id": "FLOW-001",
                            "name": "账户充值端到端流程",
                            "scenario_ids": ["SC-001"],
                            "steps": ["选择充值金额", "创建充值订单", "支付并更新余额"],
                        }
                    ],
                },
            )
            write_json(
                root / "sequences" / "sequence-index.json",
                {
                    "schema_version": "1.0",
                    "sequences": [
                        {
                            "id": "SEQ-001",
                            "scenario_id": "SC-001",
                            "name": "账户充值时序",
                            "file": "sequences/SEQ-001.json",
                            "status": "confirmed",
                        }
                    ],
                },
            )
            sequence = {
                "schema_version": "1.0",
                "id": "SEQ-001",
                "scenario_id": "SC-001",
                "name": "账户充值时序",
                "participants": [
                    {"id": "customer", "name": "用户", "kind": "actor"},
                    {"id": "recharge", "name": "充值系统", "kind": "system"},
                    {"id": "payment", "name": "支付渠道", "kind": "external_system"},
                ],
                "messages": [
                    {"step": 1, "from": "customer", "to": "recharge", "message": "提交充值", "type": "request", "api_id": None, "condition": None},
                    {"step": 2, "from": "recharge", "to": "payment", "message": "创建支付", "type": "external_call", "api_id": "API-001", "condition": "订单有效"},
                    {"step": 3, "from": "payment", "to": "recharge", "message": "返回支付结果", "type": "response", "api_id": "API-001", "condition": None},
                ],
                "notes": ["支付失败时保持余额不变。"],
            }
            write_json(root / "sequences" / "SEQ-001.json", sequence)
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--scenario-renderer", "native")
            html = (root / "outputs" / "business-scenario-atlas.html").read_text(encoding="utf-8")
            self.assertIn('id="FLOW-001"', html)
            self.assertIn('data-diagram-type="business-process"', html)
            self.assertIn('data-flow-id="FLOW-001"', html)
            self.assertIn('data-diagram-type="swimlane"', html)
            self.assertIn('data-lane-count="3"', html)
            self.assertEqual(3, html.count('class="swimlane lane"'))
            self.assertIn('data-node-type="decision"', html)
            self.assertIn('data-from="N-004" data-to="N-005"', html)
            self.assertIn('data-router-version="2"', html)
            self.assertIn('data-layout-audit="pass"', html)
            self.assertIn(">成功</text>", html)
            self.assertIn(">失败 · ALT-001</text>", html)
            self.assertEqual([], audit_swimlane_layout(build_swimlane_layout(recharge["flow_diagram"])))

            plantuml_source = compile_plantuml_activity(recharge)
            self.assertIn("|#EDF4FF|lane_1| 用户", plantuml_source)
            self.assertIn("if (支付成功？) then (成功)", plantuml_source)
            self.assertIn("else (失败 · ALT-001)", plantuml_source)
            injected = json.loads(json.dumps(recharge))
            injected["flow_diagram"]["nodes"][1]["label"] = "提交; @enduml\n!include https://example.com/diagram"
            safe_source = compile_plantuml_activity(injected)
            self.assertEqual(1, safe_source.count("@enduml"))
            self.assertNotIn("!include", safe_source)
            self.assertNotIn("https://", safe_source)
            business_source = compile_plantuml_business_flow(
                {
                    "id": "FLOW-001",
                    "name": "账户充值端到端流程",
                    "steps": ["选择充值金额", "创建充值订单", "支付并更新余额"],
                }
            )
            self.assertIn("title FLOW-001 · 账户充值端到端流程", business_source)
            self.assertIn(":创建充值订单;", business_source)
            sequence_source = sequence_svg(sequence)
            ET.fromstring(sequence_source)
            self.assertIn('data-renderer="native"', sequence_source)
            self.assertIn('data-message-type="response"', sequence_source)
            self.assertIn('stroke-dasharray="6 4"', sequence_source)
            self.assertIn("[API-001]", sequence_source)
            self.assertIn("条件：订单有效", sequence_source)
            injected_sequence = json.loads(json.dumps(sequence))
            injected_sequence["messages"][0]["message"] = '提交 <script>alert("x")</script> & 条件'
            safe_sequence_source = sequence_svg(injected_sequence)
            ET.fromstring(safe_sequence_source)
            self.assertNotIn("<script>", safe_sequence_source)
            self.assertIn("&lt;script&gt;", safe_sequence_source)

            fake_plantuml = self.fake_plantuml(Path(temp) / "fake-plantuml")
            write_json(root / "planning/page-index.json", {"pages": [{
                "id": "PAGE-001", "name": "充值页", "menu_path": ["账户中心"],
                "scenario_ids": ["SC-001"], "sequence_ids": ["SEQ-001"], "function_ids": [], "actions": [],
            }]})
            self.run_script(
                "render_atlases.py",
                root,
                "--plantuml-command",
                fake_plantuml,
            )
            comparison_html = (root / "outputs" / "business-scenario-atlas.html").read_text(encoding="utf-8")
            sequence_html = (root / "outputs" / "interaction-sequence-atlas.html").read_text(encoding="utf-8")
            self.assertIn('data-renderer-mode="compare"', comparison_html)
            self.assertIn('data-renderer="plantuml"', comparison_html)
            self.assertIn('data-plantuml-render="pass"', comparison_html)
            self.assertIn('data-renderer="native"', comparison_html)
            self.assertIn('data-diagram-action="download"', comparison_html)
            self.assertNotIn("kroki.io", comparison_html)
            self.assertNotIn("plantuml.com", comparison_html)
            self.assertFalse((root / "runtime" / "plantuml").exists())
            # Source retention is an explicit diagnostic request, while the
            # formal HTML is identical and never depends on stored .puml files.
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml, "--keep-plantuml-source")
            self.assertEqual(stable_atlas_html(comparison_html), stable_atlas_html((root / "outputs" / "business-scenario-atlas.html").read_text(encoding="utf-8")))
            self.assertTrue((root / "runtime" / "plantuml" / "FLOW-001.puml").is_file())
            self.assertTrue((root / "runtime" / "plantuml" / "SC-001.puml").is_file())
            source_before = {p.name: p.read_bytes() for p in (root / "runtime" / "plantuml").glob("*.puml")}
            source_times = {p.name: p.stat().st_mtime_ns for p in (root / "runtime" / "plantuml").glob("*.puml")}
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml, "--keep-plantuml-source")
            self.assertEqual(source_before, {p.name: p.read_bytes() for p in (root / "runtime" / "plantuml").glob("*.puml")})
            self.assertEqual(source_times, {p.name: p.stat().st_mtime_ns for p in (root / "runtime" / "plantuml").glob("*.puml")}, 'Same facts reconstruct the same source without replacing retained diagnostics')
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml)
            self.assertEqual(source_before, {p.name: p.read_bytes() for p in (root / "runtime" / "plantuml").glob("*.puml")}, 'Default rendering must preserve previous diagnostic material')
            manual = root / "runtime" / "plantuml" / "SC-001.puml"
            manual.write_text("独立人工排障内容，请保留。\n", encoding="utf-8")
            manual_before = manual.read_bytes()
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml)
            self.assertEqual(manual_before, manual.read_bytes())
            self.assertEqual(stable_atlas_html(comparison_html), stable_atlas_html((root / "outputs" / "business-scenario-atlas.html").read_text(encoding="utf-8")))
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml, "--keep-plantuml-source", expected=2)
            self.assertEqual(manual_before, manual.read_bytes(), 'Explicit diagnostics must reject overwriting independent prior material')
            self.assertFalse((root / "runtime" / "plantuml" / "SEQ-001.puml").exists())
            self.assertFalse((root / "runtime" / "plantuml" / "page-structure.puml").exists())
            # Explicit diagnostic retention must not follow parent directory
            # links into unrelated storage. Default rendering ignores them.
            external = Path(temp) / "external-diagnostics"
            external.mkdir()
            retained = root / "runtime" / "plantuml"
            retained.rename(root / "runtime" / "retained-diagnostics")
            retained.symlink_to(external, target_is_directory=True)
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml)
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml, "--keep-plantuml-source", expected=2)
            self.assertEqual([], list(external.iterdir()))
            retained.unlink()
            (root / "runtime" / "retained-diagnostics").rename(retained)
            runtime = root / "runtime"
            runtime.rename(root / "retained-runtime")
            runtime.symlink_to(external, target_is_directory=True)
            self.run_script("render_atlases.py", root, "--kind", "scenarios", "--plantuml-command", fake_plantuml, "--keep-plantuml-source", expected=2)
            self.assertEqual([], list(external.iterdir()))
            runtime.unlink()
            (root / "retained-runtime").rename(runtime)
            self.assertIn('data-renderer="native"', sequence_html)
            self.assertNotIn('data-renderer-mode="compare"', sequence_html)
            self.assertIn('data-diagram-type="sequence"', sequence_html)
            self.assertIn('data-sequence-id="SEQ-001"', sequence_html)
            self.assertNotIn('data-plantuml-render="pass"', sequence_html)
            self.assertIn('data-renderer="native"', sequence_html)
            self.run_script("verify_analysis.py", root, expected=1)
            verification = json.loads((root / "runtime" / "verification-report.json").read_text(encoding="utf-8"))
            self.assertEqual(1, verification["summary"]["plantuml_diagrams"]["present"])
            self.assertTrue(verification["summary"]["plantuml_diagrams"]["complete"])
            self.assertEqual(1, verification["summary"]["plantuml_business_process_diagrams"]["present"])
            self.assertTrue(verification["summary"]["plantuml_business_process_diagrams"]["complete"])
            self.assertEqual(1, verification["summary"]["native_sequence_diagrams"]["present"])
            self.assertTrue(verification["summary"]["native_sequence_diagrams"]["complete"])
            self.assertEqual({"required": 1, "valid": 1}, verification["summary"]["sequence_diagrams"])
            self.assertFalse(any(item["code"] == "remote_diagram_dependency" for item in verification["errors"]))

            atlas_path = root / "outputs" / "business-scenario-atlas.html"
            atlas_path.write_text(
                comparison_html.replace(' data-flow-id="FLOW-001"', ""),
                encoding="utf-8",
            )
            self.run_script("verify_analysis.py", root, expected=1)
            verification = json.loads((root / "runtime" / "verification-report.json").read_text(encoding="utf-8"))
            self.assertTrue(any(item["code"] == "missing_business_flow_diagram" for item in verification["errors"]))

            sequence_path = root / "outputs" / "interaction-sequence-atlas.html"
            sequence_path.write_text(
                sequence_html.replace(' data-sequence-id="SEQ-001"', ""),
                encoding="utf-8",
            )
            self.run_script("verify_analysis.py", root, expected=1)
            verification = json.loads((root / "runtime" / "verification-report.json").read_text(encoding="utf-8"))
            self.assertTrue(any(item["code"] == "missing_sequence_diagram" for item in verification["errors"]))

            missing_runtime = self.run_script(
                "render_atlases.py",
                root,
                "--kind",
                "scenarios",
                "--scenario-renderer",
                "plantuml",
                "--plantuml-command",
                root / "missing-plantuml",
                expected=2,
            )
            self.assertIn("找不到可执行 PlantUML", missing_runtime.stderr)

            fake_java = Path(temp) / "fake-java"
            fake_java.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_java.chmod(0o755)
            fake_jar = Path(temp) / "plantuml.jar"
            fake_jar.write_bytes(b"not-a-real-jar")
            runtime = resolve_plantuml_runtime(jar=str(fake_jar), java_command=str(fake_java))
            self.assertEqual(str(fake_java), runtime.command[0])
            self.assertIn(str(fake_jar.resolve()), runtime.command)

            invalid_branch = json.loads(json.dumps(recharge))
            invalid_branch["flow_diagram"]["edges"][-1].pop("label")
            write_json(root / "scenarios" / "SC-001.json", invalid_branch)
            failed = self.run_script(
                "render_atlases.py",
                root,
                "--kind",
                "scenarios",
                "--scenario-renderer",
                "native",
                expected=2,
            )
            self.assertIn("分支", failed.stderr)

            recharge.pop("flow_diagram")
            write_json(root / "scenarios" / "SC-001.json", recharge)
            failed = self.run_script(
                "render_atlases.py",
                root,
                "--kind",
                "scenarios",
                "--scenario-renderer",
                "native",
                expected=2,
            )
            self.assertIn("flow_diagram", failed.stderr)

    def test_feature_list_contract_rejects_invalid_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-list-contract-") as temp:
            root = Path(temp) / "feature-list"
            self.run_script("loop.py", "init", root, "--name", "功能清单校验")
            write_json(
                root / "memory" / "feature-list.json",
                {
                    "schema_version": "1.0",
                    "features": [
                        {
                            "id": "FUNC-001",
                            "version": "",
                            "module_l1": "账户中心",
                            "module_l2": "充值管理",
                            "module_l3": "",
                            "name": "账户充值",
                            "description": "支持账户充值。",
                            "notes": [],
                            "change_type": "unchanged",
                            "source_story_ids": [],
                            "source_acceptance_ids": [],
                            "source_scenario_ids": [],
                        }
                    ],
                },
            )
            self.run_script("verify_analysis.py", root, expected=1)
            report = json.loads((root / "runtime" / "verification-report.json").read_text(encoding="utf-8"))
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("missing_feature_field", codes)
            self.assertIn("invalid_feature_module_l3", codes)
            self.assertIn("invalid_feature_notes", codes)
            self.assertIn("invalid_feature_change_type", codes)
            self.assertIn("missing_feature_trace", codes)

    def test_complex_recharge_routes_do_not_overlap_or_hit_nodes(self) -> None:
        model = {
            "lanes": [
                {"id": "user", "name": "租户用户", "kind": "actor"},
                {"id": "usage", "name": "用量中心", "kind": "system"},
                {"id": "ledger", "name": "计费账本", "kind": "external_system"},
            ],
            "nodes": [
                {"id": "N-START", "lane_id": "user", "type": "start", "order": 1, "label": "进入使用情况"},
                {"id": "N-001", "lane_id": "usage", "type": "decision", "order": 2, "label": "是超级管理员？"},
                {"id": "N-SUPER", "lane_id": "user", "type": "end", "order": 3, "label": "不提供入口"},
                {"id": "N-002", "lane_id": "usage", "type": "action", "order": 3, "label": "确定当前租户"},
                {"id": "N-003", "lane_id": "usage", "type": "decision", "order": 4, "label": "租户上下文合法？"},
                {"id": "N-DENIED", "lane_id": "user", "type": "end", "order": 5, "label": "拒绝越权数据"},
                {"id": "N-004", "lane_id": "ledger", "type": "action", "order": 5, "label": "查询账户概览"},
                {"id": "N-005", "lane_id": "usage", "type": "decision", "order": 6, "label": "概览加载成功？"},
                {"id": "N-FAIL", "lane_id": "user", "type": "end", "order": 7, "label": "概览失败可重试"},
                {"id": "N-OVERVIEW", "lane_id": "usage", "type": "action", "order": 7, "label": "展示账户概览"},
                {"id": "N-DAY-CHECK", "lane_id": "usage", "type": "decision", "order": 8, "label": "存在按日记录？"},
                {"id": "N-DAY-EMPTY", "lane_id": "user", "type": "end", "order": 9, "label": "按日明细为空"},
                {"id": "N-006", "lane_id": "usage", "type": "action", "order": 9, "label": "展示按日明细"},
                {"id": "N-007", "lane_id": "user", "type": "action", "order": 10, "label": "选择日期"},
                {"id": "N-008", "lane_id": "ledger", "type": "action", "order": 11, "label": "查询任务消耗"},
                {"id": "N-009", "lane_id": "usage", "type": "decision", "order": 12, "label": "任务查询结果？"},
                {"id": "N-DETAIL-EMPTY", "lane_id": "user", "type": "end", "order": 12.5, "label": "任务明细为空"},
                {"id": "N-010", "lane_id": "usage", "type": "action", "order": 13, "label": "展示任务明细"},
                {"id": "N-DETAIL-FAIL", "lane_id": "user", "type": "end", "order": 13.5, "label": "任务加载失败"},
                {"id": "N-SUCCESS", "lane_id": "user", "type": "end", "order": 14, "label": "查看完成"},
            ],
            "edges": [
                {"from": "N-START", "to": "N-001"},
                {"from": "N-001", "to": "N-SUPER", "label": "是", "path_id": "ALT-001"},
                {"from": "N-001", "to": "N-002", "label": "否"},
                {"from": "N-002", "to": "N-003"},
                {"from": "N-003", "to": "N-004", "label": "是"},
                {"from": "N-003", "to": "N-DENIED", "label": "否", "type": "failure", "path_id": "ALT-004"},
                {"from": "N-004", "to": "N-005"},
                {"from": "N-005", "to": "N-OVERVIEW", "label": "是"},
                {"from": "N-005", "to": "N-FAIL", "label": "否", "type": "failure", "path_id": "ALT-003"},
                {"from": "N-OVERVIEW", "to": "N-DAY-CHECK"},
                {"from": "N-DAY-CHECK", "to": "N-006", "label": "是"},
                {"from": "N-DAY-CHECK", "to": "N-DAY-EMPTY", "label": "否", "path_id": "ALT-002"},
                {"from": "N-006", "to": "N-007"},
                {"from": "N-007", "to": "N-008"},
                {"from": "N-008", "to": "N-009"},
                {"from": "N-009", "to": "N-010", "label": "成功且有数据"},
                {"from": "N-009", "to": "N-DETAIL-EMPTY", "label": "成功但无数据", "path_id": "ALT-002"},
                {"from": "N-009", "to": "N-DETAIL-FAIL", "label": "失败", "type": "failure", "path_id": "ALT-003"},
                {"from": "N-010", "to": "N-SUCCESS"},
            ],
        }
        layout = build_swimlane_layout(model)
        self.assertEqual([], audit_swimlane_layout(layout))
        decision_routes = [edge for edge in layout["edges"] if edge["source_id"] == "N-009"]
        self.assertEqual(3, len({edge["source_port"] for edge in decision_routes}))
        self.assertTrue(any(edge["route_kind"] == "corridor" for edge in layout["edges"]))
        row_centers = layout["row_centers"]
        self.assertGreater(min(right - left for left, right in zip(row_centers, row_centers[1:])), 90)
        source = compile_plantuml_activity({"id": "SC-008", "name": "复杂充值查询", "flow_diagram": model})
        self.assertIn("switch (任务查询结果？)", source)
        self.assertIn(":展示任务明细;", source)


if __name__ == "__main__":
    unittest.main()
