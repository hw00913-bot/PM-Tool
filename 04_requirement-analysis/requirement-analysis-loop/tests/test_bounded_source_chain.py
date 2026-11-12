"""Finite source-chain positives and attacks; no source JavaScript is run."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from operation_attribution import StrictReview
import test_shared_attribution_contract as fixtures
from test_retained_operation_contract import bind_retained_actions


LAYER_SOURCE = '''(function(){
const ui=PlatformUI;
function openForm(){
  ui.openLayer('edit',`<p>${label}</p><button onclick="window.Pages['example'].save()">Save</button>`,'small');
}
function save(){
  return persistExistingObject();
}
window.Pages['example']={render,openForm,save};
function render(){ return `<button onclick="window.Pages['example'].openForm()">Edit</button>`; }
})();
'''
HELPER_SOURCE = '''(function(){
const ui=PlatformUI;
const action=(method,...args)=>`Pages['example'].${method}(${args.map(js).join(',')})`;
function render(){
  return `<section>${ui.toolbar('Header',`<button onclick="${action('save')}">Save</button>`)}</section>`;
}
function save(){
  return persistExistingObject();
}
Pages['example']={render,save};
})();
'''


class BoundedSourceChainTests(unittest.TestCase):
    setUp=fixtures.SharedContractTests.setUp
    tearDown=fixtures.SharedContractTests.tearDown
    retained=fixtures.SharedContractTests.retained
    action=fixtures.SharedContractTests.action
    page=fixtures.SharedContractTests.page
    canonical=fixtures.SharedContractTests.canonical
    candidate_hash=staticmethod(fixtures.SharedContractTests.candidate_hash)

    def ev(self,path,first,last=None,sid='SRC-001'):
        last=first if last is None else last
        return {'file':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'source_refs':['inputs/source-index.json#'+sid],'line_start':first,'line_end':last,
                'source_quote':'\n'.join(path.read_text().splitlines()[first-1:last])}

    def make(self,source=LAYER_SOURCE,entry_line=4,export_line=9,terminal_first=6,terminal_last=8,owner="window.Pages['example']",helper_line=None):
        scope,candidate=self.canonical();path=self.root/'inputs/evidence.txt';path.write_text(source)
        sha=hashlib.sha256(path.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=sha
        self.retained['local_source_evidence'][0]['source_sha256']=sha
        candidate.pop('trigger');candidate['source_evidence']=[{'file':str(path),'source_sha256':sha,'line':terminal_first,'locator':'function save('}]
        scope['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        self.retained['trigger']='Authorized operator saves the original object from its existing control'
        scope['field_comparison']['trigger']={'candidate_key':'candidate-query','candidate_field':'/source_evidence/0/locator','candidate_value':'function save(',
            'canonical_value':self.retained['trigger'],'assessment':'same_original_operation','rationale':'Original UI event and exact source chain, not production execution.'}
        entry={'kind':'template_event','callee':owner+'.save','event':'click','evidence':self.ev(path,entry_line),'ui_alias_evidence':self.ev(path,2),'opening_evidence':self.ev(path,10)}
        if helper_line:entry.update(handler_helper_evidence=self.ev(path,helper_line),helper_call={'name':'action','literal_method':'save'})
        proof=dict(self.ev(path,terminal_first),candidate_key='candidate-query',candidate_source_pointer='/source_evidence/0',target_symbol='save',trigger_kind='source_chain',business_trigger=self.retained['trigger'],rationale='Exact static source control binds original operation.')
        proof['source_chain']={'version':'bounded-static-chain@1','entry':entry,'links':[{'kind':'static_owner_export','callee':owner+'.save','symbol':'save','evidence':self.ev(path,export_line)}],
            'terminal':{'symbol':'save','evidence':self.ev(path,terminal_first,terminal_last)}}
        scope['derived_trigger_evidence']=proof;bind_retained_actions(self.root,self.facts)
        return proof,scope,candidate,path

    def problems(self,scope,candidate):
        r=StrictReview(self.root,self.facts)
        return r.derived_trigger_errors('candidate-query',candidate,'/source_evidence/0/locator',scope,self.retained)

    def assert_pending(self,scope,candidate,fragment=None):
        errors=self.problems(scope,candidate);self.assertTrue(errors)
        if fragment:self.assertIn(fragment,'\n'.join(errors))

    def test_layer_static_bracket_shorthand_same_IIFE(self):
        _,scope,candidate,_=self.make();self.assertEqual([],self.problems(scope,candidate))
        self.assertFalse(self.retained['production_verified'])

    def test_layer_different_quote_style_is_exact_same_static_key(self):
        source=LAYER_SOURCE.replace("window.Pages['example'].save()",'window.Pages[\'example\'].save()')
        p,s,c,_=self.make(source);p['source_chain']['entry']['callee']='window.Pages["example"].save';self.assertEqual([],self.problems(s,c))

    def test_fixed_helper_inside_bound_toolbar_returned_template(self):
        _,s,c,_=self.make(HELPER_SOURCE,5,10,7,9,"Pages['example']",3);self.assertEqual([],self.problems(s,c))

    def test_named_property_export_binding(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('{render,openForm,save}','{render,openForm,save:save}'));self.assertEqual([],self.problems(s,c))

    def test_freeze_export_binding(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('{render,openForm,save};','Object.freeze({render,openForm,save});'));self.assertEqual([],self.problems(s,c))

    def test_dynamic_parameters_do_not_change_static_method(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('.save()','.save(\'${row.id}\')'));self.assertEqual([],self.problems(s,c))

    def test_fake_button_in_diagnostic_string_never_a_UI_sink(self):
        source=LAYER_SOURCE.replace("ui.openLayer('edit',",'const diagnostic=').replace("`,'small');","`;")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_fake_button_in_console_log_not_a_UI_sink(self):
        source=LAYER_SOURCE.replace("ui.openLayer('edit',",'console.log(').replace("`,'small');","`);")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_event_text_comment_not_a_UI_sink(self):
        source=LAYER_SOURCE.replace("  ui.openLayer('edit',", "  // ui.openLayer('edit',")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_button_for_other_callee_plus_target_word_elsewhere_rejected(self):
        source=LAYER_SOURCE.replace('.save()','.other()').replace('Save</button>','save() Save</button>')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_same_method_other_owner_rejected(self):
        source=LAYER_SOURCE.replace("window.Pages['example'].save()","OtherModule.save()")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_dynamic_method_name_not_a_static_event(self):
        source=LAYER_SOURCE.replace("window.Pages['example'].save()","window.Pages['example'].${method}()")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_diagnostics_alias_cannot_imitate_real_ui_sink(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('ui=PlatformUI','ui=Diagnostics'));self.assert_pending(s,c,'const alias')

    def test_ui_alias_missing_keeps_layer_pending(self):
        p,s,c,_=self.make();p['source_chain']['entry'].pop('ui_alias_evidence');self.assert_pending(s,c,'UI entry lacks')

    def test_alias_from_other_source_scope_rejected(self):
        source=LAYER_SOURCE.replace('const ui=PlatformUI;','function unrelated(){const ui=PlatformUI;}')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'const alias')

    def test_only_definition_and_export_no_control_never_reachable(self):
        p,s,c,path=self.make();p['source_chain']['entry']['evidence']=self.ev(path,6,8);self.assert_pending(s,c,'UI entry lacks')

    def test_self_confirmation_recursion_not_initial_control(self):
        source=LAYER_SOURCE.replace("ui.openLayer('edit',`<p>${label}</p><button onclick=\"window.Pages['example'].save()\">Save</button>`,'small');",'save();')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_owner_must_map_exact_property_not_same_short_name(self):
        source=LAYER_SOURCE.replace('{render,openForm,save}','{render,openForm,save:other}')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'owner export')

    def test_no_cross_IIFE_local_symbol_guessing(self):
        source=LAYER_SOURCE.replace("window.Pages['example']={render,openForm,save};","(function(){window.Pages['example']={render,openForm,save};})();")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'lexical-scope')

    def test_method_declaration_not_original_named_implementation(self):
        source=LAYER_SOURCE.replace('function save(){','const object={save(){').replace('  return persistExistingObject();','  return persistExistingObject();').replace('\n}\nwindow.Pages','\n}};\nwindow.Pages')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'named implementation')

    def test_exact_hash_and_quote_required_for_each_link(self):
        p,s,c,_=self.make();p['source_chain']['links'][0]['evidence']['source_sha256']='0'*64;self.assert_pending(s,c,'SHA differs')

    def test_unregistered_source_reference_in_link_rejected(self):
        p,s,c,_=self.make();p['source_chain']['links'][0]['evidence']['source_refs']=['inputs/source-index.json#SRC-999'];self.assert_pending(s,c,'Unregistered')

    def test_malformed_anchor_in_link_rejected(self):
        p,s,c,_=self.make();p['source_chain']['entry']['evidence']['source_refs']=['inputs/source-index.json#SRC-001-suffix'];self.assert_pending(s,c,'Malformed')

    def test_terminal_must_cover_actual_implementation_body(self):
        p,s,c,path=self.make();p['source_chain']['terminal']['evidence']=self.ev(path,6);self.assert_pending(s,c,'terminal')

    def test_helper_wrong_owner_rejected(self):
        source=HELPER_SOURCE.replace("`Pages['example'].${method}","`OtherModule.${method}")
        _,s,c,_=self.make(source,5,10,7,9,"Pages['example']",3);self.assert_pending(s,c,'UI entry lacks')

    def test_helper_dynamic_literal_method_rejected(self):
        source=HELPER_SOURCE.replace("action('save')","action(currentMethod)")
        _,s,c,_=self.make(source,5,10,7,9,"Pages['example']",3);self.assert_pending(s,c,'UI entry lacks')

    def test_arbitrary_forwarding_function_not_ui_toolbar(self):
        source=HELPER_SOURCE.replace('ui.toolbar','Diagnostics.toolbar')
        _,s,c,_=self.make(source,5,10,7,9,"Pages['example']",3);self.assert_pending(s,c,'UI entry lacks')

    def test_unexported_render_is_not_UI_sink(self):
        source=HELPER_SOURCE.replace('{render,save}','{save}')
        _,s,c,_=self.make(source,5,10,7,9,"Pages['example']",3);self.assert_pending(s,c,'UI entry lacks')

    def test_raw_script_markup_does_not_prove_handler(self):
        source=LAYER_SOURCE.replace('<p>${label}</p><button','<script><button').replace('Save</button>','Save</button></script>')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'UI entry lacks')

    def test_unknown_chain_version_rejected(self):
        p,s,c,_=self.make();p['source_chain']['version']='approved-anything';self.assert_pending(s,c,'version')

    def make_cross_file(self,branch='alternate'):
        code="""(function(){
const ui=PlatformUI;
const runtime=()=>window.ExistingRuntime;
function openForm(){
  ui.openLayer('edit',`<button onclick="window.Pages['example'].save()">Save</button>`,'small');
}
function save(){
  const result=editing ? runtime().update(input) : runtime().commit(input);
}
window.Pages['example']={render,openForm,save};
function render(){return `<button onclick="window.Pages['example'].openForm()">Edit</button>`;}
})();
"""
        proof,scope,candidate,path=self.make(code,5,10,7,9)
        secondary=self.root/'inputs/runtime.js'
        secondary.write_text("""(function(){
function commit(input){
  return persistExistingObject(input);
}
window.ExistingRuntime=Object.freeze({commit});
})();
""")
        digest=hashlib.sha256(secondary.read_bytes()).hexdigest()
        self.facts['sources']['sources'].append({'id':'SRC-003','status':'verified','snapshot_path':'inputs/runtime.js','sha256':digest})
        self.retained['source_refs'].append('inputs/source-index.json#SRC-003')
        scope['source_refs'].append('inputs/source-index.json#SRC-003')
        candidate['source_evidence']=[{'file':str(secondary),'source_sha256':digest,'locator':'function commit('}]
        scope['field_comparison']['trigger']['candidate_value']='function commit('
        scope['candidate_record_sha256']['candidate-query']=self.candidate_hash(candidate)
        proof.update(self.ev(secondary,2,sid='SRC-003'),target_symbol='commit')
        chain=proof['source_chain']
        chain['entry']['opening_evidence']=self.ev(path,11)
        chain['links'].extend([
            {'kind':'callsite','from_symbol':'save','callee':'runtime().commit','branch':branch,'branch_condition':'editing','reachability':'conditional_only','evidence':self.ev(path,8)},
            {'kind':'factory_owner','factory_symbol':'runtime','owner':'window.ExistingRuntime','evidence':self.ev(path,3)},
            {'kind':'static_owner_export','callee':'window.ExistingRuntime.commit','symbol':'commit','evidence':self.ev(secondary,5,sid='SRC-003')}])
        chain['terminal']={'symbol':'commit','evidence':self.ev(secondary,2,4,'SRC-003')}
        return proof,scope,candidate,path,secondary

    def test_explicit_cross_file_factory_and_conditional_alternate_are_valid(self):
        p,s,c,_,_=self.make_cross_file();self.assertEqual([],self.problems(s,c))
        self.assertEqual('conditional_only',p['source_chain']['links'][1]['reachability'])

    def test_cross_file_wrong_branch_cannot_claim_same_operation(self):
        _,s,c,_,_=self.make_cross_file('consequent');self.assert_pending(s,c,'declared conditional operand')

    def test_cross_file_branch_cannot_claim_unconditional_execution(self):
        p,s,c,_,_=self.make_cross_file();p['source_chain']['links'][1]['branch']='unconditional';self.assert_pending(s,c,'complete direct call')

    def test_cross_file_wrong_factory_receiver_rejected(self):
        p,s,c,_,_=self.make_cross_file();p['source_chain']['links'][2]['owner']='window.OtherRuntime';self.assert_pending(s,c,'Factory owner requires')

    def test_cross_file_export_must_match_pending_exact_callee(self):
        p,s,c,_,_=self.make_cross_file();p['source_chain']['links'][3]['callee']='OtherModule.commit';self.assert_pending(s,c,'exact pending')

    def test_cross_file_unknown_source_cannot_supply_terminal(self):
        p,s,c,_,_=self.make_cross_file();p['source_chain']['terminal']['evidence']['source_refs']=['inputs/source-index.json#SRC-999'];self.assert_pending(s,c,'Unregistered')

    def test_cross_file_all_links_needed_no_just_definition_and_export(self):
        p,s,c,_,_=self.make_cross_file();p['source_chain']['links']=p['source_chain']['links'][:1]+p['source_chain']['links'][3:];self.assert_pending(s,c,'exact pending')

    def test_source_chain_ui_parameter_shadow_cannot_use_outer_ui_alias(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('function openForm(){','function openForm(ui){'));self.assert_pending(s,c,'shadowed')

    def test_source_chain_helper_parameter_shadow_cannot_borrow_outer_helper(self):
        _,s,c,_=self.make(HELPER_SOURCE.replace('function render(){','function render(action){'),5,10,7,9,"Pages['example']",3);self.assert_pending(s,c,'shadowed')

    def test_source_chain_later_static_owner_overwrite_is_not_unique_binding(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('})();',"window.Pages['example']={save:unrelated};\n})();"));self.assert_pending(s,c,'unique exact static owner')

    def test_window_owner_is_not_silently_treated_as_bare_owner(self):
        p,s,c,_=self.make();p['source_chain']['entry']['callee']="Pages['example'].save";p['source_chain']['links'][0]['callee']="Pages['example'].save";self.assert_pending(s,c,'owner export')

    def test_named_function_expression_not_an_outer_named_binding(self):
        source=LAYER_SOURCE.replace('function save(){','const unrelated=function save(){').replace('\n}\nwindow.Pages','\n};\nwindow.Pages')
        _,scope,candidate,_=self.make(source);self.assert_pending(scope,candidate,'named implementation')

    def test_different_module_same_short_name_cannot_bind_export(self):
        source=LAYER_SOURCE.replace('{render,openForm,save}','{render,openForm,save:OtherModule.save}')
        _,scope,candidate,_=self.make(source);self.assert_pending(scope,candidate,'owner export')

    def test_layer_without_render_opening_evidence_stays_pending(self):
        p,s,c,_=self.make();p['source_chain']['entry'].pop('opening_evidence');self.assert_pending(s,c,'structured source-chain')

    def test_layer_export_without_render_opening_control_is_not_entry(self):
        source=LAYER_SOURCE.replace('.openForm()','.unrelated()')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'opening control')

    def test_unused_unexported_layer_function_is_not_entry(self):
        source=LAYER_SOURCE.replace('{render,openForm,save}','{render,save}')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'Layer UI function')

    def test_direct_platform_ui_shadow_parameter_cannot_imitate_real_sink(self):
        source=LAYER_SOURCE.replace('function openForm(){','function openForm(PlatformUI){').replace('ui.openLayer','PlatformUI.openLayer')
        _,s,c,_=self.make(source);self.assert_pending(s,c,'shadowed')

    def test_target_property_rewrite_breaks_static_owner_proof(self):
        source=LAYER_SOURCE.replace('})();',"window.Pages['example'].save=unrelated;\n})();")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'rewritten or deleted')

    def test_target_property_deletion_breaks_static_owner_proof(self):
        source=LAYER_SOURCE.replace('})();',"delete window.Pages['example'].save;\n})();")
        _,s,c,_=self.make(source);self.assert_pending(s,c,'rewritten or deleted')

    def test_spread_may_override_target_and_is_not_static_export(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('{render,openForm,save}','{render,openForm,save,...unrelated}'));self.assert_pending(s,c,'spread')

    def test_computed_key_may_override_target_and_is_not_static_export(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace('{render,openForm,save}',"{render,openForm,save,['save']:unrelated}"));self.assert_pending(s,c,'computed')

    def test_implementation_symbol_rebind_breaks_static_identity(self):
        _,s,c,_=self.make(LAYER_SOURCE.replace("window.Pages['example']=", "save=unrelated;window.Pages['example']="));self.assert_pending(s,c,'rebound')

    def test_direct_ui_alias_shadow_in_second_comma_declaration_is_rejected(self):
        source=LAYER_SOURCE.replace('function openForm(){','function openForm(){const safe=1, PlatformUI=Diagnostics;').replace('ui.openLayer','PlatformUI.openLayer')
        _,scope,candidate,_=self.make(source);self.assert_pending(scope,candidate,'shadowed')

    def test_static_export_cannot_bind_a_shadowed_window_parameter(self):
        _,scope,candidate,_=self.make(LAYER_SOURCE.replace('(function(){','(function(window){'));self.assert_pending(scope,candidate,'shadowed')

    def test_freeze_must_not_resolve_to_shadowed_Object(self):
        source=LAYER_SOURCE.replace('const ui=PlatformUI;','const ui=PlatformUI,Object=Diagnostics;').replace('{render,openForm,save};','Object.freeze({render,openForm,save});')
        _,scope,candidate,_=self.make(source);self.assert_pending(scope,candidate,'shadowed')

    def test_dynamic_target_property_write_is_not_a_static_binding(self):
        source=LAYER_SOURCE.replace('})();',"window.Pages['example'][method]=unrelated;\n})();")
        _,scope,candidate,_=self.make(source);self.assert_pending(scope,candidate,'Dynamic owner property mutation')

    def test_unknown_ancestor_property_write_cannot_preserve_operation_owner(self):
        source=LAYER_SOURCE.replace('})();',"window.Pages[pageKey]=unrelated;\n})();")
        _,scope,candidate,_=self.make(source);self.assert_pending(scope,candidate,'Dynamic owner property mutation')

    def test_deferred_expression_arrow_call_cannot_be_direct_business_callsite(self):
        p,scope,candidate,path,_=self.make_cross_file()
        source=path.read_text().replace('  const result=editing ? runtime().update(input) : runtime().commit(input);','  const deferred=()=>\n  runtime().commit(input);')
        path.write_text(source);digest=hashlib.sha256(path.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=digest
        chain=p['source_chain'];chain['entry']['evidence']=self.ev(path,5);chain['entry']['ui_alias_evidence']=self.ev(path,2);chain['entry']['opening_evidence']=self.ev(path,12)
        chain['links'][0]['evidence']=self.ev(path,11)
        chain['links'][1].update(evidence=self.ev(path,9),branch='unconditional')
        chain['links'][2]['evidence']=self.ev(path,3)
        self.assert_pending(scope,candidate,'exact current named function')



# P1: finite table-render source provenance fixtures; no source JavaScript is run.
import copy
import hashlib
import json
import unittest

try:
    _p1_helpers = BoundedSourceChainTests
except NameError:
    import test_bounded_source_chain as _p1_helper_module
    _p1_helpers = _p1_helper_module.BoundedSourceChainTests


P1_TABLE_PAGE = '''(function(){
const ui=PlatformUI;
let view='agents';
function shell(key,rows,columns,primary,help){
  return `<section>${ui.table(columns,rows,{})}</section>`;
}
function renderAgents(){
  const rows=CloudCallData.agents;
  return `<main>${shell('agents',rows,[
    {key:'contactCenterIdentityId',label:'Agent',render:(v,row)=>`<button onclick="window.Pages['example'].openAgent('${v}')">View</button>`}
  ],'','')}</main>`;
}
function render(options){
  view=options?.view||view;
  if(view==='skills')return `<main>Skills</main>`;
  if(view==='sync')return `<main>Sync</main>`;
  return renderAgents();
}
function openAgent(id){
  const row=CloudCallData.agents.find(item=>item.contactCenterIdentityId===id);
  if(!row||!AppState.authorizeObject('',row))return;
  return showOriginalDetail(row);
}
window.Pages['example']={render,openAgent};
})();
'''

P1_TABLE_NAV = '''(function(){
const routes=[{key:'agents',page:'example',view:'agents'}];
window.navigateTo=function(key,options){
  const route=routes.find(row=>row.key===key);
  const page=window.Pages[route.page];
  const opts={view:route.view,...(options||{})};
  const content=document.getElementById('page-content');
  content.innerHTML=page.render(opts);
};
})();
'''

P1_TABLE_MENU_NAV = '''(function(){
const menu=[{key:'workspace',children:[{key:'agents',page:'example',view:'agents'}]}];
const routes={};
menu.forEach(group=>group.children.forEach(item=>{routes[item.key]={...item,parent:group.key};}));
function resolve(key){
  let route=routes[key];
  return {key,route};
}
window.navigateTo=function(key,options){
  const resolved=resolve(key);
  const route=resolved.route;
  const page=window.Pages[route.page];
  const opts={view:route.view,...(options||{})};
  const content=document.getElementById('page-content');
  content.innerHTML=page.render(opts);
};
})();
'''

P1_TABLE_UI = '''(function(){
function table(columns,rows,options){
  const body=rows.length?rows.map(row=>`<tr>${columns.map(col=>`<td>${col.render?col.render(row[col.key],row):row[col.key]}</td>`).join('')}</tr>`).join(''):'<tr><td>Empty</td></tr>';
  return `<table><tbody>${body}</tbody></table>`;
}
window.PlatformUI={table};
})();
'''


class BoundedTableReturnP1Tests(unittest.TestCase):
    """Only new cases; do not inherit/recount old BoundedSourceChainTests."""
    setUp = _p1_helpers.setUp
    tearDown = _p1_helpers.tearDown
    retained = _p1_helpers.retained
    action = _p1_helpers.action
    page = _p1_helpers.page
    canonical = _p1_helpers.canonical
    candidate_hash = staticmethod(_p1_helpers.candidate_hash)
    ev = _p1_helpers.ev
    problems = _p1_helpers.problems

    def p1_line(self, path, needle):
        matches = [i for i, line in enumerate(path.read_text().splitlines(), 1)
                   if needle in line]
        self.assertEqual(1, len(matches), (needle, matches))
        return matches[0]

    def p1_function(self, path, name):
        lines = path.read_text().splitlines()
        first = self.p1_line(path, 'function ' + name + '(')
        last = next(i for i in range(first + 1, len(lines) + 1)
                    if lines[i - 1].strip() == '}')
        return first, last

    def p1_fixture(self, page_source=P1_TABLE_PAGE, nav_source=P1_TABLE_NAV,
                   table_source=P1_TABLE_UI, entry_needle="label:'Agent'",
                   navigation_needle='window.navigateTo=',
                   route_resolution_symbol=None, opts_needle='const opts=',
                   opts_tail_needle=None):
        if not hasattr(self, '_p1_pristine_facts'):
            self._p1_pristine_facts = copy.deepcopy(self.facts)
        self.facts = copy.deepcopy(self._p1_pristine_facts)
        scope, candidate = self.canonical()
        path = self.root / 'inputs/evidence.txt'
        nav = self.root / 'inputs/p1-navigation.js'
        table = self.root / 'inputs/p1-table.js'
        for target, source in ((path, page_source), (nav, nav_source),
                               (table, table_source)):
            target.write_text(source)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.facts['sources']['sources'][0]['sha256'] = digest
        for source_id, source_path in (('SRC-003', nav), ('SRC-004', table)):
            self.facts['sources']['sources'] = [
                record for record in self.facts['sources']['sources']
                if record['id'] != source_id]
            self.facts['sources']['sources'].append({
                'id': source_id, 'status': 'verified',
                'snapshot_path': str(source_path.relative_to(self.root)),
                'sha256': hashlib.sha256(source_path.read_bytes()).hexdigest()})
        refs = ['inputs/source-index.json#SRC-001',
                'inputs/source-index.json#SRC-003',
                'inputs/source-index.json#SRC-004']
        self.retained['source_refs'] = refs[:]
        scope['source_refs'] = refs[:]
        first, last = self.p1_function(path, 'openAgent')
        candidate.pop('trigger', None)
        candidate['source_evidence'] = [{
            'file': str(path), 'source_sha256': digest, 'line': first,
            'locator': 'function openAgent('}]
        scope['candidate_record_sha256']['candidate-query'] = self.candidate_hash(candidate)
        self.retained['trigger'] = 'Authorized operator views the existing agent from its real row control'
        self.retained['local_source_evidence'] = [dict(
            self.ev(path, first, last), observed='Original guarded detail handler')]
        scope['field_comparison']['trigger'] = {
            'candidate_key': 'candidate-query',
            'candidate_field': '/source_evidence/0/locator',
            'candidate_value': 'function openAgent(',
            'canonical_value': self.retained['trigger'],
            'assessment': 'same_original_operation',
            'rationale': 'Real row control and returned rendering chain; not runtime permission or production.'}
        export = self.p1_line(path, "window.Pages['example']=")
        entry = self.p1_line(path, entry_needle)
        route = self.p1_line(nav, 'const routes=')
        nav_start = self.p1_line(nav, navigation_needle)
        opts = self.p1_line(nav, opts_tail_needle or opts_needle)
        sink = self.p1_line(nav, 'content.innerHTML=')
        table_first, table_last = self.p1_function(table, 'table')
        table_export = self.p1_line(table, 'window.PlatformUI=')
        provenance = {
            'version': 'bounded-table-return@1',
            'reachability': 'conditional_only',
            'route_key': 'agents', 'route_page': 'example', 'route_view': 'agents',
            'route_options_policy': 'default_route_view_no_override',
            'exported_render_symbol': 'render',
            'render_delegate_symbol': 'renderAgents',
            'view_parameter': 'options', 'view_variable': 'view',
            'column_key_literal': 'contactCenterIdentityId',
            'wrapper_symbol': 'shell', 'wrapper_columns_parameter': 'columns',
            'wrapper_columns_argument_index': 2,
            'wrapper_rows_parameter': 'rows', 'wrapper_rows_argument_index': 1,
            'table_sink_callee': 'PlatformUI.table',
            'table_columns_argument_index': 0, 'table_rows_argument_index': 1,
            'exact_event_target_symbol': 'openAgent',
            'event_object_argument_index': 0,
            'evidence': {
                'route_registration': self.ev(nav, route, sid='SRC-003'),
                'route_dispatch': self.ev(nav, nav_start, opts, sid='SRC-003'),
                'route_sink': self.ev(nav, sink, sid='SRC-003'),
                'render': self.ev(path, *self.p1_function(path, 'render')),
                'delegate': self.ev(path, *self.p1_function(path, 'renderAgents')),
                'wrapper': self.ev(path, *self.p1_function(path, 'shell')),
                'table_implementation': self.ev(table, table_first, table_last, sid='SRC-004'),
                'table_export': self.ev(table, table_export, sid='SRC-004')}}
        if route_resolution_symbol:
            provenance['evidence']['route_registration'] = self.ev(
                nav, self.p1_line(nav, 'const menu='),
                self.p1_line(nav, 'menu.forEach('), sid='SRC-003')
            provenance['evidence']['route_resolution'] = self.ev(
                nav, *self.p1_function(nav, route_resolution_symbol), sid='SRC-003')
        proof = dict(self.ev(path, first),
            candidate_key='candidate-query', candidate_source_pointer='/source_evidence/0',
            target_symbol='openAgent', trigger_kind='source_chain',
            business_trigger=self.retained['trigger'],
            rationale='Bounded original render/table callback, conditional only.')
        proof['source_chain'] = {
            'version': 'bounded-static-chain@1',
            'entry': {'kind': 'template_event',
                      'callee': "window.Pages['example'].openAgent", 'event': 'click',
                      'evidence': self.ev(path, entry),
                      'ui_alias_evidence': self.ev(path, 2),
                      'render_provenance': provenance},
            'links': [{'kind': 'static_owner_export',
                       'callee': "window.Pages['example'].openAgent", 'symbol': 'openAgent',
                       'evidence': self.ev(path, export)}],
            'terminal': {'symbol': 'openAgent', 'evidence': self.ev(path, first, last)}}
        scope['derived_trigger_evidence'] = proof
        # Use the same official binding helper, without importing old tests.
        from test_retained_operation_contract import bind_retained_actions
        bind_retained_actions(self.root, self.facts)
        return proof, scope, candidate, path, nav, table

    def p1_reject(self, scope, candidate):
        errors = self.problems(scope, candidate)
        self.assertTrue(errors, 'A non-rendered/misbound control must stay unproven')
        return errors

    def test_p1_actual_render_delegate_wrapper_table_callback(self):
        _, scope, candidate, *_ = self.p1_fixture()
        self.assertEqual([], self.problems(scope, candidate))

    def test_p1_event_can_use_its_exact_row_identity_property(self):
        source = P1_TABLE_PAGE.replace("openAgent('${v}')", "openAgent('${row.contactCenterIdentityId}')")
        _, scope, candidate, *_ = self.p1_fixture(page_source=source)
        self.assertEqual([], self.problems(scope, candidate))

    def test_p1_unused_column_or_unreachable_render_helper_rejected(self):
        variants = [
            P1_TABLE_PAGE.replace(
                "return `<main>${shell('agents',rows,[", "const unused=[").replace(
                "  ],'','')}</main>`;", "  ];\n  return `<main>${shell('agents',rows,[], '', '')}</main>`;"),
            P1_TABLE_PAGE.replace('  return renderAgents();', '  return `<main>No agent table</main>`;')]
        for source in variants:
            with self.subTest(source=source):
                _, scope, candidate, *_ = self.p1_fixture(page_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_table_result_must_reach_returned_page(self):
        variants = [
            P1_TABLE_PAGE.replace(
                '  return `<section>${ui.table(columns,rows,{})}</section>`;',
                '  ui.table(columns,rows,{});\n  return `<section>No table result</section>`;'),
            P1_TABLE_PAGE.replace(
                "  return `<main>${shell('agents',rows,[", "  const discarded=`<main>${shell('agents',rows,[").replace(
                "  ],'','')}</main>`;", "  ],'','')}</main>`;\n  return `<main>Other content</main>`;")]
        for source in variants:
            with self.subTest(source=source):
                _, scope, candidate, *_ = self.p1_fixture(page_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_columns_cannot_be_taken_from_the_wrong_argument_slot(self):
        source = P1_TABLE_PAGE.replace("shell('agents',rows,[", "shell('agents',[").replace(
            "  ],'','')}</main>`;", "  ],rows,'','')}</main>`;")
        _, scope, candidate, *_ = self.p1_fixture(page_source=source)
        self.p1_reject(scope, candidate)

    def test_p1_exact_column_and_event_object_identity_required(self):
        variants = [
            P1_TABLE_PAGE.replace("key:'contactCenterIdentityId'", "key:'otherId'"),
            P1_TABLE_PAGE.replace("openAgent('${v}')", "openAgent('${row.otherId}')"),
            P1_TABLE_PAGE.replace("openAgent('${v}')", "openAgent('fixed-unrelated-id')")]
        for source in variants:
            with self.subTest(source=source):
                _, scope, candidate, *_ = self.p1_fixture(page_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_fake_ui_and_wrapper_local_shadow_rejected(self):
        variants = [P1_TABLE_PAGE.replace('const ui=PlatformUI;', 'const ui=Diagnostics;'),
                    P1_TABLE_PAGE.replace('function shell(key,rows,columns,primary,help){',
                        'function shell(key,rows,columns,primary,help){\n  const ui=Diagnostics;')]
        for source in variants:
            with self.subTest(source=source):
                _, scope, candidate, *_ = self.p1_fixture(page_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_real_table_name_without_rendered_callback_is_not_a_sink(self):
        source = P1_TABLE_UI.replace(
            "  const body=rows.length?rows.map(row=>`<tr>${columns.map(col=>`<td>${col.render?col.render(row[col.key],row):row[col.key]}</td>`).join('')}</tr>`).join(''):'<tr><td>Empty</td></tr>';",
            "  const body='<tr><td>Diagnostic only</td></tr>';")
        _, scope, candidate, *_ = self.p1_fixture(table_source=source)
        self.p1_reject(scope, candidate)

    def test_p1_sequence_decoration_preserves_original_column_and_row_identity(self):
        decorated = P1_TABLE_UI.replace('function table(columns,rows,options){',
            "function table(columns,rows,options){\n  columns=[{key:'__seq',label:'Sequence'},...columns];\n  rows=rows.map((row,index)=>({...row,__seq:index+1}));")
        _, scope, candidate, *_ = self.p1_fixture(table_source=decorated)
        self.assertEqual([], self.problems(scope, candidate))
        destructive = [
            '  columns=[];',
            "  columns=columns.filter(col=>col.key!=='contactCenterIdentityId');",
            "  columns=[{key:'contactCenterIdentityId',label:'Ambiguous identity'},...columns];",
            "  rows=rows.map((row,index)=>({...row,contactCenterIdentityId:'fixed-unrelated-id'}));"]
        for transformation in destructive:
            with self.subTest(transformation=transformation):
                source = P1_TABLE_UI.replace('function table(columns,rows,options){',
                    'function table(columns,rows,options){\n' + transformation)
                _, scope, candidate, *_ = self.p1_fixture(table_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_route_view_page_and_default_options_are_not_claims(self):
        for field, wrong in [('route_view', 'skills'), ('route_page', 'another-page'),
                             ('route_options_policy', 'allow-any-override')]:
            with self.subTest(field=field):
                proof, scope, candidate, *_ = self.p1_fixture()
                proof['source_chain']['entry']['render_provenance'][field] = wrong
                self.p1_reject(scope, candidate)

    def test_p1_exact_event_owner_and_original_export_required(self):
        variants = [
            P1_TABLE_PAGE.replace("window.Pages['example'].openAgent('${v}')", "OtherPage.openAgent('${v}')"),
            P1_TABLE_PAGE.replace("window.Pages['example'].openAgent('${v}')", "window.Pages['example'].other('${v}')"),
            P1_TABLE_PAGE.replace('})();', "window.Pages['example'].openAgent=other;\n})();")]
        for source in variants:
            with self.subTest(source=source):
                _, scope, candidate, *_ = self.p1_fixture(page_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_columns_cannot_be_rewritten_before_the_real_table(self):
        source = P1_TABLE_PAGE.replace('function shell(key,rows,columns,primary,help){',
            "function shell(key,rows,columns,primary,help){\n  columns[0].render=()=>`<span>No button</span>`;")
        _, scope, candidate, *_ = self.p1_fixture(page_source=source)
        self.p1_reject(scope, candidate)

    def test_p1_source_quote_and_candidate_bindings_remain_strict(self):
        for attack in ['SHA', 'quote', 'candidate']:
            with self.subTest(attack=attack):
                proof, scope, candidate, *_ = self.p1_fixture()
                if attack == 'SHA':
                    proof['source_chain']['entry']['render_provenance']['evidence']['wrapper']['source_sha256'] = '0' * 64
                    errors = self.p1_reject(scope, candidate)
                    self.assertIn('SHA', '\n'.join(errors))
                elif attack == 'quote':
                    proof['source_chain']['entry']['render_provenance']['evidence']['delegate']['source_quote'] = 'function renderAgents(){return "fiction";}'
                    errors = self.p1_reject(scope, candidate)
                    self.assertIn('quote', '\n'.join(errors).lower())
                else:
                    candidate['completion_result'] = 'Unreviewed different operation'
                    from operation_attribution import StrictReview
                    errors = StrictReview(self.root, self.facts).reviewed_canonical_errors('canonical-query', self.retained)
                    self.assertIn('candidate SHA differs', '\n'.join(errors))

    def test_p1_conditional_source_proof_never_grants_permission_or_execution(self):
        proof, scope, candidate, *_ = self.p1_fixture()
        permission = copy.deepcopy(self.action.get('permission'))
        self.assertEqual([], self.problems(scope, candidate))
        self.assertEqual('conditional_only', proof['source_chain']['entry']['render_provenance']['reachability'])
        self.assertFalse(self.retained['production_verified'])
        self.assertFalse(scope['conditional_inclusion']['final_baseline_verified'])
        self.assertEqual('source_attribution_only', scope['verification_scope'])
        self.assertEqual('local_candidate_not_scope_or_function_approval', candidate['catalog_role'])
        self.assertEqual(permission, self.action.get('permission'))
        proof['source_chain']['entry']['render_provenance']['reachability'] = 'unconditional'
        self.p1_reject(scope, candidate)

    def test_p1_callback_markup_must_contain_a_real_control_event(self):
        source = P1_TABLE_PAGE.replace(
            '<button onclick="window.Pages[\'example\'].openAgent(\'${v}\')">View</button>',
            '<script type="application/json"><button onclick="window.Pages[\'example\'].openAgent(\'${v}\')">View</button></script>')
        _, scope, candidate, *_ = self.p1_fixture(page_source=source)
        self.p1_reject(scope, candidate)

    def test_p1_unused_navigator_cannot_certify_a_published_page_route(self):
        # Post-green attack: preserve the complete route/find/DOM-sink body,
        # honest new source/quotes/hashes and all page/table/owner bindings.
        # Only remove its actual global publication. It is now a dead helper.
        source = P1_TABLE_NAV.replace(
            'window.navigateTo=function(key,options){',
            'function unusedNavigator(key,options){').replace('\n};\n', '\n}\n')
        _, scope, candidate, *_ = self.p1_fixture(
            nav_source=source, navigation_needle='function unusedNavigator(')
        self.p1_reject(scope, candidate)

    def test_p1_returned_table_inside_inert_template_is_not_visible_UI(self):
        # Independent post-green attack: the callback/table/route all exist,
        # but the table is returned only inside an unmounted HTML template.
        # Its inert child tree must not certify a user-visible row control.
        source = P1_TABLE_PAGE.replace(
            '  return `<section>${ui.table(columns,rows,{})}</section>`;',
            '  return `<template id="cached-ui">${ui.table(columns,rows,{})}</template>`;')
        _, scope, candidate, *_ = self.p1_fixture(page_source=source)
        self.p1_reject(scope, candidate)

    def test_p1_bound_route_options_and_page_cannot_change_before_DOM_sink(self):
        variants = [
            P1_TABLE_NAV.replace('  const content=',
                "  opts.view='skills';\n  const content="),
            P1_TABLE_NAV.replace('  const content=',
                "  page.render=()=>'';\n  const content="),
            P1_TABLE_NAV.replace('const page=window.Pages[route.page];',
                "const page={render:()=>''};")]
        for source in variants:
            with self.subTest(source=source):
                _, scope, candidate, *_ = self.p1_fixture(nav_source=source)
                self.p1_reject(scope, candidate)

    def test_p1_constant_route_or_all_rows_removal_cannot_certify_a_control(self):
        # Pagination is conditional on there being rows, not a promise that
        # the data is populated. This normal slice keeps the original rows.
        paginated = P1_TABLE_PAGE.replace(
            '  return `<section>${ui.table(columns,rows,{})}</section>`;',
            '  const start=0;\n  return `<section>${ui.table(columns,rows.slice(start,start+8),{})}</section>`;')
        _, scope, candidate, *_ = self.p1_fixture(page_source=paginated)
        self.assertEqual([], self.problems(scope, candidate))
        variants = [
            ('route_length_zero', P1_TABLE_PAGE,
             P1_TABLE_NAV.replace('window.navigateTo=', 'routes.length=0;\nwindow.navigateTo=')),
            ('route_splice_zero', P1_TABLE_PAGE,
             P1_TABLE_NAV.replace('window.navigateTo=', 'routes.splice(0);\nwindow.navigateTo=')),
            ('wrapper_empty_rows', P1_TABLE_PAGE.replace(
                'function shell(key,rows,columns,primary,help){',
                'function shell(key,rows,columns,primary,help){\n  rows=[];'), P1_TABLE_NAV),
            ('wrapper_empty_slice', P1_TABLE_PAGE.replace(
                'function shell(key,rows,columns,primary,help){',
                'function shell(key,rows,columns,primary,help){\n  rows=rows.slice(0,0);'), P1_TABLE_NAV),
            ('table_argument_empty_slice', P1_TABLE_PAGE.replace(
                'ui.table(columns,rows,{})', 'ui.table(columns,rows.slice(0,0),{})'), P1_TABLE_NAV)]
        for label, source, navigation in variants:
            with self.subTest(attack=label):
                _, scope, candidate, *_ = self.p1_fixture(
                    page_source=source, nav_source=navigation)
                self.p1_reject(scope, candidate)

    def test_p1_menu_projection_resolver_preserves_selected_key_and_route(self):
        # The metadata cannot borrow a literal menu record which a resolver
        # changes before lookup, or which projection later overwrites.
        for source in [P1_TABLE_MENU_NAV, P1_TABLE_MENU_NAV.replace(
                'function resolve(key){',
                "function resolve(key){\n  if(key==='home')key='other';")]:
            with self.subTest(valid_source=source):
                _, scope, candidate, *_ = self.p1_fixture(
                    nav_source=source, route_resolution_symbol='resolve')
                self.assertEqual([], self.problems(scope, candidate))
        variants = [
            P1_TABLE_MENU_NAV.replace('function resolve(key){',
                "function resolve(key){\n  key='other';"),
            P1_TABLE_MENU_NAV.replace('function resolve(key){',
                "function resolve(key){\n  if(key==='agents')key='other';"),
            P1_TABLE_MENU_NAV.replace('function resolve(key){',
                "routes['agents']={key:'agents',page:'another-page',view:'agents'};\nfunction resolve(key){")]
        for source in variants:
            with self.subTest(invalid_source=source):
                _, scope, candidate, *_ = self.p1_fixture(
                    nav_source=source, route_resolution_symbol='resolve')
                self.p1_reject(scope, candidate)

    def test_p1_Object_assign_requires_final_real_route_view_and_stable_options(self):
        # The approved real shape is a literal const initializer followed by
        # a separate history-options merge whose final view is route.view.
        # Object.assign as the initializer is intentionally unsupported.
        preserved = P1_TABLE_NAV.replace('  const content=',
            '  Object.assign(opts,historyOptions,{routeKey:key,view:route.view});\n  const content=')
        _, scope, candidate, *_ = self.p1_fixture(
            nav_source=preserved, opts_tail_needle='Object.assign(opts,')
        self.assertEqual([], self.problems(scope, candidate))
        variants = [
            ('wrong_last_view', preserved.replace(
                'routeKey:key,view:route.view', "routeKey:key,view:'skills'"),
                'const opts=', 'Object.assign(opts,'),
            ('spread_can_override_last_view', preserved.replace(
                'routeKey:key,view:route.view', 'routeKey:key,view:route.view,...extra'),
                'const opts=', 'Object.assign(opts,'),
            ('fake_Object', preserved.replace('window.navigateTo=function(key,options){',
                'window.navigateTo=function(key,options){\n  const Object=Diagnostics;'),
                'const opts=', 'Object.assign(opts,'),
            ('opts_rebound_before_sink', preserved.replace('const opts=', 'let opts=').replace(
                '  const content=', "  opts={view:'skills'};\n  const content="),
                'let opts=', 'Object.assign(opts,'),
            ('unsupported_Object_assign_initializer', P1_TABLE_NAV.replace(
                'const opts={view:route.view,...(options||{})};',
                'const opts=Object.assign({view:route.view},options,{view:route.view});'),
                'const opts=', None)]
        for label, source, declaration, merge in variants:
            with self.subTest(attack=label):
                _, scope, candidate, *_ = self.p1_fixture(
                    nav_source=source, opts_needle=declaration,
                    opts_tail_needle=merge)
                self.p1_reject(scope, candidate)


# Do not expose the borrowed TestCase as another module test class: unittest's
# module loader would otherwise recount its old test methods in a /tmp run.
del _p1_helpers





# P2 explicit source provenance and conditional member-save regressions.

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
import unittest

from operation_attribution import StrictReview
import test_shared_attribution_contract as fixtures
from test_retained_operation_contract import bind_retained_actions


P2_EXPECTED_SCOPE = "conditional_static_source_attribution_only"


PAGE_SOURCE = r'''(function () {
  const ui = PlatformUI, esc = ui.escape, api = GroupStore;
  let editor = null, listContext = '';
  const command = (method, ...args) => esc(`GroupPage.${method}(${args.map(v => JSON.stringify(v)).join(',')})`);
  function render() {
    if (!api.canAccess()) return ui.empty('Unavailable');
    const rows = api.list();
    return `<section>${ui.table([
      { key: 'id', label: 'Operation', render: (id, group) => `
        <button onclick="${command('members', id, listContext)}">Members</button>` }
    ], rows, {})}</section>`;
  }
  function valid(key) { return api.canAccess() && (!key || key === api.contextKey()); }
  function members(id, key) {
    if (!valid(key)) return denied();
    const group = api.get(id);
    if (!group) return denied();
    const used = api.references(id).filter(task => task.pending || !['finished', 'ended'].includes(task.status));
    if (used.length) return denied();
    editor = { kind: 'members', id, context: api.contextKey(), revision: api.revision() };
    const candidates = api.candidates(group.tenantId, group.enterpriseId);
    ui.openLayer('group-edit', `
      <div>${candidates.map(person => `<label><input type="checkbox" name="group-member" value="${esc(person.id)}">${esc(person.name)}</label>`).join('')}</div>
      <button onclick="GroupPage.save()">Save members</button>`, 'wide');
  }
  function save() {
    const e = editor;
    if (!e || !valid(e.context)) return denied();
    let result;
    if (e.kind === 'create') {
      result = api.create({ name: readName() }, e.context, e.revision);
    } else if (e.kind === 'edit') {
      result = api.update(e.id, { name: readName() }, e.context, e.revision);
    } else {
      const selected = [...document.querySelectorAll('[name="group-member"]:checked')].map(node => node.value);
      result = api.setMembers(e.id, selected, e.context, e.revision);
    }
    if (!result.ok) return showToast(result.message, 'warning');
    editor = null;
    ui.closeLayer('group-edit');
    refresh();
    if (e.kind === 'create' && result.group) {
      members(result.group.id, api.contextKey()); // ORIGINAL-LINE270-OPEN-ONLY
    }
  }
  window.GroupPage = { render, members, save };
})();
'''

STORE_SOURCE = r'''(function () {
  function setMembers(groupId, memberIds, expectedContext, expectedRevision) {
    if (!validText(groupId)) return failure('Choose a group');
    return transact(expectedContext, expectedRevision, next => {
      const group = next.groups.find(row => row.id === groupId && inScope(row));
      if (!group || !inScope(group)) return failure('Group out of scope');
      const used = references(group.id).filter(task => task.pending || !['finished', 'ended'].includes(task.status));
      if (used.length) return failure('Task still active');
      const members = normalizeMembers(Array.isArray(memberIds) ? memberIds : [], group.tenantId, group.enterpriseId);
      if (!members.ok) return members;
      if (group.memberIds.length === members.value.length && group.memberIds.every((id, index) => id === members.value[index])) {
        return { unchanged: true };
      }
      const moved = moveMembersToGroup(next.groups, group, members.value);
      return { message: moved ? 'Moved and updated' : 'Updated', group };
    });
  }
  window.GroupStore = Object.freeze({ setMembers });
})();
'''

UI_SUPPORT_SOURCE = r'''(function () {
  function table(columns, rows, options) {
    const body = rows.length ? rows.map(row => `<tr>${columns.map(col => `<td>${col.render ? col.render(row[col.key], row) : row[col.key]}</td>`).join('')}</tr>`).join('') : '<tr><td>Empty</td></tr>';
    return `<table><tbody>${body}</tbody></table>`;
  }
  function escape(value) {
    return String(value == null ? '' : value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }
  window.PlatformUI = { table, escape };
})();
'''

# Required old attacks remain in the original test file, unchanged. In particular
# test_window_owner_is_not_silently_treated_as_bare_owner must still reject its
# original source/proof with NO explicit global-spelling evidence. P2 is opt-in.
OLD_NEGATIVES_MUST_REMAIN = (
    'test_window_owner_is_not_silently_treated_as_bare_owner',
    'test_cross_file_wrong_factory_receiver_rejected',
    'test_cross_file_wrong_branch_cannot_claim_same_operation',
    'test_cross_file_branch_cannot_claim_unconditional_execution',
    'test_layer_without_render_opening_evidence_stays_pending',
    'test_owner_must_map_exact_property_not_same_short_name',
    'test_exact_hash_and_quote_required_for_each_link',
    'test_source_chain_ui_parameter_shadow_cannot_use_outer_ui_alias',
)


@dataclass
class MembersCase:
    page: Path
    store: Path
    scope: dict
    candidate: dict
    proof: dict | None = None


class ConcreteP2SchemaAdapter:
    """Proof-data construction only; no parser exemptions or JS evaluation."""
    @staticmethod
    def install(test, case):
        page, store, ui = case.page, case.store, case.ui
        def ev(path, first, last=None):
            sid = 'SRC-001' if path == page else 'SRC-003' if path == store else 'SRC-004'
            return test.ev(path, first, last, sid)
        def line(path, text): return test.line(path, text)
        def span(path, start, next_anchor):
            return ev(path, line(path, start), line(path, next_anchor) - 1)
        render = span(page, 'function render(', 'function valid(')
        members = span(page, 'function members(', 'function save(')
        save = span(page, 'function save(', 'window.GroupPage =')
        page_export = ev(page, line(page, 'window.GroupPage ='))
        store_export = ev(store, line(store, 'window.GroupStore ='))
        alias = ev(page, line(page, 'const ui ='))
        terminal = span(store, 'function setMembers(', 'window.GroupStore =')
        helper = ev(page, line(page, 'const command ='))
        table = span(ui, 'function table(', 'function escape(')
        escape = span(ui, 'function escape(', 'window.PlatformUI =')
        global_page = {'version': 'explicit-window-owner@1', 'bare_owner': 'GroupPage',
                       'window_owner': 'window.GroupPage', 'evidence': copy.deepcopy(page_export)}
        global_store = {'version': 'explicit-window-owner@1', 'bare_owner': 'GroupStore',
                        'window_owner': 'window.GroupStore', 'evidence': copy.deepcopy(store_export)}
        proof = dict(ev(store, line(store, 'function setMembers(')),
            candidate_key='candidate-query', candidate_source_pointer='/source_evidence/1',
            target_symbol='setMembers', trigger_kind='source_chain',
            business_trigger=test.retained['trigger'],
            rationale='Existing members opening and separate save event; exact conditional state path and immutable object receiver, not runtime execution.')
        proof['source_chain'] = {
            'version': 'bounded-static-chain@1',
            'entry': {'kind': 'template_event', 'callee': 'GroupPage.save', 'event': 'click',
                'evidence': ev(page, line(page, "ui.openLayer('group-edit'"),
                    line(page, '<button onclick="GroupPage.save()">')),
                'opening_evidence': render,
                'ui_alias_evidence': copy.deepcopy(alias),
                'opening_provenance': {'version': 'bounded-direct-table-layer@1',
                    'opening_symbol': 'members', 'opening_callee': 'GroupPage.members',
                    'opening_handler_evidence': members, 'column_key_literal': 'id',
                    'event_object_argument_index': 0,
                    'handler_helper_evidence': helper,
                    'helper_call': {'name': 'command', 'literal_method': 'members'},
                    'escape_alias_evidence': copy.deepcopy(alias),
                    'table_implementation_evidence': table,
                    'table_export_evidence': ev(ui, line(ui, 'window.PlatformUI =')),
                    'escape_implementation_evidence': escape}},
            'links': [
                {'kind': 'static_owner_export', 'callee': 'GroupPage.save', 'symbol': 'save',
                    'evidence': page_export, 'global_owner_binding': global_page},
                {'kind': 'callsite', 'from_symbol': 'save', 'callee': 'api.setMembers',
                    'branch': 'state_else', 'reachability': 'conditional_only', 'evidence': save,
                    'state_branch': {'version': 'bounded-state-else@1',
                        'state_symbol': 'editor', 'capture_symbol': 'e', 'kind_field': 'kind',
                        'selected_value': 'members', 'excluded_values': ['create', 'edit'],
                        'result_variable': 'result', 'object_field': 'id',
                        'object_argument_index': 0, 'capture_evidence': copy.deepcopy(members)}},
                {'kind': 'const_owner_alias', 'alias': 'api', 'owner': 'GroupStore',
                    'evidence': copy.deepcopy(alias)},
                {'kind': 'static_owner_export', 'callee': 'GroupStore.setMembers',
                    'symbol': 'setMembers', 'evidence': store_export,
                    'global_owner_binding': global_store}],
            'terminal': {'symbol': 'setMembers', 'evidence': terminal}}
        case.proof = proof
        case.scope['derived_trigger_evidence'] = proof

    @staticmethod
    def omit_role(case, role):
        chain = case.proof['source_chain']
        if role == 'page_global_spelling_binding':
            del chain['links'][0]['global_owner_binding']
        elif role == 'store_global_spelling_binding':
            del chain['links'][3]['global_owner_binding']
        elif role == 'opening_helper':
            del chain['entry']['opening_provenance']['handler_helper_evidence']
        elif role == 'preceding_edit_branch':
            chain['links'][1]['state_branch']['excluded_values'] = ['create']
        else:
            raise AssertionError('Unknown proof omission: ' + role)

    @staticmethod
    def claim(case, role, value):
        chain = case.proof['source_chain']
        if role == 'api_alias_kind':
            chain['links'][2].update(kind=value, factory_symbol='api')
        elif role == 'opening_literal_method':
            chain['entry']['opening_provenance']['helper_call']['literal_method'] = value
        elif role == 'save_entry':
            assert value == 'members_opening_only'
            chain['entry']['callee'] = 'GroupPage.members'
            chain['entry']['evidence'] = copy.deepcopy(chain['entry']['opening_evidence'])
        elif role == 'selected_kind':
            chain['links'][1]['state_branch']['selected_value'] = value
        elif role == 'branch_reachability':
            chain['links'][1]['reachability'] = value
        else:
            raise AssertionError('Unknown proof claim: ' + role)

    @staticmethod
    def old_270_only(case):
        chain = case.proof['source_chain']
        lines = case.page.read_text().splitlines()
        first = next(i for i, text in enumerate(lines, 1) if 'ORIGINAL-LINE270-OPEN-ONLY' in text)
        old = chain['links'][1]['evidence']
        chain['links'][1] = {'kind': 'callsite', 'from_symbol': 'save', 'callee': 'members',
            'branch': 'unconditional', 'evidence': dict(old, line_start=first, line_end=first,
                source_quote=lines[first-1])}
        # Original candidate stays /source_evidence/1 and terminal stays store.setMembers.


P2_SCHEMA_ADAPTER = ConcreteP2SchemaAdapter()


class P2MembersSaveTests(unittest.TestCase):
    # Borrow helpers without inheriting ANY existing test_* methods.
    setUp = fixtures.SharedContractTests.setUp
    tearDown = fixtures.SharedContractTests.tearDown
    retained = fixtures.SharedContractTests.retained
    action = fixtures.SharedContractTests.action
    page = fixtures.SharedContractTests.page
    canonical = fixtures.SharedContractTests.canonical
    candidate_hash = staticmethod(fixtures.SharedContractTests.candidate_hash)

    def ev(self, path, first, last=None, sid=None):
        last = first if last is None else last
        sid = sid or ('SRC-001' if path.name == 'evidence.txt' else 'SRC-004' if path.name == 'p2-ui.js' else 'SRC-003')
        lines = path.read_text().splitlines()
        self.assertGreaterEqual(first, 1)
        self.assertLessEqual(last, len(lines))
        return {'file': str(path), 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'line_start': first, 'line_end': last,
                'source_quote': '\n'.join(lines[first-1:last]),
                'source_refs': ['inputs/source-index.json#' + sid]}

    def line(self, path, exact_fragment):
        found = [i for i, value in enumerate(path.read_text().splitlines(), 1) if exact_fragment in value]
        self.assertEqual(1, len(found), 'Fixture anchor must be unique: ' + exact_fragment)
        return found[0]

    def new_case(self):
        self.assertIsNotNone(P2_SCHEMA_ADAPTER,
            'Proposal is not yet executable: wait for P1 pass and approved P2 schema adapter')
        scope, candidate = self.canonical()
        page = self.root / 'inputs/evidence.txt'
        store = self.root / 'inputs/members-store.js'
        page.write_text(PAGE_SOURCE)
        store.write_text(STORE_SOURCE)
        support = self.root / 'inputs/p2-ui.js'
        support.write_text(UI_SUPPORT_SOURCE)
        page_sha = hashlib.sha256(page.read_bytes()).hexdigest()
        store_sha = hashlib.sha256(store.read_bytes()).hexdigest()
        self.facts['sources']['sources'][0]['sha256'] = page_sha
        self.facts['sources']['sources'].append({'id': 'SRC-003', 'status': 'verified',
            'snapshot_path': 'inputs/members-store.js', 'sha256': store_sha})
        self.facts['sources']['sources'].append({'id': 'SRC-004', 'status': 'verified',
            'snapshot_path': 'inputs/p2-ui.js', 'sha256': hashlib.sha256(support.read_bytes()).hexdigest()})
        self.retained['source_refs'] = ['inputs/source-index.json#SRC-001', 'inputs/source-index.json#SRC-003', 'inputs/source-index.json#SRC-004']
        scope['source_refs'] = list(self.retained['source_refs'])
        candidate.pop('trigger', None)
        candidate['business_object'] = self.retained['business_object'] = 'Existing outbound group'
        candidate['primary_action'] = self.retained['primary_action'] = 'Save members'
        candidate['completion_result'] = self.retained['completion_result'] = 'Original local members save result, failure or unchanged; no supplier claim'
        self.retained['trigger'] = 'Authorized user opens existing members editor and separately confirms Save members'
        candidate['source_evidence'] = [
            {'file': str(page), 'source_sha256': page_sha, 'locator': 'function members('},
            {'file': str(store), 'source_sha256': store_sha, 'locator': 'function setMembers('}]
        candidate['source_refs'] = list(scope['source_refs'])
        self.retained['local_source_evidence'] = [
            dict(self.ev(page, self.line(page, 'function members(')), source_id='SRC-001'),
            dict(self.ev(store, self.line(store, 'function setMembers(')), source_id='SRC-003')]
        for field in ('business_object', 'primary_action', 'completion_result'):
            scope['field_comparison'][field] = {'candidate_key': 'candidate-query',
                'candidate_field': field, 'candidate_value': candidate[field],
                'canonical_value': self.retained[field], 'assessment': 'same_original_operation',
                'rationale': 'Portable bounded fixture for the exact original operation structure.'}
        scope['field_comparison']['trigger'] = {'candidate_key': 'candidate-query',
            'candidate_field': '/source_evidence/1/locator', 'candidate_value': 'function setMembers(',
            'canonical_value': self.retained['trigger'], 'assessment': 'same_original_operation',
            'rationale': 'Opening members alone is not the save operation; terminal is second-file setMembers.'}
        scope['candidate_record_sha256']['candidate-query'] = self.candidate_hash(candidate)
        case = MembersCase(page, store, scope, candidate)
        case.ui = support
        P2_SCHEMA_ADAPTER.install(self, case)
        self.assertIs(case.proof, scope['derived_trigger_evidence'])
        self.assertEqual('/source_evidence/1', case.proof['candidate_source_pointer'])
        self.assertEqual('setMembers', case.proof['target_symbol'])
        self.assertEqual('source_chain', case.proof['trigger_kind'])
        self.assertFalse(self.retained['production_verified'])
        bind_retained_actions(self.root, self.facts)
        return case

    def errors(self, case):
        review = StrictReview(self.root, self.facts)
        return review.derived_trigger_errors('candidate-query', case.candidate,
            '/source_evidence/1/locator', case.scope, self.retained)

    def accepted(self, case):
        self.assertEqual([], self.errors(case))
        self.assertFalse(self.retained['production_verified'])
        self.assertEqual('setMembers', case.proof['source_chain']['terminal']['symbol'])

    def rejected(self, case, family):
        problems = self.errors(case)
        self.assertTrue(problems, 'Mutated proof must fail closed: ' + family)
        self.assertRegex('\n'.join(problems).lower(), family)

    def refresh_file_evidence(self, case, path):
        """Refresh honest source bytes/quotes so an attack tests semantics, not stale SHA."""
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines = path.read_text().splitlines()
        for source in self.facts['sources']['sources']:
            if (self.root / source.get('snapshot_path', '')).resolve() == path.resolve():
                source['sha256'] = digest
        def walk(value):
            if isinstance(value, dict):
                if value.get('file', value.get('path')) == str(path):
                    value['source_sha256'] = digest
                    if 'source_quote' in value:
                        first, last = value['line_start'], value['line_end']
                        value['source_quote'] = '\n'.join(lines[first-1:last])
                for child in value.values(): walk(child)
            elif isinstance(value, list):
                for child in value: walk(child)
        walk(case.proof); walk(case.candidate); walk(self.retained['local_source_evidence'])
        case.scope['candidate_record_sha256']['candidate-query'] = self.candidate_hash(case.candidate)

    def replace_source(self, case, path, before, after):
        self.assertEqual(path.read_text().count(before), 1, 'Attack must mutate one intended source location')
        self.assertEqual(before.count('\n'), after.count('\n'), 'Keep evidence line anchors stable')
        path.write_text(path.read_text().replace(before, after))
        self.refresh_file_evidence(case, path)

    def paired_source_attack(self, path_role, before, after, family):
        case = self.new_case()
        self.accepted(case)  # A rejection cannot be credited if its paired positive never passed.
        self.replace_source(case, getattr(case, path_role), before, after)
        self.rejected(case, family)

    def test_p2_exact_members_save_two_files_is_conditionally_attributed(self):
        case = self.new_case()
        self.accepted(case)
        self.assertIn("api = GroupStore", case.page.read_text())
        self.assertNotIn('api()', case.page.read_text())
        self.assertIn("kind: 'members'", case.page.read_text())
        self.assertIn('let result;', case.page.read_text())
        self.assertIn('result = api.setMembers(', case.page.read_text())
        self.assertIn('Array.isArray(memberIds) ? memberIds : []', case.store.read_text())
        # This asserts source attribution only: no JS execution, transaction test or remote success.

    def test_p2_bare_page_owner_without_explicit_global_evidence_rejected(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.omit_role(case, 'page_global_spelling_binding')
        self.rejected(case, r'global|owner|binding|exact')

    def test_p2_bare_store_owner_without_explicit_global_evidence_rejected(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.omit_role(case, 'store_global_spelling_binding')
        self.rejected(case, r'global|owner|binding|exact')

    def test_p2_const_object_alias_is_not_factory_evidence(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.claim(case, 'api_alias_kind', 'factory_owner')
        self.rejected(case, r'factory|alias|owner|pending')

    def test_p2_alias_receiver_wrong_even_after_SHA_refresh(self):
        self.paired_source_attack('page', 'api = GroupStore;', 'api = OtherStore;', r'alias|owner|binding|receiver')

    def test_p2_let_owner_alias_is_not_immutable_const(self):
        self.paired_source_attack('page', 'const ui = PlatformUI, esc = ui.escape, api = GroupStore;',
            'const ui = PlatformUI, esc = ui.escape; let api = GroupStore;', r'alias|const|binding')

    def test_p2_alias_rebinding_must_fail(self):
        self.paired_source_attack('page', 'const ui = PlatformUI, esc = ui.escape, api = GroupStore;',
            'const ui = PlatformUI, esc = ui.escape, api = GroupStore; api = OtherStore;', r'alias|rebind|mutat|const')

    def test_p2_api_factory_source_cannot_borrow_object_alias_proof(self):
        self.paired_source_attack('page', 'api = GroupStore;', 'api = () => GroupStore;', r'alias|factory|binding')

    def test_p2_local_api_parameter_shadows_owner_alias(self):
        self.paired_source_attack('page', 'function save() {', 'function save(api) {', r'shadow|alias|binding')

    def test_p2_bare_page_global_shadow_is_not_window_global(self):
        self.paired_source_attack('page', '(function () {', '(function (GroupPage) {', r'shadow|global|owner')

    def test_p2_bare_store_global_shadow_is_not_window_global(self):
        self.paired_source_attack('page', '(function () {', '(function (GroupStore) {', r'shadow|global|owner|alias')

    def test_p2_same_method_wrong_exported_component_rejected(self):
        self.paired_source_attack('store', 'window.GroupStore = Object.freeze({ setMembers });',
            'window.OtherStore = Object.freeze({ setMembers });', r'owner|binding|pending')

    def test_p2_escaped_helper_must_be_original_PlatformUI_escape(self):
        self.paired_source_attack('page', 'esc = ui.escape,', 'esc = Diagnostics.escape,', r'escape|helper|alias|binding')

    def test_p2_opening_helper_is_distinct_from_save_event(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.claim(case, 'opening_literal_method', 'save')
        self.rejected(case, r'opening|helper|event|literal|control')

    def test_p2_opening_helper_cannot_be_missing(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.omit_role(case, 'opening_helper')
        self.rejected(case, r'opening|helper|event|control|structured')

    def test_p2_save_button_cannot_be_proved_by_members_open_button(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.claim(case, 'save_entry', 'members_opening_only')
        self.rejected(case, r'entry|callee|opening|control|save')

    def test_p2_dynamic_opening_method_rejected(self):
        self.paired_source_attack('page', "command('members', id, listContext)",
            'command(currentMethod, id, listContext)', r'helper|literal|control|event|entry')

    def test_p2_editor_kind_must_originate_from_members_opening(self):
        self.paired_source_attack('page', "kind: 'members', id, context:",
            "kind: 'edit', id, context:", r'kind|branch|opening|selected|context')

    def test_p2_create_branch_cannot_claim_members_save(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.claim(case, 'selected_kind', 'create')
        self.rejected(case, r'branch|selected|kind|operand')

    def test_p2_edit_branch_cannot_claim_members_save(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.claim(case, 'selected_kind', 'edit')
        self.rejected(case, r'branch|selected|kind|operand')

    def test_p2_members_alternate_cannot_claim_unconditional(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.claim(case, 'branch_reachability', 'unconditional')
        self.rejected(case, r'conditional|branch|reachability|direct call')

    def test_p2_members_branch_cannot_drop_preceding_else_if(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.omit_role(case, 'preceding_edit_branch')
        self.rejected(case, r'branch|else|condition|selected')

    def test_p2_result_assignment_requires_declared_result(self):
        self.paired_source_attack('page', 'let result;', 'let unrelated;', r'declar|result|assign|branch')

    def test_p2_other_assignment_target_is_not_result(self):
        self.paired_source_attack('page', 'result = api.setMembers(', 'unrelated = api.setMembers(',
            r'result|assign|target|branch')

    def test_p2_deferred_call_is_not_this_save_invocation(self):
        self.paired_source_attack('page', 'result = api.setMembers(', 'result = () => api.setMembers(',
            r'call|scope|assign|branch|invocation')

    def test_p2_original270_only_opens_layer_not_save_terminal(self):
        case = self.new_case(); self.accepted(case)
        P2_SCHEMA_ADAPTER.old_270_only(case)
        self.rejected(case, r'terminal|candidate|opening|save|setmembers|disconnected|pending')

    def test_p2_original_candidate_pointer_cannot_switch_to_members_opener(self):
        case = self.new_case(); self.accepted(case)
        case.proof['candidate_source_pointer'] = '/source_evidence/0'
        self.rejected(case, r'source|pointer|file|terminal')

    def test_p2_target_file_SHA_mismatch_rejected(self):
        case = self.new_case(); self.accepted(case)
        case.proof['source_chain']['terminal']['evidence']['source_sha256'] = '0' * 64
        self.rejected(case, r'sha')

    def test_p2_exact_quote_mismatch_rejected(self):
        case = self.new_case(); self.accepted(case)
        case.proof['source_chain']['terminal']['evidence']['source_quote'] += ' '
        self.rejected(case, r'quote')

    def test_p2_wrong_registered_source_rejected(self):
        case = self.new_case(); self.accepted(case)
        case.proof['source_chain']['terminal']['evidence']['source_refs'] = ['inputs/source-index.json#SRC-999']
        self.rejected(case, r'unregistered|source|reference')

    def test_p2_same_named_terminal_from_different_registered_file_rejected(self):
        case = self.new_case(); self.accepted(case)
        other = self.root / 'inputs/other-members-store.js'
        other.write_text(STORE_SOURCE)
        self.facts['sources']['sources'].append({'id': 'SRC-005', 'status': 'verified',
            'snapshot_path': 'inputs/other-members-store.js', 'sha256': hashlib.sha256(other.read_bytes()).hexdigest()})
        case.scope['source_refs'].append('inputs/source-index.json#SRC-005')
        self.retained['source_refs'].append('inputs/source-index.json#SRC-005')
        first = self.line(other, 'function setMembers(')
        case.proof['source_chain']['terminal']['evidence'] = self.ev(other, first, first+15, 'SRC-005')
        self.rejected(case, r'terminal|source|file|candidate|exact')


    def test_p2_members_result_whole_value_overwrite_rejected(self):
        self.paired_source_attack('page',
            'result = api.setMembers(e.id, selected, e.context, e.revision);',
            'result = api.setMembers(e.id, selected, e.context, e.revision); result = { ok: true };',
            r'result|assign|mutat|overwrit|selected|branch')

    def test_p2_members_result_ok_property_overwrite_rejected(self):
        self.paired_source_attack('page',
            'result = api.setMembers(e.id, selected, e.context, e.revision);',
            'result = api.setMembers(e.id, selected, e.context, e.revision); result.ok = true;',
            r'result|assign|mutat|overwrit|selected|branch')

    def test_p2_inline_table_literal_empty_rows_rejected(self):
        self.paired_source_attack('page',
            'const rows = api.list();',
            'const rows = [];',
            r'row|empty|table|control|reach|dead')

    def test_p2_inline_table_rows_cleared_before_return_rejected(self):
        self.paired_source_attack('page',
            'const rows = api.list();',
            'let rows = api.list(); rows = [];',
            r'row|empty|table|control|reach|dead')

    def test_p2_inline_table_slice_zero_zero_rejected(self):
        self.paired_source_attack('page',
            '], rows, {})}</section>`;',
            '], rows.slice(0, 0), {})}</section>`;',
            r'row|empty|slice|table|control|reach|dead')


    def test_p2_const_owner_alias_member_rewrite_rejected(self):
        pristine_facts = copy.deepcopy(self.facts)
        anchor = 'window.GroupPage = { render, members, save };'
        # const freezes the alias binding, not its object properties. Each
        # receiver spelling must preserve the same exact immutable owner proof.
        for receiver in ('api', 'GroupStore', 'window.GroupStore'):
            with self.subTest(receiver=receiver):
                self.facts = copy.deepcopy(pristine_facts)
                case = self.new_case()
                self.accepted(case)
                self.replace_source(case, case.page, anchor,
                    receiver + '.setMembers = unrelated; ' + anchor)
                self.rejected(case, r'owner|alias|property|rewrit|mutat|assign|binding')


    def test_p2_opening_state_Object_assign_mutation_rejected(self):
        self.paired_source_attack('page',
            "editor = { kind: 'members', id, context: api.contextKey(), revision: api.revision() };",
            "editor = { kind: 'members', id, context: api.contextKey(), revision: api.revision() }; Object.assign(editor, { kind: 'edit' });",
            r'state|capture|kind|assign|mutat|selected|branch')

    def test_p2_protected_owner_cannot_escape_via_second_const_alias(self):
        self.paired_source_attack('page',
            'const ui = PlatformUI, esc = ui.escape, api = GroupStore;',
            'const ui = PlatformUI, esc = ui.escape, api = GroupStore; const other = api; other.setMembers = unrelated;',
            r'owner|alias|escap|copy|property|rewrit|mutat|binding')

    def test_p2_inline_table_if_false_return_is_not_reachable_control(self):
        self.paired_source_attack('page',
            'return `<section>${ui.table([',
            'if (false) return `<section>${ui.table([',
            r'return|reach|false|dead|table|control|guard')

    def test_p2_layer_if_false_call_is_not_reachable_save_event(self):
        self.paired_source_attack('page',
            "ui.openLayer('group-edit', `",
            "if (false) ui.openLayer('group-edit', `",
            r'layer|reach|false|dead|save|control|guard')


    def test_p2_result_tail_all_remaining_known_mutators_rejected(self):
        pristine_facts = copy.deepcopy(self.facts)
        anchor = 'result = api.setMembers(e.id, selected, e.context, e.revision);'
        mutations = (
            'Object.defineProperties(result, { ok: { value: true } });',
            'Object.setPrototypeOf(result, { ok: true });',
            "Reflect.deleteProperty(result, 'ok');",
        )
        for mutation in mutations:
            with self.subTest(mutator=mutation):
                self.facts = copy.deepcopy(pristine_facts)
                case = self.new_case()
                self.accepted(case)
                self.replace_source(case, case.page, anchor, anchor + ' ' + mutation)
                self.rejected(case, r'result|mutat|assign|write|overrid|tail|selected|branch')

    def test_p2_protected_state_capture_result_copy_or_forward_escape_rejected(self):
        pristine_facts = copy.deepcopy(self.facts)
        state = "editor = { kind: 'members', id, context: api.contextKey(), revision: api.revision() };"
        capture = 'const e = editor;'
        result = 'result = api.setMembers(e.id, selected, e.context, e.revision);'
        # Each subcase keeps the same guards and exact source evidence. The
        # ORIGINAL const e=editor is the only declared capture exception.
        mutations = (
            ('state_copy', state, "const hidden = editor; Object.assign(hidden, { kind: 'edit' });"),
            ('capture_copy', capture, "const hidden = e; Object.assign(hidden, { kind: 'edit' });"),
            ('result_copy', result, 'const hidden = result; hidden.ok = true;'),
            ('state_forward', state, 'unproven(editor);'),
            ('capture_forward', capture, 'unproven(e);'),
            ('result_forward', result, 'unproven(result);'),
        )
        for label, anchor, mutation in mutations:
            with self.subTest(escape=label):
                self.facts = copy.deepcopy(pristine_facts)
                case = self.new_case()
                self.accepted(case)
                self.replace_source(case, case.page, anchor, anchor + ' ' + mutation)
                self.rejected(case, r'state|capture|result|owner|alias|escap|copy|forward|mutat|selected|branch|binding')


    def test_p2_parenthesized_protected_value_copy_or_forward_rejected(self):
        pristine_facts = copy.deepcopy(self.facts)
        state = "editor = { kind: 'members', id, context: api.contextKey(), revision: api.revision() };"
        capture = 'const e = editor;'
        result = 'result = api.setMembers(e.id, selected, e.context, e.revision);'
        # Parentheses do not change protected object identity. The original
        # !e guard and exact const e=editor remain in every paired positive.
        mutations = (
            ('state_parenthesized_copy', state, "const hidden = (editor); hidden.kind = 'edit';"),
            ('capture_parenthesized_copy', capture, "const hidden = (e); hidden.kind = 'edit';"),
            ('result_parenthesized_copy', result, 'const hidden = (result); hidden.ok = true;'),
            ('state_parenthesized_forward', state, 'unproven((editor));'),
            ('capture_parenthesized_forward', capture, 'unproven((e));'),
            ('result_parenthesized_forward', result, 'unproven((result));'),
        )
        for label, anchor, mutation in mutations:
            with self.subTest(escape=label):
                self.facts = copy.deepcopy(pristine_facts)
                case = self.new_case()
                self.accepted(case)
                self.replace_source(case, case.page, anchor, anchor + ' ' + mutation)
                self.rejected(case, r'state|capture|result|bare|value|alias|escap|copy|forward|mutat|selected|branch|binding')




# P3 explicit nested row-condition source controls and strict regressions.

import copy
import hashlib
import unittest
from types import SimpleNamespace as P3_SimpleNamespace


def P3_helper_container(P3_source_class):
    return P3_SimpleNamespace(**{
        name: getattr(P3_source_class, name)
        for name in ('setUp', 'tearDown', 'retained', 'action', 'page',
                     'canonical', 'candidate_hash', 'ev', 'problems')})

try:
    P3_BASE_HELPERS = P3_helper_container(BoundedSourceChainTests)
except NameError:
    import test_bounded_source_chain as P3_HELPER_MODULE
    P3_BASE_HELPERS = P3_helper_container(P3_HELPER_MODULE.BoundedSourceChainTests)


P3_RESTORE_BUTTON = '<button onclick="window.Pages[\'example\'].restore(\'${v}\')">Restore</button>'
P3_DISABLE_BUTTON = '<button onclick="window.Pages[\'example\'].disable(\'${v}\')">Disable</button>'
P3_NESTED_EXPRESSION = ("${row.lifecycleStatus==='已启用'?`" + P3_DISABLE_BUTTON
                        + "`:(row.lifecycleStatus==='已停用'?`" + P3_RESTORE_BUTTON + "`:'')}")
P3_RENDERER_MARKUP = ('<div class="table-actions"><button onclick="window.Pages[\'example\'].inspect()">View</button>'
                       + P3_NESTED_EXPRESSION + '</div>')
P3_TABLE_PAGE = '''(function(){
const ui=PlatformUI;
let view='agents';
function shell(key,rows,columns,primary,help){
  return `<section>${ui.table(columns,rows,{})}</section>`;
}
function renderAgents(){
  const rows=CloudCallData.agents;
  return `<main>${shell('agents',rows,[
    {key:'contactCenterIdentityId',label:'Agent',render:(v,row)=>`__P3_RENDERER__`}
  ],'','')}</main>`;
}
function render(options){
  view=options?.view||view;
  if(view==='skills')return `<main>Skills</main>`;
  if(view==='sync')return `<main>Sync</main>`;
  return renderAgents();
}
function restore(id){
  const row=CloudCallData.agents.find(item=>item.contactCenterIdentityId===id);
  if(!row||!AppState.authorizeObject('',row)||row.lifecycleStatus!=='已停用')return;
  if(!persistExistingObject(row))return;
  row.lifecycleStatus='已启用';
  return showOriginalResult(row);
}
function disable(id){
  return keepOriginalDisable(id);
}
function inspect(){
  return showOriginalListHelp();
}
window.Pages['example']={render,restore,disable,inspect};
})();
'''.replace('__P3_RENDERER__', P3_RENDERER_MARKUP)

P3_TABLE_NAV = '''(function(){
const routes=[{key:'agents',page:'example',view:'agents'}];
window.navigateTo=function(key,options){
  const route=routes.find(row=>row.key===key);
  const page=window.Pages[route.page];
  const opts={view:route.view,...(options||{})};
  const content=document.getElementById('page-content');
  content.innerHTML=page.render(opts);
};
})();
'''

P3_TABLE_UI = '''(function(){
function table(columns,rows,options){
  const body=rows.length?rows.map(row=>`<tr>${columns.map(col=>`<td>${col.render?col.render(row[col.key],row):row[col.key]}</td>`).join('')}</tr>`).join(''):'<tr><td>Empty</td></tr>';
  return `<table><tbody>${body}</tbody></table>`;
}
window.PlatformUI={table};
})();
'''


class P3NestedRowConditionalTests(unittest.TestCase):
    setUp = P3_BASE_HELPERS.setUp
    tearDown = P3_BASE_HELPERS.tearDown
    retained = P3_BASE_HELPERS.retained
    action = P3_BASE_HELPERS.action
    page = P3_BASE_HELPERS.page
    canonical = P3_BASE_HELPERS.canonical
    candidate_hash = staticmethod(P3_BASE_HELPERS.candidate_hash)
    ev = P3_BASE_HELPERS.ev
    P3_problems = P3_BASE_HELPERS.problems

    def P3_line(self, path, needle):
        found = [i for i, line in enumerate(path.read_text().splitlines(), 1)
                 if needle in line]
        self.assertEqual(1, len(found), (needle, found))
        return found[0]

    def P3_function(self, path, symbol):
        first = self.P3_line(path, 'function ' + symbol + '(')
        lines = path.read_text().splitlines()
        last = next(i for i in range(first + 1, len(lines) + 1)
                    if lines[i - 1].strip() == '}')
        return first, last

    def P3_path(self):
        return {'version': 'bounded-row-ternary-template@1',
                'reachability': 'conditional_only',
                'row_parameter': 'row', 'row_field': 'lifecycleStatus',
                'path': [{'kind': 'interpolation', 'expression_index': 0},
                         {'kind': 'ternary', 'operator': '===',
                          'literal': '已启用', 'branch': 'alternate'},
                         {'kind': 'ternary', 'operator': '===',
                          'literal': '已停用', 'branch': 'consequent'}]}

    def P3_make(self, page_source=P3_TABLE_PAGE, nav_source=P3_TABLE_NAV,
                table_source=P3_TABLE_UI, nested_path=None):
        if not hasattr(self, 'P3_pristine_facts'):
            self.P3_pristine_facts = copy.deepcopy(self.facts)
        self.facts = copy.deepcopy(self.P3_pristine_facts)
        scope, candidate = self.canonical()
        path = self.root / 'inputs/evidence.txt'
        nav = self.root / 'inputs/p3-navigation.js'
        table = self.root / 'inputs/p3-table.js'
        for target, source in ((path, page_source), (nav, nav_source),
                               (table, table_source)):
            target.write_text(source)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.facts['sources']['sources'][0]['sha256'] = digest
        for source_id, source_path in (('SRC-003', nav), ('SRC-004', table)):
            self.facts['sources']['sources'] = [
                record for record in self.facts['sources']['sources']
                if record['id'] != source_id]
            self.facts['sources']['sources'].append({
                'id': source_id, 'status': 'verified',
                'snapshot_path': str(source_path.relative_to(self.root)),
                'sha256': hashlib.sha256(source_path.read_bytes()).hexdigest()})
        refs = ['inputs/source-index.json#SRC-001',
                'inputs/source-index.json#SRC-003',
                'inputs/source-index.json#SRC-004']
        self.retained['source_refs'] = refs[:]
        scope['source_refs'] = refs[:]
        first, last = self.P3_function(path, 'restore')
        candidate.pop('trigger', None)
        candidate['source_evidence'] = [{
            'file': str(path), 'source_sha256': digest, 'line': first,
            'locator': 'function restore('}]
        scope['candidate_record_sha256']['candidate-query'] = self.candidate_hash(candidate)
        self.retained['trigger'] = 'Authorized operator restores the stopped original agent from its existing row control'
        self.retained['local_source_evidence'] = [dict(
            self.ev(path, first, last), observed='Original guarded restore and local result')]
        scope['field_comparison']['trigger'] = {
            'candidate_key': 'candidate-query',
            'candidate_field': '/source_evidence/0/locator',
            'candidate_value': 'function restore(',
            'canonical_value': self.retained['trigger'],
            'assessment': 'same_original_operation',
            'rationale': 'Exact original row control and terminal; no runtime or production claim.'}
        export = self.P3_line(path, "window.Pages['example']=")
        entry = self.P3_line(path, "label:'Agent'")
        nav_start = self.P3_line(nav, 'window.navigateTo=')
        opts = self.P3_line(nav, 'const opts=')
        provenance = {
            'version': 'bounded-table-return@1',
            'reachability': 'conditional_only',
            'route_key': 'agents', 'route_page': 'example', 'route_view': 'agents',
            'route_options_policy': 'default_route_view_no_override',
            'exported_render_symbol': 'render',
            'render_delegate_symbol': 'renderAgents',
            'view_parameter': 'options', 'view_variable': 'view',
            'column_key_literal': 'contactCenterIdentityId',
            'wrapper_symbol': 'shell', 'wrapper_columns_parameter': 'columns',
            'wrapper_columns_argument_index': 2,
            'wrapper_rows_parameter': 'rows', 'wrapper_rows_argument_index': 1,
            'table_sink_callee': 'PlatformUI.table',
            'table_columns_argument_index': 0, 'table_rows_argument_index': 1,
            'exact_event_target_symbol': 'restore',
            'event_object_argument_index': 0,
            'nested_event_path': copy.deepcopy(nested_path or self.P3_path()),
            'evidence': {
                'route_registration': self.ev(nav, self.P3_line(nav, 'const routes='), sid='SRC-003'),
                'route_dispatch': self.ev(nav, nav_start, opts, sid='SRC-003'),
                'route_sink': self.ev(nav, self.P3_line(nav, 'content.innerHTML='), sid='SRC-003'),
                'render': self.ev(path, *self.P3_function(path, 'render')),
                'delegate': self.ev(path, *self.P3_function(path, 'renderAgents')),
                'wrapper': self.ev(path, *self.P3_function(path, 'shell')),
                'table_implementation': self.ev(table, *self.P3_function(table, 'table'), sid='SRC-004'),
                'table_export': self.ev(table, self.P3_line(table, 'window.PlatformUI='), sid='SRC-004')}}
        proof = dict(self.ev(path, first),
            candidate_key='candidate-query', candidate_source_pointer='/source_evidence/0',
            target_symbol='restore', trigger_kind='source_chain',
            business_trigger=self.retained['trigger'],
            rationale='Finite nested original row condition path; conditional source only.')
        proof['source_chain'] = {
            'version': 'bounded-static-chain@1',
            'entry': {'kind': 'template_event',
                      'callee': "window.Pages['example'].restore", 'event': 'click',
                      'evidence': self.ev(path, entry),
                      'ui_alias_evidence': self.ev(path, 2),
                      'render_provenance': provenance},
            'links': [{'kind': 'static_owner_export',
                       'callee': "window.Pages['example'].restore", 'symbol': 'restore',
                       'evidence': self.ev(path, export)}],
            'terminal': {'symbol': 'restore', 'evidence': self.ev(path, first, last)}}
        scope['derived_trigger_evidence'] = proof
        from test_retained_operation_contract import bind_retained_actions
        bind_retained_actions(self.root, self.facts)
        return proof, scope, candidate, path, nav, table

    def P3_base(self):
        made = self.P3_make()
        self.assertEqual([], self.P3_problems(made[1], made[2]))
        return made

    def P3_reject(self, made):
        errors = self.P3_problems(made[1], made[2])
        self.assertTrue(errors, 'Mutated nested control must remain pending')
        return errors

    def P3_source_negative(self, before, after, nested_path=None,
                           table_source=None):
        self.P3_base()
        self.assertEqual(1, P3_TABLE_PAGE.count(before))
        made = self.P3_make(page_source=P3_TABLE_PAGE.replace(before, after, 1),
                            nested_path=nested_path,
                            table_source=table_source or P3_TABLE_UI)
        return self.P3_reject(made)

    def test_P3_actual_nested_restore_path_is_conditional_only(self):
        self.P3_base()
        self.assertFalse(self.retained['production_verified'])
        self.assertIsNone(self.retained['formal_function_id'])

    def test_P3_exact_row_property_can_carry_original_identity(self):
        self.P3_base()
        source = P3_TABLE_PAGE.replace("restore('${v}')", "restore('${row.contactCenterIdentityId}')")
        made = self.P3_make(page_source=source)
        self.assertEqual([], self.P3_problems(made[1], made[2]))

    def test_P3_missing_explicit_profile_keeps_old_P1_rejection(self):
        made = self.P3_base()
        made[0]['source_chain']['entry']['render_provenance'].pop('nested_event_path')
        self.P3_reject(made)

    def test_P3_unknown_version_or_nonconditional_claim_rejected(self):
        for field, value in [('version', 'unbounded-recursion'),
                             ('reachability', 'unconditional')]:
            with self.subTest(field=field):
                made = self.P3_base()
                made[0]['source_chain']['entry']['render_provenance']['nested_event_path'][field] = value
                self.P3_reject(made)

    def test_P3_missing_extra_or_reordered_path_steps_rejected(self):
        original = self.P3_path()['path']
        variants = [[], original[1:], original[:-1],
                    [original[0], original[2], original[1]],
                    original + [original[0]]]
        for path in variants:
            with self.subTest(path=path):
                made = self.P3_base()
                made[0]['source_chain']['entry']['render_provenance']['nested_event_path']['path'] = copy.deepcopy(path)
                self.P3_reject(made)

    def test_P3_wrong_branch_cannot_borrow_target_from_other_button(self):
        for branch in ['consequent', 'missing']:
            with self.subTest(branch=branch):
                made = self.P3_base()
                made[0]['source_chain']['entry']['render_provenance']['nested_event_path']['path'][1]['branch'] = branch
                self.P3_reject(made)

    def test_P3_wrong_interpolation_or_row_metadata_rejected(self):
        mutations = [('expression_index', 1), ('expression_index', -1),
                     ('row_parameter', 'other'), ('row_field', 'otherStatus')]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                made = self.P3_base()
                profile = made[0]['source_chain']['entry']['render_provenance']['nested_event_path']
                if field == 'expression_index':
                    profile['path'][0][field] = value
                else:
                    profile[field] = value
                self.P3_reject(made)

    def test_P3_wrong_literal_operator_or_literal_type_rejected(self):
        for field, value in [('literal', '已启用'), ('literal', True),
                             ('operator', '!=='), ('operator', '==')]:
            with self.subTest(field=field, value=value):
                made = self.P3_base()
                made[0]['source_chain']['entry']['render_provenance']['nested_event_path']['path'][2][field] = value
                self.P3_reject(made)

    def test_P3_conflicting_positive_ancestor_constraints_rejected(self):
        path = self.P3_path()
        path['path'][1]['branch'] = 'consequent'
        source = ("${row.lifecycleStatus==='已启用'?(row.lifecycleStatus==='已停用'?`"
                  + P3_RESTORE_BUTTON + "`:''):''}")
        self.P3_source_negative(P3_NESTED_EXPRESSION, source, nested_path=path)

    def test_P3_excluded_ancestor_value_cannot_be_reintroduced(self):
        path = self.P3_path()
        path['path'][2]['literal'] = '已启用'
        self.P3_source_negative("row.lifecycleStatus==='已停用'?`",
                                "row.lifecycleStatus==='已启用'?`", nested_path=path)

    def test_P3_constant_dead_or_unknown_condition_rejected(self):
        for predicate in ['false', 'unknownFlag', 'runtimeCheck(row)',
                          "row.lifecycleStatus==='已停用'||override"]:
            with self.subTest(predicate=predicate):
                self.P3_source_negative("row.lifecycleStatus==='已停用'?`",
                                        predicate + '?`')

    def test_P3_wrong_row_object_field_rejected(self):
        self.P3_source_negative("restore('${v}')", "restore('${row.tenantId}')")

    def test_P3_wrong_identity_slot_or_column_rejected(self):
        for field, value in [('event_object_argument_index', 1),
                             ('column_key_literal', 'tenantId')]:
            with self.subTest(field=field):
                made = self.P3_base()
                made[0]['source_chain']['entry']['render_provenance'][field] = value
                self.P3_reject(made)

    def test_P3_unused_child_template_is_not_a_returned_control(self):
        self.P3_base()
        source = P3_TABLE_PAGE.replace('  const rows=CloudCallData.agents;',
            '  const rows=CloudCallData.agents;\n  const abandoned=`' + P3_NESTED_EXPRESSION + '`;')
        source = source.replace(P3_RENDERER_MARKUP,
                                '<div class="table-actions">No restore control</div>')
        self.P3_reject(self.P3_make(page_source=source))

    def test_P3_attribute_ancestor_does_not_activate_nested_button(self):
        self.P3_source_negative(P3_RENDERER_MARKUP,
                                '<div data-cached="' + P3_NESTED_EXPRESSION + '"></div>')

    def test_P3_raw_inert_and_comment_ancestors_survive_child_traversal(self):
        wrappers = [('<script>', '</script>'), ('<style>', '</style>'),
                    ('<textarea>', '</textarea>'), ('<title>', '</title>'),
                    ('<template>', '</template>'), ('<noscript>', '</noscript>'),
                    ('<!--', '-->')]
        for first, last in wrappers:
            with self.subTest(ancestor=first):
                self.P3_source_negative(P3_RENDERER_MARKUP,
                                        first + P3_NESTED_EXPRESSION + last)

    def test_P3_intermediate_template_keeps_outer_raw_context(self):
        for first, last in [('<textarea>', '</textarea>'),
                            ('<template>', '</template>')]:
            with self.subTest(ancestor=first):
                path = self.P3_path()
                path['path'].insert(2, {'kind': 'interpolation', 'expression_index': 0})
                source = ("${row.lifecycleStatus==='已启用'?`" + P3_DISABLE_BUTTON
                          + '`:`' + first + "${row.lifecycleStatus==='已停用'?`"
                          + P3_RESTORE_BUTTON + "`:''}" + last + '`}')
                self.P3_source_negative(P3_NESTED_EXPRESSION, source, nested_path=path)

    def test_P3_fake_UI_or_table_body_rejected(self):
        self.P3_source_negative('const ui=PlatformUI;', 'const ui=Diagnostics;')
        self.P3_base()
        fake = P3_TABLE_UI.replace('col.render(row[col.key],row)', 'row[col.key]')
        self.P3_reject(self.P3_make(table_source=fake))

    def test_P3_local_UI_shadow_rejected(self):
        self.P3_source_negative('function shell(key,rows,columns,primary,help){',
                                'function shell(key,rows,columns,primary,help){\n  const ui=Diagnostics;')

    def test_P3_rebound_original_owner_rejected(self):
        self.P3_source_negative("window.Pages['example']={render,restore,disable,inspect};",
                                "window.Pages['example']={render,restore,disable,inspect};\nwindow.Pages['example'].restore=unrelated;")

    def test_P3_wrong_source_SHA_or_quote_rejected(self):
        for field, value in [('source_sha256', '0' * 64),
                             ('source_quote', 'A quote from another control')]:
            with self.subTest(field=field):
                made = self.P3_base()
                made[0]['source_chain']['entry']['evidence'][field] = value
                self.P3_reject(made)
        made = self.P3_base()
        from operation_attribution import StrictReview
        review = StrictReview(self.root, self.facts)
        self.assertEqual([], review.reviewed_canonical_errors('canonical-query', self.retained))
        made[2]['completion_result'] = 'An unreviewed different business result'
        errors = review.reviewed_canonical_errors('canonical-query', self.retained)
        self.assertIn('candidate SHA differs', '\n'.join(errors))

    def test_P3_wrong_original_terminal_or_incomplete_body_rejected(self):
        for mutation in ['symbol', 'body']:
            with self.subTest(mutation=mutation):
                made = self.P3_base()
                terminal = made[0]['source_chain']['terminal']
                if mutation == 'symbol':
                    terminal['symbol'] = 'disable'
                else:
                    first = self.P3_function(made[3], 'restore')[0]
                    terminal['evidence'] = self.ev(made[3], first)
                self.P3_reject(made)

    def test_P3_wrong_export_cannot_bind_same_named_terminal(self):
        self.P3_source_negative('{render,restore,disable,inspect}',
                                '{render,restore:disable,disable,inspect}')

    def test_P3_unapproved_escaped_helper_combination_stays_rejected(self):
        self.P3_base()
        helper = "const command=(method,...args)=>esc(`window.Pages['example'].${method}(${args.map(value=>JSON.stringify(value)).join(',')})`);"
        source = P3_TABLE_PAGE.replace('const ui=PlatformUI;', 'const ui=PlatformUI,esc=ui.escape;\n' + helper)
        source = source.replace(P3_RESTORE_BUTTON,
            '<button onclick="${command(\'restore\',v)}">Restore</button>')
        self.P3_reject(self.P3_make(page_source=source))

    def test_P3_renderer_attributes_cannot_escape_or_mutate_whole_row(self):
        readonly = '<div class="table-actions" data-proof="${row.lifecycleStatus}">'
        attacks = [
            '<div class="table-actions" data-proof="${(()=>{const hidden=row;hidden.lifecycleStatus=\'已停用\';hidden.contactCenterIdentityId=\'different\';return \'\';})()}">',
            '<div class="table-actions" data-proof="${mutate(row)}">']
        for attack in attacks:
            with self.subTest(attribute=attack):
                path = self.P3_path()
                path['path'][0]['expression_index'] = 1
                source = P3_TABLE_PAGE.replace('<div class="table-actions">', readonly)
                made = self.P3_make(page_source=source, nested_path=path)
                self.assertEqual([], self.P3_problems(made[1], made[2]))
                mutated = source.replace(readonly, attack, 1)
                self.P3_reject(self.P3_make(page_source=mutated, nested_path=path))

    def test_P3_null_literal_must_be_explicit_in_selected_step(self):
        path = self.P3_path()
        path['path'][1]['literal'] = None
        source = P3_TABLE_PAGE.replace("row.lifecycleStatus==='已启用'?`",
                                       'row.lifecycleStatus===null?`', 1)
        made = self.P3_make(page_source=source, nested_path=path)
        self.assertEqual([], self.P3_problems(made[1], made[2]))
        made[0]['source_chain']['entry']['render_provenance']['nested_event_path']['path'][1].pop('literal')
        self.P3_reject(made)


P4_DELEGATED_HELPER = "const command=(method,...args)=>esc(`window.Pages['example'].${method}(${args.map(value=>JSON.stringify(value)).join(',')})`);"
P4_DELEGATED_PAGE = P1_TABLE_PAGE.replace(
    'const ui=PlatformUI;', 'const ui=PlatformUI,esc=ui.escape;\n' + P4_DELEGATED_HELPER).replace(
    "window.Pages['example'].openAgent('${v}')", "${command('openAgent',v)}")
P4_ESCAPE_CALLBACK_SCOPE = '''function summaries(){
  const groups=relations.map(row=>{
    const group=row.group,reasons=[];
    if(group.disabled)reasons.push('Unavailable');
    return `<p>${reasons.length?reasons.map(esc).join(';'):''}</p>`;
  });
  return groups.join('');
}
'''


class DelegatedEscapedTableEventP4Tests(unittest.TestCase):
    """Explicit P1/P2 composition; no inheritance/recounting of old tests."""
    setUp = BoundedTableReturnP1Tests.setUp
    tearDown = BoundedTableReturnP1Tests.tearDown
    retained = BoundedTableReturnP1Tests.retained
    action = BoundedTableReturnP1Tests.action
    page = BoundedTableReturnP1Tests.page
    canonical = BoundedTableReturnP1Tests.canonical
    candidate_hash = staticmethod(BoundedTableReturnP1Tests.candidate_hash)
    ev = BoundedTableReturnP1Tests.ev
    problems = BoundedTableReturnP1Tests.problems
    p1_line = BoundedTableReturnP1Tests.p1_line
    p1_function = BoundedTableReturnP1Tests.p1_function
    p1_fixture = BoundedTableReturnP1Tests.p1_fixture

    def p4_fixture(self, page_source=P4_DELEGATED_PAGE,
                   table_source=UI_SUPPORT_SOURCE):
        table_source = table_source.replace('window.PlatformUI =', 'window.PlatformUI=')
        made = self.p1_fixture(page_source=page_source,
                               table_source=table_source)
        proof, scope, candidate, page, nav, table = made
        proof['source_chain']['entry']['render_provenance']['event_helper_provenance'] = {
            'version': 'bounded-fixed-escaped-table-event@1',
            'reachability': 'conditional_only',
            'handler_helper_evidence': self.ev(page, self.p1_line(page, 'const command=(method,...args)=>')),
            'helper_call': {'name': 'command', 'literal_method': 'openAgent'},
            'escape_alias_evidence': self.ev(page, 2),
            'escape_implementation_evidence': self.ev(
                table, *self.p1_function(table, 'escape'), sid='SRC-004')}
        return made

    def p4_positive(self):
        made = self.p4_fixture()
        self.assertEqual([], self.problems(made[1], made[2]))
        return made

    def p4_reject(self, made):
        errors = self.problems(made[1], made[2])
        self.assertTrue(errors, 'Unproven helper/table combination must stay pending')
        return errors

    def p4_source_negative(self, before, after, table_source=None):
        self.p4_positive()
        self.assertEqual(1, P4_DELEGATED_PAGE.count(before))
        return self.p4_reject(self.p4_fixture(
            page_source=P4_DELEGATED_PAGE.replace(before, after, 1),
            table_source=table_source or UI_SUPPORT_SOURCE))

    def p4_callback_fixture(self, callback_source=P4_ESCAPE_CALLBACK_SCOPE):
        source = P4_DELEGATED_PAGE.replace('function openAgent(',
                                           callback_source + 'function openAgent(', 1)
        made = self.p4_fixture(page_source=source)
        proof, _, _, page, *_ = made
        proof['source_chain']['entry']['render_provenance'][
            'event_helper_provenance']['escape_callback_uses'] = [{
                'version': 'bounded-local-string-array-map@1',
                'array_symbol': 'reasons',
                'evidence': self.ev(page, *self.p1_function(page, 'summaries'))}]
        return made

    def p4_callback_positive(self):
        made = self.p4_callback_fixture()
        self.assertEqual([], self.problems(made[1], made[2]))
        return made

    def test_p4_explicit_delegated_table_and_fixed_escaped_event(self):
        made = self.p4_positive()
        self.assertFalse(self.retained['production_verified'])
        self.assertIsNone(self.retained['formal_function_id'])
        self.assertEqual('conditional_only', made[0]['source_chain']['entry'][
            'render_provenance']['event_helper_provenance']['reachability'])

    def test_p4_exact_column_row_identity_property_is_preserved(self):
        self.p4_positive()
        source = P4_DELEGATED_PAGE.replace("command('openAgent',v)",
                                           "command('openAgent',row.contactCenterIdentityId)")
        made = self.p4_fixture(page_source=source)
        self.assertEqual([], self.problems(made[1], made[2]))

    def test_p4_no_explicit_composition_keeps_old_rejection(self):
        made = self.p4_positive()
        made[0]['source_chain']['entry']['render_provenance'].pop('event_helper_provenance')
        self.p4_reject(made)

    def test_p4_wrong_literal_helper_owner_or_method_is_rejected(self):
        for before, after in [
                ("window.Pages['example'].${method}", "window.Pages['other'].${method}"),
                ("command('openAgent',v)", "command('unrelated',v)")]:
            with self.subTest(after=after):
                self.p4_source_negative(before, after)

    def test_p4_wrong_helper_metadata_is_not_a_binding(self):
        for field, value in [('name', 'unrelated'), ('literal_method', 'unrelated')]:
            with self.subTest(field=field):
                made = self.p4_positive()
                made[0]['source_chain']['entry']['render_provenance'][
                    'event_helper_provenance']['helper_call'][field] = value
                self.p4_reject(made)

    def test_p4_wrong_identity_or_slot_is_rejected(self):
        for value in ['row.otherId', "'fixed-id'", 'row']:
            with self.subTest(identity=value):
                self.p4_source_negative("command('openAgent',v)",
                                        "command('openAgent'," + value + ')')
        made = self.p4_positive()
        made[0]['source_chain']['entry']['render_provenance'][
            'event_object_argument_index'] = 1
        self.p4_reject(made)

    def test_p4_wrong_table_return_cannot_borrow_the_helper(self):
        self.p4_source_negative('${ui.table(columns,rows,{})}',
                                '<div>Unused table control</div>')
        self.p4_positive()
        fake = UI_SUPPORT_SOURCE.replace('col.render(row[col.key], row)',
                                         'row[col.key]')
        self.p4_reject(self.p4_fixture(table_source=fake))

    def test_p4_escape_alias_and_actual_implementation_are_required(self):
        self.p4_source_negative('esc=ui.escape;', 'esc=Diagnostics.escape;')
        self.p4_positive()
        fake = UI_SUPPORT_SOURCE.replace(".replace(/&/g, '&amp;')", '')
        self.p4_reject(self.p4_fixture(table_source=fake))

    def test_p4_helper_JSON_or_UI_shadow_and_rebinding_are_rejected(self):
        for before, after in [
                ('function renderAgents(){',
                 'function renderAgents(){\n  const command=unrelated;'),
                ('let view=\'agents\';',
                 "const JSON=Diagnostics;\nlet view='agents';"),
                ('let view=\'agents\';',
                 "command=unrelated;\nlet view='agents';"),
                ('let view=\'agents\';',
                 "ui.table=Diagnostics.table;\nlet view='agents';")]:
            with self.subTest(after=after):
                self.p4_source_negative(before, after)

    def test_p4_helper_must_keep_exact_JSON_map_and_escape(self):
        for before, after in [('JSON.stringify(value)', 'String(value)'),
                              (".join(',')", ".join(';')"),
                              ('args.map(value=>', 'other.map(value=>')]:
            with self.subTest(after=after):
                self.p4_source_negative(before, after)

    def test_p4_delegated_helper_renderer_parameters_are_readonly(self):
        for expression in ["v='other'", "(v)='other'", '++v', 'v*=0',
                           '--v', 'v/=2', 'v%=2', 'v**=2', 'v<<=1',
                           'v>>=1', 'v>>>=1', 'v&=1', 'v|=1', 'v^=1',
                           'v&&=1', 'v||=1', 'v??=1', '++(v)',
                           "row.contactCenterIdentityId='other'",
                           "(row.contactCenterIdentityId)='other'",
                           "(row).contactCenterIdentityId='other'",
                           'delete row.contactCenterIdentityId']:
            with self.subTest(expression=expression):
                self.p4_source_negative('<button onclick=',
                                        '<button title="${'+expression+'}" onclick=')

    def test_p4_value_identity_and_selected_row_identity_have_distinct_escape_scope(self):
        self.p4_positive()
        source = P4_DELEGATED_PAGE.replace('<button onclick=',
                                           '<button title="${mayRead(row)}" onclick=')
        made = self.p4_fixture(page_source=source)
        self.assertEqual([], self.problems(made[1], made[2]))
        # A row-key identity cannot borrow that unproved whole-row transfer.
        self.p4_reject(self.p4_fixture(page_source=source.replace(
            "command('openAgent',v)", "command('openAgent',row.contactCenterIdentityId)")))
        for escape in ['mutate(row)',
                       "(()=>{const hidden=row;hidden.contactCenterIdentityId='other';return '';})()"]:
            with self.subTest(escape=escape):
                self.p4_positive()
                source = P4_DELEGATED_PAGE.replace(
                    "command('openAgent',v)", "command('openAgent',row.contactCenterIdentityId)")
                source = source.replace('<button onclick=',
                                        '<button title="${'+escape+'}" onclick=')
                self.p4_reject(self.p4_fixture(page_source=source))

    def test_p4_source_bindings_and_conditional_optin_remain_exact(self):
        for field, value in [('source_sha256', '0' * 64),
                              ('source_quote', 'Unrelated helper source')]:
            with self.subTest(field=field):
                made = self.p4_positive()
                made[0]['source_chain']['entry']['render_provenance'][
                    'event_helper_provenance']['handler_helper_evidence'][field] = value
                self.p4_reject(made)
        made = self.p4_positive()
        made[0]['source_chain']['entry']['render_provenance'][
            'event_helper_provenance']['reachability'] = 'unconditional'
        self.p4_reject(made)

    def test_p4_explicit_pure_escape_callback_local_string_array(self):
        self.p4_callback_positive()

    def test_p4_escape_callback_requires_explicit_exact_use_evidence(self):
        made = self.p4_callback_positive()
        made[0]['source_chain']['entry']['render_provenance'][
            'event_helper_provenance'].pop('escape_callback_uses')
        self.p4_reject(made)
        for field, value in [('version', 'unknown'), ('array_symbol', 'other')]:
            with self.subTest(field=field):
                made = self.p4_callback_positive()
                made[0]['source_chain']['entry']['render_provenance'][
                    'event_helper_provenance']['escape_callback_uses'][0][field] = value
                self.p4_reject(made)

    def test_p4_escape_array_copy_forward_rewrite_or_map_override_rejected(self):
        for statement in ['const other=reasons;', 'consume(reasons);',
                          'reasons=[];', 'reasons.map=unrelated;',
                          "reasons['map']=unrelated;",
                          "Object.assign(reasons,{map:unrelated});",
                          'Object.setPrototypeOf(reasons,unrelated);',
                          'Array.prototype.map=unrelated;',
                          'Object.assign(Array.prototype,{map:unrelated});',
                          "Object.defineProperty(Array.prototype,'map',{value:unrelated});",
                          'const prototype=Array.prototype;prototype.map=unrelated;',
                          'const prototype=window.Array.prototype;consume(prototype);',
                          'reasons.length*=0;', '(reasons.length)=0;',
                          '++reasons.length;', '--reasons.length;',
                          'delete reasons.length;', 'reasons.length&&=0;',
                          'for(reasons.length of other){}',
                          'const hidden=reasons;consume(hidden);']:
            with self.subTest(statement=statement):
                self.p4_callback_positive()
                source = P4_ESCAPE_CALLBACK_SCOPE.replace(
                    "    return `<p>", '    ' + statement + '\n    return `<p>')
                self.p4_reject(self.p4_callback_fixture(source))

    def test_p4_escape_array_needs_its_own_const_literal_and_string_push(self):
        for before, after in [('reasons=[]', 'reasons=other'),
                              ('const group=row.group,reasons=[];',
                               'let group=row.group,reasons=[];'),
                              ("reasons.push('Unavailable')", 'reasons.push(row)'),
                              ('reasons.map(esc)', 'reasons.map(unrelated)')]:
            with self.subTest(after=after):
                self.p4_callback_positive()
                self.p4_reject(self.p4_callback_fixture(
                    P4_ESCAPE_CALLBACK_SCOPE.replace(before, after, 1)))

    def test_p4_escape_use_does_not_allow_other_function_or_receiver_escapes(self):
        for statement in ['const hidden=esc;', 'consume(esc);',
                          'const hidden=ui;', 'consume(ui);']:
            with self.subTest(statement=statement):
                self.p4_callback_positive()
                source = P4_ESCAPE_CALLBACK_SCOPE.replace(
                    '  const groups=', '  ' + statement + '\n  const groups=', 1)
                self.p4_reject(self.p4_callback_fixture(source))

    def test_p4_escape_callback_full_scope_SHA_and_quote_are_required(self):
        for mutation in ['scope', 'sha', 'quote']:
            with self.subTest(mutation=mutation):
                made = self.p4_callback_positive()
                proof, _, _, page, *_ = made
                rec = proof['source_chain']['entry']['render_provenance'][
                    'event_helper_provenance']['escape_callback_uses'][0]
                if mutation == 'scope':
                    rec['evidence'] = self.ev(page, self.p1_line(page, 'reasons.map(esc)'))
                elif mutation == 'sha':
                    rec['evidence']['source_sha256'] = '0' * 64
                else:
                    rec['evidence']['source_quote'] = 'A different function scope'
                self.p4_reject(made)


P4_QUOTED_PAGE='''(function(){
const ui=PlatformUI;
function render(options){
  if(options?.view==='audit')return renderAudit();
  if(options?.view==='other')return other();
  return ui.empty('No view');
}
function renderAudit(){
  if(!allowed())return ui.empty('No access');
  return `<section>${ui.pageHeader('Audit','Details','<button onclick="window.Pages[\\'example\\'].save()">Export</button>')}</section>`;
}
function save(){
  return persistExistingObject();
}
window.Pages['example']={render,renderAudit,save};
})();
'''
P4_QUOTED_UI='''(function(){
function pageHeader(title,description,actions,badge){
  return `<header><h1>${title}</h1><p>${description}</p><div>${actions||''}</div></header>`;
}
window.PlatformUI={pageHeader};
})();
'''
P4_QUOTED_NAV=P1_TABLE_NAV.replace("view:'agents'","view:'audit'")
class P4QuotedControlTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
    ev=BoundedSourceChainTests.ev
    make=BoundedSourceChainTests.make
    problems=BoundedSourceChainTests.problems
    def fixture(self,source=P4_QUOTED_PAGE,nav_source=P4_QUOTED_NAV,ui_source=P4_QUOTED_UI):
        def line(text,needle):return next(i for i,l in enumerate(text.splitlines(),1)if needle in l)
        first=line(source,'function save(');last=first+2
        proof,scope,candidate,path=self.make(source,line(source,'<button'),line(source,"window.Pages['example']="),first,last)
        nav=self.root/'inputs/p4-nav.js';ui=self.root/'inputs/p4-ui.js';nav.write_text(nav_source);ui.write_text(ui_source)
        for sid,p in [('SRC-003',nav),('SRC-004',ui)]:
            self.facts['sources']['sources']=[s for s in self.facts['sources']['sources']if s['id']!=sid];self.facts['sources']['sources'].append({'id':sid,'status':'verified','snapshot_path':str(p.relative_to(self.root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        refs=['inputs/source-index.json#SRC-001','inputs/source-index.json#SRC-003','inputs/source-index.json#SRC-004'];self.retained['source_refs']=refs[:];scope['source_refs']=refs[:];proof['source_refs']=refs[:]
        def ev(p,start,end=None,sid='SRC-001'):
            e=self.ev(p,start,end,sid);e['source_refs']=refs[:];return e
        e=proof['source_chain']['entry'];e.pop('opening_evidence',None);e['evidence']=ev(path,line(source,'<button'));e['ui_alias_evidence']=ev(path,2)
        e['quoted_control_provenance']={'version':'bounded-returned-quoted-control@1','reachability':'conditional_only','route_options_policy':'default_route_view_no_override','route_key':'agents','route_page':'example','route_view':'audit','exported_render_symbol':'render','render_delegate_symbol':'renderAudit','view_parameter':'options','ui_helper':'pageHeader','html_argument_index':2,'evidence':{'route_registration':ev(nav,2,sid='SRC-003'),'route_dispatch':ev(nav,3,7,'SRC-003'),'route_sink':ev(nav,8,sid='SRC-003'),'render':ev(path,line(source,'function render('),line(source,'function renderAudit(')-1),'delegate':ev(path,line(source,'function renderAudit('),first-1),'ui_implementation':ev(ui,2,line(ui_source,'window.PlatformUI=')-1,'SRC-004'),'ui_export':ev(ui,line(ui_source,'window.PlatformUI='),sid='SRC-004')}}
        return proof,scope,candidate,path
    def test_p4_quoted_control_exact_original_return_passes(self):
        _,s,c,_=self.fixture();self.assertEqual([],self.problems(s,c))
    def test_p4_quoted_control_without_optin_keeps_old_rejection(self):
        p,s,c,_=self.fixture();p['source_chain']['entry'].pop('quoted_control_provenance');self.assertTrue(self.problems(s,c))
    def test_p4_wrong_profile_or_HTML_slot_rejected(self):
        for key,value in [('version','unknown'),('reachability','unconditional'),('html_argument_index',0),('ui_helper','diagnostic')]:
            with self.subTest(key=key):
                p,s,c,_=self.fixture();p['source_chain']['entry']['quoted_control_provenance'][key]=value;self.assertTrue(self.problems(s,c))
    def test_p4_quoted_wrong_owner_or_method_rejected(self):
        for source in [P4_QUOTED_PAGE.replace('.save()','.other()'),P4_QUOTED_PAGE.replace("example\\'","another\\'")]:
            with self.subTest(source=source):
                _,s,c,_=self.fixture(source);self.assertTrue(self.problems(s,c))
    def test_p4_returned_UI_helper_must_consume_HTML_argument(self):
        for ui in [P4_QUOTED_UI.replace("${actions||''}","${description||''}"),P4_QUOTED_UI.replace("${actions||''}","${escape(actions)}"),P4_QUOTED_UI.replace("${actions||''}","<script>${actions||''}</script>"),P4_QUOTED_UI.replace("${actions||''}","<div title=\"${actions||''}\">No</div>")]:
            with self.subTest(ui=ui):
                _,s,c,_=self.fixture(ui_source=ui);self.assertTrue(self.problems(s,c))
    def test_p4_quoted_inert_or_comment_HTML_rejected(self):
        for tag in ['script','template','textarea','noscript']:
            with self.subTest(tag=tag):
                source=P4_QUOTED_PAGE.replace('<button onclick=',f'<{tag}><button onclick=').replace('Export</button>',f'Export</button></{tag}>');_,s,c,_=self.fixture(source);self.assertTrue(self.problems(s,c))
        _,s,c,_=self.fixture(P4_QUOTED_PAGE.replace('<button onclick=','<!-- <button onclick=').replace('Export</button>','Export</button> -->'));self.assertTrue(self.problems(s,c))
    def test_p4_helper_result_in_attribute_or_script_not_active_HTML(self):
        for src in [P4_QUOTED_PAGE.replace('`<section>${ui.pageHeader','`<section title="${ui.pageHeader').replace("Export</button>')}</section>`", "Export</button>')}\">No</section>`"),P4_QUOTED_PAGE.replace('`<section>${ui.pageHeader','`<script>${ui.pageHeader').replace('</section>`','</script>`')]:
            with self.subTest(src=src):
                _,s,c,_=self.fixture(src);self.assertTrue(self.problems(s,c))
    def test_p4_wrong_route_view_and_options_override_rejected(self):
        for nav in [P4_QUOTED_NAV.replace("view:'audit'","view:'other'"),P4_QUOTED_NAV.replace('view:route.view','view:options.view')]:
            with self.subTest(nav=nav):
                _,s,c,_=self.fixture(nav_source=nav);self.assertTrue(self.problems(s,c))
    def test_p4_shadow_rebound_or_fake_UI_alias_rejected(self):
        for src in [P4_QUOTED_PAGE.replace('ui=PlatformUI','ui=Diagnostics'),P4_QUOTED_PAGE.replace('function renderAudit(){','function renderAudit(){\n  const ui=Diagnostics;'),P4_QUOTED_PAGE.replace('function renderAudit(){','function renderAudit(){\n  ui=Diagnostics;')]:
            with self.subTest(src=src):
                _,s,c,_=self.fixture(src);self.assertTrue(self.problems(s,c))
    def test_p4_dead_or_unused_quoted_control_rejected(self):
        for src in [P4_QUOTED_PAGE.replace('  return `<section>','  if(false)return `<section>'),P4_QUOTED_PAGE.replace('  return `<section>','  const diagnostic=`<section>'),P4_QUOTED_PAGE.replace('  return `<section>','  return ui.empty("before");\n  return `<section>'),P4_QUOTED_PAGE.replace('  return `<section>','  while(true){}\n  return `<section>')]:
            with self.subTest(src=src):
                _,s,c,_=self.fixture(src);self.assertTrue(self.problems(s,c))
    def test_p4_UI_param_rewrite_or_quoted_unknown_escape_rejected(self):
        for ui in [P4_QUOTED_UI.replace('  return `<header>','  actions="";\n  return `<header>'),P4_QUOTED_UI.replace('  return `<header>','  mutate(actions);\n  return `<header>')]:
            with self.subTest(ui=ui):
                _,s,c,_=self.fixture(ui_source=ui);self.assertTrue(self.problems(s,c))
        _,s,c,_=self.fixture(P4_QUOTED_PAGE.replace('Export</button>','Export\\x20</button>'));self.assertTrue(self.problems(s,c))
    def test_p4_quote_SHA_and_same_named_export_stay_strict(self):
        p,s,c,_=self.fixture();p['source_chain']['entry']['quoted_control_provenance']['evidence']['ui_implementation']['source_sha256']='0'*64;self.assertTrue(self.problems(s,c))
        p,s,c,_=self.fixture();p['source_chain']['entry']['quoted_control_provenance']['evidence']['delegate']['source_quote']='fabricated';self.assertTrue(self.problems(s,c))


    def test_p4_quoted_hidden_inert_and_style_ancestors_rejected(self):
        for attribute in ['hidden', 'inert', 'style="display:none"']:
            with self.subTest(attribute=attribute):
                _,scope,candidate,_=self.fixture()
                self.assertEqual([],self.problems(scope,candidate))
                source=P4_QUOTED_PAGE.replace('<button onclick=', '<div '+attribute+'><button onclick=').replace('Export</button>', 'Export</button></div>')
                _,scope,candidate,_=self.fixture(source)
                self.assertTrue(self.problems(scope,candidate))

    def test_p4_quoted_UI_receiver_copy_and_forward_are_rejected(self):
        for statement in ['const other=ui;other.pageHeader=unrelated;', 'consume(ui);']:
            with self.subTest(statement=statement):
                _,scope,candidate,_=self.fixture()
                self.assertEqual([],self.problems(scope,candidate))
                source=P4_QUOTED_PAGE.replace('function renderAudit(){', 'function renderAudit(){\n  '+statement)
                _,scope,candidate,_=self.fixture(source)
                self.assertTrue(self.problems(scope,candidate))

"""P4 core guard unit additions, not a proof of lexical role/completion support."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch as P4_ANCHOR_PATCH
import operation_attribution as P4_ANCHOR_MODULE
import bounded_source_chain as P4_ANCHOR_BSC


class P4OriginalAnchorCoreGuardTests(unittest.TestCase):
    def setUp(self):
        self.P4_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.P4_tmp.cleanup)
        self.P4_path = Path(self.P4_tmp.name).resolve() / 'original.js'
        self.P4_path.write_text("const header=readonly?'Rule detail':'Edit rule';\n")
        self.P4_candidate = {'draft_key':'original-rule-detail','source_evidence':[
            {'path':str(self.P4_path),'line':1,'locator':"const header=readonly?'Rule detail'"}]}
        self.P4_field = '/source_evidence/0/locator'
        self.P4_record = {'trigger':'Inspect the selected original inbound rule'}
        self.P4_proof = {'candidate_key':'original-rule-detail',
            'candidate_source_pointer':'/source_evidence/0','business_trigger':self.P4_record['trigger'],
            'rationale':'Only exercise the typed core guard; lexical role support is mocked separately.',
            'trigger_kind':'source_chain','target_symbol':'openRoute','source_refs':[],
            'file':str(self.P4_path),'source_sha256':self.P4_sha(),
            'line_start':1,'line_end':1,'source_quote':self.P4_path.read_text().rstrip('\n')}
        self.P4_proof['original_anchor'] = self.P4_contract()
        self.P4_review = P4_ANCHOR_MODULE.StrictReview.__new__(P4_ANCHOR_MODULE.StrictReview)
        self.P4_review.references = lambda record: []
        self.P4_review.path = lambda value, **kwargs: self.P4_path
        self.P4_review.evidence_source_chain_errors = lambda observation, record: []

    def P4_sha(self):
        return hashlib.sha256(self.P4_path.read_bytes()).hexdigest()

    def P4_candidate_sha(self):
        return hashlib.sha256(json.dumps(self.P4_candidate,ensure_ascii=False,
            sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def P4_contract(self):
        return {'version':'bounded-original-anchor-role@1','role':'result_expression_noncallable',
            'candidate_key':'original-rule-detail','candidate_record_sha256':self.P4_candidate_sha(),
            'source_pointer':'/source_evidence/0','source_field_pointer':self.P4_field,
            'source_sha256':self.P4_sha(),'implementation_symbol':'openRoute',
            'role_evidence':{'unit_marker':'metadata alone is deliberately insufficient'}}

    def P4_core(self, proof=None):
        return self.P4_review.original_anchor_context('original-rule-detail',self.P4_candidate,
            self.P4_field,proof or self.P4_proof,self.P4_candidate['source_evidence'][0],
            self.P4_path,self.P4_sha())

    def P4_derive(self, proof=None):
        return self.P4_review.derived_trigger_errors('original-rule-detail',self.P4_candidate,
            self.P4_field,{},self.P4_record) if proof is None else self.P4_review.derived_trigger_errors(
            'original-rule-detail',self.P4_candidate,self.P4_field,
            {'derived_trigger_evidence':proof},self.P4_record)

    def test_P4_core_pins_original_context_without_claiming_role_proof(self):
        context, errors = self.P4_core()
        self.assertEqual([],errors)
        self.assertEqual(self.P4_candidate_sha(),context['candidate_record_sha256'])
        self.assertEqual(self.P4_path.read_text().rstrip('\n'),context['source_quote'])
        self.assertEqual('openRoute',context['implementation_symbol'])

    def test_P4_core_rejects_mismatched_required_bindings(self):
        changes = [('version','unbounded@1'),('role',[]),('role','any_function'),
            ('candidate_key','other'),('candidate_record_sha256','0'*64),
            ('source_pointer','/source_evidence/1'),('source_field_pointer','trigger'),
            ('source_sha256','0'*64),('implementation_symbol','unrelated'),('role_evidence',{})]
        for field,value in changes:
            with self.subTest(field=field,value=value):
                self.assertEqual([],self.P4_core()[1])
                proof=copy.deepcopy(self.P4_proof);proof['original_anchor'][field]=value
                self.assertTrue(self.P4_core(proof)[1])

    def test_P4_core_rejects_nonobject_optin(self):
        for value in [None,False,[],"role"]:
            with self.subTest(value=value):
                self.assertEqual([],self.P4_core()[1])
                proof=copy.deepcopy(self.P4_proof);proof['original_anchor']=value
                self.assertTrue(self.P4_core(proof)[1])

    def test_P4_core_requires_locator_in_original_anchor_span(self):
        self.assertEqual([],self.P4_core()[1])
        self.P4_candidate['source_evidence'][0]['locator']='const unused = true'
        proof=copy.deepcopy(self.P4_proof)
        proof['original_anchor']['candidate_record_sha256']=self.P4_candidate_sha()
        self.assertIn('absent from its original line span','\n'.join(self.P4_core(proof)[1]))

    def test_P4_default_noncallable_locator_still_rejected(self):
        proof=copy.deepcopy(self.P4_proof);proof.pop('original_anchor')
        with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'source_chain_errors',side_effect=AssertionError('must not reach chain')):
            self.assertIn('target is not bound','\n'.join(self.P4_derive(proof)))

    def test_P4_optin_is_source_chain_only(self):
        proof=copy.deepcopy(self.P4_proof);proof['trigger_kind']='operation_callsite'
        self.assertIn('only source_chain','\n'.join(self.P4_derive(proof)))

    def test_P4_missing_lexical_validator_is_pending(self):
        with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',None,create=True):
            self.assertIn('validator is unavailable','\n'.join(self.P4_derive(self.P4_proof)))

    def test_P4_invalid_lexical_validator_return_is_pending(self):
        for result in [None,True,(),[None]]:
            with self.subTest(result=result):
                with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',return_value=result,create=True):
                    self.assertIn('invalid lexical validator result','\n'.join(self.P4_derive(self.P4_proof)))

    def test_P4_lexical_rejection_cannot_be_overridden(self):
        with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',return_value=['readonly path not proven'],create=True), P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'source_chain_errors',side_effect=AssertionError('must not reach chain')):
            self.assertEqual(['readonly path not proven'],self.P4_derive(self.P4_proof))

    def test_P4_lexical_failure_remains_pending(self):
        with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',side_effect=ValueError('unknown role syntax'),create=True):
            self.assertIn('unknown role syntax','\n'.join(self.P4_derive(self.P4_proof)))

    def test_P4_validated_role_still_requires_ordinary_source_chain(self):
        with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',return_value=[],create=True) as role, P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'source_chain_errors',return_value=['ordinary chain pending']) as chain:
            self.assertEqual(['ordinary chain pending'],self.P4_derive(self.P4_proof))
            self.assertEqual(self.P4_candidate_sha(),role.call_args.args[-1]['candidate_record_sha256'])
            self.assertEqual(1,chain.call_count)

    def test_P4_other_roles_cannot_bypass_old_locator_target(self):
        for value in ['complete_callable_handler','initiator_only_needs_save_result','data_helper_with_callable_alternative']:
            with self.subTest(role=value):
                proof=copy.deepcopy(self.P4_proof);proof['original_anchor']['role']=value
                with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',return_value=[],create=True) as role:
                    self.assertIn('target is not bound','\n'.join(self.P4_derive(proof)))
                    role.assert_not_called()

    def test_P4_initiator_followup_rejection_precedes_chain(self):
        self.P4_path.write_text('function openImport(){return openPicker();}\n')
        self.P4_candidate['source_evidence'][0]['locator']='function openImport('
        proof=copy.deepcopy(self.P4_proof)
        proof.update(source_sha256=self.P4_sha(),source_quote=self.P4_path.read_text().rstrip('\n'),target_symbol='openImport')
        proof['original_anchor']=self.P4_contract()
        proof['original_anchor'].update(role='initiator_only_needs_save_result',implementation_symbol='openImport')
        self.assertEqual([],self.P4_core(proof)[1])
        with P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'original_anchor_role_errors',return_value=['actual follow-up save/result is not proven'],create=True), P4_ANCHOR_PATCH.object(P4_ANCHOR_BSC,'source_chain_errors',side_effect=AssertionError('must not count opener')):
            self.assertIn('follow-up save/result','\n'.join(self.P4_derive(proof)))


P4_RAW_PAGE='''(function(){
const ui=PlatformUI;
function render(options){
  if(options?.view==='audit')return renderAudit();
  if(options?.view==='other')return other();
  return ui.empty('No view');
}
function renderAudit(){
  if(!allowed())return ui.empty('No access');
  const rows=[{id:'one'}];
  return `<section>${ui.table([{key:'id',render:value=>`<code>${value}</code>`},{key:'id',render:value=>`<button onclick="window.Pages['example'].save('${value}')">View</button>`}],rows)}</section>`;
}
function save(id){
  return readExistingObject(id);
}
window.Pages['example']={render,renderAudit,save};
})();
'''
class P4RawTableTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
    ev=BoundedSourceChainTests.ev
    make=BoundedSourceChainTests.make
    problems=BoundedSourceChainTests.problems
    def fixture(self,source=P4_RAW_PAGE,nav_source=P4_QUOTED_NAV,ui_source=P1_TABLE_UI):
        def line(text,needle):return next(i for i,l in enumerate(text.splitlines(),1)if needle in l)
        first=line(source,'function save(');p,s,c,path=self.make(source,line(source,'<button'),line(source,"window.Pages['example']="),first,first+2)
        nav=self.root/'inputs/p4-nav.js';ui=self.root/'inputs/p4-ui.js';nav.write_text(nav_source);ui.write_text(ui_source);refs=['inputs/source-index.json#SRC-001','inputs/source-index.json#SRC-003','inputs/source-index.json#SRC-004']
        for sid,fp in [('SRC-003',nav),('SRC-004',ui)]:
            self.facts['sources']['sources']=[x for x in self.facts['sources']['sources']if x['id']!=sid];self.facts['sources']['sources'].append({'id':sid,'status':'verified','snapshot_path':str(fp.relative_to(self.root)),'sha256':hashlib.sha256(fp.read_bytes()).hexdigest()})
        self.retained['source_refs']=refs[:];s['source_refs']=refs[:];p['source_refs']=refs[:]
        def ev(fp,a,z=None):
            x=self.ev(fp,a,z);x['source_refs']=refs[:];return x
        e=p['source_chain']['entry'];e.pop('opening_evidence',None);e['ui_alias_evidence']=ev(path,2);e['raw_table_control_provenance']={'version':'bounded-returned-raw-table-control@1','reachability':'conditional_only','route_options_policy':'default_route_view_no_override','route_key':'agents','route_page':'example','route_view':'audit','exported_render_symbol':'render','render_delegate_symbol':'renderAudit','view_parameter':'options','column_key_literal':'id','column_index':1,'event_object_argument_index':0,'evidence':{'route_registration':ev(nav,2),'route_dispatch':ev(nav,3,7),'route_sink':ev(nav,8),'render':ev(path,line(source,'function render('),line(source,'function renderAudit(')-1),'delegate':ev(path,line(source,'function renderAudit('),first-1),'table_implementation':ev(ui,2,line(ui_source,'window.PlatformUI=')-1),'table_export':ev(ui,line(ui_source,'window.PlatformUI='))}}
        return p,s,c,path
    def test_p4_raw_exact_one_parameter_duplicate_display_key_is_bounded(self):
        _,s,c,_=self.fixture();self.assertEqual([],self.problems(s,c))
    def test_p4_raw_parenthesized_one_and_two_param_forms(self):
        for src in [P4_RAW_PAGE.replace('render:value=>`<button','render:(value)=>`<button'),P4_RAW_PAGE.replace('render:value=>`<button','render:(value,row)=>`<button').replace("save('${value}')","save('${row.id}')")]:
            with self.subTest(src=src):
                _,s,c,_=self.fixture(src);self.assertEqual([],self.problems(s,c))
    def test_p4_raw_no_optin_keeps_original_rejection(self):
        p,s,c,_=self.fixture();p['source_chain']['entry'].pop('raw_table_control_provenance');self.assertTrue(self.problems(s,c))
    def test_p4_raw_wrong_profile_column_or_argument_claim(self):
        for k,v in [('version','unknown'),('column_index',0),('column_index',4),('column_index',True),('column_key_literal','wrong'),('event_object_argument_index',1),('reachability','unconditional')]:
            with self.subTest(k=k,v=v):
                p,s,c,_=self.fixture();p['source_chain']['entry']['raw_table_control_provenance'][k]=v;self.assertTrue(self.problems(s,c))
    def test_p4_raw_wrong_identity_callee_and_owner(self):
        for src in [P4_RAW_PAGE.replace("save('${value}')","save('wrong')"),P4_RAW_PAGE.replace(".save('${value}')",".other('${value}')"),P4_RAW_PAGE.replace("Pages['example'].save","Pages['another'].save"),P4_RAW_PAGE.replace('render:value=>`<button','render:(value,row)=>`<button').replace("save('${value}')","save('${row.otherId}')")]:
            with self.subTest(src=src):
                _,s,c,_=self.fixture(src);self.assertTrue(self.problems(s,c))
    def test_p4_raw_hidden_inert_and_raw_markup_rejected(self):
        for attrs in ['hidden','inert','style="display:none"']:
            with self.subTest(attrs=attrs):
                _,s,c,_=self.fixture(P4_RAW_PAGE.replace('<button onclick=',f'<div {attrs}><button onclick=').replace('View</button>','View</button></div>'));self.assertTrue(self.problems(s,c))
        _,s,c,_=self.fixture(P4_RAW_PAGE.replace('<button onclick=','<script><button onclick=').replace('View</button>','View</button></script>'));self.assertTrue(self.problems(s,c))
    def test_p4_raw_fake_UI_table_and_destructive_table_body_rejected(self):
        for ui in [P1_TABLE_UI.replace('${body}','No body'),P1_TABLE_UI.replace('col.render(row[col.key],row)','other(row[col.key],row)'),P1_TABLE_UI.replace('function table(columns,rows,options){','function table(columns,rows,options){\n  columns=[];')]:
            with self.subTest(ui=ui):
                _,s,c,_=self.fixture(ui_source=ui);self.assertTrue(self.problems(s,c))
    def test_p4_raw_columns_variable_function_body_or_empty_rows_rejected(self):
        for src in [P4_RAW_PAGE.replace('ui.table([{','ui.table(columns,[{'),P4_RAW_PAGE.replace('render:value=>`<button','render:function(value){return `<button').replace('View</button>`}', 'View</button>`;}'),P4_RAW_PAGE.replace("const rows=[{id:'one'}]",'const rows=[]')]:
            with self.subTest(src=src):
                _,s,c,_=self.fixture(src);self.assertTrue(self.problems(s,c))
    def test_p4_raw_route_or_SHA_proof_rewrite_rejected(self):
        for key,value in [('route_view','other'),('route_page','another')]:
            with self.subTest(key=key):
                p,s,c,_=self.fixture();p['source_chain']['entry']['raw_table_control_provenance'][key]=value;self.assertTrue(self.problems(s,c))
        p,s,c,_=self.fixture();p['source_chain']['entry']['raw_table_control_provenance']['evidence']['delegate']['source_sha256']='0'*64;self.assertTrue(self.problems(s,c))


    def test_p4_raw_renderer_cannot_rewrite_value_or_row_identity(self):
        variants=[P4_RAW_PAGE.replace('<button onclick=', '<div data-proof="${value=\'other\'}"><button onclick=').replace('View</button>', 'View</button></div>'),
                  P4_RAW_PAGE.replace('render:value=>`<button', 'render:(value,row)=>`<div data-proof="${row.id=\'other\'}"><button').replace('View</button>', 'View</button></div>')]
        for source in variants:
            with self.subTest(source=source):
                _,scope,candidate,_=self.fixture()
                self.assertEqual([],self.problems(scope,candidate))
                _,scope,candidate,_=self.fixture(source)
                self.assertTrue(self.problems(scope,candidate))

    def test_p4_raw_UI_receiver_copy_and_forward_are_rejected(self):
        for statement in ['const other=ui;other.table=unrelated;', 'consume(ui);']:
            with self.subTest(statement=statement):
                _,scope,candidate,_=self.fixture()
                self.assertEqual([],self.problems(scope,candidate))
                source=P4_RAW_PAGE.replace('function renderAudit(){', 'function renderAudit(){\n  '+statement)
                _,scope,candidate,_=self.fixture(source)
                self.assertTrue(self.problems(scope,candidate))



"""Portable readonly-result-role tests only. Not a full UI/source-chain pass."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import bounded_source_chain as P4_ROLE_BSC

P4_ROLE_PAGE = '''(function(){
const ui=PlatformUI,esc=ui.escape;
const command=(method,...args)=>esc(`RulePage.${method}(${args.map(v=>JSON.stringify(v)).join(',')})`);
const access=()=>Store;
let dialog=null;
function render(){
 const rows=originalRows;
 return `<section>${ui.table([{key:'name',render:(v,row)=>`<button onclick="${command('show',row.id,key,true)}">View</button>`}],rows)}</section>`;
}
function show(id='',key,readOnly=false){
 if(!access().allowed())return;
 const readonly=!!id&&(readOnly||!access().allowed(true));
 const current={id:String(id),key:access().context(),expectedRevision:1,readonly,busy:false};dialog=current;
 const title=readonly?'Detail':id?'Edit':'Create';
 ui.openLayer('rule',`<h2>${title}</h2>${readonly?'':`<button onclick="${command('save')}">Save</button>`}`,'wide');
}
async function save(){
 const current=dialog;if(!current||current.busy||current.readonly||current.key!==access().context()||!access().allowed(true))return;
 return Store.save(current.id);
}
window.RulePage={render,show,save};
})();
'''
P4_ROLE_UI = '''(function(){
function escape(value){
 return String(value == null ? '' : value)
 .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
 .replace(/"/g,'&quot;').replace(/'/g,'&#039;');
}
window.PlatformUI={escape};
})();
'''


class P4ReadonlyResultRoleTests(unittest.TestCase):
    def setUp(self):
        self.P4_temp = tempfile.TemporaryDirectory();self.addCleanup(self.P4_temp.cleanup)
        self.P4_page = Path(self.P4_temp.name).resolve()/'page.js'
        self.P4_ui = self.P4_page.parent/'ui.js'

    def P4_build(self, source=P4_ROLE_PAGE, ui=P4_ROLE_UI):
        self.P4_page.write_text(source);self.P4_ui.write_text(ui)
        def ev(path, needle):
            lines=path.read_text().splitlines();hits=[i+1 for i,line in enumerate(lines)if needle in line]
            self.assertEqual(1,len(hits),(needle,hits));line=hits[0]
            return {'file':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'line_start':line,'line_end':line,'source_quote':lines[line-1],'source_refs':['inputs/source-index.json#SRC-001']}
        def span(path, first, last):
            e=ev(path,first);e['line_end']=ev(path,last)['line_end'];e['source_quote']='\n'.join(path.read_text().splitlines()[e['line_start']-1:e['line_end']]);return e
        proof={'source_refs':['inputs/source-index.json#SRC-001'],'source_chain':{'entry':{'kind':'template_event','callee':'RulePage.show','event':'click','evidence':ev(self.P4_page,"command('show',row.id,key," )}}}
        op={'version':'bounded-readonly-result-expression@1','containing_symbol':'show','result_symbol':'title','readonly_symbol':'readonly','identity_parameter':'id','readonly_parameter':'readOnly','identity_argument_index':0,'readonly_argument_index':2,'ui_alias':'ui','access_factory_symbol':'access','access_method':'allowed','dialog_symbol':'dialog','dialog_capture_symbol':'current','save_symbol':'save','detail_literal':'Detail','layer_id_literal':'rule','result_expression_index':0,'no_save_expression_index':1,
            'expression_evidence':ev(self.P4_page,'const title='),'readonly_binding_evidence':ev(self.P4_page,'const readonly='),'layer_evidence':ev(self.P4_page,"ui.openLayer('rule'"),'ui_alias_evidence':ev(self.P4_page,'const ui='),'readonly_call_evidence':ev(self.P4_page,"command('show',row.id,key,"),'dialog_binding_evidence':ev(self.P4_page,'const current={'),'save_guard_evidence':ev(self.P4_page,'const current=dialog;'),
            'handler_helper_evidence':ev(self.P4_page,'const command='),'helper_call':{'name':'command','literal_method':'show'},'escape_alias_evidence':ev(self.P4_page,'const ui='),'escape_implementation_evidence':span(self.P4_ui,'function escape(value){',".replace(/\"/g"),'escape_export_evidence':ev(self.P4_ui,'window.PlatformUI=')}
        op['save_owner_binding']={'kind':'static_owner_export','callee':'RulePage.save','symbol':'save',
            'evidence':ev(self.P4_page,'window.RulePage='),
            'global_owner_binding':{'version':'explicit-window-owner@1','bare_owner':'RulePage',
                'window_owner':'window.RulePage','evidence':ev(self.P4_page,'window.RulePage=')}}
        op['escape_implementation_evidence']['line_end']+=1
        e=op['escape_implementation_evidence'];e['source_quote']='\n'.join(self.P4_ui.read_text().splitlines()[e['line_start']-1:e['line_end']])
        contract={'version':'bounded-original-anchor-role@1','role':'result_expression_noncallable','role_evidence':op};proof['original_anchor']=contract
        expression=op['expression_evidence'];context={'source_locator':"const title=readonly?'Detail'",'implementation_symbol':'show','source_file':str(self.P4_page),'source_sha256':expression['source_sha256'],'source_line_start':expression['line_start'],'source_line_end':expression['line_end'],'role':contract['role']}
        class Review:
            def references(self, record):return []
            def path(self,value,**kwargs):return Path(value).resolve()
            def evidence_source_chain_errors(self, observation, record):return []
        # This stub isolates lexical role checks; ChainReview still honestly
        # verifies every fixture file SHA/quote. Real registered-source checking
        # is exercised separately on the original read-only source example.
        return Review(),proof,{'source_refs':proof['source_refs']},{'source_refs':proof['source_refs']},self.P4_page,context

    def P4_errors(self, made):
        return P4_ROLE_BSC.original_anchor_role_errors(*made)

    def P4_base(self):
        made=self.P4_build();self.assertEqual([],self.P4_errors(made));return made

    def P4_negative(self,before,after):
        self.P4_base();self.assertEqual(1,P4_ROLE_PAGE.count(before));made=self.P4_build(P4_ROLE_PAGE.replace(before,after,1));self.assertTrue(self.P4_errors(made));return made

    def test_P4_readonly_original_role_positive_not_a_full_chain_pass(self):
        self.P4_base()

    def test_P4_other_anchor_roles_keep_pending(self):
        for role in ['complete_callable_handler','initiator_only_needs_save_result','data_helper_with_callable_alternative']:
            with self.subTest(role=role):
                made=self.P4_base();made[1]['original_anchor']['role']=role;made[-1]['role']=role;self.assertTrue(self.P4_errors(made))

    def test_P4_wrong_or_missing_role_version_rejected(self):
        for value in [None,'generic-js@1']:
            with self.subTest(value=value):
                made=self.P4_base();made[1]['original_anchor']['role_evidence']['version']=value;self.assertTrue(self.P4_errors(made))

    def test_P4_wrong_parent_or_result_slot_rejected(self):
        for field,value in [('containing_symbol','save'),('result_expression_index',1),('no_save_expression_index',0),('readonly_argument_index',1)]:
            with self.subTest(field=field):
                made=self.P4_base();made[1]['original_anchor']['role_evidence'][field]=value;self.assertTrue(self.P4_errors(made))

    def test_P4_duplicate_lexical_parent_rejected(self):
        self.P4_negative('window.RulePage={render,show,save};','function show(id,key,readOnly){return unrelated();}\nwindow.RulePage={render,show,save};')

    def test_P4_wrong_readonly_source_rejected(self):
        for replacement in ['!!id&&(false||!access().allowed(true))','!!id&&(readOnly&& !access().allowed(true))','readOnly||!access().allowed(true)']:
            with self.subTest(replacement=replacement):
                self.P4_negative('!!id&&(readOnly||!access().allowed(true))',replacement)

    def test_P4_editable_call_cannot_be_readonly_entry(self):
        self.P4_negative("command('show',row.id,key,true)","command('show',row.id,key,false)")

    def test_P4_readonly_identity_or_result_rewrite_rejected(self):
        for target in ['readOnly=false;','id=\'other\';','readonly=false;','title=\'Edit\';']:
            with self.subTest(target=target):
                self.P4_negative("ui.openLayer('rule',",target+"ui.openLayer('rule',")

    def test_P4_unconsumed_original_title_rejected(self):
        self.P4_negative('<h2>${title}</h2>','<h2>Other</h2>')

    def test_P4_raw_inert_or_attribute_title_slot_rejected(self):
        for replacement in ['<input title="${title}">','<!--${title}-->','<script>${title}</script>','<template>${title}</template>','<div hidden>${title}</div>','<textarea>${title}</textarea>']:
            with self.subTest(replacement=replacement):
                self.P4_negative('<h2>${title}</h2>',replacement)

    def test_P4_dead_or_unconditional_returned_layer_rejected(self):
        for prefix in ['if(false)','return;']:
            with self.subTest(prefix=prefix):
                self.P4_negative("ui.openLayer('rule',",prefix+"ui.openLayer('rule',")

    def test_P4_true_readonly_branch_must_remove_whole_save(self):
        self.P4_negative("${readonly?'':`<button", "${readonly?'<button>Bad</button>':`<button")
        self.P4_negative('<h2>${title}</h2>','<h2>${title}</h2><button onclick="RulePage.save()">Raw extra save</button>')

    def test_P4_dialog_capture_must_preserve_readonly(self):
        self.P4_negative('expectedRevision:1,readonly,busy:false','expectedRevision:1,readonly:false,busy:false')
        self.P4_negative('dialog=current;','dialog=current;const hidden=current;hidden.readonly=false;')

    def test_P4_save_guard_must_reject_readonly_before_commit(self):
        self.P4_negative('current.busy||current.readonly||','current.busy||false||')

    def test_P4_fake_real_UI_binding_rejected(self):
        self.P4_negative('const ui=PlatformUI,esc=ui.escape;','const ui=Diagnostics,esc=ui.escape;')

    def test_P4_source_SHA_and_quote_tamper_rejected(self):
        for field,value in [('source_sha256','0'*64),('source_quote','unrelated source')]:
            with self.subTest(field=field):
                made=self.P4_base();made[1]['original_anchor']['role_evidence']['expression_evidence'][field]=value;self.assertTrue(self.P4_errors(made))

    def test_P4_protected_parameter_compound_parenthesized_prefix_writes_rejected(self):
        for write in ['readOnly*=0;','(readOnly)=false;','--readOnly;','++readOnly;']:
            with self.subTest(write=write):
                self.P4_negative('const readonly=',''+write+'const readonly=')

    def test_P4_original_restore_hook_cannot_rewrite_readonly_capture(self):
        self.P4_base()
        safe=P4_ROLE_PAGE.replace("'wide');","'wide',{onRestore(){dialog=current;}});")
        self.assertEqual([],self.P4_errors(self.P4_build(safe)))
        for rewrite in ['current.readonly=false;','const hidden=current;hidden.readonly=false;']:
            with self.subTest(rewrite=rewrite):
                source=safe.replace('onRestore(){dialog=current;}','onRestore(){dialog=current;'+rewrite+'}')
                self.assertTrue(self.P4_errors(self.P4_build(source)))

    def test_P4_same_line_decoy_result_cannot_replace_original_anchor(self):
        self.P4_base()
        source=P4_ROLE_PAGE.replace("const title=readonly?'Detail':id?'Edit':'Create';", "const title=readonly?'Detail':id?'Edit':'Create';const decoy=readonly?'Detail':id?'Edit':'Create';").replace('<h2>${title}</h2>','<h2>${decoy}</h2>')
        made=self.P4_build(source);made[1]['original_anchor']['role_evidence']['result_symbol']='decoy'
        self.assertTrue(self.P4_errors(made))

    def test_P4_nonliteral_default_or_escaped_UI_alias_rejected(self):
        self.P4_negative("show(id='',key,readOnly=false)","show(id=unrelated,key,readOnly=false)")
        self.P4_negative('const ui=PlatformUI,esc=ui.escape;','const ui=PlatformUI,esc=ui.escape;const copied=ui;copied.openLayer=unrelated;')

    def test_P4_dialog_and_capture_are_the_same_protected_object(self):
        for rewrite in ['Object.assign((dialog),{readonly:false});','const copied=dialog;copied.readonly=false;']:
            with self.subTest(rewrite=rewrite):
                self.P4_negative("ui.openLayer('rule',",rewrite+"ui.openLayer('rule',")

    def test_P4_readonly_guard_must_be_the_actual_save_export(self):
        self.P4_negative('window.RulePage={render,show,save};','window.RulePage={render,show,save:otherWrite};')





import copy,hashlib,json,unittest
from operation_attribution import StrictReview
from bounded_source_chain import ChainReview,Unsupported

RF_PUBLIC='''(function(root){
function render(){return 'view';}
function save(id){return persist(id);}
root.Widget={render,save};
root.Pages=root.Pages||{};
root.Pages['widget']=root.Widget;
})(window);
'''
RF_INLINE=RF_PUBLIC.replace('root.Widget={render,save};',"root.Widget={render,save,query(){return navigateTo('widget');}};")
RF_NAV='''(function(){
const menu=[{key:'group',children:[{key:'known',page:'known'}]}];
const routes={};
menu.forEach(group=>group.children.forEach(item=>{routes[item.key]={...item};}));
routes['inside']={key:'inside',page:'widget',view:'list',internal:true};
function resolve(key){
  let route=routes[key];
  return {key,route};
}
window.navigateTo=function(key,options){
 const resolved=resolve(key);
 const route=resolved.route;
 const page=window.Pages[route.page];
 const opts={view:route.view,...(options||{})};
 const content=document.getElementById('page-content');
 content.innerHTML=page.render(opts);
};
})();
'''
class RemainingCommonFlowTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
    def make_engine(self,source):
        scope,candidate=self.canonical();p=self.root/'inputs/evidence.txt';p.write_text(source);h=hashlib.sha256(p.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=h;refs=['inputs/source-index.json#SRC-001'];self.retained['source_refs']=refs;scope['source_refs']=refs;review=StrictReview(self.root,self.facts);engine=ChainReview(review,{'source_refs':refs},self.retained,scope,p)
        def ev(a,z=None):
            z=a if z is None else z;return dict(file=str(p),source_sha256=h,line_start=a,line_end=z,source_quote='\n'.join(source.splitlines()[a-1:z]),source_refs=refs[:])
        return engine,ev
    def publication(self,source=RF_PUBLIC,inline=False):
        e,ev=self.make_engine(source);spec=dict(version='bounded-static-publication@1',callee='Widget.query'if inline else'Widget.save',bare_global_owner='Widget',symbol='query'if inline else'save',inline_method=inline,evidence=ev(4),publication_aliases=[dict(kind='iife_parameter',alias='root',target='window',evidence=ev(1),invocation_evidence=ev(len(source.splitlines())))],owner_forwardings=[dict(target="window.Pages['widget']",evidence=ev(6))]);return e,spec
    def test_flow_real_root_iife_and_explicit_forwarding(self):
        e,p=self.publication();x=e.p4_flow_export(p);self.assertEqual('save',x['function'].symbol);self.assertEqual(('window','Widget','save'),x['canonical_callee'])
    def test_flow_window_alias_wrong_actual_arg_never_normalized(self):
        e,p=self.publication(RF_PUBLIC.replace('})(window);','})(Diagnostics);'))
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_requires_exact_forwarding_evidence(self):
        for attack in ['missing','other_target','wrong_quote']:
            with self.subTest(attack=attack):
                e,p=self.publication();self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
                if attack=='missing':p.pop('owner_forwardings')
                if attack=='other_target':p['owner_forwardings'][0]['target']="window.Pages['other']"
                if attack=='wrong_quote':p['owner_forwardings'][0]['evidence']['source_quote']='fake'
                with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_target_and_forwarded_method_rewrite_or_copy_rejected(self):
        for mutation in ["root.Widget.save=other;","root.Pages['widget'].save=other;","const copied=root.Widget;","root.Pages['widget']=other;"]:
            with self.subTest(mutation=mutation):
                source=RF_PUBLIC.replace('})(window);',mutation+'\n})(window);');e,p=self.publication(source)
                with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_inline_method_is_explicit_exact_static_publication(self):
        e,p=self.publication(RF_INLINE,True);x=e.p4_flow_export(p);fn,params,body=e.p4_flow_body(x);self.assertIsNone(fn.symbol);self.assertEqual([],params);self.assertIn('navigateTo',[t.value for t in body])
        p.pop('inline_method')
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_wrong_inline_member_body_or_duplicate_publication_rejected(self):
        for source in [RF_INLINE.replace('query(){','other(){'),RF_PUBLIC.replace('root.Widget={render,save};','root.Widget={render,save};root.Widget={render,save};')]:
            with self.subTest(source=source):
                e,p=self.publication(source,source==RF_INLINE.replace('query(){','other(){'))
                with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_const_computed_key_is_bound_not_metadata(self):
        source="""(function(){
const PAGE='widget';
function render(){return 'view';}
function save(){return persist();}
window.Pages[PAGE]={render,save};
})();
""";e,ev=self.make_engine(source);p=dict(version='bounded-static-publication@1',callee="window.Pages['widget'].save",symbol='save',evidence=ev(5),constant_keys=[dict(symbol='PAGE',literal='widget',evidence=ev(2))]);self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        p['constant_keys'][0]['literal']='other'
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_const_key_rebinding_rejected_with_fresh_evidence(self):
        source="""(function(){
const PAGE='widget';
function save(){return persist();}
PAGE='other';
window.Pages[PAGE]={save};
})();
""";e,ev=self.make_engine(source);p=dict(version='bounded-static-publication@1',callee="window.Pages['widget'].save",symbol='save',evidence=ev(5),constant_keys=[dict(symbol='PAGE',literal='widget',evidence=ev(2))])
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def nav(self,source=RF_NAV):
        e,ev=self.make_engine(source);return e,dict(route_key='inside',route_page='widget',route_view='list',route_options_policy='default_route_view_no_override',registration_kind='literal_map_property',evidence=dict(route_registration=ev(2,5),route_resolution=ev(6,9),route_dispatch=ev(10,15),route_sink=ev(16)))
    def test_flow_internal_route_has_real_property_and_dom_dispatch(self):
        e,p=self.nav();self.assertEqual('list',e.p4_flow_route(p)['route_view'])
    def test_flow_internal_route_wrong_key_page_view_or_sink_rejected(self):
        for key,value in [('route_key','unknown'),('route_page','other'),('route_view','wrong')]:
            with self.subTest(key=key):
                e,p=self.nav();self.assertEqual('list',e.p4_flow_route(p)['route_view']);p[key]=value
                with self.assertRaises(Unsupported):e.p4_flow_route(p)
        e,p=self.nav(RF_NAV.replace('content.innerHTML=page.render(opts);','console.log(page.render(opts));'))
        with self.assertRaises(Unsupported):e.p4_flow_route(p)
    def test_flow_internal_menu_collision_and_unknown_dynamic_writer_rejected(self):
        for source in [RF_NAV.replace("key:'known'","key:'inside'"),RF_NAV.replace('routes[item.key]={...item};','routes[unknown]={...item};'),RF_NAV.replace("key:'known'","key:readKey()")]:
            with self.subTest(source=source):
                e,p=self.nav(source)
                with self.assertRaises(Unsupported):e.p4_flow_route(p)
    def test_flow_ui_var_alias_is_exact_immutable_receiver(self):
        source="""(function(){
var ui=PlatformUI;
function render(){return ui.toolbar('x','y');}
})();
""";e,ev=self.make_engine(source);fn=next(f for f in e.evidence(ev(3)).model.functions if f.symbol=='render');self.assertEqual('ui',e.p4_flow_ui_alias(ev(2),'ui',fn.parent,fn.body_start+1))
        for modified in [source.replace('ui=PlatformUI','ui=Diagnostics'),source.replace('function render(){','ui=Diagnostics;\nfunction render(){'),source.replace('function render(){','const other=ui;\nfunction render(){')]:
            with self.subTest(modified=modified):
                e,ev=self.make_engine(modified);model=e.evidence(ev(2)).model;fn=next(f for f in model.functions if f.symbol=='render')
                with self.assertRaises(Unsupported):e.p4_flow_ui_alias(ev(2),'ui',fn.parent,fn.body_start+1)

    def test_flow_Object_freeze_is_real_unshadowed_builtin(self):
        frozen=RF_PUBLIC.replace('root.Widget={render,save};','root.Widget=Object.freeze({render,save});')
        e,p=self.publication(frozen);self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        bad=frozen.replace('(function(root){','(function(root){const Object={freeze:x=>({save:other})};')
        e,p=self.publication(bad)
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_unknown_computed_or_compound_method_write_rejected(self):
        e,p=self.publication();self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        for mutation in ["root.Widget[method]=other;","root.Widget.save*=0;","(root.Widget.save)=other;","++root.Widget.save;"]:
            with self.subTest(mutation=mutation):
                source=RF_PUBLIC.replace('})(window);',mutation+'\n})(window);');e,p=self.publication(source)
                with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_explicit_literal_const_multi_publication(self):
        source=RF_PUBLIC.replace('root.Widget={render,save};','const exported={render,save};root.Widget=exported;').replace("root.Pages['widget']=root.Widget;","root.Pages['widget']=exported;")
        e,ev=self.make_engine(source);p=dict(version='bounded-static-publication@1',callee='Widget.save',bare_global_owner='Widget',symbol='save',evidence=ev(4),publication_aliases=[dict(kind='iife_parameter',alias='root',evidence=ev(1),invocation_evidence=ev(7))],publication_object_binding=dict(symbol='exported',evidence=ev(4),publications=[dict(target='window.Widget',evidence=ev(4)),dict(target="window.Pages['widget']",evidence=ev(6))]));self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        p['publication_object_binding']['publications'].pop()
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
        for mutation in ["exported.save=other;","const hidden=exported;"]:
            bad=source.replace('})(window);',mutation+'\n})(window);');e,ev=self.make_engine(bad);p['evidence']=ev(4);p['publication_aliases'][0]['evidence']=ev(1);p['publication_aliases'][0]['invocation_evidence']=ev(8);p['publication_object_binding']['evidence']=ev(4);p['publication_object_binding']['publications']=[dict(target='window.Widget',evidence=ev(4)),dict(target="window.Pages['widget']",evidence=ev(6))]
            with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_extensions_are_explicit_non_target_literal_members(self):
        source=RF_PUBLIC.replace('function save(id){return persist(id);}','function save(id){return persist(id);}function extra(){return 1;}').replace("root.Pages['widget']=root.Widget;","root.Pages['widget']=root.Widget;Object.assign(root.Widget,{extra});")
        e,p=self.publication(source);p['static_extensions']=[dict(evidence=e.evidence(p['owner_forwardings'][0]['evidence']).record)];self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        p.pop('static_extensions')
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
        for arg in ['{save:extra}','{...unknown}','extensions']:
            bad=source.replace('Object.assign(root.Widget,{extra})','Object.assign(root.Widget,'+arg+')');e,p=self.publication(bad);p['static_extensions']=[dict(evidence=p['owner_forwardings'][0]['evidence'])]
            with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_local_same_name_is_not_free_implementation_rebinding(self):
        source=RF_PUBLIC.replace('function save(id){return persist(id);}',"function save(id){const query={endpoint:'x'};return persist(id);}function query(){return read();}").replace('root.Widget={render,save};','root.Widget={render,save,query};')
        e,p=self.publication(source);p.update(callee='Widget.query',symbol='query');self.assertEqual('query',e.p4_flow_export(p)['function'].symbol)
        bad=source.replace("const query={endpoint:'x'};",'query=other;');e,p=self.publication(bad);p.update(callee='Widget.query',symbol='query')
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_parameter_initializer_is_local_binding_but_default_RHS_is_not(self):
        source=RF_PUBLIC.replace('function save(id){return persist(id);}',"function save(id){return persist(id);}function query(){return read();}function persist(query=data()){return query;}").replace('root.Widget={render,save};','root.Widget={render,save,query};');e,p=self.publication(source);p.update(callee='Widget.query',symbol='query');self.assertEqual('query',e.p4_flow_export(p)['function'].symbol)
        bad=source.replace('function persist(query=data())','function persist(arg=(query=other))');e,p=self.publication(bad);p.update(callee='Widget.query',symbol='query')
        with self.assertRaises(Unsupported):e.p4_flow_export(p)

    def inline_function_source(self,source=None):
        source=source or RF_PUBLIC.replace('root.Widget={render,save};',"root.Widget={render,save,open:function(id){return Store.open(id);}};")
        e,p=self.publication(source);p.update(callee='Widget.open',symbol='open',inline_function=True);return e,p
    def test_flow_explicit_anonymous_function_property_binds_actual_body(self):
        e,p=self.inline_function_source();binding=e.p4_flow_export(p);fn,names,body=e.p4_flow_body(binding);self.assertIsNone(fn.symbol);self.assertEqual(['id'],names);self.assertEqual(['return','Store','.','open','(','id',')',';'],[x.value for x in body])
    def test_flow_anonymous_function_requires_explicit_flag_and_exact_member(self):
        for attack in ['missing_flag','wrong_symbol','wrong_member','both_flags']:
            with self.subTest(attack=attack):
                e,p=self.inline_function_source();self.assertIsNotNone(e.p4_flow_export(p)['function'])
                if attack=='missing_flag':p.pop('inline_function')
                if attack=='wrong_symbol':p['symbol']='save'
                if attack=='wrong_member':p['callee']='Widget.other'
                if attack=='both_flags':p['inline_method']=True
                with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_anonymous_published_member_rewrites_getter_or_copy_rejected(self):
        original=RF_PUBLIC.replace('root.Widget={render,save};',"root.Widget={render,save,open:function(id){return Store.open(id);}};")
        variants=[original.replace('open:function(id)', 'get open()').replace('return Store.open(id);','return other;'),original.replace('})(window);',"root.Widget.open=other;\n})(window);"),original.replace('})(window);',"const other=root.Widget;\n})(window);")]
        for source in variants:
            with self.subTest(source=source):
                e,p=self.inline_function_source();self.assertIsNotNone(e.p4_flow_export(p)['function']);e,p=self.inline_function_source(source)
                with self.assertRaises(Unsupported):e.p4_flow_export(p)

    def direct_window_publication(self,source):
        e,ev=self.make_engine(source);line=next(i for i,l in enumerate(source.splitlines(),1)if 'window.Widget='in l);return e,dict(version='bounded-static-publication@1',callee='window.Widget.save',symbol='save',evidence=ev(line))
    def test_flow_direct_window_owner_requires_actual_unshadowed_global(self):
        good="(function(){\nfunction save(id){return persist(id); }\nwindow.Widget={save};\n})();\n"
        for prefix in ['const window={};','var window;','function window(){}','class window{}','const item=foo.of, window={};','const item=foo.in, window={};','window=Diagnostics;','(window)=Diagnostics;']:
            e,p=self.direct_window_publication(good);self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
            e,p=self.direct_window_publication(good.replace('function save',prefix+'\nfunction save'))
            with self.assertRaises(Unsupported):e.p4_flow_export(p)
        e,p=self.direct_window_publication(good.replace('(function(){','(function(window){'))
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def test_flow_for_of_declarations_stop_before_unrelated_window_use(self):
        good="(function(){\nfunction save(id){for(const [collection,rows] of data){other(rows);}for(const row of data){other(row);}return window.persist(id); }\nwindow.Widget={save};\n})();\n";e,p=self.direct_window_publication(good);self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        e,p=self.direct_window_publication(good.replace('window.Widget={save};',"for(const window of data){other(window); }\nwindow.Widget={save};"))
        with self.assertRaises(Unsupported):e.p4_flow_export(p)
    def field_endpoint(self,source=None):
        source=source or "(function(){\nlet activeKind='category';\nfunction selectKind(kind){activeKind=kind;}\nfunction render(kind=activeKind){return kind;}\n})();\n"
        e,ev=self.make_engine(source);model=e.evidence(ev(4)).model;fn=next(f for f in model.functions if f.symbol=='render');selector=next(f for f in model.functions if f.symbol=='selectKind');binding=next(t for ts in model.lists for t in ts if t.value=='activeKind'and t.start<fn.start and t.start<selector.start);bound=dict(function=fn,model=model,evidence=e.evidence(ev(4)),selector_function=selector,state_binding_token=binding);flow=dict(family='execution',entry_binding=dict(version='bounded-field-kind-flow@1',operation='query'));return e,ev,bound,flow
    def test_flow_actual_for_of_top_level_header_does_not_capture_following_global(self):
        source="(function(){\nfor(const [collection,rows] of data){other(rows);}\nfor(const row of data){other(row);}\nfunction save(id){return persist(id);}\nwindow.Widget={save};\n})();\n";e,p=self.direct_window_publication(source);self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)

    def test_flow_checked_dynamic_default_requires_trusted_real_field_endpoints(self):
        e,ev,b,flow=self.field_endpoint();fn,params,body=e.p4_flow_checked_endpoint(e.evidence(ev(4)),'render',dict(verified_default_endpoint=b),flow);self.assertIs(fn,b['function']);self.assertEqual(['kind'],params)
        with self.assertRaises(Unsupported):e.p4_flow_function(e.evidence(ev(4)),'render')
    def test_flow_checked_default_cannot_be_metadata_or_another_family(self):
        for attack in ['metadata_function','missing_selector','false_binding','wrong_family','wrong_operation','wrong_evidence']:
            with self.subTest(attack=attack):
                e,ev,b,flow=self.field_endpoint();self.assertIsNotNone(e.p4_flow_checked_endpoint(e.evidence(ev(4)),'render',dict(verified_default_endpoint=b),flow))
                if attack=='metadata_function':b['function']={'symbol':'render'}
                if attack=='missing_selector':b.pop('selector_function')
                if attack=='false_binding':b['state_binding_token']=b['function']
                if attack=='wrong_family':flow['family']='resource'
                if attack=='wrong_operation':flow['entry_binding']['operation']='edit'
                if attack=='wrong_evidence':b['evidence']=e.evidence(ev(3))
                with self.assertRaises(Unsupported):e.p4_flow_checked_endpoint(e.evidence(ev(4)),'render',dict(verified_default_endpoint=b),flow)
    def test_flow_checked_default_keeps_unknown_default_and_const_state_pending(self):
        original="(function(){\nlet activeKind='category';\nfunction selectKind(kind){activeKind=kind;}\nfunction render(kind=activeKind){return kind;}\n})();\n"
        for source in [original.replace('kind=activeKind','kind=unknown'),original.replace('let activeKind','const activeKind'),original.replace("activeKind='category'","activeKind='field'")]:
            with self.subTest(source=source):
                e,ev,b,flow=self.field_endpoint(source)
                with self.assertRaises(Unsupported):e.p4_flow_checked_endpoint(e.evidence(ev(4)),'render',dict(verified_default_endpoint=b),flow)

    def test_flow_bare_global_receiver_cannot_hide_a_shadowed_window_publication(self):
        good="(function(){\nfunction save(id){return persist(id);}\nwindow.Widget={save};\n})();\n";e,p=self.direct_window_publication(good);p.update(callee='Widget.save',bare_global_owner='Widget');self.assertEqual('save',e.p4_flow_export(p)['function'].symbol)
        bad=good.replace('function save','const window={};\nfunction save');e,p=self.direct_window_publication(bad);p.update(callee='Widget.save',bare_global_owner='Widget')
        with self.assertRaises(Unsupported):e.p4_flow_export(p)


"""Paired portable tests for the exact original top-level menu leaf builder."""
import hashlib
import unittest
from bounded_source_chain import ChainReview, Unsupported, matching, split_tokens
from operation_attribution import StrictReview

P4_LEAF_SOURCE='''(function(){
const menu=[{key:'seat',page:'workbench',label:'Seat'},{key:'group',label:'Group',children:[{key:'other',page:'other',label:'Other'}]}];
const routes={};
menu.forEach(group=>{
if(!group.children)routes[group.key]={...group,page:group.page||group.key,breadcrumb:[group.label]};
(group.children||[]).filter(item=>item.type!=='section').forEach(item=>{routes[item.key]={...item,domain:item.domain||group.domain,breadcrumb:[group.label,item.label],parent:group.key};});
});
routes['separate']={page:'elsewhere'};
})();
'''

class TopLevelMenuLeafP4Tests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    retained=BoundedSourceChainTests.retained
    canonical=BoundedSourceChainTests.canonical
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)

    def p4_leaf(self,source=P4_LEAF_SOURCE,corrupt=None,child=False):
        scope,candidate=self.canonical();p=self.root/'inputs/evidence.txt';p.write_text(source);digest=hashlib.sha256(p.read_bytes()).hexdigest();refs=['inputs/source-index.json#SRC-001'];self.facts['sources']['sources'][0]['sha256']=digest;self.retained['source_refs']=refs;scope['source_refs']=refs
        e=ChainReview(StrictReview(self.root,self.facts),dict(source_refs=refs),self.retained,scope,p.resolve());data=dict(file=str(p),source_sha256=digest,line_start=1,line_end=len(source.splitlines()),source_quote=source.rstrip('\n'),source_refs=refs)
        if corrupt=='sha':data['source_sha256']='0'*64
        if corrupt=='quote':data['source_quote']='fake'
        model=e.evidence(data).model;ts=model.tokens;ms=matching(ts);at=next(i for i,t in enumerate(ts)if t.value=='[');items=split_tokens(ts[at+1:ms[at]]);selected=items[0][0].start
        if child:
            props=e.p1_props(items[1]);selected=split_tokens(props['children'][1:-1])[0][0].start
        builder=next(c for c in model.calls if c.path[-1:] == ('forEach',)and len(c.path)==2)
        return e.p4_top_level_leaf_projection(model,builder,ts[at].start,selected,'routes')

    def test_top_leaf_original_whole_callback_and_page_fallback(self):
        self.assertTrue(self.p4_leaf());self.assertTrue(self.p4_leaf(P4_LEAF_SOURCE.replace("page:'workbench',",'')))

    def test_top_leaf_does_not_replace_original_child_path(self):
        self.assertFalse(self.p4_leaf(child=True))

    def test_top_leaf_group_with_children_even_empty_is_not_leaf(self):
        for children in ["children:[],","children:[{key:'nested',label:'Nested'}],"]:
            with self.subTest(children=children):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(P4_LEAF_SOURCE.replace("key:'seat',", "key:'seat',"+children))

    def test_top_leaf_duplicate_top_or_child_key_and_dynamic_key_rejected(self):
        for old,new in [("key:'group'","key:'seat'"),("key:'other'","key:'seat'"),("key:'other'","key:readKey()")]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(P4_LEAF_SOURCE.replace(old,new))

    def test_top_leaf_exact_guard_page_and_child_projection_required(self):
        for old,new in [('if(!group.children)','if(group.children)'),('group.page||group.key',"'other'"),('...group,page:','...group,key:"other",page:'),('parent:group.key','parent:item.key')]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(P4_LEAF_SOURCE.replace(old,new))

    def test_top_leaf_actual_invocation_and_direct_source_path_required(self):
        for source in [P4_LEAF_SOURCE.replace('menu.forEach(group=>','other.forEach(group=>'),P4_LEAF_SOURCE.replace('menu.forEach(group=>','if(false){menu.forEach(group=>').replace("});\nroutes['separate']","});};\nroutes['separate']")]:
            with self.subTest(source=source):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(source)

    def test_top_leaf_unknown_dynamic_or_selected_slot_overwrite_rejected(self):
        for write in ["routes[readKey()]={page:'other'};", "routes['seat']={page:'other'};", "routes['seat'].page='other';", "Reflect.set(routes,'seat',{});", "routes={};"]:
            with self.subTest(write=write):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(P4_LEAF_SOURCE.replace("routes['separate']={page:'elsewhere'};",write))

    def test_top_leaf_exact_registered_sha_and_quote_required(self):
        for corrupt in ['sha','quote']:
            with self.subTest(corrupt=corrupt):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(corrupt=corrupt)

    def test_top_leaf_unproved_map_copy_or_forward_and_published_alias_write_rejected(self):
        for write in ["const other=routes;other['seat']={page:'other'};", "const other=(routes);other['seat']={page:'other'};", "unknown(routes);", "window.ExposedRoutes=routes;window.ExposedRoutes[readKey()]={};"]:
            with self.subTest(write=write):
                self.assertTrue(self.p4_leaf())
                with self.assertRaises(Unsupported):self.p4_leaf(P4_LEAF_SOURCE.replace("routes['separate']={page:'elsewhere'};",write))
        # Existing explicit static publications are allowed only as read-only
        # aliases; this is not an allowlist for a candidate key or file SHA.
        published=P4_LEAF_SOURCE.replace("routes['separate']={page:'elsewhere'};","window.ExposedRoutes=routes;window.Runtime={routes};window.Menu=menu;Object.keys(routes);")
        self.assertTrue(self.p4_leaf(published))


"""Finite resource returned-table and row HTML edges; source JavaScript is never executed."""
import unittest
from bounded_source_chain import Unsupported

P4_RESOURCE_TABLE='''(function(){
const ui=PlatformUI,pageSize=8;
function numberActions(row,key){
 const settings=`<button onclick="openNumber('${row.numberId}')">View</button>`;
 return settings+`<button onclick="setLocal('${row.numberId}')">Use</button>`;
}
function renderNumbers(){
 const rows=Data.numbers;
 const start=(page-1)*pageSize;
 const columns=[{key:'numberId',render:(v,r)=>`<div>${numberActions(r,key)}</div>`}];
 return `<main>${ui.table(columns,rows.slice(start,start+pageSize))}</main>`;
}
function table(columns,rows,options){
 const body=rows.length?rows.map(row=>`<tr>${columns.map(col=>`<td>${col.render?col.render(row[col.key],row):row[col.key]}</td>`).join('')}</tr>`).join(''):'<tr><td>Empty</td></tr>';
 return `<table><tbody>${body}</tbody></table>`;
}
window.PlatformUI={table};
})();
'''
class RemainingResourceFlowTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 canonical=BoundedSourceChainTests.canonical
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 def fixture(self,source=P4_RESOURCE_TABLE):
  e,ev=self.make_engine(source)
  def f(name):
   m=e.evidence(ev(1,len(source.splitlines()))).model;fn=next(x for x in m.functions if x.symbol==name and x.is_declaration)
   return dict(symbol=name,evidence=ev(m.source.count('\n',0,fn.start)+1,m.source.count('\n',0,fn.end)+1))
  content=dict(version='bounded-resource-table@1',columns_symbol='columns',column_source_index=0,column_key_literal='numberId',table_implementation=f('table')['evidence'],table_export=ev(next(i for i,s in enumerate(source.splitlines(),1)if'window.PlatformUI='in s)),pagination=dict(page_size_symbol='pageSize',page_size_literal=8,evidence=ev(2)))
  info=e.p4r_function(f('renderNumbers'));step=dict(kind='returned_row_html_helper',expression_index=0,row_argument_index=0,target=f('numberActions'))
  return e,info,content,step
 def check(self,source=P4_RESOURCE_TABLE,content_mutator=None,step_mutator=None):
  e,info,c,s=self.fixture(source)
  if content_mutator:content_mutator(c)
  if step_mutator:step_mutator(s)
  renderer=e.p4r_table(info,c,'ui');return e.p4r_row_html_helper(info,renderer,s)
 def test_resource_active_const_columns_and_returned_html_helper(self):
  _,row,ts=self.check();self.assertEqual('row',row);self.assertEqual(2,len(ts))
 def test_resource_wrong_source_column_slot_or_key(self):
  self.check()
  for field,value in [('column_source_index',1),('column_key_literal','otherId')]:
   with self.subTest(field=field):
    with self.assertRaises(Unsupported):self.check(content_mutator=lambda c:c.update({field:value}))
 def test_resource_unused_helper_not_inserted_into_actual_table(self):
  self.check()
  with self.assertRaises(Unsupported):self.check(P4_RESOURCE_TABLE.replace('${numberActions(r,key)}','No row control'))
 def test_resource_helper_receives_other_or_copied_row(self):
  self.check()
  for expr in ['other','{...r}','getRow(r)']:
   with self.subTest(expr=expr):
    with self.assertRaises(Unsupported):self.check(P4_RESOURCE_TABLE.replace('numberActions(r,key)','numberActions('+expr+',key)'))
 def test_resource_helper_result_not_returned_or_inert(self):
  self.check()
  for source in [P4_RESOURCE_TABLE.replace('return settings+','return ignored+'),P4_RESOURCE_TABLE.replace('<div>${numberActions(r,key)}</div>','<template>${numberActions(r,key)}</template>'),P4_RESOURCE_TABLE.replace('<div>${numberActions(r,key)}</div>','<div hidden>${numberActions(r,key)}</div>')]:
   with self.subTest(source=source):
    with self.assertRaises(Unsupported):self.check(source)
 def test_resource_identity_write_or_helper_forwarding_rejected(self):
  self.check()
  for write in ['row.numberId="other";','(row).numberId="other";','mutate(row);','const x=row;']:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.check(P4_RESOURCE_TABLE.replace(' const settings=',write+'\n const settings='))
 def test_resource_columns_overwrite_copy_or_filter_rejected(self):
  self.check()
  for write in ['columns=[];','const other=columns;','columns.filter(Boolean);']:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.check(P4_RESOURCE_TABLE.replace(' return `<main>',write+'\n return `<main>'))
 def test_resource_table_drops_renderer_result_rejected(self):
  self.check()
  with self.assertRaises(Unsupported):self.check(P4_RESOURCE_TABLE.replace('${col.render?col.render(row[col.key],row):row[col.key]}','Ignored'))
 def test_resource_rows_cleared_or_empty_slice_rejected(self):
  self.check()
  for source in [P4_RESOURCE_TABLE.replace(' return `<main>','rows=[];\n return `<main>'),P4_RESOURCE_TABLE.replace('rows.slice(start,start+pageSize)','rows.slice(0,0)')]:
   with self.subTest(source=source):
    with self.assertRaises(Unsupported):self.check(source)
 def test_resource_wrong_helper_arity_or_interpolation_slot_rejected(self):
  self.check()
  with self.assertRaises(Unsupported):self.check(P4_RESOURCE_TABLE.replace('numberActions(r,key)','numberActions(r)'))
  with self.assertRaises(Unsupported):self.check(step_mutator=lambda s:s.update(expression_index=1))
 def test_resource_source_SHA_and_quote_are_not_metadata_authority(self):
  self.check()
  for field,value in [('source_sha256','0'*64),('source_quote','invented table')]:
   with self.subTest(field=field):
    with self.assertRaises(Unsupported):self.check(content_mutator=lambda c:c['table_implementation'].update({field:value}))

P4_RESOURCE_QUERY='''(function(){
function query(){
 if(!allowed())return;
 filter=document.getElementById('search')?.value||'';
 RouteRuntime.refreshCurrent();
}
function renderCurrent(options){
 if(!lastNavigation)return false;
 const {key}=lastNavigation,route=routes[key],page=window.Pages[route?.page],content=document.getElementById('page-content');
 if(!content||!page)return false;
 const opts={...lastNavigation.options,...(options||{})};
 lastNavigation={key,options:opts};
 content.innerHTML=page.render(opts);
 return true;
}
window.RouteRuntime={refreshCurrent:()=>renderCurrent({preserveFilters:true})};
})();
'''
P4_RESOURCE_VIEW='''(function(){
let view='agents';
function renderSkills(){return `<main>Skills</main>`;}
function renderAgents(){return `<main>Agents</main>`;}
function render(options){
 view=options?.view||view;
 if(view==='skills'){const context=1;return renderSkills();}
 return renderAgents();
}
})();
'''
class RemainingResourceQueryTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 canonical=BoundedSourceChainTests.canonical
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 def engine(self,source):
  e,ev=self.make_engine(source)
  def f(name):
   m=e.evidence(ev(1,len(source.splitlines()))).model;fn=next(x for x in m.functions if x.symbol==name and x.is_declaration)
   return dict(symbol=name,evidence=ev(m.source.count('\n',0,fn.start)+1,m.source.count('\n',0,fn.end)+1))
  return e,ev,f
 def query(self,source=P4_RESOURCE_QUERY):
  e,ev,f=self.engine(source);spec=dict(version='bounded-current-query-return@1',kind='refresh_current',publication_evidence=ev(next(i for i,l in enumerate(source.splitlines(),1)if'window.RouteRuntime='in l)),render_current=f('renderCurrent'))
  return e.p4r_refresh(e.p4r_function(f('query')),spec,'resource')
 def test_resource_query_consumes_original_inputs_and_current_dom_result(self):self.query()
 def test_resource_query_missing_input_or_unused_return_rejected(self):
  self.query()
  for source in [P4_RESOURCE_QUERY.replace("filter=document.getElementById('search')?.value||'';",'filter=other;'),P4_RESOURCE_QUERY.replace(' RouteRuntime.refreshCurrent();',' if(false)RouteRuntime.refreshCurrent();')]:
   with self.subTest(source=source):
    with self.assertRaises(Unsupported):self.query(source)
 def test_resource_query_fake_or_wrong_current_render_rejected(self):
  self.query()
  for source in [P4_RESOURCE_QUERY.replace('page.render(opts)','fake.render(opts)'),P4_RESOURCE_QUERY.replace('content.innerHTML=page.render(opts);','console.log(page.render(opts));'),P4_RESOURCE_QUERY.replace('refreshCurrent:()=>renderCurrent','refreshCurrent:()=>unrelated')]:
   with self.subTest(source=source):
    with self.assertRaises(Unsupported):self.query(source)
 def test_resource_query_cannot_rewrite_current_page_or_options(self):
  self.query()
  for write in ["opts.view='other';","page.render=()=>'';",'page={render:fake};','(opts).view="other";']:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.query(P4_RESOURCE_QUERY.replace(' content.innerHTML=',write+'\n content.innerHTML='))
 def view(self,source=P4_RESOURCE_VIEW,selected='skills',symbol='renderSkills'):
  e,_,f=self.engine(source);r=e.p4r_function(f('render'));r['route_view']=selected
  return e.p4r_view_delegate(r,dict(kind='route_view_delegate',view_variable='view',view_parameter='options',target=f(symbol)))
 def test_resource_view_preserves_actual_selected_literal_branch(self):
  self.assertEqual('renderSkills',self.view()['symbol']);self.assertEqual('renderAgents',self.view(selected='agents',symbol='renderAgents')['symbol'])
 def test_resource_view_wrong_default_branch_or_rewrite_rejected(self):
  self.view()
  with self.assertRaises(Unsupported):self.view(selected='agents')
  for write in ["view='agents';",'view=other;','(view)="other";']:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.view(P4_RESOURCE_VIEW.replace(" if(view==='skills')",write+"\n if(view==='skills')"))
 def test_resource_view_dead_or_unreturned_delegate_rejected(self):
  self.view()
  for source in [P4_RESOURCE_VIEW.replace("if(view==='skills')","if(false)"),P4_RESOURCE_VIEW.replace('return renderSkills();','renderSkills();return unrelated();')]:
   with self.subTest(source=source):
    with self.assertRaises(Unsupported):self.view(source)
 def test_resource_literal_selector_follows_actual_source_argument(self):
  e,_,_=self.engine(P4_RESOURCE_QUERY)
  import bounded_source_chain as b
  expr=b.lex("key==='skills'?'agent-skills':key==='sync'?'sync-records':'agent-maintenance'")
  self.assertEqual('agent-skills',e.p4r_scalar(expr,{'key':'skills'}))
  self.assertEqual('agent-maintenance',e.p4r_scalar(expr,{'key':'agents'}))
  with self.assertRaises(Unsupported):e.p4r_scalar(expr,{})

P4_RESOURCE_CAPTURE='''(function(){
function save(){
 const active=editor;
 if(!active||active.pending||active.mode==='view')return false;
 if(!allowed(active.key))return error('context');
 if(current.revision!==active.revision)return error('revision');
 const input=text(active.values.name).trim();
 const result=service().update(active.id,input,{expectedContext:active.context,expectedRevision:active.revision});
 if(editor!==active)return false;
 if(!result?.ok)return error('failed');
 showToast('saved');return true;
}
})();
'''
P4_RESOURCE_BUTTON='''(function(){
function draw(){
 let body='',button='';
 if(['create','edit'].includes(editor.mode)){body=form();button=editor.mode==='create'?'Create':'Save';}
 else if(editor.mode==='view')body=details();
 else if(editor.mode==='remove'){body=`<p>Delete?</p>`;button='Delete';}
 ui.openLayer('test',`<main>${body}${button?`<button onclick="save()">${button}</button>`:''}</main>`);
}
})();
'''
class RemainingResourceCapturedModeTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 canonical=BoundedSourceChainTests.canonical
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 engine=RemainingResourceQueryTests.engine
 def capture(self,source=P4_RESOURCE_CAPTURE):
  e,_,f=self.engine(source);info=e.p4r_function(f('save'));e.p4r_save_capture_guard(info,'editor');return e,info
 def test_resource_captured_save_identity_and_form_values_have_distinct_roles(self):self.capture()
 def test_resource_captured_id_context_mode_revision_cannot_change(self):
  self.capture()
  for write in ['active.id="other";','(active).context="other";','active.mode="create";','++active.revision;','active={id:"other"};','Object.assign(active,{id:"other"});']:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.capture(P4_RESOURCE_CAPTURE.replace(' const input=',write+'\n const input='))
 def test_resource_captured_state_cannot_be_copied_forwarded_or_call_unknown_method(self):
  self.capture()
  for write in ['const copy=active;','const copy=(active);','unknown(active);','active.update();','Reflect.set(active,"id","other");']:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.capture(P4_RESOURCE_CAPTURE.replace(' const input=',write+'\n const input='))
 def test_resource_context_and_revision_guards_are_actual_return_paths(self):
  e,info=self.capture();e.p4r_guard(info,'!allowed(active.key)',info['function'].body_end);e.p4r_guard(info,'current.revision!==active.revision',info['function'].body_end)
  for source in [P4_RESOURCE_CAPTURE.replace("return error('context')","error('context')"),P4_RESOURCE_CAPTURE.replace('current.revision!==active.revision','current.revision===active.revision')]:
   with self.subTest(source=source):
    e,info=self.capture(source)
    with self.assertRaises(Unsupported):e.p4r_guard(info,'!allowed(active.key)'if"error('context');"in source and"return error('context')"not in source else'current.revision!==active.revision',info['function'].body_end)
 def button(self,source=P4_RESOURCE_BUTTON,mode='edit'):
  e,_,f=self.engine(source);info=e.p4r_function(f('draw'));call=next(c for c in info['model'].calls if c.path==('ui','openLayer'));return e.p4r_mode_save_template(call.args[1][0],info,'editor',mode)
 def test_resource_only_actual_nonempty_selected_write_mode_produces_save(self):
  for mode in ['create','edit','remove']:self.button(mode=mode)
  with self.assertRaises(Unsupported):self.button(mode='view')
 def test_resource_button_wrong_mode_unused_or_inert_is_not_save_entry(self):
  self.button()
  for source in [P4_RESOURCE_BUTTON.replace("['create','edit']","['create']"),P4_RESOURCE_BUTTON.replace('${button?','<template>${button?').replace('</main>','</template></main>'),P4_RESOURCE_BUTTON.replace('button=editor.mode','unrelated=editor.mode')]:
   with self.subTest(source=source):
    with self.assertRaises(Unsupported):self.button(source)

P4_RESOURCE_STAGE='''(function(){
function draw(){
 let footer='';
 if(s.step===1){footer='<button onclick="Widget.next()">Next</button>';}
 else if(s.step===2){footer='<button onclick="Widget.submit()">Submit</button>';}
 else{footer='<button onclick="Widget.close()">Close</button>';}
 ui.openLayer('owned',`<main>${footer}</main>`);
}
})();
'''
class RemainingResourceStageTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 canonical=BoundedSourceChainTests.canonical
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 engine=RemainingResourceQueryTests.engine
 def stage(self,source=P4_RESOURCE_STAGE,selected=2):
  e,ev,f=self.engine(source);info=e.p4r_function(f('draw'));event=dict(event='click',callee='Widget.submit',evidence=f('draw')['evidence'])
  return e.p4r_state_control(info,event,('s','.','step'),selected,'footer')
 def test_resource_actual_stage_control_exists_only_in_original_step(self):
  self.stage()
  with self.assertRaises(Unsupported):self.stage(selected=1)
 def test_resource_selected_stage_footer_cannot_be_overwritten_before_layer(self):
  self.stage()
  for write in ["footer='<p>gone</p>';","if(unknown())footer='<p>gone</p>';","if(s.step===2)footer='<p>gone</p>';"]:
   with self.subTest(write=write):
    with self.assertRaises(Unsupported):self.stage(P4_RESOURCE_STAGE.replace(" ui.openLayer",write+'\n ui.openLayer'))
  self.stage(P4_RESOURCE_STAGE.replace(" ui.openLayer", "if(s.step===1)footer='Other stage';\n ui.openLayer"))

P4_IMPORT_SELECTION_PAGE='''(function(){
const ui=PlatformUI,esc=ui.escape;
const command=(method,...args)=>esc(`AliCtiNumberImport.${method}(${args.map(v=>JSON.stringify(v)).join(',')})`);
function draw(){
 const s=state;let body='';
 body+=ui.table([{key:'id',render:(id,r)=>`<input type="checkbox" name="aci-row" onchange="${command('toggle',String(id))}">`}],s.rows);
 ui.openLayer('owned',`<main>${body}</main>`);
}
})();
'''
P4_PENDING_DOM='''(function(){
function renderPending(){
 const pending=pendingRecords(),records=ui.sortByUpdated?.(pending,['at'])||pending,el=document.getElementById('seat-import-result');
 if(el)el.innerHTML=records.length?`<div>${records.map(record=>`<button onclick="verify('${esc(record.id)}')">Verify</button>`).join('')}</div>`:'';
}
})();
'''
class RemainingResourceImportGuardTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 canonical=BoundedSourceChainTests.canonical
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 engine=RemainingResourceQueryTests.engine
 def selection(self,page=P4_IMPORT_SELECTION_PAGE):
  source=UI_SUPPORT_SOURCE+page;e,ev,f=self.engine(source);info=e.p4r_function(f('draw'));const=next(i for i,l in enumerate(source.splitlines(),1)if'const ui='in l);helper=next(i for i,l in enumerate(source.splitlines(),1)if'const command='in l);export=next(i for i,l in enumerate(source.splitlines(),1)if'window.PlatformUI ='in l)
  hp=dict(handler_helper_evidence=ev(helper),helper_call={'name':'command','literal_method':'toggle'},escape_alias_evidence=ev(const),escape_implementation_evidence=f('escape')['evidence'],escape_export_evidence=ev(export))
  spec=dict(evidence=f('draw')['evidence'],event={'handler_helper':hp},table_implementation_evidence=f('table')['evidence'],table_export_evidence=ev(export));return e.p4r_import_checkbox(info,spec,'number','AliCtiNumberImport',info['parent'])
 def test_resource_import_selection_preserves_actual_original_row_ID(self):self.selection()
 def test_resource_import_selection_cannot_substitute_other_or_fixed_ID(self):
  self.selection()
  for expression in ['String(r.otherId)','String("other")','String(id+"x")']:
   with self.subTest(expression=expression):
    with self.assertRaises(Unsupported):self.selection(P4_IMPORT_SELECTION_PAGE.replace('String(id)',expression))
 def test_resource_import_native_checkbox_cannot_be_hidden_or_inert(self):
  self.selection()
  for change in ['hidden','inert','style="display:none"']:
   with self.subTest(change=change):
    with self.assertRaises(Unsupported):self.selection(P4_IMPORT_SELECTION_PAGE.replace('<input type="checkbox"','<input '+change+' type="checkbox"'))
 def test_resource_import_checkbox_must_be_appended_to_owned_body(self):
  self.selection()
  with self.assertRaises(Unsupported):self.selection(P4_IMPORT_SELECTION_PAGE.replace('body+=ui.table','unused=ui.table'))
 def pending(self,source=P4_PENDING_DOM):
  e,ev,f=self.engine(source);info=e.p4r_function(f('renderPending'));call=next(c for c in info['model'].calls if c.path==('records','map'));return e.p4r_pending_dom(info,call)
 def test_resource_pending_results_use_the_actual_returned_record_map(self):self.pending()
 def test_resource_pending_result_cannot_drop_output_or_rebind_DOM(self):
  self.pending()
  for source in [P4_PENDING_DOM.replace("${records.map(record=>`<button onclick=\"verify('${esc(record.id)}')\">Verify</button>`).join('')}",'No returned record'),P4_PENDING_DOM.replace(" if(el)el.innerHTML",' el={};\n if(el)el.innerHTML'),P4_PENDING_DOM.replace(" if(el)el.innerHTML",' unknown(el);\n if(el)el.innerHTML')]:
   with self.subTest(source=source):
    with self.assertRaises((Unsupported,StopIteration)):self.pending(source)
 def test_resource_original_owned_DOM_target_cannot_be_under_hidden_ancestor(self):
  source='''(function(){function render(){return `<main><div id="result"></div></main>`;}})();''';e,_,f=self.engine(source);info=e.p4r_function(f('render'));e.p4r_owned_dom(e.p4r_return(info)[0],'result')
  for replacement in ['<main hidden>','<main inert>','<main style="visibility:hidden">']:
   with self.subTest(replacement=replacement):
    ee,_,ff=self.engine(source.replace('<main>',replacement));ii=ee.p4r_function(ff('render'))
    with self.assertRaises(Unsupported):ee.p4r_owned_dom(ee.p4r_return(ii)[0],'result')
 def test_resource_binding_existing_branch_cannot_be_relabelled_new(self):
  source='''(function(){function draw(){const form=current?'Associated':(dialog.mode==='existing'?'<select onchange="AccountSeat.setTarget(this.value)"></select>':'New form');return form;}})();'''
  e,_,f=self.engine(source);info=e.p4r_function(f('draw'));_,value=e.p4r_const(info,'form',info['function'].body_end);_,_,no=e.p4_execution_ternary(value);e.p4r_boolean_branch(no,"dialog.mode==='existing'",'AccountSeat.setTarget')
  bad=source.replace("mode==='existing'","mode==='new'");e,_,f=self.engine(bad);info=e.p4r_function(f('draw'));_,value=e.p4r_const(info,'form',info['function'].body_end);_,_,no=e.p4_execution_ternary(value)
  with self.assertRaises(Unsupported):e.p4r_boolean_branch(no,"dialog.mode==='existing'",'AccountSeat.setTarget')
 def test_resource_binding_captured_context_ID_and_unknown_forward_remain_guarded(self):
  source='''(function(){function save(){const current=editor;current.busy=true;if(editor!==current)return false;submit(current.configId,current.context,current.expectedVersion);}})();'''
  def check(text):
   e,_,f=self.engine(text);info=e.p4r_function(f('save'));token=e.p4r_const(info,'current',info['function'].body_end)[0];e.p4r_critical_current(info,'current','editor',{'busy'},[token])
  check(source)
  for change in ['current.configId="other";','current.context="other";','current.expectedVersion*=2;','unknown(current);','const copy=(current);']:
   with self.subTest(change=change):
    with self.assertRaises(Unsupported):check(source.replace('current.busy=true;',change+'current.busy=true;'))

P4_NATIVE_SEAT_SELECTION='''(function(){
const ui=PlatformUI,esc=ui.escape;
function query(){
 document.getElementById('seat-import-result').innerHTML=ui.table([{key:'cno',render:(cno,r)=>{const issue=rowIssue(r);if(issue)return '<span>Invalid</span>';const exists=local(r);return exists?'<span>Present</span>':`<input type="checkbox" name="seat-import-selection" value="${esc(cno)}">`;}}],dialog.rows);
}
})();
'''
def _p4_native_selection(test,page=P4_NATIVE_SEAT_SELECTION):
 source=UI_SUPPORT_SOURCE+page;e,ev,f=test.engine(source);info=e.p4r_function(f('query'));export=next(i for i,l in enumerate(source.splitlines(),1)if'window.PlatformUI ='in l);spec=dict(table_implementation_evidence=f('table')['evidence'],table_export_evidence=ev(export));return e.p4r_import_checkbox(info,spec,'seat','AliCtiSeatImport',info['parent'])
class RemainingResourceNativeSeatTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 engine=RemainingResourceQueryTests.engine
 canonical=BoundedSourceChainTests.canonical
 def test_resource_original_native_seat_CNO_checkbox_is_the_actual_value(self):_p4_native_selection(self)
 def test_resource_native_seat_checkbox_itself_cannot_be_hidden(self):
  _p4_native_selection(self)
  for attr in ['hidden','inert','style="display:none"']:
   with self.subTest(attr=attr):
    with self.assertRaises(Unsupported):_p4_native_selection(self,P4_NATIVE_SEAT_SELECTION.replace('<input type="checkbox"','<input '+attr+' type="checkbox"'))

 def test_resource_native_seat_checkbox_cannot_use_another_CNO_or_fixed_identity(self):
  _p4_native_selection(self)
  for expr in ['esc(r.otherCno)','esc("fixed")']:
   with self.subTest(expr=expr):
    with self.assertRaises(Unsupported):_p4_native_selection(self,P4_NATIVE_SEAT_SELECTION.replace('esc(cno)',expr))


# Portable reporting source-flow tests; no original project path or JS execution.
import copy, hashlib, json
from pathlib import Path
import unittest
from bounded_source_chain import ChainReview, Unsupported, Model
from operation_attribution import StrictReview

P4_REPORT_PORTABLE_SOURCE = r'''(function(){
function toolbar(primary,secondary){return `<div>${primary || ''}<aside>${secondary || ''}</aside></div>`;}
window.PlatformUI={toolbar};
})();
(function(){
const menu=[{key:'group',children:[{key:'known',page:'known'}]}];
const routes={};
menu.forEach(group=>group.children.forEach(item=>{routes[item.key]={...item};}));
routes['task-route']={key:'task-route',page:'demo',view:'ivr',internal:true};
function resolve(key){let route=routes[key];return {key,route};}
window.navigateTo=function(key,options){
const resolved=resolve(key);
const route=resolved.route;
const page=window.Pages[route.page];
const opts={view:route.view,...(options||{})};
const content=document.getElementById('page-content');
content.innerHTML=page.render(opts);
};
})();
(function(){
var ui=PlatformUI;
var view='manual';
function taskPage(type){
return '<section>'+ui.toolbar('<button onclick="window.Pages[\'demo\'].exportTasks(\''+type+'\')">Export</button>','')+'</section>';
}
function render(options){
view=options && options.view || view;
if(view==='ivr') return taskPage('ivr-type');
return '';
}
function exportTasks(type){return commitOriginal(type);}
window.Pages['demo']={render,exportTasks};
})();
'''

P4_REPORT_ARRAY_SOURCE = r'''(function(){
function cards(m){
let items;
if(view==='leads') items=[card('Count',m.count,'leadCount','Original metric')];
else items=[card('Other',m.total,'total','Other metric')];
return `<section>${items.join('')}</section>`;
}
})();
'''

P4_REPORT_PUSH_SOURCE = r'''(function(){
function actions(row){
const buttons=[];
if(row.status==='running') buttons.push(`<button onclick="Widget.control('${row.id}','pause')">Pause</button>`);
if(row.status==='paused') buttons.push(`${row.ready?`<button onclick="Widget.control('${row.id}','resume')">Resume</button>`:''}`);
return buttons.join('');
}
})();
'''
P4_REPORT_TAB_SOURCE = r'''(function(){
function body(){
const tabs='<div role="tablist">'+[['overview','Overview'],['events','Events']].map(([key,label])=>'<button onclick="Widget.setTab(\''+key+'\')">'+label+'</button>').join('')+'</div>';
return '<section>'+tabs+'</section>';
}
})();
'''



P4_REPORT_TASK_ROWS_SOURCE = r'''(function(){
var pageSize=10;
function taskPage(type){
var rows=taskRows(type);
var pageRows=rows.slice((state.page-1)*pageSize,state.page*pageSize).map(function(row,index){var displayRow=Object.assign({orderNo:(state.page-1)*pageSize+index+1},row);return displayRow;});
return ui.table([],pageRows);
}
})();
'''

P4_REPORT_IMPORT_TABLE_SOURCE = UI_SUPPORT_SOURCE + r'''(function(){
const ui=PlatformUI,esc=ui.escape;
const arg=value=>esc(JSON.stringify(String(value||'')));
function list(){
const bs=readExistingBatches();
return '<section>'+ui.table([{key:'id',render:(_,b)=>'<button onclick="Widget.open('+arg(b.id)+')">View</button>'}],bs)+'</section>';
}
})();
'''

class RemainingReportingFlowTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)

    def p4_reporting_make(self,source=P4_REPORT_PORTABLE_SOURCE):
        scope,candidate=self.canonical();path=self.root/'inputs/evidence.txt';path.write_text(source);digest=hashlib.sha256(path.read_bytes()).hexdigest();refs=['inputs/source-index.json#SRC-001'];self.facts['sources']['sources'][0]['sha256']=digest;self.retained['source_refs']=refs;scope['source_refs']=refs
        def ev(first,last=None):
            last=first if last is None else last
            return dict(file=str(path),source_sha256=digest,line_start=first,line_end=last,source_quote='\n'.join(source.splitlines()[first-1:last]),source_refs=refs[:])
        model=Model(path)
        def named(symbol):
            f=next(f for f in model.functions if f.symbol==symbol and f.is_declaration)
            return dict(symbol=symbol,evidence=ev(source[:f.start].count('\n')+1,source[:f.end].count('\n')+1))
        def line(text):return next(i for i,l in enumerate(source.splitlines(),1)if text in l)
        proof=dict(source_refs=refs,target_symbol='exportTasks');review=StrictReview(self.root,self.facts);engine=ChainReview(review,proof,self.retained,scope,path.resolve())
        return engine,ev,named,line,proof

    def p4_reporting_flow(self,source=P4_REPORT_PORTABLE_SOURCE):
        engine,ev,named,line,proof=self.p4_reporting_make(source);target="window.Pages['demo'].exportTasks";publine=line("window.Pages['demo']={")
        route=dict(route_key='task-route',route_page='demo',route_view='ivr',route_options_policy='default_route_view_no_override',registration_kind='literal_map_property',evidence=dict(route_registration=ev(line('const menu='),line("routes['task-route']=")),route_resolution=named('resolve')['evidence'],route_dispatch=ev(line('window.navigateTo='),line('const content=')),route_sink=ev(line('content.innerHTML='))))
        flow=dict(version='bounded-active-source-flow@1',reachability='conditional_only',family='reporting',profile_kind='task_list_export',entry_binding=dict(route=route,page_export=dict(callee="window.Pages['demo'].render",symbol='render',evidence=ev(publine)),render=named('render'),dispatch=dict(kind='view_literal_branches',option_parameter='options',state_variable='view',delegate=named('taskPage'))),flow_steps=[dict(kind=k)for k in ['render_to_producer','producer_to_UI_slot','UI_event_to_original_handler']],event_binding=dict(event='click',callee=target,publisher=dict(callee=target,symbol='exportTasks',evidence=ev(publine)),evidence=named('taskPage')['evidence'],ui_alias='ui',ui_alias_evidence=ev(line('var ui=')),ui_slot=dict(helper_symbol='toolbar',argument_index=0,parameter='primary',implementation_evidence=named('toolbar')['evidence'],export_evidence=ev(line('window.PlatformUI='))),scalar_selectors=[],literal_arguments=['ivr-type']),result_binding=dict(kind='handler',terminal=named('exportTasks')))
        chain=dict(version='bounded-active-source-chain@1',entry=dict(kind='template_event',event='click',callee=target,evidence=flow['event_binding']['evidence']),source_flow_provenance=flow,terminal=named('exportTasks'));proof['source_chain']=chain
        return engine,flow

    def p4_reporting_pass(self,source=P4_REPORT_PORTABLE_SOURCE):
        engine,flow=self.p4_reporting_flow(source);result=engine.validate_active_flow();self.assertEqual('exportTasks',result['terminal_symbol']);return engine,flow

    def p4_reporting_reject(self,source):
        engine,flow=self.p4_reporting_flow(source)
        with self.assertRaises(Unsupported):engine.validate_active_flow()

    def test_reporting_fixed_route_concat_control_exact_parameter_passes(self):
        self.p4_reporting_pass()

    def test_reporting_route_view_and_fixed_argument_are_source_bound(self):
        for old,new in [("taskPage('ivr-type')","taskPage('other-type')"),("view==='ivr'","view==='other'")]:
            with self.subTest(new=new):self.p4_reporting_pass();self.p4_reporting_reject(P4_REPORT_PORTABLE_SOURCE.replace(old,new))

    def test_reporting_active_slot_does_not_accept_inert_or_attribute_button(self):
        for wrapper in ['<template>{}</template>','<textarea>{}</textarea>','<div hidden>{}</div>','<!--{}-->']:
            with self.subTest(wrapper=wrapper):
                self.p4_reporting_pass();s=P4_REPORT_PORTABLE_SOURCE.replace('<button onclick=',wrapper.split('{}')[0]+'<button onclick=').replace('Export</button>','Export</button>'+wrapper.split('{}')[1]);self.p4_reporting_reject(s)

    def test_reporting_unused_control_or_dead_return_is_not_entry(self):
        for s in [P4_REPORT_PORTABLE_SOURCE.replace("return '<section>'+ui.toolbar", "const unused=ui.toolbar").replace("+'</section>';","; return '<section></section>';"),P4_REPORT_PORTABLE_SOURCE.replace("return '<section>'+ui.toolbar","if(false) return '<section>'+ui.toolbar")]:
            with self.subTest(source=s):self.p4_reporting_pass();self.p4_reporting_reject(s)

    def test_reporting_fake_ui_or_wrong_control_receiver_rejected(self):
        for old,new in [('var ui=PlatformUI','var ui=Diagnostics'),("demo\\'].exportTasks","other\\'].exportTasks")]:
            with self.subTest(new=new):self.p4_reporting_pass();self.p4_reporting_reject(P4_REPORT_PORTABLE_SOURCE.replace(old,new))

    def test_reporting_view_and_parameter_rewrites_rejected_fresh(self):
        for old,new in [("if(view==='ivr')","view='manual';if(view==='ivr')"),("function taskPage(type){","function taskPage(type){type='other';")]:
            with self.subTest(new=new):self.p4_reporting_pass();self.p4_reporting_reject(P4_REPORT_PORTABLE_SOURCE.replace(old,new))

    def test_reporting_exact_sha_quote_and_endpoints_required(self):
        for key in ['source_sha256','source_quote']:
            with self.subTest(key=key):
                e,f=self.p4_reporting_pass();f['event_binding']['evidence'][key]='0'*64 if key=='source_sha256'else'fake'
                with self.assertRaises(Unsupported):e.validate_active_flow()
        e,f=self.p4_reporting_pass();e.proof['source_chain']['terminal']['symbol']='other'
        with self.assertRaises(Unsupported):e.validate_active_flow()

    def test_reporting_missing_explicit_source_flow_not_implicitly_accepted(self):
        e,f=self.p4_reporting_pass();e.proof['source_chain'].pop('source_flow_provenance')
        with self.assertRaises(Unsupported):e.validate_active_flow()

    def p4_reporting_array(self,source=P4_REPORT_ARRAY_SOURCE,view='leads',key='leadCount'):
        e,ev,named,line,proof=self.p4_reporting_make(source);info=e.p4_rep_function(named('cards'));return e.p4_rep_array_call(info,dict(array_symbol='items',item_symbol='card',argument_index=2,literal=key),{'view':view})

    def test_reporting_literal_array_view_selection_is_exact_and_consumed(self):
        self.assertTrue(self.p4_reporting_array());self.assertTrue(self.p4_reporting_array(view='other',key='total'))

    def test_reporting_wrong_array_view_or_metric_key_rejected(self):
        for view,key in [('leads','total'),('other','leadCount')]:
            with self.subTest(view=view):self.assertTrue(self.p4_reporting_array());
            with self.assertRaises(Unsupported):self.p4_reporting_array(view=view,key=key)

    def test_reporting_array_clear_or_unused_join_rejected(self):
        for s in [P4_REPORT_ARRAY_SOURCE.replace('return `<section>','items=[];return `<section>'),P4_REPORT_ARRAY_SOURCE.replace("${items.join('')}","${other.join('')}")]:
            with self.subTest(source=s):
                self.assertTrue(self.p4_reporting_array())
                with self.assertRaises(Unsupported):self.p4_reporting_array(s)


    def p4_reporting_push(self,source=P4_REPORT_PUSH_SOURCE,action='pause'):
        e,ev,named,line,proof=self.p4_reporting_make(source);info=e.p4_rep_function(named('actions'));event=dict(event='click',callee='Widget.control',literal_arguments=[action],evidence=ev(4 if action=='pause'else 5));return e.p4_rep_push_event(info,event,dict(array_symbol='buttons',identity_field='id'))

    def p4_reporting_tabs(self,source=P4_REPORT_TAB_SOURCE):
        e,ev,named,line,proof=self.p4_reporting_make(source);info=e.p4_rep_function(named('body'));event=dict(event='click',callee='Widget.setTab',literal_arguments=['events']);return e.p4_rep_literal_tab_control(info,event,dict(evidence=ev(3),variable='tabs',literal='events'))

    def test_reporting_push_join_preserves_conditional_object_action(self):
        self.assertTrue(self.p4_reporting_push());self.assertTrue(self.p4_reporting_push(action='resume'))

    def test_reporting_push_wrong_identity_action_or_dead_guard_rejected(self):
        for old,new in [("row.id}","row.other}"),("'pause'","'other'"),("if(row.status==='running')","if(false)"),("Widget.control","Other.control")]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_reporting_push())
                with self.assertRaises(Unsupported):self.p4_reporting_push(P4_REPORT_PUSH_SOURCE.replace(old,new))

    def test_reporting_push_clear_unused_or_inert_result_rejected(self):
        for s in [P4_REPORT_PUSH_SOURCE.replace("return buttons.join('');","buttons=[];return buttons.join('');"),P4_REPORT_PUSH_SOURCE.replace('buttons.push(`<button','buttons.push(`<template><button').replace('Pause</button>`','Pause</button></template>`'),P4_REPORT_PUSH_SOURCE.replace("return buttons.join('');","buttons.join('');return '';" )]:
            with self.subTest(source=s):
                self.assertTrue(self.p4_reporting_push())
                with self.assertRaises(Unsupported):self.p4_reporting_push(s)

    def test_reporting_literal_pair_tabs_reach_active_body(self):
        self.p4_reporting_tabs()

    def test_reporting_literal_pair_tabs_wrong_key_or_inert_ancestor_rejected(self):
        for old,new in [("['events','Events']","['other','Events']"),("'<div role=","'<template><div role="),("return '<section>'+tabs","return '<section>'+unused")]:
            with self.subTest(new=new):
                self.p4_reporting_tabs()
                with self.assertRaises(Unsupported):self.p4_reporting_tabs(P4_REPORT_TAB_SOURCE.replace(old,new))


    def p4_reporting_task_rows(self,source=P4_REPORT_TASK_ROWS_SOURCE):
        e,ev,named,line,proof=self.p4_reporting_make(source);info=e.p4_rep_function(named('taskPage'));table=e.p4_rep_calls(info,'ui.table')[0];return e.p4_rep_task_rows(info,table,{'type':'IVR 外呼'})

    def test_reporting_task_rows_preserve_identity_and_positive_page_slice(self):
        self.assertTrue(self.p4_reporting_task_rows())

    def test_reporting_task_rows_wrong_clone_slot_or_later_identity_write_rejected(self):
        for old,new in [('orderNo:','taskId:'),('return displayRow;','displayRow.taskId="other";return displayRow;')]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_reporting_task_rows())
                with self.assertRaises(Unsupported):self.p4_reporting_task_rows(P4_REPORT_TASK_ROWS_SOURCE.replace(old,new))

    def test_reporting_task_rows_static_empty_slice_or_nonreturn_rejected(self):
        for old,new in [('(state.page-1)*pageSize,state.page*pageSize','0,0'),('return displayRow;','return unrelated;')]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_reporting_task_rows())
                with self.assertRaises(Unsupported):self.p4_reporting_task_rows(P4_REPORT_TASK_ROWS_SOURCE.replace(old,new))

    def test_reporting_action_identity_copy_and_known_mutator_rejected(self):
        for added in ["const hidden=row;hidden.id='other';", "const hidden=(row);hidden.id='other';", "Object.assign(row,{id:'other'});"]:
            with self.subTest(added=added):
                self.assertTrue(self.p4_reporting_push())
                with self.assertRaises(Unsupported):self.p4_reporting_push(P4_REPORT_PUSH_SOURCE.replace('const buttons=[];','const buttons=[];'+added))

    def p4_reporting_import_table(self,source=P4_REPORT_IMPORT_TABLE_SOURCE):
        e,ev,named,line,proof=self.p4_reporting_make(source);info=e.p4_rep_function(named('list'));alias=ev(line('const ui=PlatformUI,esc=ui.escape;'));exp=ev(line('window.PlatformUI ='));mapper=dict(symbol='arg',evidence=ev(line('const arg=')),ui_alias='ui',escape_alias='esc',alias_evidence=alias,escape_implementation_evidence=named('escape')['evidence'],escape_export_evidence=exp);e.p4_rep_value_mapper(mapper,info['parent'],info['function'].body_start+1)
        spec=dict(ui_alias='ui',ui_alias_evidence=alias,key='id',mapper='arg',table_implementation_evidence=named('table')['evidence'],table_export_evidence=exp);event=dict(event='click',callee='Widget.open',literal_arguments=[],evidence=named('list')['evidence']);return e.p4_rep_table_control(info,event,spec)

    def test_reporting_import_table_exact_json_escaped_batch_identity(self):
        self.assertTrue(self.p4_reporting_import_table())

    def test_reporting_import_table_wrong_field_key_or_owner_rejected(self):
        for old,new in [('arg(b.id)','arg(b.other)'),("key:'id',render", "key:'other',render"),('Widget.open','Other.open')]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_reporting_import_table())
                with self.assertRaises(Unsupported):self.p4_reporting_import_table(P4_REPORT_IMPORT_TABLE_SOURCE.replace(old,new))

    def test_reporting_import_table_static_empty_or_unused_rows_rejected(self):
        for old,new in [('const bs=readExistingBatches();','const bs=[];'),("return '<section>'+ui.table","const discarded=ui.table")]:
            with self.subTest(new=new):
                self.assertTrue(self.p4_reporting_import_table())
                src=P4_REPORT_IMPORT_TABLE_SOURCE.replace(old,new)
                if 'discarded'in src:src=src.replace("+'</section>';",";return '<section></section>';")
                with self.assertRaises(Unsupported):self.p4_reporting_import_table(src)

    def test_reporting_dead_builder_bindings_never_feed_outer_return(self):
        self.p4_reporting_tabs()
        dead=P4_REPORT_TAB_SOURCE.replace('const tabs=','if(false){const tabs=').replace("+'</div>';","+'</div>';};")
        with self.assertRaises(Unsupported):self.p4_reporting_tabs(dead)
        self.assertTrue(self.p4_reporting_push())
        with self.assertRaises(Unsupported):self.p4_reporting_push(P4_REPORT_PUSH_SOURCE.replace('const buttons=[];','if(false){const buttons=[];};'))
        self.assertTrue(self.p4_reporting_task_rows())
        with self.assertRaises(Unsupported):self.p4_reporting_task_rows(P4_REPORT_TASK_ROWS_SOURCE.replace('var rows=taskRows(type);','if(false){var rows=taskRows(type);};'))
        self.p4_reporting_pass()
        dead=P4_REPORT_PORTABLE_SOURCE.replace("if(view==='ivr') return taskPage('ivr-type');","if(false){if(view==='ivr') return taskPage('ivr-type');};")
        self.p4_reporting_reject(dead)

        self.assertTrue(self.p4_reporting_import_table())
        dead=P4_REPORT_IMPORT_TABLE_SOURCE.replace("const arg=value=>esc(JSON.stringify(String(value||'')));","if(false){const arg=value=>esc(JSON.stringify(String(value||'')));};")
        with self.assertRaises(Unsupported):self.p4_reporting_import_table(dead)


# Portable execution source-flow tests. No prototype JS is executed.
import copy,hashlib,unittest
from operation_attribution import StrictReview
from bounded_source_chain import ChainReview,Unsupported,Token

EXF_SOURCE='''(function(){
function open(id){editor={id,context:context(),mode:'edit'};}
function save(){const e=editor;return Store.commit(e.id);}
function commit(id){return {ok:true,id};}
window.Store={commit};
})();
'''
class RemainingExecutionFlowTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    def source_engine(self,source):
        scope,_=self.canonical();p=self.root/'inputs/evidence.txt';p.write_text(source);h=hashlib.sha256(p.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=h;refs=['inputs/source-index.json#SRC-001'];self.retained['source_refs']=refs;scope['source_refs']=refs;engine=ChainReview(StrictReview(self.root,self.facts),{'source_refs':refs},self.retained,scope,p)
        def ev(first,last=None):
            last=first if last is None else last
            return dict(file=str(p),source_sha256=h,line_start=first,line_end=last,source_quote='\n'.join(source.splitlines()[first-1:last]),source_refs=refs[:])
        return engine,ev
    def function(self,engine,ev,symbol,line):return engine.p4_execution_named(dict(symbol=symbol,evidence=ev(line)))
    def test_execution_active_raw_event_and_original_argument_slots(self):
        source="function show(){return '<button onclick=\"Widget.open(\\\''+row.id+'\\\')\">Open</button>';}"
        e,ev=self.source_engine(source);f=self.function(e,ev,'show',1);args=e.p4_execution_html_event(e.p4_execution_return(f),'click','Widget.open');self.assertEqual(['row','.','id'],[t.value for t in args[0]])
    def test_execution_event_rejects_inert_comment_attribute_or_wrong_owner(self):
        for source in ["function show(){return '<template><button onclick=\"Widget.open()\">Open</button></template>';}","function show(){return '<!--<button onclick=\"Widget.open()\">Open</button>-->';}","function show(){return '<script><button onclick=\"Widget.open()\">Open</button></script>';}","function show(){return '<button onclick=\"Other.open()\">Open</button>';}"]:
            with self.subTest(source=source):
                e,ev=self.source_engine("function show(){return '<button onclick=\"Widget.open()\">Open</button>';}");f=self.function(e,ev,'show',1);self.assertEqual([],e.p4_execution_html_event(e.p4_execution_return(f),'click','Widget.open'))
                e,ev=self.source_engine(source);f=self.function(e,ev,'show',1)
                with self.assertRaises(Unsupported):e.p4_execution_html_event(e.p4_execution_return(f),'click','Widget.open')
    def test_execution_original_ui_value_is_active_not_attribute(self):
        e,ev=self.source_engine("function show(){return '<section>'+body+'</section>';}");f=self.function(e,ev,'show',1);e.p4_execution_active_value(e.p4_execution_return(f),'body')
        e,ev=self.source_engine("function show(){return '<section title=\"'+body+'\">text</section>';}");f=self.function(e,ev,'show',1)
        with self.assertRaises(Unsupported):e.p4_execution_active_value(e.p4_execution_return(f),'body')
    def test_execution_conditional_extracts_exact_original_selected_branch(self):
        e,ev=self.source_engine("function show(){return current||read?'<section>'+table+'</section>':'';}");f=self.function(e,ev,'show',1);c,y,n=e.p4_execution_ternary(e.p4_execution_return(f));e.p4_execution_equal(c,'current||read');e.p4_execution_equal(n,"''");e.p4_execution_active_value(y,'table')
    def test_execution_dead_guarded_declaration_is_not_root_capture(self):
        for source in ["function show(){if(false){const value=actual();}return '';}","function show(){while(false){const value=actual();}return '';}"]:
            e,ev=self.source_engine("function show(){while(false){}const value=actual();return '';}");f=self.function(e,ev,'show',1);e.p4_execution_const(f,'value')
            e,ev=self.source_engine(source);f=self.function(e,ev,'show',1)
            with self.assertRaises(Unsupported):e.p4_execution_const(f,'value')
    def test_execution_pattern_requires_same_actual_lexical_function(self):
        e,ev=self.source_engine("function outer(){if(ok)return save(id);return '';}");f=self.function(e,ev,'outer',1);e.p4_execution_pattern(f,'save(id)')
        e,ev=self.source_engine("function outer(){const dead=()=>save(id);return '';}");f=self.function(e,ev,'outer',1)
        with self.assertRaises(Unsupported):e.p4_execution_pattern(f,'save(id)')
    def test_execution_one_return_rejects_unconditional_prior_return(self):
        e,ev=self.source_engine("function show(){return 'actual';}");f=self.function(e,ev,'show',1);e.p4_execution_return(f)
        for source in ["function show(){return '';return 'actual';}","function show(){if(true)return '';return 'actual';}"]:
            e,ev=self.source_engine(source);f=self.function(e,ev,'show',1)
            with self.assertRaises(Unsupported):e.p4_execution_return(f)
    def test_execution_callsite_quote_and_exact_owner_are_mandatory(self):
        e,ev=self.source_engine("function save(){return Store.commit(id);}");f=self.function(e,ev,'save',1);e.p4_execution_call(f,'Store.commit',ev(1))
        with self.assertRaises(Unsupported):e.p4_execution_call(f,'Other.commit',ev(1))
        bad=ev(1);bad['source_quote']='invented'
        with self.assertRaises(Unsupported):e.p4_execution_call(f,'Store.commit',bad)
    def contract(self):
        e,ev=self.source_engine(EXF_SOURCE);anchor=self.function(e,ev,'open',2);save=self.function(e,ev,'save',3);result=self.function(e,ev,'commit',4);call=e.p4_execution_call(save,'Store.commit',ev(3));publication=e.p4_flow_export(dict(kind='static_owner_export',callee='window.Store.commit',symbol='commit',evidence=ev(5)))
        save['event_evidence']=anchor['evidence'];save['capture_proofs']={k:[anchor['body'][0]]for k in ['identity','context','mode']};result.update(call=call,publication=publication)
        payload={'version':'bounded-captured-user-save@1','anchor':{'symbol':'open','evidence':ev(2)},'save':{'symbol':'save','evidence':ev(3)},'result':{'symbol':'commit','evidence':ev(4)},'save_event':{'evidence':ev(2)}}
        return e,dict(result_binding=dict(followup=payload)),anchor,save,result
    def test_execution_followup_requires_real_distinct_save_and_result_functions(self):
        e,flow,a,s,r=self.contract();self.assertTrue(e.p4_captured_user_save_contract(flow,a,s,r))
        flow['result_binding']['followup']['save']['symbol']='open'
        with self.assertRaises(Unsupported):e.p4_captured_user_save_contract(flow,a,s,r)
    def test_execution_followup_missing_context_mode_or_identity_is_pending(self):
        for role in ['context','mode','identity']:
            e,flow,a,s,r=self.contract();self.assertTrue(e.p4_captured_user_save_contract(flow,a,s,r));s['capture_proofs'].pop(role)
            with self.assertRaises(Unsupported):e.p4_captured_user_save_contract(flow,a,s,r)
    def test_execution_followup_cannot_replace_actual_tokens_with_boolean_claims(self):
        e,flow,a,s,r=self.contract();self.assertTrue(e.p4_captured_user_save_contract(flow,a,s,r));s['capture_proofs']['context']=True
        with self.assertRaises(Unsupported):e.p4_captured_user_save_contract(flow,a,s,r)
    def test_execution_followup_cannot_replace_original_save_event_evidence(self):
        e,flow,a,s,r=self.contract();self.assertTrue(e.p4_captured_user_save_contract(flow,a,s,r));flow['result_binding']['followup']['save_event']['evidence']=flow['result_binding']['followup']['save']['evidence']
        with self.assertRaises(Unsupported):e.p4_captured_user_save_contract(flow,a,s,r)
    def test_execution_followup_commit_publication_cannot_be_decoy(self):
        e,flow,a,s,r=self.contract();self.assertTrue(e.p4_captured_user_save_contract(flow,a,s,r));r['publication']['function']=a['function']
        with self.assertRaises(Unsupported):e.p4_captured_user_save_contract(flow,a,s,r)
    def test_execution_followup_call_must_be_actual_call_in_original_user_save(self):
        e,flow,a,s,r=self.contract();self.assertTrue(e.p4_captured_user_save_contract(flow,a,s,r));r['call']=object()
        with self.assertRaises(Unsupported):e.p4_captured_user_save_contract(flow,a,s,r)


# Portable field-kind source grammar regressions. No source JS is executed.
import hashlib,unittest
from bounded_source_chain import ChainReview,Unsupported
from operation_attribution import StrictReview
FDF_CONSTRUCTOR=r'''(function(){
function escape(value){return String(value==null?'':value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#039;');}
window.PlatformUI={escape};
})();
(function(){
const ui=PlatformUI,esc=ui.escape;
const action=(method,...args)=>esc('Widget.'+method+'('+args.map(v=>JSON.stringify(v)).join(',')+')');
function render(){return `<button onclick="${action('open',kind,row.id)}">Edit</button><button onclick="${action('open',kind,row.id,true)}">Preview</button>`;}
})();
'''
class RemainingFieldFlowTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    def engine(self,source):
        scope,_=self.canonical();p=self.root/'inputs/evidence.txt';p.write_text(source);h=hashlib.sha256(p.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=h;refs=['inputs/source-index.json#SRC-001'];self.retained['source_refs']=refs;scope['source_refs']=refs;e=ChainReview(StrictReview(self.root,self.facts),{'source_refs':refs},self.retained,scope,p)
        def ev(a,z=None):
            z=a if z is None else z;return dict(file=str(p),source_sha256=h,line_start=a,line_end=z,source_quote='\n'.join(source.splitlines()[a-1:z]),source_refs=refs[:])
        return e,ev
    def named(self,e,ev,symbol,line):return e.p4_execution_named(dict(symbol=symbol,evidence=ev(line)))
    def template(self,e,ev,line=1,symbol='render'):
        f=self.named(e,ev,symbol,line);return f,e.p4_execution_return(f)
    def test_field_render_default_state_is_required_even_for_literal_defaults(self):
        for default in ["'category'","'field'",'otherKind']:
            with self.subTest(default=default):
                e,ev=self.engine("function render(kind=activeKind){return '';}");f=self.named(e,ev,'render',1);e.p4_field_render_default(f)
                e,ev=self.engine('function render(kind='+default+"){return '';}");f=self.named(e,ev,'render',1)
                with self.assertRaises(Unsupported):e.p4_field_render_default(f)
    def test_field_original_default_cannot_be_removed_or_change_parameter(self):
        for source in ["function render(kind){return '';}","function render(other=activeKind){return '';}"]:
            e,ev=self.engine(source);f=self.named(e,ev,'render',1)
            with self.assertRaises(Unsupported):e.p4_field_render_default(f)
    def constructor(self,source=FDF_CONSTRUCTOR):
        e,ev=self.engine(source);f=self.named(e,ev,'render',8);spec=dict(version='bounded-concat-owner-alias-handler@1',symbol='action',escape_symbol='esc',evidence=ev(7),ui_alias='ui',ui_alias_evidence=ev(6),escape_alias_evidence=ev(6),escape_implementation_evidence=ev(2),escape_export_evidence=ev(3));return e,ev,f,spec
    def test_field_exact_edit_preview_tuple_shares_real_handler_without_merging_operations(self):
        e,ev,f,p=self.constructor();helper=e.p4_execution_constructor(p,f);tokens=e.p4_execution_return(f)
        self.assertEqual(2,len(e.p4_field_exact_event(tokens,'click','Widget.open',helper,['kind','row.id'])))
        self.assertEqual(3,len(e.p4_field_exact_event(tokens,'click','Widget.open',helper,['kind','row.id','true'])))
    def test_field_tuple_rejects_wrong_id_or_preview_boolean_with_fresh_source(self):
        for source in [FDF_CONSTRUCTOR.replace('row.id','row.otherId'),FDF_CONSTRUCTOR.replace('row.id,true','row.id,false')]:
            e,ev,f,p=self.constructor();h=e.p4_execution_constructor(p,f);e.p4_field_exact_event(e.p4_execution_return(f),'click','Widget.open',h,['kind','row.id','true'])
            e,ev,f,p=self.constructor(source);h=e.p4_execution_constructor(p,f)
            with self.assertRaises(Unsupported):e.p4_field_exact_event(e.p4_execution_return(f),'click','Widget.open',h,['kind','row.id','true'])
    def test_field_owner_alias_constructor_rejects_fake_ui_json_or_escape(self):
        for source in [FDF_CONSTRUCTOR.replace('ui=PlatformUI','ui=FakeUI'),FDF_CONSTRUCTOR.replace('JSON.stringify(v)',"'fixed'"),FDF_CONSTRUCTOR.replace('esc=ui.escape','esc=ui.raw')]:
            e,ev,f,p=self.constructor();e.p4_execution_constructor(p,f)
            e,ev,f,p=self.constructor(source)
            with self.assertRaises(Unsupported):e.p4_execution_constructor(p,f)
    def test_field_exact_tuple_cannot_borrow_inert_ancestor(self):
        source=FDF_CONSTRUCTOR.replace('return `<button','return `<template><button').replace('</button>`;','</button></template>`;')
        e,ev,f,p=self.constructor();h=e.p4_execution_constructor(p,f);e.p4_field_exact_event(e.p4_execution_return(f),'click','Widget.open',h,['kind','row.id'])
        e,ev,f,p=self.constructor(source);h=e.p4_execution_constructor(p,f)
        with self.assertRaises(Unsupported):e.p4_field_exact_event(e.p4_execution_return(f),'click','Widget.open',h,['kind','row.id'])
    def test_field_selected_conditional_is_explicit_and_activity_checked(self):
        source="function render(){return `<section>${editor.previewOnly?'':'<button onclick=\"Widget.save()\">Save</button>'}</section>`;}"
        e,ev=self.engine(source);f,t=self.template(e,ev);child=e.p4_field_selected_child(t[0],'editor.previewOnly','alternate',0);self.assertEqual([],e.p4_execution_html_event(child,'click','Widget.save'))
        for changed in [source.replace('editor.previewOnly','other.previewOnly'),source.replace('<section>','<textarea>').replace('</section>','</textarea>')]:
            e,ev=self.engine(changed);f,t=self.template(e,ev)
            with self.assertRaises(Unsupported):e.p4_field_selected_child(t[0],'editor.previewOnly','alternate',0)
    def test_field_conditional_path_missing_or_wrong_index_is_pending(self):
        e,ev=self.engine("function render(){return `<section>${editor.previewOnly?'':'<button>Save</button>'}</section>`;}");f,t=self.template(e,ev);e.p4_field_selected_child(t[0],'editor.previewOnly','alternate',0)
        for index in [-1,1,True]:
            with self.assertRaises(Unsupported):e.p4_field_selected_child(t[0],'editor.previewOnly','alternate',index)
    def test_field_root_state_or_wrapper_pattern_requires_real_scope_and_exact_quote(self):
        source="(function(){\nlet activeKind='category';\nfunction render(kind=activeKind){return '';}\n})();\n";e,ev=self.engine(source);f=self.named(e,ev,'render',3);e.p4_field_root_pattern(f['model'],f['parent'],ev(2),"let activeKind='category';")
        bad=ev(2);bad['source_quote']='invented'
        with self.assertRaises(Unsupported):e.p4_field_root_pattern(f['model'],f['parent'],bad,"let activeKind='category';")
        with self.assertRaises(Unsupported):e.p4_field_root_pattern(f['model'],f['function'],ev(2),"let activeKind='category';")


"""Portable paired tests for finite original inbound source bindings."""
import copy,hashlib
from pathlib import Path
import unittest
from bounded_source_chain import ChainReview,Unsupported,lex,Model
from operation_attribution import StrictReview

P4_INBOUND_PAGE='''(function(){
const service=()=>window.MockInbound;
function render(){return 'view';}
})();
'''
P4_INBOUND_SERVICE='''(function(root){
const api={scenario:'success',last:null};
function list(){return {ok:true,rows:[]};}
function save(input,options={}){return commit(input,options);}
Object.assign(api,{list,save});
root.MockInbound=api;
})(window);
'''
P4_INBOUND_TEMPLATE=r'''<div>${service().allowed(true)&&!r.pending?`<button onclick="${command('changeActive',id,Number(r.active)===1?2:1,key)}">Change</button>${Number(r.active)===2?`<button onclick="${command('remove',id,key)}">Delete</button>`:''}`:''}</div>'''

P4_INBOUND_WRITE_SERVICE='''(function(root){
const api={scenario:'success',last:null};const clone=r=>({...r}),str=v=>String(v),storageKey='fixture';let store={};
function list(){return {ok:true,rows:[]};}function setActive(id,active,key){const row={id,active};return write('update',{id:Number(id),active:Number(active)},{...clone(row),active:str(active)},key);}function remove(id,key){return write('delete',{id:Number(id)},null,key);}
function save(input,options={}){return null;}function write(endpoint,fields,nextRow,key){const next={fields,nextRow};if(!persist(next))return failure('保存失败，请检查浏览器存储后重试。');return {ok:true,mock:true};}function persist(next){try{localStorage.setItem(storageKey,JSON.stringify(next));store=next;return true;}catch(_){return false;}}
Object.assign(api,{list,save,setActive,remove});
root.MockInbound=api;
})(window);
'''

class InboundSourceFlowP4Tests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    retained=BoundedSourceChainTests.retained
    canonical=BoundedSourceChainTests.canonical
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)

    def p4i_fixture(self,page=P4_INBOUND_PAGE,service=P4_INBOUND_SERVICE):
        scope,candidate=self.canonical();p=self.root/'inputs/evidence.txt';q=self.root/'inputs/service.js';p.write_text(page);q.write_text(service);refs=['inputs/source-index.json#SRC-001','inputs/source-index.json#SRC-002'];self.retained['source_refs']=refs;scope['source_refs']=refs
        for index,path in enumerate([p,q]):self.facts['sources']['sources'][index].update(snapshot_path=str(path.relative_to(self.root)),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        def ev(path,a,z=None):
            z=a if z is None else z;return dict(file=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),line_start=a,line_end=z,source_quote='\n'.join(path.read_text().splitlines()[a-1:z]),source_refs=refs[:])
        def fn(path,symbol):
            m=Model(path);f=next(f for f in m.functions if f.symbol==symbol and f.is_declaration);return dict(symbol=symbol,evidence=ev(path,m.source[:f.start].count('\n')+1,m.source[:f.end].count('\n')+1))
        engine=ChainReview(StrictReview(self.root,self.facts),dict(source_refs=refs),self.retained,scope,p.resolve());parent=engine.p4i_function(fn(p,'render'))['parent'];spec=dict(version='bounded-extended-mock-service@1',factory_symbol='service',owner='window.MockInbound',factory_evidence=ev(p,2),object_symbol='api',object_evidence=ev(q,2),extension_evidence=ev(q,5),publication_evidence=ev(q,6),root_binding=dict(kind='iife_parameter',alias='root',target='window',evidence=ev(q,1),invocation_evidence=ev(q,len(service.splitlines()))),members={n:fn(q,n)for n in ['list','save']})
        return engine,parent,spec

    def p4i_service_pass(self,page=P4_INBOUND_PAGE,source=P4_INBOUND_SERVICE):
        e,parent,spec=self.p4i_fixture(page,source);result=e.p4i_service(spec,parent);self.assertEqual({'list','save'},set(result['members']));return e,parent,spec

    def test_inbound_original_const_named_extension_window_publication(self):
        self.p4i_service_pass()

    def test_inbound_wrong_factory_receiver_or_window_argument_rejected(self):
        for page,source in [(P4_INBOUND_PAGE.replace('window.MockInbound','window.Other'),P4_INBOUND_SERVICE),(P4_INBOUND_PAGE,P4_INBOUND_SERVICE.replace('})(window);','})(Diagnostics);'))]:
            with self.subTest(source=source):
                self.p4i_service_pass()
                e,parent,spec=self.p4i_fixture(page,source)
                with self.assertRaises(Unsupported):e.p4i_service(spec,parent)

    def test_inbound_wrong_or_executable_member_extension_rejected(self):
        for replacement in ['Object.assign(api,{list,save:other});','Object.assign(api,{list,save:makeSave()});','Object.assign(api,{list,...other});','Object.assign(api,{list,save,save});']:
            with self.subTest(replacement=replacement):
                self.p4i_service_pass();e,parent,spec=self.p4i_fixture(service=P4_INBOUND_SERVICE.replace('Object.assign(api,{list,save});',replacement))
                with self.assertRaises(Unsupported):e.p4i_service(spec,parent)

    def test_inbound_member_overwrite_or_alias_escape_rejected_fresh(self):
        for added in ['api.save=other;','api.save||=other;','const hidden=api;hidden.save=other;','const hidden=(api);hidden.save=other;','Object.assign(api,{save:other});','root.MockInbound.save=other;','Object.assign(root.MockInbound,{save:other});']:
            with self.subTest(added=added):
                self.p4i_service_pass();e,parent,spec=self.p4i_fixture(service=P4_INBOUND_SERVICE.replace('root.MockInbound=api;','root.MockInbound=api;'+added))
                with self.assertRaises(Unsupported):e.p4i_service(spec,parent)

    def test_inbound_deferred_extension_or_publication_cannot_bind_live_service(self):
        for old,new in [('Object.assign(api,{list,save});','if(false){Object.assign(api,{list,save});};'),('root.MockInbound=api;','if(false){root.MockInbound=api;};')]:
            with self.subTest(new=new):
                self.p4i_service_pass();e,parent,spec=self.p4i_fixture(service=P4_INBOUND_SERVICE.replace(old,new))
                with self.assertRaises(Unsupported):e.p4i_service(spec,parent)

    def test_inbound_exact_sha_quote_and_method_implementation_required(self):
        for field in ['source_sha256','source_quote']:
            with self.subTest(field=field):
                e,parent,spec=self.p4i_service_pass();spec['extension_evidence'][field]='0'*64 if field=='source_sha256'else'fake'
                with self.assertRaises(Unsupported):e.p4i_service(spec,parent)
        e,parent,spec=self.p4i_service_pass();spec['members']['save']['symbol']='list'
        with self.assertRaises(Unsupported):e.p4i_service(spec,parent)

    def test_inbound_numeric_toggle_uses_actual_original_row_state(self):
        e,parent,spec=self.p4i_service_pass()
        for active,expected in [(1,2),(2,1)]:self.assertEqual(expected,e.p4i_scalar(lex('Number(r.active)===1?2:1'),{'r.active':active}))
        self.assertTrue(e.p4i_condition(lex('service().allowed(true)&&!r.pending'),{'_factory':'service','_manage':True,'r.pending':False}))
        self.assertFalse(e.p4i_condition(lex('service().allowed(true)&&!r.pending'),{'_factory':'service','_manage':False,'r.pending':False}))

    def test_inbound_unknown_numeric_transform_or_dynamic_action_is_pending(self):
        e,parent,spec=self.p4i_service_pass()
        for text in ['Number(r.other)===1?2:1','mutate(r.active)===1?2:1']:
            with self.subTest(text=text):
                self.assertEqual(1,e.p4i_scalar(lex('Number(r.active)===1?2:1'),{'r.active':2}))
                with self.assertRaises(Unsupported):e.p4i_scalar(lex(text),{'r.active':2})

    def test_inbound_nested_delete_requires_manage_nonpending_disabled(self):
        e,parent,spec=self.p4i_service_pass();root=lex('`'+P4_INBOUND_TEMPLATE+'`')[0];leaves=[t for ts in __import__('bounded_source_chain').iter_lists([root])for t in ts if t.kind=='template'and t.value.startswith('`<button onclick="${command(\'remove\'')];self.assertEqual(1,len(leaves));leaf=leaves[0];valid={'_factory':'service','_manage':True,'r.pending':False,'r.active':2};e.p4i_ancestor(root,leaf,valid)
        for bad in [{**valid,'_manage':False},{**valid,'r.pending':True},{**valid,'r.active':1}]:
            with self.subTest(bad=bad):
                e.p4i_ancestor(root,leaf,valid)
                with self.assertRaises(Unsupported):e.p4i_ancestor(root,leaf,bad)

    def test_inbound_inert_or_attribute_ancestor_cannot_expose_nested_control(self):
        e,parent,spec=self.p4i_service_pass()
        for wrapper in ['<template>{}</template>','<textarea>{}</textarea>','<!--{}-->','<div hidden>{}</div>']:
            with self.subTest(wrapper=wrapper):
                source=wrapper.format(P4_INBOUND_TEMPLATE);root=lex('`'+source+'`')[0];leaf=next(t for ts in __import__('bounded_source_chain').iter_lists([root])for t in ts if t.kind=='template'and t.value.startswith('`<button onclick="${command(\'remove\''))
                with self.assertRaises(Unsupported):e.p4i_ancestor(root,leaf,{'_factory':'service','_manage':True,'r.pending':False,'r.active':2})

    def p4i_write_result(self,source=P4_INBOUND_WRITE_SERVICE,method='setActive'):
        e,parent,spec=self.p4i_fixture(service=source);path=Path(spec['object_evidence']['file']);model=Model(path)
        def fn(name):
            f=next(f for f in model.functions if f.symbol==name and f.is_declaration);a=source[:f.start].count('\n')+1;z=source[:f.end].count('\n')+1
            return dict(symbol=name,evidence=dict(file=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),line_start=a,line_end=z,source_quote='\n'.join(source.splitlines()[a-1:z]),source_refs=['inputs/source-index.json#SRC-001','inputs/source-index.json#SRC-002']))
        spec['members'].update({n:fn(n)for n in ['setActive','remove']});service=e.p4i_service(spec,parent);return e.p4i_component_result(service,method,dict(write=fn('write'),persist=fn('persist')))

    def test_inbound_local_write_retains_outer_call_and_nested_payload_slots(self):
        self.assertEqual('write',self.p4i_write_result()['symbol']);self.assertEqual('write',self.p4i_write_result(method='remove')['symbol'])

    def test_inbound_local_write_wrong_id_active_context_or_verb_rejected(self):
        for old,new in [('id:Number(id)','id:Number(other)'),('active:Number(active)','active:Number(other)'),("},key);}","},otherKey);}"),("write('update'","write('delete'")]:
            with self.subTest(new=new):
                self.assertEqual('write',self.p4i_write_result()['symbol'])
                with self.assertRaises(Unsupported):self.p4i_write_result(P4_INBOUND_WRITE_SERVICE.replace(old,new))

    def test_inbound_published_owner_and_factory_result_copies_cannot_rewrite_method(self):
        for page,service in [(P4_INBOUND_PAGE,P4_INBOUND_SERVICE.replace('root.MockInbound=api;','root.MockInbound=api;const hidden=root.MockInbound;hidden.save=other;')),(P4_INBOUND_PAGE.replace('const service=()=>window.MockInbound;','const service=()=>window.MockInbound;const hidden=service();hidden.save=other;'),P4_INBOUND_SERVICE)]:
            with self.subTest(page=page,service=service):
                self.p4i_service_pass()
                e,parent,spec=self.p4i_fixture(page,service)
                with self.assertRaises(Unsupported):e.p4i_service(spec,parent)


# Portable queue source-flow regression tests. No source JS is executed.
import hashlib,unittest
from bounded_source_chain import ChainReview,Unsupported
from operation_attribution import StrictReview
QQ_ANCHOR="""function continueCreate(){
if(!validPicker()||picker.kind!=='create')return denied();const ids=selectedGroups();if(!ids?.length)return pickerError('请至少选择一个当前租户可关联的技能。');
const key=picker.context;closePicker(true);QueueConfig.openCreate(ids,key);
}
"""
QQ_FOOTER='''function draw(){return `${members?'':`<button onclick="QueueManagement.${create?'continueCreate':'saveAssociation'}()">Save</button>`}`;}'''
class RemainingQueueFlowTests(unittest.TestCase):
    setUp=BoundedSourceChainTests.setUp
    tearDown=BoundedSourceChainTests.tearDown
    canonical=BoundedSourceChainTests.canonical
    candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
    retained=BoundedSourceChainTests.retained
    action=BoundedSourceChainTests.action
    page=BoundedSourceChainTests.page
    def engine(self,source):
        scope,_=self.canonical();p=self.root/'inputs/evidence.txt';p.write_text(source);h=hashlib.sha256(p.read_bytes()).hexdigest();self.facts['sources']['sources'][0]['sha256']=h;refs=['inputs/source-index.json#SRC-001'];self.retained['source_refs']=refs;scope['source_refs']=refs;e=ChainReview(StrictReview(self.root,self.facts),{'source_refs':refs},self.retained,scope,p)
        def ev(a,z=None):
            z=a if z is None else z;return dict(file=str(p),source_sha256=h,line_start=a,line_end=z,source_quote='\n'.join(source.splitlines()[a-1:z]),source_refs=refs[:])
        return e,ev
    def function(self,e,ev,name='draw',last=1):return e.p4_execution_named(dict(symbol=name,evidence=ev(1,last)))
    def test_queue_footer_selected_kind_preserves_next_and_save_actions(self):
        e,ev=self.engine(QQ_FOOTER);f=self.function(e,ev);tokens=e.p4_execution_return(f)
        e.p4_queue_projected_event(tokens,{'members':False,'create':True},'QueueManagement.continueCreate')
        e.p4_queue_projected_event(tokens,{'members':False,'create':False},'QueueManagement.saveAssociation')
        with self.assertRaises(Unsupported):e.p4_queue_projected_event(tokens,{'members':False,'create':False},'QueueManagement.continueCreate')
    def test_queue_unknown_kind_or_nonboolean_claim_cannot_project_a_control(self):
        e,ev=self.engine(QQ_FOOTER);tokens=e.p4_execution_return(self.function(e,ev));e.p4_queue_projected_event(tokens,{'members':False,'create':True},'QueueManagement.continueCreate')
        for selectors in [{},{'members':False},{'members':False,'create':'true'},{'members':False,'create':1}]:
            with self.assertRaises(Unsupported):e.p4_queue_projected_event(tokens,selectors,'QueueManagement.continueCreate')
    def test_queue_members_branch_cannot_become_create(self):
        e,ev=self.engine(QQ_FOOTER);tokens=e.p4_execution_return(self.function(e,ev))
        with self.assertRaises(Unsupported):e.p4_queue_projected_event(tokens,{'members':True,'create':True},'QueueManagement.continueCreate')
    def test_queue_inert_ancestor_rejected_with_fresh_source_evidence(self):
        for prefix,suffix in [('<template>','</template>'),('<script>','</script>'),('<!--','-->'),('<textarea>','</textarea>')]:
            e,ev=self.engine(QQ_FOOTER);tokens=e.p4_execution_return(self.function(e,ev));e.p4_queue_projected_event(tokens,{'members':False,'create':True},'QueueManagement.continueCreate')
            source=QQ_FOOTER.replace('return `${','return `'+prefix+'${').replace('}`;','}'+suffix+'`;');e,ev=self.engine(source);tokens=e.p4_execution_return(self.function(e,ev))
            with self.assertRaises(Unsupported):e.p4_queue_projected_event(tokens,{'members':False,'create':True},'QueueManagement.continueCreate')
    def test_queue_unknown_or_false_selected_condition_is_not_implicitly_true(self):
        for condition in ['false','unknown()']:
            source='function draw(){return `'+"${"+condition+"?'<button onclick=\"QueueManagement.continueCreate()\">Next</button>':''}"+'`;}';e,ev=self.engine(source);tokens=e.p4_execution_return(self.function(e,ev))
            with self.assertRaises(Unsupported):e.p4_queue_projected_event(tokens,{'create':True,'members':False},'QueueManagement.continueCreate')
    def anchor(self,source=QQ_ANCHOR):
        e,ev=self.engine(source);f=self.function(e,ev,'continueCreate',len(source.splitlines()));return e,f
    def test_queue_original_anchor_captures_selection_and_context_before_close(self):
        e,f=self.anchor();captured=e.p4_queue_create_anchor(f);self.assertEqual({'selection_ids','context'},set(captured));self.assertTrue(all(captured.values()))
    def test_queue_anchor_cannot_swap_ids_context_or_close_order(self):
        for source in [QQ_ANCHOR.replace('openCreate(ids,key)','openCreate(other,key)'),QQ_ANCHOR.replace('openCreate(ids,key)','openCreate(ids,otherContext)'),QQ_ANCHOR.replace('const key=picker.context;closePicker(true);','closePicker(true);const key=picker.context;')]:
            e,f=self.anchor();e.p4_queue_create_anchor(f)
            e,f=self.anchor(source)
            with self.assertRaises(Unsupported):e.p4_queue_create_anchor(f)
    def test_queue_anchor_cannot_drop_original_mode_or_empty_selection_guard(self):
        for source in [QQ_ANCHOR.replace("picker.kind!=='create'","picker.kind!=='association'"),QQ_ANCHOR.replace("if(!ids?.length)return pickerError('请至少选择一个当前租户可关联的技能。');",'')]:
            e,f=self.anchor();e.p4_queue_create_anchor(f)
            e,f=self.anchor(source)
            with self.assertRaises(Unsupported):e.p4_queue_create_anchor(f)
    def test_queue_anchor_proof_cannot_use_a_different_function_scope(self):
        source=QQ_ANCHOR.replace('QueueConfig.openCreate(ids,key);','const later=()=>QueueConfig.openCreate(ids,key);')
        e,f=self.anchor();e.p4_queue_create_anchor(f)
        e,f=self.anchor(source)
        with self.assertRaises(Unsupported):e.p4_queue_create_anchor(f)
    def test_queue_readonly_editable_footer_does_not_offer_save(self):
        source='''function draw(){return `<section>${editor.editable?'<button onclick="QueueDetail.save()">Save</button>':''}</section>`;}''';e,ev=self.engine(source);tokens=e.p4_execution_return(self.function(e,ev));e.p4_queue_projected_event(tokens,{'editor.editable':True},'QueueDetail.save')
        with self.assertRaises(Unsupported):e.p4_queue_projected_event(tokens,{'editor.editable':False},'QueueDetail.save')


# Portable finite original membership-column provenance tests; source JS is data only.
import hashlib,unittest
from bounded_source_chain import Unsupported
P4_MEMBER_SOURCE='(function(){\nvar ui=PlatformUI;\nvar esc=ui.escape;\nfunction accountRows() {\n    ensureSelectedTenant();\n    return memberships().filter(function (member) {\n      return member.tenantId === selectedTenantId;\n    }).map(function (member) {\n      var account = CloudCallRuntime.account(member.accountId) || {};\n      var tenant = CloudCallRuntime.tenant(member.tenantId) || {};\n      return {\n        membershipId: member.membershipId,\n        accountId: account.accountId,\n        name: account.name,\n        nickname: account.nickname || account.name,\n        avatar: account.avatar,\n        mobile: account.mobile || maskMobile(account.loginMobile),\n        loginMobile: account.loginMobile,\n        loginUsername: account.loginUsername || \'\',\n        accountStatus: account.status,\n        tenantId: tenant.tenantId,\n        enterpriseId: tenant.enterpriseId,\n        tenantName: tenant.name,\n        org: tenant.organizationLabel,\n        roleCode: member.roleCode,\n        membershipStatus: member.status,\n        updatedBy: member.updatedBy || account.updatedBy || \'平台超级管理员\',\n        updatedAt: [member.updatedAt, account.updatedAt].filter(Boolean).sort(function (a, b) { return new Date(b).getTime() - new Date(a).getTime(); })[0] || \'\',\n        createdAt: member.createdAt || member.createTime || account.createdAt || account.createTime || \'\'\n      };\n    }).filter(function (row) {\n      var text = [row.nickname, row.name, row.loginUsername, row.mobile, row.loginMobile, row.accountId, row.tenantName].join(\' \').toLowerCase();\n      return (!accountKeyword || text.indexOf(accountKeyword.toLowerCase()) >= 0) && (!accountRole || row.roleCode === accountRole);\n    });\n  }\nfunction renderAccounts() {\n    var rows = accountRows();\n    rows = ui.sortByUpdated?.(rows) || rows;\n    var cloudDomain = true;\n    var pageCount = Math.max(1, Math.ceil(rows.length / accountPageSize));\n    if (accountPage > pageCount) accountPage = pageCount;\n    var pageRows = rows.slice((accountPage - 1) * accountPageSize, accountPage * accountPageSize).map(function (row, index) {\n      return Object.assign({ orderNo: (accountPage - 1) * accountPageSize + index + 1 }, row);\n    });\n    var columns = [\n      { key: \'orderNo\', label: \'序号\', width: \'64px\' },\n      { key: \'nickname\', label: \'账号昵称\', render: function (value, row) {\n        return \'<div class="account-cell">\' + avatarHtml(row, 34) + \'<div><div class="table-main">\' + esc(value) + \'</div>\' + (row.name && row.name !== value ? \'<div class="table-sub">\' + esc(row.name) + \'</div>\' : \'\') + \'</div></div>\';\n      } },\n      { key: \'loginUsername\', label: \'账号\', render: function (value) { return esc(value || \'—\'); } },\n      { key: \'mobile\', label: \'手机号码\' },\n      { key: \'roleCode\', label: \'角色\', help: \'角色只在当前选中的租户内生效，一个账号在同一租户内只有一个角色。\', render: function (value) {\n        return \'<span class="role-pill">\' + esc(roleNames[value] || value) + \'</span>\';\n      } },\n      { key: \'membershipStatus\', label: \'状态\', render: function (value) { return ui.status(value); } },\n    ];\n    if (cloudDomain) {\n      var seatColumns = [\n      { key: \'accountId\', label: \'关联坐席\', help: \'账号在当前租户只关联一个未删除坐席；不会新增平台登录账号。\', render: function (value, row) {\n        var seat = AccountSeat.forAccount(value, row.tenantId, row.enterpriseId);\n        return \'<span class="account-seat-cell">\' + esc(seat ? seat.userName : \'未关联\') + \'</span>\';\n      } },\n      { key: \'accountId\', label: \'坐席开通\', help: \'已开通表示当前租户账号已关联坐席；坐席停用不会取消开通。实际呼叫仍需坐席启用、有效分机、电话就绪及可用号码；预测与呼入分配另行校验。\', render: function (value, row) {\n        var seat = AccountSeat.status(value, row.tenantId, row.enterpriseId);\n        return \'<div class="account-seat-opening" data-seat-opened="\' + !!seat.agent + \'"><span class="seat-opening-badge \' + (seat.agent ? \'is-opened\' : \'is-unopened\') + \'">\' + (seat.agent ? \'已开通\' : \'未开通\') + \'</span>\' +\n          (seat.agent && seat.agent.lifecycleStatus !== \'已启用\' ? \'<div class="table-sub">\' + esc(seat.agent.lifecycleStatus) + \'</div>\' : !seat.agent && seat.attempt ? \'<div class="table-sub warning-text">\' + esc(seat.label) + \'</div>\' : \'\') + \'</div>\';\n      } }\n      ];\n      columns.splice(2, 0, seatColumns[1]);\n      columns.push(seatColumns[0]);\n    }\n    columns.push(\n      ...(!cloudDomain ? [{ key: \'updatedBy\', label: \'更新人\' }] : []),\n      { key: \'updatedAt\', label: \'更新时间\', render: function (value) { return esc(value || \'—\'); } },\n      { key: \'membershipId\', label: \'操作\', className: \'action-column\', render: function (value, row) {\n        var next = row.membershipStatus === \'启用\' ? \'停用\' : \'启用\';\n        var seatAction = \'\';\n        if (cloudDomain) {\n          var error = AccountSeat.scopeError(row.tenantId, row.enterpriseId, row.accountId);\n          var seat = AccountSeat.status(row.accountId, row.tenantId, row.enterpriseId);\n          seatAction = \'<button\' + (error ? \' disabled title="\' + esc(error) + \'"\' : \'\') + \' onclick="window.Pages[\\\'account-tenant\\\'].openAccountSeat(\\\'\' + esc(value) + \'\\\')">\' + (seat.agent ? \'坐席设置\' : seat.attempt ? \'查看并重试\' : \'关联坐席\') + \'</button>\';\n        }\n        return \'<div class="table-actions"><button onclick="window.Pages[\\\'account-tenant\\\'].editMember(\\\'\' + esc(value) + \'\\\')">编辑</button>\' + seatAction + \'<button class="\' + (next === \'停用\' ? \'danger\' : \'\') + \'" onclick="window.Pages[\\\'account-tenant\\\'].confirmToggleMember(\\\'\' + esc(value) + \'\\\')">\' + next + \'</button></div>\';\n      } }\n    );\n    if (cloudDomain) {\n      // The scroll container, not the page, takes overflow.\n    var cloudWidths = [60, 180, 130, 150, 140, 100, 90, 150, 180, 230];\n      columns = columns.map(function (column, index) {\n        return Object.assign({}, column, { width: cloudWidths[index] + \'px\', className: (column.className || \'\') + \' cloud-account-col-\' + index });\n      });\n    }\n    var body = ui.table(columns, pageRows, { className: cloudDomain ? \'cloud-account-table\' : \'\', emptyText: \'当前租户下暂无账号\', emptyDetail: \'点击“新建”添加第一个账号。\', footer: ui.pagination(rows.length, accountPage, accountPageSize, "window.Pages[\'account-tenant\'].setAccountPage") });\n\n    return \'<section class="platform-page account-page\' + (cloudDomain ? \' cloud-account-page\' : \'\') + \'">\' +\n      ui.pageHeader(\'账号管理\', \'管理每个租户下关联的账号信息。\') +\n      \'<div class="account-main-layout"><aside class="account-tenant-panel"><div class="account-tenant-panel-title"><strong>租户列表</strong><span>\' + tenants().length + \' 个</span></div>\' + renderTenantCards() + \'</aside>\' +\n      \'<div class="account-content-panel"><div class="filter-panel"><label class="field"><span>手机号码</span><input id="accountKeyword" value="\' + esc(accountKeyword) + \'" placeholder="请输入手机号码"></label><label class="field"><span>角色</span><select id="accountRoleFilter"><option value="">请选择</option><option value="ADMIN"\' + (accountRole === \'ADMIN\' ? \' selected\' : \'\') + \'>管理员</option><option value="OPERATOR"\' + (accountRole === \'OPERATOR\' ? \' selected\' : \'\') + \'>运营</option></select></label><div class="filter-actions"><button class="btn" onclick="window.Pages[\\\'account-tenant\\\'].resetAccountFilters()">重置</button><button class="btn btn-primary" onclick="window.Pages[\\\'account-tenant\\\'].queryAccounts()">查询</button></div></div>\' +\n      \'<div class="account-table-shell">\' + ui.toolbar(\'<button class="btn btn-primary" onclick="window.Pages[\\\'account-tenant\\\'].openAddMember()">+ 新建</button>\', \'<button class="btn btn-quiet" onclick="window.Pages[\\\'account-tenant\\\'].refreshAccounts()" title="刷新">↻ 刷新</button><span>\' + ui.help(\'账号登录信息全局复用；角色、状态和云呼叫坐席账号按当前租户维护。\', \'账号管理说明\') + \'</span>\') + body + \'</div></div></div>\' +\n    \'</section>\';\n  }\n})();\n'
class RemainingAccountEntryTests(unittest.TestCase):
 setUp=BoundedSourceChainTests.setUp
 tearDown=BoundedSourceChainTests.tearDown
 retained=BoundedSourceChainTests.retained
 action=BoundedSourceChainTests.action
 page=BoundedSourceChainTests.page
 canonical=BoundedSourceChainTests.canonical
 candidate_hash=staticmethod(BoundedSourceChainTests.candidate_hash)
 make_engine=RemainingCommonFlowTests.make_engine
 def entry(self,source=P4_MEMBER_SOURCE,ui_source=None):
  ui_source=ui_source or UI_SUPPORT_SOURCE;source=ui_source+source;e,ev=self.make_engine(source);model=e.evidence(ev(1,len(source.splitlines()))).model
  def f(name):
   fn=next(f for f in model.functions if f.symbol==name and f.is_declaration);return dict(symbol=name,evidence=ev(model.source.count('\n',0,fn.start)+1,model.source.count('\n',0,fn.end)+1))
  line=lambda text:next(i for i,l in enumerate(source.splitlines(),1)if text in l)
  spec=dict(render=f('renderAccounts'),rows=f('accountRows'),event_evidence=ev(line("seatAction = '<button'")),ui_alias_evidence=ev(line('var ui=PlatformUI')),escape_alias_evidence=ev(line('var esc=ui.escape')),table_implementation_evidence=f('table')['evidence'],table_export_evidence=ev(line('window.PlatformUI =')),escape_implementation_evidence=f('escape')['evidence'],escape_export_evidence=ev(line('window.PlatformUI =')))
  return e.p4r_account_membership_entry(spec)
 def test_member_actual_columns_and_table_value_identity(self):
  r=self.entry();self.assertEqual('membershipId',r['identity_field']);self.assertEqual([['esc','(','value',')']],[[t.value for t in a]for a in r['arguments']])
 def test_member_wrong_row_slot_scalar_or_row_overwrite_rejected(self):
  wrong='\n'.join(l.replace('esc(value)','esc(row.accountId)')if "seatAction = '<button'"in l else l for l in P4_MEMBER_SOURCE.splitlines())
  variants=[P4_MEMBER_SOURCE.replace("key: 'membershipId', label: '操作'","key: 'accountId', label: '操作'"),wrong,P4_MEMBER_SOURCE.replace('var next = row.membershipStatus',"value='other';var next = row.membershipStatus"),P4_MEMBER_SOURCE.replace('var next = row.membershipStatus',"mutate(row);var next = row.membershipStatus")]
  for s in variants:
   with self.subTest(source=s):
    self.assertEqual('membershipId',self.entry()['identity_field'])
    with self.assertRaises(Unsupported):self.entry(s)
 def test_member_layout_mutation_alias_or_rows_forwarding_rejected(self):
  variants=[P4_MEMBER_SOURCE.replace('return Object.assign({}, column, { width:', 'return Object.assign({}, column, { render:'),P4_MEMBER_SOURCE.replace('var body = ui.table','var stolen=columns;var body = ui.table'),P4_MEMBER_SOURCE.replace('var body = ui.table','mutate(pageRows);var body = ui.table'),P4_MEMBER_SOURCE.replace('var pageRows = rows.slice','var pageRows = [].slice')]
  for s in variants:
   with self.subTest(source=s):
    self.assertEqual('membershipId',self.entry()['identity_field'])
    with self.assertRaises(Unsupported):self.entry(s)
 def test_member_cloud_or_active_html_path_must_be_real(self):
  variants=[P4_MEMBER_SOURCE.replace('var cloudDomain = true','var cloudDomain = false'),P4_MEMBER_SOURCE.replace('+ seatAction +',"+ '' +"),P4_MEMBER_SOURCE.replace('+ body +',"+ '' +"),P4_MEMBER_SOURCE.replace(' disabled title=', ' onclick=')]
  for s in variants:
   with self.subTest(source=s):
    self.assertEqual('membershipId',self.entry()['identity_field'])
    with self.assertRaises(Unsupported):self.entry(s)


if __name__ == '__main__':
    unittest.main()
