"""Canonical descriptions of assertions executed by the browser runner."""

ASSERTION_FIELDS = {
    "attribute": ("attribute", "value"),
    "css": ("property", "value"),
    "disabled": (),
    "hidden": (),
    "text": ("value",),
    "value": ("value",),
    "visible": (),
}


ACTION_FIELDS = {
    "open": (),
    "reload": (),
    "click": ("selector",),
    "fill": ("selector", "value"),
    "select": ("selector", "value"),
    "check": ("selector",),
    "uncheck": ("selector",),
}


def normalize_action(step, *, exact=True):
    """Describe the exact browser action that precedes a checkpoint."""
    if not isinstance(step, dict):
        raise ValueError("验收时点的前置动作须为对象")
    op = step.get("op")
    if not isinstance(op, str) or op not in ACTION_FIELDS:
        raise ValueError("验收时点的前置步骤须使用支持的浏览器动作")
    fields = ("op", *ACTION_FIELDS[op])
    if exact and set(step) != set(fields):
        raise ValueError(f"{op} 前置动作须填写且仅填写 {', '.join(fields)}")
    if any(not isinstance(step.get(field), str) for field in fields):
        raise ValueError(f"{op} 前置动作字段须使用文本")
    if "selector" in fields and not step["selector"].strip():
        raise ValueError(f"{op} 前置动作须指向实际控件")
    return {field: step[field] for field in fields}


def normalize_checkpoint(checkpoint):
    if not isinstance(checkpoint, dict) or set(checkpoint) != {"index", "after"}:
        raise ValueError("验收时点 checkpoint 须填写 index 和 after")
    index, after = checkpoint["index"], checkpoint["after"]
    if type(index) is not int or index < 0:
        raise ValueError("验收时点 index 须为从 0 开始的步骤序号")
    if not isinstance(after, list) or len(after) > index:
        raise ValueError("验收时点 after 须为按顺序列出的前置动作")
    return {"index": index, "after": [normalize_action(step) for step in after]}


def normalize_assertion(step, *, exact=True):
    """Return only the fields that determine the observed predicate."""
    if not isinstance(step, dict):
        raise ValueError("验收断言须为对象")
    op = step.get("op")
    if not isinstance(op, str) or op not in ASSERTION_FIELDS:
        raise ValueError("验收断言须使用支持的浏览器断言操作")
    fields = ("op", "selector", *ASSERTION_FIELDS[op])
    if exact and set(step) != set(fields):
        raise ValueError(f"{op} 验收断言须填写且仅填写 {', '.join(fields)}")
    if any(not isinstance(step.get(field), str) for field in fields):
        raise ValueError(f"{op} 验收断言字段须使用文本")
    if any(not step[field].strip() for field in fields if field != "value"):
        raise ValueError(f"{op} 验收断言须指向实际控件或页面状态")
    if op == "text" and not step["value"].strip():
        raise ValueError("text 验收断言须提供可观察的非空预期文字")
    return {field: step[field] for field in fields}


def observable_predicate(step):
    """Ignore selector-only changes for assertions with an explicit expected value."""
    fields = ASSERTION_FIELDS[step["op"]]
    return (step["op"], *(step[field] for field in (fields or ("selector",))))


def observable_checkpoint(binding):
    """Identify approved behavior and action context across suite revisions."""
    checkpoint = binding.get("checkpoint")
    if checkpoint is None:
        return None
    after = normalize_checkpoint(checkpoint)["after"]
    return observable_predicate(binding["step"]), tuple(tuple(step.items()) for step in after)


def matches_checkpoint(binding, steps):
    """Match the exact assertion occurrence and the actions before it."""
    try:
        checkpoint = normalize_checkpoint(binding.get("checkpoint"))
        index = checkpoint["index"]
        if normalize_assertion(steps[index], exact=False) != binding["step"]:
            return False
        actual = [
            normalize_action(step, exact=False)
            for step in steps[:index] if step["op"] not in ASSERTION_FIELDS
        ]
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    return actual == checkpoint["after"]


def browser_case_plan(steps):
    """Describe the ordered actions and observable predicates in one frozen case."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("验收用例缺少执行步骤")
    executed, assertions = [], []
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("op"), str):
            raise ValueError("验收用例缺少浏览器动作")
        op, selector = step["op"], step.get("selector")
        if selector and not isinstance(selector, str):
            raise ValueError("浏览器动作的 selector 须使用文本")
        executed.append(op + (" " + selector if selector else ""))
        if op in ASSERTION_FIELDS:
            try:
                assertions.append(normalize_assertion(step, exact=False))
            except ValueError:
                # Legacy suites may contain browser-coerced values. Those steps
                # still run but cannot prove a newly frozen assertion.
                continue
    return executed, assertions
