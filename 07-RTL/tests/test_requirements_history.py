from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop import artifacts, delivery, dispatch, requirements, requirements_history, requirements_run, verification
from loop.state import Store, begin_operation, finish_operation


def plan_for(feature_id="FUNC-B02", *, kind="add"):
    return requirements.normalize_plan(
        {
            "approval": "039 试点确认",
            "requirements": [{
                "id": "REQ-VALID", "name": "分类仍可用", "source": "039 冻结范围", "goal": "拦截无效分类",
                "scope": "本租户外呼", "rule": "提交前复核", "acceptance": "无效分类不能产生任务",
            }],
            "scenes": [{
                "id": "SCN-B", "name": "外呼任务创建", "requirement_ids": ["REQ-VALID"], "actor": "外呼管理员",
                "trigger": "提交任务", "preconditions": "已选择分类", "main_flow": ["查询分类", "创建任务"],
                "alternatives": ["停用时拦截"], "outcome": "无效分类不写入",
            }],
            "features": [{
                "id": feature_id, "name": "提交时复核分类", "scene_id": "SCN-B",
                "requirement_ids": ["REQ-VALID"], "rule": "选中后停用的分类不能用于提交", "cases": ["TC-B02"],
            }],
            "tasks": [{
                "id": "TASK-OUTBOUND", "name": "实现提交复核", "feature_ids": [feature_id],
                "input": "已选分类", "deliverable": "分类提交门禁", "done": "独立复验通过",
                "paths": ["index.html"], "cases": ["TC-B02"],
            }],
            "changes": [{
                "feature_id": feature_id, "kind": kind, "before": "" if kind == "add" else "停用后拒绝提交",
                "after": "停用后拒绝提交",
            }],
        },
        ["TC-B02"],
    )


class RequirementsHistoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.project = self.root / "project"
        self.output = self.project / "prototype"
        self.output.mkdir(parents=True)
        (self.output / "index.html").write_text("<html>original</html>", encoding="utf-8")
        self.ledger = self.project / requirements_history.LEDGER
        self.store = self._store("data")
        self.context = {"suite_revision": "s1", "fixture_revision": "f1"}

    def _store(self, name):
        return Store(self.root / name, reservation_root=self.root / "reservations")

    def _run(self, identifier, plan=None, *, store=None):
        store = store or self.store
        baseline = {"revision": identifier, "mandatory_cases": ["TC-B02"]}
        if plan is not None:
            baseline["requirements"] = plan
        return store.create_run(self.project, self.output, baseline, project_id="P", run_id=identifier)

    def _record_change(self, run, operation, *, delivered, structured=False):
        change_id = delivery._change_id(operation)
        receipt = {
            "candidate": "a" * 40, "entry": str(self.output / "index.html"), "approval": None,
            "evidence_ids": ["proof-1"], "change_id": change_id,
        }
        delivery._write_memory(run, operation, receipt)
        with self.store.transaction() as db:
            begin_operation(db, run["id"], operation, "delivery", {})
            if structured:
                requirements_history.record_delivery_history(self.store, db, run, receipt)
            if delivered:
                finish_operation(db, run["id"], operation, receipt)
                db.execute("UPDATE runs SET status='delivered' WHERE id=?", (run["id"],))
        return change_id

    def test_previous_delivery_and_frozen_baseline_survive_a_ledger_change(self):
        first = self._run("R1", plan_for())
        artifacts.initialize_repo(self.store, "R1", 1)
        ready = requirements_run.prepare(self.store, "R1", 1)
        change = self._record_change(first, "delivery-01", delivered=True)
        other = self._store("second")
        snapshot = Path(ready["baseline"])
        content = snapshot.read_bytes()
        snapshot.write_text("{}")
        with self.assertRaisesRegex(ValueError, "无法核对另一份台账"):
            self._run("R2", plan_for(kind="verify"), store=other)
        snapshot.write_bytes(content)
        with self.assertRaisesRegex(ValueError, "显式保留移除"):
            self._run("R2", plan_for("FUNC-NEW"), store=other)
        next_run = self._run("R2", plan_for(kind="verify"), store=other)
        with other.transaction() as db:
            requirements_history.write_ledger(other, db, next_run)
        ledger = self.ledger.read_text(encoding="utf-8")
        self.assertIn(f"[{change}](../memory/changes/{change}.md)", ledger)
        self.assertEqual(ledger.count('<a id="func-b02"></a>'), 1)
        detail = (self.project / f"memory/changes/{change}.md").read_text(encoding="utf-8")
        self.assertIn("[FUNC-B02](../../docs/需求台账.md#func-b02)", detail)

    def test_legacy_migration_tolerates_display_formatting_and_blocks_lost_history(self):
        run = self._run("R1", plan_for())
        artifacts.initialize_repo(self.store, "R1", 1)
        requirements_run.prepare(self.store, "R1", 1)
        change = self._record_change(run, "delivery-01", delivered=True)
        with self.store.transaction() as db:
            requirements_history.write_ledger(self.store, db, self.store.run("R1"))
        index = self.project / "docs/change-log.md"
        original = index.read_text(encoding="utf-8")
        index.write_text(original.replace(" -->\n", " -->  \n"), encoding="utf-8")
        other = self._store("second")
        with other.transaction(immediate=False) as db:
            self.assertEqual(requirements_history.latest_baseline(db, self.project, "P"), run["baseline"])
        index.unlink()
        with self.assertRaisesRegex(ValueError, "未在历史索引登记"):
            self._run("R2", plan_for(kind="verify"), store=other)
        detail = self.project / "memory/changes" / f"{change}.md"
        detail.unlink()
        with self.assertRaisesRegex(ValueError, "已验收历史无法核对"):
            self._run("R2", plan_for(kind="verify"), store=other)
        index.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "变更明细缺失"):
            self._run("R2", plan_for(kind="verify"), store=other)
        self.assertEqual(other.runs(), [])

    def test_structured_history_survives_display_index_changes_and_rejects_lost_index(self):
        run = self._run("R1", plan_for())
        artifacts.initialize_repo(self.store, "R1", 1)
        requirements_run.prepare(self.store, "R1", 1)
        change = self._record_change(run, "delivery-01", delivered=True, structured=True)
        index = self.project / requirements_history.HISTORY_INDEX
        history = index.read_text(encoding="utf-8")
        self.assertIn(str(self.store.path), history)
        (self.project / "docs/change-log.md").unlink()
        other = self._store("second")
        with other.transaction(immediate=False) as db:
            self.assertEqual(requirements_history.latest_baseline(db, self.project, "P"), run["baseline"])
        with self.assertRaisesRegex(ValueError, "显式保留移除"):
            self._run("R2", plan_for("FUNC-NEW"), store=other)
        index.unlink()
        with self.assertRaisesRegex(ValueError, "结构化需求变更历史缺失"):
            self._run("R2", plan_for(kind="verify"), store=other)
        index.write_text(history, encoding="utf-8")
        (self.project / "memory/changes" / f"{change}.md").unlink()
        with self.assertRaisesRegex(ValueError, "变更明细缺失"):
            self._run("R2", plan_for(kind="verify"), store=other)

    def test_origin_receipt_is_required_until_history_commits(self):
        run = self._run("R1", plan_for())
        artifacts.initialize_repo(self.store, "R1", 1)
        requirements_run.prepare(self.store, "R1", 1)
        change = self._record_change(run, "delivery-01", delivered=False, structured=True)
        with self.store.transaction(immediate=False) as db:
            self.assertIsNone(requirements_history.latest_baseline(db, self.project, "P"))
        other = self._store("second")
        with self.assertRaisesRegex(ValueError, f"原运行台账的交付回执 {change}"):
            self._run("R2", plan_for(kind="verify"), store=other)
        self.assertEqual(other.runs(), [])

    def test_reusing_run_id_in_another_ledger_preserves_first_delivery(self):
        run = self._run("R1", plan_for(kind="verify"))
        artifacts.initialize_repo(self.store, "R1", 1)
        requirements_run.prepare(self.store, "R1", 1)
        change = self._record_change(run, "delivery-01", delivered=True, structured=True)
        other = self._store("second")
        self._run("R1", plan_for(kind="verify"), store=other)
        artifacts.initialize_repo(other, "R1", 1)
        requirements_run.prepare(other, "R1", 1)
        self.assertIn(f"[{change}](../memory/changes/{change}.md)", self.ledger.read_text(encoding="utf-8"))

    def test_only_authoritative_deliveries_appear_until_current_finalization(self):
        active = self._run("R1", plan_for())
        artifacts.initialize_repo(self.store, "R1", 1)
        requirements_run.prepare(self.store, "R1", 1)
        pending = self._record_change(active, "delivery-02", delivered=False)
        with self.store.transaction() as db:
            requirements_history.write_ledger(self.store, db, active)
        initial = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn(pending, initial)
        self.assertIn("已冻结，待交付", initial)
        with self.store.transaction() as db:
            finish_operation(db, active["id"], "delivery-02", {
                "change_id": pending, "candidate": "a" * 40, "entry": str(self.output / "index.html"),
                "approval": None, "evidence_ids": ["proof-1"],
            })
            db.execute("UPDATE runs SET status='delivered' WHERE id=?", (active["id"],))
        delivered = self.store.run(active["id"])
        with self.store.transaction() as db:
            requirements_history.write_ledger(self.store, db, delivered)
            requirements_history.write_ledger(self.store, db, delivered)
        final = self.ledger.read_text(encoding="utf-8")
        self.assertEqual(final.count(f"[{pending}]"), 1)
        self.assertIn("本轮状态 **已验收**", final)
        self.assertIn("../memory/baselines/rtl-s1-R1.json", final)

    def test_ledger_refuses_to_replace_manual_documents_and_leaves_legacy_untouched(self):
        self.ledger.parent.mkdir()
        self.ledger.write_text("手工维护", encoding="utf-8")
        legacy = self._run("R0")
        with self.store.transaction() as db:
            self.assertIsNone(requirements_history.write_ledger(self.store, db, legacy))
            db.execute("UPDATE runs SET status='superseded' WHERE id='R0'")
        run = self._run("R1", plan_for())
        with self.store.transaction() as db, self.assertRaisesRegex(ValueError, "人工维护"):
            requirements_history.write_ledger(self.store, db, run)
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), "手工维护")

    def _land_for_publication(self, plan=None):
        run = self._run("R1", plan_for() if plan is None else plan)
        run_id = run["id"]
        artifacts.initialize_repo(self.store, run_id, 1)
        requirements_run.prepare(self.store, run_id, 1)
        attempt = dispatch.reserve(self.store, run_id, 1, "TASK-OUTBOUND")["id"]
        worktree = artifacts.make_worktree(self.store, run_id, attempt)
        dispatch.bind_workspace(self.store, run_id, 1, attempt, worktree)
        dispatch.start(self.store, run_id, 1, attempt, "author")
        (worktree / "index.html").write_text("<html>verified</html>", encoding="utf-8")
        artifacts.git(worktree, "add", "index.html")
        artifacts.git(worktree, "commit", "--quiet", "-m", "Implement B02")
        result = artifacts.capture_submission(self.store, run_id, attempt, "author")
        cases = {"TC-B02": {"passed": True, "actual": "无效分类未产生任务"}}
        verification.record(
            self.store, run_id, 1, "self", result["commit"], "author", cases,
            {**self.context, "attempt_id": attempt},
        )
        dispatch.submit(self.store, run_id, 1, attempt, result)
        staged = artifacts.stage(self.store, run_id, 1, [attempt])
        artifacts.promote(self.store, run_id, 1, staged["operation_id"])
        landed = delivery.land(self.store, run_id, 1, allow_replace=True)
        verification.record(
            self.store, run_id, 1, "delivery", landed["candidate"], "reviewer", cases,
            {**self.context, "operation_id": landed["operation_id"], "path": landed["entry"],
             "url": landed["url"], "browser": "TestBrowser"},
        )

        return run_id, landed

    def test_interrupted_commit_does_not_publish_accepted_human_history(self):
        run_id, landed = self._land_for_publication()
        change = delivery._change_id(landed["operation_id"])
        detail = self.project / f"memory/changes/{change}.md"
        index = self.project / "docs/change-log.md"
        with patch.object(delivery, "finish_operation", side_effect=RuntimeError("interrupted commit")):
            with self.assertRaisesRegex(RuntimeError, "interrupted commit"):
                delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(self.store.run(run_id)["status"], "active")
        self.assertIn("已冻结，待交付", self.ledger.read_text(encoding="utf-8"))
        self.assertNotIn(change, self.ledger.read_text(encoding="utf-8"))
        self.assertFalse(detail.exists())
        self.assertFalse(index.exists())
        with self.assertRaisesRegex(ValueError, "验收已部分回写"):
            delivery.cancel_acceptance(self.store, run_id, 1, landed["operation_id"], "cancel")
        receipt = delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(receipt["change_id"], change)
        self.assertEqual(self.ledger.read_text(encoding="utf-8").count(f"[{change}]"), 1)
        self.assertIn("本轮状态 **已验收**", self.ledger.read_text(encoding="utf-8"))
        self.assertIn("[FUNC-B02](../../docs/需求台账.md#func-b02)", detail.read_text(encoding="utf-8"))

    def test_terminal_replay_repairs_current_snapshot_change_and_ledger(self):
        run_id, landed = self._land_for_publication()
        with patch.object(requirements_history, "write_ledger", side_effect=RuntimeError("interrupted publication")):
            with self.assertRaisesRegex(RuntimeError, "interrupted publication"):
                delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(self.store.run(run_id)["status"], "delivered")
        self.assertIn("已冻结，待交付", self.ledger.read_text(encoding="utf-8"))
        change = delivery._change_id(landed["operation_id"])
        detail = self.project / f"memory/changes/{change}.md"
        self.assertTrue(detail.exists())
        snapshot = self.project / requirements_run.snapshot_relative_path(run_id)
        for path in (self.ledger, detail, snapshot, self.project / "docs/change-log.md"):
            path.unlink()
        receipt = delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(receipt, delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master"))
        self.assertEqual(receipt["change_id"], change)
        self.assertEqual(self.ledger.read_text(encoding="utf-8").count(f"[{change}]"), 1)
        self.assertTrue(snapshot.is_file())
        self.assertIn("本轮状态 **已验收**", self.ledger.read_text(encoding="utf-8"))
        self.assertIn("[FUNC-B02](../../docs/需求台账.md#func-b02)", detail.read_text(encoding="utf-8"))

    def test_prepare_and_terminal_replay_refresh_old_removed_rule_display(self):
        plan = plan_for()
        plan["requirements"][0]["rule"] = "旧条款只供历史核对"
        plan["scenes"][0]["outcome"] = "旧场景只供历史核对"
        plan["features"][0].update(status="removed", rule="旧功能规则只供历史核对", assertions=[{
            "case": "TC-B02", "step": {"op": "hidden", "selector": "#retired-action"},
        }])
        plan["changes"][0].update(kind="remove", before="原确认的入口", after="入口已移除")
        plan = requirements.normalize_plan(plan, ["TC-B02"])
        run_id, landed = self._land_for_publication(plan)
        snapshot = self.project / requirements_run.snapshot_relative_path(run_id)
        frozen_bytes = snapshot.read_bytes()
        active_ledger = self.ledger.read_text(encoding="utf-8")
        self.assertIn("本轮移除验证", active_ledger)
        self.assertIn("#retired-action", active_ledger)
        self.assertNotIn("只供历史核对", active_ledger)

        def restore_legacy_display(content):
            old = content.replace("## 需求分析\n", "## 需求分析\n\n旧条款只供历史核对\n", 1)
            self.ledger.write_text(old, encoding="utf-8")

        restore_legacy_display(active_ledger)
        with self.assertRaisesRegex(ValueError, "需求台账.*prepare"):
            requirements_run.check(self.store, run_id)
        requirements_run.prepare(self.store, run_id, 1)
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), active_ledger)

        receipt = delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        delivered_ledger = self.ledger.read_text(encoding="utf-8")
        change = self.project / f"memory/changes/{receipt['change_id']}.md"
        historical = change.read_bytes(), change.stat().st_mtime_ns
        self.assertIn("[FUNC-B02](../../docs/需求台账.md#func-b02)", change.read_text(encoding="utf-8"))
        self.assertEqual(delivered_ledger.count('<a id="func-b02"></a>'), 1)
        restore_legacy_display(delivered_ledger)
        self.assertEqual(delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master"), receipt)
        self.assertEqual(self.ledger.read_text(encoding="utf-8"), delivered_ledger)
        self.assertEqual((change.read_bytes(), change.stat().st_mtime_ns), historical)
        self.assertEqual(snapshot.read_bytes(), frozen_bytes)

    def test_publication_rechecks_the_landed_manifest_after_writing_metadata(self):
        run_id, landed = self._land_for_publication()
        write_ledger = requirements_history.write_ledger
        added = self.output / "unexpected.txt"

        def write_with_drift(*args):
            write_ledger(*args)
            added.write_text("output changed during publication", encoding="utf-8")

        with patch.object(requirements_history, "write_ledger", side_effect=write_with_drift):
            with self.assertRaisesRegex(ValueError, "实际落盘内容与候选清单不符"):
                delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        added.unlink()
        receipt = delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(receipt["status"], "delivered")
        self.assertEqual(delivery.manifest(self.output), self.store.operation(
            run_id, landed["operation_id"],
        )["payload"]["manifest"])

    def test_old_delivery_cannot_rewrite_another_ledgers_active_plan(self):
        run_id, landed = self._land_for_publication()
        delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        other = self._store("second")
        self._run("R2", plan_for(kind="verify"), store=other)
        artifacts.initialize_repo(other, "R2", 1)
        requirements_run.prepare(other, "R2", 1)
        before = self.ledger.read_bytes()
        with self.assertRaisesRegex(ValueError, "reserved by active run R2"):
            delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_old_delivery_cannot_rewrite_a_newer_delivered_ledger_with_same_assets(self):
        run_id, landed = self._land_for_publication()
        delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        later = self._run("R2", plan_for(kind="verify"))
        artifacts.initialize_repo(self.store, "R2", 1)
        requirements_run.prepare(self.store, "R2", 1)
        self._record_change(later, "delivery-ab", delivered=True, structured=True)
        with self.store.transaction() as db:
            committed = {**later, "status": "delivered"}
            requirements_history.write_ledger(self.store, db, committed)
        before = self.ledger.read_bytes()
        with self.assertRaisesRegex(ValueError, "只可恢复项目最新"):
            delivery.finalize(self.store, run_id, 1, landed["operation_id"], "master")
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_normative_change_columns_come_from_frozen_contract_diff(self):
        run = self._run("R1", plan_for())
        run["baseline"]["normative_changes"] = {
            "FUNC-B02": {"before": "旧契约", "after": "新契约"},
        }
        content = requirements_history.change_section(run, "CHG-01")
        table, notes = content.split("### S1 计划说明", 1)
        self.assertIn("| 旧契约 | 新契约 |", table)
        self.assertNotIn("停用后拒绝提交", table)
        self.assertIn("停用后拒绝提交", notes)
