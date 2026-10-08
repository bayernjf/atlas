# -*- coding: utf-8 -*-
"""打包 BM 缺陷批（docs/101 §2 D57）：refund-auto 模板真转人工（U1213–U1217）。

U1213 模板新形状：condition-1 分流表达式＋human_approval-1 双出口（契约形状钉住）。
U1214 转人工路径**真挂起**：超限/主观原因 → 运行挂起在 human_approval-1（approval 帧），
     resolve 前 run 不自动结束（对比 D57 修复前：shop 标 human_review 就 completed、无人被叫到）。
U1215 审批通过 → execute_refund 落库（订单 refunded）。
U1216 审批拒绝 → reject_refund 落定（订单 rejected）。
U1217 自动退款路径不变：质量原因且金额 ≤ 限额 → 直接 tool_call-1 退款、无挂起、订单 refunded。
"""

from __future__ import annotations

import threading
import time as time_mod

from atlas.graph.dsl import parse_graph
from atlas.graph.loader import build_demo_registry, run_graph
from atlas.iam.deps import tenant_registry
from atlas.llm.decision import RuleBasedDecisionClient
from atlas.shop.service import DemoShopService
from atlas.template.graphs import refund_template_graph


def _registry_with_real_shop(service: DemoShopService):
    registry = build_demo_registry()
    registry.unregister("shop")
    from atlas.harness.base import Permission
    from atlas.shop.adapter import ShopHarnessAdapter

    registry.register(
        ShopHarnessAdapter(
            service=service,
            granted_permissions={
                Permission.READ,
                Permission.WRITE,
                Permission.FINANCIAL,
            },
        )
    )
    return registry


def _wait_frames(holder: list, timeout: float = 3.0) -> dict:
    deadline = time_mod.monotonic() + timeout
    while not holder and time_mod.monotonic() < deadline:
        time_mod.sleep(0.005)
    assert holder, "应在超时前产生挂起帧"
    return holder[0]


# ---------------------------------------------------------------- U1213 形状


def test_refund_template_has_human_approval_fork():
    graph = refund_template_graph()
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert {"condition-1", "human_approval-1", "tool_call-approve", "tool_call-reject"} <= set(
        nodes
    )
    cond = nodes["condition-1"]["config"]
    assert cond["defaultTarget"] == "tool_call-1"
    assert cond["branches"][0]["target"] == "human_approval-1"
    assert "{{ai_decision-1.decision.action}}" in cond["branches"][0]["expression"]
    assert "request_human_approval" in cond["branches"][0]["expression"]
    human = nodes["human_approval-1"]["config"]
    assert human["approvedTarget"] == "tool_call-approve"
    assert human["rejectedTarget"] == "tool_call-reject"
    assert human["timeoutSeconds"] == 3600
    assert human["onTimeout"] == "reject"
    # 模板整体必须过 DSL 全量校验（test_templates U27 同款）。
    parse_graph(graph)


# ------------------------------------------------ U1214 真挂起（修复前会立即完成）


def test_high_confidence_human_approval_now_really_suspends():
    """D57 核心：高置信 request_human_approval 不再「只说了句要找」。

    规则决策器对非质量原因恒给 confidence=1.0 的 request_human_approval——置信度闸门
    （confidence < 0.6）不触发，修复前直接落到 shop 标 human_review 就 completed。
    新模板经 condition 分流到 human_approval-1 真挂起：resolve 前线程不结束。
    """
    service = DemoShopService()
    frames: list[dict] = []
    broker = tenant_registry.get("t1").approval_broker

    def run_original() -> None:
        run_graph(
            parse_graph(refund_template_graph()),
            inputs={
                "order_id": "12345",
                "reason": "不想要了",
                "amount": 5000,
            },
            decision_client=RuleBasedDecisionClient(),
            registry=_registry_with_real_shop(service),
            approval_broker=broker,
            frame_sink=frames.append,
        )

    thread = threading.Thread(target=run_original)
    thread.start()
    frame = _wait_frames(frames)
    assert frame["kind"] == "approval"
    assert frame["node_id"] == "human_approval-1"
    assert "12345" in frame["summary"]
    # 挂起中：未 resolve 时 run 线程不应结束（修复前这里直接 completed）。
    time_mod.sleep(0.1)
    assert thread.is_alive(), "转人工路径应挂起等待审批，而不是立即完成"
    broker.resolve(frame["resume_token"], "approved", comment="测试放行")
    thread.join(timeout=3)
    assert not thread.is_alive(), "run 线程应在 resolve 后结束"


# ------------------------------------------------ U1215 审批通过 → 退款落库


def test_approved_human_approval_runs_refund_on_shop():
    service = DemoShopService()
    frames: list[dict] = []
    broker = tenant_registry.get("t1").approval_broker
    result_box: list[dict] = []

    def run_original() -> None:
        result_box.append(
            run_graph(
                parse_graph(refund_template_graph()),
                inputs={
                    "order_id": "12345",
                    "reason": "不想要了",
                    "amount": 5000,
                },
                decision_client=RuleBasedDecisionClient(),
                registry=_registry_with_real_shop(service),
                approval_broker=broker,
                frame_sink=frames.append,
            )
        )

    thread = threading.Thread(target=run_original)
    thread.start()
    frame = _wait_frames(frames)
    broker.resolve(frame["resume_token"], "approved", comment="审批通过")
    thread.join(timeout=3)
    assert not thread.is_alive()
    result = result_box[0]
    assert result["status"] == "completed"
    assert result["outputs"]["human_approval-1"]["decision"] == "approved"
    assert result["outputs"]["human_approval-1"]["target"] == "tool_call-approve"
    assert result["outputs"]["tool_call-approve"]["action_status"] == "SUCCESS"
    assert service.orders["12345"].status == "refunded"


# ------------------------------------------------ U1216 审批拒绝 → 拒绝落定


def test_rejected_human_approval_marks_order_rejected():
    service = DemoShopService()
    frames: list[dict] = []
    broker = tenant_registry.get("t1").approval_broker
    result_box: list[dict] = []

    def run_original() -> None:
        result_box.append(
            run_graph(
                parse_graph(refund_template_graph()),
                inputs={
                    "order_id": "12345",
                    "reason": "不想要了",
                    "amount": 5000,
                },
                decision_client=RuleBasedDecisionClient(),
                registry=_registry_with_real_shop(service),
                approval_broker=broker,
                frame_sink=frames.append,
            )
        )

    thread = threading.Thread(target=run_original)
    thread.start()
    frame = _wait_frames(frames)
    broker.resolve(frame["resume_token"], "rejected", comment="拒绝退款")
    thread.join(timeout=3)
    assert not thread.is_alive()
    result = result_box[0]
    assert result["status"] == "completed"
    assert result["outputs"]["human_approval-1"]["decision"] == "rejected"
    assert result["outputs"]["human_approval-1"]["target"] == "tool_call-reject"
    assert result["outputs"]["tool_call-reject"]["action_status"] == "SUCCESS"
    assert service.orders["12345"].status == "rejected"


# ------------------------------------------------ U1217 自动退款路径不变


def test_auto_refund_path_unchanged_no_suspension():
    """质量原因且金额 ≤ 限额 → approve_refund（置信度闸门与 condition 分流都不触发）。"""
    service = DemoShopService()
    events: list[dict] = []
    result = run_graph(
        parse_graph(refund_template_graph()),
        inputs={
            "order_id": "12345",
            "reason": "商品破损",
            "amount": 299,
        },
        decision_client=RuleBasedDecisionClient(),
        registry=_registry_with_real_shop(service),
        emit=events.append,
    )
    assert result["status"] == "completed"
    start_nodes = [event["node_id"] for event in events if event["type"] == "node_start"]
    assert start_nodes == [
        "trigger-1",
        "ai_decision-1",
        "condition-1",
        "tool_call-1",
    ]
    assert "human_approval-1" not in [event["node_id"] for event in events if event["type"] == "node_start"]
    assert result["outputs"]["tool_call-1"]["action_status"] == "SUCCESS"
    assert service.orders["12345"].status == "refunded"
