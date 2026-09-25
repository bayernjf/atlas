"""docs/62 §2 D-2 / §5 L2：挂起帧认领门在 loader 侧的分流（进程内可测，不依赖 PG）。

本文件只回答两件事：**门卡在三个挂起点的哪一步**、**返 False 时下游是否真的没跑**。
`claim_frame_for_resume` 那条 SQL 的互斥性**不由本文件证明**（自写假 store 不能反驳
真实后端的行锁语义），那部分在 tests/test_storage_pg_integration.py 的 U809 用真库跑；
端到端"两实例不双跑"由 scripts/dev/multi_instance_resume_recon.py 实跑把关（U810）。
"""

from __future__ import annotations

import threading
import time as _time

import pytest

from atlas.api.main import RunGraphResponse
from atlas.collaboration.approvals import ApprovalBroker
from atlas.collaboration.event_waits import EventWaitBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import RunSuperseded, run_graph
from atlas.storage.frame import build_frame, deadline_iso


def _approval_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
                {"id": "human-1", "type": "human_approval", "name": "人工审批",
                 "config": {
                     "summary": "订单退款审批",
                     "approver": "客服主管",
                     "timeoutSeconds": 300,
                     "onTimeout": "reject",
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
    )


def _wait_graph(wait_type: str, **config):
    cfg = {"waitType": wait_type}
    cfg.update(config)
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待", "config": cfg},
                {"id": "tool-after", "type": "tool_call", "name": "之后",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )


def _approval_resume_frame(token: str, *, graph) -> dict:
    """续跑帧：inputs 预置 approvals，让 wait 同步返回，只测门而不测等待本身。"""
    return build_frame(
        token=token,
        run_id="",
        node_id="human-1",
        kind="approval",
        deadline_at=deadline_iso(300),
        graph_snapshot=graph.model_dump(),
        resume_state={
            "graph_id": "adhoc",
            "inputs": {"approvals": {"human-1": "approved"}},
            "outputs": {},
        },
        summary="订单退款审批",
        approver="客服主管",
    )


def _broker_for(frame: dict) -> ApprovalBroker:
    broker = ApprovalBroker()
    broker.restore(
        token=frame["resume_token"],
        node_id=frame["node_id"],
        graph_id="adhoc",
        summary=frame.get("summary", ""),
        approver=frame.get("approver", ""),
        remaining_seconds=300,
    )
    return broker


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return True
        _time.sleep(0.005)
    return predicate()


# --- 审批挂起点（三个门之一）---------------------------------------------------


def test_approval_claim_winner_continues_downstream():
    graph = _approval_graph()
    frame = _approval_resume_frame("tok-win", graph=graph)
    claims: list[str] = []

    result = run_graph(
        graph,
        approval_broker=_broker_for(frame),
        resume=frame,
        resume_claim=lambda token: (claims.append(token), True)[1],
    )

    assert claims == ["tok-win"], "门必须用帧内原 token 认领，不换号"
    assert result["status"] == "completed"
    assert result["outputs"]["human-1"]["decision"] == "approved"
    assert "tool-approve" in result["outputs"]


def test_approval_claim_loser_raises_and_never_runs_downstream():
    graph = _approval_graph()
    frame = _approval_resume_frame("tok-lose", graph=graph)
    events: list[dict] = []

    with pytest.raises(RunSuperseded) as exc:
        run_graph(
            graph,
            approval_broker=_broker_for(frame),
            resume=frame,
            emit=events.append,
            resume_claim=lambda token: False,
        )

    assert exc.value.node_id == "human-1"
    assert exc.value.token == "tok-lose"
    # 输家的硬判据：一个下游节点都没执行（node_end 里没有 tool-*），也没有 run_end。
    ended = [e["node_id"] for e in events if e["type"] == "node_end"]
    assert "tool-approve" not in ended
    assert "tool-reject" not in ended
    assert "run_end" not in [e["type"] for e in events]


def test_no_claim_gate_is_always_pass_through():
    """`resume_claim=None`（内存档）＝恒放行：两档 run 语义逐键不变（docs/62 §3.4 关键约束）。"""
    graph = _approval_graph()
    frame = _approval_resume_frame("tok-none", graph=graph)

    result = run_graph(graph, approval_broker=_broker_for(frame), resume=frame)

    assert result["status"] == "completed"
    assert "tool-approve" in result["outputs"]


# --- 时长/到点等待 --------------------------------------------------------------


def test_duration_wait_gate_rejects_loser(monkeypatch):
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: None)
    graph = _wait_graph("duration", durationSeconds=10)
    frame = build_frame(
        token="tok-dur",
        run_id="",
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(5),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {}, "outputs": {}},
    )
    claims: list[str] = []

    with pytest.raises(RunSuperseded) as exc:
        run_graph(
            graph,
            resume=frame,
            resume_claim=lambda token: (claims.append(token), False)[1],
        )

    # 续跑必须认领**帧内** token（不是新随机号），否则跨进程根本抢的是两把锁。
    assert claims == ["tok-dur"]
    assert (exc.value.node_id, exc.value.token) == ("wait-1", "tok-dur")


def test_duration_wait_gate_admits_single_process(monkeypatch):
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: None)
    graph = _wait_graph("duration", durationSeconds=10)
    frames: list[dict] = []
    claimed: list[str] = []

    def claim(token: str) -> bool:
        claimed.append(token)
        return True

    # 首跑（非续跑）也要过门：帧已落库，认领即把 resumed_at 翻起来。
    result = run_graph(graph, frame_sink=frames.append, resume_claim=claim)

    assert result["status"] == "completed"
    assert "tool-after" in result["outputs"]
    # 认领用的必须是**写进那一行**的 token，否则 UPDATE 命中 0 行＝把自己判成输家。
    assert claimed == [frames[0]["resume_token"]]


# --- 事件等待 -------------------------------------------------------------------


def _run_event_wait(broker: EventWaitBroker, *, claim, holder: list, events: list,
                    frames: list) -> None:
    def worker() -> None:
        try:
            holder.append(
                run_graph(
                    _wait_graph("event", eventKey="order_paid", timeoutSeconds=30,
                                onTimeout="continue"),
                    event_wait_broker=broker,
                    frame_sink=frames.append,
                    emit=events.append,
                    resume_claim=claim,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - 让断言在主线程做
            holder.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert _wait_until(lambda: bool(frames)), "应在超时前产生 event wait 帧"
    broker.signal_token(frames[0]["resume_token"], {"paidAt": "2026-09-23"})
    thread.join(timeout=3)
    assert not thread.is_alive(), "续跑线程不得滞留"


def test_event_wait_gate_rejects_loser():
    holder: list = []
    events: list = []
    frames: list = []
    _run_event_wait(EventWaitBroker(), claim=lambda token: False,
                    holder=holder, events=events, frames=frames)

    exc = holder[0]
    assert isinstance(exc, RunSuperseded)
    assert exc.node_id == "wait-1"
    assert exc.token == frames[0]["resume_token"], "门按帧 token 裁定"
    assert "tool-after" not in [e["node_id"] for e in events if e["type"] == "node_end"]


def test_event_wait_gate_admits_winner():
    holder: list = []
    events: list = []
    frames: list = []
    _run_event_wait(EventWaitBroker(), claim=lambda token: True,
                    holder=holder, events=events, frames=frames)

    result = holder[0]
    assert result["status"] == "completed"
    assert "tool-after" in result["outputs"]


# --- 输家/赢家共用同一份认领状态（假 store：只证分流，不证 SQL 互斥）------------


def test_two_drivers_on_one_frame_exactly_one_crosses_the_gate():
    graph = _approval_graph()
    frame = _approval_resume_frame("tok-race", graph=graph)
    claimed: set[str] = set()
    lock = threading.Lock()

    def claim(token: str) -> bool:
        with lock:
            if token in claimed:
                return False
            claimed.add(token)
            return True

    outcomes: list = []

    def drive() -> None:
        try:
            outcomes.append(
                run_graph(graph, approval_broker=_broker_for(frame), resume=frame,
                          resume_claim=claim)
            )
        except BaseException as exc:  # noqa: BLE001
            outcomes.append(exc)

    threads = [threading.Thread(target=drive) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert len(outcomes) == 2
    kinds = sorted(type(o).__name__ for o in outcomes)
    assert kinds == ["RunSuperseded", "dict"], "同一条帧只允许一个驱动者越过挂起点"
    assert claimed == {"tok-race"}


# --- 反向对照：把门恒放行，双跑必须重现（证明上一条不是"本来就只跑一次"）--------


def test_reverse_control_always_admitting_claim_lets_both_drivers_through():
    graph = _approval_graph()
    frame = _approval_resume_frame("tok-always", graph=graph)

    outcomes: list = []

    def drive() -> None:
        try:
            outcomes.append(
                run_graph(graph, approval_broker=_broker_for(frame), resume=frame,
                          resume_claim=lambda token: True)
            )
        except BaseException as exc:  # noqa: BLE001
            outcomes.append(exc)

    threads = [threading.Thread(target=drive) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    # 认领恒真＝docs/62 §0 的改造前世界：两个驱动者都越过挂起点、都执行下游。
    assert [type(o).__name__ for o in outcomes] == ["dict", "dict"], (
        "若这条也只放行一个，说明loader 里有第二道隐门，U809 测的就不是认领了"
    )
    assert all(o["outputs"].get("tool-approve") is not None for o in outcomes)


# --- 控制流穿透：不得被节点级 fail-safe 吞掉 ------------------------------------


def test_superseded_in_subgraph_passthrough_clause_exists():
    """子图 fail-safe（except Exception → 错误产出）会吞掉控制流；认领信号必须在豁免名单里。

    这里静态断言豁免元组含 RunSuperseded：把它摘掉，输家会被记成"子图执行失败"并继续跑完整图，
    正是 029 要防的事故形状。
    """
    import inspect

    from atlas.graph import loader

    source = inspect.getsource(loader)
    assert "except (RunCancelled, DebugStopped, RunSuperseded):" in source
    assert source.count("except (RunCancelled, DebugStopped, RunSuperseded):") == 2, (
        "节点执行器与子图 fail-safe 两处都要豁免，少一处就是给未来留双跑口子"
    )


# --- API 层输家分流（U810c：零终态写）------------------------------------------


def test_sync_run_endpoint_loser_writes_no_terminal_state(monkeypatch):
    from fastapi.testclient import TestClient

    from atlas.api import main as api_main
    from tests.conftest import DEFAULT_AUTH_HEADER

    client = TestClient(api_main.app)
    client.headers.update(DEFAULT_AUTH_HEADER)
    graph_id = client.post("/api/graphs", json=_approval_graph().model_dump()).json()["id"]
    monkeypatch.setattr(api_main, "_resume_claim_for", lambda: (lambda token: False))

    response = client.post(
        f"/api/graphs/{graph_id}/run",
        json={"inputs": {"order_id": "90001", "approvals": {"human-1": "approved"}}},
    )

    assert response.status_code == 200
    body = RunGraphResponse(**response.json())
    assert body.status == "suspended"
    assert body.outputs == {}
    item = next(i for i in client.get("/api/runs").json()["items"] if i["graphId"] == graph_id)
    # 关键：既不 failed 也不 completed——终态留给赢家，否则覆盖赢家的执行结果。
    assert item["status"] == "suspended", item
    assert item.get("error") in (None, "")


def test_stream_endpoint_emits_superseded_frame_without_terminal_state(monkeypatch):
    """SSE 出口（新面）：输家发 `event: superseded` 终止本流，且不改 run 终态。"""
    from fastapi.testclient import TestClient

    from atlas.api import main as api_main
    from tests.conftest import DEFAULT_AUTH_HEADER

    client = TestClient(api_main.app)
    client.headers.update(DEFAULT_AUTH_HEADER)
    graph_id = client.post("/api/graphs", json=_approval_graph().model_dump()).json()["id"]
    monkeypatch.setattr(api_main, "_resume_claim_for", lambda: (lambda token: False))

    with client.stream(
        "POST",
        f"/api/graphs/{graph_id}/run/stream",
        json={"inputs": {"order_id": "90002", "approvals": {"human-1": "approved"}}},
    ) as response:
        events = [line for line in response.iter_lines() if line.startswith("event:")]

    assert events[-1] == "event: superseded", events
    assert "event: result" not in events and "event: error" not in events
    item = next(i for i in client.get("/api/runs").json()["items"] if i["graphId"] == graph_id)
    assert item["status"] == "suspended", item
