"""业务结果提取与业务指标聚合（M9，金融灰度门控信号源；契约 03 `business_metrics`、04 §5.13 末）。

业务结果**以 shop 终态为准，不以 AI 中间决策为准**：
- tool_call 节点 output.result.status == "refunded" → 自动退款；
- tool_call 节点 output.result.status == "human_review" → 人工升级。

demo shop 的 execute_refund 不回退款金额（只回 order_id/status），故 refunded_amount
回退 expected_amount（trigger payload.amount，全额退口径），沙盘金额差异率恒 0：
门控机制与阈值先行、真实金额字段随正式 shop 接入（03 `business_metrics` 注记）。
纯函数、无 IO、零新依赖。
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from pydantic import BaseModel

_REFUNDED = "refunded"
_HUMAN_REVIEW = "human_review"


class BusinessOutcome(BaseModel):
    """单次运行的业务结果（RunRecord.business；无业务结果的运行整段为 None）。"""

    auto_refunded: bool = False
    manual_escalated: bool = False
    refunded_amount: float | None = None
    expected_amount: float | None = None
    amount_diff: bool = False


def _number(value: Any) -> float | None:
    """金额取数：bool 不算金额，int/float 接受（其余 None）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _node_types(graph: dict[str, Any]) -> dict[str, str]:
    return {
        node.get("id"): node.get("type", "unknown")
        for node in graph.get("nodes", [])
        if node.get("id")
    }


def extract_business(
    graph: dict[str, Any],
    outputs: dict[str, Any],
    *,
    event_payload: dict[str, Any] | None = None,
) -> BusinessOutcome | None:
    """从运行终态 outputs 提取业务结果；既无退款也无人工升级时返 None（不进业务率分母）。

    expected_amount 取 trigger 节点产出 context.payload.amount，缺失时回退入站
    event.payload.amount（03 `business_metrics`）。
    """
    types = _node_types(graph)

    expected_amount: float | None = None
    trigger_id = next((node_id for node_id, node_type in types.items() if node_type == "trigger"), None)
    if trigger_id is not None:
        trigger_output = outputs.get(trigger_id)
        if isinstance(trigger_output, dict):
            context = trigger_output.get("context")
            if isinstance(context, dict) and isinstance(context.get("payload"), dict):
                expected_amount = _number(context["payload"].get("amount"))
    if expected_amount is None and isinstance(event_payload, dict):
        expected_amount = _number(event_payload.get("amount"))

    auto_refunded = False
    manual_escalated = False
    refunded_amount: float | None = None
    for node_id, output in outputs.items():
        if types.get(node_id) != "tool_call" or not isinstance(output, dict):
            continue
        result = output.get("result")
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        if status == _REFUNDED:
            auto_refunded = True
            # demo shop 不回金额 → None，下方回退 expected（全额退口径）
            refunded_amount = _number(result.get("amount"))
        elif status == _HUMAN_REVIEW:
            manual_escalated = True

    if not auto_refunded and not manual_escalated:
        return None

    if refunded_amount is None:
        refunded_amount = expected_amount
    amount_diff = (
        expected_amount is not None
        and refunded_amount is not None
        and expected_amount != refunded_amount
    )
    return BusinessOutcome(
        auto_refunded=auto_refunded,
        manual_escalated=manual_escalated,
        refunded_amount=refunded_amount,
        expected_amount=expected_amount,
        amount_diff=amount_diff,
    )


def _rates(runs: list[Any]) -> dict[str, Any]:
    """对一组 RunRecord（业务结果非空）算三率；样本 0 全 null。"""
    business_runs = [run for run in runs if getattr(run, "business", None) is not None]
    samples = len(business_runs)
    if samples == 0:
        return {
            "samples": 0,
            "auto_refund_rate": None,
            "manual_escalation_rate": None,
            "refund_amount_diff_rate": None,
        }
    auto = sum(1 for run in business_runs if run.business.auto_refunded)
    manual = sum(1 for run in business_runs if run.business.manual_escalated)
    diff = sum(1 for run in business_runs if run.business.amount_diff)
    return {
        "samples": samples,
        "auto_refund_rate": auto / samples,
        "manual_escalation_rate": manual / samples,
        "refund_amount_diff_rate": diff / samples,
    }


def summarize_business(runs: list[Any]) -> dict[str, Any]:
    """业务指标段：全局 + per_graph + per_version（分母＝有业务结果 run，样本 0 为 null）。"""
    global_rates = _rates(runs)

    by_graph: OrderedDict[str, list[Any]] = OrderedDict()
    by_version: OrderedDict[tuple[str, Any], list[Any]] = OrderedDict()
    for run in runs:
        if getattr(run, "business", None) is None:
            continue
        by_graph.setdefault(run.graph_id, []).append(run)
        key = (run.graph_id, run.resolved_version)
        by_version.setdefault(key, []).append(run)

    per_graph = [
        {"graph_id": graph_id, **_rates(graph_runs)}
        for graph_id, graph_runs in by_graph.items()
    ]
    per_version = [
        {"graph_id": graph_id, "resolved_version": resolved_version, **_rates(version_runs)}
        for (graph_id, resolved_version), version_runs in by_version.items()
    ]
    return {
        "auto_refund_rate": global_rates["auto_refund_rate"],
        "manual_escalation_rate": global_rates["manual_escalation_rate"],
        "refund_amount_diff_rate": global_rates["refund_amount_diff_rate"],
        "per_graph": per_graph,
        "per_version": per_version,
    }
