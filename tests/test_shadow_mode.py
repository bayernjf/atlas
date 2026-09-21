# -*- coding: utf-8 -*-
"""D26 影子模式（线上旁路录制，docs/33 §3；U250–U269）。

覆盖两层：
- 引擎（``run_graph(shadow=True)``）：READ 透传、WRITE/DELETE/FINANCIAL 短路为 SHADOW_DRY_RUN、
  子图写能力同样短路、预置审批不挂起、非法 params 不伪造 dry-run、零 tool_metric；
- 纯函数/存储（``recording/shadow.py``）：意图提取、auto_action 推断、对比三态、ring/reset；
- REST（``/api/.../shadow-runs``）：发起/列表/详情/补录对比、零生产观测污染、权限矩阵、404/422。

PG 档（U269）仅验证进程内 ShadowStore 在 PG TenantServices 下同形（影子存储不 PG 化）。
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import build_demo_registry, run_graph
from atlas.harness.base import Permission
from atlas.iam.deps import tenant_registry
from atlas.llm.decision import RuleBasedDecisionClient
from atlas.recording.shadow import (
    HumanOutcome,
    ShadowStore,
    ToolIntent,
    compare_shadow,
    extract_shadow_events,
    infer_auto_action,
    normalize_human_action,
    preset_all_approvals,
)
from atlas.shop.adapter import ShopHarnessAdapter
from atlas.shop.service import DemoShopService

client = TestClient(app)


# --- 图/装配样板 -------------------------------------------------------------------


def _refund_graph_dict() -> dict:
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "新退款申请",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "退款决策",
             "config": {"promptTemplate": "退款 {{trigger-1.context.payload.reason}} 金额 {{trigger-1.context.payload.amount}}"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "执行处理",
             "config": {"tool": "shop/process_refund"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e2", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def _registry_with_service(service: DemoShopService):
    """注册绑定同一可观察店铺服务的全权限 shop 适配器（其余 demo 适配器照旧）。"""
    registry = build_demo_registry()
    registry.unregister("shop")
    registry.register(
        ShopHarnessAdapter(
            service=service,
            granted_permissions={Permission.READ, Permission.WRITE, Permission.FINANCIAL},
        )
    )
    return registry


def _single_tool_graph(tool: str, params: str | None = None, trigger_type: str = "webhook"):
    config = {"tool": tool}
    if params is not None:
        config["params"] = params
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": trigger_type, "webhookUrl": "/hooks/x"}},
                {"id": "tool-1", "type": "tool_call", "name": "工具", "config": config},
            ],
            "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        }
    )


# ---------- U250 自动退款：影子短路 + 店铺零变化 + 无 tool_metric ----------


def test_shadow_auto_refund_short_circuits_without_side_effect():
    service = DemoShopService()
    registry = _registry_with_service(service)
    events: list[dict] = []

    shadow = run_graph(
        parse_graph(_refund_graph_dict()),
        inputs={"order_id": "12345", "reason": "商品破损", "amount": 299},
        decision_client=RuleBasedDecisionClient(),
        registry=registry,
        emit=events.append,
        shadow=True,
    )
    assert shadow["status"] == "completed"
    intent_output = shadow["outputs"]["tool_call-1"]
    assert intent_output["action_status"] == "SHADOW_DRY_RUN"
    result = intent_output["result"]
    assert result["status"] == "SHADOW_DRY_RUN"
    assert result["tool"] == "shop/process_refund" and result["permission"] == "financial"
    assert result["parameters"]["action"] == "approve_refund"
    assert result["parameters"]["order_id"] == "12345"
    # 零副作用：店铺仍是待处理，未真退款
    assert service.orders["12345"].status == "pending"
    # 零监控污染：影子运行不发 tool_metric
    assert not any(event["type"] == "tool_metric" for event in events)

    # 对照：同一输入正常运行 → 真实退款 + tool_metric
    service2 = DemoShopService()
    normal = run_graph(
        parse_graph(_refund_graph_dict()),
        inputs={"order_id": "12345", "reason": "商品破损", "amount": 299},
        decision_client=RuleBasedDecisionClient(),
        registry=_registry_with_service(service2),
        emit=events.append,
    )
    assert normal["outputs"]["tool_call-1"]["result"]["status"] == "refunded"
    assert service2.orders["12345"].status == "refunded"
    assert any(event["type"] == "tool_metric" for event in events)


# ---------- U251 转人工：影子记录 human_review 意图、店铺零变化 ----------


def test_shadow_human_review_intent_keeps_shop_unchanged():
    service = DemoShopService()
    result = run_graph(
        parse_graph(_refund_graph_dict()),
        inputs={"order_id": "12346", "reason": "不想要了", "amount": 5000},
        decision_client=RuleBasedDecisionClient(),
        registry=_registry_with_service(service),
        shadow=True,
    )
    assert result["outputs"]["ai_decision-1"]["decision"]["action"] == "request_human_approval"
    output = result["outputs"]["tool_call-1"]
    assert output["action_status"] == "SHADOW_DRY_RUN"
    assert output["result"]["parameters"]["action"] == "request_human_approval"
    assert service.orders["12346"].status == "pending"  # 未真转人工


# ---------- U252 READ 能力透传真实执行 ----------


def test_shadow_read_capability_passes_through():
    service = DemoShopService()
    result = run_graph(
        _single_tool_graph("shop/list_pending_refunds"),
        inputs={"order_id": "12345"},
        registry=_registry_with_service(service),
        shadow=True,
    )
    output = result["outputs"]["tool-1"]
    assert output["action_status"] == "SUCCESS"  # read 透传真实执行
    assert "orders" in output["result"]  # list_pending_refunds 成功体为 {"orders": [...]}，无短路回执
    assert any(order["order_id"] == "12345" for order in output["result"]["orders"])


# ---------- U253 generic JSON 写通道短路（http/database/message） ----------


@pytest.mark.parametrize(
    "tool,params",
    [
        ("http/request", '{"url": "https://example.invalid/hook", "method": "POST", "body": {"x": 1}}'),
        ("database/execute", '{"sql": "DELETE FROM orders"}'),
        ("message/send", '{"to": "ops", "body": "已退款"}'),
    ],
)
def test_shadow_generic_write_channels_short_circuit(tool, params):
    registry = build_demo_registry()
    result = run_graph(
        _single_tool_graph(tool, params=params),
        inputs={},
        registry=registry,
        shadow=True,
    )
    output = result["outputs"]["tool-1"]
    assert output["action_status"] == "SHADOW_DRY_RUN"
    assert output["result"]["status"] == "SHADOW_DRY_RUN"
    assert output["result"]["tool"] == tool
    assert output["result"]["permission"] != "read"


# ---------- U254 generic READ（database/query）透传 + 非法 JSON 不伪造 dry-run ----------


def test_shadow_generic_read_passthrough_and_invalid_json_still_fails():
    registry = build_demo_registry()
    read_result = run_graph(
        _single_tool_graph("database/query", params='{"sql": "SELECT 1"}'),
        inputs={},
        registry=registry,
        shadow=True,
    )
    read_output = read_result["outputs"]["tool-1"]
    # read 能力不短路：要么真实 SUCCESS，要么适配器层 FAILED，但绝不是 SHADOW_DRY_RUN
    assert read_output["action_status"] != "SHADOW_DRY_RUN"

    bad = run_graph(
        _single_tool_graph("http/request", params='{"url": "oops", '),
        inputs={},
        registry=build_demo_registry(),
        shadow=True,
    )
    bad_output = bad["outputs"]["tool-1"]
    assert bad_output["action_status"] == "FAILED"
    assert bad_output["result"]["code"] == "INVALID_PARAMETER"  # 非法 JSON 仍报错，不伪造 dry-run


# ---------- U255 预置审批纯函数（API 层自动预置见 U267） ----------


def test_preset_all_approvals_covers_human_nodes():
    from atlas.template import get_template

    graph = parse_graph(get_template("approval-timeout-reject").graph)
    presets = preset_all_approvals(graph)
    assert presets  # 非空：模板含 human_approval 节点
    assert all(decision == "approved" for decision in presets.values())
    human_nodes = {node.id for node in graph.nodes if node.type == "human_approval"}
    assert set(presets) == human_nodes


# ---------- U256 子图内写能力同样短路（运行级语义透传） ----------


def test_shadow_propagates_into_subgraph_write_capability():
    service = DemoShopService()
    registry = _registry_with_service(service)

    child = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/c"}},
                {"id": "c-tool", "type": "tool_call", "name": "子图退款",
                 "config": {"tool": "shop/execute_refund"}},
            ],
            "edges": [{"id": "ce1", "source": "c-trigger", "target": "c-tool"}],
        }
    )
    parent = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/p"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": "g-child",
                            "inputs": {"order_id": "{{p-trigger.context.payload.order_id}}"}}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-1"},
                {"id": "pe2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )
    events: list[dict] = []
    shadow = run_graph(
        parent,
        graph_id="g-parent",
        graph_resolver={"g-child": child}.get,
        inputs={"order_id": "12345"},
        registry=registry,
        emit=events.append,
        shadow=True,
    )
    assert shadow["status"] == "completed"
    # 子层 c-tool 事件带 subgraphPath，其产出为短路回执
    child_tool_events = [
        event for event in events
        if event.get("subgraphPath") == ["subgraph-1"]
        and event.get("node_id") == "c-tool" and event.get("type") == "node_end"
    ]
    assert child_tool_events, "应收到子图内部 c-tool 的 node_end 事件"
    assert child_tool_events[0]["output"]["action_status"] == "SHADOW_DRY_RUN"
    assert service.orders["12345"].status == "pending"  # 子图写能力未触达店铺

    # 对照：关闭影子，同一子图真实退款
    service2 = DemoShopService()
    run_graph(
        parent,
        graph_id="g-parent",
        graph_resolver={"g-child": child}.get,
        inputs={"order_id": "12345"},
        registry=_registry_with_service(service2),
    )
    assert service2.orders["12345"].status == "refunded"


# ---------- U257–U259 纯函数：推断 / 归一 / 对比 ----------


def _intent(tool, dry_run, parameters=None):
    return ToolIntent(
        node_id="t", tool=tool,
        permission="financial" if dry_run else "read",
        dry_run=dry_run, parameters=parameters,
        action_status="SHADOW_DRY_RUN" if dry_run else "SUCCESS",
    )


def test_infer_auto_action_classifies_refund_and_human():
    assert infer_auto_action([_intent("shop/process_refund", True, {"action": "approve_refund"})]) == "refunded"
    assert infer_auto_action([_intent("shop/process_refund", True, {"action": "request_human_approval"})]) == "human_review"
    assert infer_auto_action([_intent("shop/execute_refund", True, {})]) == "refunded"
    assert infer_auto_action([_intent("shop/request_human_approval", True, {})]) == "human_review"
    # READ 透传意图、SIMULATED 不产生写动作
    assert infer_auto_action([_intent("shop/list_pending_refunds", False)]) is None
    assert infer_auto_action([]) is None


def test_normalize_human_action_aliases():
    assert normalize_human_action("Refunded") == "refunded"
    assert normalize_human_action(" approve_refund ") == "refunded"
    assert normalize_human_action("request_human_approval") == "human_review"
    assert normalize_human_action("escalate") == "escalate"  # 自定义动作原样小写保留


def test_compare_shadow_three_states():
    match = compare_shadow("refunded", HumanOutcome(action="refunded"))
    assert match.match is True and match.diffs == []
    mismatch = compare_shadow("refunded", HumanOutcome(action="human_review"))
    assert mismatch.match is False and mismatch.diffs
    none = compare_shadow(None, None)
    assert none.match is None and none.diffs
    none_with_human = compare_shadow(None, HumanOutcome(action="refunded"))
    assert none_with_human.match is None


# ---------- U260 事件提取 ----------


def test_extract_shadow_events_builds_decisions_and_intents():
    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "webhook", "webhookUrl": "/h"}},
                {"id": "cond-1", "type": "condition", "name": "路由",
                 "config": {"branches": [{"label": "其他", "expression": "1 == 2", "target": "tool-2"}],
                            "defaultTarget": "tool-1"}},
                {"id": "tool-1", "type": "tool_call", "name": "退款",
                 "config": {"tool": "shop/execute_refund"}},
                {"id": "tool-2", "type": "tool_call", "name": "其他",
                 "config": {"tool": "noop"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "cond-1"},
                {"id": "e2", "source": "cond-1", "target": "tool-1"},
                {"id": "e3", "source": "cond-1", "target": "tool-2"},
            ],
        }
    )
    node_index = {node.id: node for node in graph.nodes}
    permissions = {"shop/execute_refund": "financial", "shop/list_pending_refunds": "read"}
    events = [
        ("cond-1", {"branch": "__default__", "target": "tool-1"}),
        ("tool-1", {"result": {"status": "SHADOW_DRY_RUN", "tool": "shop/execute_refund",
                               "permission": "financial", "parameters": {"order_id": "1"}},
                    "action_status": "SHADOW_DRY_RUN"}),
    ]
    decisions, intents = extract_shadow_events(events, node_index, permissions)
    assert len(decisions) == 1 and decisions[0].node_type == "condition"
    assert decisions[0].target == "tool-1"
    assert len(intents) == 1
    assert intents[0].dry_run is True and intents[0].tool == "shop/execute_refund"


# ---------- U261 ShadowStore ring / attach / reset ----------


def test_shadow_store_add_attach_ring_reset():
    store = ShadowStore(maxlen=2)
    refund_intents = [_intent("shop/execute_refund", True, {})]
    first = store.add(graph_id="g1", trace_id="tr1", decisions=[], tool_intents=refund_intents)
    assert first["id"] == "sr-1" and first["auto_action"] == "refunded"
    assert first["comparison"]["match"] is None  # 无人工结果，未比对

    # 补录人工结果 → 重算 comparison（一致）
    attached = store.attach_outcome("sr-1", HumanOutcome(action="refunded"))
    assert attached["comparison"]["match"] is True
    assert attached["human_outcome"]["action"] == "refunded"
    assert store.attach_outcome("sr-999", HumanOutcome(action="refunded")) is None

    store.add(graph_id="g1", trace_id="tr2", decisions=[], tool_intents=refund_intents)
    store.add(graph_id="g1", trace_id="tr3", decisions=[], tool_intents=refund_intents)
    listing = store.list("g1")
    assert [item["id"] for item in listing] == ["sr-3", "sr-2"]  # ring 淘汰 sr-1、倒序
    assert store.get("sr-1") is None and store.get("sr-3") is not None

    store.reset()
    assert store.list() == []
    again = store.add(graph_id="g1", trace_id="tr4", decisions=[], tool_intents=refund_intents)
    assert again["id"] == "sr-1"  # 计数归零


# --- REST 层 -----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _admin_session():
    token = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.post("/api/demo/reset")
    yield
    client.headers.pop("authorization", None)


def _create_refund_graph() -> str:
    return client.post("/api/graphs", json=_refund_graph_dict()).json()["id"]


def _monitoring_run_count() -> int:
    return len(client.get("/api/monitoring/runs").json().get("items", []))


# ---------- U262 发起影子运行：201 + 零生产观测污染 ----------


def test_create_shadow_run_records_intent_without_pollution():
    from atlas.api import main as main_module

    graph_id = _create_refund_graph()
    before_runs = _monitoring_run_count()
    before_status = main_module._demo_shop.orders["12345"].status

    response = client.post(
        f"/api/graphs/{graph_id}/shadow-runs",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": 299}},
    )
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["id"].startswith("sr-")
    assert record["graph_id"] == graph_id and record["status"] == "completed"
    assert record["trace_id"]
    assert record["auto_action"] == "refunded"
    intents = record["tool_intents"]
    write_intents = [item for item in intents if item["dry_run"]]
    assert len(write_intents) == 1
    assert write_intents[0]["action_status"] == "SHADOW_DRY_RUN"
    assert write_intents[0]["tool"] == "shop/process_refund"
    # 决策记录含 AI 上游的路由节点（condition/human/loop；ai_decision 不在其列）
    # 零污染：店铺状态不变、RunRecord 不增（不进监控/告警/门控）
    assert main_module._demo_shop.orders["12345"].status == before_status == "pending"
    assert _monitoring_run_count() == before_runs


# ---------- U263 创建带人工结果即出对比；列表/详情 ----------


def test_create_shadow_run_with_human_outcome_and_read_back():
    graph_id = _create_refund_graph()
    created = client.post(
        f"/api/graphs/{graph_id}/shadow-runs",
        json={
            "inputs": {"order_id": "12345", "reason": "商品破损", "amount": 299},
            "human_outcome": {"action": "refunded", "note": "人工确认退款"},
        },
    ).json()
    assert created["comparison"]["match"] is True
    assert created["human_outcome"]["note"] == "人工确认退款"

    items = client.get("/api/shadow-runs").json()["items"]
    assert items[0]["id"] == created["id"]
    detail = client.get(f"/api/shadow-runs/{created['id']}").json()
    assert detail["id"] == created["id"] and detail["comparison"]["match"] is True

    filtered = client.get(f"/api/shadow-runs?graph_id={graph_id}").json()["items"]
    assert all(item["graph_id"] == graph_id for item in filtered)
    assert len(client.get("/api/shadow-runs?limit=0").json()["items"]) >= 1  # clamp 下限 1


# ---------- U264 事后补录对比：重算 / 422 / 404 ----------


def test_compare_endpoint_recomputes_and_validates():
    graph_id = _create_refund_graph()
    sid = client.post(
        f"/api/graphs/{graph_id}/shadow-runs",
        json={"inputs": {"order_id": "12346", "reason": "不想要了", "amount": 5000}},
    ).json()["id"]
    # 系统本会 human_review，人工却退款 → 不一致
    mismatch = client.post(
        f"/api/shadow-runs/{sid}/compare",
        json={"human_outcome": {"action": "refunded"}},
    )
    assert mismatch.status_code == 200, mismatch.text
    assert mismatch.json()["comparison"]["match"] is False
    assert mismatch.json()["comparison"]["auto_action"] == "human_review"

    # 空 action → 422
    assert client.post(
        f"/api/shadow-runs/{sid}/compare", json={"human_outcome": {"action": "  "}}
    ).status_code == 422
    assert client.post(f"/api/shadow-runs/{sid}/compare", json={}).status_code == 422
    # 不存在 → 404
    assert client.post(
        "/api/shadow-runs/sr-999/compare", json={"human_outcome": {"action": "refunded"}}
    ).status_code == 404
    assert client.get("/api/shadow-runs/sr-999").status_code == 404


# ---------- U265 图不存在 404（发起/列表过滤） ----------


def test_shadow_run_404_when_graph_missing():
    assert client.post("/api/graphs/graph-ghost/shadow-runs", json={}).status_code == 404


# ---------- U266 权限矩阵：viewer 只读、跨租户 404 ----------


def test_shadow_permission_matrix_and_tenant_isolation():
    graph_id = _create_refund_graph()
    record = client.post(
        f"/api/graphs/{graph_id}/shadow-runs",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": 299}},
    ).json()

    viewer_token = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {viewer_token}"
    assert client.get("/api/shadow-runs").status_code == 200  # read 可看
    assert client.get(f"/api/shadow-runs/{record['id']}").status_code == 200
    assert client.post(f"/api/graphs/{graph_id}/shadow-runs", json={}).status_code == 403  # 不可发起
    assert client.post(
        f"/api/shadow-runs/{record['id']}/compare",
        json={"human_outcome": {"action": "refunded"}},
    ).status_code == 403

    admin_b_token = client.post(
        "/api/auth/login", json={"username": "admin-b", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {admin_b_token}"
    client.post("/api/demo/reset")
    assert client.get(f"/api/shadow-runs/{record['id']}").status_code == 404  # 跨租户不泄漏
    assert client.post(f"/api/graphs/{graph_id}/shadow-runs", json={}).status_code == 404


# ---------- U267 影子自动预置审批，human_approval 不挂起 + READ 透传意图 ----------


def test_shadow_run_presets_approvals_and_passes_read_through():
    # approval-timeout-reject 模板含 human_approval 节点：影子预置 approved 秒过，不挂起
    template_graph = client.get("/api/templates/approval-timeout-reject").json()["graph"]
    graph_id = client.post("/api/graphs", json=template_graph).json()["id"]
    response = client.post(f"/api/graphs/{graph_id}/shadow-runs", json={"inputs": {}})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"  # 未在审批处挂起

    # READ 能力透传：list_pending_refunds 图影子跑，意图 dry_run=False / SUCCESS
    read_graph = client.post("/api/graphs", json={
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/h"}},
            {"id": "tool-1", "type": "tool_call", "name": "拉单",
             "config": {"tool": "shop/list_pending_refunds"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }).json()["id"]
    read_run = client.post(f"/api/graphs/{read_graph}/shadow-runs", json={"inputs": {}}).json()
    read_intents = [item for item in read_run["tool_intents"] if item["tool"] == "shop/list_pending_refunds"]
    assert len(read_intents) == 1
    assert read_intents[0]["dry_run"] is False
    assert read_intents[0]["action_status"] == "SUCCESS"


# ---------- U268 reset 清空影子记录 ----------


def test_demo_reset_clears_shadow_runs():
    graph_id = _create_refund_graph()
    client.post(
        f"/api/graphs/{graph_id}/shadow-runs",
        json={"inputs": {"order_id": "12345", "reason": "商品破损", "amount": 299}},
    )
    services = tenant_registry.get("t1")
    assert services.shadow_store.list()
    client.post("/api/demo/reset")
    assert services.shadow_store.list() == []


# ---------- U269 PG 档：进程内 ShadowStore 同形（影子存储不 PG 化） ----------


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ATLAS_RUN_INTEGRATION") != "1",
    reason="set ATLAS_RUN_INTEGRATION=1 and DATABASE_URL to run PG integration",
)
def test_pg_tenant_services_shadow_store_inprocess():
    assert os.environ.get("ATLAS_STORAGE_BACKEND", "memory") == "pg"
    services = tenant_registry.get("t1")
    assert isinstance(services.shadow_store, ShadowStore)
    saved = services.shadow_store.add(
        graph_id="g-pg", trace_id="tr-pg", decisions=[],
        tool_intents=[_intent("shop/execute_refund", True, {})],
    )
    assert saved["auto_action"] == "refunded"
    assert services.shadow_store.get(saved["id"])["id"] == saved["id"]
    services.shadow_store.reset()
