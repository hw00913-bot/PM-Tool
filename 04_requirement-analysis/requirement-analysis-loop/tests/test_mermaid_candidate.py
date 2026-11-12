"""Isolated candidate tests: no browser, dependencies, or formal project mutation."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from render_mermaid_candidate import TOOL, create_candidate, json_bytes, prepare_payload, sha
from verify_mermaid_candidate import verify_candidate


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(data))


class MermaidCandidateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mermaid-candidate-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root, self.out = self.base / "project", self.base / "candidate"
        self.detail = {"id": "SC-001", "name": "充值重试流程", "actors": ["租户", "业务系统"], "main_flow": [],
                       "alternate_flows": [{"id": "ALT-001", "condition": "失败后重试"}],
                       "flow_diagram": {"lanes": [{"id": "tenant", "name": "租户", "kind": "actor"}, {"id": "system", "name": "业务系统", "kind": "system"}],
                                        "nodes": [{"id": "开始-N", "lane_id": "tenant", "type": "start", "order": 1, "label": "开始"},
                                                  {"id": "充值-请求", "lane_id": "tenant", "type": "action", "order": 2, "label": "提交充值：" + "中文长标签" * 30 + ' <script> & " | `'},
                                                  {"id": "结果?", "lane_id": "system", "type": "decision", "order": 3, "label": "处理成功？"},
                                                  {"id": "完成", "lane_id": "tenant", "type": "end", "order": 4, "label": "完成"}],
                                        "edges": [{"from": "开始-N", "to": "充值-请求"}, {"from": "充值-请求", "to": "结果?"},
                                                  {"from": "结果?", "to": "完成", "label": "成功"},
                                                  {"from": "结果?", "to": "充值-请求", "label": "失败后重试", "path_id": "ALT-001"}]}}
        self.index = {"scenarios": [{"id": "SC-001", "name": "充值重试流程", "file": "scenarios/SC-001.json"}]}
        self.rules = {"business_rules": [], "business_flows": [{"id": "FLOW-001", "name": "充值", "steps": ["提交", "处理", "返回"], "scenario_ids": ["SC-001"]}]}
        self.save()

    def save(self):
        write(self.root / "scenarios/scenario-index.json", self.index)
        write(self.root / "scenarios/SC-001.json", self.detail)
        write(self.root / "memory/business-rules.json", self.rules)

    def candidate(self, diagram="SC-001", out=None):
        return create_candidate(self.root, diagram, out or self.out, prepare_only=True)

    def test_cycle_mapping_long_labels_and_read_only_prepare(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.candidate()
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        graph = json.loads((self.out / "graph.json").read_text())
        self.assertEqual(len(graph["nodes"]), 4)
        self.assertEqual(len(graph["edges"]), 4)
        self.assertEqual(graph["source_lanes"], self.detail["flow_diagram"]["lanes"])
        self.assertEqual(graph["nodes"][1]["original_label"], self.detail["flow_diagram"]["nodes"][1]["label"])
        self.assertEqual(graph["nodes"][1]["source_lane_name"], "租户")
        self.assertEqual(graph["edges"][-1]["target"], graph["nodes"][1]["id"])
        self.assertEqual(graph["edges"][-1]["path_id"], "ALT-001")
        self.assertEqual(graph["edges"][-1]["original_label"], "失败后重试")
        self.assertIn("普通流程候选，非泳道", graph["title"])
        text = (self.out / "完整流程.mmd").read_text()
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("&#124;", text)
        self.assertIn("&#96;", text)
        result = verify_candidate(self.root, self.out)
        self.assertEqual(result["status"], "prepared_only")
        self.assertFalse(result["render_pass"])
        self.assertFalse(result["formal_delivery"])
        self.assertFalse((self.out / "index.html").exists())
        self.assertFalse((self.out / "verification.json").exists())

    def test_flow_only_existing_step_order_no_branches(self):
        self.candidate("FLOW-001")
        graph = json.loads((self.out / "graph.json").read_text())
        self.assertEqual([n["text"] for n in graph["nodes"]], ["提交", "处理", "返回"])
        self.assertEqual([(e["source"], e["target"], e["text"]) for e in graph["edges"]], [("N0001", "N0002", ""), ("N0002", "N0003", "")])
        self.assertTrue(all(n["kind"] == "process" for n in graph["nodes"]))
        self.assertFalse(verify_candidate(self.root, self.out)["render_pass"])

    def test_duplicate_ids_and_duplicate_json_keys_rejected(self):
        self.index["scenarios"].append(copy.deepcopy(self.index["scenarios"][0]))
        self.save()
        with self.assertRaisesRegex(ValueError, "重复 ID"):
            self.candidate()
        self.index["scenarios"].pop()
        self.save()
        self.detail["flow_diagram"]["nodes"].append(copy.deepcopy(self.detail["flow_diagram"]["nodes"][1]))
        self.save()
        with self.assertRaisesRegex(ValueError, "重复"):
            self.candidate()
        self.detail["flow_diagram"]["nodes"].pop()
        self.save()
        (self.root / "memory/business-rules.json").write_text('{"business_flows": [], "business_flows": []}')
        with self.assertRaisesRegex(ValueError, "重复字段"):
            self.candidate("FLOW-001")
        self.assertFalse(self.out.exists())

    def test_index_path_traversal_absolute_and_symlink_escape(self):
        outside = self.base / "outside.json"
        write(outside, self.detail)
        for path in ("../outside.json", str(outside), "scenarios/../../outside.json", "scenarios\\outside.json"):
            with self.subTest(path=path):
                self.index["scenarios"][0]["file"] = path
                self.save()
                with self.assertRaises(ValueError):
                    self.candidate()
        (self.root / "scenarios/link.json").symlink_to(outside)
        self.index["scenarios"][0]["file"] = "scenarios/link.json"
        self.save()
        with self.assertRaisesRegex(ValueError, "越界"):
            self.candidate()
        self.assertFalse(self.out.exists())

    def test_output_must_be_new_and_only_runtime_inside_project(self):
        for directory in ("outputs/candidate", "deliverables/candidate", "memory/candidate", "scenarios/candidate", "runtime/other", "runtime/mermaid-candidates"):
            with self.subTest(directory=directory), self.assertRaisesRegex(ValueError, "只允许"):
                self.candidate(out=self.root / directory)
        good = self.root / "runtime/mermaid-candidates/test-one"
        self.candidate(out=good)
        initial = (good / "graph.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "必须全新"):
            self.candidate(out=good)
        self.assertEqual((good / "graph.json").read_bytes(), initial)
        (self.root / "runtime/mermaid-candidates/link").symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.candidate(out=self.root / "runtime/mermaid-candidates/link/new")

    def test_stale_source_and_removed_source_binding_rejected(self):
        self.candidate()
        self.detail["name"] = "来源已改"
        self.save()
        with self.assertRaisesRegex(ValueError, "来源内容已变化"):
            verify_candidate(self.root, self.out)
        self.detail["name"] = "充值重试流程"
        self.save()
        path = self.out / "candidate-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["source_files"].pop("scenarios/scenario-index.json")
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, "来源内容已变化"):
            verify_candidate(self.root, self.out)

    def test_derived_tamper_even_with_updated_hash_rejected(self):
        self.candidate()
        path = self.out / "graph.json"
        value = json.loads(path.read_text())
        value["nodes"][0]["text"] = "篡改"
        write(path, value)
        with self.assertRaisesRegex(ValueError, "摘要不匹配"):
            verify_candidate(self.root, self.out)
        manifest_path = self.out / "candidate-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["graph.json"] = sha(path.read_bytes())
        write(manifest_path, manifest)
        with self.assertRaisesRegex(ValueError, "不再对应来源事实"):
            verify_candidate(self.root, self.out)

    def test_no_false_render_pass_or_unbound_html(self):
        self.candidate()
        path = self.out / "candidate-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["render"]["status"] = "passed"
        manifest["browser"]["status"] = "passed"
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, "缺少真实渲染记录"):
            verify_candidate(self.root, self.out)
        manifest["render"]["status"] = manifest["browser"]["status"] = "not_run"
        write(path, manifest)
        (self.out / "index.html").write_text("not-rendered")
        with self.assertRaisesRegex(ValueError, "未绑定渲染产物"):
            verify_candidate(self.root, self.out)

    def test_duplicate_flow_and_explicit_edge_ids(self):
        self.rules["business_flows"].append(copy.deepcopy(self.rules["business_flows"][0]))
        self.save()
        with self.assertRaisesRegex(ValueError, "重复 ID"):
            self.candidate("FLOW-001")
        self.rules["business_flows"].pop()
        for edge in self.detail["flow_diagram"]["edges"][:2]:
            edge["id"] = "same-edge"
        self.save()
        with self.assertRaisesRegex(ValueError, "重复或非法连线 ID"):
            self.candidate()

    def test_snapshot_mermaid_and_artifact_symlink_tamper(self):
        self.candidate()
        for name in ("完整流程.mmd", "source-snapshot.json"):
            path = self.out / name
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "摘要不匹配"):
                verify_candidate(self.root, self.out)
            path.write_bytes(original)
        other = self.base / "other-mmd"
        path = self.out / "完整流程.mmd"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "普通文件"):
            verify_candidate(self.root, self.out)

    def test_blocked_render_cannot_be_promoted_without_geometry_pass(self):
        self.candidate()
        # Deliberately synthetic negative evidence; no browser is run in this test.
        report = {"contract": "mermaid-candidate-verification@1", "diagram_id": "SC-001", "opened_via": "file://", "status": "blocked", "formal_delivery": False, "failed": ["geometry"], "checks": [{"name": "geometry", "ok": False}],
                  "geometry": {"status": "blocked", "blockers": ["synthetic-blocker"]}, "source_review": "manual_required", "business_acceptance": "not_claimed", "visual_review": "not_performed"}
        write(self.out / "verification.json", report)
        path = self.out / "candidate-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["render"]["status"] = "blocked"
        manifest["browser"]["status"] = "failed"
        manifest["rendered_files"] = {"verification.json": sha((self.out / "verification.json").read_bytes())}
        write(path, manifest)
        result = verify_candidate(self.root, self.out)
        self.assertEqual(result["status"], "candidate_render_blocked")
        self.assertFalse(result["render_pass"])
        for name in ("index.html", "完整流程.svg"):
            (self.out / name).write_text("synthetic-negative-fixture")
            manifest["rendered_files"][name] = sha((self.out / name).read_bytes())
        manifest["render"]["status"] = manifest["browser"]["status"] = "passed"
        write(path, manifest)
        with self.assertRaisesRegex(ValueError, "渲染报告未通过"):
            verify_candidate(self.root, self.out)

    def test_only_target_type_sources_are_required_and_bound(self):
        self.index["scenarios"].append({"id": "SC-002", "name": "尚未生成", "file": "scenarios/SC-002.json"})
        self.save()
        self.candidate()
        manifest = json.loads((self.out / "candidate-manifest.json").read_text())
        self.assertEqual(set(manifest["source_files"]), {"scenarios/scenario-index.json", "scenarios/SC-001.json"})
        self.rules["business_flows"][0]["name"] = "与此场景无关的变更"
        self.save()
        self.assertEqual(verify_candidate(self.root, self.out)["status"], "prepared_only")
        (self.root / "memory/business-rules.json").unlink()
        self.assertEqual(verify_candidate(self.root, self.out)["status"], "prepared_only")
        self.save()
        flow_out = self.base / "flow-candidate"
        self.candidate("FLOW-001", flow_out)
        flow_manifest = json.loads((flow_out / "candidate-manifest.json").read_text())
        self.assertEqual(set(flow_manifest["source_files"]), {"memory/business-rules.json"})
        (self.root / "scenarios/scenario-index.json").unlink()
        (self.root / "scenarios/SC-001.json").unlink()
        self.assertEqual(verify_candidate(self.root, flow_out)["status"], "prepared_only")

    def test_short_ids_and_unrelated_unsafe_index_path_rejected(self):
        for diagram in ("SC-1", "FLOW-12", "SC-１２３"):
            with self.subTest(diagram=diagram), self.assertRaisesRegex(ValueError, "至少三位"):
                self.candidate(diagram)
        self.index["scenarios"].append({"id": "SC-002", "name": "非法路径", "file": "../outside.json"})
        self.save()
        with self.assertRaisesRegex(ValueError, "越界"):
            self.candidate()

    def test_report_wrong_diagram_contract_open_mode_and_geometry_rejected(self):
        self.candidate()
        # Synthetic rejection fixtures only, not a claim that a browser ran.
        base_report = {"contract": "mermaid-candidate-verification@1", "diagram_id": "SC-001", "opened_via": "file://", "status": "passed", "formal_delivery": False,
                       "failed": [], "checks": [{"name": "synthetic-check", "ok": True}], "geometry": {"status": "pass", "blockers": []},
                       "source_review": "manual_required", "business_acceptance": "not_claimed", "visual_review": "not_performed"}
        path = self.out / "candidate-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["render"]["status"] = manifest["browser"]["status"] = "passed"
        manifest["rendered_files"] = {}
        for name in ("index.html", "完整流程.svg"):
            (self.out / name).write_text("synthetic-negative-fixture")
            manifest["rendered_files"][name] = sha((self.out / name).read_bytes())
        for field, value in (("contract", "other@1"), ("diagram_id", "SC-999"), ("opened_via", "http://"), ("geometry", {"status": "pass", "blockers": ["unresolved"]}), ("visual_review", "passed")):
            report = copy.deepcopy(base_report)
            report[field] = value
            write(self.out / "verification.json", report)
            manifest["rendered_files"]["verification.json"] = sha((self.out / "verification.json").read_bytes())
            write(path, manifest)
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_candidate(self.root, self.out)

    def test_nonfinite_positions_and_note_number_type(self):
        spec = importlib.util.spec_from_file_location("test_candidate_build", TOOL / "scripts/build_graph.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        graph = {"title": "test", "nodes": [{"id": "N1", "kind": "note", "text": "说明", "source_ids": ["s1"]}], "edges": []}
        for field, value in (("x", "<img>"), ("y", True), ("w", float("inf")), ("h", float("nan")), ("note_no", {"bad": "value"})):
            bad = copy.deepcopy(graph)
            bad["nodes"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                module.build(bad)
        graph["nodes"][0]["note_no"] = '<span onclick="bad">R1</span>'
        _, mmd = module.build(graph)
        self.assertNotIn("<span", mmd)
        self.assertIn("&lt;span", mmd)

    def test_cli_prepare_and_verify_without_browser(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "render_mermaid_candidate.py"), str(self.root), "--diagram-id", "SC-001", "--out", str(self.out), "--prepare-only", "--node-command", "intentionally-unavailable"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["browser"], "not_run")
        verify = subprocess.run([sys.executable, str(SCRIPTS / "verify_mermaid_candidate.py"), str(self.root), str(self.out)], capture_output=True, text=True)
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        self.assertFalse(json.loads(verify.stdout)["render_pass"])


if __name__ == "__main__":
    unittest.main()
