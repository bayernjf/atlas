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
import time
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from atlas.collaboration.approvals import ApprovalBroker
from atlas.harness.base import ActionRequest, ActionStatus
from atlas.harness.registry import AdapterRegistry
from atlas.llm.decision import get_decision_client
from atlas.shop.adapter import ShopHarnessAdapter
from .conditions import ConditionEvalError, evaluate_expression
from .dsl import (
    MAX_SUBGRAPH_DEPTH,
    GraphDSL,
    GraphValidationError,
    NodeDSL,
    _loop_body_set,
    validate_graph,
)

_TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
_PATH_SEGMENT_RE = re.compile(r"[^.[\]]+|\[\d+\]")

EventCallback = Callable[[dict[str, Any]], None]

# 编译期注入的汇聚网关节点名前缀（04 §5.4）；该节点事件不下发。
JOIN_GATE_PREFIX = "__join__"

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
    approval_broker: ApprovalBroker,
    graph_id: str,
    emit: EventCallback,
    graph_resolver: Callable[[str], GraphDSL] | None,
    subgraph_depth: int,
):
    def execute(state: GraphState) -> dict:
        context = {"global": state["variables"].get("global", {}), **state["outputs"]}
        start_event: dict[str, Any] = {
            "type": "node_start",
            "node_id": node.id,
            "node_type": node.type,
        }

        if node.type == "human_approval":
            start_event["approval"] = _register_approval(
                node,
                context=context,
                trigger_payload=trigger_payload,
                broker=approval_broker,
                graph_id=graph_id,
            )
        emit(start_event)

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
            time.sleep(seconds)
            output = {"mode": "wait", "waitType": "duration", "durationSeconds": seconds}
            message = f"{node.id}: waited {seconds}s"
        elif node.type == "human_approval":
            output, message = _await_human_approval(
                node,
                token=start_event["approval"]["token"],
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
            )
        else:
            output = _execute_tool(node, context, registry)
            message = f"{node.id}({node.type}): executed"

        emit({"type": "node_end", "node_id": node.id, "node_type": node.type, "output": output})
        return {
            "outputs": {node.id: output},
            "status": "running",
            "messages": [message],
        }

    return execute


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
    token = broker.request(
        node_id=node.id,
        graph_id=graph_id,
        summary=summary,
        approver=approver,
        timeout_seconds=int(config["timeoutSeconds"]),
    )
    return {
        "token": token,
        "summary": summary,
        "approver": approver,
        "timeoutSeconds": int(config["timeoutSeconds"]),
    }


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
    output = {
        "mode": "human_approval",
        "decision": decision,
        "target": target,
        "token": token,
        "summary": broker.get(token)["summary"],
        "approver": broker.get(token)["approver"],
        "resolvedBy": resolved_by,
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
) -> tuple[dict[str, Any], str]:
    """进程内重入执行被引用子图（04 §5.7）；任何异常 fail-safe 为 failed，父 run 仍 completed。"""
    graph_ref = str(node.config.get("graphId", ""))
    mapping = node.config.get("inputs") or {}
    child_inputs = {key: interpolate(str(value), context) for key, value in mapping.items()}
    try:
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
        )
    except Exception as exc:
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
) -> list[str]:
    """编译期跨图递归校验（04 §5.7）：可解析、禁自引用/跨图环、深度≤3、子图递归过图校验。"""
    errors: list[str] = []
    for node in graph.nodes:
        if node.type != "subgraph":
            continue
        ref = node.config.get("graphId")
        prefix = f"子图节点 {node.id}"
        if not isinstance(ref, str) or not ref.strip():
            errors.append(f"{prefix} 必须选择引用的已保存子图（graphId）")
            continue
        if resolver is None:
            errors.append(f"{prefix} 子图解析器未注入（graphId={ref}）")
            continue
        if ref == graph_id:
            errors.append(f"{prefix} 子图不能直接引用自身：{ref}")
            continue
        if ref in chain:
            errors.append(
                f"{prefix} 检测到跨图引用环：{' → '.join((*chain, ref))}"
            )
            continue
        if depth + 1 > MAX_SUBGRAPH_DEPTH:
            errors.append(
                f"{prefix} 子图嵌套深度超过上限 {MAX_SUBGRAPH_DEPTH}（引用链：{' → '.join((*chain, ref))}）"
            )
            continue
        try:
            child = resolver(ref)
        except KeyError:
            errors.append(f"{prefix} 引用的子图不存在：{ref}")
            continue
        if child is None:
            errors.append(f"{prefix} 引用的子图不存在：{ref}")
            continue
        for child_error in validate_graph(child):
            errors.append(f"子图 {ref}：{child_error}")
        errors.extend(
            _validate_subgraph_refs(
                child, resolver, ref, chain=(*chain, ref), depth=depth + 1
            )
        )
    return errors


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


def _make_join_gate(node: NodeDSL, meta: dict[str, Any], emit: EventCallback):
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
        emit({"type": "node_end", "node_id": node.id, "node_type": "parallel", "output": output})
        return {"outputs": {node.id: output}, "messages": [message]}

    return gate


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
):
    decision_client = decision_client or get_decision_client()
    registry = registry if registry is not None else build_demo_registry()
    approval_broker = approval_broker or _default_approval_broker
    noop_emit: EventCallback = lambda event: None
    emit = emit or noop_emit
    payload = trigger_payload or {}

    ref_errors = _validate_subgraph_refs(
        graph, graph_resolver, graph_id, chain=(graph_id,), depth=_subgraph_depth
    )
    if ref_errors:
        raise GraphValidationError(ref_errors)

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
        builder.add_node(meta["gate"], _make_join_gate(node, meta, emit))
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
) -> dict[str, Any]:
    """编译并执行，返回状态/节点产出/轨迹。

    Webhook 载荷（退款单 order_id/reason/amount）经 inputs 传入，
    作为 trigger 节点 context.payload 供下游引用。
    inputs.approvals 可预置 {<human 节点 id>: "approved"|"rejected"} 秒过审批（04 §5.6）。
    graph_resolver 按 subgraph 节点 config.graphId 解析已保存子图（04 §5.7）。
    """
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
    if emit:
        emit({"type": "run_end", **result})
    return result
