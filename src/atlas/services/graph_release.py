"""Graph release/rollout/debug helpers, extracted verbatim from api/main.py (Tier0 review P2).

Functions here are free of module-level state: every dependency (tenant
services, principal, the demo adapter registry and the secret provider that
main.py owns as process singletons) arrives as a parameter. HTTP-shaped
failures still raise HTTPException — the route contract (status codes and
422 detail shapes) must not change.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from atlas.graph.conditions import validate_expression
from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import compile_graph
from atlas.harness.registry import AdapterRegistry
from atlas.harness.runtime import build_runtime_registry
from atlas.iam.principals import Principal
from atlas.iam.registry import TenantServices
from atlas.routing import RolloutConfig, RolloutState, TriggerEvent


def tenant_graph_resolver(services: TenantServices):
    """subgraph 节点 graph_resolver（04 §5.7）：在当前租户 GraphStore 内按 id 解析，缺失抛 KeyError。"""

    def resolve(graph_id: str):
        # M6 钉版：subgraph graphId 可为 `graph-7@3`（先按 pin 版本，失败回退 latest 由 get 返回 None 触发 KeyError）
        ref_id, _, version = graph_id.partition("@")
        release_version = int(version) if version else None
        raw = services.graph_store.get(ref_id, release_version)
        if raw is None:
            raise KeyError(graph_id)
        return parse_graph(raw)

    return resolve


def runtime_registry(
    services: TenantServices,
    *,
    demo_registry: AdapterRegistry,
    secret_provider: Any,
) -> AdapterRegistry:
    """执行期适配器注册表：沿用全局 shop/http/database 适配器实例，
    message 适配器替换为当前租户消息服务（04 §5.14 分区；06 §6.12）。

    装配本体在 atlas.harness.runtime（MCP 的 atlas_list_adapters 锚同一张表）。
    `demo_registry`／`secret_provider` 由调用方传入（main 持有进程单例，测试可
    patch），本模块不读模块级状态。"""
    return build_runtime_registry(services, demo_registry, secret_provider)


def promote_compile(
    services: TenantServices,
    graph: dict[str, Any],
    *,
    graph_id: str,
    runtime_registry_of: Any,
) -> dict[str, Any]:
    """对草稿试编译：只回报，不写库（docs/128 AT-D4／AT-D5）。

    `runtime_registry_of` 是 `services -> AdapterRegistry` 的解析函数（main 的
    `_runtime_registry`，含进程级 demo registry 注入）。
    """
    try:
        compile_graph(
            parse_graph(graph),
            graph_id=graph_id,
            graph_resolver=tenant_graph_resolver(services),
            registry=runtime_registry_of(services),
        )
    except GraphValidationError as exc:
        return {
            "ok": False,
            "issues": list(exc.errors),
            "codes": list(exc.codes),
            "params": list(exc.params),
            "locations": list(exc.locations),
        }
    return {"ok": True, "issues": [], "codes": [], "params": [], "locations": []}


def load_graph_or_404(
    services: TenantServices, graph_id: str, release_version: int | None = None
):
    raw = services.graph_store.get(graph_id, release_version)
    if raw is None:
        raise HTTPException(status_code=404, detail=f"Graph 不存在：{graph_id}")
    return parse_graph(raw)


def graph_version(graph_id: str, release_version: int | None) -> str:
    """M10 graphVersion 标注（与 M6 钉版形态一致，SUBSCRIPT_KEY='@'）：
    发布版 `graphId@<releaseVersion>`（int），草稿/未发布 `graphId@draft`。"""
    if release_version is not None:
        return f"{graph_id}@{release_version}"
    return f"{graph_id}@draft"


def resolve_event_version(
    services: TenantServices, principal: Principal, graph_id: str, payload: dict[str, Any]
) -> tuple[int | None, int | None]:
    """M9 入站事件路由（03 `route_decision`）：返 (加载用 releaseVersion, RunRecord.resolved_version)。

    - 无 event：旧行为（手动 releaseVersion 或草稿），resolved_version 恒 None，零回归；
    - 带 event：经 RoutingStore 三段分桶解析钉住的发布版（tenant 取 Principal 不取 body）；
      event 与 releaseVersion 同传 422（语义冲突）；无任何发布版可路由 409。
    """
    event_raw = payload.get("event")
    if event_raw is None:
        return payload.get("releaseVersion"), None
    if payload.get("releaseVersion") is not None:
        raise HTTPException(status_code=422, detail="event 与 releaseVersion 不可同时指定（入站路由与手动钉版互斥）")
    if payload.get("debug") is not None:
        raise HTTPException(status_code=422, detail="入站事件运行不支持单步调试（debug 仅用于草稿手动运行）")
    if not isinstance(event_raw, dict):
        raise HTTPException(status_code=422, detail="event 必须是对象：{channel?, payload?}")
    try:
        event = TriggerEvent.model_validate(event_raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"event 非法：{exc.errors()[0]['msg']}") from exc
    version, _segment = services.routing_store.resolve(
        graph_id, tenant=principal.tenant_id, event=event
    )
    if version is None:
        raise HTTPException(status_code=409, detail="图尚未发布，入站事件无版本可路由")
    return version, version


def rollout_config_or_422(body: Any) -> RolloutConfig:
    """PUT rollout body → RolloutConfig；非法形状聚合为中文 422（不泄漏 pydantic 英文堆栈）。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="rollout 配置必须是 JSON 对象")
    try:
        return RolloutConfig.model_validate(body)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            messages.append(f"{loc}：{error['msg']}" if loc else error["msg"])
        raise HTTPException(status_code=422, detail="灰度配置非法：" + "；".join(messages)) from exc


def rollout_projection(state: RolloutState) -> dict[str, Any]:
    """GET rollout 投影（camelCase；config 未配置为 null）。"""
    return {
        "graphId": state.graph_id,
        "status": state.status,
        "config": state.config.model_dump() if state.config is not None else None,
        "stable": state.stable,
        "candidate": state.candidate,
        "startedAt": state.started_at,
        "rolledBackAt": state.rolled_back_at,
        "rollbackReason": state.rollback_reason,
        "rollbackActor": state.rollback_actor,
        "traffic": state.traffic,
    }


def validate_debug(graph, debug: Any) -> list[dict[str, Any]]:
    """校验 /run/stream 的 debug.breakpoints，返回规范化列表；错误聚合成中文 422。"""
    if not isinstance(debug, dict):
        raise HTTPException(status_code=422, detail="debug 必须为对象：{breakpoints: [...]}")
    raw_points = debug.get("breakpoints", [])
    if not isinstance(raw_points, list):
        raise HTTPException(status_code=422, detail="debug.breakpoints 必须为数组")
    node_ids = {node.id for node in graph.nodes}
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, point in enumerate(raw_points):
        if not isinstance(point, dict) or not isinstance(point.get("node_id"), str):
            errors.append(f"第 {index + 1} 个断点缺少 node_id 字符串")
            continue
        node_id = point["node_id"]
        if node_id not in node_ids:
            errors.append(f"断点节点不存在：{node_id}")
        expression = point.get("expression")
        if expression is not None:
            if not isinstance(expression, str):
                errors.append(f"断点 {node_id} 的 expression 必须为字符串")
                continue
            if expression.strip():
                errors.extend(
                    f"断点 {node_id} 表达式：{message}"
                    for message in validate_expression(expression)
                )
        # B 包（docs/27 §4.2）：hitCount 正整数、logMessage 字符串（非空即日志断点）。
        hit_count = point.get("hitCount")
        if hit_count is not None and (
            not isinstance(hit_count, int)
            or isinstance(hit_count, bool)
            or hit_count < 1
        ):
            errors.append(f"断点 {node_id} 的 hitCount 必须为正整数")
        log_message = point.get("logMessage")
        if log_message is not None and not isinstance(log_message, str):
            errors.append(f"断点 {node_id} 的 logMessage 必须为字符串")
        # docs/28 §3.2：onException 布尔（异常断点）；bool 是 int 子类须先判 bool。
        on_exception = point.get("onException", False)
        if not isinstance(on_exception, bool):
            errors.append(f"断点 {node_id} 的 onException 必须为布尔值")
            on_exception = False
        normalized.append(
            {
                "node_id": node_id,
                "expression": expression,
                "hitCount": hit_count if isinstance(hit_count, int) and hit_count > 0 else None,
                "logMessage": (
                    log_message
                    if isinstance(log_message, str) and log_message.strip()
                    else None
                ),
                "onException": on_exception,
            }
        )
    if errors:
        raise HTTPException(status_code=422, detail="；".join(errors))
    return normalized


def tool_call_from_event(event: dict[str, Any]) -> dict[str, Any]:
    """docs/28 §4.1 ⑧：tool_metric SSE/采集事件 → RunRecord.tool_calls 载荷（dict，pydantic 转模型）。"""
    return {
        "node_id": event["node_id"],
        "tool": event.get("tool", ""),
        "duration_ms": float(event.get("duration_ms", 0.0)),
        "action_status": event["action_status"],
        "error_code": event.get("error_code"),
    }
