"""M5b 批 2-1：中断帧序列化 + loader 续跑（docs/24 §2.3，U43/U44）。

进程内后端即可测（内存 broker + frame_sink 收集帧），不依赖 PG；
跨进程恢复编排（读 interruptions 表 + 重启续跑线程）在批 2-2 的 recovery.py。
"""

from __future__ import annotations

import threading
import time as time_mod

from atlas.collaboration.approvals import ApprovalBroker
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import run_graph
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
                     "summary": "订单 {{trigger-1.context.payload.order_id}} 退款审批",
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


def _wait_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "config": {"waitType": "duration", "durationSeconds": 10}},
                {"id": "tool-after", "type": "tool_call", "name": "之后",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )


def _wait_frames(holder: list, timeout: float = 2.0) -> dict:
    deadline = time_mod.monotonic() + timeout
    while not holder and time_mod.monotonic() < deadline:
        time_mod.sleep(0.005)
    assert holder, "应在超时前产生挂起帧"
    return holder[0]


def test_frame_sink_captures_approval_frame():
    frames: list[dict] = []
    result = run_graph(
        _approval_graph(),
        inputs={"order_id": "12360", "approvals": {"human-1": "approved"}},
        frame_sink=frames.append,
    )
    assert result["status"] == "completed"
    assert frames
    frame = frames[0]
    assert frame["kind"] == "approval"
    assert frame["node_id"] == "human-1"
    assert frame["resume_token"]
    assert frame["deadline_at"]
    assert frame["graph_snapshot"]["nodes"]  # 帧内嵌图定义副本
    assert "trigger-1" in frame["resume_state"]["outputs"]  # 挂起前已完成 trigger
    assert frame["resume_state"]["inputs"]["order_id"] == "12360"
    assert frame["summary"] == "订单 12360 退款审批"  # 已插值


def test_resume_approval_frame_continues_with_same_token():
    # 第一进程：跑到审批挂起，frame_sink 捕获帧（不 resolve，线程挂起）。
    broker_a = ApprovalBroker()
    frames: list[dict] = []

    def run_original() -> None:
        run_graph(
            _approval_graph(),
            inputs={"order_id": "12361"},
            approval_broker=broker_a,
            frame_sink=frames.append,
        )

    first = threading.Thread(target=run_original)
    first.start()
    frame = _wait_frames(frames)
    token = frame["resume_token"]

    # 第二进程：新 broker（旧 pending 已失），restore + 续跑 + 同 token 决策。
    broker_b = ApprovalBroker()
    broker_b.restore(
        token=token,
        node_id=frame["node_id"],
        graph_id="adhoc",
        summary=frame["summary"],
        approver=frame["approver"],
        remaining_seconds=300,
    )

    resumed: dict = {}
    resume_events: list[dict] = []

    def run_resume() -> None:
        resumed["result"] = run_graph(
            _approval_graph(),
            inputs={"order_id": "12361"},
            approval_broker=broker_b,
            emit=resume_events.append,
            resume=frame,
        )

    second = threading.Thread(target=run_resume)
    second.start()
    assert broker_b.resolve(token, "approved", comment="同意退款") is True
    second.join(timeout=2)

    result = resumed["result"]
    assert result["status"] == "completed"
    assert result["outputs"]["human-1"]["decision"] == "approved"
    assert result["outputs"]["human-1"]["resolvedBy"] == "human"
    assert result["outputs"]["human-1"]["summary"] == "订单 12361 退款审批"
    assert "tool-approve" in result["outputs"]
    assert "tool-reject" not in result["outputs"]
    # 上游已完成：trigger 产出保留（预填）但不在续跑中重跑（无 node_start）。
    assert "trigger-1" in result["outputs"]
    resume_starts = [e["node_id"] for e in resume_events if e["type"] == "node_start"]
    assert "trigger-1" not in resume_starts
    assert "human-1" in resume_starts
    assert broker_b.list_pending() == []

    # 清理第一进程的挂起线程。
    broker_a.resolve(token, "rejected")
    first.join(timeout=2)


def test_resume_wait_uses_remaining_duration(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: slept.append(seconds))

    graph = _wait_graph()
    # 手动构造 wait 帧：原本 10s，deadline 距今 5s（重启已消耗 5s）。
    frame = build_frame(
        token="t",
        run_id="",
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(5),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {}, "outputs": {}},
    )
    result = run_graph(graph, resume=frame)
    assert result["status"] == "completed"
    assert slept and slept[-1] <= 5  # 剩余时长照扣，不满额重计（docs/24 §3.1）
    assert result["outputs"]["wait-1"]["mode"] == "wait"
    assert "tool-after" in result["outputs"]
