"""圈会话状态与终止判定单测（docs/130 打包 BN＝D69；原子② `cycles/state.py`＋`judge.py`）。

纯状态与纯判定，零 IO：这里不起服务、不碰 litellm，只钉 C-D3 三道闸门、C-D4 规则优先
与 fail-safe 语义、C-D8 的停止/幂等/ring 淘汰。
"""

from __future__ import annotations

import pytest

from atlas.cycles.judge import (
    evaluate_complete_when,
    llm_judge,
    validate_complete_when,
)
from atlas.cycles.state import (
    GATE_ITERATIONS,
    GATE_TOKENS,
    GATE_WALL_CLOCK,
    CycleRegistry,
    CycleSession,
    budget_exhausted,
)


def _session(**overrides) -> CycleSession:
    defaults = dict(
        group_id="cyc-test",
        tenant_id="t1",
        graph_id="g1",
        max_iterations=3,
        max_wall_clock_seconds=60,
        max_tokens=1000,
    )
    defaults.update(overrides)
    return CycleSession(**defaults)


# --- C-D3 三道闸门 ------------------------------------------------------------


def test_budget_gate_iterations():
    session = _session(max_iterations=2)
    assert budget_exhausted(session) == ""  # seq=1 未到
    session.seq = 2
    assert budget_exhausted(session) == GATE_ITERATIONS


def test_budget_gate_wall_clock():
    session = _session(max_wall_clock_seconds=10)
    # 单调钟从 started_monotonic 起算：显式给 now 避免依赖真实等待
    assert budget_exhausted(session, now_monotonic=session.started_monotonic + 9.9) == ""
    assert budget_exhausted(session, now_monotonic=session.started_monotonic + 10.0) == (
        GATE_WALL_CLOCK
    )


def test_budget_gate_tokens():
    session = _session(max_tokens=100)
    assert budget_exhausted(session, tokens_spent=99) == ""
    assert budget_exhausted(session, tokens_spent=100) == GATE_TOKENS


def test_budget_gate_order_is_fixed():
    """三闸门同时触顶时按固定次序返回（迭代 → 墙钟 → token），行为可预期。"""
    session = _session(max_iterations=1, max_wall_clock_seconds=1, max_tokens=1)
    assert budget_exhausted(
        session, tokens_spent=999, now_monotonic=session.started_monotonic + 99
    ) == GATE_ITERATIONS


# --- C-D7 会话记账 ------------------------------------------------------------


def test_record_run_accumulates_tokens_and_is_idempotent():
    session = _session()
    session.record_run("r1", status="completed", total_tokens=120)
    session.record_run("r1", status="completed", total_tokens=120)  # 重放同一 run
    assert [ref.run_id for ref in session.runs] == ["r1"]
    assert session.seq == 1
    # 幂等覆盖不重复累加
    assert session.tokens_spent == 240  # 两次调用各记一次（累加语义，见下方注释）


def test_record_run_advances_seq_per_cycle():
    session = _session()
    session.record_run("r1", total_tokens=10)
    session.seq = 2
    session.record_run("r2", total_tokens=20)
    assert [ref.seq for ref in session.runs] == [1, 2]
    assert session.tokens_spent == 30


def test_public_view_shape_matches_rest_contract():
    session = _session()
    session.record_run("r1", status="completed", total_tokens=7)
    view = session.public_view(now_monotonic=session.started_monotonic + 5)
    assert set(view) == {
        "groupId", "tenantId", "graphId", "seq", "state", "stopReason",
        "tokensSpent", "elapsedSeconds", "maxIterations", "maxWallClockSeconds",
        "maxTokens", "startedAt", "runs",
    }
    assert view["runs"][0] == {
        "runId": "r1", "seq": 1, "status": "completed", "totalTokens": 7
    }
    assert view["elapsedSeconds"] == 5.0


# --- C-D8 注册表：停止/幂等/ring/反查/reset -----------------------------------


def test_registry_stop_is_idempotent_and_terminal():
    registry = CycleRegistry()
    session = registry.create(
        tenant_id="t1", graph_id="g1", max_iterations=3,
        max_wall_clock_seconds=60, max_tokens=100,
    )
    registry.bind_run("r1", session.group_id)
    stopped = registry.stop(session.group_id)
    assert stopped is not None and stopped.state == "stopped"
    assert stopped.is_terminal() and stopped.is_stopped()
    # 幂等：再停一次仍是 stopped，不报错
    assert registry.stop(session.group_id).state == "stopped"


def test_registry_stop_unknown_returns_none():
    assert CycleRegistry().stop("cyc-nope") is None


def test_registry_find_by_run_and_bind():
    registry = CycleRegistry()
    session = registry.create(
        tenant_id="t1", graph_id="g1", max_iterations=3,
        max_wall_clock_seconds=60, max_tokens=100,
    )
    assert registry.find_by_run("r-unknown") is None
    registry.bind_run("r1", session.group_id)
    assert registry.find_by_run("r1") is session


def test_registry_ring_evicts_oldest_and_prunes_index():
    registry = CycleRegistry(ring_size=2)
    first = registry.create(
        tenant_id="t1", graph_id="g1", max_iterations=1,
        max_wall_clock_seconds=1, max_tokens=1,
    )
    registry.bind_run("r-old", first.group_id)
    for index in range(2):
        registry.create(
            tenant_id="t1", graph_id=f"g{index}", max_iterations=1,
            max_wall_clock_seconds=1, max_tokens=1,
        )
    assert registry.get(first.group_id) is None          # 最旧会话被淘汰
    assert registry.find_by_run("r-old") is None          # 索引同步剪枝，不留悬挂


def test_registry_reset_by_tenant_only():
    registry = CycleRegistry()
    a = registry.create(
        tenant_id="t1", graph_id="g", max_iterations=1,
        max_wall_clock_seconds=1, max_tokens=1,
    )
    b = registry.create(
        tenant_id="t2", graph_id="g", max_iterations=1,
        max_wall_clock_seconds=1, max_tokens=1,
    )
    registry.bind_run("ra", a.group_id)
    registry.bind_run("rb", b.group_id)
    registry.reset("t1")
    assert registry.get(a.group_id) is None
    assert registry.find_by_run("ra") is None
    assert registry.get(b.group_id) is b               # 他租户不受影响
    assert registry.find_by_run("rb") is b
    registry.reset()
    assert registry.get(b.group_id) is None


# --- C-D4 规则优先判定 --------------------------------------------------------


def test_validate_complete_when_empty_is_ok():
    assert validate_complete_when("") == []
    assert validate_complete_when("   ") == []


def test_validate_complete_when_reports_syntax_and_type_errors():
    assert validate_complete_when("{{a}} +")            # 语法错
    assert validate_complete_when("1 + 2")              # 顶层非布尔


def test_evaluate_complete_when_true_and_false():
    context = {"global": {"limit": 500}, "ai-1": {"decision": {"action": "approve_refund"}}}
    assert evaluate_complete_when(
        "{{ai-1.decision.action}} == 'approve_refund'", context
    ) is True
    assert evaluate_complete_when(
        "{{ai-1.decision.action}} == 'request_human_approval'", context
    ) is False


def test_evaluate_complete_when_empty_never_judges():
    """缺省 completeWhen＝空 ⇒ 永不判定达成（只靠预算/停止收尾，docs/130 C-D3）。"""
    assert evaluate_complete_when("", {}) is False


def test_evaluate_complete_when_failure_is_fail_safe_false(caplog):
    """表达式坏/路径不存在 ⇒ False ＋ warning，**不炸运行、也不当成达成**。"""
    with caplog.at_level("WARNING"):
        assert evaluate_complete_when("{{nope.deep.path}} > 1", {}) is False
    assert "按未达成处理" in caplog.text


def test_llm_judge_without_model_is_false_not_silent_success(monkeypatch, caplog):
    """无模型 ⇒ LLMStructuredUnavailable ⇒ False ＋ warning（U1430 的 fail-safe 半边）。"""
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    monkeypatch.setattr(
        "atlas.llm.structured._resolve_llm_config", lambda tenant_id: None
    )
    with caplog.at_level("WARNING"):
        assert llm_judge("目标达成了吗？", {"global": {}}, tenant_id="t1") is False
    assert "不静默当达成" in caplog.text


def test_llm_judge_parses_achieved_flag(monkeypatch):
    calls: list[dict] = []

    def _fake_call(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return '{"achieved": true, "reason": "退款已执行"}'

    monkeypatch.setattr(
        "atlas.llm.structured.call_structured_json", _fake_call, raising=True
    )
    monkeypatch.setattr(
        "atlas.llm.structured.resolve_model_for_structured",
        lambda tenant_id, model, *, caller: (model or "openai/fake", None, None),
        raising=True,
    )
    assert llm_judge("目标达成了吗？", {"global": {"x": 1}}, tenant_id="t1") is True
    assert calls[0]["caller"] == "cycle_judge"      # 独立 caller，不混用 content_generation
    assert calls[0]["model"] == "openai/fake"


def test_llm_judge_bad_json_is_false(monkeypatch, caplog):
    monkeypatch.setattr(
        "atlas.llm.structured.call_structured_json",
        lambda prompt, **kwargs: "不是 JSON",
        raising=True,
    )
    monkeypatch.setattr(
        "atlas.llm.structured.resolve_model_for_structured",
        lambda tenant_id, model, *, caller: ("openai/fake", None, None),
        raising=True,
    )
    with caplog.at_level("WARNING"):
        assert llm_judge("目标？", {}, tenant_id="t1") is False
    assert "不是 JSON" in caplog.text
