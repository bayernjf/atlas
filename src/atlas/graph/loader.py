"""DSL → LangGraph 编译器（08 §7.1 W7-W8；W9-W10 接入真实决策与工具）。

将编辑器 Graph JSON（docs/04 §5.2 node_schema）编译为 LangGraph
StateGraph：每个 DSL 节点成为一个图节点，边按 DSL 原样装配，
无前驱节点接 START、无后继节点接 END。

W9-W10 执行器：
- trigger：webhook/手动触发的载荷进入节点产出 context.payload；
- ai_decision：经 DecisionClient 决策退款动作（LiteLLM，缺 key 时
  规则兜底，见 `atlas.llm.decision`），提示词运行时渲染仅用于展示；
- tool_call：经适配器注册表解析 `<adapter>/<capability>` 调用；
  退款 Demo 的 shop/process_refund 按上游决策路由到退款/人工审批。
依赖均通过参数注入，默认装配 Demo 退款链路；事件回调供运行进度推送。
"""

from __future__ import annotations

import operator
import re
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from atlas.harness.base import ActionRequest, ActionStatus
from atlas.harness.registry import AdapterRegistry
from atlas.llm.decision import get_decision_client
from atlas.shop.adapter import ShopHarnessAdapter
from .conditions import ConditionEvalError, evaluate_expression
from .dsl import GraphDSL, NodeDSL

_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_PATH_SEGMENT_RE = re.compile(r"[^.[\]]+|\[\d+\]")

EventCallback = Callable[[dict[str, Any]], None]


class GraphState(TypedDict):
    variables: dict
    outputs: dict[str, dict[str, Any]]
    messages: Annotated[list[str], operator.add]
    status: str


def interpolate(template: str, context: dict[str, Any]) -> str:
    """渲染 04 §6.3 {{路径}}；路径缺失时占位符原样保留（与前端一致）。"""

    def replace(match: re.Match[str]) -> str:
        value = resolve_path(match.group(1), context)
        return match.group(0) if value is None else str(value)

    return _TEMPLATE_RE.sub(replace, template)


def resolve_path(path: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for segment in _PATH_SEGMENT_RE.findall(path):
        if segment.startswith("["):
            index = int(segment[1:-1])
            if not isinstance(current, list) or index >= len(current):
                return None
            current = current[index]
        else:
            if not isinstance(current, dict) or segment not in current:
                return None
            current = current[segment]
    return current


def build_demo_registry() -> AdapterRegistry:
    """Demo 默认适配器注册表：shop 适配器授予 read/write/financial（仅 Demo）。"""
    registry = AdapterRegistry()
    permissions = {"read", "write", "delete", "financial"}
    registry.register(ShopHarnessAdapter(granted_permissions=permissions))
    return registry


def _seed_variables(graph: GraphDSL) -> dict[str, Any]:
    return {"global": {variable.name: variable.value for variable in graph.variables}}


def _find_trigger(graph: GraphDSL) -> NodeDSL | None:
    return next((node for node in graph.nodes if node.type == "trigger"), None)


def _first_decision(outputs: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for output in outputs.values():
        decision = output.get("decision")
        if isinstance(decision, dict):
            return decision
    return None


def _make_executor(
    node: NodeDSL,
    *,
    trigger_payload: dict[str, Any],
    decision_client: Any,
    registry: AdapterRegistry | None,
    emit: EventCallback,
):
    def execute(state: GraphState) -> dict:
        emit({"type": "node_start", "node_id": node.id, "node_type": node.type})
        context = {"global": state["variables"].get("global", {}), **state["outputs"]}

        if node.type == "trigger":
            output = {
                "context": {
                    "triggerType": node.config.get("triggerType", "manual"),
                    "cron": node.config.get("cron", ""),
                    "webhookUrl": node.config.get("webhookUrl", ""),
                    "payload": trigger_payload,
                }
            }
            message = f"{node.id}({node.type}): executed"
        elif node.type == "ai_decision":
            payload = trigger_payload or {}
            prompt = interpolate(node.config.get("promptTemplate", ""), context)
            try:
                limit = float(context["global"].get("approval_limit", 500))
            except (TypeError, ValueError):
                limit = 500.0
            result = decision_client.decide_refund(
                reason=str(payload.get("reason", "")),
                amount=float(payload.get("amount", 0)),
                limit=limit,
            )
            output = {"decision": result, "prompt_rendered": prompt}
            message = f"{node.id}({node.type}): executed"
        elif node.type == "condition":
            output = _execute_condition(node, state, context)
            message = f"{node.id}: branch={output['branch']} → {output['target']}"
        else:
            output = _execute_tool(node, context, registry)
            message = f"{node.id}({node.type}): executed"

        outputs = {**state["outputs"], node.id: output}
        emit({"type": "node_end", "node_id": node.id, "node_type": node.type, "output": output})
        return {
            "variables": state["variables"],
            "outputs": outputs,
            "status": "running",
            "messages": [message],
        }

    return execute


def _execute_condition(node: NodeDSL, state: GraphState, context: dict[str, Any]) -> dict[str, Any]:
    """按 branches 顺序短路求值（04 §5.2）；异常 fail-safe 走 defaultTarget。"""
    evaluation: list[dict[str, Any]] = []
    errors: list[str] = []
    target: str | None = None
    branch = "__default__"
    for item in node.config.get("branches", []):
        label, expression = item["label"], item["expression"]
        try:
            result = evaluate_expression(expression, context)
        except ConditionEvalError as exc:
            errors.append(f"分支 {label}：{exc}")
            evaluation.append({"label": label, "expression": expression, "result": None})
            continue
        if not isinstance(result, bool):
            errors.append(f"分支 {label}：表达式结果必须是布尔值，实际为 {type(result).__name__}")
            evaluation.append({"label": label, "expression": expression, "result": None})
            continue
        evaluation.append({"label": label, "expression": expression, "result": result})
        if result:
            branch, target = label, item["target"]
            break
    if target is None:
        target = node.config["defaultTarget"]
    return {
        "branch": branch,
        "target": target,
        "evaluation": evaluation,
        "expression_errors": errors,
    }


def _execute_tool(
    node: NodeDSL, context: dict[str, Any], registry: AdapterRegistry | None
) -> dict[str, Any]:
    tool_name = node.config.get("tool", "")
    params_text = interpolate(node.config.get("params", ""), context)
    if "/" not in tool_name or registry is None:
        return {"result": {"status": "SIMULATED", "tool": tool_name}, "params_rendered": params_text}

    adapter_id, capability_name = tool_name.split("/", 1)
    try:
        adapter = registry.get(adapter_id)
    except KeyError:
        return {"result": {"status": "FAILED", "error": f"适配器未注册：{adapter_id}"}}

    trigger_payload: dict[str, Any] = {}
    for output in context.values():
        candidate = output.get("context", {}).get("payload") if isinstance(output, dict) else None
        if isinstance(candidate, dict):
            trigger_payload = candidate
            break
    decision = _first_decision(context)

    parameters: dict[str, Any] = {}
    if capability_name == "process_refund":
        if decision is None:
            return {"result": {"status": "FAILED", "error": "process_refund 缺少上游 AI 决策"}}
        parameters = {
            "order_id": trigger_payload.get("order_id", ""),
            "action": decision["action"],
            "note": decision.get("reason", ""),
        }
    elif capability_name in ("execute_refund", "request_human_approval"):
        parameters = {"order_id": trigger_payload.get("order_id", ""), "note": params_text}
    elif capability_name == "login":
        # Demo 平台固定账号；真实渠道凭据走环境变量与凭据库（Phase 2）
        parameters = {"username": "demo", "password": "demo"}
    elif capability_name in {"list_pending_refunds"}:
        parameters = {}
    else:
        parameters["note"] = params_text

    result = adapter.execute(
        ActionRequest(capability_name=capability_name, parameters=parameters)
    )
    if result.status == ActionStatus.SUCCESS:
        output: dict[str, Any] = {"result": result.output, "action_status": result.status.value}
    else:
        error = result.error
        output = {
            "result": {
                "status": "FAILED",
                "code": error.code if error else "UNKNOWN",
                "message": error.message if error else "",
            },
            "action_status": result.status.value,
        }
    return output


def compile_graph(
    graph: GraphDSL,
    *,
    decision_client: Any | None = None,
    registry: AdapterRegistry | None = None,
    emit: EventCallback | None = None,
    trigger_payload: dict[str, Any] | None = None,
):
    """校验由 parse_graph 完成；本函数只负责装配并返回 compiled graph。"""
    decision_client = decision_client or get_decision_client()
    registry = registry if registry is not None else build_demo_registry()
    noop_emit: EventCallback = lambda event: None
    emit = emit or noop_emit
    payload = trigger_payload or {}

    builder = StateGraph(GraphState)
    for node in graph.nodes:
        builder.add_node(
            node.id,
            _make_executor(
                node,
                trigger_payload=payload,
                decision_client=decision_client,
                registry=registry,
                emit=emit,
            ),
        )

    incoming = {edge.target for edge in graph.edges}
    for node in graph.nodes:
        if node.id not in incoming:
            builder.add_edge(START, node.id)

    condition_ids = {node.id for node in graph.nodes if node.type == "condition"}

    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
        if edge.source not in condition_ids:
            # condition 出边全部改走 conditional edges，混用会导致双路激活
            builder.add_edge(edge.source, edge.target)

    for condition_id in condition_ids:
        targets = outgoing.get(condition_id, [])

        def route(state: GraphState, cid: str = condition_id) -> str:
            return state["outputs"][cid]["target"]

        builder.add_conditional_edges(condition_id, route, {target: target for target in targets})

    for node in graph.nodes:
        if node.id not in outgoing:
            builder.add_edge(node.id, END)

    return builder.compile()


def initial_state(graph: GraphDSL, *, inputs: dict[str, Any] | None = None) -> GraphState:
    # inputs 同时承担两个角色（W9-W10）：同名键覆盖全局变量（W7-W8 语义），
    # 且整体作为 webhook 载荷进入 trigger 节点 context.payload。
    variables = _seed_variables(graph)
    if inputs:
        variables["global"] = {**variables.get("global", {}), **inputs}
    return {"variables": variables, "outputs": {}, "messages": [], "status": "running"}


def run_graph(
    graph: GraphDSL,
    *,
    inputs: dict[str, Any] | None = None,
    decision_client: Any | None = None,
    registry: AdapterRegistry | None = None,
    emit: EventCallback | None = None,
) -> dict[str, Any]:
    """编译并执行，返回状态/节点产出/轨迹。

    Webhook 载荷（退款单 order_id/reason/amount）经 inputs 传入，
    作为 trigger 节点 context.payload 供下游引用。
    """
    compiled = compile_graph(
        graph,
        decision_client=decision_client,
        registry=registry,
        emit=emit,
        trigger_payload=inputs,
    )
    final_state = compiled.invoke(initial_state(graph, inputs=inputs))
    result = {
        "status": "completed",
        "outputs": final_state["outputs"],
        "trace": final_state["messages"],
    }
    if emit:
        emit({"type": "run_end", **result})
    return result
