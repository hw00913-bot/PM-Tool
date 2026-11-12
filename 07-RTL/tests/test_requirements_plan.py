import unittest

from loop.requirements import current_work_features, normalize_plan, normalize_plan_task, render_plan


def b02_plan():
    return {
        "approval": "用户确认 S1",
        "requirements": [{
            "id": "REQ-B", "name": "外呼任务提交", "source": "需求评审记录 §2",
            "goal": "当前租户能创建外呼任务", "scope": "原型仅模拟创建",
            "rule": "提交时核对分类归属和启用状态", "acceptance": "停用分类不得产生新任务",
        }],
        "scenes": [{
            "id": "SCN-B", "name": "外呼任务创建", "requirement_ids": ["REQ-B"], "actor": "运营",
            "trigger": "选择分类", "preconditions": "当前租户存在可选分类",
            "main_flow": ["选择有效分类", "提交并读取任务列表"],
            "alternatives": ["分类选中后停用"], "outcome": "创建成功或明确拒绝",
        }],
        "features": [{
            "id": "FUNC-B02", "name": "提交时复核分类", "requirement_ids": ["REQ-B"],
            "scene_id": "SCN-B", "rule": "停用后提交被拒", "cases": ["TC-B02"],
        }],
        "tasks": [{
            "id": "TASK-B", "name": "实现提交复核", "feature_ids": ["FUNC-B02"],
            "input": "分类契约及用例", "deliverable": "外呼页面提交逻辑", "done": "TC-B02 通过",
            "paths": ["src/outbound"], "edges": ["category-query"], "cases": ["TC-B02"], "risk": True,
        }],
        "changes": [{"feature_id": "FUNC-B02", "kind": "add", "after": "新增提交复核"}],
    }


class RequirementsPlanTests(unittest.TestCase):
    def test_validated_plan_can_drive_multiple_tasks_and_render_feature_anchor(self):
        draft = b02_plan()
        draft["requirements"][0]["source"] = "[原始记录](source.md) §2 | 审核"
        second = {**draft["tasks"][0], "id": "TASK-B-REVIEW", "deps": ["TASK-B"]}
        draft["tasks"].append(second)
        plan = normalize_plan(draft, ["TC-B02"])
        draft["features"][0]["cases"].append("TC-DRIFT")
        self.assertEqual(plan["features"][0]["cases"], ["TC-B02"])
        self.assertEqual(plan["tasks"][1]["deps"], ["TASK-B"])
        self.assertEqual(plan["changes"][0]["before"], "")
        ledger = render_plan(plan, "r1")
        self.assertIn('<a id="func-b02"></a>`FUNC-B02`', ledger)
        self.assertIn("TASK-B<br>TASK-B-REVIEW", ledger)
        self.assertIn("[FUNC-B02](#func-b02)", ledger)
        self.assertIn("[原始记录](source.md) §2 \\| 审核", ledger)

    def test_rejects_missing_business_analysis_and_mismatched_scene(self):
        draft = b02_plan()
        del draft["requirements"][0]["acceptance"]
        with self.assertRaisesRegex(ValueError, "REQ-B.*acceptance"):
            normalize_plan(draft, ["TC-B02"])
        draft = b02_plan()
        draft["features"][0]["requirement_ids"] = ["REQ-A"]
        with self.assertRaisesRegex(ValueError, "场景需求.*REQ-A"):
            normalize_plan(draft, ["TC-B02"])

    def test_rejects_orphan_features_and_missing_mandatory_case(self):
        draft = b02_plan()
        with self.assertRaisesRegex(ValueError, "基线必跑.*TC-MISSING"):
            normalize_plan(draft, ["TC-B02", "TC-MISSING"])
        draft["features"].append({**draft["features"][0], "id": "FUNC-B03", "cases": ["TC-B03"]})
        with self.assertRaisesRegex(ValueError, "任务用例覆盖.*FUNC-B03"):
            normalize_plan(draft, ["TC-B02", "TC-B03"])
        draft["tasks"][0]["feature_ids"].append("FUNC-B03")
        with self.assertRaisesRegex(ValueError, "任务用例覆盖.*FUNC-B03"):
            normalize_plan(draft, ["TC-B02", "TC-B03"])

    def test_rejects_unknown_and_cyclic_planned_dependencies(self):
        draft = b02_plan()
        draft["tasks"][0]["deps"] = ["TASK-A"]
        with self.assertRaisesRegex(ValueError, "依赖.*TASK-A"):
            normalize_plan(draft, ["TC-B02"])
        draft["tasks"].append({**draft["tasks"][0], "id": "TASK-A", "deps": ["TASK-B"]})
        with self.assertRaisesRegex(ValueError, "依赖成环"):
            normalize_plan(draft, ["TC-B02"])

    def test_scene_dependencies_use_scene_ids_and_allow_bidirectional_flows(self):
        draft = b02_plan()
        draft["scenes"][0]["depends_on"] = ["SCN-A"]
        with self.assertRaisesRegex(ValueError, "业务依赖.*SCN-A"):
            normalize_plan(draft, ["TC-B02"])
        draft["scenes"][0]["depends_on"] = ["SCN-B"]
        with self.assertRaisesRegex(ValueError, "不能依赖自身"):
            normalize_plan(draft, ["TC-B02"])
        draft["scenes"][0]["depends_on"] = ["SCN-A"]
        draft["scenes"].append({**draft["scenes"][0], "id": "SCN-A", "depends_on": ["SCN-B"]})
        draft["features"].append({**draft["features"][0], "id": "FUNC-A01", "scene_id": "SCN-A", "cases": ["TC-A01"]})
        draft["tasks"][0]["feature_ids"].append("FUNC-A01")
        draft["tasks"][0]["cases"].append("TC-A01")
        plan = normalize_plan(draft, ["TC-B02", "TC-A01"])
        self.assertEqual([scene["depends_on"] for scene in plan["scenes"]], [["SCN-A"], ["SCN-B"]])
        self.assertIn("业务依赖", render_plan(plan, "r1"))

    def test_scene_features_must_cover_requirements_declared_for_that_scene(self):
        draft = b02_plan()
        draft["requirements"].append({**draft["requirements"][0], "id": "REQ-A"})
        draft["scenes"][0]["requirement_ids"].append("REQ-A")
        draft["scenes"].append({**draft["scenes"][0], "id": "SCN-A", "requirement_ids": ["REQ-A"]})
        draft["features"].append({**draft["features"][0], "id": "FUNC-A01", "scene_id": "SCN-A",
                                  "requirement_ids": ["REQ-A"], "cases": ["TC-A01"]})
        with self.assertRaisesRegex(ValueError, "SCN-B 的需求覆盖.*REQ-A"):
            normalize_plan(draft, ["TC-B02", "TC-A01"])

    def test_current_removal_requires_proof_and_historical_removal_keeps_anchor(self):
        draft = b02_plan()
        draft["features"].append({**draft["features"][0], "id": "FUNC-B01", "status": "removed", "cases": []})
        draft["features"].append({**draft["features"][0], "id": "FUNC-B00", "status": "removed", "cases": ["TC-OLD"]})
        draft["changes"].append({"feature_id": "FUNC-B01", "kind": "remove", "before": "旧入口"})
        with self.assertRaisesRegex(ValueError, "本轮移除.*FUNC-B01.*必跑用例"):
            normalize_plan(draft, ["TC-B02"])
        draft["features"][1]["cases"] = ["TC-B01-ABSENT"]
        with self.assertRaisesRegex(ValueError, "任务用例覆盖.*FUNC-B01"):
            normalize_plan(draft, ["TC-B02", "TC-B01-ABSENT"])
        draft["tasks"].append({**draft["tasks"][0], "id": "TASK-REMOVE", "feature_ids": ["FUNC-B01"],
                               "cases": ["TC-B01-ABSENT"]})
        plan = normalize_plan(draft, ["TC-B02", "TC-B01-ABSENT"])
        self.assertEqual(set(current_work_features(plan)), {"FUNC-B01", "FUNC-B02"})
        self.assertIn('<a id="func-b01"></a>', render_plan(plan, "r2"))
        self.assertIn('<a id="func-b00"></a>', render_plan(plan, "r2"))
        task = {**plan["tasks"][0], "feature_ids": ["FUNC-B00"], "cases": ["TC-OLD"]}
        with self.assertRaisesRegex(ValueError, "本轮功能.*FUNC-B00"):
            normalize_plan_task(task, plan)

    def test_ledger_separates_retired_rules_and_current_removal_proof(self):
        draft = b02_plan()
        active_requirement = draft["requirements"][0]
        draft["requirements"].extend((
            {**active_requirement, "id": "REQ-MIXED", "rule": "已退役的同场景条款"},
            {**active_requirement, "id": "REQ-OLD", "rule": "已退役的独立条款"},
        ))
        active_scene = draft["scenes"][0]
        active_scene["requirement_ids"].append("REQ-MIXED")
        draft["scenes"].append({
            **active_scene, "id": "SCN-OLD", "requirement_ids": ["REQ-OLD"], "outcome": "已退役的场景结果",
        })
        active_feature = draft["features"][0]
        draft["features"].extend((
            {**active_feature, "id": "FUNC-REMOVE", "requirement_ids": ["REQ-MIXED"], "status": "removed",
             "rule": "本轮待移除的旧规则", "cases": ["TC-REMOVE"], "assertions": [{
                 "case": "TC-REMOVE", "step": {"op": "hidden", "selector": "#retired-action"},
             }]},
            {**active_feature, "id": "FUNC-OLD", "scene_id": "SCN-OLD", "requirement_ids": ["REQ-OLD"],
             "status": "removed", "rule": "此前已移除的旧规则", "cases": ["TC-OLD"]},
        ))
        draft["changes"].append({"feature_id": "FUNC-REMOVE", "kind": "remove", "before": "退役前入口"})
        draft["tasks"].append({**draft["tasks"][0], "id": "TASK-REMOVE", "feature_ids": ["FUNC-REMOVE"],
                               "cases": ["TC-REMOVE"]})
        plan = normalize_plan(draft, ["TC-B02", "TC-REMOVE"])
        ledger = render_plan(plan, "r2")
        current, _, remainder = ledger.partition("## 本轮移除验证")
        removal, _, history = remainder.partition("## 已移除功能索引")
        self.assertIn("提交时核对分类归属和启用状态", current)
        for stale in ("REQ-MIXED", "REQ-OLD", "SCN-OLD", "已退役的场景结果", "TC-OLD", "本轮待移除的旧规则",
                      "此前已移除的旧规则"):
            self.assertNotIn(stale, ledger)
        self.assertNotIn("TC-REMOVE", current)
        for proof in ('<a id="func-remove"></a>', "TASK-REMOVE", "TC-REMOVE", "#retired-action"):
            self.assertIn(proof, removal)
        self.assertIn('<a id="func-old"></a>', history)
        self.assertEqual(ledger.count('<a id="func-remove"></a>'), 1)
        self.assertIn("[FUNC-REMOVE](#func-remove)", ledger)
        self.assertIn("退役前入口", ledger)

    def test_extra_task_must_reference_frozen_feature_cases(self):
        plan = normalize_plan(b02_plan(), ["TC-B02"])
        extra = {**plan["tasks"][0], "id": "TASK-EXTRA", "deps": ["TASK-OTHER"]}
        self.assertEqual(normalize_plan_task(extra, plan)["deps"], ["TASK-OTHER"])
        extra["cases"] = []
        with self.assertRaisesRegex(ValueError, "至少需要一个.*用例"):
            normalize_plan_task(extra, plan)
        extra["cases"] = ["TC-OTHER"]
        with self.assertRaisesRegex(ValueError, "功能用例.*TC-OTHER"):
            normalize_plan_task(extra, plan)
