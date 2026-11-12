"""Shared structural validation for business-scenario swimlane diagrams."""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any


LANE_KINDS = {"actor", "system", "external_system"}
NODE_TYPES = {"start", "action", "event", "decision", "end"}


def issue(code: str, message: str, ids: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "message": message}
    if ids:
        result["ids"] = ids
    return result


def duplicates(values: list[str]) -> set[str]:
    return {value for value, count in Counter(values).items() if value and count > 1}


def inspect_swimlane(detail: dict[str, Any]) -> list[dict[str, Any]]:
    scenario_id = str(detail.get("id", "未知场景"))
    problems: list[dict[str, Any]] = []
    model = detail.get("flow_diagram")
    if not isinstance(model, dict):
        return [issue("missing_swimlane", f"{scenario_id} 缺少 flow_diagram", [scenario_id])]

    lanes = model.get("lanes")
    nodes = model.get("nodes")
    edges = model.get("edges")
    if not isinstance(lanes, list) or not lanes:
        problems.append(issue("empty_swimlane_lanes", f"{scenario_id} 的 flow_diagram.lanes 为空", [scenario_id]))
        lanes = []
    if not isinstance(nodes, list) or not nodes:
        problems.append(issue("empty_swimlane_nodes", f"{scenario_id} 的 flow_diagram.nodes 为空", [scenario_id]))
        nodes = []
    if not isinstance(edges, list):
        problems.append(issue("invalid_swimlane_edges", f"{scenario_id} 的 flow_diagram.edges 必须是数组", [scenario_id]))
        edges = []
    elif len(nodes) > 1 and not edges:
        problems.append(issue("empty_swimlane_edges", f"{scenario_id} 有多个节点但没有流程连线", [scenario_id]))

    lane_ids = [str(lane.get("id", "")) for lane in lanes if isinstance(lane, dict)]
    for lane in lanes:
        if not isinstance(lane, dict):
            problems.append(issue("invalid_swimlane_lane", f"{scenario_id} 包含非对象泳道", [scenario_id]))
            continue
        lane_id = str(lane.get("id", ""))
        if not lane_id:
            problems.append(issue("empty_swimlane_lane_id", f"{scenario_id} 包含空泳道 ID", [scenario_id]))
        if not lane.get("name"):
            problems.append(issue("empty_swimlane_lane_name", f"{scenario_id} 的泳道 {lane_id or '?'} 缺少名称", [scenario_id]))
        if lane.get("kind") not in LANE_KINDS:
            problems.append(issue("invalid_swimlane_lane_kind", f"{scenario_id} 的泳道 {lane_id or '?'} kind 不合法", [scenario_id]))
    for lane_id in sorted(duplicates(lane_ids)):
        problems.append(issue("duplicate_swimlane_lane", f"{scenario_id} 的泳道 ID 重复：{lane_id}", [scenario_id]))

    node_ids = [str(node.get("id", "")) for node in nodes if isinstance(node, dict)]
    for node_id in sorted(duplicates(node_ids)):
        problems.append(issue("duplicate_swimlane_node", f"{scenario_id} 的节点 ID 重复：{node_id}", [scenario_id]))
    lane_id_set = set(lane_ids)
    node_id_set = set(node_ids)
    node_by_id: dict[str, dict[str, Any]] = {}
    used_lanes: set[str] = set()
    lane_orders: set[tuple[str, float]] = set()
    main_steps = {step.get("step") for step in detail.get("main_flow", []) if isinstance(step, dict)}
    referenced_steps: set[Any] = set()
    for node in nodes:
        if not isinstance(node, dict):
            problems.append(issue("invalid_swimlane_node", f"{scenario_id} 包含非对象节点", [scenario_id]))
            continue
        node_id = str(node.get("id", ""))
        lane_id = str(node.get("lane_id", ""))
        node_type = node.get("type")
        if not node_id:
            problems.append(issue("empty_swimlane_node_id", f"{scenario_id} 包含空节点 ID", [scenario_id]))
            continue
        node_by_id[node_id] = node
        if lane_id not in lane_id_set:
            problems.append(issue("unknown_swimlane_lane", f"{scenario_id} 的节点 {node_id} 引用了未知泳道 {lane_id}", [scenario_id]))
        else:
            used_lanes.add(lane_id)
        if node_type not in NODE_TYPES:
            problems.append(issue("invalid_swimlane_node_type", f"{scenario_id} 的节点 {node_id} type 不合法", [scenario_id]))
        if not node.get("label"):
            problems.append(issue("empty_swimlane_node_label", f"{scenario_id} 的节点 {node_id} 缺少 label", [scenario_id]))
        order = node.get("order")
        if not isinstance(order, (int, float)) or isinstance(order, bool) or order <= 0:
            problems.append(issue("invalid_swimlane_order", f"{scenario_id} 的节点 {node_id} 缺少正数 order", [scenario_id]))
        elif lane_id in lane_id_set:
            position = (lane_id, float(order))
            if position in lane_orders:
                problems.append(issue("swimlane_node_overlap", f"{scenario_id} 的泳道 {lane_id} 在 order={order} 存在节点重叠", [scenario_id]))
            lane_orders.add(position)
        if node.get("step_ref") is not None and node.get("step_ref") not in main_steps:
            problems.append(issue("unknown_swimlane_step_ref", f"{scenario_id} 的节点 {node_id} 引用了不存在的 main_flow step", [scenario_id]))
        elif node.get("step_ref") is not None:
            referenced_steps.add(node.get("step_ref"))

    for step in sorted(main_steps - referenced_steps, key=str):
        problems.append(issue("main_step_not_in_swimlane", f"{scenario_id} 的 main_flow step {step} 没有对应泳道节点", [scenario_id]))

    for lane_id in sorted(lane_id_set - used_lanes):
        problems.append(issue("unused_swimlane_lane", f"{scenario_id} 的泳道 {lane_id} 没有流程节点", [scenario_id]))

    lane_names = {str(lane.get("name")) for lane in lanes if isinstance(lane, dict) and lane.get("name")}
    actor_names = {
        str(actor.get("name")) if isinstance(actor, dict) else str(actor)
        for actor in detail.get("actors", [])
        if actor
    }
    for actor in sorted(actor_names - lane_names):
        problems.append(issue("actor_without_swimlane", f"{scenario_id} 的参与方“{actor}”没有对应泳道", [scenario_id]))
    for step in detail.get("main_flow", []):
        if isinstance(step, dict) and step.get("actor") and str(step["actor"]) not in lane_names:
            problems.append(issue("step_actor_without_swimlane", f"{scenario_id} 的主流程责任方“{step['actor']}”没有对应泳道", [scenario_id]))

    outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
    incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edge_keys: list[tuple[str, str, str]] = []
    alternate_ids = {
        str(path.get("id"))
        for path in detail.get("alternate_flows", [])
        if isinstance(path, dict) and path.get("id")
    }
    represented_alternates: set[str] = set()
    for edge in edges:
        if not isinstance(edge, dict):
            problems.append(issue("invalid_swimlane_edge", f"{scenario_id} 包含非对象连线", [scenario_id]))
            continue
        source = str(edge.get("from", ""))
        target = str(edge.get("to", ""))
        label = str(edge.get("label", ""))
        if source not in node_id_set or target not in node_id_set:
            problems.append(issue("unknown_swimlane_edge_endpoint", f"{scenario_id} 的连线 {source} → {target} 存在未知端点", [scenario_id]))
            continue
        if source == target:
            problems.append(issue("self_swimlane_edge", f"{scenario_id} 的节点 {source} 不能连接自身", [scenario_id]))
        outgoing[source].append(edge)
        incoming[target].append(edge)
        edge_keys.append((source, target, label))
        path_id = edge.get("path_id")
        if path_id:
            path_id = str(path_id)
            if path_id not in alternate_ids:
                problems.append(issue("unknown_swimlane_path_ref", f"{scenario_id} 的连线引用了未知备选路径 {path_id}", [scenario_id]))
            else:
                represented_alternates.add(path_id)
        source_order = node_by_id.get(source, {}).get("order")
        target_order = node_by_id.get(target, {}).get("order")
        if isinstance(source_order, (int, float)) and isinstance(target_order, (int, float)) and target_order <= source_order and not label:
            problems.append(issue("unlabelled_swimlane_loop", f"{scenario_id} 的回退连线 {source} → {target} 必须说明重试或回退条件", [scenario_id]))
    for raw in sorted(duplicates(["\x00".join(key) for key in edge_keys])):
        source, target, _label = raw.split("\x00", 2)
        problems.append(issue("duplicate_swimlane_edge", f"{scenario_id} 的流程连线重复：{source} → {target}", [scenario_id]))
    for path_id in sorted(alternate_ids - represented_alternates):
        problems.append(issue("alternate_not_in_swimlane", f"{scenario_id} 的备选/异常路径 {path_id} 没有通过 edge.path_id 进入泳道图", [scenario_id]))

    starts = [node_id for node_id, node in node_by_id.items() if node.get("type") == "start"]
    ends = [node_id for node_id, node in node_by_id.items() if node.get("type") == "end"]
    if len(starts) != 1:
        problems.append(issue("invalid_swimlane_start_count", f"{scenario_id} 必须且只能有一个 start 节点，当前为 {len(starts)}", [scenario_id]))
    if not ends:
        problems.append(issue("missing_swimlane_end", f"{scenario_id} 至少需要一个 end 节点", [scenario_id]))
    for start in starts:
        if incoming.get(start):
            problems.append(issue("swimlane_start_has_incoming", f"{scenario_id} 的 start 节点 {start} 不能有入边", [scenario_id]))
    for end in ends:
        if outgoing.get(end):
            problems.append(issue("swimlane_end_has_outgoing", f"{scenario_id} 的 end 节点 {end} 不能有出边", [scenario_id]))

    for node_id, node in node_by_id.items():
        branches = outgoing.get(node_id, [])
        if node.get("type") == "decision" and len(branches) < 2:
            problems.append(issue("decision_without_branches", f"{scenario_id} 的决策节点 {node_id} 至少需要两条出边", [scenario_id]))
        if len(branches) > 1:
            labels = [str(edge.get("label", "")).strip() for edge in branches]
            if any(not label for label in labels) or len(set(labels)) != len(labels):
                problems.append(issue("invalid_branch_labels", f"{scenario_id} 的分支节点 {node_id} 必须使用非空且互不重复的条件标签", [scenario_id]))

    if len(starts) == 1 and node_by_id:
        reachable = {starts[0]}
        queue = deque(starts)
        while queue:
            current = queue.popleft()
            for edge in outgoing.get(current, []):
                target = str(edge["to"])
                if target not in reachable:
                    reachable.add(target)
                    queue.append(target)
        for node_id in sorted(node_id_set - reachable):
            problems.append(issue("unreachable_swimlane_node", f"{scenario_id} 的节点 {node_id} 无法从开始节点到达", [scenario_id]))

    if ends and node_by_id:
        can_finish = set(ends)
        queue = deque(ends)
        while queue:
            current = queue.popleft()
            for edge in incoming.get(current, []):
                source = str(edge["from"])
                if source not in can_finish:
                    can_finish.add(source)
                    queue.append(source)
        for node_id in sorted(node_id_set - can_finish):
            problems.append(issue("swimlane_dead_end", f"{scenario_id} 的节点 {node_id} 无法到达结束节点", [scenario_id]))
    return problems
