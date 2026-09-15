"""录制回放：易变值归一化、审批决策预置、操作序列与逐节点比对（纯函数）。

契约 04 §5.11、运行时边界 06 §6.9。
"""

from collections.abc import Callable
from typing import Any

from .cases import RecordStep

_VOLATILE_KEYS = ("token", "sent_at")


def normalize(
    value: Any, tool: str | None = None, node_type: str | None = None
) -> Any:
    """深拷贝后递归剔除运行时易变值。

    - 任意层级删除 ``token`` 与 ``sent_at``；
    - 同层含 ``sent_at`` 的 dict（message/send 记录）额外删除 uuid ``id``；
    - tool == "http/request" 的节点产出删除 ``result.headers.date``；
    - human_approval 产出删除 ``resolvedBy``（回放经 inputs.approvals 预置，
      决策来源 input/timeout/human 属运行时来源，不是业务结果）；
    - trigger 产出删除 ``context.payload.approvals``（预置通道随载荷回显）。
    业务键（order_id 等）不受影响。
    """
    if node_type == "human_approval" and isinstance(value, dict):
        value = {k: v for k, v in value.items() if k != "resolvedBy"}
    elif node_type == "trigger" and isinstance(value, dict):
        context = value.get("context")
        if isinstance(context, dict) and isinstance(context.get("payload"), dict):
            payload = {k: v for k, v in context["payload"].items() if k != "approvals"}
            value = {**value, "context": {**context, "payload": payload}}
    return _normalize(value, tool)


def _normalize(value: Any, tool: str | None) -> Any:
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        is_message_record = "sent_at" in value
        if tool == "http/request":
            result = value.get("result")
            if isinstance(result, dict) and isinstance(result.get("headers"), dict):
                result = {**result, "headers": {k: v for k, v in result["headers"].items() if k.lower() != "date"}}
                value = {**value, "result": result}
        for key, item in value.items():
            if key in _VOLATILE_KEYS:
                continue
            if is_message_record and key == "id":
                continue
            normalized[key] = _normalize(item, None)
        return normalized
    if isinstance(value, list):
        return [_normalize(item, None) for item in value]
    return value


def preset_approvals(steps: list[RecordStep]) -> dict[str, str]:
    """从 baseline human_approval 步骤抽取 {node_id: decision}，供回放 inputs.approvals 预置。"""
    presets: dict[str, str] = {}
    for step in steps:
        if step.node_type == "human_approval":
            decision = step.output.get("decision")
            if isinstance(decision, str):
                presets[step.node_id] = decision
    return presets


def dedupe_steps(steps: list[RecordStep]) -> list[RecordStep]:
    """node_id 去重保末（parallel 两次 node_end、loop 重访）；末次出现的位置为准。"""
    by_id: dict[str, RecordStep] = {}
    order: list[str] = []
    for step in steps:
        if step.node_id in by_id:
            order.remove(step.node_id)
        by_id[step.node_id] = step
        order.append(step.node_id)
    return [by_id[node_id] for node_id in order]


def _diff_top_level(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return sorted(key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key))


def compare(
    baseline: list[RecordStep],
    replay_steps: list[RecordStep],
    *,
    tools_by_node: dict[str, str | None],
    baseline_status: str,
    replay_status: str,
) -> dict[str, Any]:
    """序列先比对（多走/漏走节点 = 分支漂移），再逐节点归一化深等；产出 ReplayReport。"""
    base_steps = dedupe_steps(baseline)
    replay_steps = dedupe_steps(replay_steps)
    replay_by_id = {step.node_id: step for step in replay_steps}
    base_ids = {step.node_id for step in base_steps}

    rows: list[dict[str, Any]] = []
    all_match = baseline_status == replay_status

    for step in base_steps:
        replayed = replay_by_id.get(step.node_id)
        if replayed is None:
            rows.append({"node_id": step.node_id, "match": False, "note": "回放缺少该节点（操作序列不一致，疑似分支漂移）"})
            all_match = False
            continue
        tool = tools_by_node.get(step.node_id)
        expected = normalize(step.output, tool, step.node_type)
        actual = normalize(replayed.output, tool, step.node_type)
        notes: list[str] = []
        if step.node_type != replayed.node_type:
            notes.append(f"节点类型不一致（baseline={step.node_type}，replay={replayed.node_type}）")
            all_match = False
        if expected == actual:
            rows.append({"node_id": step.node_id, "match": True, "note": "；".join(notes) or "一致"})
        else:
            diff_keys = _diff_top_level(expected, actual)
            notes.append(f"归一化后产出不一致，差异顶层键：{', '.join(diff_keys) if diff_keys else '（嵌套差异）'}")
            rows.append({"node_id": step.node_id, "match": False, "note": "；".join(notes), "diff_keys": diff_keys})
            all_match = False

    for step in replay_steps:
        if step.node_id not in base_ids:
            rows.append({"node_id": step.node_id, "match": False, "note": "回放多出该节点（操作序列不一致，疑似分支漂移）"})
            all_match = False

    return {
        "matches": all_match,
        "baseline_status": baseline_status,
        "replay_status": replay_status,
        "steps": rows,
    }


def collect_steps() -> tuple[Callable[[dict[str, Any]], None], Callable[[], list[RecordStep]]]:
    """返回 (emit, take_steps)：emit 接 run_graph 事件，take 取去重保末的 node_end 步骤。"""
    collected: list[RecordStep] = []

    def emit(event: dict[str, Any]) -> None:
        if event.get("type") == "node_end":
            collected.append(
                RecordStep(
                    node_id=event["node_id"],
                    node_type=event["node_type"],
                    output=event.get("output") or {},
                )
            )

    def take_steps() -> list[RecordStep]:
        return dedupe_steps(collected)

    return emit, take_steps
