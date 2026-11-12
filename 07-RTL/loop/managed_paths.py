"""Project metadata that must stay outside the delivered prototype tree."""

from pathlib import Path

from .state import LoopError, overlaps


MANAGED_PROJECT_PATHS = (
    "docs/change-log.md",
    "docs/design-style.md",
    "docs/feature-map.md",
    "docs/需求台账.md",
    "memory/baselines",
    "memory/changes",
    "memory/contracts",
    "memory/project.md",
    "memory/requirements-history.json",
    "memory/requirements-revisions.json",
)


def check_output_path(project, output):
    project = Path(project).resolve()
    output = Path(output).resolve()
    for relative in MANAGED_PROJECT_PATHS:
        managed = project / relative
        if overlaps(output, managed) or overlaps(output, managed.resolve()):
            raise LoopError(f"原型落盘目录不能与 RTL 管理路径重叠：{output}（{relative}）")
