"""把录制用例升格成可编辑的 Graph 草稿（docs/128，打包 AT＝D68）。

一句话：录制的产物是"这张图 ＋ 这一份入参"，promote 做的事是把写死在节点参数里的
业务字面量换成指向入参的 payload 引用，于是同一条流程能换一单继续跑。

两个形状来自读码，不是设计偏好：
- 入参流经的唯一文本位是 `tool_call.config.params`（`loader.py:2907` 对它做 interpolate），
  所以替换按**词边界**在文本里定位，不是按 path 换标量；
- 引用形态照模板图既有写法 `{{trigger-1.context.payload.<key>}}`，不发明新语法。
"""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel, Field

from atlas.recording.cases import RecordingCase

# 业务入参真正流经的位置（AT-D3）：节点类型 → 会被 interpolate 的文本配置键。
PARAM_TEXT_KEYS: dict[str, tuple[str, ...]] = {"tool_call": ("params",)}

# 词边界字符集。含 `.` 与 `_` 是刻意的：`amount` 不许命中 `min_amount` 的尾巴，
# `299` 不许命中 `1299` 的子串——短值吞长文是本模块唯一的致命错误形态。
_WORD_EDGE = "0-9A-Za-z_."

# 1 字符的值（"1"、"2"）不参与匹配：它会淹没整段文本，勾起来没有意义。
_MIN_VALUE_LEN = 2


class PromoteRequestError(ValueError):
    """请求接受了机器没提议过的替换（端点折成 422）。"""

    def __init__(self, message: str, *, unknown: list[str]) -> None:
        super().__init__(message)
        self.unknown = unknown


class PromoteRequest(BaseModel):
    """promote 请求体（docs/128 §3.2）。`accept` 是候选 **id**，不是 path。"""

    accept: list[str] = Field(default_factory=list)
    pruneToPath: bool = False
    name: str | None = Field(default=None, min_length=1, max_length=100)


def scalar_form(value: Any) -> str | None:
    """把入参值转成可在配置文本里匹配的字符串形态；不可安全匹配的回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        text = value.strip()
        return text if len(text) >= _MIN_VALUE_LEN else None
    return None


def trigger_node_id(graph: dict[str, Any]) -> str | None:
    for node in graph.get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "trigger":
            node_id = node.get("id")
            if isinstance(node_id, str) and node_id:
                return node_id
    return None


def payload_reference(trigger_id: str, key: str) -> str:
    """运行入参在图里的既有引用形态（模板图与 loader 一致）。"""
    return "{{" + f"{trigger_id}.context.payload.{key}" + "}}"


def _nodes(graph: dict[str, Any]) -> list[Any]:
    nodes = graph.get("nodes")
    return nodes if isinstance(nodes, list) else []


def _text_positions(graph: dict[str, Any]) -> list[tuple[int, str, str, str, str]]:
    """所有文本型配置位：(节点下标, 节点 id, 配置键, 节点类型, 文本)。"""
    found: list[tuple[int, str, str, str, str]] = []
    for index, node in enumerate(_nodes(graph)):
        if not isinstance(node, dict):
            continue
        config = node.get("config")
        if not isinstance(config, dict):
            continue
        node_type = str(node.get("type") or "")
        node_id = str(node.get("id") or f"node-{index}")
        for key, value in config.items():
            if isinstance(value, str) and value:
                found.append((index, node_id, str(key), node_type, value))
    return found


def _is_param_text_position(node_type: str, key: str) -> bool:
    return key in PARAM_TEXT_KEYS.get(node_type, ())


def _spans(text: str, needle: str) -> list[int]:
    pattern = rf"(?<![{_WORD_EDGE}]){re.escape(needle)}(?![{_WORD_EDGE}])"
    return [m.start() for m in re.finditer(pattern, text)]


def _excerpt(text: str, start: int, needle: str) -> str:
    """候选证据片段：让运营在勾选之前看清到底是哪一处。"""
    left = max(0, start - 12)
    right = min(len(text), start + len(needle) + 12)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(text) else ""
    return f"{prefix}{text[left:right]}{suffix}"


def inputs_declaration(case: RecordingCase) -> list[dict[str, str]]:
    """草稿的入参声明；类型只作展示（图里落的是 payload 引用，不是变量）。"""
    rows: list[dict[str, str]] = []
    for key, value in (case.inputs or {}).items():
        if isinstance(value, bool):
            kind = "boolean"
        elif isinstance(value, int):
            kind = "integer"
        elif isinstance(value, float):
            kind = "number"
        elif value is None:
            kind = "null"
        else:
            kind = "string"
        rows.append({"key": str(key), "type": kind})
    return rows


def find_candidates(case: RecordingCase) -> dict[str, Any]:
    """扫描冻结快照 → substitutions／ambiguous／skipped 三表（只读，不改任何东西）。"""
    graph = case.graph if isinstance(case.graph, dict) else {}
    trigger_id = trigger_node_id(graph)
    needles = {key: scalar_form(value) for key, value in (case.inputs or {}).items()}
    needles = {key: value for key, value in needles.items() if value}

    substitutions: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for index, node_id, key, node_type, text in _text_positions(graph):
        by_start: dict[int, list[str]] = {}
        length_at_start: dict[int, int] = {}
        for input_key, needle in needles.items():
            for start in _spans(text, needle):
                by_start.setdefault(start, []).append(input_key)
                length_at_start[start] = len(needle)
        if not by_start:
            continue

        allowed = _is_param_text_position(node_type, key)
        path = f"nodes[{index}].config.{key}"
        for occurrence, start in enumerate(sorted(by_start), start=1):
            candidate_keys = sorted(set(by_start[start]))
            entry_id = f"{path}#{occurrence}"
            value = text[start : start + length_at_start[start]]
            excerpt = _excerpt(text, start, value)
            base = {
                "id": entry_id,
                "path": path,
                "nodeId": node_id,
                "occurrence": occurrence,
                "value": value,
                "excerpt": excerpt,
            }
            if not allowed:
                # 非替换位优先归 skipped，哪怕它同时撞上同值多键：这里没有"换哪个"可判，
                # 机器也不该让人去勾选一个不允许的位置。
                skipped.append(
                    {
                        **base,
                        "candidates": candidate_keys,
                        "inputKey": candidate_keys[0] if len(candidate_keys) == 1 else None,
                        "reason": "非替换位（白名单＝tool_call.config.params 文本）",
                    }
                )
            elif trigger_id is None:
                skipped.append(
                    {
                        **base,
                        "candidates": candidate_keys,
                        "inputKey": candidate_keys[0] if len(candidate_keys) == 1 else None,
                        "reason": "图里没有 trigger 节点，无 payload 引用可指向",
                    }
                )
            elif len(candidate_keys) > 1:
                ambiguous.append(
                    {
                        **base,
                        "candidates": candidate_keys,
                        "reason": "同值多键，机器不选",
                    }
                )
            else:
                input_key = candidate_keys[0]
                substitutions.append(
                    {
                        "id": entry_id,
                        "path": path,
                        "nodeId": node_id,
                        "occurrence": occurrence,
                        "from": value,
                        "to": payload_reference(trigger_id, input_key),
                        "inputKey": input_key,
                        "excerpt": excerpt,
                    }
                )

    return {
        "triggerId": trigger_id,
        "inputsDeclaration": inputs_declaration(case),
        "substitutions": substitutions,
        "ambiguous": ambiguous,
        "skipped": skipped,
    }


def plan_promotion(case: RecordingCase) -> dict[str, Any]:
    """plan 端点响应体：候选表 ＋ 原快照回显 ＋ 剪枝预览 ＋ 建议名。"""
    graph = case.graph if isinstance(case.graph, dict) else {}
    candidates = find_candidates(case)
    node_ids = [
        str(node.get("id")) for node in _nodes(graph) if isinstance(node, dict) and node.get("id")
    ]
    walked = {step.node_id for step in case.steps}
    return {
        "caseId": case.id,
        "suggestedName": f"{case.name}（来自用例 {case.id}）",
        "graph": copy.deepcopy(graph),
        "triggerId": candidates["triggerId"],
        "inputsDeclaration": candidates["inputsDeclaration"],
        "substitutions": candidates["substitutions"],
        "ambiguous": candidates["ambiguous"],
        "skipped": candidates["skipped"],
        "prunePreview": {
            "keptNodeIds": [node_id for node_id in node_ids if node_id in walked],
            "droppedNodeIds": [node_id for node_id in node_ids if node_id not in walked],
        },
    }


def _split_path(path: str) -> tuple[int | None, str | None]:
    match = re.fullmatch(r"nodes\[(\d+)\]\.config\.(.+)", path)
    if not match:
        return None, None
    return int(match.group(1)), match.group(2)


def _apply_text_replacements(text: str, spans: list[tuple[int, int, str]]) -> str:
    """同一位置多处替换：按下标从后往前，避免先做的替换把后面的偏移挪走。"""
    result = text
    for start, end, replacement in sorted(spans, key=lambda item: item[0], reverse=True):
        result = result[:start] + replacement + result[end:]
    return result


def _prune_to_walked(graph: dict[str, Any], walked: set[str]) -> dict[str, Any]:
    """只留走过的节点与其间连线；被裁掉的分支目标留给 DSL 校验去拒（AT-D5）。"""
    nodes = [node for node in _nodes(graph) if isinstance(node, dict) and node.get("id") in walked]
    kept_ids = {str(node.get("id")) for node in nodes}
    edges = [
        edge
        for edge in (graph.get("edges") or [])
        if isinstance(edge, dict)
        and edge.get("source") in kept_ids
        and edge.get("target") in kept_ids
    ]
    return {**graph, "nodes": nodes, "edges": edges}


def apply_promotion(
    case: RecordingCase,
    *,
    accept: list[str],
    prune_to_path: bool = False,
) -> dict[str, Any]:
    """应用人勾选的替换集合，返回草稿。`accept` 里出现机器没提议过的 id ⇒ 整请求拒（R4）。"""
    candidates = find_candidates(case)
    known = {item["id"]: item for item in candidates["substitutions"]}
    unknown = sorted({item for item in accept if item not in known})
    if unknown:
        raise PromoteRequestError(
            "接受的替换不在机器提议的候选表里：" + "、".join(unknown), unknown=unknown
        )

    graph = copy.deepcopy(case.graph) if isinstance(case.graph, dict) else {}
    nodes = _nodes(graph)
    # 偏移不存下来而是重算：plan 与 promote 之间图不会变（录制事实不可改，
    # `RecordingUpdateRequest` 只允许动 name/inputs），但同值多次出现时只有"第 N 次"能定位。
    by_path: dict[str, list[tuple[int, int, str]]] = {}
    for entry_id in accept:
        item = known[entry_id]
        node_index, key = _split_path(item["path"])
        if node_index is None or key is None or node_index >= len(nodes):
            continue
        node = nodes[node_index]
        config = node.get("config") if isinstance(node, dict) else None
        text = config.get(key) if isinstance(config, dict) else None
        if not isinstance(text, str):
            continue
        starts = _spans(text, item["from"])
        index = item["occurrence"] - 1
        if index >= len(starts):
            continue
        start = starts[index]
        by_path.setdefault(item["path"], []).append((start, start + len(item["from"]), item["to"]))

    applied: list[dict[str, Any]] = []
    for path, spans in by_path.items():
        node_index, key = _split_path(path)
        if node_index is None or key is None or node_index >= len(nodes):
            continue
        node = nodes[node_index]
        if not isinstance(node, dict):
            continue
        config = node.get("config")
        if not isinstance(config, dict) or not isinstance(config.get(key), str):
            continue
        config[key] = _apply_text_replacements(config[key], spans)
        applied.extend(
            sorted(
                (dict(known[item_id]) for item_id in accept if known[item_id]["path"] == path),
                key=lambda item: item["occurrence"],
            )
        )

    if prune_to_path:
        graph = _prune_to_walked(graph, {step.node_id for step in case.steps})
    return {
        "graph": graph,
        "applied": sorted(applied, key=lambda item: (item["path"], item["occurrence"])),
        "candidates": candidates,
    }
