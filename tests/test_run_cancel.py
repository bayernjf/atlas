"""B 包（docs/27 §4）：协作式急停 + hitCount/logpoint 断点 + resume 变量改写（U124–U133）。

- RunCancellationBroker：注册/取消幂等/注销/reset/无句柄 False；
- run_graph 普通流节点边界命中 is_cancelled 抛 RunCancelled（含 parallel 分支、子图穿透 fail-safe）；
- 调试流急停折叠为 DebugStopped（event:stopped，不重复 cancelled）；
- logpoint 命中只发 debug_log 不暂停；hitCount 每 N 次命中暂停；条件真才计数；
- resume globals 浅合并写回、续跑即用新值；非法键/不可序列化/未知暂停 422 口径；stop 忽略覆盖；
- REST：POST /runs/{id}/cancel 运行中 200（重复幂等）、未知 404、已结束 409、需 operate 权限；
- SSE 运行中急停收 event:cancelled，run 落 cancelled 状态（不进灰度门控评估）。
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.collaboration.cancellations import RunCancellationBroker, RunCancelled
from atlas.debug.controller import DebugController
from atlas.debug.sessions import DebuggerBroker, DebugStopped
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph

from tests.conftest import DEFAULT_AUTH_HEADER

client = TestClient(app)
TIMEOUT = 5


# --------------------------------------------------------------------------- #
# 图夹具
# --------------------------------------------------------------------------- #
def _linear_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "tool-1", "type": "tool_call", "name": "工具1",
                 "config": {"tool": "op-a"}},
                {"id": "tool-2", "type": "tool_call", "name": "工具2",
                 "config": {"tool": "op-b"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "tool-1"},
                {"id": "e2", "source": "tool-1", "target": "tool-2"},
            ],
        }
    )


def _parallel_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "parallel-1", "type": "parallel", "name": "并行",
                 "config": {
                     "joinStrategy": "all_completed",
                     "branches": [
                         {"label": "A", "target": "tool-a"},
                         {"label": "B", "target": "tool-b"},
                     ],
                     "joinTarget": "tool-join",
                 }},
                {"id": "tool-a", "type": "tool_call", "name": "A",
                 "config": {"tool": "op-a"}},
                {"id": "tool-b", "type": "tool_call", "name": "B",
                 "config": {"tool": "op-b"}},
                {"id": "tool-join", "type": "tool_call", "name": "汇聚",
                 "config": {"tool": "op-join"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "parallel-1"},
                {"id": "e2", "source": "parallel-1", "target": "tool-a"},
                {"id": "e3", "source": "parallel-1", "target": "tool-b"},
                {"id": "e4", "source": "tool-a", "target": "tool-join"},
                {"id": "e5", "source": "tool-b", "target": "tool-join"},
            ],
        }
    )


def _nested_child():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "c-trigger", "type": "trigger", "name": "ct",
                 "config": {"triggerType": "manual"}},
                {"id": "c-tool", "type": "tool_call", "name": "子工具1",
                 "config": {"tool": "op-a"}},
                {"id": "c-tool-2", "type": "tool_call", "name": "子工具2",
                 "config": {"tool": "op-b"}},
            ],
            "edges": [
                {"id": "ce1", "source": "c-trigger", "target": "c-tool"},
                {"id": "ce2", "source": "c-tool", "target": "c-tool-2"},
            ],
        }
    )


def _parent_with_subgraph(child_graph_id="g-child-b"):
    child = _nested_child()
    parent = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "p-trigger", "type": "trigger", "name": "pt",
                 "config": {"triggerType": "manual"}},
                {"id": "subgraph-1", "type": "subgraph", "name": "子流程",
                 "config": {"graphId": child_graph_id, "inputs": {}}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "pe1", "source": "p-trigger", "target": "subgraph-1"},
                {"id": "pe2", "source": "subgraph-1", "target": "tool-after"},
            ],
        }
    )
    return parent, {child_graph_id: child}


def _condition_global_graph():
    """global.amount 路由：>1000 走大额工具，否则自动工具（用于变量改写续跑观测）。"""
    return parse_graph(
        {
            "version": 1,
            # amount 须在 variables 声明，{{global.amount}} 引用才过静态 REF 校验（dsl.py）。
            "variables": [{"name": "amount", "value": 50}],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "condition-1", "type": "condition", "name": "金额路由",
                 "config": {
                     "branches": [
                         {"label": "大额",
                          "expression": "{{global.amount}} > 1000",
                          "target": "tool-big"},
                     ],
                     "defaultTarget": "tool-small",
                 }},
                {"id": "tool-big", "type": "tool_call", "name": "大额侧",
                 "config": {"tool": "human-review"}},
                {"id": "tool-small", "type": "tool_call", "name": "小额侧",
                 "config": {"tool": "auto-refund"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "condition-1"},
                {"id": "e2", "source": "condition-1", "target": "tool-big"},
                {"id": "e3", "source": "condition-1", "target": "tool-small"},
            ],
        }
    )


def _wait_graph():
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "config": {"waitType": "duration", "durationSeconds": 2}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "op-after"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


# --------------------------------------------------------------------------- #
# U124 broker 单元
# --------------------------------------------------------------------------- #
def test_cancellation_broker_register_cancel_idempotent_unregister_reset():
    broker = RunCancellationBroker()
    event = broker.register("r1")
    assert not event.is_set()

    # 重复注册不覆盖既有事件。
    assert broker.register("r1") is event
    # 无句柄 cancel False。
    assert broker.cancel("missing") is False
    # 置位 True，重复取消幂等仍 True（事件保持置位）。
    assert broker.cancel("r1") is True
    assert event.is_set()
    assert broker.cancel("r1") is True

    broker.unregister("r1")
    assert broker.cancel("r1") is False  # 注销后无句柄

    e2 = broker.register("r2")
    broker.reset()
    assert e2.is_set() and broker.cancel("r2") is False


# --------------------------------------------------------------------------- #
# U125–U127 run_graph 节点边界 / parallel / 子图穿透
# --------------------------------------------------------------------------- #
def test_normal_run_cancelled_at_node_boundary():
    calls = {"n": 0}

    def is_cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] >= 2  # 放行 trigger-1，tool-1 边界取消

    events: list[dict] = []
    with pytest.raises(RunCancelled) as exc_info:
        run_graph(_linear_graph(), emit=events.append, is_cancelled=is_cancelled)
    assert exc_info.value.node_id == "tool-1"
    started = [e["node_id"] for e in events if e.get("type") == "node_start"]
    assert "trigger-1" in started and "tool-2" not in started


def test_cancellation_propagates_into_parallel_branches():
    calls = {"n": 0}

    def is_cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3  # 放行 trigger / parallel 控制节点，分支边界取消

    events: list[dict] = []
    with pytest.raises(RunCancelled):
        run_graph(_parallel_graph(), emit=events.append, is_cancelled=is_cancelled)
    started = [e["node_id"] for e in events if e.get("type") == "node_start"]
    assert "tool-join" not in started  # 汇聚未执行


def test_cancellation_pierces_subgraph_failsafe():
    parent, children = _parent_with_subgraph()
    calls = {"n": 0}

    def is_cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] >= 5  # p-trigger/subgraph-1/c-trigger/c-tool 放行，c-tool-2 取消

    events: list[dict] = []
    with pytest.raises(RunCancelled) as exc_info:
        run_graph(
            parent,
            emit=events.append,
            is_cancelled=is_cancelled,
            graph_resolver=lambda gid: children[gid],
        )
    # 必须是 RunCancelled 穿透，而非折叠成 subgraph failed。
    assert exc_info.value.node_id == "c-tool-2"
    started = [e["node_id"] for e in events if e.get("type") == "node_start"]
    assert "tool-after" not in started


# --------------------------------------------------------------------------- #
# U128/U129 logpoint / hitCount / 条件计数（controller 层）
# --------------------------------------------------------------------------- #
def _controller_session(breakpoints, step_mode=False):
    broker = DebuggerBroker()
    session = broker.create(graph_id="g", breakpoints=breakpoints)
    session.step_mode = step_mode
    events: list[dict] = []
    return session, DebugController(session, events.append), events


def test_logpoint_emits_debug_log_without_pausing_and_run_completes():
    session, controller, events = _controller_session(
        [{"node_id": "tool-1", "logMessage": "命中工具节点"}]
    )
    result = run_graph(
        _linear_graph(), emit=events.append, debug_controller=controller
    )
    assert result["status"] == "completed"
    logs = [e for e in events if e.get("type") == "debug_log"]
    assert len(logs) == 1
    assert logs[0]["node_id"] == "tool-1"
    assert logs[0]["hits"] == 1
    assert logs[0]["message"] == "命中工具节点"  # v1 原样不插值
    assert not [e for e in events if e.get("type") == "paused"]


def test_hit_count_pauses_only_on_every_nth_hit():
    session, controller, _ = _controller_session(
        [{"node_id": "x", "hitCount": 2}]
    )
    state = {"variables": {"global": {}}, "outputs": {}}
    first = controller._classify_hit("x", state)
    second = controller._classify_hit("x", state)
    third = controller._classify_hit("x", state)
    assert first is None and third is None
    assert second is not None and second[0] == "pause"
    assert session.hit_counts["x"] == 3


def test_conditional_breakpoint_counts_only_when_expression_true():
    session, controller, _ = _controller_session(
        [{"node_id": "x", "expression": "{{global.amount}} > 100", "hitCount": 1}]
    )
    false_state = {"variables": {"global": {"amount": 50}}, "outputs": {}}
    true_state = {"variables": {"global": {"amount": 500}}, "outputs": {}}
    assert controller._classify_hit("x", false_state) is None
    assert session.hit_counts.get("x", 0) == 0  # 条件假不计数
    decision = controller._classify_hit("x", true_state)
    assert decision is not None and decision[0] == "pause"
    assert session.hit_counts["x"] == 1


def test_condition_expression_error_failsafe_no_pause_no_count():
    session, controller, _ = _controller_session(
        [{"node_id": "x", "expression": "{{global.missing}} > 1"}]
    )
    state = {"variables": {"global": {}}, "outputs": {}}
    assert controller._classify_hit("x", state) is None
    assert session.last_condition_error


# --------------------------------------------------------------------------- #
# U130–U132 resume globals
# --------------------------------------------------------------------------- #
class _DebugRun:
    def __init__(self, graph, *, breakpoints=None, inputs=None, graph_resolver=None):
        self.events: list[dict] = []
        self.errors: list[Exception] = []
        self.broker = DebuggerBroker()
        self.session = self.broker.create(graph_id="g", breakpoints=breakpoints)
        controller = DebugController(self.session, self.events.append)

        def work():
            try:
                run_graph(
                    graph,
                    inputs=inputs,
                    graph_id="g",
                    graph_resolver=graph_resolver,
                    emit=self.events.append,
                    debug_controller=controller,
                )
            except DebugStopped as exc:
                self.errors.append(exc)

        self.thread = threading.Thread(target=work, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def wait_paused(self, node_id):
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            for event in reversed(self.events):
                if event.get("type") == "paused" and event.get("node_id") == node_id:
                    return event
            if self.errors:
                raise AssertionError(f"stopped: {self.errors[0]!r}")
            if not self.thread.is_alive():
                raise AssertionError(f"ended without pause at {node_id}: {self.events}")
            time.sleep(0.01)
        raise AssertionError(f"timeout at {node_id}")

    def join(self):
        self.thread.join(TIMEOUT)
        assert not self.thread.is_alive(), "debug thread hung"


def test_resume_globals_override_routes_condition_to_big_branch():
    run = _DebugRun(_condition_global_graph(), inputs={"amount": 50}).start()
    trig = run.wait_paused("trigger-1")
    assert run.session.resolve(trig["token"], "step")
    cond = run.wait_paused("condition-1")
    # 续跑前改写 global.amount（浅合并），condition 即用新值。
    run.session.apply_overrides(cond["token"], {"amount": 9999})
    assert run.session.resolve(cond["token"], "continue")
    run.join()
    started = [e["node_id"] for e in run.events if e.get("type") == "node_start"]
    assert "tool-big" in started and "tool-small" not in started
    assert not run.errors


def test_apply_overrides_rejects_bad_keys_unserializable_and_settled_pause():
    run = _DebugRun(_condition_global_graph(), inputs={"amount": 50}).start()
    trig = run.wait_paused("trigger-1")
    token = trig["token"]
    for bad_key in ("1x", "a-b", ""):
        with pytest.raises(ValueError):
            run.session.apply_overrides(token, {bad_key: 1})
    with pytest.raises(ValueError):
        run.session.apply_overrides(token, {"x": object()})
    with pytest.raises(ValueError):
        run.session.apply_overrides(token, {"x": {1, 2}})
    # 合法覆盖可暂存深拷贝。
    run.session.apply_overrides(token, {"amount": 1, "nested": {"k": "v"}})
    assert run.session.take_overrides(token) == {"amount": 1, "nested": {"k": "v"}}
    # 已决暂停再覆盖报错。
    assert run.session.resolve(token, "stop")
    run.join()
    with pytest.raises(ValueError):
        run.session.apply_overrides(token, {"amount": 2})
    assert run.errors and isinstance(run.errors[0], DebugStopped)


# --------------------------------------------------------------------------- #
# U124/U126/U133 REST + SSE
# --------------------------------------------------------------------------- #
def _create_graph(payload: dict) -> str:
    resp = client.post("/api/graphs", json=payload, headers=DEFAULT_AUTH_HEADER)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _find_run_id(graph_id: str) -> str:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        items = client.get("/api/runs", headers=DEFAULT_AUTH_HEADER).json().get("items", [])
        for item in items:
            if item.get("graphId") == graph_id:
                return item["runId"]
        time.sleep(0.05)
    raise AssertionError("run id not found")


def _start_stream(graph_id: str, body: dict):
    frames: list[str] = []

    def work():
        with client.stream(
            "POST", f"/api/graphs/{graph_id}/run/stream",
            json=body, headers=DEFAULT_AUTH_HEADER,
        ) as resp:
            for chunk in resp.iter_text():
                frames.append(chunk)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    return thread, frames


def _wait_debug_token(graph_id: str) -> str:
    """调试流不落 run 库；经 GET /api/debug 轮询活动暂停取 token（不依赖 SSE 实时帧）。"""
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        items = client.get("/api/debug", headers=DEFAULT_AUTH_HEADER).json().get("items", [])
        for item in items:
            if item.get("graph_id") == graph_id:
                return item["token"]
        time.sleep(0.05)
    raise AssertionError("debug pause token not found")


def test_cancel_running_stream_emits_cancelled_and_sets_status():
    gid = _create_graph(_wait_graph())
    thread, frames = _start_stream(gid, {"inputs": {}})
    run_id = _find_run_id(gid)
    time.sleep(0.4)  # 进入 wait 阻塞（不在中点强杀，等下一节点边界）

    resp = client.post(f"/api/runs/{run_id}/cancel", headers=DEFAULT_AUTH_HEADER)
    assert resp.status_code == 200 and resp.json() == {"run_id": run_id, "cancelled": True}
    # 重复取消幂等 200（句柄仍在直至 worker 退出）。
    assert client.post(f"/api/runs/{run_id}/cancel", headers=DEFAULT_AUTH_HEADER).status_code == 200

    thread.join(TIMEOUT)
    assert not thread.is_alive(), "stream thread hung"
    text = "".join(frames)
    assert "event: cancelled" in text
    assert '"reason": "user_cancel"' in text
    detail = client.get(f"/api/runs/{run_id}", headers=DEFAULT_AUTH_HEADER).json()
    assert detail["status"] == "cancelled"


def test_cancel_unknown_run_returns_404():
    resp = client.post("/api/runs/nope-does-not-exist/cancel", headers=DEFAULT_AUTH_HEADER)
    assert resp.status_code == 404


def test_cancel_finished_run_returns_409():
    gid = _create_graph(_linear_graph_dict())
    run = client.post(
        f"/api/graphs/{gid}/run", json={"inputs": {}}, headers=DEFAULT_AUTH_HEADER
    )
    assert run.status_code == 200
    items = client.get("/api/runs", headers=DEFAULT_AUTH_HEADER).json()["items"]
    run_id = next(i["runId"] for i in items if i["graphId"] == gid)
    resp = client.post(f"/api/runs/{run_id}/cancel", headers=DEFAULT_AUTH_HEADER)
    assert resp.status_code == 409


def _linear_graph_dict() -> dict:
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "tool-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "op-a"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }


def test_cancel_requires_operate_permission():
    login = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()
    viewer_headers = {"Authorization": f"Bearer {login['token']}"}
    resp = client.post("/api/runs/any-id/cancel", headers=viewer_headers)
    assert resp.status_code == 403


def test_debug_controller_cancel_check_folds_to_debug_stopped():
    # 控制器层：调试流注入运行级 is_cancelled，节点边界折叠为 DebugStopped（不发 cancelled）。
    broker = DebuggerBroker()
    session = broker.create(graph_id="g", breakpoints=[])
    from types import SimpleNamespace
    controller = DebugController(session, lambda e: None, is_cancelled=lambda: True)
    node = SimpleNamespace(id="tool-1", type="tool_call")
    state = {"variables": {"global": {}}, "outputs": {}}
    with pytest.raises(DebugStopped) as exc_info:
        controller.before_node(node, state)
    assert exc_info.value.node_id == "tool-1"


def test_debug_stream_stop_emits_stopped_not_cancelled():
    gid = _create_graph(_linear_graph_dict())
    thread, frames = _start_stream(gid, {"inputs": {}, "debug": {"breakpoints": []}})
    token = _wait_debug_token(gid)  # step 模式暂停在首节点

    # 调试流的急停语义是 stop：resume action=stop（即便带 globals 也忽略覆盖）。
    resp = client.post(
        f"/api/debug/{token}/resume",
        json={"action": "stop", "globals": {"anything": 1}},
        headers=DEFAULT_AUTH_HEADER,
    )
    assert resp.status_code == 200
    thread.join(TIMEOUT)
    assert not thread.is_alive(), "debug stream hung"
    text = "".join(frames)
    assert "event: stopped" in text
    assert "event: cancelled" not in text  # 调试流终态是 stopped，不产生 cancelled 帧


def test_resume_globals_rejected_with_422_on_bad_identifier():
    gid = _create_graph(_linear_graph_dict())
    thread, frames = _start_stream(gid, {"inputs": {}, "debug": {"breakpoints": []}})
    token = _wait_debug_token(gid)

    # continue 带非法 global 键名 → 422（首决未发生，暂停仍活动）。
    bad = client.post(
        f"/api/debug/{token}/resume",
        json={"action": "continue", "globals": {"bad-name": 1}},
        headers=DEFAULT_AUTH_HEADER,
    )
    assert bad.status_code == 422
    # 注：不可序列化值无法进入 JSON 请求体；该守卫由会话层单测
    # test_apply_overrides_rejects...（set/object）覆盖。
    # stop 带 globals 不校验、忽略覆盖、正常 200。
    ok = client.post(
        f"/api/debug/{token}/resume",
        json={"action": "stop", "globals": {"bad-name": 1}},
        headers=DEFAULT_AUTH_HEADER,
    )
    assert ok.status_code == 200
    thread.join(TIMEOUT)
    assert "event: stopped" in "".join(frames)
