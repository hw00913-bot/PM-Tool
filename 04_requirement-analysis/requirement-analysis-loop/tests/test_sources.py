"""Explicit discovery of the prototype consumer for requirement integration tests.

Copy this support module into the requirement-analysis tests directory. Source
and installed layouts are intentionally distinct; isolated runs configure the
consumer explicitly and never fall back after a bad explicit path.
"""
from __future__ import annotations

import os
from pathlib import Path


def prototype_source_root(analysis_root: Path | None = None) -> Path:
    analysis_root = Path(analysis_root or Path(__file__).resolve().parents[1]).resolve()
    configured = os.environ.get("PROTOTYPE_SOURCE_ROOT")
    if configured is not None:
        if not configured.strip():
            raise ValueError("PROTOTYPE_SOURCE_ROOT is set but empty; provide the prototype source or installed skill root")
        candidate = Path(configured).expanduser().resolve()
        origin = "PROTOTYPE_SOURCE_ROOT"
    elif analysis_root.parent.name == "04_requirement-analysis":
        canonical = analysis_root.parent.parent / "02_LOOP_PROTOTYPE"
        transition = analysis_root.parent / "prototype-loop-orchestrator"
        # Prefer the complete physical 02 owner after migration. The old 04
        # consumer is allowed only as a complete transition implementation.
        closure = ("SKILL.md", "VERSION_WORKFLOW.md",
                   "orchestrator/scripts/import_analysis.py",
                   "orchestrator/scripts/project_versions.py",
                   "skill-library/loop-project-scaffolder/scripts/create_project.py")
        candidate = canonical if all((canonical / name).is_file() for name in closure) else transition
        origin = "physical 02 source" if candidate == canonical else "04 transition source"
    elif analysis_root.parent.name == "skills":
        candidate = analysis_root.parent / "prototype-loop-orchestrator"
        origin = "installed skills layout"
    else:
        raise FileNotFoundError(
            f"Cannot discover prototype consumer from {analysis_root}; "
            "set PROTOTYPE_SOURCE_ROOT for isolated or custom layouts"
        )
    required = (
        "SKILL.md",
        "VERSION_WORKFLOW.md",
        "orchestrator/scripts/import_analysis.py",
        "orchestrator/scripts/project_versions.py",
        "skill-library/loop-project-scaffolder/scripts/create_project.py",
    )
    missing = [relative for relative in required if not (candidate / relative).is_file()]
    if missing:
        raise FileNotFoundError(
            f"Prototype consumer from {origin} is incomplete at {candidate}: {', '.join(missing)}; "
            "integration checks must not silently skip a missing consumer"
        )
    return candidate


def prototype_script(relative: str, analysis_root: Path | None = None) -> Path:
    script = prototype_source_root(analysis_root) / relative
    if not script.is_file():
        raise FileNotFoundError(f"Required prototype consumer script is missing: {script}")
    return script
