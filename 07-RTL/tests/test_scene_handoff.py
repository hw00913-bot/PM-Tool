import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_codex_host

from loop import __main__ as cli
from loop.codex_host import _prompt
from loop.state import LoopError, pack


class SceneHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_codex_host.CodexHostTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.invoke = self.fixture.invoke
        self.store = self.fixture.store

    def test_case_alias_does_not_allow_data_in_tool_root(self):
        tool = self.fixture.root / "ToolRoot"
        tool.mkdir()
        alias = tool.with_name("TOOLROOT") / "ledger"
        parser = cli.build_parser()
        options = parser.parse_args(["--data", str(alias), "runs"])
        with patch.object(cli, "ROOT", tool), self.assertRaisesRegex(ValueError, "outside Loop source"):
            cli.invoke(options, parser)
        self.assertFalse((tool / "ledger").exists())

    def test_project_style_and_design_result_reach_actual_dispatch_after_takeover(self):
        project = Path(self.store.run("R")["project_root"])
        style = project / "docs" / "design-style.md"
        style.parent.mkdir()
        style_bytes = b"# Style baseline\n\nUse handoff-token-7.\n"
        style.write_bytes(style_bytes)

        design = self.invoke("claim", "R", "A", "--phase", "design", "--epoch", "1")
        loaded = design["style_skill"]
        self.assertEqual(loaded["id"], "prototype-design-style")
        self.assertEqual(loaded["project_style"]["sha256"], hashlib.sha256(style_bytes).hexdigest())
        self.assertIn("handoff-token-7", loaded["content"])
        design_id = design["id"]
        self.invoke("start", "R", design_id, "--worker", "designer", "--epoch", "1")
        with self.assertRaisesRegex(LoopError, "非空的设计交接摘要"):
            self.invoke("submit", "R", design_id, "--actor", "designer", "--epoch", "1")
        self.assertEqual(self.store.attempt(design_id)["state"], "running")
        summary = {"proposal": "scene-design-handoff-721", "blocked": False}
        path = self.fixture.root / "design.json"
        path.write_text(json.dumps(summary))
        self.invoke("submit", "R", design_id, "--actor", "designer", "--result", str(path), "--epoch", "1")

        epoch = self.invoke("takeover", "R", "--epoch", "1")["epoch"]
        status = self.invoke("status", "R")
        recorded = next(item for item in status["attempts"] if item["id"] == design_id)
        self.assertEqual(recorded["result"]["summary"], summary)
        claim = self.invoke("claim", "R", "A", "--epoch", str(epoch))
        self.assertEqual(claim["input"]["design_handoff"], {"attempt_id": design_id, "summary": summary})
        self.invoke("codex-start", "R", claim["id"], "--epoch", str(epoch))
        prompt = self.fixture.host.start_calls[-1][1]
        self.assertIn("handoff-token-7", prompt)
        self.assertIn("Design tokens and states", prompt)
        self.assertIn("scene-design-handoff-721", prompt)
        turn = self.store.operation("R", f"codex-turn:{claim['operation_id']}")["payload"]
        style_receipt = self.store.operation("R", turn["style_skill_operation"])["payload"]
        self.assertEqual(style_receipt["id"], "prototype-design-style")
        self.assertEqual(style_receipt["project_style"]["sha256"], hashlib.sha256(style_bytes).hexdigest())

    def test_removed_feature_brief_carries_removal_checks_without_retired_rules(self):
        active = {"id": "F-A", "scene_id": "S-A", "requirement_ids": ["R-A"], "rule": "current-feature"}
        removed = {
            "id": "F-B", "scene_id": "S-B", "requirement_ids": ["R-B"], "rule": "retired-feature",
            "status": "removed", "cases": ["TC-REMOVE"],
            "assertions": [{"case": "TC-REMOVE", "step": {"op": "absent", "selector": "#old-action"}}],
        }
        plan = {
            "features": [active, removed],
            "requirements": [{"id": "R-A", "rule": "current-clause"}, {"id": "R-B", "rule": "retired-clause"}],
            "scenes": [{"id": "S-A", "outcome": "current-outcome"}, {"id": "S-B", "outcome": "retired-outcome"}],
        }
        attempt = {"id": "attempt", "task_id": "A", "input": {
            "spec": {"feature_ids": ["F-A", "F-B"]}, "phase": "implement", "candidate": "candidate",
        }}
        prompt = _prompt({"baseline": {"requirements": plan}}, attempt, self.fixture.root,
                         "dispatch marker", {"content": "current skill"}, {})
        for value in ("current-feature", "current-clause", "current-outcome", "F-B", "TC-REMOVE", "#old-action"):
            self.assertIn(value, prompt)
        for value in ("retired-feature", "retired-clause", "retired-outcome"):
            self.assertNotIn(value, prompt)

    def test_legacy_completed_codex_design_without_stop_proof_can_be_isolated(self):
        design = self.invoke("claim", "R", "A", "--phase", "design", "--epoch", "1")
        attempt_id = design["id"]
        started = self.invoke("codex-start", "R", attempt_id, "--epoch", "1")
        thread = self.fixture.host.threads[started["thread_id"]]
        thread["turns"][0]["status"] = "completed"
        thread["status"] = {"type": "idle"}
        summary = {"proposal": "historical design"}
        path = self.fixture.root / "design.json"
        path.write_text(json.dumps(summary))
        result = self.invoke("submit", "R", attempt_id, "--actor", started["worker"],
                             "--result", str(path), "--epoch", "1")["result"]
        result["codex_execution"].pop("tool_proof")
        with self.store.transaction() as db:
            db.execute("UPDATE attempts SET result=? WHERE id=?", (pack(result), attempt_id))
        self.fixture.host.tools_drained = False
        with self.assertRaisesRegex(LoopError, "工具正在写入"):
            self.invoke("fence", "R", attempt_id, "--stopped", "--epoch", "1")
        fenced = self.invoke("fence", "R", attempt_id, "--isolate-codex", "retired design", "--epoch", "1")
        self.assertEqual(fenced["state"], "fenced")
        self.assertEqual(fenced["result"]["summary"], summary)
        self.assertFalse(Path(design["workspace"]).exists())
        self.assertTrue(Path(fenced["isolation"]["quarantine"]).is_dir())
