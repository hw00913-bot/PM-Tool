"""Read-only retained-operation evidence and ownership parser.

Used by the shared attribution contract. It records provenance and unresolved
boundaries, never approves stages, risks, production capability or G3. There is
no project-specific diagnostic adapter or writing CLI in this module.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from evidence_paths import EvidencePathError, EvidencePathResolver

DEFAULT_SKILL = Path(__file__).resolve().parent.parent
FILES = {
    'features': 'memory/feature-list.json', 'pages': 'planning/page-index.json',
    'stories': 'memory/normalized-user-stories.json',
    'scenarios': 'scenarios/scenario-index.json',
    'sources': 'inputs/source-index.json', 'issues': 'runtime/open-items.json',
}
READ_ACTIONS = {'查询', '查看', '下载', '导出', '核对', '读取'}


def text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def texts(value: Any, required: bool = False) -> bool:
    return isinstance(value, list) and (bool(value) or not required) and all(text(v) for v in value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def navigation_transition(record: dict[str, Any]) -> bool:
    # The navigation operation protects entry/leaving;
    # it never lends the destination's business permissions to its caller.
    return (record.get('operation_key') == 'navigate-authorized-location'
            and record.get('boundary', {}).get('business_object') == '当前工作位置')


class Review:
    def __init__(self, root: Path, facts: dict[str, Any]):
        self.root = root.resolve()
        self.facts = facts
        self.errors: list[dict[str, Any]] = []
        self.evidence_paths = EvidencePathResolver(self.root)
        self._path_errors = set()
        self.pending: list[dict[str, Any]] = []
        self.evidence_checks: dict[str, list[str]] = {}
        self.source_checks: dict[str, list[str]] = {}
        self.sources = self.index(facts['sources'].get('sources'), 'id', 'source')
        self.stories = self.index(facts['stories'].get('user_stories'), 'id', 'story')
        self.acceptance: dict[str, dict[str, Any]] = {}
        for story in self.stories.values():
            for ac in story.get('acceptance_criteria', []):
                if isinstance(ac, dict):
                    aid = ac.get('id')
                    if aid in self.acceptance:
                        self.error('duplicate_acceptance', str(aid), 'AC ID duplicated')
                    self.acceptance[aid] = dict(ac, owner_story_id=story['id'])
        self.scenarios = self.index(facts['scenarios'].get('scenarios'), 'id', 'scenario')
        self.features = self.index(facts['features'].get('features'), 'id', 'function')
        self.retained = self.index(facts['features'].get('retained_capability_references', []), 'draft_key', 'retained')
        self.unresolved = self.index(facts['features'].get('unresolved_operation_boundaries', []), 'draft_key', 'unresolved')
        self.candidates = self.index(facts['features'].get('observed_original_operation_candidates', []), 'catalog_key', 'candidate', 'observation_key')
        detail_rows = [dict(x, _detail_id=x.get('draft_key') or x.get('observation_key') or x.get('key')) if isinstance(x, dict) else x
                       for x in facts['features'].get('original_operation_details_and_auxiliary_fragments', [])]
        self.details = self.index(detail_rows, '_detail_id', 'detail')
        self.pages = self.index(facts['pages'].get('pages'), 'id', 'page')
        self.migrations = self.index(facts['pages'].get('capability_migrations', []), 'migration_id', 'migration')
        self.issues = self.index(facts['issues'].get('items', []), 'id', 'issue')
        self.action_owner: dict[str, str] = {}
        for pid, page in self.pages.items():
            for action in page.get('actions', []):
                aid = action.get('id') if isinstance(action, dict) else None
                if not text(aid) or aid in self.action_owner:
                    self.error('invalid_or_duplicate_action', str(aid), 'Every action needs a unique ID')
                else:
                    self.action_owner[aid] = pid

    def index(self, rows: Any, key: str, label: str, fallback: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if not isinstance(rows, list):
            self.error('invalid_catalog', label, 'Expected an array')
            return result
        for row in rows:
            rid = row.get(key, row.get(fallback)) if isinstance(row, dict) else None
            if not text(rid) or rid in result:
                self.error('invalid_or_duplicate_key', label, str(rid))
            else:
                result[rid] = row
        return result

    def error(self, code: str, target: str, message: str) -> None:
        self.errors.append({'code': code, 'target': target, 'message': message})

    def path(self, value: str, *, internal: bool = False, must_exist: bool = True) -> Path | None:
        try:
            return self.evidence_paths.resolve(value, internal=internal, must_exist=must_exist)
        except EvidencePathError as exc:
            problem = str(exc)
            if problem not in self._path_errors:
                self._path_errors.add(problem)
                self.error('invalid_evidence_relocation', 'path-relocations', problem)
            return None

    def source(self, sid: str) -> list[str]:
        if sid in self.source_checks:
            return self.source_checks[sid]
        problems = []
        record = self.sources.get(sid)
        if not record:
            problems.append('Unregistered source ID: ' + str(sid))
        else:
            if record.get('status') not in {'verified', 'partial'}:
                problems.append('Source is missing/conflicting/unverified: ' + sid)
            p = self.path(record.get('snapshot_path', ''), internal=True)
            if p is None:
                problems.append('Registered local snapshot missing/outside project: ' + sid)
            elif text(record.get('sha256')) and sha(p) != record['sha256']:
                problems.append('Registered source snapshot SHA differs: ' + sid)
        self.source_checks[sid] = problems
        return problems

    def references(self, record: dict[str, Any]) -> list[str]:
        refs = record.get('source_refs')
        if not texts(refs, True):
            return ['Missing source_refs']
        problems = []
        for ref in refs:
            match = re.fullmatch(r'inputs/source-index\.json#(SRC-\d+)', ref)
            if match:
                problems.extend(self.source(match[1]))
            elif self.path(ref, internal=True) is None:
                problems.append('Unresolved local evidence reference: ' + ref)
        return problems

    def upstream(self, record: dict[str, Any]) -> list[str]:
        problems = []
        for field, catalog in (('source_story_ids', self.stories), ('source_acceptance_ids', self.acceptance), ('source_scenario_ids', self.scenarios)):
            refs = record.get(field)
            if not texts(refs, True) or any(ref not in catalog for ref in refs):
                problems.append('Invalid/missing ' + field)
        stories = record.get('source_story_ids', [])
        for aid in record.get('source_acceptance_ids', []):
            if aid in self.acceptance and self.acceptance[aid]['owner_story_id'] not in stories:
                problems.append('AC is not owned by referenced story: ' + aid)
        for sid in record.get('source_scenario_ids', []):
            if sid in self.scenarios and self.scenarios[sid].get('scope', 'in_scope') != 'in_scope':
                problems.append('Scenario is out of scope: ' + sid)
        return problems

    def evidence(self, key: str, record: dict[str, Any]) -> list[str]:
        if key in self.evidence_checks:
            return self.evidence_checks[key]
        problems = self.references(record) + self.upstream(record)
        for field in ('business_object', 'primary_action', 'trigger', 'completion_result'):
            if not text(record.get(field)):
                problems.append('Missing concrete operation field: ' + field)
        if record.get('delivery_class') != 'retained_reference_not_delta' or record.get('formal_function_id') is not None:
            problems.append('Retained reference must not masquerade as a new/iteration FUNC')
        if record.get('change_type') in {'new', 'iteration'}:
            problems.append('Unchanged operation incorrectly counted as version delta')
        if record.get('catalog_role') == 'local_candidate_not_scope_or_function_approval' or 'candidate' in str(record.get('s7_treatment', '')):
            problems.append('Candidate cannot be promoted by reference alone')
        observations = record.get('local_source_evidence')
        if not isinstance(observations, list) or not observations:
            problems.append('No local original-operation evidence')
        else:
            for observation in observations:
                if not isinstance(observation, dict):
                    problems.append('Evidence entry must be an object')
                    continue
                if observation.get('source_id'):
                    problems.extend(self.source(observation['source_id']))
                p = self.path(observation.get('file', observation.get('path', '')))
                if p is None:
                    problems.append('Original operation source missing')
                    continue
                if text(observation.get('source_sha256')) and sha(p) != observation['source_sha256']:
                    problems.append('Original operation source SHA differs: ' + str(p))
                start = observation.get('line_start', observation.get('line'))
                end = observation.get('line_end', start)
                if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(p.read_text(encoding='utf-8').splitlines()):
                    problems.append('Evidence line range invalid: ' + str(p))
                if not text(observation.get('observed', observation.get('excerpt'))):
                    problems.append('Evidence has no observed operation/result')
        self.evidence_checks[key] = problems
        return problems

    def feature(self, fid: str) -> list[str]:
        record = self.features.get(fid)
        if not record:
            return ['Undefined FUNC: ' + fid]
        problems = self.upstream(record) + self.references(record)
        if record.get('evidence_level') not in {'confirmed_business_direction', 'actual_user_confirmed_business_scope_implementation_unverified', 'confirmed_business_scope'}:
            problems.append('FUNC lacks confirmed business-scope evidence')
        if record.get('counts_as_confirmed_delta') is False or record.get('status') in {'candidate', 'placeholder', 'unknown'}:
            problems.append('FUNC is only a candidate/placeholder')
        if not any(self.acceptance.get(aid, {}).get('decision_status') in {'confirmed_direction', 'confirmed', 'user_confirmed', 'derived_constraint'} for aid in record.get('source_acceptance_ids', [])):
            problems.append('FUNC has only candidate/unknown upstream AC')
        return problems

    def migration(self, page: dict[str, Any], action: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
        """Return current fragment destinations; never inherit target action rights."""
        pid = page['id']
        mids = action.get('capability_migration_ids', []) if action else [mid for mid, m in self.migrations.items() if m.get('source_page_id') == pid]
        problems, destinations = [], set()
        if not texts(mids, True):
            return ['Historical record has no explicit capability migration'], []
        for mid in mids:
            m = self.migrations.get(mid)
            if not m or m.get('source_page_id') != pid:
                problems.append('Undefined or wrong-source migration: ' + mid)
                continue
            problems.extend(self.references(m))
            targets = m.get('target_page_ids')
            policy = m.get('permission_policy', {})
            if not texts(targets, True) or any(t not in self.pages or self.pages[t].get('lifecycle_status') == 'retired_source' for t in targets):
                problems.append('Migration has no valid current destination: ' + mid)
                continue
            if not text(policy.get('source_view_right')) or not text(policy.get('target_other_capabilities')) or not text(m.get('source_fragment')):
                problems.append('Migration lacks fragment/independent-permission boundary: ' + mid)
            if any(policy.get(flag) is True or m.get(flag) is True for flag in ('grant_target_permissions', 'grants_target_permissions', 'grant_all', 'inherits_write_permission')):
                problems.append('Migration cannot grant target details/write rights: ' + mid)
            for target in targets:
                auth = self.pages[target].get('fragment_authorization', {})
                if auth.get('source_scope_preserved') is not True or not text(auth.get('rule')):
                    # A whole-operation merge may independently map the SAME
                    # operation FUNC and its target action permission. This is
                    # not a shortcut from summary rights to target CRUD rights.
                    source_actions = [action] if action else page.get('actions', [])
                    source_funcs = {fid for a in source_actions for fid in a.get('source_function_ids', [])
                                    if not navigation_transition(self.features.get(fid, {}))}
                    guarded_funcs = {fid for a in self.pages[target].get('actions', [])
                                     if text(a.get('permission')) for fid in a.get('source_function_ids', [])}
                    if not source_funcs or not source_funcs.issubset(guarded_funcs):
                        problems.append('Target lacks fragment guard or independently permissioned SAME operation: ' + target)
            destinations.update(targets)
        if action and set(action.get('current_target_page_ids', [])) != destinations:
            problems.append('Historical action destinations disagree with migrations')
        if page.get('menu_path') or page.get('is_current_bearing') is not False:
            problems.append('Historical record still acts as a current/menu page')
        return problems, sorted(destinations)

    def action(self, page: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        aid, pid = action['id'], page['id']
        problems: list[str] = []
        witnesses: list[dict[str, str]] = []
        pending_refs: list[str] = []
        scope = action.get('source_scenario_ids', [])
        if not texts(scope, True) or not set(scope).issubset(page.get('scenario_ids', [])):
            problems.append('Action lacks valid page-scope SC attribution')
        # Independent original-operation reuse needs its own permission
        # boundary. Ordinary FUNC-only projects keep the public schema.
        reuses_original = bool(action.get('retained_operation_refs') or action.get('prerequisite_retained_operation_refs')
                               or any(detail.get('parent_ref') in self.retained for detail in action.get('main_operation_details', [])))
        if reuses_original and not text(action.get('permission')):
            problems.append('Action lacks its own operation permission boundary')
        for field in ('source_function_ids', 'retained_operation_refs', 'prerequisite_retained_operation_refs', 'unresolved_operation_refs'):
            if not texts(action.get(field, [])):
                problems.append('Invalid reference array: ' + field)
        for fid in action.get('source_function_ids', []):
            problems.extend(self.feature(fid))
            if fid not in page.get('function_ids', []):
                problems.append('Action FUNC is not mapped on its owner page: ' + fid)
            f = self.features.get(fid, {})
            if not navigation_transition(f) and not set(f.get('source_scenario_ids', [])).intersection(page.get('scenario_ids', [])):
                problems.append('FUNC is outside owner page business scope: ' + fid)
            if action.get('business_object') and action['business_object'] != f.get('boundary', {}).get('business_object') and not action.get('main_operation_details'):
                problems.append('Action/FUNC business objects disagree: ' + fid)
            witnesses.append({'kind': 'confirmed_delta_reference', 'ref': fid})
        for conditional in action.get('conditional_function_mappings', []):
            fid = conditional.get('function_id')
            if fid not in action.get('source_function_ids', []) or not text(conditional.get('condition')) or not texts(conditional.get('excluded_cases'), True):
                problems.append('Conditional backend reference lacks function/condition/exclusions')
            if fid in self.features and conditional.get('operation_key') != self.features[fid].get('operation_key'):
                problems.append('Conditional mapping points to a different operation key')
        primary_retained = action.get('retained_operation_refs', [])
        prerequisites = action.get('prerequisite_retained_operation_refs', [])
        if set(primary_retained).intersection(prerequisites):
            problems.append('Same retained reference cannot be both main coverage and prerequisite')
        for key in primary_retained + prerequisites:
            record = self.retained.get(key)
            if record is None:
                problems.append(('Candidate/placeholder is not retained ownership: ' if key in self.candidates or key in self.details else 'Undefined retained key: ') + key)
                continue
            problems.extend(self.evidence(key, record))
            if key in prerequisites:
                if aid not in record.get('prerequisite_source_action_ids', []):
                    problems.append('Retained prerequisite is attached to an unrelated prerequisite action: ' + key)
                boundaries = [item for item in action.get('retained_prerequisite_details', [])
                              if isinstance(item, dict) and item.get('operation_ref') == key]
                if (len(boundaries) != 1
                        or any(not text(boundaries[0].get(field)) for field in ('purpose', 'scope_rule', 'permission'))
                        or boundaries[0].get('target_scenario_id') not in scope
                        or boundaries[0].get('counts_as_main_operation_coverage') is not False):
                    problems.append('Retained prerequisite lacks explicit source scope/independent permission/non-coverage boundary: ' + key)
            elif aid not in record.get('source_action_ids', []):
                # prerequisite_source_action_ids deliberately cannot satisfy
                # this primary-operation relation.
                problems.append('Retained reference is attached to an unrelated action: ' + key)
            # Reuse across scenarios is allowed. Reciprocal ACTION attribution
            # and the owning PAGE's real scope establish the relationship;
            # merely sharing a broad SC would not prove same-object ownership.
            if not set(record.get('source_scenario_ids', [])).issubset(page.get('scenario_ids', [])):
                problems.append('Retained operation is outside owner page scenario scope: ' + key)
            historical = page.get('lifecycle_status') == 'retired_source'
            if not historical and pid not in record.get('current_bearing_page_ids', []):
                problems.append('Retained operation belongs to a different page/object: ' + key)
            if action.get('business_object') and action['business_object'] != record.get('business_object'):
                if key not in prerequisites:
                    problems.append('Action/retained business objects disagree: ' + key)
            permission = str(action.get('permission', ''))
            if record.get('primary_action') not in READ_ACTIONS and ('只读' in permission or 'read-only' in permission or '仅查看' in permission):
                problems.append('Write operation cannot be authorized by read-only/summary permission: ' + key)
            if key in prerequisites:
                if record.get('primary_action') not in READ_ACTIONS:
                    problems.append('A prerequisite source-view reference cannot authorize a write: ' + key)
            else:
                witnesses.append({'kind': 'retained_original_operation_reference', 'ref': key})
        for key in action.get('unresolved_operation_refs', []):
            if key not in self.unresolved:
                problems.append('Undefined unresolved boundary: ' + key)
            pending_refs.append(key)
        for key in action.get('local_candidate_observation_refs', []):
            if key not in self.candidates and key not in self.details and not any(x.get('observation_key') == key for x in self.facts['features'].get('placeholder_and_unproven_source_controls', [])):
                problems.append('Undefined source candidate/placeholder: ' + key)
        # Optional explicit necessary-detail contract. Merely naming a parent is
        # insufficient: same object, trigger, result and an existing witness are required.
        for detail in action.get('main_operation_details', []):
            ref = detail.get('parent_ref')
            parent = self.retained.get(ref) or self.features.get(ref, {}).get('boundary')
            if not parent or not detail.get('necessary_same_transaction') is True or not text(detail.get('rationale')):
                problems.append('Necessary detail lacks a real same-transaction parent')
                continue
            if any(detail.get(k) != parent.get(k) for k in ('business_object', 'trigger', 'completion_result')):
                problems.append('Detail changes parent object/trigger/result')
            if ref in self.retained:
                problems.extend(self.evidence(ref, self.retained[ref]))
                if aid not in self.retained[ref].get('source_action_ids', []):
                    problems.append('Detail parent is unrelated to action')
            else:
                problems.extend(self.feature(ref))
                if ref not in page.get('function_ids', []):
                    problems.append('Detail FUNC parent not on owner page')
            witnesses.append({'kind': 'necessary_main_operation_detail', 'ref': str(ref)})
        historical = page.get('lifecycle_status') == 'retired_source'
        if historical:
            migration_errors, destinations = self.migration(page, action)
            problems.extend(migration_errors)
            witnesses.append({'kind': 'historical_fragment_migration_only', 'ref': ','.join(destinations)})
        if not witnesses:
            if action.get('local_candidate_observation_refs'):
                pending_refs.append('candidate_evidence_without_confirmed_ownership')
            if not pending_refs:
                problems.append('Empty/unowned action; source_function_ids and valid retained_operation_refs provide no operation witness')
        status = 'invalid' if problems else ('scope_pending' if pending_refs else 'attributed_reference_only')
        return {'action_id': aid, 'page_id': pid, 'status': status, 'witnesses': witnesses,
                'prerequisite_refs_checked_not_main_coverage': prerequisites,
                'scope_pending_refs': pending_refs, 'errors': problems,
                'historical_only': historical, 'grants_any_permission': False,
                'production_or_delivery_complete': False}

    def run(self) -> dict[str, Any]:
        # Validate every definition, not only those accidentally referenced today.
        for fid, record in self.features.items():
            for problem in self.feature(fid):
                self.error('invalid_function_attribution', fid, problem)
            if record.get('classification_pending') or record.get('contract_verification_status') == 'open':
                self.pending.append({'target': fid, 'kind': 'delta_classification_or_contract_pending', 'issue_ids': record.get('open_issue_ids', [])})
            if not any(self.acceptance.get(aid, {}).get('decision_status') in {'confirmed_direction', 'confirmed', 'user_confirmed'} for aid in record.get('source_acceptance_ids', [])):
                self.pending.append({'target': fid, 'kind': 'only_derived_upstream_acceptance_needs_scope_review', 'issue_ids': record.get('open_issue_ids', [])})
        for key, record in self.retained.items():
            for problem in self.evidence(key, record):
                self.error('invalid_retained_evidence', key, problem)
            if record.get('production_verified') is not True:
                self.pending.append({'target': key, 'kind': 'original_baseline_or_production_unverified', 'issue_ids': record.get('issue_ids', [])})
        actions, pages = [], []
        for pid, page in self.pages.items():
            owned = [self.action(page, action) for action in page.get('actions', [])]
            actions.extend(owned)
            problems = []
            for fid in page.get('function_ids', []):
                problems.extend(self.feature(fid))
                f = self.features.get(fid, {})
                if not navigation_transition(f) and not set(f.get('source_scenario_ids', [])).intersection(page.get('scenario_ids', [])):
                    problems.append('Page borrows an unrelated-scope FUNC: ' + fid)
            for key in page.get('retained_operation_refs', []):
                if key not in self.retained:
                    problems.append('Undefined retained page key: ' + key)
                else:
                    problems.extend(self.evidence(key, self.retained[key]))
                    if page.get('lifecycle_status') != 'retired_source' and pid not in self.retained[key].get('current_bearing_page_ids', []):
                        problems.append('Page borrows a different operation/object: ' + key)
            for key in page.get('unresolved_operation_refs', []):
                if key not in self.unresolved:
                    problems.append('Undefined unresolved page boundary: ' + key)
            historical = page.get('lifecycle_status') == 'retired_source'
            if historical:
                migration_errors, _ = self.migration(page)
                problems.extend(migration_errors)
            has_witness = bool(page.get('function_ids') or page.get('retained_operation_refs') or owned)
            if not has_witness and not historical:
                problems.append('Page has no operation attribution through function_ids or valid retained_operation_refs')
            invalid = problems or any(a['status'] == 'invalid' for a in owned)
            scope_pending = page.get('unresolved_operation_refs') or any(a['status'] == 'scope_pending' for a in owned)
            pages.append({'page_id': pid, 'status': 'invalid' if invalid else ('scope_pending' if scope_pending else 'attributed_reference_only'),
                          'historical_only': historical, 'errors': problems, 'grants_any_permission': False,
                          'coverage_means_entire_target_page_authorized': False})
        for row in actions:
            for problem in row['errors']:
                self.error('invalid_action_ownership', row['action_id'], problem)
        for row in pages:
            for problem in row['errors']:
                self.error('invalid_page_ownership', row['page_id'], problem)
        for key, record in self.unresolved.items():
            self.pending.append({'target': key, 'kind': 'business_scope_or_delivery_boundary_pending', 'issue_ids': record.get('issue_ids', [])})
        for issue in self.issues.values():
            if issue.get('status') == 'open':
                self.pending.append({'target': issue['id'], 'kind': 'open_contract_or_design_issue', 'severity': issue.get('severity')})
        return {'action_checks': actions, 'page_checks': pages, 'attribution_errors': self.errors,
                'business_scope_or_contract_pending': self.pending,
                'counts': {'action_statuses': dict(Counter(a['status'] for a in actions)),
                           'page_statuses': dict(Counter(p['status'] for p in pages)),
                           'retained_definitions': len(self.retained), 'delta_functions': len(self.features)}}


