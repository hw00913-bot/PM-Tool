"""Resolve the maintained local Skill and its applicable references for each invocation."""

import hashlib
import json
import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills"
ROLES = {"master", "architect", "scene", "integration", "verifier"}
STAGES = {f"S{index}" for index in range(5)}
REQUIREMENT_STEPS = {"analysis", "scenes", "features", "tasks"}
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RULE_KEYS = {"ref", "stages", "roles", "steps", "purposes", "requires_edges"}
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _rule_names(rule, field, label, allowed=None):
    values = rule[field]
    if not isinstance(values, list) or not values or any(
        not isinstance(value, str)
        or (value not in allowed if allowed is not None else not NAME.fullmatch(value))
        for value in values
    ) or len(values) != len(set(values)):
        raise ValueError(f"Invalid Skill reference {field}: {label}")
    return values


def _select_references(item, role, stage, step, purpose, task):
    rules = item.get("references", [])
    if not isinstance(rules, list):
        raise ValueError("Invalid Skill references declaration")
    selected = []
    matched_purpose = False
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - RULE_KEYS or not {"ref", "stages"} <= rule.keys():
            raise ValueError("Invalid Skill reference rule")
        ref = rule["ref"]
        if not isinstance(ref, str) or len(parts := ref.split("#")) != 2 or not all(
            NAME.fullmatch(part) for part in parts
        ):
            raise ValueError(f"Invalid local Skill reference: {ref}")
        stages = _rule_names(rule, "stages", ref, item["stages"])
        roles = _rule_names(rule, "roles", ref, item["roles"]) if "roles" in rule else item["roles"]
        steps = _rule_names(rule, "steps", ref, REQUIREMENT_STEPS) if "steps" in rule else None
        purposes = _rule_names(rule, "purposes", ref) if "purposes" in rule else None
        if steps and (item["id"] != "requirement-architecture" or "S1" not in stages):
            raise ValueError(f"Invalid S1 Skill reference steps: {ref}")
        if "requires_edges" in rule and rule["requires_edges"] is not True:
            raise ValueError(f"Invalid Skill reference requires_edges: {ref}")
        if stage not in stages or role not in roles or (steps and step not in steps):
            continue
        if rule.get("requires_edges") and not (task and task.get("edges")):
            continue
        if purposes and purpose not in purposes:
            continue
        if ref in selected:
            raise ValueError(f"Duplicate applicable Skill reference: {ref}")
        selected.append(ref)
        matched_purpose = matched_purpose or purposes is not None
    if purpose is not None and not matched_purpose:
        raise ValueError(f"Skill {item['id']} does not support purpose {purpose} in this context")
    return selected


def _read_local(path, label):
    try:
        if path.resolve() != path or not path.is_file():
            raise ValueError(f"{label} must point to a regular local file: {path}")
        return path.read_bytes()
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Unable to read local {label}: {path}") from error


def _skill_body(text, name, label):
    lines = text.splitlines(keepends=True)
    if lines and lines[0].rstrip("\r\n") == "---":
        header_end = next((i for i, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"), None)
        if header_end and any(line.rstrip("\r\n") == f"name: {name}" for line in lines[1:header_end]):
            return "".join(lines[header_end + 1:])
    raise ValueError(f"{label} front matter does not match its identifier: {name}")


def _section_headings(lines):
    fence = None
    for index, line in enumerate(lines):
        line = line.rstrip("\r\n")
        fenced = FENCE.fullmatch(line)
        if fence is not None:
            if fenced:
                marker, rest = fenced.groups()
                if marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
                    fence = None
            continue
        if fenced:
            fence = fenced[1]
            continue
        heading = HEADING.fullmatch(line)
        if heading:
            marker, title = heading.groups()
            title = re.sub(r"[ \t]+#+$", "", title)
            slug = re.sub(r"[^\w\s-]", "", title.lower()).replace(" ", "-").replace("\t", "-")
            yield index, len(marker), title, slug


def _read_reference(text, ref):
    lines = text.splitlines(keepends=True)
    headings = list(_section_headings(lines))
    slug = ref.split("#")[1]
    positions = [i for i, heading in enumerate(headings) if heading[3] == slug]
    if not positions:
        raise ValueError(f"Missing local Skill reference section: {ref}")
    if len(positions) != 1:
        raise ValueError(f"Duplicate local Skill reference section: {ref}")
    position = positions[0]
    start, depth, title, _ = headings[position]
    end = next((index for index, level, _, _ in headings[position + 1:] if level <= depth), len(lines))
    if not "".join(lines[start + 1:end]).strip():
        raise ValueError(f"Empty local Skill reference section: {ref}")
    return title, "".join(lines[start:end])


def read_skill(skill_id, role, stage, root=SKILL_ROOT, *, step=None, purpose=None, task=None):
    root = root.resolve()
    if purpose is not None and (not isinstance(purpose, str) or not NAME.fullmatch(purpose)):
        raise ValueError(f"Invalid Skill purpose: {purpose}")
    if task is not None and (
        not isinstance(task, dict) or not isinstance(task.get("id"), str) or not task["id"]
        or not isinstance(task.get("edges", []), list)
    ):
        raise ValueError("Invalid Skill task context")
    registry = root / "registry.json"
    raw_registry = _read_local(registry, "Skill registry")
    entries = json.loads(raw_registry)["skills"]
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ValueError("Invalid Skill registry entries")
    ids = [item.get("id") for item in entries]
    if any(not isinstance(name, str) or not NAME.fullmatch(name) for name in ids):
        raise ValueError("Invalid Skill identifier in registry")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate Skill identifiers in registry")
    if skill_id not in ids:
        raise ValueError(f"Skill not registered: {skill_id}")
    item = entries[ids.index(skill_id)]
    roles, stages = item.get("roles"), item.get("stages")
    if not isinstance(roles, list) or not isinstance(stages, list):
        raise ValueError("Invalid Skill role or stage declaration")
    if any(not isinstance(x, str) or x not in ROLES for x in roles):
        raise ValueError("Unknown Skill role in registry")
    if any(not isinstance(x, str) or x not in STAGES for x in stages):
        raise ValueError("Unknown Skill stage in registry")
    if role not in roles or stage not in stages:
        raise ValueError(f"Skill {skill_id} does not permit {role} at {stage}")
    expected = root / skill_id / "SKILL.md"
    if item.get("path") != f"{skill_id}/SKILL.md" or expected.resolve() != expected:
        raise ValueError("Skill path must point to its registered local file")
    contents = _read_local(expected, "Skill")
    snapshots = {registry: raw_registry, expected: contents}
    text = contents.decode("utf-8")
    _skill_body(text, skill_id, "Skill")
    result = {"id": skill_id, "role": role, "stage": stage}
    digest_parts = [raw_registry, contents]
    source = expected
    if step is not None:
        if skill_id != "requirement-architecture" or role != "architect" or stage != "S1" or step not in REQUIREMENT_STEPS:
            raise ValueError("S1 四步规范仅由需求架构师通过 requirement-architecture 调用")
        source = expected.parent / "references" / f"{step}.md"
        guide_contents = _read_local(source, "S1 guide")
        snapshots[source] = guide_contents
        result["step"] = step
        digest_parts.extend((step.encode(), guide_contents))
        text = guide_contents.decode("utf-8")
    if purpose is not None:
        result["purpose"] = purpose
    if task is not None:
        result["task_id"] = task["id"]

    references = []
    excerpts = []
    for ref in _select_references(item, role, stage, step, purpose, task):
        pack, slug = ref.split("#")
        path = root / "references" / pack / "SKILL.md"
        if path not in snapshots:
            snapshots[path] = _read_local(path, "Skill reference")
        reference = _skill_body(snapshots[path].decode("utf-8"), pack, "Skill reference")
        title, excerpt = _read_reference(reference, ref)
        section_bytes = excerpt.encode("utf-8")
        metadata = {
            "ref": ref, "path": str(path), "section": slug, "title": title,
            "sha256": hashlib.sha256(section_bytes).hexdigest(),
        }
        references.append(metadata)
        excerpts.append(f"来源 `{ref}`（{path}）\n\n{excerpt}")
        digest_parts.extend((json.dumps(metadata, sort_keys=True).encode("utf-8"), section_bytes))

    if any(_read_local(path, "Skill input") != snapshot for path, snapshot in snapshots.items()):
        raise ValueError("Skill changed while loading. Retry the invocation.")
    result["source_path"] = str(source)
    result["references"] = references
    result["digest"] = hashlib.sha256(b"\0".join(digest_parts)).hexdigest()
    result["content"] = text if not excerpts else text.rstrip() + "\n\n# RTL 本地参考\n\n" + "\n\n".join(excerpts)
    return result
