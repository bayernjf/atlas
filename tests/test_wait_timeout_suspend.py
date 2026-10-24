"""打包 BS（docs/136）：挂起超时转 suspended（onTimeout="suspend"）。

覆盖：
- U1528  DSL：human_approval/wait 的 onTimeout="suspend" 编译通过；非法值拒绝；
- U1529  顶层 human_approval 超时 + suspend → run_graph 抛 WaitTimeoutSuspend（原样穿透、
         token 仍 pending 未消费、resolve 仍可决）；
- U1530  API 同步路径：超时 + suspend → 200 status="suspended"、run 保持 suspended 未终态；
- U1531  顶层 wait(event) 超时 + suspend → run_graph 抛 WaitTimeoutSuspend；
- U1532  兼容性：缺省 onTimeout（human_approval=reject / wait=continue）超时行为逐字不变
         （human_approval 走 complete_timeout、wait 走 continue，均不抛）；
- U1533  反向门：配置全局自愈的图，WaitTimeoutSuspend 仍原样穿透（穿透族优先于自愈，
         不被吞掉或二次包装）。
"""

from __future__ import annotations

import threading

import pytest
from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import WaitNodeFailure, WaitTimeoutSuspend, run_graph

# 复用 test_api_runs 的模块级 client：conftest autouse fixture 会注入 t1 admin 登录头。
from tests.test_api_runs import client


def _approval_graph(on_timeout: str = "suspend") -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批",
             "config": {
                 "summary": "测试审批",
                 "approver": "",
                 "timeoutSeconds": 10,
                 "onTimeout": on_timeout,
                 "approvedTarget": "tool-approve",
                 "rejectedTarget": "tool-reject",
             }},
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


def _wait_event_graph(on_timeout: str = "suspend") -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待事件",
             "config": {"waitType": "event", "eventKey": "order_paid",
                        "timeoutSeconds": 1, "onTimeout": on_timeout}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


def _run_in_thread(broker, graph, **kwargs):
    holder: dict = {}

    def worker():
        try:
            holder["result"] = run_graph(graph, approval_broker=broker, **kwargs)
        except BaseException as exc:  # noqa: BLE001 测试线程捕获异常存入 holder
            holder["result"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return holder, thread


# ---------------------------------------------------------------- U1528 DSL

def test_u1528_suspend_is_valid_on_timeout_for_both_nodes():
    """onTimeout="suspend" 在 human_approval 与 wait(event) 均编译通过。"""
    parse_graph(_approval_graph(on_timeout="suspend"))
    parse_graph(_wait_event_graph(on_timeout="suspend"))


def test_u1528_invalid_on_timeout_rejected():
    """非法 onTimeout 值编译期拒绝（既有校验覆盖，超集扩展未破坏）。"""
    with pytest.raises((ValueError, KeyError)):
        parse_graph(_approval_graph(on_timeout="nonsense"))
    with pytest.raises((ValueError, KeyError)):
        parse_graph(_wait_event_graph(on_timeout="nonsense"))


# ------------------------------------------------- U1529 human_approval 超时 suspend

def test_u1529_approval_timeout_suspend_raises_and_keeps_token_pending(monkeypatch):
    """超时 + suspend：run_graph 抛 WaitTimeoutSuspend（原样穿透），token 仍 pending 可 resolve。"""
    broker = ApprovalBroker()
    graph = parse_graph(_approval_graph(on_timeout="suspend"))
    frames: list[dict] = []

    def fake_wait(self, token):
        # 模拟 broker 超时：不消费 token（decision 保持 None、event 未 set），返回 None。
        return None

    monkeypatch.setattr(ApprovalBroker, "wait", fake_wait)

    holder, thread = _run_in_thread(broker, graph, graph_id="g1", frame_sink=frames.append)
    thread.join(timeout=5)
    assert not thread.is_alive(), "run_graph 必须在超时 suspend 后退出"

    exc = holder["result"]
    assert isinstance(exc, WaitTimeoutSuspend), f"期望 WaitTimeoutSuspend，实际 {exc!r}"
    assert exc.node_id == "human-1"
    assert frames, "挂起帧必须在超时前已写（run 处于 suspended）"
    # token 未被 complete_timeout 消费：仍 pending 且可被人工 resolve（U1529 后半）。
    token = frames[0]["resume_token"]
    assert broker.resolve(token, "approved", resolved_by="human") is True
    assert broker.get(token)["decision"] == "approved"


def test_u1531_event_wait_timeout_suspend_raises(monkeypatch):
    """wait(event) 超时 + suspend：run_graph 抛 WaitTimeoutSuspend（原样穿透）。"""
    broker = EventWaitBroker()
    graph = parse_graph(_wait_event_graph(on_timeout="suspend"))
    frames: list[dict] = []

    def fake_wait(self, token, *, is_cancelled=None):
        return None  # 模拟事件等待超时

    monkeypatch.setattr(EventWaitBroker, "wait", fake_wait)

    holder: dict = {}

    def worker():
        try:
            holder["result"] = run_graph(
                graph,
                graph_id="g2",
                event_wait_broker=broker,
                frame_sink=frames.append,
            )
        except BaseException as exc:  # noqa: BLE001 测试线程捕获异常存入 holder
            holder["result"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert not thread.is_alive(), "run_graph 必须在超时 suspend 后退出"

    exc = holder["result"]
    assert isinstance(exc, WaitTimeoutSuspend), f"期望 WaitTimeoutSuspend，实际 {exc!r}"
    assert exc.node_id == "wait-1"
    assert frames, "挂起帧必须在超时前已写（run 处于 suspended）"


# ------------------------------------------------------------ U1530 API 同步路径

def test_u1530_sync_run_timeout_suspend_keeps_run_suspended(monkeypatch):
    """API 同步路径：超时 + suspend → 200 status="suspended"，run 保持 suspended 未终态。"""
    def fake_wait(self, token):
        return None

    monkeypatch.setattr(ApprovalBroker, "wait", fake_wait)

    graph_id = client.post("/api/graphs", json=_approval_graph("suspend")).json()["id"]
    response = client.post(f"/api/graphs/{graph_id}/run", json={})
    assert response.status_code == 200
    assert response.json()["status"] == "suspended"

    items = client.get("/api/runs").json()["items"]
    run = next(item for item in items if item["graphId"] == graph_id)
    assert run["status"] == "suspended", "超时 suspend 的 run 必须保持 suspended（非 failed）"


# ---------------------------------------------------------------- U1532 兼容性

def test_u1532_default_approval_timeout_unchanged(monkeypatch):
    """兼容性：human_approval 缺省 onTimeout=reject 超时走 complete_timeout，不抛。"""
    broker = ApprovalBroker()
    graph = parse_graph(_approval_graph(on_timeout="reject"))
    frames: list[dict] = []

    def fake_wait(self, token):
        return None

    monkeypatch.setattr(ApprovalBroker, "wait", fake_wait)

    holder, thread = _run_in_thread(broker, graph, graph_id="g3", frame_sink=frames.append)
    thread.join(timeout=5)
    result = holder["result"]
    assert not isinstance(result, WaitTimeoutSuspend)
    # 缺省行为：超时 → complete_timeout 落 rejected → 走 rejectedTarget（tool-reject 执行）。
    assert isinstance(result, dict)
    # tool-reject 被执行（rejectedTarget 侧产出出现）即证明缺省超时走 complete_timeout→rejected。
    assert "tool-reject" in result["outputs"]


def test_u1532_default_event_wait_timeout_unchanged(monkeypatch):
    """兼容性：wait(event) 缺省 onTimeout=continue 超时继续（resolvedBy=timeout），不抛。"""
    broker = EventWaitBroker()
    graph = parse_graph(_wait_event_graph(on_timeout="continue"))
    frames: list[dict] = []

    def fake_wait(self, token, *, is_cancelled=None):
        return None

    monkeypatch.setattr(EventWaitBroker, "wait", fake_wait)

    holder: dict = {}

    def worker():
        try:
            holder["result"] = run_graph(
                graph,
                graph_id="g4",
                event_wait_broker=broker,
                frame_sink=frames.append,
            )
        except BaseException as exc:  # noqa: BLE001 测试线程捕获异常存入 holder
            holder["result"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=5)
    result = holder["result"]
    assert not isinstance(result, WaitTimeoutSuspend)
    assert isinstance(result, dict)
    wait_out = result["outputs"]["wait-1"]
    assert wait_out["resolvedBy"] == "timeout"
    assert wait_out["signaled"] is False


# ---------------------------------------------------------------- U1533 反向门（穿透优先于自愈）

def test_u1533_wait_timeout_suspend_pierces_self_healing(monkeypatch):
    """反向门：图配置全局自愈（onUncaughtError=route_to_subgraph）时，WaitTimeoutSuspend
    仍原样穿透——穿透族优先于自愈，异常不得被吞掉或二次包装。"""
    broker = ApprovalBroker()
    raw = _approval_graph(on_timeout="suspend")
    # 自愈目标：errorSubgraphId 必须指向图中 subgraph 类型节点（docs/123 §2.2）。
    # heal-sub 挂在 approved 出口（tool-approve → heal-sub → tool-heal-after）之后：
    # 超时 suspend 时 human-1 不路由，自愈链路不可达；仅在真正自愈时才进入。
    # 子图本体必须无挂起点（否则持久化档下子图内挂起被 SUBGRAPH_SUSPEND_UNSUPPORTED 拦截）。
    raw["nodes"].extend([
        {"id": "heal-sub", "type": "subgraph", "name": "自愈子流程",
         "config": {"graphId": "g-heal", "inputs": {}}},
        {"id": "tool-heal-after", "type": "tool_call", "name": "自愈后继",
         "config": {"tool": "op-after"}},
    ])
    raw["edges"].extend([
        {"id": "heal-e1", "source": "tool-approve", "target": "heal-sub"},
        {"id": "heal-e2", "source": "heal-sub", "target": "tool-heal-after"},
    ])
    graph = parse_graph(raw)
    graph.globalErrorHandler = {
        "onUncaughtError": "route_to_subgraph",
        "errorSubgraphId": "heal-sub",
    }
    frames: list[dict] = []

    def fake_wait(self, token):
        return None

    monkeypatch.setattr(ApprovalBroker, "wait", fake_wait)

    def _plain_heal_subgraph():
        return parse_graph({
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "gh-trigger", "type": "trigger", "name": "ght",
                 "config": {"triggerType": "manual"}},
                {"id": "gh-tool", "type": "tool_call", "name": "自愈处理",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "ghe1", "source": "gh-trigger", "target": "gh-tool"},
            ],
        })

    resolver = {"g-heal": _plain_heal_subgraph()}

    holder, thread = _run_in_thread(
        broker, graph, graph_id="g5", graph_resolver=resolver.get, frame_sink=frames.append
    )
    thread.join(timeout=5)
    result = holder["result"]
    assert isinstance(result, WaitTimeoutSuspend), (
        f"自愈不得吞掉超时 suspend 信号，期望 WaitTimeoutSuspend，实际 {result!r}"
    )
    assert result.node_id == "human-1"


def test_u1533_wait_node_failure_still_fails_without_suspend(monkeypatch):
    """对照：wait(event) onTimeout=fail 超时仍是 WaitNodeFailure（suspend 只加新值不改旧值）。"""
    broker = EventWaitBroker()
    graph = parse_graph(_wait_event_graph(on_timeout="fail"))

    def fake_wait(self, token, *, is_cancelled=None):
        return None

    monkeypatch.setattr(EventWaitBroker, "wait", fake_wait)

    holder: dict = {}

    def worker():
        try:
            holder["result"] = run_graph(graph, graph_id="g6", event_wait_broker=broker)
        except BaseException as exc:  # noqa: BLE001 测试线程捕获异常存入 holder
            holder["result"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=5)
    exc = holder["result"]
    assert isinstance(exc, WaitNodeFailure)
    assert exc.code == "WAIT_TIMEOUT_FAILED"
