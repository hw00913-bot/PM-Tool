import unittest

from loop import analysis_mapping


class AnalysisMappingTests(unittest.TestCase):
    def setUp(self):
        entrypoints = {
            "normalized_user_stories": "memory/stories.json", "scenario_index": "scenarios/index.json",
            "feature_list": "memory/features.json", "page_index": "planning/pages.json",
            "interface_index": "interfaces/index.json",
        }
        self.bundle = {
            "binding": {"manifest_sha256": "a" * 64, "baseline_id": "04-current"},
            "facts": {
                "handoff": {"entrypoints": {key: {"path": path} for key, path in entrypoints.items()}},
                "payloads": {
                    entrypoints["normalized_user_stories"]: {"user_stories": [
                        {"id": "US-001", "title": "提交任务", "goal": "创建任务", "acceptance_criteria": [
                            {"id": "AC-001", "statement": "返回创建结果"},
                        ]},
                    ]},
                    entrypoints["scenario_index"]: {"scenarios": [
                        {"id": "SC-001", "name": "页面提交", "file": "scenarios/SC-001.json", "ui_surface": "required"},
                        {"id": "SC-002", "name": "后台处理", "file": "scenarios/SC-002.json", "ui_surface": "not_required"},
                    ]},
                    "scenarios/SC-001.json": {"id": "SC-001", "trigger": "点击提交"},
                    "scenarios/SC-002.json": {"id": "SC-002", "trigger": "事件触发"},
                    entrypoints["feature_list"]: {
                        "features": [
                            {"id": "FUNC-001", "name": "提交任务", "description": "创建并返回结果",
                             "source_scenario_ids": ["SC-001", "SC-002"], "source_story_ids": ["US-001"],
                             "source_acceptance_ids": ["AC-001"], "boundary": {"business_object": "任务"},
                             "change_type": "new", "change_detail": {"before": None, "after": "支持创建"}},
                        ],
                        "retained_capability_references": [
                            {"draft_key": "retained-list", "source_scenario_ids": ["SC-001"],
                             "source_evidence": [{"source_quote": "audit-only"}]},
                        ],
                    },
                    entrypoints["page_index"]: {"pages": [
                        {"id": "PAGE-001", "name": "提交页", "actions": [
                            {"id": "ACTION-001", "name": "提交", "source_function_ids": ["FUNC-001"]},
                        ]},
                    ]},
                    entrypoints["interface_index"]: {"interfaces": [{"id": "API-001", "name": "创建任务"}]},
                },
            },
        }
        self.feature = {"id": "FUNC-001", "scene_id": "SC-001", "requirement_ids": ["US-001"], "assertions": [
            {"case": "TC-001", "step": {"op": "visible", "selector": "#result"},
             "checkpoint": {"index": 1, "after": [{"op": "click", "selector": "#submit"}]}},
        ]}
        self.baseline = {"requirements": {"features": [self.feature]}}
        self.mapping = {"features": {"FUNC-001": {"scene_id": "SC-001", "acceptance_cases": {
            "AC-001": [{"case": "TC-001", "checkpoint": 1}],
        }}}}
        self.local_mapping = self.mapping["features"]["FUNC-001"]

    def test_projection_preserves_all_source_links_and_keeps_retained_separate(self):
        result = analysis_mapping.project(self.bundle)
        self.assertEqual(result["features"][0]["source_scenario_ids"], ["SC-001", "SC-002"])
        self.assertEqual(result["features"][0]["page_ids"], ["PAGE-001"])
        self.assertEqual(result["requirements"][0]["acceptance_criteria"][0]["id"], "AC-001")
        self.assertEqual(result["pages"][0]["actions"][0]["id"], "ACTION-001")
        self.assertEqual(result["scenes"][1]["ui_surface"], "not_required")
        self.assertEqual(result["retained_capability_references"], [
            {"draft_key": "retained-list", "source_scenario_ids": ["SC-001"]},
        ])
        self.assertNotIn("tasks", result)
        self.assertNotIn("scene_id", result["features"][0])
        self.assertNotIn("assertions", result["features"][0])
        result["features"][0]["source_scenario_ids"].pop()
        self.assertEqual(analysis_mapping.project(self.bundle)["features"][0]["source_scenario_ids"], ["SC-001", "SC-002"])

    def test_binding_records_confirmed_owner_without_losing_other_source_scenarios(self):
        bound = analysis_mapping.bind(self.bundle, self.baseline, self.mapping)
        feature = bound["features"]["FUNC-001"]
        self.assertEqual(bound["origin"], self.bundle["binding"])
        self.assertEqual(feature["scene_id"], "SC-001")
        self.assertEqual(feature["source_scenario_ids"], ["SC-001", "SC-002"])
        self.assertEqual(feature["source_scene_ui"]["SC-002"], "not_required")
        self.assertEqual(feature["acceptance_cases"], self.local_mapping["acceptance_cases"])
        self.assertEqual(bound["retained_capability_references"][0]["draft_key"], "retained-list")

    def test_binding_refuses_unconfirmed_or_out_of_scope_scene_owner(self):
        self.local_mapping["scene_id"] = "SC-002"
        with self.assertRaisesRegex(ValueError, "匹配已确认 S1"):
            analysis_mapping.bind(self.bundle, self.baseline, self.mapping)
        self.feature["scene_id"] = "SC-002"
        self.bundle["facts"]["payloads"]["scenarios/index.json"]["scenarios"][1]["scope"] = "out_of_scope"
        with self.assertRaisesRegex(ValueError, "04 本期源场景"):
            analysis_mapping.bind(self.bundle, self.baseline, self.mapping)

    def test_binding_rejects_missing_acceptance_or_changed_checkpoint(self):
        self.local_mapping["acceptance_cases"] = {}
        with self.assertRaisesRegex(ValueError, "映射全部 04 来源 AC"):
            analysis_mapping.bind(self.bundle, self.baseline, self.mapping)
        self.local_mapping["acceptance_cases"] = {"AC-001": [{"case": "TC-001", "checkpoint": 2}]}
        with self.assertRaisesRegex(ValueError, "须对应唯一的本轮已确认断言"):
            analysis_mapping.bind(self.bundle, self.baseline, self.mapping)

    def test_binding_rejects_narrowed_source_stories_or_missing_imported_feature(self):
        self.feature["requirement_ids"] = ["LOCAL-001"]
        with self.assertRaisesRegex(ValueError, "04 用户故事不一致"):
            analysis_mapping.bind(self.bundle, self.baseline, self.mapping)
        with self.assertRaisesRegex(ValueError, "覆盖冻结包中的全部本期功能"):
            analysis_mapping.bind(self.bundle, self.baseline, {"features": {}})
