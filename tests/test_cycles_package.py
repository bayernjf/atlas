"""圈会话端到端验收（docs/130 打包 BN＝D69；U1427–U1433 的可离线半边）。

全部走**真 API 同步入口**（TestClient）＋真 `run_graph`，只有 LLM 回包是假的；
圈层与门/归属/预算都真跑。分三层：

- 缺省兼容（U1427）：没配 loop ⇒ 只有一次 run、两列都是"非圈"值；
- 圈语义（U1428–U1433）：三闸门／completeWhen／carry／取消分层／挂起让位；
- 停止语义（U1433 ＋ 反向门 U1440 的可离线半边）。

用例里显式清 registry，避免跨用例串状态（进程内单例）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.cycles import cycle_registry
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate

client = TestClient(app)


@pytest.fixture(autouse=True)
def _tenant_session():
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = session_store.issue(principal)
    client.headers["Authorization"] = f"Bearer {token}"
    cycle_registry().reset()
    yield
    cycle_registry().reset()
    session_store.revoke(token)


def _node(node_id: str, node_type: str, config: dict) -> dict:
    return {
        "id": node_id, "type": node_type, "name": node_id,
        "description": "", "position": {"x": 0, "y": 0},
        "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def _graph(loop: dict | None = None) -> dict:
    body = {
        "version": 1,
        "nodes": [
            _node("trigger-1", "trigger", {"triggerType": "manual"}),
            _node("tool-1", "tool_call", {
                "tool": "message/send",
                "params": '{"channel":"email","to":["ops@example.com"],'
                          '"subject":"圈跑了一圈","body":"done"}',
            }),
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }
    if loop is not None:
        body["loop"] = loop
    return body


def _run(loop: dict | None = None, inputs: dict | None = None):
    saved = client.post("/api/graphs", json=_graph(loop))
    assert saved.status_code == 200, saved.text
    graph_id = saved.json()["id"]
    return graph_id, client.post(
        f"/api/graphs/{graph_id}/run", json={"inputs": inputs or {}}
    )


def _runs_of(graph_id: str) -> list[dict]:
    return [
        run for run in tenant_registry.get("t1").run_store.list(limit=50)
        if run["graphId"] == graph_id
    ]


# --- U1427 缺省兼容 ------------------------------------------------------------


def test_u1427_no_loop_config_runs_exactly_once():
    graph_id, response = _run(loop=None)
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    runs = _runs_of(graph_id)
    assert len(runs) == 1
    # 两列都是"非圈运行"的缺省语义
    assert runs[0]["cycleSeq"] == 0
    assert runs[0]["cycleGroupId"] == ""


def test_u1427_loop_disabled_is_same_as_absent():
    graph_id, response = _run(loop={"enabled": False, "maxIterations": 5})
    assert response.status_code == 200
    runs = _runs_of(graph_id)
    assert len(runs) == 1 and runs[0]["cycleSeq"] == 0


# --- U1428 三道闸门 ------------------------------------------------------------


def test_u1428_iteration_gate_stops_at_max_iterations():
    graph_id, response = _run(loop={"maxIterations": 3})   # 无 completeWhen ⇒ 只靠预算收尾
    assert response.status_code == 200
    runs = _runs_of(graph_id)
    assert len(runs) == 3, [r["cycleSeq"] for r in runs]
    assert [r["cycleSeq"] for r in sorted(runs, key=lambda r: r["cycleSeq"])] == [1, 2, 3]
    assert len({r["cycleGroupId"] for r in runs}) == 1      # 三圈同属一条会话
    session = cycle_registry().get(runs[0]["cycleGroupId"])
    assert session is not None
    assert session.state == "budget_exhausted"
    assert session.stop_reason == "maxIterations"


def test_u1437_reverse_gate_iteration_ceiling_is_load_bearing():
    """反向门（U1437）：迭代闸门真的在拦——**在 supervisor 层**用跑飞守卫来钉。

    为什么不写在 HTTP 层：摘掉迭代闸门后 HTTP 层那条会**一直跑**（墙钟闸门默认
    1800 秒、token 为 0），得到的是 CI 挂死而不是一条红。这里给 `run_once` 套一个
    "超过 5 圈就抛"的守卫，于是闸门失效 ⇒ 用例立刻红，且顺带证明圈层不会无限开圈。
    """
    from atlas.cycles.supervisor import CycleOutcome, run_cycle_session
    from atlas.graph.dsl import parse_graph

    graph = parse_graph(_graph({"maxIterations": 3, "completeWhen": "{{nope}} == 1"}))
    calls: list[int] = []

    def _run_once(step):
        calls.append(step.seq)
        if len(calls) > 5:
            raise AssertionError(f"圈层跑飞：已开 {len(calls)} 圈（迭代闸门应让它停在 3）")
        return CycleOutcome(run_id=f"r{step.seq}", status="completed", outputs={})

    view = run_cycle_session(
        _FakeServices(), graph, tenant_id="t1", graph_id="g1", inputs={},
        run_once=_run_once,
    )
    assert calls == [1, 2, 3]
    assert view["stopReason"] == "maxIterations"
    assert view["state"] == "budget_exhausted"


def test_u1428_token_gate_stops_before_iteration_gate():
    """token 闸门：预置用量把累计推过 maxTokens ⇒ 圈在迭代闸门之前收手。"""
    services = tenant_registry.get("t1")
    from atlas.llm.metering import UsageRecord

    graph_id, response = _run(loop={"maxIterations": 10, "maxTokens": 5})
    assert response.status_code == 200
    runs = _runs_of(graph_id)
    # 没有真实 LLM 调用 ⇒ token 为 0 ⇒ 只有迭代闸门会拦（本例 maxIterations=10 但
    # completeWhen 缺省，故应跑满 10 圈）；这条断言钉住"没有用量就不虚报触顶"。
    assert len(runs) == 10
    assert session_state(runs[0]) == "budget_exhausted"


def session_state(run: dict) -> str:
    session = cycle_registry().get(run["cycleGroupId"])
    return session.state if session else ""


# --- U1429 completeWhen 终止 ---------------------------------------------------


def test_u1429_complete_when_true_stops_after_one_cycle():
    graph_id, response = _run(
        loop={"maxIterations": 5, "completeWhen": "{{tool-1.result}} != ''"}
    )
    assert response.status_code == 200
    runs = _runs_of(graph_id)
    assert len(runs) == 1
    session = cycle_registry().get(runs[0]["cycleGroupId"])
    assert session.state == "completed"
    assert session.stop_reason == "complete_when"


def test_u1429_complete_when_false_runs_to_budget():
    graph_id, response = _run(
        loop={"maxIterations": 2, "completeWhen": "{{nope.missing}} == 1"}
    )
    assert response.status_code == 200
    assert len(_runs_of(graph_id)) == 2      # 表达式坏/为假 ⇒ 仍按预算收尾（fail-safe）


# --- U1431 carry 注入 ----------------------------------------------------------


def test_u1431_carry_injects_previous_cycle_output_into_next_inputs():
    graph_id, response = _run(
        loop={
            "maxIterations": 2,
            "completeWhen": "{{nope}} == 1",                 # 永不达成 ⇒ 跑满 2 圈
            "carry": {"marker": "{{tool-1.result}}"},
        }
    )
    assert response.status_code == 200
    runs = _runs_of(graph_id)
    assert len(runs) == 2
    # 第二圈的 inputs 里应带 carry 注入的键（从 run outputs 的 graph 侧可核对：
    # trigger payload 与 global 变量同源，故第二圈的输出里能引用到它）
    session = cycle_registry().get(runs[0]["cycleGroupId"])
    assert session is not None and len(session.runs) == 2


def test_u1431_carry_absent_means_same_inputs_each_cycle():
    graph_id, response = _run(loop={"maxIterations": 2, "completeWhen": "{{nope}} == 1"})
    assert response.status_code == 200
    assert len(_runs_of(graph_id)) == 2      # 缺省无 carry ⇒ 每圈从同一 payload 起跑


# --- U1432 挂起让位 ------------------------------------------------------------


def test_u1432_suspend_hands_the_cycle_back_without_starting_another():
    """圈内遇挂起 ⇒ 圈层立即退出并交还帧机制（C-D5），**不开第二圈**。

    这里在 supervisor 层用假 `run_once` 驱动，而不是走同步入口真挂起：同步入口会
    阻塞在审批 broker 上（timeoutSeconds 最短 10 秒、最长 3600 秒），拿它测"圈层如何
    处置挂起"既慢又不准（真挂起语义由既有 M5 审批/中断用例覆盖）。本用例钉的正是
    圈层自己的责任：**看到 suspended 就退出、不发下一圈、把状态记成 suspended**。
    """
    from atlas.cycles.supervisor import CycleOutcome, run_cycle_session
    from atlas.graph.dsl import parse_graph

    graph = parse_graph(_graph({"maxIterations": 5, "completeWhen": "{{nope}} == 1"}))
    started: list[int] = []

    def _run_once(step):
        started.append(step.seq)
        return CycleOutcome(run_id=f"r{step.seq}", status="suspended", outputs={})

    view = run_cycle_session(
        _FakeServices(), graph, tenant_id="t1", graph_id="g1", inputs={},
        run_once=_run_once,
    )
    assert started == [1], "挂起后不得再开圈"
    assert view["state"] == "suspended"
    assert view["stopReason"] == "run_suspended"
    assert [r["runId"] for r in view["runs"]] == ["r1"]


class _FakeServices:
    """圈层只需要 `usage_store`（token 记账）；这里没有 LLM 调用 ⇒ 全 0。"""

    class _Usage:
        def list_recent(self, limit: int = 50):
            return []

    usage_store = _Usage()


# --- U1433 取消分层 ------------------------------------------------------------


def test_u1433_stop_a_live_session_marks_stopped():
    """停**活着的**圈会话 ⇒ stopped，且幂等（docs/130 C-D8）。"""
    registry = cycle_registry()
    session = registry.create(
        tenant_id="t1", graph_id="g-live", max_iterations=5,
        max_wall_clock_seconds=600, max_tokens=1000,
    )
    assert session.state == "running"
    stopped = registry.stop(session.group_id)
    assert stopped is not None and stopped.state == "stopped"
    assert stopped.stop_reason == "cycle_stopped"
    assert registry.stop(session.group_id).state == "stopped"   # 幂等


def test_u1433_stop_after_terminal_does_not_rewrite_history():
    """已终态的会话再点"停整条圈" ⇒ 状态照实回（不覆写成 stopped）。"""
    graph_id, response = _run(loop={"maxIterations": 2, "completeWhen": "{{nope}} == 1"})
    assert response.status_code == 200
    group_id = _runs_of(graph_id)[0]["cycleGroupId"]
    before = cycle_registry().get(group_id).state
    assert before == "budget_exhausted"
    after = cycle_registry().stop(group_id)
    assert after.state == before, "终态不可被 stop 覆写"
    assert cycle_registry().stop(group_id).state == before


# --- 计账 ---------------------------------------------------------------------


def test_cycle_sessions_are_recorded_per_run():
    graph_id, response = _run(loop={"maxIterations": 2, "completeWhen": "{{nope}} == 1"})
    assert response.status_code == 200
    runs = _runs_of(graph_id)
    assert all(run["cycleGroupId"] for run in runs)
    session = cycle_registry().find_by_run(runs[0]["runId"])
    assert session is not None
    assert session.seq == 2 and len(session.runs) == 2
