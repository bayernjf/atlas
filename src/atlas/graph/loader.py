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
import logging
import math
import operator
import random
import re
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from atlas.cards.catalog import get_card
from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.cancellations import RunCancelled
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.collaboration.notifications import ApprovalNotifier
from atlas.debug.sessions import DebugStopped
from atlas.database.adapter import DatabaseHarnessAdapter
from atlas.database.service import DatabaseClient, demo_engine
from atlas.harness.base import ActionRequest, ActionStatus
from atlas.harness.registry import AdapterRegistry
from atlas.httpapi.adapter import HttpApiHarnessAdapter
from atlas.llm.decision import get_decision_client
from atlas.llm.condition_classifier import get_condition_classifier
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
    MAX_LOOP_ITERATIONS,
    MAX_SUBGRAPH_DEPTH,
    GraphDSL,
    GraphValidationError,
    NodeDSL,
    Issue,
    _loc,
    _loop_body_set,
    valid_event_key,
    MIN_WAIT_SECONDS,
    MAX_WAIT_SECONDS,
    MIN_EVENT_WAIT_SECONDS,
    MAX_EVENT_WAIT_SECONDS,
    MAX_JITTER_SECONDS,
    validate_graph_report,
)
from .interpolation import interpolate, resolve_path

EventCallback = Callable[[dict[str, Any]], None]

# 编译期注入的汇聚网关节点名前缀（04 §5.4）；该节点事件不下发。
JOIN_GATE_PREFIX = "__join__"
BREAK_GATE_PREFIX = "__break__"

# M10：run_graph 的 tracer 哨兵——未显式传 tracer 时自建；显式传 None 表示不埋点
# （debug 单步会话口径，04 §5.13/§5.15：SSE 帧保持无 span 字段的旧形状）。
_AUTO_TRACER = object()

# 进程内审批信号单例（04 §5.6）；API/测试可注入自己的实例。
logger = logging.getLogger(__name__)

_default_approval_broker = ApprovalBroker()
_default_event_wait_broker = EventWaitBroker()


class RunSuperseded(Exception):
    """挂起帧已被其它进程认领 → 本进程停止驱动（docs/62 §2 D-4 L2）。

    控制流信号，不是运行失败：捕获处（API 三入口）必须**跳过** run 终态写入、监控记录与
    门控评估，否则输家会覆盖赢家的终态。刻意不新增 run 状态、不新增错误码。
    """

    def __init__(self, node_id: str, token: str) -> None:
        super().__init__(f"运行已被其它进程接管（节点 {node_id}，帧 {token}）")
        self.node_id = node_id
        self.token = token


def _gate_resume(
    resume_claim: Callable[[str], bool] | None, token: str, node_id: str
) -> None:
    """越过挂起点之前的认领门。

    `resume_claim is None`（进程内后端、以及从不写帧的子图重入）＝没有帧可认领＝恒放行；
    有 claim 时，只有把 `resumed_at` 从 NULL 翻起来的那个进程可以继续执行下游。
    """
    if resume_claim is not None and not resume_claim(token):
        raise RunSuperseded(node_id, token)


class WaitNodeFailure(Exception):
    """wait 节点确定性失败（docs/47 §3.4）；run 标记 failed，不沿出边继续。"""

    def __init__(self, node_id: str, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.node_id = node_id
        self.code = code


def runtime_error_meta(exc: Exception) -> dict[str, Any]:
    """docs/60 G1：运行期终态异常归一化为机器可读码（中文 error 字符串仍由调用方原样保留）。

    WaitNodeFailure 携带既有 WAIT_* 5 码与 nodeId；ConditionEvalError 携带 COND_* 码族与
    params；其余未预期异常统一 RUNTIME_UNEXPECTED。返回 errorCode/errorParams 两键，供
    run failed 结果与 SSE error 帧并行下发（纯超集，旧 error 字段不变）。
    """
    if isinstance(exc, WaitNodeFailure):
        return {"errorCode": exc.code, "errorParams": {"nodeId": exc.node_id}}
    if isinstance(exc, ConditionEvalError):
        return {"errorCode": exc.code, "errorParams": dict(exc.params)}
    return {"errorCode": "RUNTIME_UNEXPECTED", "errorParams": {}}


_ABSOLUTE_EPOCH_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _resolve_absolute_wait(
    node_id: str, raw_text: Any, context: dict[str, Any], now: datetime
) -> tuple[int, str]:
    """absoluteTime → (秒数, 渲染后 ISO 时刻)（docs/50 §2）。

    插值后按 epoch 纯数字 / ISO8601（Z 兼容、朴素时刻按 UTC）解析；
    目标须为未来 1-3600 秒（docs/54 随 MAX_WAIT_SECONDS 放宽）；坏值抛 WaitNodeFailure，不睡眠。
    """
    text = raw_text if isinstance(raw_text, str) else ""
    try:
        rendered = interpolate(text, context).strip() if "{{" in text else text.strip()
        if _ABSOLUTE_EPOCH_RE.match(rendered):
            target = datetime.fromtimestamp(float(rendered), tz=timezone.utc)
        else:
            target = datetime.fromisoformat(rendered.replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            else:
                target = target.astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise WaitNodeFailure(
            node_id,
            "WAIT_ABSOLUTE_TIME_INVALID",
            f"等待节点 {node_id} 目标时刻无法求值/解析：{exc}",
        )
    delta = (target - now).total_seconds()
    if not math.isfinite(delta) or not 1 <= round(delta) <= MAX_WAIT_SECONDS:
        raise WaitNodeFailure(
            node_id,
            "WAIT_ABSOLUTE_TIME_INVALID",
            f"等待节点 {node_id} 目标时刻非法"
            f"（需为未来 1-{MAX_WAIT_SECONDS} 秒内，当前差值 {delta:.0f}s）",
        )
    return int(round(delta)), target.isoformat()


def _resolve_wait_expression(
    node_id: str, raw_expression: Any, context: dict[str, Any], lo: int, hi: int
) -> int:
    """等待时长表达式 → 秒数（docs/49 duration dynamic；B5 event timeout expression）。

    经条件引擎求值，结果须为非 bool 有限数值且落在 [lo,hi]；任何坏值抛
    WaitNodeFailure(WAIT_DURATION_INVALID)，不睡眠。duration/event 共用，零新错误码。
    """
    try:
        raw = evaluate_expression(str(raw_expression), context)
    except ConditionEvalError as exc:
        raise WaitNodeFailure(
            node_id,
            "WAIT_DURATION_INVALID",
            f"等待节点 {node_id} 等待时长表达式无法求值：{exc}",
        )
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(float(raw))
    ):
        raise WaitNodeFailure(
            node_id,
            "WAIT_DURATION_INVALID",
            f"等待节点 {node_id} 等待时长表达式结果非法"
            f"（需为 {lo}-{hi} 秒，当前 {raw!r}）",
        )
    seconds = int(round(raw))
    if not lo <= seconds <= hi:
        raise WaitNodeFailure(
            node_id,
            "WAIT_DURATION_INVALID",
            f"等待节点 {node_id} 等待时长表达式结果非法"
            f"（需为 {lo}-{hi} 秒，当前 {raw!r}）",
        )
    return seconds




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
# `channel:` 前缀同走此路（docs/67 打包 M）：渠道适配器的能力入参本来就是 JSON 形状
# （create_refund 要 order_id/amount），此前落到下方 shop 硬编码装配只拿到 note ⇒ KeyError。
# 只有 demo 的 shop 适配器仍走按能力硬编码装配。
GENERIC_JSON_ADAPTERS = frozenset({"http", "database", "message", "memory"})


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
    condition_classifier: Any,
    registry: AdapterRegistry | None,
    approval_broker: ApprovalBroker,
    event_wait_broker: EventWaitBroker,
    graph_id: str,
    emit: EventCallback,
    approval_notifier: ApprovalNotifier | None = None,
    graph_resolver: Callable[[str], GraphDSL] | None,
    subgraph_depth: int,
    debug_controller: Any = None,
    frame_sink: Callable[[dict], None] | None = None,
    resume_claim: Callable[[str], bool] | None = None,
    resume: dict | None = None,
    graph_snapshot: dict | None = None,
    tracer: Tracer | None = None,
    base_span: Span | None = None,
    internal_spans: bool = False,
    now: datetime | None = None,
    subgraph_path: tuple[str, ...] = (),
    is_cancelled: Callable[[], bool] | None = None,
    tool_mocks: dict[str, Any] | None = None,
    shadow: bool = False,
    tool_permissions: dict[str, str] | None = None,
    jitter_rng: random.Random | None = None,
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
                        notifier=approval_notifier,
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

            # B 包（docs/27 §4.1）：节点边界协作式取消。调试流由 DebugController
            # 统一折叠为 DebugStopped（event:stopped）；普通流在此抛 RunCancelled。
            if debug_controller is None and is_cancelled is not None and is_cancelled():
                raise RunCancelled(node.id)

            if debug_controller is not None:
                debug_controller.before_node(node, state, now=now)
                if node.type == "human_approval" and not resume_here:
                    approval_payload = _register_approval(
                        node,
                        context=context,
                        trigger_payload=trigger_payload,
                        broker=approval_broker,
                        graph_id=graph_id,
                        notifier=approval_notifier,
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

            try:
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
                    output = _execute_condition(
                        node,
                        state,
                        context,
                        now=now,
                        classifier=condition_classifier,
                    )
                    if output.get("mode") == "llm":
                        message = (
                            f"{node.id}: llm branch={output['branch']} → {output['target']}"
                        )
                    else:
                        message = f"{node.id}: branch={output['branch']} → {output['target']}"
                elif node.type == "loop":
                    output = _execute_loop(node, state, context, now=now)
                    if output["exitReason"] is None:
                        if output["mode"] == "foreach":
                            message = (
                                f"{node.id}: foreach item {output['index']}/"
                                f"{len(output['items'])} → {output['target']}"
                            )
                        else:
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
                    if node.config["waitType"] == "event":
                        preset_events = trigger_payload.get("waitEvents") or {}
                        preset = preset_events.get(node.id)
                        if preset is not None:
                            # run inputs 预置：不登记 broker、不阻塞（docs/47 §3.3）。
                            event_payload = preset if isinstance(preset, dict) else {}
                            output = {
                                "mode": "wait",
                                "waitType": "event",
                                "eventKey": "",
                                "signaled": True,
                                "payload": event_payload,
                                "waitedSeconds": 0,
                                "resolvedBy": "input",
                                "token": "",
                            }
                            message = f"{node.id}: event preset from inputs"
                        else:
                            if resume_here:
                                wait_frame = resume.get("wait") or {}
                                if wait_frame.get("waitType") != "event":
                                    raise WaitNodeFailure(
                                        node.id,
                                        "WAIT_EVENT_FRAME_INVALID",
                                        f"等待节点 {node.id} 续跑帧缺少事件等待信息",
                                    )
                                frame_keys = wait_frame.get("eventKeys")
                                if isinstance(frame_keys, list) and frame_keys:
                                    event_keys = [str(k) for k in frame_keys]
                                else:
                                    event_keys = [str(wait_frame.get("eventKey", ""))]
                                event_key = event_keys[0]
                                wait_mode = (
                                    wait_frame.get("eventWaitMode")
                                    if wait_frame.get("eventWaitMode") in ("any", "all")
                                    else "any"
                                )
                                on_timeout = wait_frame.get("onTimeout", "continue")
                                timeout_seconds = int(wait_frame.get("timeoutSeconds", 0))
                                token = resume["resume_token"]
                            else:
                                raw_keys = node.config.get("eventKeys")
                                if isinstance(raw_keys, list) and raw_keys:
                                    # docs/54 多事件 OR 竞速：逐键插值并运行时校验，任一非法即失败
                                    event_keys = []
                                    for raw in raw_keys:
                                        rendered = interpolate(str(raw), context).strip()
                                        if not valid_event_key(rendered):
                                            raise WaitNodeFailure(
                                                node.id,
                                                "WAIT_EVENT_KEY_INVALID",
                                                f"等待节点 {node.id} 渲染后的事件标识非法：{rendered}",
                                            )
                                        event_keys.append(rendered)
                                else:
                                    template = str(node.config["eventKey"])
                                    rendered = interpolate(template, context).strip()
                                    if not valid_event_key(rendered):
                                        raise WaitNodeFailure(
                                            node.id,
                                            "WAIT_EVENT_KEY_INVALID",
                                            f"等待节点 {node.id} 渲染后的事件标识非法：{rendered}",
                                        )
                                    event_keys = [rendered]
                                event_key = event_keys[0]
                                wait_mode = (
                                    node.config.get("eventWaitMode")
                                    if node.config.get("eventWaitMode") in ("any", "all")
                                    else "any"
                                )
                                timeout_mode = node.config.get("timeoutMode", "static")
                                if timeout_mode == "expression":
                                    timeout_seconds = _resolve_wait_expression(
                                        node.id,
                                        node.config.get("timeoutExpression"),
                                        context,
                                        MIN_EVENT_WAIT_SECONDS,
                                        MAX_EVENT_WAIT_SECONDS,
                                    )
                                else:
                                    static_timeout = node.config.get("timeoutSeconds")
                                    if (
                                        isinstance(static_timeout, bool)
                                        or not isinstance(static_timeout, int)
                                        or not MIN_EVENT_WAIT_SECONDS
                                        <= static_timeout
                                        <= MAX_EVENT_WAIT_SECONDS
                                    ):
                                        raise WaitNodeFailure(
                                            node.id,
                                            "WAIT_DURATION_INVALID",
                                            f"等待节点 {node.id} 超时时间非法"
                                            f"（需为 {MIN_EVENT_WAIT_SECONDS}-"
                                            f"{MAX_EVENT_WAIT_SECONDS} 秒，"
                                            f"当前 {static_timeout!r}）",
                                        )
                                    timeout_seconds = int(static_timeout)
                                on_timeout = node.config.get("onTimeout", "continue")
                                token = event_wait_broker.request_any(
                                    event_keys=event_keys,
                                    node_id=node.id,
                                    graph_id=graph_id,
                                    timeout_seconds=timeout_seconds,
                                    mode=wait_mode,
                                )
                            wait_info = {
                                "token": token,
                                "eventKey": event_key,
                                "timeoutSeconds": timeout_seconds,
                                "onTimeout": on_timeout,
                            }
                            if len(event_keys) > 1:
                                wait_info["eventKeys"] = list(event_keys)
                            if wait_mode == "all":
                                wait_info["eventWaitMode"] = "all"
                            # 第二个 node_start 携带 wait 载荷，前端据此展示等待态。
                            emit({**start_event, "wait": wait_info})
                            if not resume_here:
                                _emit_frame(
                                    frame_sink,
                                    node,
                                    state,
                                    token=token,
                                    kind="wait",
                                    graph_id=graph_id,
                                    graph_snapshot=graph_snapshot,
                                    trigger_payload=trigger_payload,
                                    timeout_seconds=timeout_seconds,
                                    wait={
                                        "waitType": "event",
                                        "eventKey": event_key,
                                        "onTimeout": on_timeout,
                                        "timeoutSeconds": timeout_seconds,
                                        **(
                                            {"eventKeys": list(event_keys)}
                                            if len(event_keys) > 1
                                            else {}
                                        ),
                                        **(
                                            {"eventWaitMode": "all"}
                                            if wait_mode == "all"
                                            else {}
                                        ),
                                    },
                                )
                            started = time.monotonic()
                            event_payload = event_wait_broker.wait(
                                token, is_cancelled=is_cancelled
                            )
                            _gate_resume(resume_claim, token, node.id)
                            waited = int(time.monotonic() - started)
                            if event_payload is not None:
                                matched = event_payload.get("matchedEventKey", event_key)
                                output = {
                                    "mode": "wait",
                                    "waitType": "event",
                                    "eventKey": event_key,
                                    "matchedEventKey": matched,
                                    "signaled": True,
                                    "payload": event_payload,
                                    "waitedSeconds": waited,
                                    "resolvedBy": "signal",
                                    "token": token,
                                }
                                if len(event_keys) > 1:
                                    output["eventKeys"] = list(event_keys)
                                if wait_mode == "all":
                                    output["eventWaitMode"] = "all"
                                    output["matchedEventKeys"] = list(
                                        event_payload.get("matchedEventKeys", event_keys)
                                    )
                                    output["matchedPayloads"] = dict(
                                        event_payload.get("matchedPayloads", {})
                                    )
                                message = (
                                    f"{node.id}: event {matched} signaled after {waited}s"
                                )
                            elif on_timeout == "fail":
                                raise WaitNodeFailure(
                                    node.id,
                                    "WAIT_TIMEOUT_FAILED",
                                    f"等待事件 {event_key} 超时",
                                )
                            else:
                                output = {
                                    "mode": "wait",
                                    "waitType": "event",
                                    "eventKey": event_key,
                                    "signaled": False,
                                    "payload": {},
                                    "waitedSeconds": timeout_seconds,
                                    "resolvedBy": "timeout",
                                    "token": token,
                                }
                                if len(event_keys) > 1:
                                    output["eventKeys"] = list(event_keys)
                                if wait_mode == "all":
                                    output["eventWaitMode"] = "all"
                                    output["receivedKeys"] = (
                                        event_wait_broker.take_last_received(token)
                                    )
                                message = (
                                    f"{node.id}: event {event_key} timeout after "
                                    f"{timeout_seconds}s (continue)"
                                )
                    else:
                        duration_mode = node.config.get("durationMode", "static")
                        expression = node.config.get("durationExpression")
                        rendered_absolute_time = None
                        if duration_mode == "absolute" and not resume_here:
                            seconds, rendered_absolute_time = _resolve_absolute_wait(
                                node.id, node.config.get("absoluteTime"), context, now
                            )
                        elif duration_mode == "dynamic" and not resume_here:
                            seconds = _resolve_wait_expression(
                                node.id,
                                expression,
                                context,
                                MIN_WAIT_SECONDS,
                                MAX_WAIT_SECONDS,
                            )
                        elif resume_here:
                            # 续跑：按剩余时长等待（绝对 deadline 照扣，docs/24 §3.1）。
                            seconds = int(remaining_seconds(resume.get("deadline_at")))
                            if duration_mode == "absolute":
                                rendered_absolute_time = resume.get("deadline_at")
                        else:
                            seconds = int(node.config["durationSeconds"])
                        jitter_cfg = node.config.get("jitterSeconds", 0)
                        if (
                            isinstance(jitter_cfg, bool)
                            or not isinstance(jitter_cfg, int)
                            or jitter_cfg < 0
                        ):
                            jitter_cfg = 0
                        jitter_cfg = min(jitter_cfg, MAX_JITTER_SECONDS)
                        # docs/54 §3：仅首次进入叠加均匀抖动并入帧 deadline；续跑按剩余、不二次抖动。
                        if not resume_here and jitter_cfg > 0:
                            active_rng = jitter_rng if jitter_rng is not None else random
                            jitter_applied = int(active_rng.randint(0, jitter_cfg))
                        else:
                            jitter_applied = 0
                        actual_seconds = seconds + jitter_applied
                        # 首次进入用新随机 token 写帧；续跑沿用帧内原 token（认领按它判定）。
                        wait_token = (
                            str(resume["resume_token"]) if resume_here else uuid.uuid4().hex
                        )
                        if not resume_here:
                            _emit_frame(
                                frame_sink,
                                node,
                                state,
                                token=wait_token,
                                kind="wait",
                                graph_id=graph_id,
                                graph_snapshot=graph_snapshot,
                                trigger_payload=trigger_payload,
                                timeout_seconds=actual_seconds,
                            )
                        time.sleep(max(actual_seconds, 0))
                        _gate_resume(resume_claim, wait_token, node.id)
                        output = {
                            "mode": "wait",
                            "waitType": "duration",
                            "durationSeconds": actual_seconds,
                        }
                        # 仅首次进入且显式启用抖动时附加新字段；续跑按帧内剩余、不二次抖动，output 回归旧形状。
                        if not resume_here and jitter_cfg > 0:
                            output["plannedDurationSeconds"] = seconds
                            output["jitterSeconds"] = jitter_applied
                        if duration_mode == "dynamic":
                            output["durationMode"] = "dynamic"
                            output["durationExpression"] = expression
                        elif duration_mode == "absolute":
                            output["durationMode"] = "absolute"
                            output["absoluteTime"] = rendered_absolute_time
                        message = f"{node.id}: waited {actual_seconds}s"
                elif node.type == "human_approval":
                    output, message = _await_human_approval(
                        node,
                        token=approval_payload["token"],
                        trigger_payload=trigger_payload,
                        broker=approval_broker,
                        notifier=approval_notifier,
                    )
                    # docs/62 §2 D-2：审批返回 ≠ 获得继续的权利——先认领越过点，输家就此停手。
                    _gate_resume(resume_claim, approval_payload["token"], node.id)
                elif node.type == "subgraph":
                    output, message = _execute_subgraph(
                        node,
                        context=context,
                        registry=registry,
                        decision_client=decision_client,
                        condition_classifier=condition_classifier,
                        approval_broker=approval_broker,
                        event_wait_broker=event_wait_broker,
                        approval_notifier=approval_notifier,
                        resolver=graph_resolver,
                        depth=subgraph_depth,
                        tracer=tracer,
                        now=now,
                        emit=emit,
                        subgraph_path=subgraph_path,
                        is_cancelled=is_cancelled,
                        debug_controller=debug_controller,
                        shadow=shadow,
                        jitter_rng=jitter_rng,
                    )
                elif tool_mocks is not None and node.id in tool_mocks:
                    # Mock 回放（docs/28 §2.2）：以录制桩 output 替代真实适配器调用，
                    # 隔离外部系统；不触达 registry/harness、不发 tool span、不发 tool_metric。
                    # node_end/outputs/trace 与真实分支同构，比对两端各自 normalize 必然一致。
                    # 子图重入不透传 tool_mocks（steps 只录顶层 node_end）。
                    output = tool_mocks[node.id]
                    message = f"{node.id}({node.type}): executed"
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
                    tool_started = time.monotonic()
                    with tool_cm as tool_span:
                        output = _execute_tool(
                            node,
                            context,
                            registry,
                            shadow=shadow,
                            tool_permissions=tool_permissions,
                        )
                        if isinstance(tool_span, Span) and _node_failure(output):
                            tool_span.end("error")
                    # docs/28 §4.1 ⑧：executor 无条件埋点（debug 流也 emit，worker 不采集）；
                    # mock 命中走上方分支不发，子层 tool_metric 经 _namespaced_emit 白名单吞掉。
                    # docs/33 §3.2：影子运行零监控污染——短路/透传工具均不发 tool_metric。
                    if emit is not None and not shadow:
                        metric_event = _tool_metric_event(
                            node_id=node.id,
                            tool_name=tool_name,
                            output=output,
                            duration_ms=(time.monotonic() - tool_started) * 1000,
                        )
                        if metric_event is not None:
                            emit(metric_event)
                    message = f"{node.id}({node.type}): executed"
            except (RunCancelled, DebugStopped, RunSuperseded):
                raise
            except Exception as exc:
                # docs/28 §3.2：仅调试流且该节点配置异常断点时暂停观测；
                # 未配置 on_exception 立即返回，随后裸 raise，失败语义与现状一致。
                if debug_controller is not None:
                    debug_controller.on_exception(node, exc, state)
                raise

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
    wait: dict[str, Any] | None = None,
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
            wait=wait,
        )
    )


def _register_approval(
    node: NodeDSL,
    *,
    context: dict[str, Any],
    trigger_payload: dict[str, Any],
    broker: ApprovalBroker,
    graph_id: str,
    notifier: ApprovalNotifier | None = None,
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
    recipients = _resolve_notify_recipients(config.get("notifyEmails"), context)
    token = broker.request(
        node_id=node.id,
        graph_id=graph_id,
        summary=summary,
        approver=approver,
        timeout_seconds=timeout_seconds,
        card_template_id=card_template_id,
        card_context=card_context,
        notify_recipients=recipients,
    )
    # docs/35 §2：挂起通知（旁路，fail-safe）。
    notified = False
    notify_error: str | None = None
    if notifier is not None and recipients:
        try:
            notifier.notify_pending(
                graph_id=graph_id,
                node_id=node.id,
                token=token,
                summary=summary,
                approver=approver,
                timeout_seconds=timeout_seconds,
                recipients=recipients,
            )
            notified = True
        except Exception as exc:  # noqa: BLE001 旁路通知任何异常都不得阻断图
            notify_error = str(exc)
            logger.warning("审批挂起通知失败 node=%s: %s", node.id, exc)
    payload: dict[str, Any] = {
        "token": token,
        "summary": summary,
        "approver": approver,
        "timeoutSeconds": timeout_seconds,
        "notified": notified,
    }
    if notify_error is not None:
        payload["notifyError"] = notify_error
    if card_template_id:
        payload["cardTemplateId"] = card_template_id
    return payload


def _resolve_notify_recipients(raw: Any, context: dict[str, Any]) -> list[str]:
    # docs/35 §2.1：notifyEmails 运行时插值，剔除空值与插值后无 @ 的项，去重保序。
    if not isinstance(raw, list):
        return []
    recipients: list[str] = []
    for item in raw:
        rendered = interpolate(str(item), context).strip()
        if not rendered:
            continue
        if "@" not in rendered:
            logger.warning("审批通知邮箱插值后不含 @，已丢弃：%r", item)
            continue
        if rendered not in recipients:
            recipients.append(rendered)
    return recipients


def _await_human_approval(
    node: NodeDSL,
    *,
    token: str,
    trigger_payload: dict[str, Any],
    broker: ApprovalBroker,
    notifier: ApprovalNotifier | None = None,
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
    # docs/37 §4：超时/预置来源由 loader 发结果邮件（人工/邮件链接来源在 API helper 发，
    # 避免双发）；旁路 fail-safe。
    if notifier is not None and resolved_by in ("timeout", "input"):
        recipients = broker.get_notify_recipients(token)
        if recipients:
            try:
                notifier.notify_decided(
                    graph_id=info["graph_id"],
                    node_id=node.id,
                    summary=info["summary"],
                    decision=decision,
                    resolved_by=resolved_by,
                    comment=info.get("comment", ""),
                    recipients=recipients,
                )
            except Exception as exc:  # noqa: BLE001 结果通知任何异常都不得阻断图
                logger.warning("审批结果通知失败 node=%s: %s", node.id, exc)
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


def _namespaced_emit(parent: EventCallback, path: tuple[str, ...]) -> EventCallback:
    """A 包（docs/27 §3.2）：子图重入的命名空间事件回调。

    - 只转发节点级 ``node_start``/``node_end``（含 node_start 上的 approval 载荷）；
    - 子层 ``run_end``/result 等终帧一律吞掉，避免子图结束被 SSE 误判为整图结束
      （子图结果由父图 subgraph 节点自身的 node_end.output 体现）；
    - 给本层直接节点事件附加 ``subgraphPath``＝每层父图 subgraph 节点 id 路径；
      更深层 wrapper 已附完整路径的事件原样转发（完整路径在最深一层一次性构造，
      嵌套重入时不在此重复叠加）；
    - 回调自身任何异常都不得影响图执行（fail-safe，§3.2 第 5 条）。
    """
    path_list = list(path)

    def _emit(event: dict[str, Any]) -> None:
        try:
            # docs/28 §3.3：子层调试 paused/debug_log 同样上屏（附 subgraphPath）；
            # run_end/result/stopped/error/cancelled 等终帧仍吞，整图终帧唯一。
            if event.get("type") not in (
                "node_start",
                "node_end",
                "paused",
                "debug_log",
            ):
                return
            if event.get("subgraphPath"):
                parent(event)
            else:
                parent({**event, "subgraphPath": path_list})
        except Exception:
            return

    return _emit


def _execute_subgraph(
    node: NodeDSL,
    *,
    context: dict[str, Any],
    registry: AdapterRegistry | None,
    decision_client: Any,
    condition_classifier: Any,
    approval_broker: ApprovalBroker,
    event_wait_broker: EventWaitBroker,
    approval_notifier: ApprovalNotifier | None = None,
    resolver: Callable[[str], GraphDSL] | None,
    depth: int,
    tracer: Tracer | None = None,
    now: datetime | None = None,
    emit: EventCallback | None = None,
    subgraph_path: tuple[str, ...] = (),
    is_cancelled: Callable[[], bool] | None = None,
    debug_controller: Any = None,
    shadow: bool = False,
    jitter_rng: random.Random | None = None,
) -> tuple[dict[str, Any], str]:
    """进程内重入执行被引用子图（04 §5.7）；任何异常 fail-safe 为 failed，父 run 仍 completed。

    影子是运行级语义，子图重入必须透传 shadow（与 tool_mocks 不同）：子图内写能力同样短路。
    """
    graph_ref = str(node.config.get("graphId", ""))
    # A 包（docs/27 §3.1）：本 subgraph 节点在父图中的完整路径，子层内部事件据此上屏。
    child_path = (*subgraph_path, node.id)
    child_emit = _namespaced_emit(emit, child_path) if emit is not None else None
    # docs/28 §3.3：子层复用同一 controller/session，仅把其 emit 换成本层命名空间回调，
    # 使子层 paused/debug_log 帧附 subgraphPath；同线程顺序重入，finally 弹栈恢复。
    if debug_controller is not None and child_emit is not None:
        debug_controller.push_namespaced_emit(child_emit)
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
                condition_classifier=condition_classifier,
                registry=registry,
                approval_broker=approval_broker,
                event_wait_broker=event_wait_broker,
                approval_notifier=approval_notifier,
                graph_id=graph_ref,
                emit=child_emit,
                graph_resolver=resolver,
                _subgraph_depth=depth + 1,
                _subgraph_path=child_path,
                tracer=tracer,
                now_override=now,
                is_cancelled=is_cancelled,
                debug_controller=debug_controller,
                _parent_span=sub_span if isinstance(sub_span, Span) else None,
                shadow=shadow,
                jitter_rng=jitter_rng,
            )
    except (RunCancelled, DebugStopped, RunSuperseded):
        # 取消与调试急停/异常断点 stop 均须穿透子图 fail-safe 兜底，冒泡终止整图
        # （docs/27 §4.1 修 RunCancelled；docs/28 §3.2/§3.3 补 DebugStopped）。
        # RunSuperseded 同属控制流：被这里的 fail-safe 吞掉＝输家继续执行，正是 029 要防的事故。
        if isinstance(sub_span, Span):
            sub_span.end("error")
        raise
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
    finally:
        if debug_controller is not None and child_emit is not None:
            debug_controller.pop_namespaced_emit()

    output = {
        "mode": "subgraph",
        "graphId": graph_ref,
        "status": "success",
        "outputs": result["outputs"],
        "trace": result["trace"],
    }
    return output, f"{node.id}: {graph_ref} success ({len(child.nodes)} nodes)"


def _subgraph_output_index(
    graph: GraphDSL,
    resolver: Callable[[str], GraphDSL] | None,
) -> dict[str, set[str]]:
    """D30/B2：subgraph 节点 id -> 被引子图内部节点 id 集，供 outputs.<内部id> 存在性校验。

    只解析直接一层（嵌套子图由递归校验各自覆盖）；解析不到（无 resolver / 子图缺失 /
    钉版失败）的子图不入索引，模板校验降级为仅放行 outputs 根（子图不存在另有专规报错）。
    """
    index: dict[str, set[str]] = {}
    if resolver is None:
        return index
    for node in graph.nodes:
        if node.type != "subgraph":
            continue
        ref = node.config.get("graphId")
        if not isinstance(ref, str) or not ref.strip():
            continue
        try:
            child = resolver(ref)
        except KeyError:
            continue
        if child is None:
            continue
        index[node.id] = {candidate.id for candidate in child.nodes}
    return index


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

        def add_own(
            message: str,
            pointer: str | None = None,
            *,
            code: str,
            params: dict[str, Any] | None = None,
        ) -> None:
            # 深层节点错误只挂最外层子图节点 id，pointer 缓做。
            location = _loc(node.id, pointer) if top_node_id is None else _loc(top_node_id)
            issues.append((message, location, code, {'owner': top_node_id or node.id, **(params or {})}))

        if not isinstance(ref, str) or not ref.strip():
            add_own(f"{prefix} 必须选择引用的已保存子图（graphId）", "/graphId",
                    code="SUBREF_GRAPH_ID_REQUIRED")
            continue
        if resolver is None:
            add_own(f"{prefix} 子图解析器未注入（graphId={ref}）",
                    code="SUBREF_RESOLVER_MISSING", params={"ref": ref})
            continue
        if ref == graph_id:
            add_own(f"{prefix} 子图不能直接引用自身：{ref}",
                    code="SUBREF_SELF_REF", params={"ref": ref})
            continue
        if ref in chain:
            add_own(
                f"{prefix} 检测到跨图引用环：{' → '.join((*chain, ref))}",
                code="SUBREF_CYCLE", params={"chain": " → ".join((*chain, ref))},
            )
            continue
        if depth + 1 > MAX_SUBGRAPH_DEPTH:
            add_own(
                f"{prefix} 子图嵌套深度超过上限 {MAX_SUBGRAPH_DEPTH}（引用链：{' → '.join((*chain, ref))}）",
                code="SUBREF_DEPTH_EXCEEDED",
                params={"max": MAX_SUBGRAPH_DEPTH, "chain": " → ".join((*chain, ref))},
            )
            continue
        try:
            child = resolver(ref)
        except KeyError:
            add_own(f"{prefix} 引用的子图不存在：{ref}",
                    code="SUBREF_NOT_FOUND", params={"ref": ref})
            continue
        if child is None:
            add_own(f"{prefix} 引用的子图不存在：{ref}",
                    code="SUBREF_NOT_FOUND", params={"ref": ref})
            continue
        child_messages, _child_locations, child_codes, child_params = validate_graph_report(
            child,
            tool_output_schemas,
            check_refs=True,
            subgraph_index=_subgraph_output_index(child, resolver),
        )
        for child_error, child_code, child_prm in zip(
            child_messages, child_codes, child_params
        ):
            issues.append((
                f"子图 {ref}：{child_error}",
                _loc(owner),
                child_code,
                {**child_prm, "subgraph": ref, "owner": owner},
            ))
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


def _execute_condition(
    node: NodeDSL,
    state: GraphState,
    context: dict[str, Any],
    *,
    now: datetime | None = None,
    classifier: Any = None,
) -> dict[str, Any]:
    """condition 求值（04 §5.2）；rule 顺序短路，llm 单次分类；异常 fail-safe 走 defaultTarget。"""
    config = node.config
    if config.get("conditionMode", "rule") == "llm":
        return _execute_llm_condition(node, state, context, classifier=classifier)

    evaluation: list[dict[str, Any]] = []
    errors: list[str] = []
    target: str | None = None
    branch = "__default__"
    for item in config.get("branches", []):
        label, expression = item["label"], item["expression"]
        try:
            result = evaluate_expression(expression, context, now=now)
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
        target = config["defaultTarget"]
    return {
        "branch": branch,
        "target": target,
        "evaluation": evaluation,
        "expression_errors": errors,
    }


_CONTEXT_LIMIT = 12000


def _execute_llm_condition(
    node: NodeDSL, state: GraphState, context: dict[str, Any], *, classifier: Any
) -> dict[str, Any]:
    """LLM 单次分类选唯一分支（04 §5.2 追加段，docs/48）；任何失败 fail-safe 走 defaultTarget。"""
    config = node.config
    branches = config.get("branches", [])
    errors: list[str] = []

    try:
        context_text = json.dumps(
            {"global": context.get("global", {}), "nodes": _node_outputs_projection(context)},
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001 - 序列化兜底：default=str 仍失败即分类失败
        context_text = "{}"
        errors.append(f"上下文序列化失败：{exc}")
    if len(context_text) > _CONTEXT_LIMIT:
        context_text = context_text[:_CONTEXT_LIMIT] + "\n…<截断>"

    instruction = str(config.get("classifierPrompt") or "").strip()
    label: str
    try:
        label = classifier.classify(
            branches=branches, context_text=context_text, instruction=instruction
        )
    except Exception as exc:  # noqa: BLE001 - 供应商错误/解析错误统一 fail-safe
        label = "__default__"
        errors.append(str(exc))

    target: str | None = None
    if label != "__default__":
        for item in branches:
            if item["label"] == label:
                target = item["target"]
                break
        if target is None:
            errors.append(f"LLM 返回了未知分支标签：{label}")
            label = "__default__"
    if target is None:
        target = config["defaultTarget"]

    evaluation = [
        {
            "label": item["label"],
            "description": item["description"],
            "result": item["label"] == label if label != "__default__" else None,
        }
        for item in branches
    ]
    return {
        "mode": "llm",
        "branch": label,
        "target": target,
        "evaluation": evaluation,
        "llm_errors": errors,
    }


def _node_outputs_projection(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items() if key != "global"}


def _execute_loop(
    node: NodeDSL, state: GraphState, context: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """条件循环重入求值（04 §5.3）；达上限/求值异常 fail-safe 走 exitTarget。"""
    config = node.config
    if config.get("mode") == "foreach":
        return _execute_foreach(node, state, context, now=now)
    body_target = config["bodyTarget"]
    exit_target = config["exitTarget"]
    max_iterations = int(config.get("maxIterations", 10))

    previous = state["outputs"].get(node.id, {})
    iterations = int(previous.get("iterations", 0)) if isinstance(previous, dict) else 0

    expression_errors: list[str] = []
    expression_error_codes: list[str] = []
    exit_reason: str | None = None

    if iterations >= max_iterations:
        target = exit_target
        exit_reason = "max_iterations"
        expression_errors.append(f"已达最大次数 {max_iterations}，强制退出循环")
        expression_error_codes.append("LOOP_MAX_ITERATIONS")
    else:
        # 首轮自身产出尚不存在；播种 index 供 {{loop-x.index}} 求值
        loop_context = {**context, node.id: {"index": iterations, "iterations": iterations}}
        try:
            result = evaluate_expression(config["continueExpression"], loop_context, now=now)
        except ConditionEvalError as exc:
            target = exit_target
            exit_reason = "expression_error"
            expression_errors.append(str(exc))
            expression_error_codes.append(exc.code)
        else:
            if not isinstance(result, bool):
                target = exit_target
                exit_reason = "expression_error"
                expression_errors.append(
                    f"继续条件结果必须是布尔值，实际为 {type(result).__name__}"
                )
                expression_error_codes.append("COND_TYPE_MISMATCH")
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
        "expressionErrorCodes": expression_error_codes,
    }


def _foreach_output(
    *,
    items: list[Any],
    index: int,
    item: Any,
    results: list[Any],
    target: str,
    exit_reason: str | None,
    expression_errors: list[str],
    expression_error_codes: list[str] | None = None,
) -> dict[str, Any]:
    if expression_error_codes is None:
        expression_error_codes = [""] * len(expression_errors)
    return {
        "mode": "foreach",
        "items": items,
        "index": index,
        "iterations": index,
        "item": item,
        "results": results,
        "target": target,
        "exitReason": exit_reason,
        "expression_errors": expression_errors,
        "expressionErrorCodes": expression_error_codes,
    }


def _execute_foreach(
    node: NodeDSL, state: GraphState, context: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """遍历循环（04 §5.3 foreach 段，docs/45）：数组首轮求值冻结、串行逐项、回边聚合。"""
    config = node.config
    body_target = config["bodyTarget"]
    exit_target = config["exitTarget"]

    previous = state["outputs"].get(node.id, {})
    if not isinstance(previous, dict):
        previous = {}

    def fail_exit(message: str, exit_reason: str, code: str = "") -> dict[str, Any]:
        return _foreach_output(
            items=[],
            index=0,
            item=None,
            results=[],
            target=exit_target,
            exit_reason=exit_reason,
            expression_errors=[message],
            expression_error_codes=[code],
        )

    if "items" not in previous:
        seed_context = {**context, node.id: {"index": 0, "iterations": 0}}
        try:
            items = evaluate_expression(config["itemsExpression"], seed_context, now=now)
        except ConditionEvalError as exc:
            return fail_exit(str(exc), "expression_error", exc.code)
        if not isinstance(items, list):
            return fail_exit(
                f"遍历对象必须是数组，实际为 {type(items).__name__}",
                "expression_error",
                "COND_TYPE_MISMATCH",
            )
        if len(items) > MAX_LOOP_ITERATIONS:
            return fail_exit(
                f"遍历数组长度 {len(items)} 超过上限 {MAX_LOOP_ITERATIONS}",
                "items_too_large",
                "LOOP_ITEMS_TOO_LARGE",
            )
        if not items:
            return _foreach_output(
                items=[],
                index=0,
                item=None,
                results=[],
                target=exit_target,
                exit_reason="empty",
                expression_errors=[],
                expression_error_codes=[],
            )
        return _foreach_output(
            items=items,
            index=0,
            item=items[0],
            results=[],
            target=body_target,
            exit_reason=None,
            expression_errors=[],
            expression_error_codes=[],
        )

    items = previous["items"]
    index = int(previous.get("index", 0))
    results = list(previous.get("results", []))

    collect_target = config.get("collectTarget") or ""
    expression_errors: list[str] = []
    expression_error_codes: list[str] = []
    if collect_target:
        collected = state["outputs"].get(collect_target)
        if collected is None:
            expression_errors.append(
                f"聚合节点 {collect_target} 无产出，跳过本轮收集"
            )
            expression_error_codes.append("FOREACH_COLLECT_MISSING")
        else:
            results.append(collected)

    next_index = index + 1
    if next_index >= len(items):
        return _foreach_output(
            items=items,
            index=next_index,
            item=items[index],
            results=results,
            target=exit_target,
            exit_reason="completed",
            expression_errors=expression_errors,
            expression_error_codes=expression_error_codes,
        )
    return _foreach_output(
        items=items,
        index=next_index,
        item=items[next_index],
        results=results,
        target=body_target,
        exit_reason=None,
        expression_errors=expression_errors,
        expression_error_codes=expression_error_codes,
    )


def _make_break_gate(
    loop_node: NodeDSL,
    emit: EventCallback,
    *,
    tracer: Tracer | None = None,
    base_span: Span | None = None,
):
    """D17/A2 break 合成网关：体内 condition 选中 break 分支（直连 exitTarget）后，
    经本网关节点把循环控制态收口为 exitReason='break' 再放行进退出目标。

    与 __join__ 同属编译期内部节点（用户图中不存在、不新增 DSL 节点类型）；
    outputs 为浅合并，故须先读 loop 节点既有产出（iterations/index）再整体回写。
    """
    exit_target = loop_node.config["exitTarget"]
    is_foreach = loop_node.config.get("mode") == "foreach"

    def gate(state: GraphState) -> dict[str, Any]:
        previous = state["outputs"].get(loop_node.id, {})
        if not isinstance(previous, dict):
            previous = {}
        iterations = int(previous.get("iterations", 0))
        if is_foreach:
            # break 发生在当前元素的体执行之后、下一次回边之前；collectTarget 本轮产出
            # 尚未经回边追加，在此补收，保证已执行体的结果不丢。
            results = list(previous.get("results", []))
            collect_target = loop_node.config.get("collectTarget") or ""
            if collect_target:
                collected = state["outputs"].get(collect_target)
                if collected is not None:
                    results.append(collected)
            final_index = iterations + 1
            output = _foreach_output(
                items=previous.get("items", []),
                index=final_index,
                item=previous.get("item"),
                results=results,
                target=exit_target,
                exit_reason="break",
                expression_errors=[],
            )
        else:
            output = {
                "mode": "while",
                "iterations": iterations,
                "index": iterations,
                "target": exit_target,
                "exitReason": "break",
                "expression_errors": [],
            }
        count = final_index if is_foreach else iterations
        message = f"{loop_node.id}: exit (break) after {count} → {exit_target}"
        # 以 loop 节点自身补发 node_end（同 __join__ 汇聚补发模式），供画布展示 break 终态。
        emit({"type": "node_end", "node_id": loop_node.id, "node_type": "loop", "output": output})
        return {"outputs": {loop_node.id: output}, "messages": [message]}

    return gate


def _execute_tool(
    node: NodeDSL,
    context: dict[str, Any],
    registry: AdapterRegistry | None,
    *,
    shadow: bool = False,
    tool_permissions: dict[str, str] | None = None,
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

    # 影子模式权限判定（docs/33 §3.2）：仅声明为 read 的能力透传，其余（含权限表缺失，
    # 保守起见）一律短路为意图回执，不触达适配器。
    permission = (tool_permissions or {}).get(tool_name)
    shadow_blocked = shadow and permission != "read"

    if adapter_id in GENERIC_JSON_ADAPTERS or adapter_id.startswith(("openapi:", "channel:")):
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
        # 短路点在 JSON 解析之后：非法 params 仍返 INVALID_PARAMETER，不伪造 dry-run（docs/33 §10.1）。
        if shadow_blocked:
            return _shadow_dry_run(tool_name, capability_name, permission, parameters)
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

    # 写能力短路点在专用参数组装（process_refund 的 decision/order_id/note 等）之后，
    # 回执截获最终参数；process_refund 缺上游 decision 在上方已返 FAILED（配置错误不伪造 dry-run）。
    if shadow_blocked:
        return _shadow_dry_run(tool_name, capability_name, permission, parameters)
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


def _tool_metric_event(
    *, node_id: str, tool_name: str, output: dict[str, Any], duration_ms: float
) -> dict[str, Any]:
    """docs/28 §4.1 ⑧：把工具节点终态 output 归一为 tool_metric 事件。

    - SIMULATED（无 ``adapter/capability`` 或 registry 缺失的本地构造）记 ``SIMULATED``、无 code；
    - 其余取外层 action_status（ActionResult 状态），回退 result.status 并大写归一为 SUCCESS/FAILED；
    - error_code 取 result.code（INVALID_PARAMETER/UNKNOWN/适配器错误码），成功通常为空。
    """
    result = output.get("result") if isinstance(output, dict) else None
    if isinstance(result, dict) and result.get("status") == "SIMULATED":
        action_status = "SIMULATED"
        error_code = None
    else:
        raw_status = output.get("action_status") if isinstance(output, dict) else None
        if raw_status is None and isinstance(result, dict):
            raw_status = result.get("status")
        action_status = "FAILED" if str(raw_status or "SUCCESS").upper() == "FAILED" else "SUCCESS"
        code = result.get("code") if isinstance(result, dict) else None
        error_code = str(code) if code else None
    return {
        "type": "tool_metric",
        "node_id": node_id,
        "tool": tool_name,
        "duration_ms": round(float(duration_ms), 3),
        "action_status": action_status,
        "error_code": error_code,
    }


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


def _branch_outcomes(meta: dict[str, Any], outputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """逐分支判定终态/成功/失败（04 §5.4，all_success 与 any_success 共用）。

    settled：该分支已有末端节点产出（走到汇聚）；failure：区域内首个失败节点；
    success：settled 且区域内无失败。any_success 下未 settled 的分支将被短路跳过。
    """
    outcomes: dict[str, dict[str, Any]] = {}
    for entry in meta["entries"]:
        area = meta["entry_areas"][entry]
        terminals = meta["entry_terminals"][entry]
        # 被 any_success 短路守卫跳过（skipped 产出）的末端不算真正走到汇聚。
        settled = any(
            terminal in outputs
            and not (
                isinstance(outputs.get(terminal), dict)
                and outputs[terminal].get("skipped")
            )
            for terminal in terminals
        )
        failure = ""
        for area_node in area:
            area_output = outputs.get(area_node)
            if isinstance(area_output, dict) and not area_output.get("skipped"):
                found = _node_failure(area_output)
                if found:
                    failure = found
                    break
        outcomes[entry] = {
            "settled": settled,
            "success": settled and not failure,
            "failure": failure,
        }
    return outcomes


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
        # 汇聚目标已执行即说明已放行过：any_success 下迟到的残留分支再次触发网关时
        # 空转（不重复 emit / 不覆盖结论），由 route_gate 收口到 END。
        if meta["join_target"] in outputs:
            return {"messages": []}
        outcomes = _branch_outcomes(meta, outputs)
        if strategy == "any_success":
            # D18/A1 OR-join：任一分支成功即汇聚；否则等所有分支走到终态，全失败才 failed。
            any_ok = any(o["success"] for o in outcomes.values())
            all_settled = all(o["settled"] for o in outcomes.values())
            if not (any_ok or all_settled):
                return {"messages": []}
            overall = "success" if any_ok else "failed"
        else:
            if not all(o["settled"] for o in outcomes.values()):
                return {"messages": []}
            overall = (
                "failed"
                if strategy == "all_success"
                and any(o["failure"] for o in outcomes.values())
                else "success"
            )

        branches: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        failed: list[tuple[str, str]] = []
        for entry in meta["entries"]:
            outcome = outcomes[entry]
            executed = [
                terminal for terminal in meta["entry_terminals"][entry] if terminal in outputs
            ]
            if executed:
                result[entry] = outputs[executed[0]]
            if outcome["failure"]:
                status = "failed"
                failed.append((labels.get(entry, entry), outcome["failure"]))
            elif outcome["settled"]:
                status = "success"
            else:
                # any_success 抢先汇聚时该分支尚未走到末端：标记短路跳过（未启动）。
                status = "skipped"
            branches.append(
                {
                    "label": labels.get(entry, entry),
                    "target": entry,
                    "status": status,
                    "error": outcome["failure"],
                }
            )
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


def _guard_any_success(
    base_executor: Callable[[GraphState], dict],
    parallel_id: str,
    node_id: str,
    node_type: str,
    safe_target: str | None,
    emit: EventCallback,
) -> Callable[[GraphState], dict]:
    """D18/A1 any_success 短路守卫：OR-join 抢先成功汇聚后，区域内尚未启动的节点
    不再产生副作用（不发起 tool 调用、不进入 wait/human_approval）。

    已发起的外部事实不可逆、不回滚（fail-safe）：守卫只在 executor 入口判定，
    已进入执行的节点不受影响。条件路由节点（condition/human/loop）需补一个
    target，使其条件边仍能流向汇聚网关；普通节点靠普通边自然链式跳过。
    """

    def guarded(state: GraphState) -> dict:
        joined = state["outputs"].get(parallel_id)
        if (
            isinstance(joined, dict)
            and joined.get("joinStrategy") == "any_success"
            and joined.get("status") == "success"
        ):
            output: dict[str, Any] = {
                "skipped": True,
                "reason": "any_success_joined",
                "parallelId": parallel_id,
            }
            if safe_target is not None:
                output["target"] = safe_target
            emit(
                {
                    "type": "node_end",
                    "node_id": node_id,
                    "node_type": node_type,
                    "output": output,
                }
            )
            return {
                "outputs": {node_id: output},
                "messages": [f"{node_id}: skipped (any_success {parallel_id} joined)"],
            }
        return base_executor(state)

    return guarded


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


def _tool_permissions(registry: AdapterRegistry) -> dict[str, str]:
    """影子模式编译期权限表（docs/33 §3.2）：``<adapter_id>/<tool> -> permission``。

    照 ``_tool_output_schemas`` 遍历 ``registry.list_adapters()``；影子运行据此判定
    读透传/写短路，不做能力名启发式（通用通道如 http/request 声明 write，保守短路）。
    """
    table: dict[str, str] = {}
    for adapter in registry.list_adapters():
        for tool in adapter["tools"]:
            table[f"{adapter['id']}/{tool['name']}"] = tool["permission"]
    return table


def _shadow_dry_run(
    tool_name: str, capability_name: str, permission: str | None, parameters: Any
) -> dict[str, Any]:
    """写能力在影子运行下的意图回执（docs/33 §3.2）：不触达适配器，仅记录「本会怎么做」。

    ``parameters`` 为短路点之前已组装完成的最终参数（专用通道 dict / 通用通道解析后 JSON）。
    """
    return {
        "result": {
            "status": "SHADOW_DRY_RUN",
            "tool": tool_name,
            "capability": capability_name,
            "permission": permission,
            "parameters": parameters,
        },
        "action_status": "SHADOW_DRY_RUN",
    }


def compile_graph(
    graph: GraphDSL,
    *,
    decision_client: Any | None = None,
    condition_classifier: Any | None = None,
    registry: AdapterRegistry | None = None,
    approval_broker: ApprovalBroker | None = None,
    event_wait_broker: EventWaitBroker | None = None,
    approval_notifier: ApprovalNotifier | None = None,
    graph_id: str = "adhoc",
    emit: EventCallback | None = None,
    trigger_payload: dict[str, Any] | None = None,
    graph_resolver: Callable[[str], GraphDSL] | None = None,
    _subgraph_depth: int = 0,
    debug_controller: Any = None,
    frame_sink: Callable[[dict], None] | None = None,
    resume_claim: Callable[[str], bool] | None = None,
    resume: dict | None = None,
    _subgraph_path: tuple[str, ...] = (),
    validate_with: GraphDSL | None = None,
    tracer: Tracer | None = None,
    graph_version: str | None = None,
    now: datetime | None = None,
    _parent_span: Span | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    tool_mocks: dict[str, Any] | None = None,
    shadow: bool = False,
    jitter_rng: random.Random | None = None,
):
    decision_client = decision_client or get_decision_client()
    condition_classifier = condition_classifier or get_condition_classifier()
    registry = registry if registry is not None else build_demo_registry()
    approval_broker = approval_broker or _default_approval_broker
    event_wait_broker = event_wait_broker or _default_event_wait_broker
    # C（docs/27 §2.1）：单次编译/运行固定一个 UTC 时钟，供 today()/now() 与条件断点求值；
    # 录制/回放由 run_graph(now_override=) 注入冻结时刻，未注入则入口取一次当前 UTC。
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
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
    # 影子模式编译期权限表（docs/33 §3.2）：随执行器闭包透传，读透传/写短路据此判定。
    tool_permissions = _tool_permissions(registry)
    # D30/B2：用 resolver 预算子图内部节点索引（解析不到则降级），供 outputs.<内部id> 存在性校验。
    subgraph_index = _subgraph_output_index(validation_graph, graph_resolver)
    # 编译期 L2 复查（04 §6.5 防绕过）：parse_graph 时无注册表，引用与工具深层路径在此补判。
    ref_errors, ref_locations, ref_codes, ref_params = validate_graph_report(
        validation_graph, tool_schemas, check_refs=True, subgraph_index=subgraph_index
    )
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
        for index, (_message, location, _code, _params) in enumerate(subgraph_issues)
        if location is not None
    ]
    ref_errors = ref_errors + [message for message, _location, _code, _params in subgraph_issues]
    ref_locations = ref_locations + subgraph_locations
    ref_codes = ref_codes + [code for _message, _location, code, _params in subgraph_issues]
    ref_params = ref_params + [prm for _message, _location, _code, prm in subgraph_issues]
    if ref_errors:
        raise GraphValidationError(ref_errors, ref_locations, ref_codes, ref_params)

    builder = StateGraph(GraphState)

    # 边表与 parallel 区域需在 add_node 前就绪：any_success 短路守卫要按区域包裹 executor。
    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source, []).append(edge.target)
    parallels = [node for node in graph.nodes if node.type == "parallel"]
    metas = {node.id: _parallel_meta(node, outgoing) for node in parallels}

    # D18/A1 any_success：region 内节点 -> (parallel_id, 条件路由节点的安全 target)。
    routing_kinds = {"condition", "human_approval", "loop"}
    type_by_node = {n.id: n.type for n in graph.nodes}
    skip_guards: dict[str, tuple[str, str | None]] = {}
    for pnode in parallels:
        if pnode.config.get("joinStrategy") != "any_success":
            continue
        pmeta = metas[pnode.id]
        join_t = pmeta["join_target"]
        for member in pmeta["region"]:
            if type_by_node.get(member) in routing_kinds:
                outs = outgoing.get(member, [])
                safe_target = join_t if join_t in outs else (outs[0] if outs else None)
            else:
                safe_target = None
            skip_guards[member] = (pnode.id, safe_target)

    for node in graph.nodes:
        executor = _make_executor(
            node,
            trigger_payload=payload,
            decision_client=decision_client,
            condition_classifier=condition_classifier,
            registry=registry,
            approval_broker=approval_broker,
            event_wait_broker=event_wait_broker,
            approval_notifier=approval_notifier,
            graph_id=graph_id,
            emit=emit,
            graph_resolver=graph_resolver,
            subgraph_depth=_subgraph_depth,
            debug_controller=debug_controller,
            frame_sink=frame_sink,
            resume_claim=resume_claim,
            resume=resume,
            graph_snapshot=graph_snapshot,
            tracer=tracer,
            base_span=base_span,
            internal_spans=internal_spans,
            now=now,
            subgraph_path=_subgraph_path,
            is_cancelled=is_cancelled,
            tool_mocks=tool_mocks,
            shadow=shadow,
            tool_permissions=tool_permissions,
            jitter_rng=jitter_rng,
        )
        if node.id in skip_guards:
            parallel_id, safe_target = skip_guards[node.id]
            executor = _guard_any_success(
                executor, parallel_id, node.id, node.type, safe_target, emit
            )
        builder.add_node(node.id, executor)

    # parallel 区域推导：区域内节点指向 joinTarget 的边在编译期改指向汇聚网关，
    # 网关等待全部分支末端产出后聚合一次再放行进 joinTarget（04 §5.4）。
    retarget: dict[tuple[str, str], str] = {}
    for node in parallels:
        meta = metas[node.id]
        builder.add_node(
            meta["gate"], _make_join_gate(node, meta, emit, tracer=tracer, base_span=base_span)
        )
        for source in meta["region"]:
            if meta["join_target"] in outgoing.get(source, []):
                retarget[(source, meta["join_target"])] = meta["gate"]

    # D17/A2 break 网关：循环体内 condition 经分支直连 loop.exitTarget 的边，编译期
    # retarget 到合成 __break__ 节点（收口 exitReason='break'）后再放行进退出目标。
    type_by_id = {node.id: node.type for node in graph.nodes}
    outgoing_set: dict[str, set[str]] = {}
    for edge in graph.edges:
        outgoing_set.setdefault(edge.source, set()).add(edge.target)
    for lnode in [n for n in graph.nodes if n.type == "loop"]:
        body_target = lnode.config.get("bodyTarget")
        exit_target = lnode.config.get("exitTarget")
        if not (
            isinstance(body_target, str)
            and isinstance(exit_target, str)
            and body_target != lnode.id
            and exit_target != lnode.id
        ):
            continue
        body = _loop_body_set(body_target, lnode.id, exit_target, outgoing_set)
        sources = sorted(
            member
            for member in body
            if type_by_id.get(member) == "condition"
            and exit_target in outgoing_set.get(member, set())
        )
        if not sources:
            continue
        gate_id = f"{BREAK_GATE_PREFIX}{lnode.id}"
        builder.add_node(
            gate_id, _make_break_gate(lnode, emit, tracer=tracer, base_span=base_span)
        )
        builder.add_edge(gate_id, exit_target)
        for member in sources:
            retarget[(member, exit_target)] = gate_id

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

        # path map 键须为 route 实际返回值（retarget 后的网关 id），否则 langgraph 查无分支。
        destinations = {retarget.get((condition_id, t), t) for t in targets}
        builder.add_conditional_edges(
            condition_id, route, {dest: dest for dest in destinations}
        )

    for loop in graph.nodes:
        if loop.type != "loop":
            continue
        targets = outgoing.get(loop.id, [])

        def route_loop(state: GraphState, cid: str = loop.id) -> str:
            target = state["outputs"][cid]["target"]
            return retarget.get((cid, target), target)

        loop_destinations = {retarget.get((loop.id, t), t) for t in targets}
        builder.add_conditional_edges(
            loop.id, route_loop, {dest: dest for dest in loop_destinations}
        )

    for node in parallels:
        meta = metas[node.id]
        targets = [branch["target"] for branch in node.config.get("branches", [])]

        def route_parallel(state: GraphState, branch_targets: list[str] = targets) -> list[str]:
            return branch_targets

        builder.add_conditional_edges(
            node.id, route_parallel, {target: target for target in targets}
        )

        def route_gate(
            state: GraphState,
            m: dict[str, Any] = meta,
            strat: str = node.config.get("joinStrategy", "all_success"),
        ) -> str:
            # 已放行过汇聚目标：残留分支的迟到触发收口到 END，不重复执行 join 节点。
            if m["join_target"] in state["outputs"]:
                return "done"
            outcomes = _branch_outcomes(m, state["outputs"])
            if strat == "any_success":
                # 任一分支成功即放行；否则等所有分支终态（全失败时 gate 产出 failed 后放行）。
                ready = any(o["success"] for o in outcomes.values()) or all(
                    o["settled"] for o in outcomes.values()
                )
            else:
                ready = all(o["settled"] for o in outcomes.values())
            return m["join_target"] if ready else "wait"

        builder.add_conditional_edges(
            meta["gate"],
            route_gate,
            {
                meta["join_target"]: meta["join_target"],
                "wait": meta["gate"],
                "done": END,
            },
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
            # foreach 长度运行期才可知，按上限 100 预留（实际 ≤100）。
            iterations_cap = (
                MAX_LOOP_ITERATIONS
                if node.config.get("mode") == "foreach"
                else int(node.config.get("maxIterations", 10))
            )
            loop_steps += iterations_cap * (len(body) + 1)
    # parallel 汇聚网关在不等长分支下按超步空转等待，每个区域节点至多贡献两轮。
    parallel_wait = 0
    for node in graph.nodes:
        if node.type == "parallel":
            meta = _parallel_meta(node, outgoing)
            parallel_wait += 2 * len(meta["region"])
    # D17/A2：每个含 break 出口的循环额外一个 __break__ 网关节点超步。
    type_by_id = {n.id: n.type for n in graph.nodes}
    break_gates = 0
    for node in graph.nodes:
        if node.type != "loop":
            continue
        bt, et = node.config.get("bodyTarget"), node.config.get("exitTarget")
        if not (isinstance(bt, str) and isinstance(et, str)):
            continue
        body = _loop_body_set(bt, node.id, et, outgoing)
        if any(
            type_by_id.get(member) == "condition" and et in outgoing.get(member, set())
            for member in body
        ):
            break_gates += 1
    return 2 * len(graph.nodes) + 2 * loop_steps + parallel_wait + 2 * break_gates + 10


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
    condition_classifier: Any | None = None,
    registry: AdapterRegistry | None = None,
    approval_broker: ApprovalBroker | None = None,
    event_wait_broker: EventWaitBroker | None = None,
    approval_notifier: ApprovalNotifier | None = None,
    graph_id: str = "adhoc",
    emit: EventCallback | None = None,
    graph_resolver: Callable[[str], GraphDSL] | None = None,
    _subgraph_depth: int = 0,
    debug_controller: Any = None,
    frame_sink: Callable[[dict], None] | None = None,
    resume_claim: Callable[[str], bool] | None = None,
    resume: dict | None = None,
    _subgraph_path: tuple[str, ...] = (),
    tracer: Tracer | None | object = _AUTO_TRACER,
    graph_version: str | None = None,
    now_override: datetime | None = None,
    _parent_span: Span | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    tool_mocks: dict[str, Any] | None = None,
    shadow: bool = False,
    jitter_rng: random.Random | None = None,
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
            condition_classifier=condition_classifier,
            registry=registry,
            approval_broker=approval_broker,
            event_wait_broker=event_wait_broker,
            approval_notifier=approval_notifier,
            graph_id=resume_graph_id,
            emit=emit,
            trigger_payload=resume_inputs,
            graph_resolver=graph_resolver,
            _subgraph_depth=_subgraph_depth,
            debug_controller=debug_controller,
            frame_sink=frame_sink,
            resume_claim=resume_claim,
            resume=resume,
            _subgraph_path=_subgraph_path,
            validate_with=resume_graph,
            tracer=tracer,
            graph_version=graph_version,
            now=now_override,
            is_cancelled=is_cancelled,
            _parent_span=_parent_span,
            tool_mocks=tool_mocks,
            shadow=shadow,
            jitter_rng=jitter_rng,
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
        condition_classifier=condition_classifier,
        registry=registry,
        approval_broker=approval_broker,
        event_wait_broker=event_wait_broker,
        approval_notifier=approval_notifier,
        graph_id=graph_id,
        emit=emit,
        trigger_payload=inputs,
        graph_resolver=graph_resolver,
        _subgraph_depth=_subgraph_depth,
        debug_controller=debug_controller,
        frame_sink=frame_sink,
        resume_claim=resume_claim,
        _subgraph_path=_subgraph_path,
        tracer=tracer,
        graph_version=graph_version,
        now=now_override,
        is_cancelled=is_cancelled,
        _parent_span=_parent_span,
        tool_mocks=tool_mocks,
        shadow=shadow,
        jitter_rng=jitter_rng,
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
