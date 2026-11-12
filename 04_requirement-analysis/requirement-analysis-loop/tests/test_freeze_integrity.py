from argparse import Namespace
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
import test_workflow as workflow
from test_sources import prototype_script
import subprocess
import sys
import zipfile
import hashlib
from unittest.mock import patch
import loop

class FreezeIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='freeze-integrity-')
        cls.base = Path(cls.temp.name)
        destination = cls.base / 'baseline'
        class Capture(workflow.WorkflowTest):
            def run_script(self, name, *args, **kwargs):
                if name == 'loop.py' and args[0] == 'invalidate' and not destination.exists():
                    shutil.copytree(Path(args[1]), destination)
                return super().run_script(name, *args, **kwargs)
        Capture('test_full_loop_and_incremental_invalidation').test_full_loop_and_incremental_invalidation()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.root = self.base / self._testMethodName
        shutil.copytree(self.base / 'baseline', self.root)
        p = self.root / 'runtime/loop-state.json'
        s = json.loads(p.read_text()); s['run'].update(current_stage='S9', status='active')
        p.write_text(json.dumps(s))

    def run_script(self, name, *args):
        return subprocess.run([sys.executable, str(workflow.SCRIPTS / name), str(self.root), *args],
                              capture_output=True, text=True)

    def test_high_issue_after_approval_rejects_stale_report(self):
        p = self.root / 'runtime/open-items.json'
        d = json.loads(p.read_text()); d['items'].append({'id':'ISSUE-999','severity':'high','status':'open',
                                                       'summary':'新增阻断','related_ids':['SC-001']})
        p.write_text(json.dumps(d))
        self.assertEqual(2, self.run_script('package_delivery.py').returncode)

    def test_reverification_does_not_reapprove_changed_facts(self):
        p = self.root / 'memory/project-facts.json'
        d = json.loads(p.read_text()); d['project']['objective'] = '更新后的目标'
        p.write_text(json.dumps(d))
        self.assertEqual(0, self.run_script('verify_analysis.py').returncode)
        result = self.run_script('package_delivery.py')
        self.assertEqual(2, result.returncode)
        self.assertIn('G3', result.stderr)

    def test_scenario_priority_drift_invalidates_g1_basis(self):
        index = self.root / 'scenarios/scenario-index.json'
        data = json.loads(index.read_text())
        data['scenarios'][0]['priority'] = 'P2'
        workflow.write_json(index, data)
        result = self.run_script('render_documents.py', '--kind', 'scenarios')
        self.assertEqual(0, result.returncode, result.stderr)
        verification = self.run_script('verify_analysis.py')
        self.assertEqual(1, verification.returncode, verification.stderr)
        self.assertIn('changed_g1_scenarios', (self.root / 'runtime/verification-report.json').read_text())
        package = self.run_script('package_delivery.py')
        self.assertEqual(2, package.returncode)
        self.assertIn('G1', package.stderr)

    def test_interface_source_bytes_drift_invalidates_g2_basis(self):
        source = self.root / 'inputs/interface-documents/vendor-api.md'
        original = source.read_bytes()
        changed = original.replace(b'POST /tasks', b'DELETE /tasks')
        self.assertNotEqual(original, changed)
        source.write_bytes(changed)
        verification = self.run_script('verify_analysis.py')
        self.assertEqual(1, verification.returncode, verification.stderr)
        self.assertIn('changed_g2_interfaces', (self.root / 'runtime/verification-report.json').read_text())
        package = self.run_script('package_delivery.py')
        self.assertEqual(2, package.returncode)
        self.assertIn('G2', package.stderr)

    def waiting_for_g3(self):
        state_path = self.root / 'runtime/loop-state.json'
        state = json.loads(state_path.read_text())
        state['run'].update(current_stage='S8', status='needs_confirmation')
        state['gates']['G3']['status'] = 'pending'
        state['gates']['G3'].pop('approval_snapshot')
        workflow.write_json(state_path, state)
        return state_path, Namespace(project_dir=str(self.root), gate='G3', by='测试用户', note='原核验版本')

    def test_g3_rejects_changed_acceptance_after_reverification(self):
        state_path, args = self.waiting_for_g3()
        saved_state = state_path.read_bytes()
        stories = self.root / 'memory/normalized-user-stories.json'
        original_now = loop.now

        def edit_acceptance_after_verification():
            data = json.loads(stories.read_text())
            data['user_stories'][0]['acceptance_criteria'][0]['statement'] = '重复提交时返回原任务'
            workflow.write_json(stories, data)
            result = self.run_script('render_documents.py')
            self.assertEqual(0, result.returncode, result.stderr)
            return original_now()

        with patch.object(loop, 'now', side_effect=edit_acceptance_after_verification):
            with self.assertRaisesRegex(loop.LoopError, 'G3 确认期间交付内容发生变化'):
                loop.cmd_approve(args)
        self.assertEqual(saved_state, state_path.read_bytes())
        self.assertEqual(0, self.run_script('verify_analysis.py').returncode)
        self.assertEqual(2, self.run_script('package_delivery.py').returncode)

    def test_g3_preserves_state_invalidated_during_confirmation(self):
        state_path, args = self.waiting_for_g3()
        original_now = loop.now

        def invalidate_after_verification():
            with patch.object(loop, 'now', original_now):
                loop.cmd_invalidate(Namespace(project_dir=str(self.root), from_stage='S7', ids=None,
                                              reason='并发修订', change_id=None))
            return original_now()

        with patch.object(loop, 'now', side_effect=invalidate_after_verification):
            with self.assertRaisesRegex(loop.LoopError, 'G3 确认期间流程状态已变化'):
                loop.cmd_approve(args)
        state = json.loads(state_path.read_text())
        self.assertEqual('S7', state['run']['current_stage'])
        self.assertEqual('pending', state['gates']['G3']['status'])

    def test_g3_rejects_replacement_report_after_s8_completed(self):
        state_path, args = self.waiting_for_g3()
        previous = json.loads(state_path.read_text())['stages']['S8']['reviewed_fingerprint']
        facts = self.root / 'memory/project-facts.json'
        data = json.loads(facts.read_text())
        data['project']['objective'] = 'S8 后修改的目标'
        workflow.write_json(facts, data)
        self.assertEqual(0, self.run_script('verify_analysis.py').returncode)
        report = json.loads((self.root / 'runtime/verification-report.json').read_text())
        self.assertNotEqual(previous, report['approval_snapshot']['fingerprint'])
        with self.assertRaisesRegex(loop.LoopError, '须回到 S8'):
            loop.cmd_approve(args)
        self.assertEqual('pending', json.loads(state_path.read_text())['gates']['G3']['status'])

    def test_g3_accepts_same_snapshot_in_regenerated_report(self):
        state_path, args = self.waiting_for_g3()
        self.assertEqual(0, self.run_script('verify_analysis.py').returncode)
        report = json.loads((self.root / 'runtime/verification-report.json').read_text())
        loop.cmd_approve(args)
        approved = json.loads(state_path.read_text())['gates']['G3']['approval_snapshot']
        self.assertEqual(report['approval_snapshot'], approved)

    def test_changed_flow_rejects_old_diagram(self):
        p = self.root / 'memory/business-rules.json'
        d = json.loads(p.read_text()); d['business_flows'][0]['steps'][0] = '改为人工批准'
        p.write_text(json.dumps(d))
        self.assertEqual(1, self.run_script('verify_analysis.py').returncode)
        self.assertEqual(2, self.run_script('package_delivery.py').returncode)

    def test_blank_formal_svg_is_rejected(self):
        p = self.root / 'outputs/business-scenario-atlas.html'
        p.write_text(re.sub(r'(<svg\b[^>]*data-plantuml-render="pass"[^>]*>).*?</svg>',
                            r'\1</svg>', p.read_text(), flags=re.S))
        self.assertEqual(1, self.run_script('verify_analysis.py').returncode)
        self.assertEqual(2, self.run_script('package_delivery.py').returncode)

    def test_new_package_without_g3_snapshot_is_rejected_by_consumer(self):
        manifest = json.loads((self.root/'deliverables/delivery-manifest.json').read_text())
        with zipfile.ZipFile(self.root/manifest['package_file']) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        handoff = json.loads(files['prototype-handoff.json'])
        del handoff['gates']['G3']['approval_snapshot']
        files['prototype-handoff.json'] = json.dumps(handoff).encode()
        manifest = json.loads(files['delivery-manifest.json'])
        for entry in manifest['files']:
            if entry['path'] == 'prototype-handoff.json':
                entry.update(bytes=len(files['prototype-handoff.json']),
                             sha256=hashlib.sha256(files['prototype-handoff.json']).hexdigest())
        files['delivery-manifest.json'] = json.dumps(manifest).encode()
        package = self.root/'missing-approval.zip'
        with zipfile.ZipFile(package, 'w') as archive:
            for name, raw in files.items():
                archive.writestr(name, raw)
        importer = prototype_script("orchestrator/scripts/import_analysis.py", workflow.SKILL_DIR)
        result = subprocess.run([sys.executable,str(importer),'inspect',str(package)],capture_output=True,text=True)
        self.assertEqual(2,result.returncode)
        self.assertIn('G3',result.stderr)
