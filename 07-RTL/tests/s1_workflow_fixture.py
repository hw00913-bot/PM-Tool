"""Prepare an S1 record via the normal CLI for the browser smoke tests."""

import json


def record_s1(cli, root, project, baseline, *, name="initial"):
    frozen = json.loads(baseline.read_text())
    plan = frozen["requirements"]
    record = root / f"s1-{name}.json"
    contract = root / f"contract-{name}.json"
    contract.write_text(json.dumps({
        "revision": frozen["revision"], "requirements": plan["requirements"], "pending": [],
        "human_acceptance": frozen.get("human_acceptance", False),
        "change_assertions": [
            change for change in plan["changes"] if {"assertion_changes", "assertion_reuse"} & change.keys()
        ],
    }))
    invoke = ("skill", "requirement-architecture", "--role", "architect", "--stage", "S1", "--s1-record", record)
    cli(*invoke, "--step", "analysis")
    cli("s1-confirm", "--project", project, "--contract", contract, "--approval", plan["approval"],
        "--s1-record", record)
    for step in ("scenes", "features", "tasks"):
        cli(*invoke, "--step", step)
        cli("s1-handoff", "--s1-record", record, "--step", step, "--artifact", baseline)
    return record
