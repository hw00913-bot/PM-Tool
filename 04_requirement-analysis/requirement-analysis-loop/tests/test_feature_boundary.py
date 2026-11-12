from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from feature_contract import (
    GRANULARITY_VERSION, BOUNDARY_TEXT_FIELDS, BOUNDARY_LIST_FIELDS,
    feature_boundary_errors, feature_artifact_errors,
)
from render_documents import render_feature_list, RenderError


def fixture() -> dict:
    return {
        "schema_version": "1.0", "granularity_version": GRANULARITY_VERSION,
        "features": [{
            "id": "FUNC-001", "version": "1.0", "module_l1": "账户", "module_l2": "申请",
            "module_l3": None, "name": "发起充值申请", "description": "创建待支付订单并返回支付入口",
            "notes": None, "change_type": "new", "source_story_ids": ["US-001"],
            "source_acceptance_ids": ["AC-001"], "source_scenario_ids": ["SC-001"],
            "boundary": {
                "business_object": "充值申请", "primary_action": "发起充值申请",
                "system_boundary": "账户系统负责申请；不实现渠道内部收款",
                "initiator": "账户用户", "trigger": "提交充值申请",
                "primary_outcome": "取得有效待支付订单", "completion_result": "返回待支付订单及支付入口",
                "included_scope": ["金额校验", "建单及返回入口"], "excluded_scope": ["余额入账"],
                "business_rules": ["同一次提交不重复建单"], "exception_paths": ["无效金额拒绝"],
                "dependencies": ["渠道支付入口创建"], "assumptions": [],
                "split_rationale": "待支付是完整结果；金额字段和渠道调用是内部组成",
            },
            "change_detail": {"before": None, "after": "1.0 新增申请", "impact": ["新增申请及待支付结果"]},
        }],
    }


class FeatureBoundaryTest(unittest.TestCase):
    def test_operation_level_fields_and_generic_bundle_rejection(self) -> None:
        data = fixture()
        data["granularity_version"] = "minimal-business-process@1"
        self.assertTrue(any("granularity_version" in e for e in feature_boundary_errors(data)))
        data = fixture()
        for label in ("管理", "全生命周期管理", "全流程处理"):
            with self.subTest(label=label):
                data["features"][0]["boundary"]["primary_action"] = label
                self.assertTrue(any("管理包概述" in e for e in feature_boundary_errors(data)))
        data["features"][0]["boundary"]["primary_action"] = "创建租户"
        self.assertEqual([], feature_boundary_errors(data), "校验不是语义判定器，非空的具体操作仍需人工检查与业务对象／说明是否一致")

    def test_new_and_iteration_boundary_contract(self) -> None:
        data = fixture()
        self.assertEqual([], feature_boundary_errors(data))
        feature = data["features"][0]
        feature["change_type"] = "iteration"
        feature["change_detail"] = {"before": "1.0 只支持固定金额", "after": "1.1 支持自定义金额", "impact": ["修改金额规则；其他边界不变"]}
        self.assertEqual([], feature_boundary_errors(data))
        feature["change_detail"]["before"] = None
        self.assertTrue(any("before" in error for error in feature_boundary_errors(data)))

    def test_boundary_missing_types_and_explicit_empty_scope(self) -> None:
        for field in BOUNDARY_TEXT_FIELDS:
            for invalid in (None, "", " ", [], {}):
                with self.subTest(field=field, value=invalid):
                    data = fixture()
                    data["features"][0]["boundary"][field] = invalid
                    self.assertTrue(any(field in error for error in feature_boundary_errors(data)))
        for field in BOUNDARY_LIST_FIELDS:
            for invalid in (None, "无", [""], [None], [{}]):
                with self.subTest(field=field, value=invalid):
                    data = fixture()
                    data["features"][0]["boundary"][field] = invalid
                    self.assertTrue(any(field in error for error in feature_boundary_errors(data)))
        data = fixture()
        data["features"][0]["boundary"]["included_scope"] = []
        self.assertTrue(feature_boundary_errors(data))
        data = fixture()
        del data["features"][0]["boundary"]
        self.assertTrue(any("boundary" in e for e in feature_boundary_errors(data)))
        data = fixture()
        del data["granularity_version"]
        self.assertTrue(any("granularity_version" in e for e in feature_boundary_errors(data)))
        data = fixture()
        data["features"][0]["change_detail"]["before"] = "旧功能不能伪装新增"
        self.assertTrue(feature_boundary_errors(data))
        data["features"][0]["change_detail"] = {}
        self.assertTrue(any("impact" in e for e in feature_boundary_errors(data)))
        data = fixture()
        data["features"][0]["source_acceptance_ids"] = None
        self.assertTrue(any("source_acceptance_ids" in e for e in feature_boundary_errors(data)))

    def test_rendered_scope_acceptance_and_stale_view_rejection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-boundary-") as temp:
            root = Path(temp)
            files = {
                "memory/feature-list.json": fixture(),
                "scenarios/scenario-index.json": {"scenarios": [{"id": "SC-001", "scope": "in_scope"}]},
                "memory/normalized-user-stories.json": {"user_stories": [{"id": "US-001", "acceptance_criteria": [{"id": "AC-001", "statement": "返回有效待支付订单和支付入口"}]}]},
            }
            for relative, data in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            view = render_feature_list(root, "隔离测试")
            content = view.read_text(encoding="utf-8")
            for text in ("业务对象：充值申请", "单一主操作：发起充值申请", "不包含范围：余额入账", "AC-001：返回有效待支付订单和支付入口", "拆分／合并依据", "不是标准功能点数", "变更前：不适用（新增）"):
                self.assertIn(text, content)
            self.assertEqual([], feature_artifact_errors(root))
            changed = deepcopy(files["memory/feature-list.json"])
            changed["features"][0]["boundary"]["excluded_scope"].append("线下汇款")
            source = root / "memory/feature-list.json"
            source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(any("当前功能" in e for e in feature_artifact_errors(root)))
            render_feature_list(root, "隔离测试")
            self.assertEqual([], feature_artifact_errors(root))
            stories = files["memory/normalized-user-stories.json"]
            stories["user_stories"][0]["acceptance_criteria"][0]["statement"] = "相同请求必须返回同一待支付订单"
            (root / "memory/normalized-user-stories.json").write_text(json.dumps(stories, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(any("验收事实" in e for e in feature_artifact_errors(root)))
            render_feature_list(root, "隔离测试")
            self.assertEqual([], feature_artifact_errors(root))
            previous = view.read_bytes()
            del changed["features"][0]["boundary"]
            source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(RenderError):
                render_feature_list(root, "隔离测试")
            self.assertEqual(previous, view.read_bytes(), "拒绝缺失边界时不得覆盖既有视图")


if __name__ == "__main__":
    unittest.main()
