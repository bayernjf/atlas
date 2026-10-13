"""圈层判定的二段与其反向门（docs/130 打包 BN＝D69；U1430／U1439／U1440）。

- U1430：`judgeMode="llm"` 显式开启 ⇒ 走结构化客户端、成本进 AU 的账（caller=cycle_judge）；
  无模型 ⇒ 折 False ＋ warning（不静默当达成）。
- U1439（反向门）：carry 声明写错键路径 ⇒ 下一圈 inputs **不含**该键（不是整包透传）。
- U1440（反向门）：摘掉 supervisor 的 stopped 检查 ⇒ 已叫停的会话仍会被开新圈（必须红）。
"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.cycles.supervisor import CycleOutcome, run_cycle_session, session_for_run
from atlas.graph.dsl import parse_graph


class _Usage:
    """够用的用量档：只实现圈层用到的两个方法（record/list_recent）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, rec) -> None:
        self.rows.append(rec.public_view())

    def list_recent(self, limit: int = 50):
        return list(self.rows)


class _Services:
    def __init__(self) -> None:
        self.usage_store = _Usage()


def _node(node_id: str, node_type: str, config: dict) -> dict:
    return {
        "id": node_id, "type": node_type, "name": node_id,
        "description": "", "position": {"x": 0, "y": 0}, "config": config,
        "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
    }


def _graph(loop: dict) -> dict:
    return {
        "version": 1,
        "nodes": [
            _node("trigger-1", "trigger", {"triggerType": "manual"}),
            _node("tool-1", "tool_call", {
                "tool": "message/send",
                "params": '{"channel":"email","to":["ops@example.com"],'
                          '"subject":"圈","body":"b"}',
            }),
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
        "loop": loop,
    }


def _install_litellm(monkeypatch, content: str, calls: list | None = None):
    def _completion(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        payload = {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
        }

        class _Resp:
            def __getitem__(self, key):
                return payload[key]

            @property
            def usage(self):
                return payload["usage"]

        return _Resp()

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)


# --- U1430 LLM 二段 ------------------------------------------------------------


def test_u1430_llm_judge_ends_the_cycle_and_bills_the_au_ledger(monkeypatch):
    """判定那一次 LLM 调用也必须进 AU 的账（caller=cycle_judge）。

    这条用例抓到过第二个真缺陷：判定发生在 `run_graph` 返回**之后**，此时运行入口设在
    run 体内的 usage 上下文已退出，圈判定成本曾整段漏记——而 AU 建立的按 caller 归因
    就是为了这种"钱花在哪要说得清"。
    """
    from atlas.cycles import cycle_registry

    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    calls: list[dict] = []
    _install_litellm(monkeypatch, '{"achieved": true, "reason": "退款已执行"}', calls)
    services = _Services()
    graph = parse_graph(_graph({
        "maxIterations": 5,
        "completeWhen": "{{tool-1.result}} == 'never'",     # 规则永远说"没达成"
        "judgeMode": "llm", "judgePrompt": "目标达成了吗？",
    }))
    steps: list[int] = []

    def _run_once(step):
        steps.append(step.seq)
        return CycleOutcome(run_id=f"r{step.seq}", status="completed", outputs={})

    cycle_registry().reset()
    try:
        view = run_cycle_session(
            services, graph, tenant_id="t1", graph_id="g1", inputs={},
            run_once=_run_once,
        )
        assert steps == [1], "LLM 判达成 ⇒ 只跑一圈"
        assert view["state"] == "completed" and view["stopReason"] == "complete_when"
        assert [row["caller"] for row in services.usage_store.rows] == ["cycle_judge"]
        assert services.usage_store.rows[0]["totalTokens"] == 26
        assert services.usage_store.rows[0]["runId"] == "r1"
        assert calls and calls[0]["model"] == "openai/fake"
    finally:
        cycle_registry().reset()


def test_u1430_llm_judge_not_reached_when_rule_already_says_done(monkeypatch):
    """规则判达成 ⇒ 不再问模型（省一次调用，也省一次钱）。"""
    from atlas.cycles import cycle_registry
    from atlas.llm.structured import call_structured_json

    seen: list[str] = []
    monkeypatch.setattr(
        "atlas.cycles.supervisor.llm_judge",
        lambda *a, **k: seen.append("llm") or True,
        raising=True,
    )
    graph = parse_graph(_graph({
        "maxIterations": 5,
        "completeWhen": "1 == 1",
        "judgeMode": "llm", "judgePrompt": "问吧",
    }))
    cycle_registry().reset()
    try:
        view = run_cycle_session(
            _Services(), graph, tenant_id="t1", graph_id="g1", inputs={},
            run_once=lambda step: CycleOutcome(
                run_id=f"r{step.seq}", status="completed", outputs={}
            ),
        )
        assert view["stopReason"] == "complete_when"
        assert seen == [], "规则已判达成，不该再调 LLM"
        assert call_structured_json is not None      # 仅避免未使用告警
    finally:
        cycle_registry().reset()


# --- U1439 carry 反向门 --------------------------------------------------------


def test_u1439_carry_with_a_bad_path_does_not_leak_a_whole_payload(monkeypatch):
    """carry 写错键路径 ⇒ 下一圈 inputs 里**没有**那个键。

    反向门针对的是"无脑整包透传"：如果实现改成把上一圈 outputs 全塞进下一圈 inputs，
    本例就会红（下一圈会拿到 tool-1 这个不该注入的键）。
    """
    from atlas.cycles import cycle_registry

    graph = parse_graph(_graph({
        "maxIterations": 2,
        "completeWhen": "{{nope}} == 1",
        "carry": {"marker": "{{no.such.path}}"},
    }))
    seen_inputs: list[dict] = []

    def _run_once(step):
        seen_inputs.append(dict(step.inputs))
        return CycleOutcome(
            run_id=f"r{step.seq}", status="completed",
            outputs={"tool-1": {"result": "ok"}},
        )

    cycle_registry().reset()
    try:
        run_cycle_session(
            _Services(), graph, tenant_id="t1", graph_id="g1",
            inputs={"order_id": "12345"}, run_once=_run_once,
        )
        assert len(seen_inputs) == 2
        assert "marker" not in seen_inputs[1], "解析不出就不该带这个键"
        # 且没有把上一圈 outputs 整包塞进去
        assert "tool-1" not in seen_inputs[1]
        assert seen_inputs[1]["order_id"] == "12345", "原始 inputs 必须保留"
    finally:
        cycle_registry().reset()


def test_carry_injects_resolved_value_into_next_cycle():
    from atlas.cycles import cycle_registry

    graph = parse_graph(_graph({
        "maxIterations": 2,
        "completeWhen": "{{nope}} == 1",
        "carry": {"marker": "{{tool-1.result}}"},
    }))
    seen_inputs: list[dict] = []

    def _run_once(step):
        seen_inputs.append(dict(step.inputs))
        return CycleOutcome(
            run_id=f"r{step.seq}", status="completed",
            outputs={"tool-1": {"result": "refunded"}},
        )

    cycle_registry().reset()
    try:
        run_cycle_session(
            _Services(), graph, tenant_id="t1", graph_id="g1", inputs={},
            run_once=_run_once,
        )
        assert seen_inputs[1]["marker"] == "refunded"
    finally:
        cycle_registry().reset()


# --- U1440 停止反向门 ----------------------------------------------------------


def test_u1440_stopped_session_must_not_start_another_cycle():
    """正向：叫停整条会话后再推进 ⇒ 不开新圈。

    **这条用例抓到过一个真缺陷**：supervisor 的循环里原本没有"整条圈已被叫停"的检查，
    于是 `stop` 端点只停得下一圈、人点了还在跑（反向门存在的意义就在这儿）。
    反向门（把 `session.is_stopped()` 那段摘掉）会让本例红——用例会看到第 2 圈被开出来。
    """
    from atlas.cycles import cycle_registry

    graph = parse_graph(_graph({"maxIterations": 5, "completeWhen": "{{nope}} == 1"}))
    started: list[int] = []

    def _run_once(step):
        started.append(step.seq)
        # 第一圈跑完就叫人停掉整条会话（模拟运营点"停整条圈"：此时会话已在 registry 里，
        # 只是 run→group 的绑定发生在 run_once 返回之后，所以按租户列会话来停）
        if step.seq == 1:
            sessions = cycle_registry().list_tenant("t1")
            assert len(sessions) == 1
            cycle_registry().stop(sessions[0].group_id)
        return CycleOutcome(run_id=f"r{step.seq}", status="completed", outputs={})

    cycle_registry().reset()
    try:
        view = run_cycle_session(
            _Services(), graph, tenant_id="t1", graph_id="g1", inputs={},
            run_once=_run_once,
        )
        assert started == [1], f"被叫停后不该再开圈，实得 {started}"
        assert view["state"] == "stopped"
    finally:
        cycle_registry().reset()


def test_stopped_session_is_terminal_and_viewable():
    from atlas.cycles import cycle_registry

    cycle_registry().reset()
    try:
        session = cycle_registry().create(
            tenant_id="t1", graph_id="g", max_iterations=3,
            max_wall_clock_seconds=60, max_tokens=100,
        )
        cycle_registry().bind_run("r1", session.group_id)
        cycle_registry().stop(session.group_id)
        found = session_for_run("r1")
        assert found is not None and found.state == "stopped"
        assert found.is_terminal() and found.is_stopped()
    finally:
        cycle_registry().reset()
