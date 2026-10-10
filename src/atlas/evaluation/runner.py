"""打包 AC（docs/110）：离线批评估 runner。

对指定 graph_id 批量跑 `run_graph`（全链路，含 subgraph 解析），从帧流收集
语义动作（ai_decision 决策结论 / human_approval 挂起），verify 复用条件引擎
白名单表达式对终态 outputs 断言，汇总 metrics 三元组。

- 决策客户端：缺省 `get_decision_client(None)`（未配 LITELLM_MODEL 时确定性
  RuleBasedDecisionClient，评估可复现）；可显式注入。
- 审批：每次 case 注入真实内存 ApprovalBroker；case.inputs 可带 approvals
  预置放行（`{approvals: {node_id: "approved"|"rejected"}}`，loader 现成特性）。
- 失败语义：case 级运行异常记 error 不使整批失败；verify 非法同样 case 级。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from atlas.collaboration.approvals import ApprovalBroker
from atlas.graph.conditions import evaluate_expression
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
from atlas.llm.decision import get_decision_client
from atlas.recording.shadow import preset_all_approvals, preset_all_wait_events

from .models import (
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationSummary,
    EvaluationTask,
)

REQUEST_HUMAN_APPROVAL = "request_human_approval"


class EvaluationGraphNotFound(Exception):
    """评估目标图不存在（端点 → 404 EVALUATION_GRAPH_NOT_FOUND）。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return f"ev-{uuid.uuid4().hex[:12]}"


def _summarize(
    task: EvaluationTask, cases: list[EvaluationCaseResult]
) -> EvaluationSummary:
    n = len(cases)
    if n == 0:
        return EvaluationSummary(task_success_rate=0.0, average_steps=0.0)
    success = sum(1 for c in cases if c.passed)
    decision_cases = [c for c in cases if c.decision_matched is not None]
    return EvaluationSummary(
        task_success_rate=success / n,
        average_steps=sum(c.steps for c in cases) / n,
        decision_accuracy=(
            sum(1 for c in decision_cases if c.decision_matched) / len(decision_cases)
            if decision_cases
            else None
        ),
    )


def run_task(
    graph_store: Any,
    graph_id: str,
    task: EvaluationTask,
    *,
    decision_client: Any | None = None,
) -> EvaluationRun:
    """对 graph_id 批量执行 task.test_cases，返回一次 EvaluationRun。"""
    raw = graph_store.get(graph_id)
    if raw is None:
        raise EvaluationGraphNotFound(graph_id)
    graph = parse_graph(raw)
    # 打包 AJ（docs/121 §5）：评估语义是「遍历全图比对决策与产出」——
    # tool_call 节点降级为 continue（工具失败如「订单不存在」不应截断后续比对），
    # 决策类节点保持原样：异常（如 amount 非数字）仍穿透为 case 级 error。
    # 仅作用于本次评估执行的内存副本，不改写 graph_store 里的图。
    for node in graph.nodes:
        if node.type == "tool_call":
            node.retry.on_error = "continue"
    resolver = None
    if hasattr(graph_store, "get"):
        # subgraph 引用解析（同 API run 路径：graph_resolver 走 graph_store.get）
        resolver = graph_store.get

    decision = decision_client if decision_client is not None else get_decision_client(None)
    cases: list[EvaluationCaseResult] = []

    for idx, case in enumerate(task.test_cases):
        name = case.name or f"case-{idx + 1}"
        actions: list[str] = []
        steps = 0

        def emit(ev: dict[str, Any]) -> None:
            nonlocal steps
            ev_type = ev.get("type")
            if ev_type == "node_start":
                steps += 1
                if "approval" in ev:
                    actions.append(REQUEST_HUMAN_APPROVAL)
            elif ev_type == "node_end" and ev.get("node_type") == "ai_decision":
                output = ev.get("output")
                if isinstance(output, dict):
                    # ai_decision 输出形状：{"decision": {action, reason, confidence, source}, ...}
                    decision = output.get("decision")
                    if isinstance(decision, dict):
                        action = decision.get("action")
                        if isinstance(action, str):
                            actions.append(action)

        verify_ok: bool | None = None
        verify_error: str | None = None
        # 离线确定性：默认预置全部审批 approved 秒过、event wait 空 payload
        # （影子惯例 docs/33 §3.1 + 打包 ZJ，loader 零改动）；case.inputs 自带的
        # approvals/waitEvents 以 case 为准覆盖。approval 帧照发，语义动作收集不受影响。
        case_inputs = dict(case.inputs or {})
        case_inputs.setdefault(
            "approvals",
            preset_all_approvals(graph, resolver=resolver),
        )
        case_inputs.setdefault(
            "waitEvents",
            preset_all_wait_events(graph, resolver=resolver),
        )
        try:
            result = run_graph(
                graph,
                inputs=case_inputs,
                decision_client=decision,
                approval_broker=ApprovalBroker(),
                emit=emit,
                graph_id=graph_id,
                graph_resolver=resolver,
            )
        except Exception as exc:  # case 级运行异常：不使整批失败
            cases.append(
                EvaluationCaseResult(
                    name=name,
                    passed=False,
                    decision_matched=False,
                    steps=steps,
                    error=str(exc),
                )
            )
            continue

        expected = case.expected
        if expected is not None and expected.verify:
            try:
                # verify 上下文：outputs（node_id → output）＋ inputs（全局初值）；
                # 表达式用 {{outputs.<node_id>.<field>}} / {{<input 键>}} 路径（条件引擎惯例）。
                context = {"outputs": result.get("outputs", {}), **(case.inputs or {})}
                verify_ok = bool(evaluate_expression(expected.verify, context))
            except Exception as exc:
                verify_ok = False
                verify_error = str(exc)

        matched: bool | None = None
        if expected is not None and expected.action:
            matched = any(a == expected.action for a in actions)

        cases.append(
            EvaluationCaseResult(
                name=name,
                passed=True if verify_ok is None else verify_ok,
                decision_matched=matched,
                steps=steps,
                error=verify_error,
            )
        )

    return EvaluationRun(
        id=_run_id(),
        task_id=task.task_id,
        graph_id=graph_id,
        summary=_summarize(task, cases),
        cases=cases,
        created_at=_utc_now(),
    )
