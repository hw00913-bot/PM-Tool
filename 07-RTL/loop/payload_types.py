"""Static shapes for ledger JSON and the records that carry it."""

from typing import Any, Literal, NotRequired, TypedDict


class FrozenSuite(TypedDict):
    engine: str
    cases: dict[str, list[dict[str, str]]]


class RequirementsPlan(TypedDict):
    approval: str
    requirements: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    features: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    changes: list[dict[str, Any]]


class Baseline(TypedDict, total=False):
    """Stored baseline. Historical runs may predate any individual field."""

    revision: str
    frontend: Literal["static", "react"]
    mandatory_cases: list[str]
    suite_revision: str
    fixture_revision: str
    suite_digests: dict[str, list[str]]
    suite_catalog: dict[str, FrozenSuite]
    acceptance_browsers: list[str]
    human_acceptance: bool
    requirements: RequirementsPlan
    s1_review: dict[str, Any]
    analysis_origin: dict[str, Any]
    analysis_mapping: dict[str, Any]
    analysis_lineage: dict[str, Any]
    s1_handoff_checks: bool
    assertion_checkpoint_checks: bool
    change_assertion_checks: bool
    change_proof_checks: bool
    feature_entry_checks: bool
    feature_route_checks: bool
    normative_change_checks: bool
    normative_changes: dict[str, dict[str, Any]]


class RunRecord(TypedDict):
    id: str
    project_id: str
    project_root: str
    output_path: str
    status: Literal["active", "delivered", "superseded"]
    epoch: int
    candidate: str | None
    baseline: Baseline
    created_at: str


class OperationRecord(TypedDict):
    id: str
    run_id: str
    kind: str
    status: str
    payload: dict[str, Any]
    receipt: dict[str, Any] | None


class ContractParty(TypedDict):
    task: str
    commit: str


class FeatureRouteProof(TypedDict):
    map_sha256: str
    routes: dict[str, dict[str, Any]]
    entry_check: NotRequired[dict[str, Any]]


class EvidenceContext(TypedDict, total=False):
    fixture_revision: str
    suite_revision: str
    suite_digest: str
    browser: str
    browser_families: list[str | None]
    path: str
    entry: str
    url: str
    protocol: str
    asset_digest: str
    operation_id: str
    attempt_id: str
    evidence: str
    evidence_sha256: str
    report_path: str
    report_digest: str
    report_sha256: str
    case_fingerprints: dict[str, str]
    feature_routes: FeatureRouteProof
    edge: str
    version: str
    provider: ContractParty
    consumer: ContractParty


class CaseResult(TypedDict, total=False):
    passed: bool
    status: Literal["pass", "fail", "blocked", "not_run", "not-run"]
    actual: str
    executed: list[str]
    errors: list[str]
    assertions: list[dict[str, str]]
    step_locations: list[dict[str, Any]]


CaseResults = dict[str, bool | CaseResult]


class EvidenceRecord(TypedDict):
    id: str
    run_id: str
    kind: str
    target: str
    actor: str
    cases: CaseResults
    context: EvidenceContext


class InputRevisions(TypedDict):
    suite_revision: str
    fixture_revision: str


class AcceptanceInputs(InputRevisions):
    operation_id: str
    path: str
    url: str


class AcceptanceContext(AcceptanceInputs):
    browser_families: list[str | None]
    browser: NotRequired[str]
    feature_map_sha256: NotRequired[str]


class BuildReceipt(TypedDict):
    source_commit: str
    lockfile_sha256: str
    artifact_manifest_sha256: str


class LandingIntent(TypedDict):
    operation_id: str
    candidate: str
    output: str
    manifest: dict[str, str]
    previous_manifest: dict[str, str] | None
    staging: str
    previous: str
    build: NotRequired[BuildReceipt]


class LandingReceipt(TypedDict):
    operation_id: str
    candidate: str
    output: str
    entry: str
    url: str
    status: Literal["pending_verification"]
    build: NotRequired[BuildReceipt]


class AcceptanceReceipt(TypedDict):
    operation_id: str
    candidate: str
    entry: str
    actor: str
    approval: str | None
    evidence_ids: list[str]
    status: Literal["delivered"]
    feature_map_sha256: NotRequired[str]
    acceptance_browsers: NotRequired[list[str | None]]
    build: NotRequired[BuildReceipt]
    source: NotRequired[str]


class DeliveryReceipt(AcceptanceReceipt):
    change_id: str


class AcceptanceIntent(TypedDict):
    receipt: AcceptanceReceipt
    context: AcceptanceContext
    previous_evidence_ids: NotRequired[list[list[str]]]
