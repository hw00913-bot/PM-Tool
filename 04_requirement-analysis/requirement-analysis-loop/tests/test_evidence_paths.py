"""Relocation changes lookup, while original evidence and SHA remain binding."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from evidence_paths import EvidencePathError, EvidencePathResolver
from operation_attribution import StrictReview
import test_bounded_source_chain as chain_fixtures
from test_retained_operation_contract import fixture


class EvidencePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='evidence-relocation-')
        self.base = Path(self.tmp.name).resolve()
        self.original = self.base / 'original'
        self.project = self.base / 'rehearsal'
        self.old_workspace = self.original / '需求/2.0.0/分析工作区'
        self.workspace = self.project / '2.0.0/需求/分析工作区'
        self.old_workspace.mkdir(parents=True)
        self.workspace.mkdir(parents=True)
        self.source = self.old_workspace / 'inputs/evidence.txt'
        self.source.parent.mkdir()
        self.source.write_text('preserved source\n')
        self.target = self.workspace / 'inputs/evidence.txt'
        self.target.parent.mkdir()
        self.target.write_bytes(self.source.read_bytes())
        self.mapping = {'from': str(self.original / '需求/2.0.0'), 'to': '2.0.0/需求'}
        self.manifest = self.project / 'memory/path-relocations.json'

    def tearDown(self):
        self.tmp.cleanup()

    def manifest_data(self, mappings=None):
        return {'schema': 'path-relocations@1', 'project_root': str(self.original),
                'mappings': mappings if mappings is not None else [self.mapping]}

    def write_manifest(self, data=None):
        self.manifest.parent.mkdir(exist_ok=True)
        self.manifest.write_text(json.dumps(data or self.manifest_data()))

    def test_no_manifest_preserves_legacy_absolute_and_relative_lookup(self):
        resolver = EvidencePathResolver(self.workspace)
        self.assertEqual(self.source, resolver.resolve(str(self.source)))
        self.assertEqual(self.target, resolver.resolve('inputs/evidence.txt#L1', internal=True))

    def write_ledger(self, data):
        ledger = self.project / 'memory/project-versions.json'
        ledger.parent.mkdir(exist_ok=True)
        ledger.write_text(json.dumps(data))

    def test_version_bundle_missing_manifest_cannot_read_valid_old_source(self):
        self.write_ledger({'directory_layout': {'schema': 'version-bundle@1'}})
        with self.assertRaisesRegex(EvidencePathError, 'requires its own'):
            EvidencePathResolver(self.workspace).resolve(str(self.source))

    def test_nested_manifest_cannot_replace_required_project_manifest(self):
        self.write_ledger({'directory_layout': {'schema': 'version-bundle@1'}})
        nested = self.workspace / 'memory/path-relocations.json'
        nested.parent.mkdir()
        nested.write_text(json.dumps(self.manifest_data()))
        with self.assertRaisesRegex(EvidencePathError, 'requires its own'):
            EvidencePathResolver(self.workspace).resolve(str(self.source))

    def test_legacy_ledger_does_not_require_a_relocation_manifest(self):
        self.write_ledger({'schema_version': '1.0', 'project_id': 'legacy-project'})
        self.assertEqual(self.source, EvidencePathResolver(self.workspace).resolve(str(self.source)))

    def test_mapping_wins_even_when_original_file_still_exists_with_other_bytes(self):
        self.write_manifest()
        self.source.write_text('different original checkout\n')
        resolver = EvidencePathResolver(self.workspace)
        self.assertEqual(self.target, resolver.resolve(str(self.source) + '#L1', internal=True))
        self.assertEqual('preserved source\n', resolver.resolve(str(self.source)).read_text())

    def test_missing_mapped_target_never_reads_existing_original(self):
        self.write_manifest()
        self.target.unlink()
        self.assertIsNone(EvidencePathResolver(self.workspace).resolve(str(self.source)))

    def test_unmapped_old_project_path_cannot_fall_back(self):
        self.write_manifest()
        old_other = self.original / 'unmapped.txt'
        old_other.write_text('original-only source')
        self.assertIsNone(EvidencePathResolver(self.workspace).resolve(str(old_other)))

    def test_unrelated_external_evidence_is_not_reinterpreted(self):
        self.write_manifest()
        outside = self.base / 'supplier.txt'
        outside.write_text('external source')
        resolver = EvidencePathResolver(self.workspace)
        self.assertEqual(outside, resolver.resolve(str(outside)))
        self.assertIsNone(resolver.resolve(str(outside), internal=True))

    def test_mapped_cross_version_file_cannot_be_internal_source(self):
        other = self.project / '1.1.0/prototype/source.js'
        other.parent.mkdir(parents=True)
        other.write_text('source')
        old = self.original / 'prototype/source.js'
        self.write_manifest(self.manifest_data([self.mapping, {'from': str(old.parent), 'to': '1.1.0/prototype'}]))
        resolver = EvidencePathResolver(self.workspace)
        self.assertEqual(other, resolver.resolve(str(old)))
        self.assertIsNone(resolver.resolve(str(old), internal=True))

    def test_same_project_root_accepts_current_paths_but_not_unmapped_old_paths(self):
        data = self.manifest_data()
        data['project_root'] = str(self.project)
        data['mappings'][0]['from'] = str(self.project / '需求/2.0.0')
        self.write_manifest(data)
        old = self.project / 'unmapped.txt'
        old.write_text('old unmapped evidence')
        resolver = EvidencePathResolver(self.workspace)
        self.assertEqual(self.target, resolver.resolve(str(self.target), internal=True))
        self.assertIsNone(resolver.resolve(str(old)))

    def test_target_traversal_and_absolute_target_are_rejected(self):
        for target in ['../outside', '/tmp/outside', '2.0.0/../outside', '2.0.0//需求']:
            with self.subTest(target=target):
                self.write_manifest(self.manifest_data([dict(self.mapping, to=target)]))
                with self.assertRaises(EvidencePathError):
                    EvidencePathResolver(self.workspace).resolve(str(self.source))

    def test_symlink_escape_is_rejected_without_original_fallback(self):
        self.write_manifest()
        self.target.unlink()
        self.target.symlink_to(self.source)
        with self.assertRaisesRegex(EvidencePathError, 'escapes'):
            EvidencePathResolver(self.workspace).resolve(str(self.source))

    def test_overlapping_conflicting_mappings_are_rejected(self):
        self.write_manifest(self.manifest_data([self.mapping, {
            'from': str(self.old_workspace), 'to': '2.0.0/other-workspace'}]))
        with self.assertRaisesRegex(EvidencePathError, 'Ambiguous'):
            EvidencePathResolver(self.workspace).resolve(str(self.source))

    def test_consistent_overlapping_mappings_do_not_change_identity(self):
        self.write_manifest(self.manifest_data([self.mapping, {
            'from': str(self.old_workspace), 'to': '2.0.0/需求/分析工作区'}]))
        self.assertEqual(self.target, EvidencePathResolver(self.workspace).resolve(str(self.source)))

    def test_duplicate_prefix_and_foreign_source_are_rejected(self):
        for mappings in [[self.mapping, self.mapping], [{'from': '/foreign/project', 'to': '2.0.0/需求'}]]:
            self.write_manifest(self.manifest_data(mappings))
            with self.assertRaises(EvidencePathError):
                EvidencePathResolver(self.workspace).resolve(str(self.source))

    def test_invalid_or_ambiguous_manifest_never_uses_legacy_fallback(self):
        self.write_manifest(dict(self.manifest_data(), schema='unknown'))
        with self.assertRaisesRegex(EvidencePathError, 'schema'):
            EvidencePathResolver(self.workspace).resolve(str(self.source))
        self.write_manifest()
        inner = self.workspace / 'memory/path-relocations.json'
        inner.parent.mkdir()
        inner.write_text(self.manifest.read_text())
        with self.assertRaisesRegex(EvidencePathError, 'Ambiguous'):
            EvidencePathResolver(self.workspace).resolve(str(self.source))


class PreservedSourceChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='relocated-source-chain-')
        self.base = Path(self.tmp.name).resolve()
        self.original = self.base / 'original'
        self.project = self.base / 'rehearsal'
        self.old_workspace = self.original / '需求/2.0.0/分析工作区'
        self.workspace = self.project / '2.0.0/需求/分析工作区'
        self.old_workspace.mkdir(parents=True)
        self.harness = chain_fixtures.BoundedSourceChainTests()
        self.harness.root = self.old_workspace
        self.harness.facts = fixture(self.old_workspace)
        self.proof, self.scope, self.candidate, self.old_source = self.harness.make()
        shutil.copytree(self.old_workspace, self.workspace)
        manifest = self.project / 'memory/path-relocations.json'
        manifest.parent.mkdir()
        manifest.write_text(json.dumps({'schema': 'path-relocations@1', 'project_root': str(self.original),
                                      'mappings': [{'from': str(self.original / '需求/2.0.0'), 'to': '2.0.0/需求'}]}))
        self.harness.root = self.workspace
        self.source = self.workspace / 'inputs/evidence.txt'

    def tearDown(self):
        self.tmp.cleanup()

    def problems(self):
        return self.harness.problems(self.scope, self.candidate)

    def test_preserved_bounded_chain_passes_using_relocated_bytes(self):
        self.old_source.write_text('unrelated original source')
        self.assertEqual([], self.problems())

    def test_relocated_source_sha_and_quote_still_bind(self):
        self.source.write_text(self.source.read_text().replace('persistExistingObject', 'changedOperation'))
        self.assertIn('SHA differs', '; '.join(self.problems()))

    def test_missing_relocated_source_does_not_use_valid_original(self):
        self.source.unlink()
        self.assertTrue(self.problems())

    def test_historical_absolute_source_index_reference_resolves_in_current_workspace(self):
        review = StrictReview(self.workspace, self.harness.facts)
        self.assertEqual([], review.references({'source_refs': [str(self.old_workspace / 'inputs/source-index.json') + '#SRC-001']}))

    def test_malformed_mapping_is_reported_as_a_gate_error(self):
        manifest = self.project / 'memory/path-relocations.json'
        manifest.write_text('{"schema":"unknown"}')
        review = StrictReview(self.workspace, self.harness.facts)
        self.assertIsNone(review.path(str(self.old_source)))
        self.assertEqual('invalid_evidence_relocation', review.errors[0]['code'])
        self.assertTrue(review.derived_trigger_errors('candidate-query', self.candidate,
                                                     '/source_evidence/0/locator', self.scope, self.harness.retained))


if __name__ == '__main__':
    unittest.main()
