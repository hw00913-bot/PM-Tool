"""Support-tool regression only: synthetic files, no browser and no business project."""
import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import test_mermaid_candidate as candidate_fixtures
from package_delivery import collect_files, INCLUDE_DIRS, INCLUDE_RUNTIME

TOOL = candidate_fixtures.TOOL

def module_at(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOL / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MermaidSupportTest(unittest.TestCase):
    def test_bundled_example_preserves_complete_notes_and_real_edges(self):
        example = json.loads((TOOL / "assets/example.graph.json").read_text())
        graph, mmd = module_at("support_build_graph", "build_graph.py").build(example)
        self.assertEqual(graph["source_coverage"]["status"], "not_checked")
        self.assertEqual([(n["id"], n["text"]) for n in graph["nodes"]],
                         [(n["id"], n["text"]) for n in example["nodes"]])
        self.assertEqual([(e["source"], e["target"]) for e in graph["edges"]],
                         [(e["source"], e["target"]) for e in example["edges"]])
        self.assertIn("flowchart TB", mmd)
        self.assertIn("&#124;", module_at("support_build_graph_2", "build_graph.py").label("|", 30))

    def test_static_axure_prepare_keeps_javascript_as_unexecuted_bytes(self):
        inspector = module_at("support_inspect_axure", "inspect_axure.py")
        with tempfile.TemporaryDirectory(prefix="mermaid-static-source-") as tmp:
            base = Path(tmp)
            source, out = base / "source.zip", base / "unpacked"
            script = b'throw new Error("this source must not execute");'
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("demo/data/document.js", script)
                archive.writestr("demo/流程.html", "<p>示例流程原文</p>")
            inspector.prepare(argparse.Namespace(input=source, out=out, max_bytes=100000))
            self.assertEqual((out / "demo/data/document.js").read_bytes(), script)
            with self.assertRaisesRegex(ValueError, "new directory"):
                inspector.prepare(argparse.Namespace(input=source, out=out, max_bytes=100000))
            with zipfile.ZipFile(base / "bad.zip", "w") as archive:
                archive.writestr("../escape.txt", "bad")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                inspector.prepare(argparse.Namespace(input=base / "bad.zip", out=base / "bad-out", max_bytes=100000))
            self.assertFalse((base / "escape.txt").exists())

    def test_candidate_runtime_is_not_packaged_as_a_prototype_input(self):
        with tempfile.TemporaryDirectory(prefix="mermaid-package-scope-") as tmp:
            root = Path(tmp)
            for name in INCLUDE_DIRS:
                (root / name).mkdir(parents=True)
            for name in INCLUDE_RUNTIME:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}")
            candidate = root / "runtime/mermaid-candidates/SC-001/index.html"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("candidate only")
            self.assertNotIn(candidate, collect_files(root))

    def test_dense_multirole_branches_preserve_every_source_edge(self):
        # Reuse the isolated project factory, not any user business materials.
        fixture = candidate_fixtures.MermaidCandidateTest()
        fixture.setUp()
        try:
            model = fixture.detail["flow_diagram"]
            model["lanes"].append({"id": "vendor", "name": "外部渠道", "kind": "external_system"})
            model["nodes"] = [
                {"id": "start", "lane_id": "tenant", "type": "start", "order": 1, "label": "发起"},
                {"id": "decide", "lane_id": "system", "type": "decision", "order": 2, "label": "选择已确认分支"},
                {"id": "end", "lane_id": "tenant", "type": "end", "order": 16, "label": "汇合结束"},
            ]
            model["edges"] = [{"from": "start", "to": "decide"}]
            for i in range(12):
                branch = "branch-" + str(i)
                model["nodes"].append({"id": branch, "lane_id": "vendor" if i % 2 else "system",
                                       "type": "action", "order": 3 + i, "label": "分支原文" + str(i)})
                model["edges"] += [{"from": "decide", "to": branch, "label": "类别=" + str(i)},
                                   {"from": branch, "to": "end"}]
            fixture.detail["alternate_flows"] = []
            fixture.save()
            fixture.candidate()
            graph = json.loads((fixture.out / "graph.json").read_text())
            self.assertEqual(len(graph["nodes"]), 15)
            self.assertEqual(len(graph["edges"]), 25)
            self.assertEqual([(e["original_source"], e["original_target"], e["original_label"]) for e in graph["edges"]],
                             [(e["from"], e["to"], e.get("label", "")) for e in model["edges"]])
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
