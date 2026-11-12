"""One strict attribution contract for delta functions, retained operations and history.

This module validates ownership/provenance, not production execution or G3 approval.
Candidate/placeholder catalogs and unresolved boundaries are never main coverage.
"""
from __future__ import annotations
import json
import hashlib
import re
from pathlib import Path
from typing import Any
from html.parser import HTMLParser
from retained_operation_contract import Review, FILES, navigation_transition, text, texts
from feature_contract import feature_boundary_errors


class JSONPointerError(ValueError):
    """A read-only JSON pointer did not resolve within its JSON document."""


def pointer_tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise JSONPointerError('Expected an absolute JSON pointer')
    result = []
    for token in pointer[1:].split('/'):
        if re.search(r'~(?![01])', token):
            raise JSONPointerError('Invalid JSON pointer escape')
        result.append(token.replace('~1', '/').replace('~0', '~'))
    return result


def json_pointer_value(document, pointer):
    """RFC6901 object/array lookup only: no eval, attributes or execution."""
    value = document
    for token in pointer_tokens(pointer):
        if isinstance(value, dict):
            if token not in value:
                raise JSONPointerError('Missing JSON object property: ' + token)
            value = value[token]
        elif isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', token):
                raise JSONPointerError('Invalid JSON array index: ' + token)
            index = int(token)
            if index >= len(value):
                raise JSONPointerError('JSON array index out of range: ' + token)
            value = value[index]
        else:
            raise JSONPointerError('Cannot traverse a scalar JSON value')
    return value


def callable_only_trigger(value):
    if not isinstance(value, str):
        return True
    value = value.strip()
    return (bool(re.match(r'^(?:async\s+)?function\b', value))
            or bool(re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$.]*(?:\s*\([^;\n]*\))?;?', value)))


ACTION_SIGNATURE_FIELDS = ('id', 'name', 'permission', 'source_scenario_ids',
                           'source_function_ids', 'retained_operation_refs',
                           'business_object', 'primary_action')


def action_signature_sha256(page_id, action):
    payload = {'page_id': page_id, 'action_fields': {key: action.get(key) for key in ACTION_SIGNATURE_FIELDS}}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def json_objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from json_objects(child)


JS_IDENTIFIER = r'[A-Za-z_$][A-Za-z0-9_$]*'
JS_CALLEE = JS_IDENTIFIER + r'(?:\.' + JS_IDENTIFIER + r')*'
UI_EVENTS = {'click', 'change', 'submit', 'input', 'keydown', 'keyup'}
UI_SINK_CALLEES = {'UI.openLayer', 'UI.openDrawer', 'Modal.open', 'Drawer.open', 'openLayer', 'openDrawer'}


def mask_js_literals_comments(source):
    """Lexical masking for the bounded proof grammar, never JS execution.

    Interpolated templates and slash tokens other than comments are outside
    this grammar. They fail closed rather than being guessed/interpreted.
    """
    result = list(source);i = 0
    while i < len(source):
        if source.startswith('//', i):
            end = source.find('\n', i)
            end = len(source) if end < 0 else end
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            if end < 0:return None
            end += 2
        elif source[i] in "'\"`":
            quote = source[i];end = i + 1
            while end < len(source):
                if source[end] == '\\':end += 2;continue
                if quote == '`' and source.startswith('${', end):return None
                if source[end] == quote:break
                end += 1
            if end >= len(source):return None
            end += 1
        else:
            if source[i] == '/':return None
            i += 1;continue
        for j in range(i, end):
            if result[j] not in '\r\n':result[j] = ' '
        i = end
    return ''.join(result)


def split_static_arguments(source):
    code = mask_js_literals_comments(source)
    if code is None:return None
    stack=[];start=0;parts=[];pairs={')':'(',']':'[','}':'{'}
    for i,char in enumerate(code):
        if char in '([{':stack.append(char)
        elif char in ')]}':
            if not stack or stack.pop()!=pairs[char]:return None
        elif char==',' and not stack:parts.append(source[start:i].strip());start=i+1
    if stack:return None
    parts.append(source[start:].strip())
    return [] if len(parts)==1 and not parts[0] else parts


def single_call_statement(source):
    """Only one exact call, optionally return/await/simple variable assignment."""
    code=mask_js_literals_comments(source)
    if code is None:return None
    prefix=re.match(r'^\s*(?:(?:return\s+)?await\s+|return\s+|(?:const|let|var)\s+'+JS_IDENTIFIER+r'\s*=\s*(?:await\s+)?)?',code)
    start=prefix.end();match=re.match(r'\s*('+JS_CALLEE+r')\s*\(',code[start:])
    if not match:return None
    open_at=start+match.end()-1;stack=[];pairs={')':'(',']':'[','}':'{'};close_at=None
    for i,char in enumerate(code[open_at:],open_at):
        if char in '([{':stack.append(char)
        elif char in ')]}':
            if not stack or stack.pop()!=pairs[char]:return None
            if not stack:close_at=i;break
    if close_at is None or code[close_at+1:].strip() not in {'',';'}:return None
    args=split_static_arguments(source[open_at+1:close_at])
    if args is None or any(not arg for arg in args) or '${' in code:return None
    return match.group(1),args


def static_js_literal(source):
    source=source.strip()
    if len(source)<2 or source[0] not in "'\"`" or source[-1]!=source[0]:return None
    if '\\' in source or '${' in source:return None
    if source[0] in source[1:-1]:return None
    return source[1:-1]


def static_object_properties(source):
    source=source.strip()
    if not source.startswith('{') or not source.endswith('}'):return None
    parts=split_static_arguments(source[1:-1])
    if parts is None:return None
    result={}
    for part in parts:
        match=re.fullmatch(r'\s*('+JS_IDENTIFIER+r'|"[^"\\]*"|\'[^\'\\]*\')\s*:\s*(.*)',part,re.S)
        if not match:return None
        key=match.group(1)
        if key[:1] in {'"',"'"}:key=key[1:-1]
        if key in result:return None
        result[key]=match.group(2).strip()
    return result


def direct_owner_binding(source,callee,symbol):
    """Two supported static owner forms, not arbitrary IIFE/data-flow inference."""
    code=mask_js_literals_comments(source)
    if code is None:return False
    code=code.strip().removesuffix(';').strip()
    if re.fullmatch(re.escape(callee)+r'\s*=\s*'+re.escape(symbol),code):return True
    owner,_,method=callee.rpartition('.')
    match=re.fullmatch(r'(?:(?:const|let|var)\s+)?'+re.escape(owner)+r'\s*=\s*(\{.*\})',source.strip().removesuffix(';').strip(),re.S)
    if not match:return False
    properties=static_object_properties(match.group(1))
    return bool(properties is not None and properties.get(method)==symbol)


class UIHandlerParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.handlers=[];self.raw=[]
    def handle_starttag(self,tag,attrs):
        if self.raw:return
        text=self.get_starttag_text() or '';line=self.getpos()[0];end=line+text.count('\n')
        if tag in {'button','a','input','select','textarea','form','div','span','label','td','tr'}:
            for key,value in attrs:
                if key.startswith('on') and key[2:] in UI_EVENTS and isinstance(value,str):
                    self.handlers.append((line,end,value))
        if tag in {'script','style','textarea','title','template','noscript'}:self.raw.append(tag)
    def handle_endtag(self,tag):
        if self.raw and self.raw[-1]==tag:self.raw.pop()


def html_has_exact_handler(html,callee,start=None,end=None):
    parser=UIHandlerParser()
    try:parser.feed(html);parser.close()
    except Exception:return False
    for first,last,handler in parser.handlers:
        if start is not None and (first<start or last>end):continue
        call=single_call_statement(handler)
        if call and call[0]==callee:return True
    return False


def callback_is_exact(callback,callee):
    callback=callback.strip()
    if callback==callee:return True
    # Only an expression-bodied arrow making exactly that one call.
    arrow=re.fullmatch(r'(?:\(\s*(?:'+JS_IDENTIFIER+r'(?:\s*,\s*'+JS_IDENTIFIER+r')*)?\s*\)|'+JS_IDENTIFIER+r')\s*=>\s*(.*)',callback,re.S)
    call=single_call_statement(arrow.group(1)) if arrow else None
    return bool(call and call[0]==callee)


def supported_ui_binding(source,callee,file_path,start,end,full_file):
    call=single_call_statement(source)
    if call and call[0].endswith('.addEventListener') and len(call[1]) in {2,3}:
        event=static_js_literal(call[1][0])
        return event in UI_EVENTS and callback_is_exact(call[1][1],callee)
    code=mask_js_literals_comments(source)
    if code is not None:
        match=re.fullmatch(r'\s*'+JS_CALLEE+r'\.on('+ '|'.join(sorted(UI_EVENTS))+r')\s*=\s*(.*?)\s*;?\s*',source,re.S)
        if match and callback_is_exact(match.group(2).removesuffix(';'),callee):return True
    if file_path.suffix.lower() in {'.html','.htm'}:
        return html_has_exact_handler(full_file,callee,start,end)
    # A complete, simple named render() returning one static HTML literal.
    render=re.fullmatch(r'\s*function\s+render\s*\(\s*\)\s*\{\s*return\s+(.*?)\s*;\s*\}\s*;?\s*',source,re.S)
    if render:
        html=static_js_literal(render.group(1))
        return html is not None and html_has_exact_handler(html,callee)
    # Direct document lookup -> real DOM assignment; arbitrary const strings
    # or arbitrary object.innerHTML are intentionally unsupported.
    dom=re.fullmatch(r'\s*document\.(?:getElementById|querySelector)\(\s*("[^"\\]*"|\'[^\'\\]*\')\s*\)\.innerHTML\s*=\s*(.*?)\s*;?\s*',source,re.S)
    if dom:
        html=static_js_literal(dom.group(2).removesuffix(';').strip())
        return html is not None and html_has_exact_handler(html,callee)
    if call and call[0] in UI_SINK_CALLEES and len(call[1])==1:
        props=static_object_properties(call[1][0])
        if props is not None:
            for key in ('body','content','html'):
                html=static_js_literal(props.get(key,''))
                if html is not None and html_has_exact_handler(html,callee):return True
    return False


class StrictReview(Review):
    def __init__(self, root, facts, *, validate_feature_bodies=True):
        self.validate_feature_bodies=validate_feature_bodies
        super().__init__(root,facts)

    def source(self, sid):
        problems = super().source(sid)
        source = self.sources.get(sid)
        if source and not text(source.get('sha256')):
            return problems + ['Registered source snapshot needs a SHA-256 binding: ' + sid]
        return problems

    def references(self, record, require_bound_source=True):
        refs = record.get('source_refs')
        if not texts(refs, True):
            return ['Missing source_refs']
        errors = []
        index_path = (self.root / 'inputs/source-index.json').resolve()
        for ref in refs:
            file_part, sep, fragment = ref.partition('#')
            # Historical absolute source-index references use the same guarded
            # lookup as their registered snapshots and source-chain files.
            is_index = self.path(file_part, internal=True, must_exist=False) == index_path
            if is_index:
                if not sep or not re.fullmatch(r'SRC-[0-9]{3,}', fragment):
                    errors.append('Malformed source-index reference; an exact registered SRC fragment is required: ' + ref)
                elif require_bound_source:
                    errors.extend(self.source(fragment))
                elif fragment not in self.sources:
                    errors.append('Unregistered source ID: ' + fragment)
                else:
                    # A normal FUNC may refer to legacy non-Wiki source records
                    # without this new reuse contract's snapshot/SHA fields.
                    source=self.sources[fragment]
                    if source.get('sha256') and source.get('snapshot_path'):
                        path=self.path(source['snapshot_path'],internal=True)
                        if path is None or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:
                            errors.append('Registered source snapshot SHA differs: '+fragment)
            elif self.path(ref, internal=True) is None:
                errors.append('Unresolved local evidence reference: ' + ref)
        return errors

    def feature(self, fid):
        # Ordinary FUNC projects keep the public schema contract; reuse must
        # not retroactively require this project's private evidence labels.
        record = self.features.get(fid)
        if record is None:
            return ['Undefined FUNC: ' + str(fid)]
        errors = []
        if self.validate_feature_bodies:
            errors.extend(self.upstream(record))
            data = {'granularity_version': self.facts['features'].get('granularity_version'), 'features': [record]}
            errors.extend(feature_boundary_errors(data))
            for field in ('version', 'module_l1', 'module_l2', 'name', 'description'):
                if not text(record.get(field)):
                    errors.append('FUNC is missing public schema field: ' + field)
        if record.get('source_refs'):
            errors.extend(self.references(record,require_bound_source=False))
        if (record.get('counts_as_confirmed_delta') is False or record.get('candidate_only') is True
                or record.get('status') in {'candidate', 'placeholder', 'unknown'}
                or record.get('catalog_role') == 'local_candidate_not_scope_or_function_approval'
                or record.get('evidence_level') in {'candidate', 'placeholder', 'placeholder_or_simulated_completion_only'}):
            errors.append('FUNC is explicitly only a candidate/placeholder')
        return errors

    def snapshot_objects(self, sid):
        cache = self.__dict__.setdefault('_snapshot_objects_cache', {})
        if sid in cache:
            return cache[sid]
        source = self.sources.get(sid, {})
        p = self.path(source.get('snapshot_path', ''), internal=True)
        if p is None or self.source(sid):
            cache[sid] = []
        else:
            try:
                cache[sid] = list(json_objects(json.loads(p.read_text(encoding='utf-8'))))
            except (ValueError, OSError):
                cache[sid] = []
        return cache[sid]

    def registered_source_ids(self, record):
        return [ref.partition('#')[2] for ref in record.get('source_refs', [])
                if isinstance(ref, str) and ref.startswith('inputs/source-index.json#')
                and re.fullmatch(r'SRC-[0-9]{3,}', ref.partition('#')[2])]

    def evidence_source_chain_errors(self, observation, record):
        path = self.path(observation.get('file', observation.get('path', '')))
        expected = observation.get('source_sha256')
        if path is None:
            return ['Retained source evidence file is missing']
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if expected is not None and (not text(expected) or expected!=actual):
            return ['Retained source evidence file SHA differs: ' + str(path)]
        # The authoritative SHA may be carried by a registered snapshot's
        # exact file->SHA map; it need not be duplicated on every evidence row.
        expected=actual
        ids = set(self.registered_source_ids(record))
        if observation.get('source_id'):
            ids.add(observation['source_id'])
        for sid in ids:
            if self.source(sid):
                continue
            source = self.sources[sid]
            snapshot = self.path(source.get('snapshot_path', ''), internal=True)
            if snapshot == path and source.get('sha256') == expected:
                return []
            for node in self.snapshot_objects(sid):
                for field in ('file', 'path', 'source_file', 'source_path'):
                    candidate_path = self.path(node.get(field, ''))
                    if candidate_path == path and expected in {node.get('sha256'), node.get('source_sha256')}:
                        return []
                # Registered operation-review snapshots may use an exact
                # file-path -> SHA dictionary rather than a list of objects.
                for file_name, digest in node.items():
                    if isinstance(file_name, str) and isinstance(digest, str) and digest == expected and self.path(file_name) == path:
                        return []
        return ['Retained source evidence has no actual registered snapshot file/SHA chain: ' + str(path)]

    def retained_action_binding_errors(self, page, action, key, record):
        bindings = [b for b in action.get('retained_operation_bindings', [])
                    if isinstance(b, dict) and b.get('operation_ref') == key]
        if len(bindings) != 1:
            return ['Retained ACTION needs one independently reviewed operation binding: ' + key]
        binding = bindings[0]
        errors = self.references(binding)
        if (binding.get('binding_kind') != 'retained_operation_binding'
                or binding.get('page_id') != page['id'] or binding.get('action_id') != action['id']
                or binding.get('business_object') != record.get('business_object')
                or binding.get('primary_action') != record.get('primary_action')
                or not text(binding.get('reviewed_by')) or not text(binding.get('reviewed_on'))):
            errors.append('Retained ACTION binding lacks concrete reviewed object/operation/context: ' + key)
        scopes = binding.get('source_scenario_ids', [])
        if (not texts(scopes, True) or not set(scopes).issubset(action.get('source_scenario_ids', []))
                or not set(scopes).issubset(record.get('source_scenario_ids', []))):
            errors.append('Retained ACTION binding is outside actual operation scenario scope: ' + key)
        if binding.get('action_signature_sha256') != action_signature_sha256(page['id'], action):
            errors.append('Retained ACTION name/permission/scope signature changed after independent review: ' + key)
        fields = ('binding_kind', 'page_id', 'action_id', 'operation_ref', 'business_object', 'primary_action',
                  'source_scenario_ids', 'action_signature_sha256', 'reviewed_by', 'reviewed_on')
        declared = any(all(node.get(field) == binding.get(field) for field in fields)
                       for sid in self.registered_source_ids(binding) for node in self.snapshot_objects(sid))
        if not declared:
            errors.append('Retained ACTION binding is not bound by its registered independent review snapshot: ' + key)
        return errors

    def evidence(self, key, record):
        problems = list(super().evidence(key, record))
        for observation in record.get('local_source_evidence', []):
            if isinstance(observation, dict):
                problems.extend(self.evidence_source_chain_errors(observation, record))
        origin_keys = record.get('retained_scope_review', {}).get('origin_candidate_keys', [])
        # A changed key cannot launder a byte-equivalent source candidate.
        def operation_value(item, field):
            return item.get(field, item.get('completion_result_observed_in_source')) if field == 'completion_result' else item.get(field)
        matches_candidate = any(all(operation_value(record, field) == operation_value(candidate, field)
                                    for field in ('business_object', 'primary_action', 'trigger', 'completion_result'))
                                for candidate in self.candidates.values())
        if key in self.candidates or origin_keys or matches_candidate or record.get('catalog_role') == 'canonical_reviewed_original_operation_reference':
            problems.extend(self.reviewed_canonical_errors(key, record))
        if key in self.details or any(key == p.get('observation_key') for p in self.facts['features'].get('placeholder_and_unproven_source_controls', [])):
            problems.append('Auxiliary detail/placeholder cannot be promoted to an independent retained operation: ' + key)
        if record.get('candidate_only') is True or record.get('counts_as_confirmed_existing_usable_operation') is False:
            problems.append('Unapproved candidate/placeholder is not an unchanged original operation: ' + key)
        if not any(isinstance(ref, str) and ref.startswith('inputs/source-index.json#SRC-') for ref in record.get('source_refs', [])):
            problems.append('Original operation needs registered source provenance: ' + key)
        allowed_stories, allowed_ac = set(), set()
        for sid in record.get('source_scenario_ids', []):
            sc = self.scenarios.get(sid, {})
            allowed_stories.update(sc.get('source_story_ids', []))
            allowed_ac.update(sc.get('source_acceptance_ids', []))
        if not set(record.get('source_story_ids', [])).issubset(allowed_stories):
            problems.append('Retained story does not belong to its cited scenario scope: ' + key)
        if not set(record.get('source_acceptance_ids', [])).issubset(allowed_ac):
            problems.append('Retained acceptance does not belong to its cited scenario scope: ' + key)
        return problems

    def reviewed_canonical_errors(self, key, record):
        """A candidate is not permanently banned; promotion needs a bound audit.

        This checks a source-attribution review, never new business approval or
        production availability. Placeholder evidence remains inadmissible.
        """
        problems = []
        review = record.get('retained_scope_review', {})
        origins = review.get('origin_candidate_keys', [])
        if not texts(origins, True) or any(origin not in self.candidates for origin in origins):
            return ['Reviewed canonical retained operation needs existing origin_candidate_keys: ' + key]
        if (not text(review.get('reviewed_by')) or not text(review.get('reviewed_on'))
                or review.get('decision') != 'unchanged_original_operation_reference'
                or review.get('verification_scope') != 'source_attribution_only'):
            problems.append('Canonical retained operation lacks an explicit bounded source review: ' + key)
        problems.extend(self.references(review))
        basis = review.get('scope_basis_acceptance_ids', [])
        if (not texts(basis, True) or not set(basis).issubset(record.get('source_acceptance_ids', []))
                or not any(self.acceptance.get(aid, {}).get('decision_status') in {'confirmed_direction', 'confirmed', 'user_confirmed'} for aid in basis)):
            problems.append('Canonical review lacks confirmed existing-scope acceptance basis: ' + key)
        bindings = review.get('candidate_record_sha256', {})
        for origin in origins:
            candidate = self.candidates[origin]
            actual = hashlib.sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if bindings.get(origin) != actual:
                problems.append('Canonical review candidate SHA differs: ' + origin)
            placeholder_keys = {p.get('observation_key') for p in self.facts['features'].get('placeholder_and_unproven_source_controls', [])}
            if (origin in placeholder_keys or 'placeholder' in str(candidate.get('evidence_level', ''))
                    or candidate.get('counts_as_confirmed_existing_usable_operation') is False):
                problems.append('Placeholder cannot become usable retained operation through a review: ' + origin)
            if candidate.get('formal_function_id') is not None or candidate.get('counts_as_version_delta') is not False:
                problems.append('Origin candidate was promoted or mutated instead of preserved: ' + origin)
            if candidate.get('catalog_role') != 'local_candidate_not_scope_or_function_approval':
                problems.append('Origin candidate role must remain unapproved candidate: ' + origin)
        comparisons = review.get('field_comparison', {})
        for field in ('business_object', 'primary_action', 'trigger', 'completion_result'):
            item = comparisons.get(field, {})
            candidate_key = item.get('candidate_key')
            candidate = self.candidates.get(candidate_key, {})
            candidate_field = item.get('candidate_field')
            allowed_fields = {field, 'completion_result_observed_in_source'} if field == 'completion_result' else {field}
            value = None
            pointer = isinstance(candidate_field, str) and candidate_field.startswith('/')
            if pointer:
                try:
                    tokens = pointer_tokens(candidate_field)
                    if (field != 'trigger' or len(tokens) != 3 or tokens[0] != 'source_evidence'
                            or tokens[2] not in {'locator', 'excerpt', 'observed'}):
                        raise JSONPointerError('Only trigger source-evidence locators may use JSON pointers')
                    value = json_pointer_value(candidate, candidate_field)
                except JSONPointerError as exc:
                    problems.append('Invalid canonical source JSON pointer: ' + str(exc))
            elif isinstance(candidate_field, str) and candidate_field in allowed_fields:
                value = candidate.get(candidate_field)
            if (candidate_key not in origins or (not pointer and (not isinstance(candidate_field, str) or candidate_field not in allowed_fields))
                    or not text(value)
                    or item.get('candidate_value') != value
                    or item.get('canonical_value') != record.get(field)
                    or item.get('assessment') != 'same_original_operation'
                    or not text(item.get('rationale'))):
                problems.append('Canonical review lacks concrete object/action/trigger/result comparison: ' + field)
            if field == 'trigger':
                if callable_only_trigger(record.get('trigger')):
                    problems.append('Canonical trigger must describe a business initiation, not a callable name/declaration')
                if pointer or not text(candidate.get('trigger')) or callable_only_trigger(candidate.get('trigger')):
                    problems.extend(self.derived_trigger_errors(candidate_key, candidate, candidate_field, review, record))
        evidence = record.get('local_source_evidence', [])
        if not evidence or any(not text(e.get('source_sha256')) for e in evidence):
            problems.append('Reviewed original-operation evidence requires current source SHA bindings: ' + key)
        inclusion = review.get('conditional_inclusion', {})
        if (inclusion.get('scope') != 'source_existing_only'
                or inclusion.get('placeholder_excluded') is not True
                or inclusion.get('production_verified') is not False
                or inclusion.get('final_baseline_verified') is not False):
            problems.append('Conditional source inclusion must not claim placeholder/production/final-baseline coverage: ' + key)
        pending = inclusion.get('pending_baseline_issue_ids', [])
        if (not texts(pending, True) or any(i not in record.get('issue_ids', []) or self.issues.get(i, {}).get('status') != 'open' for i in pending)):
            problems.append('Unverified baseline remains explicitly linked to its open issues: ' + key)
        return problems

    def original_anchor_context(self, candidate_key, candidate, candidate_field,
                                proof, source, path, actual_sha):
        """Pin an explicit role claim to original bytes; never prove its role.

        The lexical role/readonly/follow-up-save obligations belong to the
        bounded-source validator.  This guard cannot turn metadata into proof.
        """
        contract = proof.get('original_anchor')
        prefix = 'Bounded original-anchor role contract pending: '
        if not isinstance(contract, dict):
            return None, [prefix + 'original_anchor must be an explicit object']
        roles = {'complete_callable_handler', 'initiator_only_needs_save_result',
                 'result_expression_noncallable', 'data_helper_with_callable_alternative'}
        if contract.get('version') != 'bounded-original-anchor-role@1' or not isinstance(contract.get('role'), str) or contract['role'] not in roles:
            return None, [prefix + 'unsupported version or anchor role']
        digest = hashlib.sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest()
        if contract.get('candidate_key') != candidate_key or contract.get('candidate_record_sha256') != digest:
            return None, [prefix + 'role claim does not bind the exact original candidate/SHA']
        pointer = proof.get('candidate_source_pointer')
        if contract.get('source_pointer') != pointer or contract.get('source_field_pointer') != candidate_field:
            return None, [prefix + 'role source/field pointers differ from the original comparison']
        try:
            tokens = pointer_tokens(candidate_field)
            if len(tokens) != 3 or tokens[:2] != pointer_tokens(pointer) or tokens[2] not in {'locator', 'excerpt', 'observed'}:
                raise JSONPointerError('role field must select the same original source locator')
            locator = json_pointer_value(candidate, candidate_field)
        except JSONPointerError as exc:
            return None, [prefix + str(exc)]
        if not text(locator) or contract.get('source_sha256') != actual_sha:
            return None, [prefix + 'original locator/source SHA binding differs']
        symbol = contract.get('implementation_symbol')
        if not isinstance(symbol, str) or not re.fullmatch(JS_IDENTIFIER, symbol) or symbol != proof.get('target_symbol'):
            return None, [prefix + 'implementation must equal the exact bare source-chain target']
        evidence = contract.get('role_evidence')
        if not isinstance(evidence, dict) or not evidence:
            return None, [prefix + 'the role needs explicit lexical/result evidence']
        lines = path.read_text(encoding='utf-8').splitlines()
        first = source.get('line', source.get('line_start'))
        last = source.get('line_end', first)
        if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(lines):
            return None, [prefix + 'original source anchor lacks a valid exact line span']
        quote = '\n'.join(lines[first - 1:last])
        if locator not in quote:
            return None, [prefix + 'the claimed original locator is absent from its original line span']
        context = {'candidate_key': candidate_key, 'candidate_record_sha256': digest,
                   'source_pointer': pointer, 'source_field_pointer': candidate_field,
                   'source_locator': locator, 'source_file': str(path),
                   'source_sha256': actual_sha, 'source_line_start': first,
                   'source_line_end': last, 'source_quote': quote,
                   'implementation_symbol': symbol, 'role': contract['role']}
        return context, []

    def derived_trigger_errors(self, candidate_key, candidate, candidate_field, review, record):
        proof = review.get('derived_trigger_evidence')
        if not isinstance(proof, dict):
            return ['Missing derived_trigger_evidence for a source-locator-derived business trigger']
        problems = self.references(proof)
        if proof.get('candidate_key') != candidate_key:
            problems.append('Derived trigger points to a different origin candidate')
        source_pointer = proof.get('candidate_source_pointer')
        source = None
        try:
            tokens = pointer_tokens(source_pointer)
            if len(tokens) != 2 or tokens[0] != 'source_evidence':
                raise JSONPointerError('Derived trigger must select an original source_evidence item')
            source = json_pointer_value(candidate, source_pointer)
            if not isinstance(source, dict):
                raise JSONPointerError('Derived trigger source must be a JSON object')
            if isinstance(candidate_field, str) and candidate_field.startswith('/') and candidate_field.rsplit('/', 1)[0] != source_pointer:
                problems.append('Derived trigger source differs from the field-comparison source')
        except JSONPointerError as exc:
            problems.append('Invalid derived-trigger source JSON pointer: ' + str(exc))
        if (proof.get('business_trigger') != record.get('trigger')
                or not text(proof.get('rationale')) or callable_only_trigger(proof.get('business_trigger'))):
            problems.append('Derived trigger needs explicit business initiation semantics and rationale')
        allowed_refs = set(candidate.get('source_refs', [])) | set(record.get('source_refs', [])) | set(review.get('source_refs', []))
        if isinstance(source, dict) and source.get('source_id'):
            allowed_refs.add('inputs/source-index.json#' + source['source_id'])
        if not set(proof.get('source_refs', [])).issubset(allowed_refs):
            problems.append('Derived trigger source refs do not belong to its original candidate/source')
        original_path = self.path(source.get('file', source.get('path', ''))) if isinstance(source, dict) else None
        path = self.path(proof.get('file', ''))
        if path is None or original_path is None or path != original_path:
            problems.append('Derived trigger uses a different source file than its candidate evidence')
            return problems
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if proof.get('source_sha256') != actual_sha:
            problems.append('Derived trigger source file SHA differs')
        problems.extend(self.evidence_source_chain_errors(proof, {'source_refs': proof.get('source_refs', [])}))
        lines = path.read_text(encoding='utf-8').splitlines()
        start, end = proof.get('line_start'), proof.get('line_end')
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            problems.append('Derived trigger source line range is invalid')
            return problems
        quote = '\n'.join(lines[start - 1:end])
        if not text(proof.get('source_quote')) or proof['source_quote'] != quote:
            problems.append('Derived trigger source quote does not match exact original lines')
            return problems
        symbol_text = proof.get('target_symbol')
        if 'original_anchor' in proof and proof.get('trigger_kind') != 'source_chain':
            return problems + ['Bounded original-anchor role contract pending: only source_chain may opt in']
        if proof.get('trigger_kind') == 'source_chain':
            if not isinstance(symbol_text, str) or not re.fullmatch(JS_IDENTIFIER, symbol_text):
                return problems + ['Bounded source-chain target needs the original bare implementation symbol']
            try:
                locator = json_pointer_value(candidate, candidate_field) if isinstance(candidate_field, str) and candidate_field.startswith('/') else candidate.get(candidate_field)
            except JSONPointerError:
                locator = None
            target_in_locator = isinstance(locator, str) and re.search(
                r'(?<![A-Za-z0-9_$])' + re.escape(symbol_text) + r'(?![A-Za-z0-9_$])', locator)
            if 'original_anchor' not in proof:
                if not target_in_locator:
                    return problems + ['Bounded source-chain target is not bound to the original candidate locator']
            else:
                context, anchor_errors = self.original_anchor_context(
                    candidate_key, candidate, candidate_field, proof, source, path, actual_sha)
                if anchor_errors:
                    return problems + anchor_errors
                if context['role'] != 'result_expression_noncallable' and not target_in_locator:
                    return problems + ['Bounded source-chain target is not bound to the original candidate locator']
                # Missing/incomplete P4 support stays pending.  In particular,
                # an opener's UI binding cannot count as its later save result.
                import bounded_source_chain
                validate_role = getattr(bounded_source_chain, 'original_anchor_role_errors', None)
                if not callable(validate_role):
                    return problems + ['Bounded original-anchor role contract pending: lexical role validator is unavailable']
                try:
                    anchor_errors = validate_role(self, proof, record, review, path, context)
                except (TypeError, KeyError, ValueError, AttributeError, RecursionError, OSError) as exc:
                    return problems + ['Bounded original-anchor role contract pending: ' + str(exc)]
                if not isinstance(anchor_errors, list) or any(not text(error) for error in anchor_errors):
                    return problems + ['Bounded original-anchor role contract pending: invalid lexical validator result']
                if anchor_errors:
                    return problems + anchor_errors
            from bounded_source_chain import source_chain_errors
            return problems + source_chain_errors(self, proof, record, review, path)
        callee = proof.get('target_callee', symbol_text)
        if not isinstance(symbol_text,str) or not re.fullmatch(JS_CALLEE,symbol_text):
            problems.append('Derived trigger target symbol is not bound to its original locator')
            return problems
        symbol=symbol_text.rsplit('.',1)[-1]
        locator=None
        try:
            locator=json_pointer_value(candidate,candidate_field) if isinstance(candidate_field,str) and candidate_field.startswith('/') else candidate.get(candidate_field)
        except JSONPointerError:pass
        if (not isinstance(locator,str) or not re.search(r'(?<![A-Za-z0-9_$])'+re.escape(symbol)+r'(?![A-Za-z0-9_$])',locator)
                or not isinstance(callee,str) or not re.fullmatch(JS_CALLEE,callee)):
            problems.append('Derived trigger target symbol is not bound to its original locator')
            return problems
        if '.' in callee:
            owner=proof.get('owner_binding_evidence')
            if not isinstance(owner,dict):
                problems.append('Qualified target callee needs an exact registered owner binding; short-name matching is forbidden')
                return problems
            problems.extend(self.references(owner))
            owner_path=self.path(owner.get('file',''))
            if owner_path!=path or owner.get('source_sha256')!=actual_sha:
                problems.append('Owner binding must use the same current original source file/SHA')
                return problems
            problems.extend(self.evidence_source_chain_errors(owner,{'source_refs':owner.get('source_refs',[])}))
            first,last=owner.get('line_start'),owner.get('line_end')
            if type(first) is not int or type(last) is not int or not 1<=first<=last<=len(lines):
                problems.append('Owner binding source line range is invalid')
                return problems
            owner_quote='\n'.join(lines[first-1:last])
            if owner.get('source_quote')!=owner_quote or not direct_owner_binding(owner_quote,callee,symbol):
                problems.append('Owner binding is outside supported direct-assignment/static-object grammar')
                return problems
        elif callee!=symbol:
            problems.append('Bare callee must match the exact original callable')
            return problems
        kind=proof.get('trigger_kind')
        if kind=='ui_control':
            if not supported_ui_binding(quote,callee,path,start,end,'\n'.join(lines)):
                problems.append('Derived trigger lacks a real control binding to the exact original callee in a supported UI sink')
        elif kind=='operation_callsite':
            call=single_call_statement(quote)
            if not call or call[0]!=callee:
                problems.append('Derived trigger contains only a declaration/name, unsupported syntax, or not an invocation of the exact original operation')
        else:
            problems.append('Derived trigger kind must be ui_control or operation_callsite')
        return problems

    def migration(self, page, action=None):
        problems, targets = super().migration(page, action)
        declared = self.facts['pages'].get('retired_source_page_ids', [])
        if page['id'] not in declared:
            problems.append('Historical page must be explicitly declared as retired source: ' + page['id'])
        current = self.facts['pages'].get('current_page_ids')
        for target in targets:
            if current is not None and target not in current:
                problems.append('Historical destination is not a declared current bearing page: ' + target)
            mids = action.get('capability_migration_ids', []) if action else [m for m, rec in self.migrations.items() if rec.get('source_page_id') == page['id']]
            incoming = self.pages[target].get('incoming_capability_migration_ids', [])
            if any(mid not in incoming for mid in mids if target in self.migrations.get(mid, {}).get('target_page_ids', [])):
                problems.append('Historical destination lacks reciprocal migration provenance: ' + target)
        return problems, targets

    def action(self, page, action):
        row = super().action(page, action)
        errors = row['errors']
        for fid in action.get('source_function_ids', []):
            feature = self.features.get(fid, {})
            if (not navigation_transition(feature)
                    and not set(feature.get('source_scenario_ids', [])).intersection(action.get('source_scenario_ids', []))):
                errors.append('FUNC is outside its direct ACTION scenario scope: ' + fid)
        if 'historical_destination_function_refs' in action:
            refs=action['historical_destination_function_refs']
            if page.get('lifecycle_status')!='retired_source' or not texts(refs):
                errors.append('Historical destination FUNC references are provenance only and require a retired source owner')
            elif any(fid not in self.features for fid in refs):
                errors.append('Historical destination references an undefined FUNC')
        if page.get('lifecycle_status') != 'retired_source' and action.get('lifecycle_status') == 'retained_source_action':
            errors.append('Historical source ACTION cannot masquerade as a current business action')
        main_retained = list(action.get('retained_operation_refs', []))
        main_retained.extend(detail.get('parent_ref') for detail in action.get('main_operation_details', [])
                             if detail.get('parent_ref') in self.retained and detail.get('parent_ref') not in main_retained)
        for key in main_retained:
            rec = self.retained.get(key, {})
            if rec and not set(rec.get('source_scenario_ids', [])).intersection(action.get('source_scenario_ids', [])):
                errors.append('Retained operation is outside its ACTION scenario scope: ' + key)
            if rec:
                errors.extend(self.retained_action_binding_errors(page, action, key, rec))
        # Shared navigation may accompany a business operation, but is not its witness.
        navigation_only = []
        for witness in list(row['witnesses']):
            if witness['kind'] != 'confirmed_delta_reference':
                continue
            rec = self.features.get(witness['ref'], {})
            if not navigation_transition(rec):
                continue
            if (page.get('surface_kind') == 'shared_shell'
                    and set(action.get('source_scenario_ids', [])).issubset(rec.get('source_scenario_ids', []))):
                continue
            row['witnesses'].remove(witness)
            navigation_only.append(witness['ref'])
        if navigation_only and not row['witnesses']:
            detail = action.get('navigation_detail_of', {})
            if (detail.get('function_id') in navigation_only
                    and detail.get('kind') == 'navigation_detail'
                    and detail.get('counts_as_business_operation') is False
                    and texts(detail.get('target_page_ids'), True)
                    and all(pid in self.pages and self.pages[pid].get('lifecycle_status') != 'retired_source' for pid in detail['target_page_ids'])
                    and text(detail.get('target_permissions_independent'))
                    and detail.get('operation_key') == self.features[detail['function_id']].get('operation_key')
                    and texts(detail.get('source_scenario_ids'), True)
                    and set(detail['source_scenario_ids']).issubset(action.get('source_scenario_ids', []))
                    and not self.references(detail)):
                row['witnesses'].append({'kind': 'navigation_detail_only', 'ref': detail['function_id']})
            else:
                errors.append('Navigation FUNC alone cannot cover unrelated business scope; explicit navigation-detail attribution required')
        row['status'] = 'invalid' if errors else ('scope_pending' if row['scope_pending_refs'] else 'attributed_reference_only')
        return row

    def run(self):
        report = super().run()
        declared_current = self.facts['pages'].get('current_page_ids')
        declared_retired = self.facts['pages'].get('retired_source_page_ids', [])
        actual_retired = {pid for pid, pg in self.pages.items() if pg.get('lifecycle_status') == 'retired_source'}
        if set(declared_retired) != actual_retired:
            self.error('historical_catalog_mismatch', 'planning/page-index.json', 'Declared retired sources do not match historical page lifecycles')
        if declared_current is not None and set(declared_current) != set(self.pages) - actual_retired:
            self.error('current_catalog_mismatch', 'planning/page-index.json', 'Current pages must exclude historical sources and include every current bearing')
        # Resolve coverage from validated direct ownership, never candidates/prerequisites.
        direct = set()
        retained_coverage = {'source_story_ids': set(), 'source_acceptance_ids': set(), 'source_scenario_ids': set()}
        for action_row in report['action_checks']:
            if action_row['status'] != 'attributed_reference_only' or action_row['historical_only']:
                continue
            page = self.pages[action_row['page_id']]
            action = next(a for a in page.get('actions', []) if a['id'] == action_row['action_id'])
            for witness in action_row['witnesses']:
                if witness['kind'] != 'retained_original_operation_reference':
                    continue
                key = witness['ref'];rec = self.retained[key]
                if self.evidence(key, rec):
                    continue
                binding = next(b for b in action.get('retained_operation_bindings', []) if b.get('operation_ref') == key)
                scopes = (set(rec.get('source_scenario_ids', [])) & set(action.get('source_scenario_ids', []))
                          & set(page.get('scenario_ids', [])) & set(binding.get('source_scenario_ids', [])))
                direct.add(key)
                for sid in scopes:
                    sc = self.scenarios[sid]
                    stories = set(rec.get('source_story_ids', [])) & set(sc.get('source_story_ids', []))
                    retained_coverage['source_scenario_ids'].add(sid)
                    retained_coverage['source_story_ids'].update(stories)
                    retained_coverage['source_acceptance_ids'].update(aid for aid in rec.get('source_acceptance_ids', [])
                        if aid in sc.get('source_acceptance_ids', []) and self.acceptance.get(aid, {}).get('owner_story_id') in stories)
        pending_scenarios = set()
        for action in report['action_checks']:
            if action['status'] != 'attributed_reference_only':
                pg = self.pages[action['page_id']]
                a = next(a for a in pg.get('actions', []) if a['id'] == action['action_id'])
                pending_scenarios.update(a.get('source_scenario_ids', []))
        for rec in self.unresolved.values():
            pending_scenarios.update(rec.get('source_scenario_ids', []))
        report['retained_coverage'] = {k: sorted(v) for k, v in retained_coverage.items()}
        report['validated_retained_keys_with_direct_ownership'] = sorted(direct)
        report['pending_scenario_ids'] = sorted(pending_scenarios)
        report['current_page_ids'] = sorted(set(self.pages) - actual_retired)
        report['historical_source_page_ids'] = sorted(actual_retired)
        report['coverage_boundary'] = 'references_only_not_full_acceptance_or_production_completion; unresolved ownership and ordinary issue gates remain blocking'
        return report


def attribution_report(root: Path, *, validate_feature_bodies=True) -> dict[str, Any]:
    root = root.resolve()
    optional = {'sources': {'sources': []}, 'issues': {'items': []}}
    facts = {}
    for name, rel in FILES.items():
        path = root / rel
        facts[name] = json.loads(path.read_text(encoding='utf-8')) if path.is_file() or name not in optional else optional[name]
    return StrictReview(root, facts, validate_feature_bodies=validate_feature_bodies).run()


def attribution_errors(root: Path, *, validate_feature_bodies=True) -> list[str]:
    report = attribution_report(root, validate_feature_bodies=validate_feature_bodies)
    errors = [f"{e['target']} 归属无效：{e['message']}" for e in report['attribution_errors']]
    for row in report['action_checks']:
        if row['status'] == 'scope_pending':
            errors.append(f"{row['action_id']} 原操作归属仍待核：{','.join(row['scope_pending_refs'])}")
    for row in report['page_checks']:
        if row['status'] == 'scope_pending':
            errors.append(f"{row['page_id']} 原操作归属仍待核；不能以导航或部分引用替代整页承接")
    facts = json.loads((root / 'planning/page-index.json').read_text(encoding='utf-8'))
    bound_pending = {key for page in facts['pages'] for owner in [page, *page.get('actions', [])] for key in owner.get('unresolved_operation_refs', [])}
    features = json.loads((root / 'memory/feature-list.json').read_text(encoding='utf-8'))
    for boundary in features.get('unresolved_operation_boundaries', []):
        if boundary['draft_key'] not in bound_pending:
            errors.append(f"{boundary['draft_key']} 尚有未挂UI的真实未决操作/责任边界，不得因没有按钮而遗漏")
    return errors


def scenario_coverage_errors(root: Path, feature_scenarios: set[str], required: set[str]) -> list[str]:
    report = attribution_report(root)
    retained = set(report['retained_coverage']['source_scenario_ids'])
    errors = []
    for sid in sorted(required - feature_scenarios):
        if sid not in retained:
            errors.append(f'{sid} 没有对应本期功能或有有效来源及操作归属的存量引用')
        elif sid in report['pending_scenario_ids']:
            errors.append(f'{sid} 原功能仅部分归属，尚有真实未决操作，不能宣称完整承接')
    return errors
