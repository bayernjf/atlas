# -*- coding: utf-8 -*-
"""M8 批 2（U54）：human_approval cardTemplateId 接线 + 卡片 REST 端点。

覆盖（docs/13 U54）：
- 内置卡片目录 GET /api/cards；
- cardTemplateId 编译校验（未知卡 422、卡片 bindings 进 L2 作用域复查）；
- 挂起审批三渠道渲染（web/im/email）、GET 无决策副作用、非法 channel 422；
- 卡片动作决策 {actionId, form} 映射 decision/comment，旧 {decision, comment} 不回归；
- 节点产出超集 comment/card、node_start approval 载荷带 cardTemplateId；
- 中断帧重建卡片上下文并 restore（进程内 broker；PG 端到端属 integration）。
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.cards import get_card, map_action_output, render_card
from atlas.collaboration.approvals import ApprovalBroker
from atlas.graph.dsl import GraphValidationError, parse_graph, validate_graph
from atlas.graph.loader import build_demo_registry, run_graph
from atlas.llm.decision import RuleBasedDecisionClient
from atlas.storage.frame import card_context_from_frame

client = TestClient(app)

CARD_ID = "refund-approval"


@pytest.fixture(autouse=True)
def _admin_session():
    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    yield
    client.post("/api/demo/reset")
    client.headers.pop("authorization", None)


def _trigger_node():
    return {
        "id": "trigger-1",
        "type": "trigger",
        "name": "新退款申请",
        "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"},
    }


def _card_graph(card_id: str | None = CARD_ID) -> dict:
    """trigger → ai_decision → human_approval（挂卡）→ 两侧 tool；卡片 bindings 均可解析。"""
    human_config = {
        "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
        "approver": "客服主管",
        "timeoutSeconds": 30,
        "onTimeout": "reject",
        "approvedTarget": "tool-approve",
        "rejectedTarget": "tool-reject",
    }
    if card_id is not None:
        human_config["cardTemplateId"] = card_id
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            _trigger_node(),
            {
                "id": "ai_decision-1",
                "type": "ai_decision",
                "name": "退款决策",
                "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}}"},
            },
            {"id": "human-1", "type": "human_approval", "name": "人工审批", "config": human_config},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "human-1"},
            {"id": "e3", "source": "human-1", "target": "tool-approve"},
            {"id": "e4", "source": "human-1", "target": "tool-reject"},
        ],
    }


def _plain_graph() -> dict:
    """无 ai_decision、无卡的旧 summary 审批图（trigger → human → 两侧 tool）。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            _trigger_node(),
            {
                "id": "human-1",
                "type": "human_approval",
                "name": "人工审批",
                "config": {
                    "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
                    "approver": "客服主管",
                    "timeoutSeconds": 30,
                    "onTimeout": "reject",
                    "approvedTarget": "tool-approve",
                    "rejectedTarget": "tool-reject",
                },
            },
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human-1"},
            {"id": "e2", "source": "human-1", "target": "tool-approve"},
            {"id": "e3", "source": "human-1", "target": "tool-reject"},
        ],
    }


def _start_suspended_run(graph: dict, inputs: dict):
    """后台触发 run，轮询到审批挂起，返回 (thread, run_id, token)。"""
    graph_id = client.post("/api/graphs", json=graph).json()["id"]
    box: dict = {}

    def _bg() -> None:
        box["resp"] = client.post(f"/api/graphs/{graph_id}/run", json={"inputs": inputs})

    thread = threading.Thread(target=_bg)
    thread.start()
    run_id = token = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        items = client.get("/api/runs", params={"status": "suspended"}).json()["items"]
        if items:
            run_id = items[0]["runId"]
            token = items[0]["resumeToken"]
            break
        time.sleep(0.02)
    assert run_id and token
    return thread, run_id, token


def _wait_completed(thread, run_id: str) -> dict:
    thread.join(timeout=10)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        detail = client.get(f"/api/runs/{run_id}").json()
        if detail.get("status") == "completed":
            return detail
        time.sleep(0.02)
    raise AssertionError("run did not complete in time")


# --- 目录与编译期校验 -------------------------------------------------------

def test_cards_catalog_listed():
    resp = client.get("/api/cards")
    assert resp.status_code == 200
    items = resp.json()["items"]
    ids = [item["id"] for item in items]
    assert CARD_ID in ids
    card = next(item for item in items if item["id"] == CARD_ID)
    assert card["name"] == "退款审批卡片"
    assert set(card["channels"]) == {"web", "im", "email"}
    assert {action["id"] for action in card["actions"]} == {"approve", "reject"}


def test_unknown_card_template_rejected_at_compile():
    bad = _card_graph("no-such-card")
    with pytest.raises(GraphValidationError) as exc:
        parse_graph(bad)
    joined = "; ".join(exc.value.errors)
    assert "人机协作节点 human-1" in joined
    assert "交互卡片不存在" in joined and "no-such-card" in joined
    # API 保存图同样 422（全局 GraphValidationError handler）
    assert client.post("/api/graphs", json=bad).status_code == 422


def test_card_bindings_go_through_l2_scope_check():
    # 挂卡但上游缺 ai_decision-1：卡片 binding 引用不可见节点，编译期 L2 复查拦截
    raw = _plain_graph()
    raw["nodes"][1]["config"]["cardTemplateId"] = CARD_ID
    graph = parse_graph(raw)
    issues = validate_graph(graph, check_refs=True)
    assert any("ai_decision-1" in message for message in issues)


def test_valid_card_graph_compiles_and_decision_reason_path_allowed():
    # ai_decision.decision.reason 白名单放行后，挂卡图（含 AI 建议 binding）编译期 L2 无 refs 错误
    graph = parse_graph(_card_graph())
    assert validate_graph(graph, check_refs=True) == []


# --- 三渠道渲染与只读语义 ---------------------------------------------------

def test_card_rendered_for_three_channels_without_side_effects():
    _, _, token = _start_suspended_run(
        _card_graph(), {"order_id": "C1001", "amount": 5000, "reason": "商品破损"}
    )

    # 列表项携带 cardTemplateId
    pending = client.get("/api/approvals").json()["items"]
    mine = next(item for item in pending if item["token"] == token)
    assert mine["cardTemplateId"] == CARD_ID

    web = client.get(f"/api/approvals/{token}/card")
    assert web.status_code == 200
    web_body = web.json()
    assert web_body["channel"] == "web" and web_body["cardId"] == CARD_ID
    fields = {row["label"]: row["value"] for row in web_body["fields"]}
    assert fields["订单号"] == "C1001"
    assert "5000" in f"{fields['退款金额']}"
    assert "商品破损" in f"{fields['退款原因']}"
    assert "{{" not in f"{fields['AI 建议']}"  # AI 建议理由已插值
    assert "500" in f"{fields['审批限额']}"
    form_names = [item["name"] for item in web_body["form"]]
    assert form_names == ["comment"]
    actions = {item["id"]: item for item in web_body["actions"]}
    assert actions["approve"]["style"] == "primary"
    assert actions["reject"]["style"] == "danger"
    assert web_body["token"] == token and web_body["approver"] == "客服主管"
    assert web_body["timeoutSeconds"] == 30

    im = client.get(f"/api/approvals/{token}/card", params={"channel": "im"}).json()
    assert im["channel"] == "im" and token in im["detailUrl"]
    assert {button["id"] for button in im["buttons"]} == {"approve", "reject"}
    assert all(token in button["url"] and "actionId=" in button["url"] for button in im["buttons"])

    email = client.get(f"/api/approvals/{token}/card", params={"channel": "email"}).json()
    assert email["channel"] == "email"
    assert "退款审批卡片" in email["subject"]
    assert "C1001" in email["html"]
    assert {link["id"] for link in email["links"]} == {"approve", "reject"}

    # GET 渲染是只读的：审批仍挂起、无决策
    still = client.get("/api/approvals").json()["items"]
    assert any(item["token"] == token for item in still)

    # 非法 channel 由 FastAPI 判 422
    assert client.get(f"/api/approvals/{token}/card", params={"channel": "fax"}).status_code == 422
    # 未知 token 404；无卡审批 404 在另例覆盖
    assert client.get("/api/approvals/does-not-exist/card").status_code == 404

    # 收尾：同意，避免挂起线程残留
    client.post(f"/api/approvals/{token}/decision", json={"actionId": "approve"})


def test_render_returns_404_when_approval_has_no_card():
    _, _, token = _start_suspended_run(
        _plain_graph(), {"order_id": "C2001", "amount": 100}
    )
    pending = client.get("/api/approvals").json()["items"]
    mine = next(item for item in pending if item["token"] == token)
    assert "cardTemplateId" not in mine
    resp = client.get(f"/api/approvals/{token}/card")
    assert resp.status_code == 404
    client.post(f"/api/approvals/{token}/decision", json={"decision": "approved"})


# --- 卡片动作决策 -----------------------------------------------------------

def test_approve_action_maps_decision_and_emits_card_output():
    thread, run_id, token = _start_suspended_run(
        _card_graph(), {"order_id": "C3001", "amount": 5000, "reason": "大额订单"}
    )
    resp = client.post(f"/api/approvals/{token}/decision", json={"actionId": "approve"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "approved" and body["actionId"] == "approve"

    detail = _wait_completed(thread, run_id)
    node_output = detail["outputs"]["human-1"]
    assert node_output["decision"] == "approved"
    assert node_output["comment"] == ""  # 未填表单 → comment 回填空串
    assert node_output["card"] == {"templateId": CARD_ID, "actionId": "approve"}


def test_reject_action_maps_form_comment():
    thread, run_id, token = _start_suspended_run(
        _card_graph(), {"order_id": "C3002", "amount": 5000, "reason": "大额订单"}
    )
    resp = client.post(
        f"/api/approvals/{token}/decision",
        json={"actionId": "reject", "form": {"comment": "证据不足"}},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "rejected"

    detail = _wait_completed(thread, run_id)
    node_output = detail["outputs"]["human-1"]
    assert node_output["decision"] == "rejected"
    assert node_output["comment"] == "证据不足"
    assert node_output["card"]["actionId"] == "reject"


def test_bad_action_and_missing_payload_422_then_legacy_decision_still_works():
    thread, run_id, token = _start_suspended_run(
        _card_graph(), {"order_id": "C3003", "amount": 5000, "reason": "大额订单"}
    )
    # 未知动作 422
    bad = client.post(f"/api/approvals/{token}/decision", json={"actionId": "nope"})
    assert bad.status_code == 422
    # decision 与 actionId 都缺 422
    assert client.post(f"/api/approvals/{token}/decision", json={}).status_code == 422
    # 仍挂起（未被坏请求首决）
    assert any(item["token"] == token for item in client.get("/api/approvals").json()["items"])
    # 卡审批也兼容旧 {decision} 路径
    ok = client.post(f"/api/approvals/{token}/decision", json={"decision": "approved"})
    assert ok.status_code == 200 and "actionId" not in ok.json()

    detail = _wait_completed(thread, run_id)
    node_output = detail["outputs"]["human-1"]
    assert node_output["decision"] == "approved"
    assert node_output["card"] == {"templateId": CARD_ID, "actionId": None}


def test_legacy_decision_path_unchanged_without_card():
    thread, run_id, token = _start_suspended_run(
        _plain_graph(), {"order_id": "C4001", "amount": 100}
    )
    resp = client.post(
        f"/api/approvals/{token}/decision",
        json={"decision": "rejected", "comment": "旧路径备注"},
    )
    assert resp.status_code == 200 and resp.json()["decision"] == "rejected"
    detail = _wait_completed(thread, run_id)
    node_output = detail["outputs"]["human-1"]
    assert node_output["decision"] == "rejected"
    assert node_output["comment"] == "旧路径备注"
    assert "card" not in node_output  # 未挂卡不产出 card


# --- 运行时载荷（单元，不经 HTTP）-------------------------------------------

def test_run_graph_node_start_carries_card_template_and_output():
    graph = parse_graph(_card_graph())
    broker = ApprovalBroker()
    events: list = []
    box: dict = {}

    def _bg() -> None:
        box["result"] = run_graph(
            graph,
            inputs={"order_id": "U5001", "amount": 5000, "reason": "大额"},
            approval_broker=broker,
            registry=build_demo_registry(),
            decision_client=RuleBasedDecisionClient(),
            emit=events.append,
        )

    thread = threading.Thread(target=_bg)
    thread.start()
    token = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pending = broker.list_pending()
        if pending:
            token = pending[0]["token"]
            break
        time.sleep(0.02)
    assert token and broker.list_pending()[0]["cardTemplateId"] == CARD_ID

    approval_events = [
        event.get("approval")
        for event in events
        if event.get("type") == "node_start" and event.get("approval")
    ]
    assert approval_events and approval_events[0]["cardTemplateId"] == CARD_ID

    mapped = map_action_output(get_card(CARD_ID), "approve", None)
    assert broker.resolve(
        token, mapped["decision"], comment=mapped.get("comment", ""), action_id="approve"
    )
    thread.join(timeout=10)

    node_output = box["result"]["outputs"]["human-1"]
    assert node_output["decision"] == "approved"
    assert node_output["card"] == {"templateId": CARD_ID, "actionId": "approve"}
    assert node_output["comment"] == ""


def test_card_context_rebuilt_from_frame_and_restored_for_render():
    frame = {
        "graph_snapshot": {
            "variables": [{"name": "approval_limit", "value": 500}],
        },
        "resume_state": {
            "inputs": {"order_id": "O1", "amount": 9, "reason": "r"},
            "outputs": {
                "trigger-1": {
                    "context": {"payload": {"order_id": "O1", "amount": 9, "reason": "r"}}
                },
                "ai_decision-1": {
                    "decision": {"action": "request_human_approval", "reason": "AI-R"}
                },
            },
        },
    }
    context = card_context_from_frame(frame)
    assert context["global"]["approval_limit"] == 500
    assert context["trigger-1"]["context"]["payload"]["order_id"] == "O1"
    assert context["ai_decision-1"]["decision"]["reason"] == "AI-R"

    broker = ApprovalBroker()
    broker.restore(
        token="t",
        node_id="human-1",
        graph_id="g",
        summary="s",
        approver="a",
        remaining_seconds=30,
        card_template_id=CARD_ID,
        card_context=context,
    )
    assert broker.get_card_context("t") is not None
    web = render_card(
        get_card(CARD_ID), broker.get_card_context("t"), token="t", channel="web"
    )
    values = [f"{row['value']}" for row in web["fields"]]
    assert any("O1" in value for value in values)
    assert any("AI-R" in value for value in values)
    assert broker.get("t")["cardTemplateId"] == CARD_ID
    # 无卡审批取不到上下文
    broker.request(
        node_id="n2", graph_id="g", summary="s", approver="a", timeout_seconds=10
    )
    plain_token = broker.list_pending()[-1]["token"]
    assert broker.get_card_context(plain_token) is None
