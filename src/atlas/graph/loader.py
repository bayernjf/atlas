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

import copy
import json
import operator
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from atlas.cards.catalog import get_card
from atlas.collaboration.approvals import ApprovalBroker
from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import DatabaseClient, demo_engine
from atlas.harness.base import ActionRequest, ActionStatus
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.llm.decision import get_decision_client
from atlas.message.adapter import MessageHarnessAdapter
from atlas.message.service import MessageService
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.storage.frame import build_frame, deadline_iso, remaining_seconds
from atlas.tracing import (
    KIND_NODE,
    KIND_PARALLEL,
    KIND_SUBGRAPH,
    KIND_TOOL,
    Span,
    Tracer,
)
from .conditions import ConditionEvalError, evaluate_expression
from .dsl import (
    MAX_SUBGRAPH_DEPTH,
    GraphDSL,
    GraphValidationError,
    NodeDSL,
    Issue,
    _loc,
    _loop_body_set,
    validate_graph_report,
)
from .interpolation import interpolate, resolve_path

EventCallback = Callable[[dict[str, Any]], None]

# 编译期注入的汇聚网关节点名前缀（04 §5.4）；该节点事件不下发。
JOIN_GATE_PREFIX = "__join__"

# M10：run_graph 的 tracer 哨兵——未显式传 tracer 时自建；显式传 None 表示不埋点
# （debug 单步会话口径，04 §5.13/§5.15：SSE 帧保持无 span 字段的旧形状）。
_AUTO_TRACER = object()

# 进程内审批信号单例（04 §5.6）；API/测试可注入自己的实例。
_default_approval_broker = ApprovalBroker()


def _merge_outputs(left: dict, right: dict) -> dict:
    """outputs 通道按键合并（04 §5.4）：同超步并发分支各写自身分片，同 key 后者覆盖。"""
    return {**left, **right}


def _last_write(left: Any, right: Any) -> Any:
    """并发分支同超步写同值状态时 last-write-wins。"""
    return right


class GraphState(TypedDict):
    variables: dict
    outputs: Annotated[dict[str, dict[str, Any]], _merge_outputs]
    messages: Annotated[list[str], operator.add]
    status: Annotated[str, _last_write]


# params 插值后为 JSON 对象、整体透传给适配器的通用通道（04 §4.6-4.8）；
# 其余适配器（shop）走下方按能力硬编码装配。
GENERIC_JSON_ADAPTERS = frozenset({"http", "database", "message"})


def build_demo_registry() -> AdapterRegistry:
    """Demo 默认适配器注册表：shop/http/database/message 均授予全四权限（仅 Demo）。"""
    registry = AdapterRegistry()
    permissions = {"read", "write", "delete", "financial"}
    registry.register(ShopHarnessAdapter(granted_permissions=permissions))
    registry.register(HttpApiHarnessAdapter(granted_permissions=permissions))
    registry.register(
        DatabaseHarnessAdapter(
            client=DatabaseClient(demo_engine(), demo=True),
            granted_permissions=permissions,
        )
    )
    registry.register(
        MessageHarnessAdapter(service=MessageService(), granted_permissions=permissions)
    )
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
    approval_broker: ApprovalBroker,
    graph_id: str,
    emit: EventCallback,
    graph_resolver: Callable[[str], GraphDSL] | None,
    subgraph_depth: int,
    debug_controller: Any = None,
    frame_sink: Callable[[dict], None] | None = None,
    resume: dict | None = None,
    graph_snapshot: dict | None = None,
    tracer: Tracer | None = None,
    base_span: Span | None = None,
    internal_spans: bool = False,
):
    def execute(state: GraphState) -> dict:
        context = {"global": state["variables"].get("global", {}), **state["outputs"]}

        # M10：每节点一个 node span（parallel 用 parallel kind 并记 fork attrs）；
        # parent 显式取 base_span（顶层=root，subgraph 重入=subgraph span），不依赖跨超步 current。
        span_attrs: dict[str, Any] = {"nodeId": node.id, "nodeType": node.type}
        if node.type == "parallel":
            fork_targets = [
                branch.get("target") for branch in node.config.get("branches", [])
            ]
            span_attrs["forkCount"] = len(fork_targets)
            span_attrs["targets"] = fork_targets
        if tracer is not None:
            span_cm = tracer.span(
                f"{node.type}:{node.id}",
                kind=KIND_PARALLEL if node.type == "parallel" else KIND_NODE,
                parent=base_span,
                internal=internal_spans,
                **span_attrs,
            )
        else:
            span_cm = nullcontext()

        with span_cm as node_span:
            start_event: dict[str, Any] = {
                "type": "node_start",
                "node_id": node.id,
                "node_type": node.type,
            }
            if isinstance(node_span, Span):
                start_event.update(node_span.context())

            # 续跑时仅在挂起节点生效：用帧内原 token 继续等待（不重新登记审批）。
            resume_here = resume is not None and node.id == resume["node_id"]

            approval_payload = None
            if node.type == "human_approval":
                if resume_here:
                    approval_payload = {
                        "token": resume["resume_token"],
                        "summary": resume.get("summary", ""),
                        "approver": resume.get("approver", ""),
                        "timeoutSeconds": 0,
                    }
                    if resume.get("card_template_id"):
                        approval_payload["cardTemplateId"] = resume["card_template_id"]
                elif debug_controller is None:
                    approval_payload = _register_approval(
                        node,
                        context=context,
                        trigger_payload=trigger_payload,
                        broker=approval_broker,
                        graph_id=graph_id,
                    )
                    start_event["approval"] = approval_payload
                    _emit_frame(
                        frame_sink,
                        node,
                        state,
                        token=approval_payload["token"],
                        kind="approval",
                        graph_id=graph_id,
                        graph_snapshot=graph_snapshot,
                        trigger_payload=trigger_payload,
                        timeout_seconds=int(node.config["timeoutSeconds"]),
                        summary=approval_payload["summary"],
                        approver=approval_payload["approver"],
                        card_template_id=approval_payload.get("cardTemplateId", ""),
                    )
            emit(start_event)

            if debug_controller is not None:
                debug_controller.before_node(node, state)
                if node.type == "human_approval" and not resume_here:
                    approval_payload = _register_approval(
                        node,
                        context=context,
                        trigger_payload=trigger_payload,
                        broker=approval_broker,
                        graph_id=graph_id,
                    )
                    # 第二个 node_start 携带 approval 载荷，前端据此打开审批 Modal。
                    emit({**start_event, "approval": approval_payload})
                    _emit_frame(
                        frame_sink,
                        node,
                        state,
                        token=approval_payload["token"],
                        kind="approval",
                        graph_id=graph_id,
                        graph_snapshot=graph_snapshot,
                        trigger_payload=trigger_payload,
                        timeout_seconds=int(node.config["timeoutSeconds"]),
                        summary=approval_payload["summary"],
                        approver=approval_payload["approver"],
                        card_template_id=approval_payload.get("cardTemplateId", ""),
                    )

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
            elif node.type == "loop":
                output = _execute_loop(node, state, context)
                if output["exitReason"] is None:
                    message = (
                        f"{node.id}: continue ({output['iterations']}/"
                        f"{node.config.get('maxIterations')}) → {output['target']}"
                    )
                else:
                    message = (
                        f"{node.id}: exit ({output['exitReason']}) after "
                        f"{output['iterations']} → {output['target']}"
                    )
            elif node.type == "parallel":
                output = _parallel_running_output(node)
                targets = [branch["target"] for branch in node.config.get("branches", [])]
                message = f"{node.id}: fork {len(targets)} branches → {', '.join(targets)}"
            elif node.type == "wait":
                seconds = int(node.config["durationSeconds"])
                if resume_here:
                    # 续跑：按剩余时长等待（绝对 deadline 照扣，docs/24 §3.1）。
                    seconds = int(remaining_seconds(resume.get("deadline_at")))
                else:
                    _emit_frame(
                        frame_sink,
                        node,
                        state,
                        token=uuid.uuid4().hex,
                        kind="wait",
                        graph_id=graph_id,
                        graph_snapshot=graph_snapshot,
                        trigger_payload=trigger_payload,
                        timeout_seconds=seconds,
                    )
                time.sleep(max(seconds, 0))
                output = {"mode": "wait", "waitType": "duration", "durationSeconds": seconds}
                message = f"{node.id}: waited {seconds}s"
            elif node.type == "human_approval":
                output, message = _await_human_approval(
                    node,
                    token=approval_payload["token"],
                    trigger_payload=trigger_payload,
                    broker=approval_broker,
                )
            elif node.type == "subgraph":
                output, message = _execute_subgraph(
                    node,
                    context=context,
                    registry=registry,
                    decision_client=decision_client,
                    approval_broker=approval_broker,
                    resolver=graph_resolver,
                    depth=subgraph_depth,
                    tracer=tracer,
                )
            else:
                # M10：工具调用包 tool span（parent 经 contextvars 就近取当前 node span）。
                tool_name = node.config.get("tool", "")
                if tracer is not None and "/" in tool_name:
                    adapter_id, capability_name = tool_name.split("/", 1)
                    tool_cm = tracer.span(
                        f"tool:{tool_name}",
                        kind=KIND_TOOL,
                        adapter=adapter_id,
                        capability=capability_name,
                        internal=internal_spans,
                    )
                else:
                    tool_cm = nullcontext()
                with tool_cm as tool_span:
                    output = _execute_tool(node, context, registry)
                    if isinstance(tool_span, Span) and _node_failure(output):
                        tool_span.end("error")
                message = f"{node.id}({node.type}): executed"

            end_event: dict[str, Any] = {
                "type": "node_end",
                "node_id": node.id,
                "node_type": node.type,
                "output": output,
            }
            if isinstance(node_span, Span):
                end_event.update(node_span.context())
                if _span_error(output):
                    node_span.end("error")
            emit(end_event)
            return {
                "outputs": {node.id: output},
                "status": "running",
                "messages": [message],
            }

    return execute


def _emit_frame(
    frame_sink: Callable[[dict], None] | None,
    node: NodeDSL,
    state: GraphState,
    *,
    token: str,
    kind: str,
    graph_id: str,
    graph_snapshot: dict | None,
    trigger_payload: dict[str, Any],
    timeout_seconds: int,
    summary: str = "",
    approver: str = "",
    card_template_id: str = "",
) -> None:
    """挂起前经 frame_sink 序列化中断帧（含 graph_snapshot 与截至挂起点的 outputs）。

    frame_sink 由 API 层注入（PG 后端写 interruptions 表）；进程内后端不注入时跳过。
    """
    if frame_sink is None or graph_snapshot is None:
        return
    frame_sink(
        build_frame(
            token=token,
            run_id="",
            node_id=node.id,
            kind=kind,
            deadline_at=deadline_iso(timeout_seconds),
            graph_snapshot=graph_snapshot,
            resume_state={
                "graph_id": graph_id,
                "inputs": trigger_payload,
                "outputs": state["outputs"],
            },
            summary=summary,
            approver=approver,
            card_template_id=card_template_id,
        )
    )


def _register_approval(
    node: NodeDSL,
    *,
    context: dict[str, Any],
    trigger_payload: dict[str, Any],
    broker: ApprovalBroker,
    graph_id: str,
) -> dict[str, Any]:
    """登记 pending 审批请求并返回随 node_start 下发的 approval 载荷（04 §5.6）。"""
    config = node.config
    summary = interpolate(str(config.get("summary", "")), context)
    approver = interpolate(str(config.get("approver", "")), context) if config.get("approver") else ""
    timeout_seconds = int(config["timeoutSeconds"])
    # M8：可选内置卡片；编译期已校验目录命中，运行时防御未命中即按无卡（旧 summary 路径）。
    card_template_id = config.get("cardTemplateId") or None
    if card_template_id is not None and get_card(card_template_id) is None:
        card_template_id = None
    card_context = copy.deepcopy(context) if card_template_id else None
    token = broker.request(
        node_id=node.id,
        graph_id=graph_id,
        summary=summary,
        approver=approver,
        timeout_seconds=timeout_seconds,
        card_template_id=card_template_id,
        card_context=card_context,
    )
    payload: dict[str, Any] = {
        "token": token,
        "summary": summary,
        "approver": approver,
        "timeoutSeconds": timeout_seconds,
    }
    if card_template_id:
        payload["cardTemplateId"] = card_template_id
    return payload


def _await_human_approval(
    node: NodeDSL,
    *,
    token: str,
    trigger_payload: dict[str, Any],
    broker: ApprovalBroker,
) -> tuple[dict[str, Any], str]:
    """阻塞等待审批结果（预置 inputs/人工放行/超时），返回节点产出与 trace 行。"""
    config = node.config
    preset = (trigger_payload.get("approvals") or {}).get(node.id)
    if preset in ("approved", "rejected"):
        broker.resolve(token, preset, resolved_by="input")

    decision = broker.wait(token)
    if decision is None:
        fallback = "approved" if config.get("onTimeout", "reject") == "approve" else "rejected"
        decision, resolved_by = broker.complete_timeout(token, fallback)
    else:
        resolved_by = broker.get(token)["resolvedBy"]

    target = config["approvedTarget"] if decision == "approved" else config["rejectedTarget"]
    info = broker.get(token)
    output: dict[str, Any] = {
        "mode": "human_approval",
        "decision": decision,
        "target": target,
        "token": token,
        "summary": info["summary"],
        "approver": info["approver"],
        "resolvedBy": resolved_by,
        "comment": info.get("comment", ""),
    }
    # M8：命中卡片时回带模板 id 与本次动作 id（预置/超时来源 actionId 为 None）。
    if info.get("cardTemplateId"):
        output["card"] = {
            "templateId": info["cardTemplateId"],
            "actionId": info.get("actionId"),
        }
    message = f"{node.id}: {decision} ({resolved_by}) → {target}"
    return output, message


def _execute_subgraph(
    node: NodeDSL,
    *,
    context: dict[str, Any],
    registry: AdapterRegistry | None,
    decision_client: Any,
    approval_broker: ApprovalBroker,
    resolver: Callable[[str], GraphDSL] | None,
    depth: int,
    tracer: Tracer | None = None,
) -> tuple[dict[str, Any], str]:
    """进程内重入执行被引用子图（04 §5.7）；任何异常 fail-safe 为 failed，父 run 仍 completed。"""
    graph_ref = str(node.config.get("graphId", ""))
    mapping = node.config.get("inputs") or {}
    child_inputs = {key: interpolate(str(value), context) for key, value in mapping.items()}
    # M10：subgraph span（非 internal，折叠后代表整段子图）；子图内部节点 span 标 internal。
    sub_cm = (
        tracer.span(
            f"subgraph:{graph_ref}",
            kind=KIND_SUBGRAPH,
            graphId=graph_ref,
        )
        if tracer is not None
        else nullcontext()
    )
    sub_span: Span | None = None
    try:
        with sub_cm as sub_span:
            if resolver is None:
                raise RuntimeError("子图解析器未注入")
            if depth + 1 > MAX_SUBGRAPH_DEPTH:
                raise RuntimeError(f"子图嵌套深度超过上限 {MAX_SUBGRAPH_DEPTH}")
            child = resolver(graph_ref)
            result = run_graph(
                child,
                inputs=child_inputs,
                decision_client=decision_client,
                registry=registry,
                approval_broker=approval_broker,
                graph_id=graph_ref,
                emit=None,
                graph_resolver=resolver,
                _subgraph_depth=depth + 1,
                tracer=tracer,
                _parent_span=sub_span if isinstance(sub_span, Span) else None,
            )
    except Exception as exc:
        if isinstance(sub_span, Span):
            sub_span.end("error")
        output = {
            "mode": "subgraph",
            "graphId": graph_ref,
            "status": "failed",
            "error": str(exc),
            "outputs": {},
            "trace": [],
        }
        return output, f"{node.id}: {graph_ref} failed: {exc}"

    output = {
        "mode": "subgraph",
        "graphId": graph_ref,
        "status": "success",
        "outputs": result["outputs"],
        "trace": result["trace"],
    }
    return output, f"{node.id}: {graph_ref} success ({len(child.nodes)} nodes)"


def _validate_subgraph_refs(
    graph: GraphDSL,
    resolver: Callable[[str], GraphDSL] | None,
    graph_id: str,
    *,
    chain: tuple[str, ...],
    depth: int,
    tool_output_schemas: dict[str, dict[str, Any]] | None = None,
    top_node_id: str | None = None,
) -> list[Issue]:
    """编译期跨图递归校验（04 §5.7）：可解析、禁自引用/跨图环、深度≤3、子图递归过图校验。

    返回 (文案, 定位)：本图子图节点错误带 nodeId（缺 graphId 时根图带 /graphId）；
    深层递归子错误 v1 只带最外层子图节点 id（06 §6.13，深层定位缓做）。
    """
    issues: list[Issue] = []
    for node in graph.nodes:
        if node.type != "subgraph":
            continue
        ref = node.config.get("graphId")
        prefix = f"子图节点 {node.id}"
        owner = top_node_id or node.id

        def add_own(message: str, pointer: str | None = None) -> None:
            # 深层节点错误只挂最外层子图节点 id，pointer 缓做。
            location = _loc(node.id, pointer) if top_node_id is None else _loc(top_node_id)
            issues.append((message, location))

        if not isinstance(ref, str) or not ref.strip():
            add_own(f"{prefix} 必须选择引用的已保存子图（graphId）", "/graphId")
            continue
        if resolver is None:
            add_own(f"{prefix} 子图解析器未注入（graphId={ref}）")
            continue
        if ref == graph_id:
            add_own(f"{prefix} 子图不能直接引用自身：{ref}")
            continue
        if ref in chain:
            add_own(
                f"{prefix} 检测到跨图引用环：{' → '.join((*chain, ref))}"
            )
            continue
        if depth + 1 > MAX_SUBGRAPH_DEPTH:
            add_own(
                f"{prefix} 子图嵌套深度超过上限 {MAX_SUBGRAPH_DEPTH}（引用链：{' → '.join((*chain, ref))}）"
            )
            continue
        try:
            child = resolver(ref)
        except KeyError:
            add_own(f"{prefix} 引用的子图不存在：{ref}")
            continue
        if child is None:
            add_own(f"{prefix} 引用的子图不存在：{ref}")
            continue
        child_messages, _child_locations = validate_graph_report(
            child, tool_output_schemas, check_refs=True
        )
        for child_error in child_messages:
            issues.append((f"子图 {ref}：{child_error}", _loc(owner)))
        issues.extend(
            _validate_subgraph_refs(
                child, resolver, ref, chain=(*chain, ref), depth=depth + 1,
                tool_output_schemas=tool_output_schemas,
                top_node_id=owner,
            )
        )
    return issues


def _parallel_running_output(node: NodeDSL) -> dict[str, Any]:
    return {
        "mode": "parallel",
        "joinStrategy": node.config.get("joinStrategy", "all_success"),
        "status": "running",
        "branches": [
            {"label": branch["label"], "target": branch["target"], "status": "running", "error": ""}
            for branch in node.config.get("branches", [])
        ],
        "result": {},
        "joinTarget": node.config["joinTarget"],
    }


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


def _execute_loop(node: NodeDSL, state: GraphState, context: dict[str, Any]) -> dict[str, Any]:
    """条件循环重入求值（04 §5.3）；达上限/求值异常 fail-safe 走 exitTarget。"""
    config = node.config
    body_target = config["bodyTarget"]
    exit_target = config["exitTarget"]
    max_iterations = int(config.get("maxIterations", 10))

    previous = state["outputs"].get(node.id, {})
    iterations = int(previous.get("iterations", 0)) if isinstance(previous, dict) else 0

    expression_errors: list[str] = []
    exit_reason: str | None = None

    if iterations >= max_iterations:
        target = exit_target
        exit_reason = "max_iterations"
        expression_errors.append(f"已达最大次数 {max_iterations}，强制退出循环")
    else:
        # 首轮自身产出尚不存在；播种 index 供 {{loop-x.index}} 求值
        loop_context = {**context, node.id: {"index": iterations, "iterations": iterations}}
        try:
            result = evaluate_expression(config["continueExpression"], loop_context)
        except ConditionEvalError as exc:
            target = exit_target
            exit_reason = "expression_error"
            expression_errors.append(str(exc))
        else:
            if not isinstance(result, bool):
                target = exit_target
                exit_reason = "expression_error"
                expression_errors.append(
                    f"继续条件结果必须是布尔值，实际为 {type(result).__name__}"
                )
            elif result:
                iterations += 1
                target = body_target
            else:
                target = exit_target
                exit_reason = "condition_false"

    return {
        "mode": "while",
        "iterations": iterations,
        "index": iterations,
        "target": target,
        "exitReason": exit_reason,
        "expression_errors": expression_errors,
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

    if adapter_id in GENERIC_JSON_ADAPTERS:
        # 通用 JSON 通道（04 §4.6-4.8）：params 插值后必须是 JSON 对象并整体透传
        try:
            parameters = json.loads(params_text) if params_text.strip() else {}
        except json.JSONDecodeError:
            return {
                "result": {"status": "FAILED", "code": "INVALID_PARAMETER", "message": "params 不是合法 JSON"},
                "action_status": "FAILED",
            }
        if not isinstance(parameters, dict):
            return {
                "result": {"status": "FAILED", "code": "INVALID_PARAMETER", "message": "params 必须是 JSON 对象"},
                "action_status": "FAILED",
            }
        result = adapter.execute(ActionRequest(capability_name=capability_name, parameters=parameters))
        if result.status == ActionStatus.SUCCESS:
            return {"result": result.output, "action_status": result.status.value}
        error = result.error
        return {
            "result": {
                "status": "FAILED",
                "code": error.code if error else "UNKNOWN",
                "message": error.message if error else "",
            },
            "action_status": result.status.value,
        }

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


def _parallel_meta(node: NodeDSL, outgoing: dict[str, list[str]]) -> dict[str, Any] | None:
    """推导并行区域（04 §5.4）：区域集合、逐分支可达集与汇聚末端节点。

    合法性（targets 存在、区域不交叉等）由 dsl 静态校验保证，此处只编译。
    """
    if node.type != "parallel":
        return None
    join_target = node.config["joinTarget"]
    entries = [branch["target"] for branch in node.config.get("branches", [])]

    def bfs(start: str) -> set[str]:
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current in seen or current in (node.id, join_target):
                continue
            seen.add(current)
            stack.extend(outgoing.get(current, []))
        return seen

    entry_areas = {entry: bfs(entry) for entry in entries}
    region: set[str] = set().union(*entry_areas.values()) if entry_areas else set()
    terminals = {
        current for current in region if join_target in outgoing.get(current, [])
    }
    return {
        "gate": f"{JOIN_GATE_PREFIX}{node.id}",
        "join_target": join_target,
        "entries": entries,
        "region": region,
        "entry_areas": entry_areas,
        "entry_terminals": {
            entry: area & terminals for entry, area in entry_areas.items()
        },
    }


def _node_failure(output: dict[str, Any]) -> str | None:
    result = output.get("result")
    if isinstance(result, dict) and result.get("status") == "FAILED":
        return str(result.get("error") or result.get("message") or result.get("code") or "FAILED")
    return None


def _span_error(output: dict[str, Any]) -> str | None:
    """节点产出是否应把 node span 标 error（M10）：工具 FAILED / subgraph / parallel failed。"""
    failure = _node_failure(output)
    if failure:
        return failure
    if output.get("mode") == "subgraph" and output.get("status") == "failed":
        return str(output.get("error") or "subgraph failed")
    if output.get("mode") == "parallel" and output.get("status") == "failed":
        return "parallel join failed"
    return None


def _make_join_gate(
    node: NodeDSL,
    meta: dict[str, Any],
    emit: EventCallback,
    tracer: Tracer | None = None,
    base_span: Span | None = None,
):
    """汇聚网关（04 §5.4）：所有分支末端都有产出时聚合一次，否则空转等下超步。"""

    config = node.config
    strategy = config.get("joinStrategy", "all_success")
    labels = {branch["target"]: branch["label"] for branch in config.get("branches", [])}

    def gate(state: GraphState) -> dict:
        outputs = state["outputs"]
        done = all(
            any(terminal in outputs for terminal in terminals)
            for terminals in meta["entry_terminals"].values()
        )
        if not done:
            return {"messages": []}

        branches: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        failed: list[tuple[str, str]] = []
        for entry in meta["entries"]:
            area = meta["entry_areas"][entry]
            error = ""
            for area_node in area:
                area_output = outputs.get(area_node)
                if isinstance(area_output, dict):
                    failure = _node_failure(area_output)
                    if failure:
                        error = failure
                        break
            executed = [
                terminal for terminal in meta["entry_terminals"][entry] if terminal in outputs
            ]
            if executed:
                result[entry] = outputs[executed[0]]
            status = "failed" if error else "success"
            if error:
                failed.append((labels.get(entry, entry), error))
            branches.append(
                {"label": labels.get(entry, entry), "target": entry, "status": status, "error": error}
            )

        overall = "success"
        if strategy == "all_success" and failed:
            overall = "failed"
        output = {
            "mode": "parallel",
            "joinStrategy": strategy,
            "status": overall,
            "branches": branches,
            "result": result,
            "joinTarget": meta["join_target"],
        }
        if overall == "failed":
            detail = ", ".join(f"{label}（{error}）" for label, error in failed)
            message = f"{node.id}: joined ({strategy}) failed: {detail}"
        else:
            message = f"{node.id}: joined ({strategy}) success"
        # 汇聚完成时以 parallel 节点自身补发一次 node_end（fork 时产出为 running），
        # 供 SSE 画布展示最终汇聚结果；等待超步不发事件。
        join_event: dict[str, Any] = {
            "type": "node_end",
            "node_id": node.id,
            "node_type": "parallel",
            "output": output,
        }
        if tracer is not None:
            # M10：__join__ 网关本身不外泄 span——只建一个 internal parallel 汇聚 span，
            # include_internal=False 折叠；traceId 与本 run 一致。
            join_span = tracer.start_span(
                f"parallel-join:{node.id}",
                kind=KIND_PARALLEL,
                parent=base_span,
                internal=True,
                nodeId=node.id,
                joinStrategy=strategy,
                status=overall,
            )
            join_span.end("error" if overall == "failed" else "ok")
            join_event.update(join_span.context())
        emit(join_event)
        return {"outputs": {node.id: output}, "messages": [message]}

    return gate


def _tool_output_schemas(registry: AdapterRegistry) -> dict[str, dict[str, Any]]:
    """编译期 L2 复查用：``<adapter_id>/<tool> -> output_schema``（04 §4.9/§6.5）。"""
    table: dict[str, dict[str, Any]] = {}
    for adapter in registry.list_adapters():
        for tool in adapter["tools"]:
            table[f"{adapter['id']}/{tool['name']}"] = tool["output_schema"]
    return table


def tool_input_schemas(registry: AdapterRegistry) -> dict[str, dict[str, Any]]:
    """NL 参数填充尽力校验用：``<adapter_id>/<tool> -> input_schema``（04 §4.9 ⑤）。"""
    table: dict[str, dict[str, Any]] = {}
    for adapter in registry.list_adapters():
        for tool in adapter["tools"]:
            table[f"{adapter['id']}/{tool['name']}"] = tool["input_schema"]
    return table


def compile_graph(
    graph: GraphDSL,
    *,
    decision_client: Any | None = None,
    registry: AdapterRegistry | None = None,
    approval_broker: ApprovalBroker | None = None,
    graph_id: str = "adhoc",
    emit: EventCallback | None = None,
    trigger_payload: dict[str, Any] | None = None,
    graph_resolver: Callable[[str], GraphDSL] | None = None,
    _subgraph_depth: int = 0,
    debug_controller: Any = None,
    frame_sink: Callable[[dict], None] | None = None,
    resume: dict | None = None,
    validate_with: GraphDSL | None = None,
    tracer: Tracer | None = None,
    graph_version: str | None = None,
    _parent_span: Span | None = None,
):
    decision_client = decision_client or get_decision_client()
    registry = registry if registry is not None else build_demo_registry()
    approval_broker = approval_broker or _default_approval_broker
    noop_emit: EventCallback = lambda event: None
    emit = emit or noop_emit
    # M10：节点 span 的父——subgraph 重入为 subgraph span（子图节点 internal），
    # 顶层为 tracer.root；tracer 为 None（debug/直接 compile）时不埋点。
    base_span: Span | None = (
        _parent_span if _parent_span is not None
        else (tracer.root if tracer is not None else None)
    )
    internal_spans = _parent_span is not None
    payload = trigger_payload or {}
    graph_snapshot = graph.model_dump()
    # 续跑时校验用完整图（尾图节点仍引用上游已完成节点，其引用合法），
    # 编译仍用裁剪后的尾图。
    validation_graph = validate_with or graph

    tool_schemas = _tool_output_schemas(registry)
    # 编译期 L2 复查（04 §6.5 防绕过）：parse_graph 时无注册表，引用与工具深层路径在此补判。
    ref_errors, ref_locations = validate_graph_report(validation_graph, tool_schemas, check_refs=True)
    subgraph_issues = _validate_subgraph_refs(
        validation_graph,
        graph_resolver,
        graph_id,
        chain=(graph_id,),
        depth=_subgraph_depth,
        tool_output_schemas=tool_schemas,
    )
    offset = len(ref_errors)
    subgraph_locations = [
        {"index": offset + index, **location}
        for index, (_message, location) in enumerate(subgraph_issues)
        if location is not None
    ]
    ref_errors = ref_errors + [message for message, _location in subgraph_issues]
    ref_locations = ref_locations + subgraph_locations
    if ref_errors:
        raise GraphValidationError(ref_errors, ref_locations)

    builder = StateGraph(GraphState)
    for node in graph.nodes:
        builder.add_node(
            node.id,
            _make_executor(
                node,
                trigger_payload=payload,
                decision_client=decision_client,
                registry=registry,
                approval_broker=approval_broker,
                graph_id=graph_id,
                emit=emit,
                graph_resolver=graph_resolver,
                subgraph_depth=_subgraph_depth,
                debug_controller=debug_controller,
                frame_sink=frame_sink,
                resume=resume,
                graph_snapshot=graph_snapshot,
                tracer=tracer,
                base_span=base_span,
                internal_spans=internal_spans,
            ),
        )

    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)

    # parallel 区域推导：区域内节点指向 joinTarget 的边在编译期改指向汇聚网关，
    # 网关等待全部分支末端产出后聚合一次再放行进 joinTarget（04 §5.4）。
    parallels = [node for node in graph.nodes if node.type == "parallel"]
    metas = {node.id: _parallel_meta(node, outgoing) for node in parallels}
    retarget: dict[tuple[str, str], str] = {}
    for node in parallels:
        meta = metas[node.id]
        builder.add_node(
            meta["gate"], _make_join_gate(node, meta, emit, tracer=tracer, base_span=base_span)
        )
        for source in meta["region"]:
            if meta["join_target"] in outgoing.get(source, []):
                retarget[(source, meta["join_target"])] = meta["gate"]

    incoming = {edge.target for edge in graph.edges}
    for node in graph.nodes:
        if node.id not in incoming:
            builder.add_edge(START, node.id)

    condition_ids = {node.id for node in graph.nodes if node.type == "condition"}
    loop_ids = {node.id for node in graph.nodes if node.type == "loop"}
    human_ids = {node.id for node in graph.nodes if node.type == "human_approval"}
    parallel_ids = set(metas)
    conditional_ids = condition_ids | loop_ids | human_ids | parallel_ids

    for edge in graph.edges:
        if edge.source in conditional_ids:
            # condition/loop/parallel 出边全部走 conditional edges，混用会导致双路激活。
            continue
        # 循环回边 source 在循环体内，作为普通边装配。
        builder.add_edge(edge.source, retarget.get((edge.source, edge.target), edge.target))

    for condition_id in condition_ids | human_ids:
        # condition/human_approval 同构：执行器写 outputs[id].target，双（多）出口全部 conditional。
        targets = outgoing.get(condition_id, [])

        def route(state: GraphState, cid: str = condition_id) -> str:
            target = state["outputs"][cid]["target"]
            return retarget.get((cid, target), target)

        builder.add_conditional_edges(
            condition_id,
            route,
            {target: retarget.get((condition_id, target), target) for target in targets},
        )

    for loop in graph.nodes:
        if loop.type != "loop":
            continue
        targets = outgoing.get(loop.id, [])

        def route_loop(state: GraphState, cid: str = loop.id) -> str:
            target = state["outputs"][cid]["target"]
            return retarget.get((cid, target), target)

        builder.add_conditional_edges(
            loop.id,
            route_loop,
            {target: retarget.get((loop.id, target), target) for target in targets},
        )

    for node in parallels:
        meta = metas[node.id]
        targets = [branch["target"] for branch in node.config.get("branches", [])]

        def route_parallel(state: GraphState, branch_targets: list[str] = targets) -> list[str]:
            return branch_targets

        builder.add_conditional_edges(
            node.id, route_parallel, {target: target for target in targets}
        )

        def route_gate(state: GraphState, m: dict[str, Any] = meta) -> str:
            ready = all(
                any(terminal in state["outputs"] for terminal in terminals)
                for terminals in m["entry_terminals"].values()
            )
            return m["join_target"] if ready else "wait"

        builder.add_conditional_edges(
            meta["gate"],
            route_gate,
            {meta["join_target"]: meta["join_target"], "wait": meta["gate"]},
        )

    for node in graph.nodes:
        if node.id not in outgoing:
            builder.add_edge(node.id, END)

    return builder.compile()


def initial_state(graph: GraphDSL, *, inputs: dict[str, Any] | None = None) -> GraphState:
    # inputs 同时承担两个角色（W9-W10）：同名键覆盖全局变量（W7-W8 语义），
    # 且整体作为 webhook 载荷进入 trigger 节点 context.payload。
    variables = _seed_variables(graph)
    if inputs:
        # approvals 是运行控制键（human_approval 预置决策），不进入全局变量。
        overrides = {key: value for key, value in inputs.items() if key != "approvals"}
        variables["global"] = {**variables.get("global", {}), **overrides}
    return {"variables": variables, "outputs": {}, "messages": [], "status": "running"}


def _recursion_limit(graph: GraphDSL) -> int:
    """按循环体规模派生 LangGraph recursion_limit（04 §5.3），默认 25 会中断长循环。"""
    outgoing: dict[str, set[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, set()).add(edge.target)
    loop_steps = 0
    for node in graph.nodes:
        if node.type == "loop":
            body = _loop_body_set(
                node.config["bodyTarget"], node.id, node.config["exitTarget"], outgoing
            )
            loop_steps += int(node.config.get("maxIterations", 10)) * (len(body) + 1)
    # parallel 汇聚网关在不等长分支下按超步空转等待，每个区域节点至多贡献两轮。
    parallel_wait = 0
    for node in graph.nodes:
        if node.type == "parallel":
            meta = _parallel_meta(node, outgoing)
            parallel_wait += 2 * len(meta["region"])
    return 2 * len(graph.nodes) + 2 * loop_steps + parallel_wait + 10


def _tail_subgraph(graph: GraphDSL, resume_node_id: str) -> GraphDSL:
    """续跑尾图：从挂起节点沿边可达的子图（挂起节点为新入口，上游视为已完成）。

    docs/24 §2.3：续跑不重跑上游——已完成节点产出注入 initial state，只执行尾图。
    """
    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
    reachable: set[str] = set()
    stack = [resume_node_id]
    while stack:
        current = stack.pop()
        if current in reachable:
            continue
        reachable.add(current)
        stack.extend(outgoing.get(current, []))
    return GraphDSL(
        version=graph.version,
        variables=graph.variables,
        nodes=[node for node in graph.nodes if node.id in reachable],
        edges=[
            edge for edge in graph.edges
            if edge.source in reachable and edge.target in reachable
        ],
    )


def run_graph(
    graph: GraphDSL,
    *,
    inputs: dict[str, Any] | None = None,
    decision_client: Any | None = None,
    registry: AdapterRegistry | None = None,
    approval_broker: ApprovalBroker | None = None,
    graph_id: str = "adhoc",
    emit: EventCallback | None = None,
    graph_resolver: Callable[[str], GraphDSL] | None = None,
    _subgraph_depth: int = 0,
    debug_controller: Any = None,
    frame_sink: Callable[[dict], None] | None = None,
    resume: dict | None = None,
    tracer: Tracer | None | object = _AUTO_TRACER,
    graph_version: str | None = None,
    _parent_span: Span | None = None,
) -> dict[str, Any]:
    """编译并执行，返回状态/节点产出/轨迹。

    Webhook 载荷（退款单 order_id/reason/amount）经 inputs 传入，
    作为 trigger 节点 context.payload 供下游引用。
    inputs.approvals 可预置 {<human 节点 id>: "approved"|"rejected"} 秒过审批（04 §5.6）。
    graph_resolver 按 subgraph 节点 config.graphId 解析已保存子图（04 §5.7）。
    debug_controller 注入时在每个节点 node_start 后/逻辑前暂停（04 §5.12）；
    subgraph 重入不传控制器，子图整段执行。
    frame_sink 在挂起点（human_approval/wait）经回调序列化中断帧（docs/24 §2.3）。
    resume 为中断帧时：以帧内 graph_snapshot 为权威图定义（防图已改错位）裁剪尾图，
    从挂起节点续跑到 END；调用方须先用帧 restore 审批 pending（恢复扫描器职责）。

    M10：未传 tracer 时自建（库直跑/测试也有 span）；subgraph 重入复用父 tracer 并传
    _parent_span=subgraph span（子图节点 internal）。顶层 result 带 traceId/traceTree，
    run_end 帧带 root spanId 与 graphVersion（04 §5.15）。
    """
    if tracer is _AUTO_TRACER:
        tracer = Tracer(graph_id=graph_id, graph_version=graph_version)
    internal_run = _parent_span is not None

    def _finish(result: dict[str, Any], *, emit_end: bool) -> dict[str, Any]:
        """收尾：结束 root（仅顶层）、result 挂 traceId/traceTree、发 run_end 超集帧。

        tracer 显式为 None（debug 单步）时不埋点：result 不挂 trace 字段、
        run_end 保持旧形状（无 span 三元组）。
        """
        if tracer is None:
            if emit_end and emit is not None:
                emit({"type": "run_end", **result})
            return result
        result["traceId"] = tracer.trace_id
        if not internal_run:
            tracer.finish("ok" if result.get("status") == "completed" else "error")
            result["traceTree"] = tracer.to_tree(include_internal=True)
        if emit_end and emit is not None:
            # 终帧只带 span 三元组与 graphVersion；完整 traceTree 留在进程内返回值，
            # 不进 SSE（04 §5.15：完整树由 tracer.to_tree 导出/未来调试端点）。
            run_end: dict[str, Any] = {
                "type": "run_end",
                "status": result.get("status", "completed"),
                "outputs": result.get("outputs", {}),
                "trace": result.get("trace", []),
                "traceId": tracer.trace_id,
                "spanId": tracer.root.span_id,
            }
            if tracer.graph_version is not None:
                run_end["graphVersion"] = tracer.graph_version
            emit(run_end)
        return result

    if resume is not None:
        resume_graph = GraphDSL.model_validate(resume["graph_snapshot"])
        tail = _tail_subgraph(resume_graph, resume["node_id"])
        resume_state = resume.get("resume_state", {})
        resume_inputs = resume_state.get("inputs", {})
        resume_graph_id = resume_state.get("graph_id", graph_id)
        compiled = compile_graph(
            tail,
            decision_client=decision_client,
            registry=registry,
            approval_broker=approval_broker,
            graph_id=resume_graph_id,
            emit=emit,
            trigger_payload=resume_inputs,
            graph_resolver=graph_resolver,
            _subgraph_depth=_subgraph_depth,
            debug_controller=debug_controller,
            frame_sink=frame_sink,
            resume=resume,
            validate_with=resume_graph,
            tracer=tracer,
            graph_version=graph_version,
            _parent_span=_parent_span,
        )
        state = initial_state(tail, inputs=resume_inputs)
        state["outputs"] = resume_state.get("outputs", {})
        final_state = compiled.invoke(
            state, config={"recursion_limit": _recursion_limit(tail)}
        )
        result = {
            "status": "completed",
            "outputs": final_state["outputs"],
            "trace": final_state["messages"],
        }
        return _finish(result, emit_end=True)

    compiled = compile_graph(
        graph,
        decision_client=decision_client,
        registry=registry,
        approval_broker=approval_broker,
        graph_id=graph_id,
        emit=emit,
        trigger_payload=inputs,
        graph_resolver=graph_resolver,
        _subgraph_depth=_subgraph_depth,
        debug_controller=debug_controller,
        frame_sink=frame_sink,
        tracer=tracer,
        graph_version=graph_version,
        _parent_span=_parent_span,
    )
    final_state = compiled.invoke(
        initial_state(graph, inputs=inputs),
        config={"recursion_limit": _recursion_limit(graph)},
    )
    result = {
        "status": "completed",
        "outputs": final_state["outputs"],
        "trace": final_state["messages"],
    }
    return _finish(result, emit_end=True)
