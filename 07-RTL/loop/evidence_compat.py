"""Interpret historical evidence against its original run's frozen basis."""

from collections.abc import Iterable
from typing import cast

from .payload_types import AcceptanceContext, Baseline, EvidenceContext, InputRevisions


INPUT_REVISIONS = ("fixture_revision", "suite_revision")


def browser_family(browser: object) -> str | None:
    """Return the engine label without its observed version."""
    if not isinstance(browser, str) or not browser.strip():
        return None
    return browser.split(maxsplit=1)[0].casefold()


def inputs_match(baseline: Baseline, context: EvidenceContext, cases: Iterable[str]) -> bool:
    if any(field in baseline and baseline[field] != context.get(field) for field in INPUT_REVISIONS):
        return False
    digests = baseline.get("suite_digests")
    if digests is None:
        return True
    digest = context.get("suite_digest")
    if not isinstance(digest, str) or digest not in digests or set(cases) != set(digests[digest]):
        return False
    catalog = baseline.get("suite_catalog")
    # Older digest-bound runs froze the file and cases without an engine catalogue.
    return catalog is None or (
        digest in catalog and browser_family(context.get("browser")) == catalog[digest]["engine"]
    )


def requires_report(baseline: Baseline) -> bool:
    return "suite_digests" in baseline


def delivery_revisions(
    baseline: Baseline, pinned: EvidenceContext | AcceptanceContext,
) -> InputRevisions:
    return {
        "suite_revision": baseline.get("suite_revision", pinned["suite_revision"]),
        "fixture_revision": baseline.get("fixture_revision", pinned["fixture_revision"]),
    }


def delivery_browsers(
    baseline: Baseline, pinned: EvidenceContext | AcceptanceContext, observed: EvidenceContext,
) -> list[str | None]:
    approved = baseline.get("acceptance_browsers") or pinned.get("browser_families")
    if not approved:
        return [browser_family(pinned.get("browser") or observed["browser"])]
    return cast(list[str | None], approved)
