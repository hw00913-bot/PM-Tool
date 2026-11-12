from functools import partial
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop import __main__ as cli
from loop import artifacts, verification
from loop.skills import read_skill
from loop.state import Store


PROJECT = Path(__file__).resolve().parents[1]


class SkillReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.write_skill(self.root / "loop-control" / "SKILL.md", "loop-control", "Use the dispatch guide.")
        self.pack = self.root / "references" / "workflow" / "SKILL.md"
        self.sections = {
            "Shared brief": "Send the exact candidate to the next role.\n\n### Handoff fields\n\nInclude the task ID.",
            "Master review": "Check the requirement-to-task trace.",
            "Repair": "Save the original failing observation.",
            "Contract pair": "Verify the provider and consumer on the same candidate.",
            "Analysis": "Attach an evidence location to each requirement.",
            "Task planning": "Assign an owner for each shared path.",
            "Unused": "A distinct unrelated reference section.",
        }
        self.write_pack()
        self.registry = self.root / "registry.json"
        self.registry.write_text(json.dumps({"skills": [
            {
                "id": "loop-control",
                "path": "loop-control/SKILL.md",
                "roles": ["master", "architect"],
                "stages": ["S1", "S2"],
                "references": [
                    {"ref": "workflow#shared-brief", "stages": ["S1", "S2"]},
                    {"ref": "workflow#master-review", "stages": ["S1"], "roles": ["master"]},
                    {"ref": "workflow#repair", "stages": ["S2"], "purposes": ["repair"]},
                    {"ref": "workflow#contract-pair", "stages": ["S2"], "requires_edges": True},
                ],
            },
            {
                "id": "requirement-architecture",
                "path": "requirement-architecture/SKILL.md",
                "roles": ["architect"],
                "stages": ["S1"],
                "references": [
                    {"ref": "workflow#analysis", "stages": ["S1"], "steps": ["analysis"]},
                    {"ref": "workflow#task-planning", "stages": ["S1"], "steps": ["tasks"]},
                ],
            },
        ]}), encoding="utf-8")
        architect = self.root / "requirement-architecture"
        self.write_skill(architect / "SKILL.md", "requirement-architecture", "Use the S1 step guide.")
        (architect / "references").mkdir()
        (architect / "references" / "analysis.md").write_text("Analyze the source material.\n", encoding="utf-8")
        (architect / "references" / "tasks.md").write_text("Split and allocate tasks.\n", encoding="utf-8")

    @staticmethod
    def write_skill(path, name, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\ndescription: A reference fixture.\n---\n\n{body}\n", encoding="utf-8")

    def write_pack(self):
        body = "\n\n".join(f"## {title}\n\n{text}" for title, text in self.sections.items())
        self.write_skill(self.pack, "workflow", body)

    def load(self, stage="S1", role="master", **kwargs):
        return read_skill("loop-control", role, stage, self.root, **kwargs)

    @staticmethod
    def selected(result):
        return {entry["ref"] for entry in result["references"]}

    def test_returns_only_selected_sections_as_executable_context(self):
        result = self.load()
        self.assertEqual(self.selected(result), {"workflow#shared-brief", "workflow#master-review"})
        self.assertIn("Use the dispatch guide.", result["content"])
        for name in ("Shared brief", "Master review"):
            self.assertEqual(result["content"].count(self.sections[name]), 1)
        for name in ("Repair", "Contract pair", "Unused"):
            self.assertNotIn(self.sections[name], result["content"])
        self.assertEqual(Path(result["source_path"]), self.root / "loop-control" / "SKILL.md")
        for item in result["references"]:
            self.assertEqual(Path(item["path"]), self.pack)
            self.assertEqual(item["section"], item["ref"].split("#", 1)[1])

    def test_role_stage_purpose_and_actual_task_edges_filter_references(self):
        architect = self.load(role="architect")
        self.assertEqual(self.selected(architect), {"workflow#shared-brief"})
        no_task = self.load(stage="S2", purpose="repair")
        self.assertEqual(self.selected(no_task), {"workflow#shared-brief", "workflow#repair"})
        task_a = self.load(stage="S2", task={"id": "A", "edges": ["category-query"]})
        task_c = self.load(stage="S2", task={"id": "C", "edges": []})
        self.assertEqual(self.selected(task_a), {"workflow#shared-brief", "workflow#contract-pair"})
        self.assertEqual(self.selected(task_c), {"workflow#shared-brief"})
        self.assertNotIn(self.sections["Contract pair"], task_c["content"])

    def test_s1_loads_step_specific_reference_and_preserves_role_permissions(self):
        analysis = read_skill("requirement-architecture", "architect", "S1", self.root, step="analysis")
        tasks = read_skill("requirement-architecture", "architect", "S1", self.root, step="tasks")
        self.assertEqual(self.selected(analysis), {"workflow#analysis"})
        self.assertEqual(self.selected(tasks), {"workflow#task-planning"})
        self.assertIn("Analyze the source material.", analysis["content"])
        self.assertIn(self.sections["Analysis"], analysis["content"])
        self.assertNotIn(self.sections["Task planning"], analysis["content"])
        self.assertEqual(Path(analysis["source_path"]),
                         self.root / "requirement-architecture" / "references" / "analysis.md")
        with self.assertRaisesRegex(ValueError, "does not permit"):
            read_skill("requirement-architecture", "master", "S1", self.root, step="tasks")
        with self.assertRaisesRegex(ValueError, "not registered"):
            read_skill("workflow", "master", "S1", self.root)

    def test_reference_refreshes_on_next_invocation_and_changes_digest(self):
        first = self.load()
        self.sections["Master review"] = "Check the requirement-to-case trace before dispatch."
        self.write_pack()
        second = self.load()
        self.assertIn("Check the requirement-to-task trace.", first["content"])
        self.assertNotIn("Check the requirement-to-task trace.", second["content"])
        self.assertIn(self.sections["Master review"], second["content"])
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertEqual(second["digest"], self.load()["digest"])

    def test_referenced_heading_renamed_or_file_removed_fails_closed(self):
        self.load()
        self.sections["Renamed review"] = self.sections.pop("Master review")
        self.write_pack()
        with self.assertRaises(ValueError):
            self.load()
        self.pack.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.load()

    def test_reference_pack_cannot_escape_the_registry_root(self):
        outside = self.root.parent / f"{self.root.name}-outside.md"
        self.addCleanup(outside.unlink, missing_ok=True)
        self.pack.rename(outside)
        self.pack.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.load()


class SkillReferenceCLITests(unittest.TestCase):
    def test_public_s1_call_returns_real_registered_reference_body(self):
        command = subprocess.run(
            [sys.executable, "-m", "loop", "skill", "requirement-architecture",
             "--role", "architect", "--stage", "S1", "--step", "analysis"],
            cwd=PROJECT, capture_output=True, text=True, check=True, timeout=10,
        )
        result = json.loads(command.stdout)
        actual = PROJECT / "skills" / "references" / "requirements-workflows" / "SKILL.md"
        metadata = next(item for item in result["references"] if item["ref"] == "requirements-workflows#extract-requirements")
        self.assertEqual(Path(metadata["path"]), actual)
        source = actual.read_text(encoding="utf-8")
        body = source.split("\n## Extract requirements\n", 1)[1].split("\n## ", 1)[0].strip()
        self.assertIn(body, result["content"])

    def task_run(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        project = root / "project"
        prototype = project / "prototype"
        prototype.mkdir(parents=True)
        (prototype / "index.html").write_text("<title>Skill fixture</title>", encoding="utf-8")
        store = Store(root / "data", reservation_root=root / "reservations")
        run = store.create_run(project, root / "output", {}, run_id="run")
        for task, edges in (("A", ["category-query"]), ("B", ["category-query"]), ("C", [])):
            store.add_task(run["id"], run["epoch"], {
                "id": task, "paths": [f"{task}.html"], "cases": [f"TC-{task}"], "edges": edges,
            })
        return store, run

    @staticmethod
    def invoke(store, *args):
        parser = cli.build_parser()
        options = parser.parse_args(["--data", str(store.data_root), *args])
        with patch.object(cli, "Store", return_value=store):
            return cli.invoke(options, parser)

    def assert_task_references(self, task, packet):
        self.assertEqual(packet["task_id"], task)
        references = {item["ref"] for item in packet["references"]}
        self.assertIn("superpowers-workflows#implementer-brief", references)
        shared_contract = "engineering-verification#consumer-mock-and-provider-verification"
        if task in {"A", "B"}:
            self.assertIn(shared_contract, references)
            self.assertIn("## Consumer mock and provider verification", packet["content"])
        else:
            self.assertNotIn(shared_contract, references)
            self.assertNotIn("## Consumer mock and provider verification", packet["content"])

    def test_public_task_context_loads_contract_reference_only_for_edge_tasks(self):
        store, run = self.task_run()

        def load(task):
            return self.invoke(
                store, "skill", "scene-delivery", "--role", "scene", "--stage", "S2",
                "--run", run["id"], "--epoch", str(run["epoch"]), "--task", task,
            )

        for task in ("A", "B", "C"):
            with self.subTest(task=task):
                self.assert_task_references(task, load(task))
        with self.assertRaisesRegex(ValueError, "unknown task"):
            load("unknown")

    def test_statechart_is_a_separate_local_entry_for_execution_audits(self):
        expected = PROJECT / "skills" / "statechart" / "SKILL.md"
        for role, stage in (("master", "S2"), ("integration", "S3"), ("verifier", "S4")):
            with self.subTest(role=role, stage=stage):
                result = read_skill("statechart", role, stage)
                self.assertEqual(Path(result["source_path"]), expected)
                self.assertIn("TLA+", result["content"])
        with self.assertRaisesRegex(ValueError, "does not permit"):
            read_skill("statechart", "master", "S1")
        with self.assertRaisesRegex(ValueError, "does not permit"):
            read_skill("statechart", "scene", "S2")

    def test_paused_run_records_statechart_load_without_resuming_dispatch(self):
        store, run = self.task_run()
        run_id, epoch = run["id"], str(run["epoch"])
        pause = self.invoke(store, "pause-run", run_id, "--reason", "inspect recovery order", "--epoch", epoch)
        packet = self.invoke(
            store, "skill", "statechart", "--role", "master", "--stage", "S2",
            "--run", run_id, "--epoch", epoch, "--task", "A",
        )
        self.recorded_skill(store, run_id, packet)
        current = self.invoke(store, "status", run_id)["pause"]
        self.assertEqual(current["pause_id"], pause["pause_id"])
        with self.assertRaisesRegex(ValueError, "已请求暂停"):
            self.invoke(store, "claim", run_id, "A", "--epoch", epoch)

    def recorded_skill(self, store, run_id, packet, *, attempt_id=None):
        operations = self.invoke(store, "status", run_id)["operations"]
        record = next(item for item in operations if item["id"] == packet["operation_id"])
        self.assertEqual(record["kind"], "skill")
        self.assertEqual(record["status"], "done")
        self.assertTrue(record["receipt"]["loaded"])
        payload = record["payload"]
        self.assertNotIn("content", payload)
        for field in ("id", "role", "stage", "source_path", "digest", "references", "task_id"):
            self.assertEqual(payload[field], packet[field])
        for entry in payload["references"]:
            self.assertEqual(set(entry), {"ref", "path", "section", "title", "sha256"})
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
        if attempt_id is not None:
            self.assertEqual(payload["attempt_id"], attempt_id)
        return payload

    def test_run_receipts_keep_each_loaded_reference_identity_without_body(self):
        store, run = self.task_run()
        run_id = run["id"]
        copied_skills = store.data_root.parent / "skills"
        shutil.copytree(PROJECT / "skills", copied_skills)
        pack = copied_skills / "references" / "pstack-workflows" / "SKILL.md"
        marker = "## Bug fix"
        changed_body = "The updated local defect workflow uses the observed entry."
        command = (
            "skill", "prototype-verification", "--role", "verifier", "--stage", "S3",
            "--run", run_id, "--epoch", str(run["epoch"]), "--purpose", "defect", "--task", "A",
        )
        with patch.object(cli, "read_skill", partial(read_skill, root=copied_skills)):
            first = self.invoke(store, *command)
            first_record = self.recorded_skill(store, run_id, first)
            self.assertEqual(first_record["purpose"], "defect")
            old_text = pack.read_text(encoding="utf-8")
            self.assertIn(marker, old_text)
            pack.write_text(old_text.replace(marker, f"{marker}\n\n{changed_body}", 1), encoding="utf-8")
            second = self.invoke(store, *command)
            second_record = self.recorded_skill(store, run_id, second)
            self.assertEqual(second_record["purpose"], "defect")

            def bug_fix_digest(packet):
                return next(entry["sha256"] for entry in packet["references"]
                            if entry["ref"] == "pstack-workflows#bug-fix")

            self.assertIn(changed_body, second["content"])
            self.assertNotIn(changed_body, first["content"])
            self.assertNotEqual(first["operation_id"], second["operation_id"])
            self.assertNotEqual(first["digest"], second["digest"])
            self.assertNotEqual(bug_fix_digest(first_record), bug_fix_digest(second_record))
            self.assertEqual(self.recorded_skill(store, run_id, first), first_record)

            pack.write_text(old_text.replace(marker, "## Renamed defect guide", 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Missing local Skill reference section"):
                self.invoke(store, *command)
            recorded = [item for item in self.invoke(store, "status", run_id)["operations"]
                        if item["kind"] == "skill"]
            self.assertEqual({item["id"] for item in recorded}, {first["operation_id"], second["operation_id"]})

    def test_claim_and_prepare_attempt_include_task_filtered_scene_instructions(self):
        store, run = self.task_run()
        run_id, epoch = run["id"], str(run["epoch"])
        artifacts.initialize_repo(store, run_id, run["epoch"])
        for task in ("A", "C"):
            with self.subTest(task=task):
                claim = self.invoke(store, "claim", run_id, task, "--epoch", epoch, "--phase", "design")
                self.assert_task_references(task, claim["skill"])
                replay = self.invoke(store, "prepare-attempt", run_id, claim["id"], "--epoch", epoch)
                for action in (claim, replay):
                    self.recorded_skill(store, run_id, action["skill"], attempt_id=claim["id"])
                self.assertEqual(
                    {key: value for key, value in replay["skill"].items() if key != "operation_id"},
                    {key: value for key, value in claim["skill"].items() if key != "operation_id"},
                )

    def test_s3_repair_dispatch_retains_its_stage_in_prepare_and_resume(self):
        store, run = self.task_run()
        run_id, epoch = run["id"], str(run["epoch"])
        artifacts.initialize_repo(store, run_id, run["epoch"])
        initial = self.invoke(store, "claim", run_id, "C", "--epoch", epoch)
        self.assertEqual(initial["skill"]["stage"], "S2")
        self.invoke(store, "start", run_id, initial["id"], "--worker", "scene-c", "--epoch", epoch)
        workspace = Path(initial["workspace"])
        (workspace / "C.html").write_text("<p>Layout preference</p>", encoding="utf-8")
        artifacts.git(workspace, "add", "C.html")
        artifacts.git(workspace, "commit", "--quiet", "-m", "Implement scene C")
        submitted = self.invoke(store, "submit", run_id, initial["id"], "--actor", "scene-c", "--epoch", epoch)
        verification.record(
            store, run_id, 1, "self", submitted["result"]["commit"], "scene-c",
            {"TC-C": {"status": "pass", "actual": "Saved layout on the scene candidate"}},
            {"fixture_revision": "f1", "suite_revision": "s1", "attempt_id": initial["id"]},
        )
        staged = self.invoke(store, "stage", run_id, initial["id"], "--epoch", epoch)
        verification.record(
            store, run_id, 1, "integration", staged["candidate"], "independent",
            {"TC-C": {"status": "fail", "actual": "Layout reset after integration"}},
            {"fixture_revision": "f1", "suite_revision": "s1", "operation_id": staged["operation_id"]},
        )
        with self.assertRaisesRegex(ValueError, "TC-C"):
            self.invoke(store, "promote", run_id, staged["operation_id"], "--epoch", epoch)
        self.invoke(store, "fence", run_id, initial["id"], "--epoch", epoch)

        repair = self.invoke(store, "claim", run_id, "C", "--epoch", epoch, "--stage", "S3")
        self.assertNotEqual(repair["id"], initial["id"])
        self.assertEqual(repair["input"]["dispatch_stage"], "S3")
        self.assertEqual(store.operation(run_id, repair["operation_id"])["payload"]["dispatch_stage"], "S3")
        self.assertEqual(repair["skill"]["stage"], "S3")
        self.recorded_skill(store, run_id, repair["skill"], attempt_id=repair["id"])

        prepared = self.invoke(store, "prepare-attempt", run_id, repair["id"], "--epoch", epoch)
        self.assertEqual(prepared["skill"]["stage"], "S3")
        self.recorded_skill(store, run_id, prepared["skill"], attempt_id=repair["id"])

        self.invoke(store, "start", run_id, repair["id"], "--worker", "scene-c", "--epoch", epoch)
        self.invoke(store, "pause", run_id, repair["id"], "--stopped", "--epoch", epoch)
        with self.assertRaisesRegex(ValueError, "S3"):
            self.invoke(store, "claim", run_id, "C", "--epoch", epoch, "--stage", "S2")
        self.assertEqual(store.attempt(repair["id"])["state"], "paused")
        resumed = self.invoke(store, "claim", run_id, "C", "--epoch", epoch)
        self.assertEqual(resumed["id"], repair["id"])
        self.assertEqual(resumed["skill"]["stage"], "S3")
        self.assertEqual(store.operation(run_id, resumed["operation_id"])["payload"]["dispatch_stage"], "S3")
        self.recorded_skill(store, run_id, resumed["skill"], attempt_id=repair["id"])

    def test_prepare_and_resume_legacy_attempts_default_to_s2(self):
        store, run = self.task_run()
        run_id, epoch = run["id"], str(run["epoch"])
        artifacts.initialize_repo(store, run_id, run["epoch"])
        attempt = self.invoke(store, "claim", run_id, "C", "--epoch", epoch)
        with store.transaction() as db:
            old_input = {key: value for key, value in attempt["input"].items() if key != "dispatch_stage"}
            db.execute("UPDATE attempts SET input=? WHERE id=?", (json.dumps(old_input), attempt["id"]))
        prepared = self.invoke(store, "prepare-attempt", run_id, attempt["id"], "--epoch", epoch)
        self.assertEqual(prepared["skill"]["stage"], "S2")
        self.invoke(store, "start", run_id, attempt["id"], "--worker", "scene-c", "--epoch", epoch)
        self.invoke(store, "pause", run_id, attempt["id"], "--stopped", "--epoch", epoch)
        resumed = self.invoke(store, "claim", run_id, "C", "--epoch", epoch)
        self.assertEqual(resumed["id"], attempt["id"])
        self.assertEqual(resumed["skill"]["stage"], "S2")

    def test_claim_with_missing_reference_can_resume_the_same_attempt(self):
        store, run = self.task_run()
        run_id, epoch = run["id"], str(run["epoch"])
        artifacts.initialize_repo(store, run_id, run["epoch"])
        copied_skills = store.data_root.parent / "skills"
        shutil.copytree(PROJECT / "skills", copied_skills)
        pack = copied_skills / "references" / "superpowers-workflows" / "SKILL.md"
        original = pack.read_text(encoding="utf-8")
        pack.write_text(original.replace("## Implementer Brief", "## Renamed brief", 1), encoding="utf-8")

        with patch.object(cli, "read_skill", partial(read_skill, root=copied_skills)):
            with self.assertRaisesRegex(ValueError, "Missing local Skill reference section") as blocked:
                self.invoke(store, "claim", run_id, "C", "--epoch", epoch)
            state = self.invoke(store, "status", run_id)
            attempt, = state["attempts"]
            self.assertIn(f"prepare-attempt {run_id} {attempt['id']} --epoch {epoch}", str(blocked.exception))
            self.assertIsNone(attempt["workspace"])
            self.assertFalse(any(op["kind"] == "skill" for op in state["operations"]))

            pack.write_text(original, encoding="utf-8")
            restored = self.invoke(store, "prepare-attempt", run_id, attempt["id"], "--epoch", epoch)
            self.assertEqual(restored["id"], attempt["id"])
            self.assert_task_references("C", restored["skill"])
            self.recorded_skill(store, run_id, restored["skill"], attempt_id=attempt["id"])

    def test_cli_purpose_adds_only_the_applicable_optional_reference(self):
        def read(purpose):
            command = subprocess.run(
                [sys.executable, "-m", "loop", "skill", "prototype-verification",
                 "--role", "verifier", "--stage", "S4", "--purpose", purpose],
                cwd=PROJECT, capture_output=True, text=True, check=True, timeout=10,
            )
            return {item["ref"] for item in json.loads(command.stdout)["references"]}

        defect = read("defect")
        maintenance = read("maintenance")
        self.assertEqual(defect - maintenance, {"pstack-workflows#bug-fix"})
        self.assertEqual(maintenance - defect, {"pstack-workflows#maintain-verification"})

    def test_cli_task_requires_a_run_context(self):
        command = subprocess.run(
            [sys.executable, "-m", "loop", "skill", "scene-delivery",
             "--role", "scene", "--stage", "S2", "--task", "A"],
            cwd=PROJECT, capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertNotEqual(command.returncode, 0)
        self.assertIn("--run", command.stderr)
