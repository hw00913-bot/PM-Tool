"""Positive and adversarial tests of the shared gate, not production acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from operation_attribution import StrictReview, attribution_errors, scenario_coverage_errors
from analysis_contract import page_errors
from test_retained_operation_contract import fixture, feature_record, bind_retained_actions


class SharedContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='strict-source-gate-')
        self.root = Path(self.tmp.name)
        self.facts = fixture(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    @property
    def retained(self): return self.facts['features']['retained_capability_references'][0]
    @property
    def action(self): return self.facts['pages']['pages'][0]['actions'][0]
    @property
    def page(self): return self.facts['pages']['pages'][0]

    def review(self): return StrictReview(self.root, self.facts).run()
    def errors(self): return '\n'.join(e['message'] for e in self.review()['attribution_errors'])

    def write(self):
        from retained_operation_contract import FILES
        for key, relative in FILES.items():
            p = self.root/relative; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self.facts[key]), encoding='utf-8')
        p = self.root/'sequences/sequence-index.json';p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps({'sequences':[{'id':'SEQ-001','scenario_id':'SC-001'}]}))

    def canonical(self):
        candidate = dict(copy.deepcopy(self.retained), catalog_key='candidate-query', formal_function_id=None,
                         catalog_role='local_candidate_not_scope_or_function_approval',counts_as_version_delta=False)
        self.facts['features']['observed_original_operation_candidates']=[candidate]
        self.facts['issues']['items']=[{'id':'ISSUE-001','status':'open','severity':'high'}]
        self.retained['draft_key']='canonical-query'
        self.retained['catalog_role']='canonical_reviewed_original_operation_reference'
        self.retained['issue_ids']=['ISSUE-001']
        self.action['retained_operation_refs']=['canonical-query']
        self.page['retained_operation_refs']=['canonical-query']
        source=self.root/'inputs/evidence.txt'
        self.retained['local_source_evidence'][0]['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        review={'origin_candidate_keys':['candidate-query'],'reviewed_by':'independent source reviewer',
                'reviewed_on':'2026-10-19','decision':'unchanged_original_operation_reference',
                'verification_scope':'source_attribution_only','source_refs':['inputs/source-index.json#SRC-001'],
                'scope_basis_acceptance_ids':['AC-001'],
                'candidate_record_sha256':{'candidate-query':self.candidate_hash(candidate)},
                'field_comparison':{},'conditional_inclusion':{'scope':'source_existing_only',
                    'placeholder_excluded':True,'production_verified':False,'final_baseline_verified':False,
                    'pending_baseline_issue_ids':['ISSUE-001']}}
        for f in ('business_object','primary_action','trigger','completion_result'):
            review['field_comparison'][f]={'candidate_key':'candidate-query','candidate_field':f,
                    'candidate_value':candidate[f],'canonical_value':self.retained[f],
                    'assessment':'same_original_operation','rationale':'Same independently located source operation, no new process.'}
        self.retained['retained_scope_review']=review
        bind_retained_actions(self.root, self.facts)
        return review,candidate

    @staticmethod
    def candidate_hash(c):return hashlib.sha256(json.dumps(c,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def nav(self):
        f=feature_record();f['id']='FUNC-001';f['operation_key']='navigate-authorized-location'
        f['boundary']['business_object']='当前工作位置';f['source_scenario_ids']=['SC-001']
        self.facts['features']['features']=[f];self.page['function_ids']=['FUNC-001']
        self.action['retained_operation_refs']=[];self.action['source_function_ids']=['FUNC-001']
        return f

    def test_legitimate_retained_query_needs_no_fake_FUNC_or_production_call(self):
        self.write();self.assertEqual([],page_errors(self.root,final=True))
        r=self.review();self.assertEqual(['existing-query'],r['validated_retained_keys_with_direct_ownership'])
        self.assertFalse(r['action_checks'][0]['production_or_delivery_complete'])

    def test_candidate_review_with_preserved_origin_and_exact_source_bindings_is_legal(self):
        self.canonical();self.assertFalse(self.review()['attribution_errors'])
        self.assertFalse(self.retained['production_verified'])
        self.assertIsNone(self.retained['formal_function_id'])

    def test_candidate_cannot_be_laundered_by_catalog_role_alone(self):
        self.canonical();self.retained.pop('retained_scope_review')
        self.assertIn('needs existing origin_candidate_keys',self.errors())

    def test_candidate_record_drift_invalidates_canonical_review(self):
        review,candidate=self.canonical();candidate['completion_result']='An unrelated newly published object'
        self.assertIn('candidate SHA differs',self.errors())

    def test_review_cannot_silently_promote_the_original_candidate(self):
        review,candidate=self.canonical();candidate['catalog_role']='confirmed';review['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        self.assertIn('Origin candidate role must remain',self.errors())

    def test_canonical_review_cannot_use_an_unconfirmed_scope_AC(self):
        self.canonical();self.facts['stories']['user_stories'][0]['acceptance_criteria'][0]['decision_status']='design_candidate'
        self.assertIn('confirmed existing-scope acceptance basis',self.errors())

    def test_review_cannot_fabricate_the_original_field_values(self):
        review,_=self.canonical();review['field_comparison']['primary_action']['candidate_value']='Delete'
        self.assertIn('concrete object/action/trigger/result comparison',self.errors())

    def test_missing_current_source_hash_rejects_reviewed_canonical(self):
        self.canonical();self.retained['local_source_evidence'][0].pop('source_sha256')
        self.assertIn('requires current source SHA',self.errors())

    def test_placeholder_is_not_made_usable_by_a_signed_review(self):
        review,candidate=self.canonical();candidate['evidence_level']='placeholder_or_simulated_completion_only'
        review['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        self.assertIn('Placeholder cannot become usable',self.errors())

    def test_conditional_inclusion_cannot_close_the_unknown_baseline(self):
        self.canonical();self.facts['issues']['items'][0]['status']='resolved'
        self.assertIn('explicitly linked to its open issues',self.errors())

    def test_unregistered_or_fabricated_audit_provenance_fails(self):
        review,_=self.canonical();review['source_refs']=['inputs/source-index.json#SRC-999']
        self.assertIn('Unregistered source ID',self.errors())

    def test_known_SC_but_wrong_story_to_SC_relationship_fails(self):
        self.facts['stories']['user_stories'].append({'id':'US-002','acceptance_criteria':[{'id':'AC-002','statement':'Other','decision_status':'confirmed'}]})
        self.retained['source_story_ids']=['US-002'];self.retained['source_acceptance_ids']=['AC-002']
        self.assertIn('does not belong to its cited scenario scope',self.errors())

    def test_navigation_FUNC_on_business_page_is_not_coverage_even_with_same_SC(self):
        self.nav();self.assertIn('Navigation FUNC alone cannot cover',self.errors())

    def test_explicit_navigation_detail_needs_real_source_and_scope_proof(self):
        self.nav();self.action['navigation_detail_of']={'function_id':'FUNC-001','kind':'navigation_detail',
            'counts_as_business_operation':False,'target_page_ids':['PAGE-001'],
            'target_permissions_independent':'Destination permissions remain independent',
            'operation_key':'navigate-authorized-location','source_scenario_ids':['SC-001'],
            'source_refs':['inputs/source-index.json#SRC-001']}
        self.assertFalse(self.review()['attribution_errors'])
        self.action['navigation_detail_of']['source_refs']=['inputs/source-index.json#SRC-999']
        self.assertIn('Navigation FUNC alone cannot cover',self.errors())

    def test_matching_shared_shell_navigation_scope_does_not_need_a_fake_business_FUNC(self):
        self.nav();self.page['surface_kind']='shared_shell';self.assertFalse(self.review()['attribution_errors'])

    def test_only_retained_reference_does_not_close_a_partly_unresolved_SC(self):
        self.action['unresolved_operation_refs']=['missing-delete']
        self.facts['features']['unresolved_operation_boundaries']=[{'draft_key':'missing-delete','source_scenario_ids':['SC-001']}]
        self.write();self.assertTrue(scenario_coverage_errors(self.root,set(),{'SC-001'}))
        self.assertTrue(attribution_errors(self.root))

    def test_unlinked_backend_boundary_cannot_disappear_for_lack_of_a_button(self):
        self.facts['features']['unresolved_operation_boundaries']=[{'draft_key':'unknown-receipt','source_scenario_ids':['SC-001']}]
        self.write();self.assertTrue(any('unknown-receipt' in e for e in attribution_errors(self.root)))

    def test_relabeling_page_as_history_without_declaration_does_not_bypass_gate(self):
        self.page.update(lifecycle_status='retired_source',menu_path=[],is_current_bearing=False)
        self.assertIn('explicitly declared as retired source',self.errors())

    def test_current_page_catalog_must_not_include_a_retired_source(self):
        self.page.update(lifecycle_status='retired_source',menu_path=[],is_current_bearing=False)
        self.facts['pages']['retired_source_page_ids']=['PAGE-001'];self.facts['pages']['current_page_ids']=['PAGE-001']
        self.assertTrue(any(e['code']=='current_catalog_mismatch' for e in self.review()['attribution_errors']))

    def test_adding_non_product_kind_alone_cannot_skip_business_ownership(self):
        self.action['retained_operation_refs']=[];self.page['retained_operation_refs']=[]
        self.page['kind']='non_product_source_only';self.action['kind']='non_product_source_only'
        self.assertIn('Empty/unowned action',self.errors())

    def test_source_snapshot_must_be_hash_bound(self):
        self.facts['sources']['sources'][0].pop('sha256')
        self.assertIn('needs a SHA-256 binding',self.errors())

    def test_partial_reference_does_not_grant_any_destination_permission(self):
        result=self.review();self.assertTrue(all(not row['grants_any_permission'] for row in result['action_checks']))

if __name__=='__main__':unittest.main()
