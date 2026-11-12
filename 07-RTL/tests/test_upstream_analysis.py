import unittest
from unittest.mock import patch

from loop import upstream_analysis
from loop.state import LoopError


class UpstreamAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "package": "/frozen/requirements.zip",
            "manifest_sha256": "a" * 64,
            "baseline_id": "RA-FROZEN",
            "baseline_fingerprint": "b" * 64,
            "project": {"name": "Demo", "version": "1"},
            "project_version": {"snapshot_id": "snapshot-1"},
            "approval_provenance": "registered-delivery",
            "g3_fingerprint": "c" * 64,
        }

    def test_missing_live_approval_does_not_fall_back_to_saved_binding(self):
        with patch.object(upstream_analysis, "_read_verified", side_effect=LoopError("Stage stale/unbound: analysis")) as check:
            with self.assertRaisesRegex(LoopError, "Stage stale/unbound: analysis"):
                upstream_analysis.require_current(self.binding, include_facts=False)
        check.assert_called_once_with(self.binding["package"], include_facts=False)

    def test_approved_replacement_package_does_not_match_pinned_manifest(self):
        changed = {**self.binding, "manifest_sha256": "d" * 64}
        with patch.object(upstream_analysis, "_read_verified", return_value={"binding": changed, "facts": {}}):
            with self.assertRaisesRegex(LoopError, "身份与登记来源不一致"):
                upstream_analysis.require_current(self.binding)

    def test_unversioned_package_can_be_inspected_but_not_bound(self):
        old = {**self.binding, "project_version": None, "approval_provenance": "unversioned-unverified"}
        with patch.object(upstream_analysis, "_read_verified", return_value={"binding": old, "facts": {}}):
            self.assertEqual(upstream_analysis.inspect(old["package"])["binding"], old)
            with self.assertRaisesRegex(LoopError, "缺少可核对的当前版本批准"):
                upstream_analysis.bind(old["package"])

    def test_saved_receipt_cannot_choose_an_executable_path(self):
        modified = {**self.binding, "verifier_root": "/from/receipt"}
        with patch.object(upstream_analysis, "_read_verified") as check:
            with self.assertRaisesRegex(LoopError, "来源绑定无效"):
                upstream_analysis.require_current(modified)
        check.assert_not_called()
