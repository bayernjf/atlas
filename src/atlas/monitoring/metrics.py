"""基础监控告警：节点失败推断与指标聚合纯函数（契约 04 §5.13，06 §6.11）。"""

import math
from collections import OrderedDict
from typing import Any, Literal

from pydantic import BaseModel


class NodeResult(BaseModel):
    node_id: str
    node_type: str
    status: Literal["success", "failed"]
    error: str | None = None


def _node_failure(output: Any) -> str | None:
    """失败返回错误文本，成功返回 None。

    两形状：parallel/subgraph fail-safe 折叠 output.status=="failed"；
    tool_call ActionResult 数据化失败 output.result.status=="FAILED"。
    """
    if not isinstance(output, dict):
        return None
    if output.get("status") == "failed":
        return str(output.get("error") or "节点执行失败")
    result = output.get("result")
    if isinstance(result, dict) and result.get("status") == "FAILED":
        return str(result.get("message") or result.get("code") or "工具执行失败")
    return None


def extract_node_results(graph: dict[str, Any], outputs: dict[str, Any]) -> list[NodeResult]:
    """从运行终态 outputs 推断逐节点成败；node_id→type 取自图定义。"""
    node_types = {node.get("id"): node.get("type", "unknown") for node in graph.get("nodes", []) if node.get("id")}
    nodes: list[NodeResult] = []
    for node_id, output in outputs.items():
        if node_id not in node_types:
            continue
        error = _node_failure(output)
        nodes.append(
            NodeResult(
                node_id=node_id,
                node_type=node_types[node_id],
                status="failed" if error else "success",
                error=error,
            )
        )
    return nodes


def percentile(values: list[float], p: float) -> float | None:
    """nearest-rank 百分位（ceil(p/100*n)，1-based），空集 None。"""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(p / 100 * len(ordered))
    return ordered[max(1, rank) - 1]


def is_healthy(record: object) -> bool:
    return record.status == "completed" and all(node.status != "failed" for node in record.nodes)


def _stats(runs: list) -> dict[str, Any]:
    durations = [run.duration_ms for run in runs]
    total = len(runs)
    healthy = sum(1 for run in runs if is_healthy(run))
    return {
        "total": total,
        "healthy": healthy,
        "unhealthy": total - healthy,
        "success_rate": (healthy / total) if total else None,
        "p50": percentile(durations, 50),
        "p95": percentile(durations, 95),
    }


def summarize(runs: list) -> dict[str, Any]:
    """全局 + 按图分组指标 + 失败节点 Top（按 count 降序，count 同则末次时间晚者优先）。"""
    by_graph: OrderedDict[str, list] = OrderedDict()
    for run in runs:
        by_graph.setdefault(run.graph_id, []).append(run)
    per_graph = [{"graph_id": graph_id, **_stats(graph_runs)} for graph_id, graph_runs in by_graph.items()]

    failed_nodes: dict[str, dict[str, Any]] = {}
    for run in runs:
        for node in run.nodes:
            if node.status != "failed":
                continue
            entry = failed_nodes.get(node.node_id)
            if entry is None:
                failed_nodes[node.node_id] = {
                    "node_id": node.node_id,
                    "node_type": node.node_type,
                    "count": 1,
                    "last_error": node.error,
                    "last_seen": run.finished_at,
                }
            else:
                entry["count"] += 1
                entry["last_error"] = node.error
                entry["last_seen"] = run.finished_at
    top = sorted(failed_nodes.values(), key=lambda item: (item["count"], item["last_seen"]), reverse=True)

    return {**_stats(runs), "per_graph": per_graph, "failed_nodes": top}
