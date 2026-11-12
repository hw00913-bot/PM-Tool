"""Counterexamples for local attribution, not product acceptance tests.

python3 -B -m unittest discover -s runtime/validation -p 'test_retained_operation_contract.py' -v
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from operation_attribution import StrictReview as Review, action_signature_sha256
from retained_operation_contract import FILES


def fixture(root):
    (root / 'inputs').mkdir()
    (root / 'inputs/evidence.txt').write_text('query returns the authorized existing records\n', encoding='utf-8')
    source = {'id': 'SRC-001', 'status': 'verified', 'snapshot_path': 'inputs/evidence.txt',
              'sha256': hashlib.sha256((root / 'inputs/evidence.txt').read_bytes()).hexdigest()}
    source_refs = ['inputs/source-index.json#SRC-001']
    upstream = {'source_story_ids': ['US-001'], 'source_acceptance_ids': ['AC-001'], 'source_scenario_ids': ['SC-001']}
    retained = dict(copy.deepcopy(upstream), draft_key='existing-query', business_object='Existing records', primary_action='查询',
                    trigger='Authorized user opens list', completion_result='Authorized list is returned',
                    source_refs=source_refs, delivery_class='retained_reference_not_delta', formal_function_id=None,
                    local_source_evidence=[{'source_id': 'SRC-001', 'file': str(root / 'inputs/evidence.txt'),
                                            'line_start': 1, 'line_end': 1, 'observed': 'Returns records'}],
                    source_action_ids=['ACTION-001'], current_bearing_page_ids=['PAGE-001'], production_verified=False)
    action = {'id': 'ACTION-001', 'name': 'Query original list', 'source_scenario_ids': ['SC-001'],
              'source_function_ids': [], 'retained_operation_refs': ['existing-query'],
              'unresolved_operation_refs': [], 'permission': 'Existing independent query permission'}
    page = {'id': 'PAGE-001', 'name': 'Existing list', 'menu_path': ['Reports'], 'scenario_ids': ['SC-001'],
            'sequence_ids': ['SEQ-001'], 'function_ids': [], 'retained_operation_refs': ['existing-query'],
            'unresolved_operation_refs': [], 'lifecycle_status': 'active', 'actions': [action]}
    facts = {
        'features': {'granularity_version': 'business-operation@2', 'features': [],
                     'retained_capability_references': [retained], 'unresolved_operation_boundaries': [],
                     'observed_original_operation_candidates': [], 'original_operation_details_and_auxiliary_fragments': []},
        'pages': {'pages': [page], 'capability_migrations': []},
        'sources': {'sources': [source]},
        'stories': {'user_stories': [{'id': 'US-001', 'acceptance_criteria': [
            {'id': 'AC-001', 'statement': 'Preserve original query', 'decision_status': 'confirmed_direction', 'source_refs': source_refs}]}]},
        'scenarios': {'scenarios': [dict(copy.deepcopy(upstream), id='SC-001', scope='in_scope')]},
        'issues': {'items': []},
    }
    bind_retained_actions(root, facts)
    return facts


def bind_retained_actions(root, facts):
    """An independently registered fixture review, not a runtime bypass."""
    declarations = []
    retained = {r['draft_key']: r for r in facts['features'].get('retained_capability_references', [])}
    for record in retained.values():
        for e in record.get('local_source_evidence', []):
            p = Path(e.get('file', e.get('path')))
            e['source_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
    for page in facts['pages']['pages']:
        for action in page.get('actions', []):
            keys = list(action.get('retained_operation_refs', []))
            keys.extend(d['parent_ref'] for d in action.get('main_operation_details', []) if d.get('parent_ref') in retained and d['parent_ref'] not in keys)
            rows = []
            for key in keys:
                if key not in retained: continue
                record = retained[key]
                row = {'binding_kind':'retained_operation_binding','page_id':page['id'],'action_id':action['id'],
                       'operation_ref':key,'business_object':record['business_object'],'primary_action':record['primary_action'],
                       'source_scenario_ids':sorted(set(action['source_scenario_ids'])&set(record['source_scenario_ids'])),
                       'reviewed_by':'Independent fixture source reviewer','reviewed_on':'2026-10-19',
                       'action_signature_sha256':action_signature_sha256(page['id'], action),
                       'source_refs':['inputs/source-index.json#SRC-002']}
                rows.append(row);declarations.append(row)
            action['retained_operation_bindings'] = rows
    snapshot = root/'inputs/action-review.json'
    snapshot.write_text(json.dumps({'action_bindings':declarations},ensure_ascii=False),encoding='utf-8')
    source = {'id':'SRC-002','status':'verified','snapshot_path':'inputs/action-review.json',
              'sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest()}
    facts['sources']['sources'] = [s for s in facts['sources']['sources'] if s['id']!='SRC-002']+[source]


def feature_record():
    return {'id': 'FUNC-001', 'operation_key': 'receive-existing-result', 'version': '2.0.0',
            'module_l1': 'Results', 'module_l2': 'Receive', 'module_l3': None, 'name': 'Receive one result',
            'description': 'Validate and receive the exact existing result', 'notes': None, 'change_type': 'new',
            'evidence_level': 'confirmed_business_direction', 'source_refs': ['inputs/source-index.json#SRC-001'],
            'source_story_ids': ['US-001'], 'source_acceptance_ids': ['AC-001'], 'source_scenario_ids': ['SC-001'],
            'boundary': {'business_object': 'Exact result', 'primary_action': '接收', 'system_boundary': 'This service',
                         'initiator': 'Original external service', 'trigger': 'Exact callback arrives',
                         'primary_outcome': 'Exact result is retained', 'completion_result': 'Exact result accepted',
                         'split_rationale': 'Independent external trigger; has no user button',
                         'included_scope': ['Accept exact verified result'], 'excluded_scope': [], 'business_rules': [],
                         'exception_paths': [], 'dependencies': [], 'assumptions': []},
            'change_detail': {'before': None, 'after': 'One accepted result', 'impact': ['Receive result']}}


class AttributionCounterexamples(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='retained-contract-test-')
        self.root = Path(self.temp.name)
        self.facts = fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    @property
    def action(self):
        return self.facts['pages']['pages'][0]['actions'][0]

    @property
    def retained(self):
        return self.facts['features']['retained_capability_references'][0]

    def run_review(self):
        return Review(self.root, self.facts).run()

    def errors(self):
        return '\n'.join(e['message'] for e in self.run_review()['attribution_errors'])

    def test_retained_query_attributes_without_inventing_FUNC_or_production_success(self):
        result = self.run_review()
        self.assertFalse(result['attribution_errors'])
        self.assertEqual('attributed_reference_only', result['action_checks'][0]['status'])
        self.assertFalse(result['action_checks'][0]['production_or_delivery_complete'])
        self.assertEqual('original_baseline_or_production_unverified', result['business_scope_or_contract_pending'][0]['kind'])
        self.assertEqual([], self.facts['features']['features'])

    def test_undefined_retained_key_does_not_count_as_coverage(self):
        self.action['retained_operation_refs'] = ['typo']
        self.assertIn('Undefined retained key', self.errors())

    def test_observed_candidate_cannot_impersonate_retained_operation(self):
        candidate = dict(self.retained, catalog_key='unapproved-delete', observation_key='unapproved-delete')
        self.facts['features']['observed_original_operation_candidates'].append(candidate)
        self.action['retained_operation_refs'] = ['unapproved-delete']
        self.assertIn('Candidate/placeholder is not retained ownership', self.errors())

    def test_candidate_laundered_into_retained_catalog_still_fails(self):
        self.retained['catalog_role'] = 'local_candidate_not_scope_or_function_approval'
        self.assertIn('Candidate cannot be promoted', self.errors())

    def test_source_ID_cannot_be_fabricated(self):
        self.retained['source_refs'] = ['inputs/source-index.json#SRC-999']
        self.assertIn('Unregistered source ID', self.errors())

    def test_empty_source_record_is_rejected(self):
        self.retained['local_source_evidence'] = [{}]
        self.assertIn('Original operation source missing', self.errors())

    def test_stale_registered_snapshot_is_not_silently_accepted(self):
        (self.root / 'inputs/evidence.txt').write_text('different source now\n')
        self.assertIn('snapshot SHA differs', self.errors())

    def test_out_of_range_source_locator_fails(self):
        self.retained['local_source_evidence'][0]['line_end'] = 999
        self.assertIn('Evidence line range invalid', self.errors())

    def test_unrelated_action_cannot_borrow_same_SC_retained_operation(self):
        self.action['id'] = 'ACTION-002'
        self.assertIn('attached to an unrelated action', self.errors())

    def test_same_action_cannot_borrow_a_different_business_object(self):
        self.action['business_object'] = 'Unrelated billing write'
        self.assertIn('business objects disagree', self.errors())

    def test_changing_page_FUNC_list_cannot_launder_unrelated_scope(self):
        f = feature_record()
        f['source_scenario_ids'] = ['SC-002']
        self.facts['scenarios']['scenarios'].append({'id': 'SC-002', 'scope': 'in_scope'})
        self.facts['features']['features'] = [f]
        self.facts['pages']['pages'][0]['function_ids'] = ['FUNC-001']
        self.action['source_function_ids'] = ['FUNC-001']
        self.assertIn('FUNC is outside owner page business scope', self.errors())

    def test_write_permission_cannot_come_from_read_only_summary(self):
        self.retained['primary_action'] = '删除'
        self.action['permission'] = '只读摘要'
        self.assertIn('Write operation cannot be authorized', self.errors())

    def test_no_page_required_for_backend_operation(self):
        self.facts['features']['features'] = [feature_record()]
        self.facts['pages']['pages'] = []
        result = self.run_review()
        self.assertFalse(result['attribution_errors'])
        self.assertEqual([], result['page_checks'])
        self.assertEqual(1, result['counts']['delta_functions'])

    def test_empty_action_is_not_covered_by_valid_page_level_reference(self):
        self.action['retained_operation_refs'] = []
        self.assertIn('Empty/unowned action', self.errors())

    def test_prerequisite_source_view_is_not_main_operation_coverage(self):
        self.action['retained_operation_refs'] = []
        self.make_prerequisite()
        self.assertIn('Empty/unowned action', self.errors())

    def test_prerequisite_source_view_can_accompany_independent_confirmed_operation(self):
        self.action['retained_operation_refs'] = []
        self.make_prerequisite()
        self.action['source_function_ids'] = ['FUNC-001']
        self.facts['features']['features'] = [feature_record()]
        self.facts['pages']['pages'][0]['function_ids'] = ['FUNC-001']
        result = self.run_review()
        self.assertFalse(result['attribution_errors'])
        row = result['action_checks'][0]
        self.assertEqual(['existing-query'], row['prerequisite_refs_checked_not_main_coverage'])
        self.assertEqual([{'kind': 'confirmed_delta_reference', 'ref': 'FUNC-001'}], row['witnesses'])

    def test_adding_a_write_as_source_view_prerequisite_is_rejected(self):
        self.action['retained_operation_refs'] = []
        self.retained['primary_action'] = '删除'
        self.make_prerequisite()
        self.assertIn('prerequisite source-view reference cannot authorize a write', self.errors())

    def make_prerequisite(self):
        self.retained['source_action_ids'] = []
        self.retained['prerequisite_source_action_ids'] = ['ACTION-001']
        self.action['prerequisite_retained_operation_refs'] = ['existing-query']
        self.action['retained_prerequisite_details'] = [{
            'operation_ref': 'existing-query', 'purpose': 'Check exact original record before confirmation',
            'target_scenario_id': 'SC-001', 'scope_rule': 'Same authorized tenant and source object only',
            'permission': 'Original source-view right remains independently required',
            'counts_as_main_operation_coverage': False}]

    def test_prerequisite_action_ID_cannot_be_used_to_claim_primary_operation(self):
        self.retained['source_action_ids'] = []
        self.retained['prerequisite_source_action_ids'] = ['ACTION-001']
        self.assertIn('Retained reference is attached to an unrelated action', self.errors())

    def test_prerequisite_explicit_boundary_is_required(self):
        self.action['retained_operation_refs'] = []
        self.make_prerequisite()
        self.action['retained_prerequisite_details'][0]['counts_as_main_operation_coverage'] = True
        self.assertIn('non-coverage boundary', self.errors())

    def test_same_reference_cannot_be_both_prerequisite_and_main_coverage(self):
        self.make_prerequisite()
        self.retained['source_action_ids'] = ['ACTION-001']
        self.assertIn('both main coverage and prerequisite', self.errors())

    def test_known_unresolved_scope_is_pending_rather_than_false_success(self):
        self.action['retained_operation_refs'] = []
        self.action['unresolved_operation_refs'] = ['maybe-export']
        self.facts['features']['unresolved_operation_boundaries'] = [{'draft_key': 'maybe-export'}]
        result = self.run_review()
        self.assertFalse(result['attribution_errors'])
        self.assertEqual('scope_pending', result['action_checks'][0]['status'])

    def test_unknown_boundary_is_an_error_instead_of_a_pending_escape_hatch(self):
        self.action['unresolved_operation_refs'] = ['invented']
        self.assertIn('Undefined unresolved boundary', self.errors())

    def test_unchanged_original_cannot_be_relabelled_as_new_delivery(self):
        self.retained['change_type'] = 'new'
        self.assertIn('incorrectly counted as version delta', self.errors())

    def test_AC_must_belong_to_the_cited_story(self):
        self.facts['stories']['user_stories'].append({'id': 'US-002', 'acceptance_criteria': []})
        self.retained['source_story_ids'] = ['US-002']
        self.assertIn('AC is not owned', self.errors())

    def test_necessary_detail_has_same_trigger_object_and_result(self):
        parent = self.retained
        self.action['retained_operation_refs'] = []
        detail = {k: parent[k] for k in ('business_object', 'trigger', 'completion_result')}
        detail.update(parent_ref='existing-query', necessary_same_transaction=True, rationale='Filtering is part of one returned list')
        self.action['main_operation_details'] = [detail]
        bind_retained_actions(self.root, self.facts)
        self.assertFalse(self.run_review()['attribution_errors'])
        detail['completion_result'] = 'Separately publish a new configuration'
        self.assertIn('Detail changes parent object/trigger/result', self.errors())

    def history(self):
        page = self.facts['pages']['pages'][0]
        page.update(lifecycle_status='retired_source', is_current_bearing=False, menu_path=[])
        self.action.update(capability_migration_ids=['MIG-001'], current_target_page_ids=['PAGE-002'])
        self.retained['current_bearing_page_ids'] = ['PAGE-002']
        self.facts['pages']['pages'].append({'id': 'PAGE-002', 'name': 'Target details and writes', 'menu_path': [],
            'scenario_ids': ['SC-001'], 'sequence_ids': ['SEQ-001'], 'function_ids': [],
            'retained_operation_refs': [], 'actions': [], 'fragment_authorization':
            {'source_scope_preserved': True, 'rule': 'Source summary only; details/export/write separately authorized'}})
        migration = {'migration_id': 'MIG-001', 'source_page_id': 'PAGE-001', 'source_fragment': 'Original summary',
                     'target_page_ids': ['PAGE-002'], 'source_refs': ['inputs/source-index.json#SRC-001'],
                     'permission_policy': {'source_view_right': 'Original summary right',
                                           'target_other_capabilities': 'Independent detail, export, and write rights'}}
        self.facts['pages']['capability_migrations'] = [migration]
        self.facts['pages']['retired_source_page_ids'] = ['PAGE-001']
        self.facts['pages']['current_page_ids'] = ['PAGE-002']
        self.facts['pages']['pages'][1]['incoming_capability_migration_ids'] = ['MIG-001']
        return migration

    def test_history_without_current_destination_fails(self):
        migration = self.history()
        migration['target_page_ids'] = ['PAGE-999']
        self.assertIn('no valid current destination', self.errors())

    def test_history_migration_must_not_grant_target_write_rights(self):
        migration = self.history()
        migration['permission_policy']['grant_target_permissions'] = True
        self.assertIn('cannot grant target details/write rights', self.errors())

    def test_valid_history_summary_does_not_cover_target_detail_or_write(self):
        self.history()
        self.facts['pages']['pages'][1]['actions'] = [{'id': 'ACTION-002', 'source_scenario_ids': ['SC-001'],
            'source_function_ids': [], 'retained_operation_refs': [], 'permission': 'Source summary right'}]
        result = self.run_review()
        self.assertEqual('attributed_reference_only', result['action_checks'][0]['status'])
        self.assertFalse(result['action_checks'][0]['grants_any_permission'])
        self.assertEqual('invalid', result['action_checks'][1]['status'])

    def test_official_FUNC_schema_error_still_reported(self):
        f = feature_record()
        f['change_type'] = 'retained'
        self.facts['features']['features'] = [f]
        self._write_bundle()
        from feature_contract import feature_boundary_errors
        self.assertTrue(any('change_type' in e for e in feature_boundary_errors(self.facts['features'])))
        self.assertTrue(any('change_type' in e['message'] for e in self.run_review()['attribution_errors']))

    def test_legal_retained_empty_FUNC_array_is_valid_without_becoming_a_FUNC(self):
        self._write_bundle()
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        from analysis_contract import page_errors
        self.assertEqual([], page_errors(self.root, final=True))
        self.assertEqual([], self.facts['features']['features'])
        self.assertFalse(self.run_review()['action_checks'][0]['production_or_delivery_complete'])
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def _write_bundle(self):
        from retained_operation_contract import FILES
        for key, rel in FILES.items():
            p = self.root / rel
            p.parent.mkdir(exist_ok=True, parents=True)
            p.write_text(json.dumps(self.facts[key]), encoding='utf-8')
        p = self.root / 'sequences/sequence-index.json'
        p.parent.mkdir()
        p.write_text(json.dumps({'sequences': [{'id': 'SEQ-001', 'scenario_id': 'SC-001'}]}))
        (self.root / 'outputs').mkdir()
        from feature_contract import feature_view_marker, load_acceptance
        marker = feature_view_marker(self.facts['features'], load_acceptance(self.root))
        (self.root / 'outputs/feature-list.md').write_text(marker)


if __name__ == '__main__':
    unittest.main()
