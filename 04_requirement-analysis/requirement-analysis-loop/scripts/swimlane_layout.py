"""Deterministic, collision-aware layout for business-scenario swimlanes."""

from __future__ import annotations

from collections import defaultdict
from math import isclose
from typing import Any


NODE_SIZES = {
    "start": (124.0, 42.0),
    "end": (124.0, 42.0),
    "decision": (144.0, 68.0),
    "event": (164.0, 54.0),
    "action": (164.0, 54.0),
}
HEADER_HEIGHT = 64.0
CANVAS_MARGIN_Y = 28.0
ROW_PADDING = 30.0
BASE_GAP = 54.0
TRACK_SPACING = 24.0
PORT_SPACING = 14.0
CHANNEL_SPACING = 12.0
NODE_CLEARANCE = 10.0


def _port_color_offset(color: int) -> float:
    if color == 0:
        return 0.0
    magnitude = ((color + 1) // 2) * PORT_SPACING
    return -magnitude if color % 2 else magnitude


def _node_anchor(node: dict[str, Any], offset: float, side: str) -> tuple[float, float]:
    x = float(node["x"]) + offset
    y = float(node["y"])
    width = float(node["width"])
    height = float(node["height"])
    direction = -1.0 if side == "top" else 1.0
    if node["type"] == "decision":
        ratio = min(1.0, abs(offset) / (width / 2))
        y += direction * (height / 2) * (1.0 - ratio)
    else:
        y += direction * height / 2
    return (x, y)


def _simplify(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        point = (round(point[0], 2), round(point[1], 2))
        if result and isclose(result[-1][0], point[0], abs_tol=0.01) and isclose(result[-1][1], point[1], abs_tol=0.01):
            continue
        result.append(point)
        while len(result) >= 3:
            first, middle, last = result[-3:]
            same_x = isclose(first[0], middle[0], abs_tol=0.01) and isclose(middle[0], last[0], abs_tol=0.01)
            same_y = isclose(first[1], middle[1], abs_tol=0.01) and isclose(middle[1], last[1], abs_tol=0.01)
            if not (same_x or same_y):
                break
            result.pop(-2)
    return result


def _edge_label_position(points: list[tuple[float, float]]) -> dict[str, Any]:
    horizontal = []
    vertical = []
    for start, end in zip(points, points[1:]):
        if isclose(start[1], end[1], abs_tol=0.01):
            horizontal.append((abs(end[0] - start[0]), start, end))
        else:
            vertical.append((abs(end[1] - start[1]), start, end))
    if horizontal:
        _length, start, end = max(horizontal, key=lambda item: item[0])
        return {"x": (start[0] + end[0]) / 2, "y": start[1] - 8, "anchor": "middle"}
    _length, start, end = max(vertical, key=lambda item: item[0])
    return {"x": start[0] + 9, "y": (start[1] + end[1]) / 2 - 4, "anchor": "start"}


def estimate_label_width(value: str) -> float:
    width = sum(11 if ord(char) > 255 else 6.5 for char in value)
    return min(190.0, max(34.0, width + 14))


def _interval_channels(items: list[tuple[int, float, float]]) -> dict[int, int]:
    assignments: dict[int, int] = {}
    channel_ends: list[float] = []
    for edge_index, start, end in sorted(items, key=lambda item: (item[1], item[2], item[0])):
        selected = None
        for channel, previous_end in enumerate(channel_ends):
            if previous_end + 6 < start:
                selected = channel
                channel_ends[channel] = end
                break
        if selected is None:
            selected = len(channel_ends)
            channel_ends.append(end)
        assignments[edge_index] = selected
    return assignments


def _internal_channel_offset(channel: int) -> float:
    magnitude = CHANNEL_SPACING * (channel // 2 + 1)
    return -magnitude if channel % 2 == 0 else magnitude


def build_swimlane_layout(model: dict[str, Any]) -> dict[str, Any]:
    """Build node geometry and non-overlapping orthogonal edge routes."""

    lanes = model["lanes"]
    raw_nodes = model["nodes"]
    raw_edges = model["edges"]
    lane_index = {str(lane["id"]): index for index, lane in enumerate(lanes)}
    orders = sorted({float(node["order"]) for node in raw_nodes})
    order_index = {order: index for index, order in enumerate(orders)}

    nodes: dict[str, dict[str, Any]] = {}
    for raw in raw_nodes:
        node_id = str(raw["id"])
        node_type = str(raw.get("type", "action"))
        width, height = NODE_SIZES.get(node_type, NODE_SIZES["action"])
        nodes[node_id] = {
            "id": node_id,
            "lane_id": str(raw["lane_id"]),
            "lane_index": lane_index[str(raw["lane_id"])],
            "row_index": order_index[float(raw["order"])],
            "order": float(raw["order"]),
            "type": node_type,
            "label": raw.get("label", node_id),
            "width": width,
            "height": height,
            "raw": raw,
        }

    edge_meta: list[dict[str, Any]] = []
    last_row = len(orders) - 1
    for index, raw in enumerate(raw_edges, start=1):
        source = nodes[str(raw["from"])]
        target = nodes[str(raw["to"])]
        source_row = int(source["row_index"])
        target_row = int(target["row_index"])
        if target_row > source_row:
            source_side, target_side, direction = "bottom", "top", "forward"
        elif target_row < source_row:
            source_side, target_side, direction = "top", "bottom", "backward"
        else:
            below = source_row < last_row or last_row == 0
            source_side = target_side = "bottom" if below else "top"
            direction = "same-row-below" if below else "same-row-above"
        edge_meta.append(
            {
                "index": index,
                "raw": raw,
                "source_id": source["id"],
                "target_id": target["id"],
                "source_row": source_row,
                "target_row": target_row,
                "source_lane": int(source["lane_index"]),
                "target_lane": int(target["lane_index"]),
                "source_side": source_side,
                "target_side": target_side,
                "direction": direction,
            }
        )

    outgoing: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    incoming: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for edge in edge_meta:
        outgoing[(edge["source_id"], edge["source_side"])].append(edge)
        incoming[(edge["target_id"], edge["target_side"])].append(edge)

    gap_demands: dict[int, list[tuple[int, str]]] = defaultdict(list)
    external_bottom: list[tuple[int, str]] = []
    for edge in edge_meta:
        source_row = edge["source_row"]
        target_row = edge["target_row"]
        same_lane = edge["source_lane"] == edge["target_lane"]
        isolated_ports = (
            len(outgoing[(edge["source_id"], edge["source_side"])]) == 1
            and len(incoming[(edge["target_id"], edge["target_side"])]) == 1
        )
        if edge["direction"] == "forward":
            if target_row == source_row + 1 and same_lane and isolated_ports:
                edge["route_kind"] = "straight"
            elif target_row == source_row + 1:
                edge["route_kind"] = "bridge"
                edge["bridge_gap"] = source_row
                gap_demands[source_row].append((edge["index"], "bridge"))
            else:
                edge["route_kind"] = "corridor"
                edge["exit_gap"] = source_row
                edge["entry_gap"] = target_row - 1
                gap_demands[source_row].append((edge["index"], "exit"))
                gap_demands[target_row - 1].append((edge["index"], "entry"))
        elif edge["direction"] == "backward":
            edge["route_kind"] = "corridor"
            edge["exit_gap"] = source_row - 1
            edge["entry_gap"] = target_row
            gap_demands[source_row - 1].append((edge["index"], "exit"))
            gap_demands[target_row].append((edge["index"], "entry"))
        else:
            edge["route_kind"] = "same-row"
            gap_index = source_row if edge["direction"] == "same-row-below" and source_row < last_row else source_row - 1
            if gap_index >= 0:
                edge["bridge_gap"] = gap_index
                gap_demands[gap_index].append((edge["index"], "bridge"))
            else:
                external_bottom.append((edge["index"], "bridge"))

    endpoint_groups: list[list[tuple[int, str]]] = []
    for group in outgoing.values():
        endpoint_groups.append([(int(edge["index"]), "source") for edge in group])
    for group in incoming.values():
        endpoint_groups.append([(int(edge["index"]), "target") for edge in group])
    gap_lane_endpoints: dict[tuple[Any, int], list[tuple[int, str]]] = defaultdict(list)
    for edge in edge_meta:
        if edge["route_kind"] == "straight":
            continue
        if edge["route_kind"] in {"bridge", "same-row"}:
            source_gap: Any = edge.get("bridge_gap", "external-bottom")
            target_gap: Any = source_gap
        else:
            source_gap = edge["exit_gap"]
            target_gap = edge["entry_gap"]
        gap_lane_endpoints[(source_gap, int(edge["source_lane"]))].append((int(edge["index"]), "source"))
        gap_lane_endpoints[(target_gap, int(edge["target_lane"]))].append((int(edge["index"]), "target"))
    endpoint_groups.extend(gap_lane_endpoints.values())

    adjacency: dict[tuple[int, str], set[tuple[int, str]]] = defaultdict(set)
    for group in endpoint_groups:
        for endpoint in group:
            adjacency.setdefault(endpoint, set())
            adjacency[endpoint].update(other for other in group if other != endpoint)
    colors: dict[tuple[int, str], int] = {}
    for endpoint in sorted(adjacency, key=lambda item: (-len(adjacency[item]), item[0], item[1])):
        unavailable = {colors[neighbor] for neighbor in adjacency[endpoint] if neighbor in colors}
        color = 0
        while color in unavailable:
            color += 1
        colors[endpoint] = color
    for edge in edge_meta:
        edge["source_offset"] = _port_color_offset(colors.get((int(edge["index"]), "source"), 0))
        edge["target_offset"] = _port_color_offset(colors.get((int(edge["index"]), "target"), 0))

    for node_id, node in nodes.items():
        offsets = []
        offsets.extend(abs(float(edge["source_offset"])) for edge in edge_meta if edge["source_id"] == node_id)
        offsets.extend(abs(float(edge["target_offset"])) for edge in edge_meta if edge["target_id"] == node_id)
        if offsets:
            node["width"] = max(float(node["width"]), 2 * (max(offsets) + 30.0))

    row_half_heights = []
    for row in range(len(orders)):
        row_half_heights.append(max(float(node["height"]) / 2 for node in nodes.values() if node["row_index"] == row))
    gap_heights = {
        gap: max(BASE_GAP, TRACK_SPACING * (len(gap_demands.get(gap, [])) + 1))
        for gap in range(max(0, len(orders) - 1))
    }
    body_top = CANVAS_MARGIN_Y + HEADER_HEIGHT
    row_centers: list[float] = [body_top + ROW_PADDING + row_half_heights[0]]
    for row in range(len(orders) - 1):
        row_centers.append(
            row_centers[-1] + row_half_heights[row] + gap_heights[row] + row_half_heights[row + 1]
        )
    bottom_extra = TRACK_SPACING * (len(external_bottom) + 1) if external_bottom else 0.0
    lane_bottom = row_centers[-1] + row_half_heights[-1] + ROW_PADDING + bottom_extra
    height = lane_bottom + CANVAS_MARGIN_Y

    track_y: dict[tuple[int, str], float] = {}
    edge_by_index = {int(edge["index"]): edge for edge in edge_meta}

    def demand_order(demand: tuple[int, str]) -> tuple[int, int, int, int]:
        edge_index, role = demand
        edge = edge_by_index[edge_index]
        if role == "bridge":
            band = 1
        elif (role == "exit" and edge["direction"] == "forward") or (
            role == "entry" and edge["direction"] == "backward"
        ):
            band = 0
        else:
            band = 2
        return (band, int(edge["source_lane"]), int(edge["target_lane"]), edge_index)

    for gap, demands in gap_demands.items():
        top = row_centers[gap] + row_half_heights[gap]
        gap_height = gap_heights[gap]
        for slot, demand in enumerate(sorted(demands, key=demand_order), start=1):
            track_y[demand] = top + gap_height * slot / (len(demands) + 1)
    if external_bottom:
        top = row_centers[-1] + row_half_heights[-1]
        for slot, demand in enumerate(sorted(external_bottom), start=1):
            track_y[demand] = top + TRACK_SPACING * slot

    corridor_intervals: dict[int, list[tuple[int, float, float]]] = defaultdict(list)
    for edge in edge_meta:
        if edge["route_kind"] != "corridor":
            continue
        exit_y = track_y[(edge["index"], "exit")]
        entry_y = track_y[(edge["index"], "entry")]
        if edge["target_lane"] < edge["source_lane"]:
            boundary = edge["target_lane"] + 1
        elif edge["target_lane"] > edge["source_lane"]:
            boundary = edge["target_lane"]
        else:
            lane = edge["source_lane"]
            boundary = lane if lane <= (len(lanes) - 1) / 2 else lane + 1
        edge["corridor_boundary"] = boundary
        corridor_intervals[boundary].append((edge["index"], min(exit_y, entry_y), max(exit_y, entry_y)))
    channel_assignments: dict[int, int] = {}
    for items in corridor_intervals.values():
        channel_assignments.update(_interval_channels(items))

    max_internal_offset = 0.0
    max_outer_offset = 0.0
    for edge in edge_meta:
        if edge["route_kind"] != "corridor":
            continue
        boundary = int(edge["corridor_boundary"])
        channel = channel_assignments[edge["index"]]
        edge["corridor_channel"] = channel
        if boundary == 0 or boundary == len(lanes):
            offset = 18.0 + channel * CHANNEL_SPACING
            max_outer_offset = max(max_outer_offset, offset)
        else:
            offset = _internal_channel_offset(channel)
            max_internal_offset = max(max_internal_offset, abs(offset))
        edge["corridor_offset"] = offset

    maximum_node_width = max(float(node["width"]) for node in nodes.values())
    lane_width = max(236.0, maximum_node_width + 2 * (max_internal_offset + NODE_CLEARANCE))
    side_margin = max(58.0, max_outer_offset + 34.0)
    width = side_margin * 2 + lane_width * len(lanes)
    lane_left = {str(lane["id"]): side_margin + index * lane_width for index, lane in enumerate(lanes)}
    for node in nodes.values():
        node["x"] = lane_left[node["lane_id"]] + lane_width / 2
        node["y"] = row_centers[int(node["row_index"])]
        node["bbox"] = {
            "left": node["x"] - node["width"] / 2,
            "right": node["x"] + node["width"] / 2,
            "top": node["y"] - node["height"] / 2,
            "bottom": node["y"] + node["height"] / 2,
        }

    layout_edges: list[dict[str, Any]] = []
    for edge in edge_meta:
        source = nodes[edge["source_id"]]
        target = nodes[edge["target_id"]]
        start = _node_anchor(source, float(edge["source_offset"]), edge["source_side"])
        end = _node_anchor(target, float(edge["target_offset"]), edge["target_side"])
        if edge["route_kind"] == "straight":
            points = [start, end]
        elif edge["route_kind"] in {"bridge", "same-row"}:
            route_y = track_y[(edge["index"], "bridge")]
            points = [start, (start[0], route_y), (end[0], route_y), end]
        else:
            exit_y = track_y[(edge["index"], "exit")]
            entry_y = track_y[(edge["index"], "entry")]
            boundary = int(edge["corridor_boundary"])
            boundary_x = side_margin + boundary * lane_width
            offset = float(edge["corridor_offset"])
            if boundary == 0:
                corridor_x = boundary_x - offset
            elif boundary == len(lanes):
                corridor_x = boundary_x + offset
            else:
                corridor_x = boundary_x + offset
            points = [start, (start[0], exit_y), (corridor_x, exit_y), (corridor_x, entry_y), (end[0], entry_y), end]
        points = _simplify(points)
        label_text = str(edge["raw"].get("label", ""))
        if edge["raw"].get("path_id"):
            label_text = f"{label_text} · {edge['raw']['path_id']}" if label_text else str(edge["raw"]["path_id"])
        label_position = _edge_label_position(points)
        label_bbox = None
        if label_text:
            chip_width = estimate_label_width(label_text)
            label_x = float(label_position["x"])
            label_y = float(label_position["y"])
            chip_x = label_x - chip_width / 2 if label_position["anchor"] == "middle" else label_x - 5
            label_bbox = {
                "left": chip_x,
                "right": chip_x + chip_width,
                "top": label_y - 12,
                "bottom": label_y + 6,
            }
        layout_edges.append(
            {
                **edge,
                "points": points,
                "source_port": points[0],
                "target_port": points[-1],
                "label_text": label_text,
                "label_position": label_position,
                "label_bbox": label_bbox,
            }
        )

    return {
        "width": round(width, 2),
        "height": round(height, 2),
        "lane_width": round(lane_width, 2),
        "lane_left": lane_left,
        "lane_top": CANVAS_MARGIN_Y,
        "lane_bottom": lane_bottom,
        "header_height": HEADER_HEIGHT,
        "row_centers": row_centers,
        "nodes": nodes,
        "edges": layout_edges,
    }


def _segments(points: list[tuple[float, float]]) -> list[tuple[str, float, float, float]]:
    result = []
    for start, end in zip(points, points[1:]):
        if isclose(start[0], end[0], abs_tol=0.01):
            result.append(("v", start[0], min(start[1], end[1]), max(start[1], end[1])))
        elif isclose(start[1], end[1], abs_tol=0.01):
            result.append(("h", start[1], min(start[0], end[0]), max(start[0], end[0])))
        else:
            result.append(("diagonal", 0.0, 0.0, 0.0))
    return result


def audit_swimlane_layout(layout: dict[str, Any]) -> list[str]:
    """Return deterministic geometry defects that would make a diagram ambiguous."""

    problems: list[str] = []
    nodes = layout["nodes"]
    edges = layout["edges"]

    def point_close(left: tuple[float, float], right: tuple[float, float]) -> bool:
        return isclose(left[0], right[0], abs_tol=0.02) and isclose(left[1], right[1], abs_tol=0.02)

    node_items = list(nodes.items())
    for left_index, (left_id, left) in enumerate(node_items):
        for right_id, right in node_items[left_index + 1 :]:
            a, b = left["bbox"], right["bbox"]
            overlap_x = min(a["right"], b["right"]) - max(a["left"], b["left"])
            overlap_y = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            if overlap_x > 0.01 and overlap_y > 0.01:
                problems.append(f"节点重叠：{left_id} 与 {right_id}")

    edge_segments: dict[int, list[tuple[str, float, float, float]]] = {}
    for edge in edges:
        edge_id = int(edge["index"])
        points = edge["points"]
        expected_source = _node_anchor(
            nodes[edge["source_id"]], float(edge["source_offset"]), str(edge["source_side"])
        )
        expected_target = _node_anchor(
            nodes[edge["target_id"]], float(edge["target_offset"]), str(edge["target_side"])
        )
        if not all(
            [
                point_close(points[0], edge["source_port"]),
                point_close(points[-1], edge["target_port"]),
                point_close(points[0], expected_source),
                point_close(points[-1], expected_target),
            ]
        ):
            problems.append(f"连线端口错位：{edge['source_id']} → {edge['target_id']}")
        segments = _segments(points)
        edge_segments[edge_id] = segments
        if any(segment[0] == "diagonal" for segment in segments):
            problems.append(f"连线不是正交线：{edge['source_id']} → {edge['target_id']}")
        for segment_index, (orientation, axis, start, end) in enumerate(segments):
            if orientation == "diagonal":
                continue
            for node_id, node in nodes.items():
                if node_id == edge["source_id"] and segment_index == 0:
                    continue
                if node_id == edge["target_id"] and segment_index == len(segments) - 1:
                    continue
                box = node["bbox"]
                if orientation == "v":
                    hits = box["left"] < axis < box["right"] and min(end, box["bottom"]) - max(start, box["top"]) > 0.01
                else:
                    hits = box["top"] < axis < box["bottom"] and min(end, box["right"]) - max(start, box["left"]) > 0.01
                if hits:
                    problems.append(f"连线穿过节点：{edge['source_id']} → {edge['target_id']} / {node_id}")

    for left_index, left in enumerate(edges):
        for right in edges[left_index + 1 :]:
            for left_segment in edge_segments[int(left["index"])]:
                for right_segment in edge_segments[int(right["index"])]:
                    if left_segment[0] != right_segment[0] or left_segment[0] == "diagonal":
                        continue
                    if not isclose(left_segment[1], right_segment[1], abs_tol=0.01):
                        continue
                    overlap = min(left_segment[3], right_segment[3]) - max(left_segment[2], right_segment[2])
                    if overlap > 0.01:
                        problems.append(
                            f"连线重叠：{left['source_id']} → {left['target_id']} 与 "
                            f"{right['source_id']} → {right['target_id']}"
                        )
    labelled_edges = [edge for edge in edges if edge.get("label_bbox")]
    for edge in labelled_edges:
        label_box = edge["label_bbox"]
        for node_id, node in nodes.items():
            node_box = node["bbox"]
            overlap_x = min(label_box["right"], node_box["right"]) - max(label_box["left"], node_box["left"])
            overlap_y = min(label_box["bottom"], node_box["bottom"]) - max(label_box["top"], node_box["top"])
            if overlap_x > 0.01 and overlap_y > 0.01:
                problems.append(f"连线标签遮挡节点：{edge['label_text']} / {node_id}")
    for left_index, left in enumerate(labelled_edges):
        for right in labelled_edges[left_index + 1 :]:
            a, b = left["label_bbox"], right["label_bbox"]
            overlap_x = min(a["right"], b["right"]) - max(a["left"], b["left"])
            overlap_y = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            if overlap_x > 0.01 and overlap_y > 0.01:
                problems.append(f"连线标签重叠：{left['label_text']} 与 {right['label_text']}")
    return list(dict.fromkeys(problems))
