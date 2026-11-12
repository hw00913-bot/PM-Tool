"""Narrow source proof grammar: actual callee/binding, never token co-occurrence."""
import hashlib
import unittest
from test_retained_operation_contract import bind_retained_actions
import test_trigger_and_scope_regressions as regression_fixture
from operation_attribution import supported_ui_binding, single_call_statement


class BoundedTriggerGrammarTests(unittest.TestCase):
    setUp = regression_fixture.TriggerAndScopeRegressions.setUp
    tearDown = regression_fixture.TriggerAndScopeRegressions.tearDown
    retained = regression_fixture.TriggerAndScopeRegressions.retained
    action = regression_fixture.TriggerAndScopeRegressions.action
    page = regression_fixture.TriggerAndScopeRegressions.page
    review = regression_fixture.TriggerAndScopeRegressions.review
    errors = regression_fixture.TriggerAndScopeRegressions.errors
    canonical = regression_fixture.TriggerAndScopeRegressions.canonical
    candidate_hash = staticmethod(regression_fixture.TriggerAndScopeRegressions.candidate_hash)
    cloud_trigger = regression_fixture.TriggerAndScopeRegressions.cloud_trigger

    def source_proof(self, text, kind='ui_control', owner_text=None, callee=None):
        review,candidate,proof,source=self.cloud_trigger()
        lines=source.read_text().splitlines()
        owner_start=len(lines)+1
        if owner_text:lines.extend(owner_text.splitlines())
        first=len(lines)+1;lines.extend(text.splitlines());source.write_text('\n'.join(lines)+'\n')
        digest=hashlib.sha256(source.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=digest
        candidate['source_evidence'][0]['source_sha256']=digest;review['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        proof.update(source_sha256=digest,line_start=first,line_end=len(lines),source_quote=text,trigger_kind=kind)
        if callee:proof['target_callee']=callee
        if owner_text:
            proof['owner_binding_evidence']={'file':str(source),'source_sha256':digest,
                'source_refs':['inputs/source-index.json#SRC-001'],'line_start':owner_start,
                'line_end':first-1,'source_quote':owner_text}
        bind_retained_actions(self.root,self.facts)
        return proof

    def assert_rejected(self,text,kind='ui_control'):
        self.source_proof(text,kind)
        self.assertTrue(self.review()['attribution_errors'])
        self.assertEqual([],self.review()['validated_retained_keys_with_direct_ownership'])

    def test_ui_diagnostic_attribute_string_is_not_an_event_binding(self):
        self.assert_rejected('const diagnostic = \'onclick="reloadExistingRecords()"\';')

    def test_ui_unrelated_binding_plus_comment_is_not_the_original_action(self):
        self.assert_rejected('button.addEventListener("click", unrelatedAction); // reloadExistingRecords()')

    def test_ui_inline_block_comment_cannot_forge_binding(self):
        self.assert_rejected('let unused; /* onclick="reloadExistingRecords()" */')

    def test_ui_disjoint_binding_and_call_do_not_form_one_handler(self):
        self.assert_rejected('button.addEventListener("click", unrelatedAction);\nfunction helper() { reloadExistingRecords(); }')

    def test_named_function_expression_is_not_a_callsite(self):
        self.assert_rejected('const callback = function reloadExistingRecords() {};','operation_callsite')

    def test_object_method_syntax_is_not_a_callsite(self):
        self.assert_rejected('const otherObject = { reloadExistingRecords() {} };','operation_callsite')

    def test_different_receiver_is_not_the_same_bare_function(self):
        self.assert_rejected('OtherModule.reloadExistingRecords();','operation_callsite')

    def test_full_diagnostic_button_string_is_not_mounted_UI(self):
        self.assert_rejected('const diagnostic = \'<button onclick="reloadExistingRecords()">Fake</button>\';')

    def test_logged_button_markup_is_not_mounted_UI(self):
        self.assert_rejected('console.log(\'<button onclick="reloadExistingRecords()">Fake</button>\');')

    def test_raw_JS_button_fragment_has_no_UI_sink_proof(self):
        self.assert_rejected('<button onclick="reloadExistingRecords()">Raw fragment</button>')

    def test_real_other_handler_plus_script_call_is_not_same_attribute(self):
        self.assert_rejected('function render() { return `<button onclick="unrelatedAction()">Other</button><script>reloadExistingRecords()</script>`; }')

    def test_intermediate_or_dynamic_render_data_remains_unproven(self):
        self.assert_rejected('function render() { const html = `<button onclick="reloadExistingRecords()">Fake</button>`; return html; }')

    def test_arbitrary_innerHTML_object_is_not_a_document_target(self):
        self.assert_rejected('diagnostic.innerHTML = `<button onclick="reloadExistingRecords()">Fake</button>`;')

    def test_unrecognized_sink_owner_is_not_assumed_UI(self):
        self.assert_rejected('Diagnostics.openLayer({body: `<button onclick="reloadExistingRecords()">Fake</button>`});')

    def test_exact_listener_reference_is_supported(self):
        self.source_proof('button.addEventListener("click", reloadExistingRecords);')
        self.assertFalse(self.review()['attribution_errors'])

    def test_exact_expression_arrow_listener_is_supported(self):
        self.source_proof('button.addEventListener("click", (event) => reloadExistingRecords(event));')
        self.assertFalse(self.review()['attribution_errors'])

    def test_block_arrow_with_other_statements_is_intentionally_unsupported(self):
        self.assert_rejected('button.addEventListener("click", (event) => { unrelatedAction(); reloadExistingRecords(event); });')

    def test_exact_event_property_reference_is_supported(self):
        self.source_proof('button.onclick = reloadExistingRecords;')
        self.assertFalse(self.review()['attribution_errors'])

    def test_explicit_document_innerHTML_sink_is_supported(self):
        self.source_proof('document.getElementById("records").innerHTML = `<button onclick="reloadExistingRecords()">Refresh</button>`;')
        self.assertFalse(self.review()['attribution_errors'])

    def test_known_layer_body_sink_is_supported(self):
        self.source_proof('UI.openLayer({title: "Records", body: `<button onclick="reloadExistingRecords()">Refresh</button>`});')
        self.assertFalse(self.review()['attribution_errors'])

    def test_qualified_call_requires_exact_owner_binding(self):
        self.source_proof('SourceOwner.query();','operation_callsite',callee='SourceOwner.query')
        self.assertIn('needs an exact registered owner binding',self.errors())

    def test_direct_owner_assignment_proves_exact_qualified_call(self):
        self.source_proof('SourceOwner.query();','operation_callsite',
            owner_text='SourceOwner.query = reloadExistingRecords;',callee='SourceOwner.query')
        self.assertFalse(self.review()['attribution_errors'])

    def test_static_object_owner_binding_is_supported(self):
        self.source_proof('SourceOwner.query();','operation_callsite',
            owner_text='const SourceOwner = { query: reloadExistingRecords };',callee='SourceOwner.query')
        self.assertFalse(self.review()['attribution_errors'])

    def test_owner_binding_for_another_function_does_not_prove_the_original(self):
        self.source_proof('SourceOwner.query();','operation_callsite',
            owner_text='SourceOwner.query = unrelatedAction;',callee='SourceOwner.query')
        self.assertIn('outside supported direct-assignment/static-object grammar',self.errors())

    def test_qualified_handler_uses_its_own_bound_owner(self):
        self.source_proof('button.addEventListener("click", SourceOwner.query);',
            owner_text='SourceOwner.query = reloadExistingRecords;',callee='SourceOwner.query')
        self.assertFalse(self.review()['attribution_errors'])

    def test_html_parser_ignores_handler_looking_text_in_script(self):
        full='<html><script>const diagnostic = \'<button onclick="reloadExistingRecords()">Fake</button>\';</script></html>'
        from pathlib import Path
        self.assertFalse(supported_ui_binding(full,'reloadExistingRecords',Path('/not-used.html'),1,1,full))

    def test_bare_call_does_not_match_optional_or_computed_dispatch(self):
        self.assertIsNone(single_call_statement('registry["reloadExistingRecords"]();'))
        self.assertIsNone(single_call_statement('registry?.reloadExistingRecords();'))

if __name__=='__main__':unittest.main()
