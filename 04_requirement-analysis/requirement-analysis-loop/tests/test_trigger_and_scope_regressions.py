"""Generic independent-review regressions and safe derived-trigger proofs."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from operation_attribution import json_pointer_value, JSONPointerError, action_signature_sha256, scenario_coverage_errors
from analysis_contract import page_errors
from feature_contract import feature_boundary_errors
import test_shared_attribution_contract as base_contract
from test_retained_operation_contract import bind_retained_actions, feature_record


class TriggerAndScopeRegressions(unittest.TestCase):
    setUp = base_contract.SharedContractTests.setUp
    tearDown = base_contract.SharedContractTests.tearDown
    retained = base_contract.SharedContractTests.retained
    action = base_contract.SharedContractTests.action
    page = base_contract.SharedContractTests.page
    review = base_contract.SharedContractTests.review
    errors = base_contract.SharedContractTests.errors
    write = base_contract.SharedContractTests.write
    canonical = base_contract.SharedContractTests.canonical
    candidate_hash = staticmethod(base_contract.SharedContractTests.candidate_hash)

    def cloud_trigger(self):
        review,candidate=self.canonical()
        source=self.root/'inputs/evidence.txt'
        source.write_text('async function reloadExistingRecords() {\n'
                          '  return queryExistingRecords();\n'
                          '}\n'
                          'function render() { return `<button onclick="reloadExistingRecords()">Refresh</button>`; }\n'
                          'reloadExistingRecords();\n'
                          'function unrelatedAction() {}\n'
                          'console.log("reloadExistingRecords()");\n',encoding='utf-8')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        self.facts['sources']['sources'][0]['sha256']=digest
        candidate.pop('trigger')
        candidate['source_evidence']=[{'path':str(source),'source_sha256':digest,'line':1,
                                     'locator':'async function reloadExistingRecords() {'}]
        self.retained['trigger']='Authorized user refreshes the existing record list'
        review['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        review['field_comparison']['trigger']={'candidate_key':'candidate-query',
            'candidate_field':'/source_evidence/0/locator','candidate_value':candidate['source_evidence'][0]['locator'],
            'canonical_value':self.retained['trigger'],'assessment':'same_original_operation',
            'rationale':'The original control invokes this existing query; the locator is not a business trigger.'}
        proof={'candidate_key':'candidate-query','candidate_source_pointer':'/source_evidence/0',
            'source_refs':['inputs/source-index.json#SRC-001'],'file':str(source),'source_sha256':digest,
            'line_start':4,'line_end':4,'source_quote':source.read_text().splitlines()[3],
            'trigger_kind':'ui_control','target_symbol':'reloadExistingRecords',
            'business_trigger':self.retained['trigger'],
            'rationale':'Original Refresh control invokes this same existing query handler.'}
        review['derived_trigger_evidence']=proof
        bind_retained_actions(self.root,self.facts)
        return review,candidate,proof,source

    def test_valid_cloud_locator_pointer_requires_and_accepts_actual_control_quote(self):
        self.cloud_trigger();self.assertFalse(self.review()['attribution_errors'])

    def test_actual_operation_callsite_is_valid_but_definition_alone_is_not(self):
        review,candidate,proof,source=self.cloud_trigger()
        proof.update(line_start=5,line_end=5,source_quote=source.read_text().splitlines()[4],trigger_kind='operation_callsite')
        self.assertFalse(self.review()['attribution_errors'])
        proof.update(line_start=1,line_end=1,source_quote=source.read_text().splitlines()[0])
        self.assertIn('only a declaration/name',self.errors())

    def test_pointer_cannot_supply_missing_trigger_without_derived_evidence(self):
        review,_,_,_=self.cloud_trigger();review.pop('derived_trigger_evidence')
        self.assertIn('Missing derived_trigger_evidence',self.errors())

    def test_pointer_missing_property_stays_unresolved(self):
        review,_,_,_=self.cloud_trigger();review['field_comparison']['trigger']['candidate_field']='/source_evidence/0/missing'
        self.assertIn('Invalid canonical source JSON pointer',self.errors())

    def test_pointer_out_of_bounds_stays_unresolved(self):
        review,_,proof,_=self.cloud_trigger();review['field_comparison']['trigger']['candidate_field']='/source_evidence/99/locator';proof['candidate_source_pointer']='/source_evidence/99'
        self.assertIn('index out of range',self.errors())

    def test_pointer_never_evaluates_attribute_or_expression_text(self):
        for pointer in ['/source_evidence/-1/locator','/source_evidence/01/locator','/source_evidence/0/__class__','/source_evidence/0/locator/execute()']:
            with self.subTest(pointer=pointer):
                with self.assertRaises(JSONPointerError):json_pointer_value({'source_evidence':[{'locator':'fn'}]},pointer)
        self.assertEqual('literal',json_pointer_value({'a/b':{'~value':'literal'}},'/a~1b/~0value'))

    def test_trigger_quote_cannot_borrow_another_source_file(self):
        _,_,proof,source=self.cloud_trigger();other=self.root/'inputs/another-file.txt';other.write_text(source.read_text())
        proof.update(file=str(other),source_sha256=hashlib.sha256(other.read_bytes()).hexdigest())
        self.assertIn('different source file',self.errors())

    def test_trigger_quote_cannot_use_another_registered_source(self):
        _,_,proof,_=self.cloud_trigger();proof['source_refs']=['inputs/source-index.json#SRC-002']
        self.assertIn('do not belong to its original candidate/source',self.errors())

    def test_trigger_cannot_borrow_a_different_evidence_item(self):
        review,candidate,proof,_=self.cloud_trigger();candidate['source_evidence'].append(copy.deepcopy(candidate['source_evidence'][0]));review['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        proof['candidate_source_pointer']='/source_evidence/1'
        self.assertIn('differs from the field-comparison source',self.errors())

    def test_exact_quote_and_sha_are_required(self):
        _,_,proof,_=self.cloud_trigger();proof['source_quote']='  '+proof['source_quote']
        self.assertIn('does not match exact original lines',self.errors())
        proof['source_sha256']='0'*64
        self.assertIn('source file SHA differs',self.errors())

    def test_isolated_function_declaration_is_not_a_UI_control(self):
        _,_,proof,source=self.cloud_trigger();proof.update(line_start=1,line_end=3,source_quote='\n'.join(source.read_text().splitlines()[:3]))
        self.assertIn('lacks a real control binding',self.errors())

    def test_diagnostic_string_is_not_a_function_invocation(self):
        _,_,proof,source=self.cloud_trigger();proof.update(line_start=7,line_end=7,source_quote=source.read_text().splitlines()[6],trigger_kind='operation_callsite')
        self.assertIn('not an invocation',self.errors())

    def test_canonical_business_trigger_cannot_be_just_a_callable_name(self):
        review,_,proof,_=self.cloud_trigger();self.retained['trigger']='reloadExistingRecords()';proof['business_trigger']=self.retained['trigger'];review['field_comparison']['trigger']['canonical_value']=self.retained['trigger']
        self.assertIn('business initiation, not a callable',self.errors())

    def test_control_for_an_unrelated_handler_does_not_prove_the_original_trigger(self):
        _,_,proof,_=self.cloud_trigger();proof['target_symbol']='unrelatedAction'
        self.assertIn('not bound to its original locator',self.errors())

    def test_regular_FUNC_cannot_use_PAGE_broad_SC_to_bypass_ACTION_direct_SC(self):
        self.facts['features']['features']=[feature_record()];self.facts['features']['retained_capability_references']=[]
        self.facts['stories']['user_stories'].append({'id':'US-002','acceptance_criteria':[{'id':'AC-002','statement':'Other original workflow'}]})
        self.facts['scenarios']['scenarios'].append({'id':'SC-002','scope':'in_scope','source_story_ids':['US-002'],'source_acceptance_ids':['AC-002']})
        self.page.update(scenario_ids=['SC-001','SC-002'],function_ids=['FUNC-001'],retained_operation_refs=[])
        self.action.update(source_scenario_ids=['SC-002'],source_function_ids=['FUNC-001'],retained_operation_refs=[])
        self.assertIn('direct ACTION scenario scope',self.errors())

    def test_signed_query_binding_detects_ACTION_changed_to_delete_without_optional_object(self):
        self.action.update(name='Delete all existing records',permission='Delete records permission')
        self.assertNotIn('business_object',self.action)
        self.assertIn('signature changed after independent review',self.errors())

    def test_recomputing_inline_signature_cannot_override_registered_review(self):
        self.action.update(name='Delete all existing records',permission='Delete records permission')
        self.action['retained_operation_bindings'][0]['action_signature_sha256']=action_signature_sha256(self.page['id'],self.action)
        self.assertIn('not bound by its registered independent review snapshot',self.errors())

    def test_any_source_index_anchor_must_be_an_exact_registered_SRC(self):
        for ref in ['inputs/source-index.json#SRC-999-unregistered','inputs/./source-index.json#SRC-001-suffix','inputs/source-index.json#','inputs/source-index.json']:
            with self.subTest(ref=ref):
                self.retained['source_refs']=[ref]
                self.assertIn('Malformed source-index reference',self.errors())

    def test_SC_and_US_AC_coverage_follows_current_ACTION_not_whole_retained_record(self):
        self.facts['stories']['user_stories'].append({'id':'US-002','acceptance_criteria':[{'id':'AC-002','statement':'Other original workflow'}]})
        self.facts['scenarios']['scenarios'].append({'id':'SC-002','scope':'in_scope','source_story_ids':['US-002'],'source_acceptance_ids':['AC-002']})
        for k,ref in [('source_story_ids','US-002'),('source_acceptance_ids','AC-002'),('source_scenario_ids','SC-002')]:self.retained[k].append(ref)
        self.page['scenario_ids'].append('SC-002')
        coverage=self.review()['retained_coverage']
        self.assertNotIn('SC-002',coverage['source_scenario_ids']);self.assertNotIn('US-002',coverage['source_story_ids']);self.assertNotIn('AC-002',coverage['source_acceptance_ids'])

    def test_history_and_page_level_references_never_create_current_ACTION_coverage(self):
        self.page.update(lifecycle_status='retired_source',is_current_bearing=False,menu_path=[])
        self.action.update(capability_migration_ids=['MIG-001'],current_target_page_ids=['PAGE-002'])
        self.retained['current_bearing_page_ids']=['PAGE-002']
        self.facts['pages']['pages'].append({'id':'PAGE-002','name':'Current landing','menu_path':['Reports'],'scenario_ids':['SC-001'],'sequence_ids':['SEQ-001'],'function_ids':[],'retained_operation_refs':['existing-query'],'actions':[],
          'fragment_authorization':{'source_scope_preserved':True,'rule':'Original summary rights only'},'incoming_capability_migration_ids':['MIG-001']})
        self.facts['pages'].update(retired_source_page_ids=['PAGE-001'],current_page_ids=['PAGE-002'],capability_migrations=[{'migration_id':'MIG-001','source_page_id':'PAGE-001','source_fragment':'Original summary','target_page_ids':['PAGE-002'],'source_refs':['inputs/source-index.json#SRC-001'],
            'permission_policy':{'source_view_right':'Original summary','target_other_capabilities':'Independent query/detail/write rights'}}])
        result=self.review();self.assertEqual([],result['validated_retained_keys_with_direct_ownership'])
        self.assertEqual([],result['retained_coverage']['source_scenario_ids'])

    def test_every_retained_source_file_must_have_a_current_registered_hash_chain(self):
        other=self.root/'inputs/unregistered.txt';other.write_text('Totally unrelated file')
        self.retained['local_source_evidence'][0].update(file=str(other));self.retained['local_source_evidence'][0].pop('source_id')
        self.assertTrue('SHA differs' in self.errors() or 'registered snapshot file/SHA chain' in self.errors())
        self.retained['local_source_evidence'][0]['source_sha256']=hashlib.sha256(other.read_bytes()).hexdigest()
        self.assertIn('registered snapshot file/SHA chain',self.errors())

    def test_candidate_cannot_be_laundered_by_renaming_its_key(self):
        candidate=dict(copy.deepcopy(self.retained),catalog_key='pending-original-query',catalog_role='local_candidate_not_scope_or_function_approval',counts_as_version_delta=False,formal_function_id=None)
        self.facts['features']['observed_original_operation_candidates']=[candidate]
        self.retained['draft_key']='renamed-promoted-query';self.action['retained_operation_refs']=['renamed-promoted-query'];self.page['retained_operation_refs']=['renamed-promoted-query']
        self.assertIn('needs existing origin_candidate_keys',self.errors())

    def test_normal_FUNC_project_keeps_original_public_schema_compatibility(self):
        feature=feature_record();feature.pop('evidence_level');feature.pop('source_refs')
        self.facts['features']={'granularity_version':'business-operation@2','features':[feature]}
        self.facts['stories']['user_stories'][0]['acceptance_criteria'][0].pop('decision_status')
        self.page['function_ids']=['FUNC-001'];self.page.pop('retained_operation_refs');self.page.pop('unresolved_operation_refs')
        self.action['source_function_ids']=['FUNC-001'];self.action.pop('retained_operation_refs');self.action.pop('unresolved_operation_refs');self.action.pop('permission')
        self.write();self.assertEqual([],feature_boundary_errors(self.facts['features']));self.assertEqual([],page_errors(self.root,final=True))

    def test_ordinary_no_Wiki_project_may_omit_optional_source_and_issue_indexes(self):
        feature=feature_record();feature.pop('evidence_level');feature.pop('source_refs')
        self.facts['features']={'granularity_version':'business-operation@2','features':[feature]}
        self.page['function_ids']=['FUNC-001'];self.page.pop('retained_operation_refs');self.page.pop('unresolved_operation_refs')
        self.action['source_function_ids']=['FUNC-001'];self.action.pop('retained_operation_refs');self.action.pop('unresolved_operation_refs');self.action.pop('permission')
        self.write();(self.root/'inputs/source-index.json').unlink();(self.root/'runtime/open-items.json').unlink()
        self.assertEqual([],page_errors(self.root,final=True))

    def test_registered_snapshot_SHA_may_bind_retained_evidence_without_duplicate_field(self):
        self.retained['local_source_evidence'][0].pop('source_sha256')
        self.assertFalse(self.review()['attribution_errors'])

    def test_ordinary_non_Wiki_FUNC_does_not_gain_private_snapshot_hash_requirement(self):
        feature=feature_record();feature.pop('evidence_level')
        self.facts['features']={'granularity_version':'business-operation@2','features':[feature]}
        self.facts['sources']['sources'][0].pop('sha256');self.facts['sources']['sources'][0].pop('snapshot_path')
        self.page['function_ids']=['FUNC-001'];self.page.pop('retained_operation_refs');self.page.pop('unresolved_operation_refs')
        self.action['source_function_ids']=['FUNC-001'];self.action.pop('retained_operation_refs');self.action.pop('unresolved_operation_refs');self.action.pop('permission')
        self.write();self.assertEqual([],page_errors(self.root,final=True))

if __name__=='__main__':unittest.main()
