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


# --- U519: 带 jitter 的 duration wait 续跑按帧内剩余、不二次抖动（docs/54 §3）---
def test_resume_jittered_wait_uses_remaining_no_second_jitter(monkeypatch):
    import random as _random

    slept: list[float] = []
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: slept.append(seconds))

    graph = parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "wait-1", "type": "wait", "name": "抖动等待",
                 "config": {"waitType": "duration", "durationSeconds": 10,
                            "jitterSeconds": 300}},
                {"id": "tool-after", "type": "tool_call", "name": "后继",
                 "config": {"tool": "op-after"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "wait-1"},
                {"id": "e2", "source": "wait-1", "target": "tool-after"},
            ],
        }
    )
    # 首次 actual 已写入帧 deadline（10+jitter）；重启后 deadline 距今 5s。
    frame = build_frame(
        token="t",
        run_id="",
        node_id="wait-1",
        kind="wait",
        deadline_at=deadline_iso(5),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": {}, "outputs": {}},
    )
    result = run_graph(graph, resume=frame, jitter_rng=_random.Random(7))
    assert result["status"] == "completed"
    assert slept and slept[-1] <= 5  # 仅睡剩余，不再叠加 0-300 的二次抖动
    out = result["outputs"]["wait-1"]
    assert "jitterSeconds" not in out
    assert "plannedDurationSeconds" not in out
    assert "tool-after" in result["outputs"]


# --- U1001–U1003: 挂起点在循环体内（foreach 逐项审批/等待）的续跑（docs/24 §2.3 追记）---
def _loop_approval_graph():
    """foreach 逐项审批：bodyTarget/collectTarget 均为审批节点，两条分支都连回 loop。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [{"name": "orders", "type": "array", "required": True}],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "逐单审批",
                 "config": {"mode": "foreach", "itemsExpression": "{{global.orders}}",
                            "bodyTarget": "human-1", "exitTarget": "tool-done",
                            "collectTarget": "human-1"}},
                {"id": "human-1", "type": "human_approval", "name": "审批",
                 "config": {"summary": "第 {{loop-1.item.id}} 单审批", "approver": "主管",
                            "timeoutSeconds": 300, "onTimeout": "reject",
                            "approvedTarget": "tool-approve",
                            "rejectedTarget": "tool-reject"}},
                {"id": "tool-approve", "type": "tool_call", "name": "通过",
                 "config": {"tool": "op-approve"}},
                {"id": "tool-reject", "type": "tool_call", "name": "拒绝",
                 "config": {"tool": "op-reject"}},
                {"id": "tool-done", "type": "tool_call", "name": "收尾",
                 "config": {"tool": "op-done"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "human-1"},
                {"id": "e3", "source": "loop-1", "target": "tool-done"},
                {"id": "e4", "source": "human-1", "target": "tool-approve"},
                {"id": "e5", "source": "human-1", "target": "tool-reject"},
                {"id": "e6", "source": "tool-approve", "target": "loop-1"},
                {"id": "e7", "source": "tool-reject", "target": "loop-1"},
            ],
        }
    )


def _loop_wait_graph():
    """foreach 逐项等待：等待节点即循环体入口，单出边连回 loop。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [{"name": "orders", "type": "array", "required": True}],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "逐单等待",
                 "config": {"mode": "foreach", "itemsExpression": "{{global.orders}}",
                            "bodyTarget": "wait-1", "exitTarget": "tool-done"}},
                {"id": "wait-1", "type": "wait", "name": "等待",
                 "config": {"waitType": "duration", "durationSeconds": 3}},
                {"id": "tool-done", "type": "tool_call", "name": "收尾",
                 "config": {"tool": "op-done"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "wait-1"},
                {"id": "e3", "source": "loop-1", "target": "tool-done"},
                {"id": "e4", "source": "wait-1", "target": "loop-1"},
            ],
        }
    )


def _foreach_state(items, index, results, target):
    return {
        "mode": "foreach", "items": items, "index": index, "iterations": index,
        "item": items[index], "results": results, "target": target,
        "exitReason": None, "expression_errors": [], "expressionErrorCodes": [],
    }


def _resume_frame(graph, *, token, node_id, kind, orders, outputs, timeout=300):
    """手动构造挂起帧：node_id 即循环体内的挂起点，outputs 为截至挂起点的产出。

    inputs 里预置 approvals 使审批秒过，用例无需线程（预置来源 resolvedBy="input"）。
    """
    return build_frame(
        token=token,
        run_id="",
        node_id=node_id,
        kind=kind,
        deadline_at=deadline_iso(timeout),
        graph_snapshot=graph.model_dump(),
        resume_state={
            "graph_id": "adhoc",
            "inputs": {"orders": orders, "approvals": {node_id: "approved"}},
            "outputs": outputs,
        },
        summary="第 1 单审批",
        approver="主管",
    )


def test_resume_inside_foreach_body_gives_each_item_its_own_approval():
    """U1001：挂起点在循环体内时，续跑只消费帧内 token 一次，后续每项各自登记新审批。

    修复前：尾图里挂起点的前驱（loop 节点）经回边自挂起点可达，langgraph 找不到入口，
    续跑直接抛 ValueError: Graph must have an entrypoint；补入口后仍会把帧内 token
    复用到每一轮，第二项起的审批被第一项的决定静默顶掉（既不重登记也不写新帧）。
    """
    graph = _loop_approval_graph()
    orders = [{"id": 1}, {"id": 2}, {"id": 3}]
    broker = ApprovalBroker()
    broker.restore(
        token="tok-loop-0", node_id="human-1", graph_id="adhoc",
        summary="第 1 单审批", approver="主管", remaining_seconds=300,
    )
    frame = _resume_frame(
        graph, token="tok-loop-0", node_id="human-1", kind="approval",
        orders=orders,
        outputs={"loop-1": _foreach_state(orders, 0, [], "human-1")},
    )

    result = run_graph(graph, resume=frame, approval_broker=broker)

    assert result["status"] == "completed"
    loop = result["outputs"]["loop-1"]
    assert loop["exitReason"] == "completed"
    assert loop["index"] == 3 and loop["iterations"] == 3
    results = loop["results"]
    assert len(results) == 3
    # 续跑那一轮用帧内原 token，后续每项各自新登记（token 互不相同）。
    assert [item["token"] for item in results] == [
        "tok-loop-0", results[1]["token"], results[2]["token"],
    ]
    assert len({item["token"] for item in results}) == 3
    # 摘要按当轮 item 渲染，不是第一项的重放。
    assert [item["summary"] for item in results] == [
        "第 1 单审批", "第 2 单审批", "第 3 单审批",
    ]
    assert "tool-done" in result["outputs"]
    assert broker.list_pending() == []


def test_resume_inside_foreach_body_midway_consumes_frame_token_once():
    """U1002：挂起发生在第 2 项（outputs 里已有上一轮的 human-1 产出）时同样只消费一次。

    这一条锁住"消费判定按运行级计数、不按 outputs 里有无该节点"——挂起在第 2 项时
    outputs 已含 human-1，若按 outputs 判定就会放弃帧内 token、另登记一条新审批，
    帧内那条 pending 再也无人解决。
    """
    graph = _loop_approval_graph()
    orders = [{"id": 1}, {"id": 2}, {"id": 3}]
    round0 = {"mode": "human_approval", "decision": "approved", "target": "tool-approve",
              "token": "tok-round-0", "summary": "第 1 单审批", "approver": "主管",
              "resolvedBy": "human", "comment": ""}
    broker = ApprovalBroker()
    broker.restore(
        token="tok-loop-1", node_id="human-1", graph_id="adhoc",
        summary="第 2 单审批", approver="主管", remaining_seconds=300,
    )
    frame = _resume_frame(
        graph, token="tok-loop-1", node_id="human-1", kind="approval",
        orders=orders,
        outputs={
            "loop-1": _foreach_state(orders, 1, [round0], "human-1"),
            "human-1": round0,
        },
    )

    result = run_graph(graph, resume=frame, approval_broker=broker)

    loop = result["outputs"]["loop-1"]
    assert loop["exitReason"] == "completed"
    assert loop["index"] == 3
    assert [item["token"] for item in loop["results"]] == [
        "tok-round-0", "tok-loop-1", loop["results"][2]["token"],
    ]
    assert loop["results"][1]["summary"] == "第 2 单审批"
    assert loop["results"][2]["summary"] == "第 3 单审批"


def test_resume_inside_foreach_body_rewait_each_item(monkeypatch):
    """U1003：挂起点在循环体内时，后续每项按完整时长重新等待（不是复用帧内剩余时长）。

    修复前：帧内 token 与 deadline 被每一轮复用，第二项起 remaining 已归零、且不再写
    新帧——等待被静默跳过。
    """
    slept: list[float] = []
    monkeypatch.setattr("atlas.graph.loader.time.sleep", lambda seconds: slept.append(seconds))

    graph = _loop_wait_graph()
    orders = [{"id": 1}, {"id": 2}, {"id": 3}]
    frame = _resume_frame(
        graph, token="tok-wait-0", node_id="wait-1", kind="wait",
        orders=orders,
        outputs={"loop-1": _foreach_state(orders, 0, [], "wait-1")},
        timeout=2,
    )

    result = run_graph(graph, resume=frame)

    assert result["status"] == "completed"
    assert result["outputs"]["loop-1"]["exitReason"] == "completed"
    assert len(slept) == 3
    assert slept[0] <= 2  # 续跑那一轮只睡帧内剩余，不满额重计
    assert slept[1:] == [3, 3]  # 后续每项按节点配置的完整时长等待
    assert "tool-done" in result["outputs"]


# --- U1004–U1005：挂起点在循环体内的续跑，另两种形态（探测无缺陷，转守护）---
def _while_approval_graph():
    """while 循环 + 体内审批：游标是 iterations，每轮重入重估 continueExpression。"""
    return parse_graph(
        {
            "version": 1,
            "variables": [{"name": "n", "type": "number", "required": True}],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "条件循环",
                 "config": {"mode": "while",
                            "continueExpression": "{{loop-1.iterations}} < {{global.n}}",
                            "maxIterations": 10,
                            "bodyTarget": "human-1", "exitTarget": "tool-done"}},
                {"id": "human-1", "type": "human_approval", "name": "审批",
                 "config": {"summary": "第 {{loop-1.index}} 轮审批", "approver": "主管",
                            "timeoutSeconds": 300, "onTimeout": "reject",
                            "approvedTarget": "tool-ok",
                            "rejectedTarget": "tool-no"}},
                {"id": "tool-ok", "type": "tool_call", "name": "通过",
                 "config": {"tool": "op-ok"}},
                {"id": "tool-no", "type": "tool_call", "name": "拒绝",
                 "config": {"tool": "op-no"}},
                {"id": "tool-done", "type": "tool_call", "name": "收尾",
                 "config": {"tool": "op-done"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "human-1"},
                {"id": "e3", "source": "loop-1", "target": "tool-done"},
                {"id": "e4", "source": "human-1", "target": "tool-ok"},
                {"id": "e5", "source": "human-1", "target": "tool-no"},
                {"id": "e6", "source": "tool-ok", "target": "loop-1"},
                {"id": "e7", "source": "tool-no", "target": "loop-1"},
            ],
        }
    )


def _skip_in_body_graph():
    """foreach 体：审批 → condition（跳过本轮的分支直连 loop）→ 工具 → 回 loop。

    collectTarget 是工具节点而非审批节点，故续跑后每轮的聚合值各自独立。
    """
    return parse_graph(
        {
            "version": 1,
            "variables": [{"name": "orders", "type": "array", "required": True}],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "manual"}},
                {"id": "loop-1", "type": "loop", "name": "逐单处理",
                 "config": {"mode": "foreach",
                            "itemsExpression": "{{global.orders}}",
                            "bodyTarget": "human-1", "exitTarget": "tool-done",
                            "collectTarget": "tool-ok"}},
                {"id": "human-1", "type": "human_approval", "name": "审批",
                 "config": {"summary": "第 {{loop-1.item.id}} 单审批", "approver": "主管",
                            "timeoutSeconds": 300, "onTimeout": "reject",
                            "approvedTarget": "cond-1",
                            "rejectedTarget": "tool-no"}},
                {"id": "cond-1", "type": "condition", "name": "跳过判定",
                 "config": {"branches": [{"label": "跳过",
                                          "expression": "{{loop-1.item.id}} == 2",
                                          "target": "loop-1"}],
                            "defaultTarget": "tool-ok"}},
                {"id": "tool-ok", "type": "tool_call", "name": "处理",
                 "config": {"tool": "op-ok",
                            "params": "id={{loop-1.item.id}}"}},
                {"id": "tool-no", "type": "tool_call", "name": "拒绝",
                 "config": {"tool": "op-no"}},
                {"id": "tool-done", "type": "tool_call", "name": "收尾",
                 "config": {"tool": "op-done"}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "loop-1"},
                {"id": "e2", "source": "loop-1", "target": "human-1"},
                {"id": "e3", "source": "loop-1", "target": "tool-done"},
                {"id": "e4", "source": "human-1", "target": "cond-1"},
                {"id": "e5", "source": "human-1", "target": "tool-no"},
                {"id": "e6", "source": "cond-1", "target": "loop-1"},
                {"id": "e7", "source": "cond-1", "target": "tool-ok"},
                {"id": "e8", "source": "tool-ok", "target": "loop-1"},
                {"id": "e9", "source": "tool-no", "target": "loop-1"},
            ],
        }
    )


def _approval_rounds(result):
    return sum(1 for line in result["trace"] if ": approved (" in line)


def _probe_frame(graph, *, token, node_id, inputs, outputs):
    """通用挂起帧：inputs/outputs 由调用方给全（while 与 foreach 的载荷不同）。"""
    return build_frame(
        token=token,
        run_id="",
        node_id=node_id,
        kind="approval",
        deadline_at=deadline_iso(300),
        graph_snapshot=graph.model_dump(),
        resume_state={"graph_id": "adhoc", "inputs": inputs, "outputs": outputs},
        summary="第 1 单审批",
        approver="主管",
    )


def test_resume_inside_while_body_continues_from_suspended_round():
    """U1004：while 循环体内的挂起点续跑后，游标从挂起轮继续，不从头重跑。

    帧内 `iterations` 预填 4、`n=5`：续跑只应剩第 4/5 两轮（审批两行）；若循环从零
    重跑则是五轮五行。`iterations` 终值两条路径相同（都由 n 决定），故判别式只能是
    循环体的实际执行轮数。尾图入口是挂起节点本身，故挂起轮以帧内 token 复跑一次，
    其后每轮重新登记（docs/04 §5.3 四条规则之二、之三）。
    """
    graph = _while_approval_graph()
    broker = ApprovalBroker()
    broker.restore(
        token="tok-w0", node_id="human-1", graph_id="adhoc",
        summary="第 4 轮审批", approver="主管", remaining_seconds=300,
    )
    frame = _probe_frame(
        graph, token="tok-w0", node_id="human-1",
        inputs={"n": 5, "approvals": {"human-1": "approved"}},
        outputs={
            "loop-1": {"mode": "while", "iterations": 4, "index": 4,
                       "target": "human-1", "exitReason": None,
                       "expression_errors": []},
        },
    )

    result = run_graph(graph, resume=frame, approval_broker=broker)

    loop = result["outputs"]["loop-1"]
    assert result["status"] == "completed"
    assert loop["exitReason"] == "condition_false"
    assert loop["iterations"] == 5 and loop["index"] == 5
    assert _approval_rounds(result) == 2
    assert "tool-done" in result["outputs"]
    assert broker.list_pending() == []


def test_resume_inside_foreach_body_keeps_skip_gate_working():
    """U1005：续跑尾图里 skip gate 照常推进游标且不聚合跳过轮（打包 Z 语义不因续跑退化）。

    帧内游标预填到第 2 项（`id=2`，正是跳过分支命中的那一项）：续跑只应剩第 2/3 两轮
    （审批两行；若从零重跑则三行），第 2 项被跳过、不聚合、游标照常推进。跳过轮在尾图
    里同样要「index+1、换 item、不读 collectTarget」，若 gate 在尾图失效（例如被当成
    普通回边），第 2 项会被误聚合或游标停滞。
    """
    graph = _skip_in_body_graph()
    orders = [{"id": 1}, {"id": 2}, {"id": 3}]
    broker = ApprovalBroker()
    broker.restore(
        token="tok-s1", node_id="human-1", graph_id="adhoc",
        summary="第 2 单审批", approver="主管", remaining_seconds=300,
    )
    frame = _probe_frame(
        graph, token="tok-s1", node_id="human-1",
        inputs={"orders": orders, "approvals": {"human-1": "approved"}},
        outputs={"loop-1": _foreach_state(
            orders, 1, [{"params_rendered": "id=1"}], "human-1")},
    )

    result = run_graph(graph, resume=frame, approval_broker=broker)

    loop = result["outputs"]["loop-1"]
    assert loop["exitReason"] == "completed"
    assert loop["index"] == 3
    assert _approval_rounds(result) == 2
    # 第 2 项被跳过：不聚合、不执行处理工具，游标照常推进到末项。
    assert [item["params_rendered"] for item in loop["results"]] == ["id=1", "id=3"]
    assert any("skip item" in line for line in result["trace"])
    assert "tool-done" in result["outputs"]
    assert broker.list_pending() == []
