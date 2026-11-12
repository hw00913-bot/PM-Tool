"""Exercise all four delivery selections against one complete semantic baseline."""
import itertools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
import test_workflow as workflow
from test_sources import prototype_script


class OptionalDiagramsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='optional-diagram-tests-')
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

    def run_command(self, script, *args, expected=0):
        result = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def test_four_selections_package_import_and_publish(self):
        for business, sequence in itertools.product((False, True), repeat=2):
            with self.subTest(business=business, sequence=sequence):
                root = self.base / f'analysis-{business}-{sequence}'
                shutil.copytree(self.base / 'baseline', root)
                selected = {'business_flow': business, 'interaction_sequence': sequence}
                policy = {'contract': 'optional-analysis-diagrams@1', 'requested': selected,
                          'requests': [{'kind': k, 'by': 'fixture-user', 'reason': 'explicit test request',
                                        'at': '2026-11-03T00:00:00+00:00'} for k,v in selected.items() if v]}
                (root / 'memory/diagram-deliverables.json').write_text(json.dumps(policy))
                # Unrequested historical HTML is deliberately retained; packaging must exclude it.
                self.run_command(workflow.SCRIPTS / 'render_documents.py', root, '--kind', 'baseline')
                self.run_command(workflow.SCRIPTS / 'loop.py', 'invalidate', root, '--from-stage', 'S8', '--reason', 'test selection')
                self.run_command(workflow.SCRIPTS / 'loop.py', 'start', root)
                self.run_command(workflow.SCRIPTS / 'verify_analysis.py', root)
                self.run_command(workflow.SCRIPTS / 'loop.py', 'complete', root)
                self.run_command(workflow.SCRIPTS / 'loop.py', 'approve', root, 'G3', '--by', 'fixture-user', '--note', 'synthetic test approval')
                self.run_command(workflow.SCRIPTS / 'package_delivery.py', root)
                manifest = json.loads((root / 'deliverables/delivery-manifest.json').read_text())
                package = root / manifest['package_file']
                with zipfile.ZipFile(package) as archive:
                    names = set(archive.namelist())
                    self.assertEqual(business, 'outputs/business-scenario-atlas.html' in names)
                    self.assertEqual(sequence, 'outputs/interaction-sequence-atlas.html' in names)
                    self.assertIn('outputs/page-structure-atlas.html', names)
                    self.assertIn('scenarios/SC-001.json', names)
                    self.assertIn('sequences/SEQ-001.json', names)
                importer = prototype_script('orchestrator/scripts/import_analysis.py')
                self.run_command(importer, 'inspect', package)
                prototype = self.base / f'prototype-{business}-{sequence}'
                self.run_command(prototype_script('skill-library/loop-project-scaffolder/scripts/create_project.py'), prototype, '--name', 'optional-test')
                self.assertFalse((prototype / 'flowcharts/business-process.html').exists())
                self.assertFalse((prototype / 'flowcharts/sequence-interaction.html').exists())
                self.run_command(importer, 'import', prototype, package, '--allow-unversioned')
                config_path = prototype / 'config/workflow.json'
                config = json.loads(config_path.read_text()); config['stage'] = 's3'
                imported = json.loads((prototype / 'config/requirement-analysis.json').read_text())
                config['currentIteration'].update(planConfirmed=True,
                    approvedRequirementAnalysisManifestSha256=imported['manifestSha256'])
                config_path.write_text(json.dumps(config))
                self.run_command(importer, 'materialize', prototype)
                config['stage'] = 's7'; config_path.write_text(json.dumps(config))
                self.run_command(importer, 'publish-diagrams', prototype)
                self.assertEqual(business, (prototype / 'flowcharts/business-process.html').exists())
                self.assertEqual(sequence, (prototype / 'flowcharts/sequence-interaction.html').exists())
                nav = (prototype / 'js/delivery-nav.js').read_text()
                self.assertIn(f'var includeBusinessFlow = {str(business).lower()};', nav)
                self.assertIn(f'var includeSequenceFlow = {str(sequence).lower()};', nav)
                sys.path.insert(0, str(importer.parent))
                import loop_preflight
                errors = []
                loop_preflight.check_delivery_diagrams(prototype, 's8', errors)
                loop_preflight.check_delivery_navigation(prototype, 's8', errors)
                self.assertEqual([], errors)
                # Retained old artifacts must never leak into the next frozen
                # view; exercise the real verified imported package, not mocks.
                for enabled, rel in [(business,'flowcharts/business-process.html'),
                                     (sequence,'flowcharts/sequence-interaction.html'),
                                     (False,'docs/interaction.html')]:
                    if not enabled:
                        artifact=prototype / rel
                        artifact.parent.mkdir(parents=True,exist_ok=True)
                        artifact.write_text('<html><body>retained old output</body></html>')
                snapshot = self.base / f'snapshot-{business}-{sequence}'
                program = ('import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); '
                           'import project_versions as p; '
                           'p.export_static_view(Path(sys.argv[2]),Path(sys.argv[3]),Path(sys.argv[4]),"test")')
                run=subprocess.run([sys.executable,'-c',program,str(importer.parent),str(prototype),str(snapshot),str(self.base)],capture_output=True,text=True)
                self.assertEqual(0,run.returncode,run.stdout+run.stderr)
                root_view=snapshot/'prototype'
                entries={item['path'] for item in json.loads((root_view/'source.txt').read_text())['files']}
                self.assertTrue((prototype / imported['baselineRoot'] / 'inputs/user-stories.md').is_file())
                self.assertFalse((root_view / 'inputs/requirement-analysis').exists())
                self.assertFalse(any(name.startswith('inputs/requirement-analysis/') for name in entries))
                for enabled, rel in [(business,'flowcharts/business-process.html'),
                                     (sequence,'flowcharts/sequence-interaction.html'),
                                     (False,'docs/interaction.html')]:
                    self.assertEqual(enabled,(root_view/rel).exists())
                    self.assertEqual(enabled,rel in entries)
                    self.assertEqual(enabled,(root_view/('.source-html/'+rel+'.source.txt')).exists())

    def test_disabled_views_do_not_disable_fact_validation(self):
        root = self.base / 'invalid-facts'
        shutil.copytree(self.base / 'baseline', root)
        (root / 'memory/diagram-deliverables.json').write_text(json.dumps({
            'contract': 'optional-analysis-diagrams@1',
            'requested': {'business_flow': False, 'interaction_sequence': False}, 'requests': []}))
        scenario = root / 'scenarios/SC-001.json'
        detail = json.loads(scenario.read_text()); detail['main_flow'] = []
        scenario.write_text(json.dumps(detail))
        sequence = root / 'sequences/SEQ-001.json'
        detail = json.loads(sequence.read_text()); detail['messages'] = []
        sequence.write_text(json.dumps(detail))
        self.run_command(workflow.SCRIPTS / 'verify_analysis.py', root, expected=1)
        report = json.loads((root / 'runtime/verification-report.json').read_text())
        codes = {e['code'] for e in report['errors']}
        self.assertIn('empty_main_flow', codes)
        self.assertTrue(any('sequence' in code or 'message' in code for code in codes), codes)

    def test_default_skips_engines_but_keeps_semantic_gates(self):
        root = self.base / 'default'
        self.run_command(workflow.SCRIPTS / 'loop.py', 'init', root, '--name', 'default')
        for kind in ('scenarios', 'sequences'):
            self.run_command(workflow.SCRIPTS / 'render_atlases.py', root, '--kind', kind, '--plantuml-command', '/missing/engine')
        self.assertFalse((root / 'outputs/business-scenario-atlas.html').exists())
        self.assertFalse((root / 'outputs/interaction-sequence-atlas.html').exists())
        result = self.run_command(workflow.SCRIPTS / 'verify_analysis.py', root, expected=1)
        report = json.loads((root / 'runtime/verification-report.json').read_text())
        self.assertTrue(report['errors'])
        self.assertFalse(any(e.get('artifact') in ('outputs/business-scenario-atlas.html', 'outputs/interaction-sequence-atlas.html') for e in report['errors']))

    def test_request_records_and_invalidates_without_reapproving(self):
        root = self.base / 'request'
        self.run_command(workflow.SCRIPTS / 'loop.py', 'init', root, '--name', 'request')
        self.run_command(workflow.SCRIPTS / 'loop.py', 'request-diagrams', root, '--interaction-sequence', '--by', 'fixture-user', '--reason', 'sequence only')
        policy = json.loads((root / 'memory/diagram-deliverables.json').read_text())
        self.assertEqual({'business_flow': False, 'interaction_sequence': True}, policy['requested'])
        state = json.loads((root / 'runtime/loop-state.json').read_text())
        self.assertNotEqual('approved', state['gates']['G3']['status'])
