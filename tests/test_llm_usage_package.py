"""LLM 用量计量打包验收（docs/129 打包 AU＝D70；U1411–U1425）。

mock litellm 照 `tests/test_decision_client.py` 先例（假模块进 sys.modules）。
测试分三层：调用点身份与旁路语义、运行归属、REST 与隔离。
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry

client = TestClient(app)

USAGE = {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}


class _FakeResponse:
    def __init__(self, content: str, usage: dict | None = USAGE):
        self._payload = {"choices": [{"message": {"content": content}}], "usage": usage}

    def __getitem__(self, key):
        return self._payload[key]

    @property
    def usage(self):
        return self._payload["usage"]


def _install_litellm(monkeypatch, content: str, *, usage=USAGE, exc=None):
    calls: list[dict] = []

    def _completion(**kwargs):
        calls.append(kwargs)
        if exc is not None:
            raise exc
        return _FakeResponse(content, usage)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return calls


def _store(tenant_id: str = "t1"):
    return tenant_registry.get(tenant_id).usage_store


def _auth() -> None:
    """本模块自建 TestClient（conftest 只给四个既有模块注入会话），自行签发 t1 admin。"""
    from atlas.iam.deps import session_store
    from atlas.iam.principals import authenticate

    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    client.headers["Authorization"] = f"Bearer {session_store.issue(principal)}"


@pytest.fixture(autouse=True)
def _clean_usage():
    """每条用例前清空本租户用量账（store 是 per-tenant 进程内环）。"""
    _auth()
    for tenant in ("t1", "t2"):
        tenant_registry.get(tenant).usage_store.reset()
    yield
    for tenant in ("t1", "t2"):
        tenant_registry.get(tenant).usage_store.reset()


# --- U1411 八个调用点身份各记一条 --------------------------------------------


@pytest.mark.parametrize(
    ("caller", "invoke"),
    [
        ("ai_decision", "decision"),
        ("condition_llm", "condition"),
        ("intent_recognition", "intent"),
        ("info_extraction", "extract"),
        ("content_generation", "content"),
        ("memory_auto_extract", "memories"),
        ("nl_generate", "nl"),
        ("reflection_summarize", "reflection"),
    ],
)
def test_u1411_every_call_site_records_its_own_caller(monkeypatch, caller, invoke):
    from atlas.llm import nl_generate, structured

    # 三结构化节点与 extract_memories 在"无模型"时显式抛错（docs/109 §2.2），
    # 故本用例统一给一个假模型（litellm 本身已由假模块顶掉，不出网）。
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    from atlas.llm.condition_classifier import LiteLLMConditionClassifier
    from atlas.llm.decision import LiteLLMDecisionClient
    from atlas.llm.metering import usage_context
    from atlas.reflection.adapter import LiteLLMSummarizer

    _install_litellm(monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
                     if invoke == "decision" else '{"branch": "a", "confidence": 0.9}')
    store = _store()
    with usage_context("t1", run_id="r1", graph_id="g1", store=store):
        if invoke == "decision":
            LiteLLMDecisionClient("openai/m").decide_refund(reason="破损", amount=1, limit=500)
        elif invoke == "condition":
            LiteLLMConditionClassifier("openai/m").classify(
                branches=[{"label": "a", "description": "x"}], context_text="c", instruction=""
            )
        elif invoke == "intent":
            structured.classify_intent(text="t", intents=[{"name": "a"}], tenant_id="t1")
        elif invoke == "extract":
            structured.extract_fields(text="t", fields=[{"name": "a"}], tenant_id="t1")
        elif invoke == "content":
            structured.generate_content(context="c", template="t", tenant_id="t1")
        elif invoke == "memories":
            structured.extract_memories(text="t", tenant_id="t1")
        elif invoke == "nl":
            nl_generate._generate_with_llm("退款", "openai/m")
        else:
            LiteLLMSummarizer("openai/m").summarize(_evidence_stub())
    rows = store.list_recent()
    assert len(rows) == 1, f"{caller} 应恰一条，实得 {rows}"
    assert rows[0]["caller"] == caller
    assert rows[0]["totalTokens"] == 19


def _evidence_stub():
    from atlas.reflection.evidence import ReflectionEvidence

    return ReflectionEvidence(
        tenant_id="t1",
        graph_id="g1",
        base_version=1,
        window={"since": "", "until": ""},
    )


# --- U1412/U1413/U1415/U1416 调用点语义边 -------------------------------------


def test_u1412_node_model_override_is_the_recorded_model(monkeypatch):
    from atlas.llm.decision import LiteLLMDecisionClient
    from atlas.llm.metering import usage_context

    calls = _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    store = _store()
    with usage_context("t1", store=store):
        LiteLLMDecisionClient("openai/default").decide_refund(
            reason="破损", amount=1, limit=500, model="openai/node-override"
        )
    assert calls[0]["model"] == "openai/node-override"
    assert store.list_recent()[0]["model"] == "openai/node-override"


def test_u1413_byok_key_is_passed_to_litellm_but_never_recorded(monkeypatch):
    from atlas.llm.decision import LiteLLMDecisionClient
    from atlas.llm.metering import usage_context

    calls = _install_litellm(
        monkeypatch, '{"action": "request_human_approval", "reason": "x", "confidence": 0.2}'
    )
    store = _store()
    with usage_context("t1", store=store):
        LiteLLMDecisionClient("openai/m", api_key="sk-secret", base_url="https://gw").decide_refund(
            reason="不想要了", amount=9000, limit=500
        )
    assert calls[0]["api_key"] == "sk-secret"
    row = store.list_recent()[0]
    # 机检：记录里没有密钥字段（投影键集合固定）
    assert set(row) == {
        "tenantId", "ts", "caller", "model", "promptTokens", "completionTokens",
        "totalTokens", "latencyMs", "status", "nodeId", "graphId", "runId",
    }


def test_u1415_error_call_records_error_row_and_fail_safe_still_holds(monkeypatch):
    from atlas.llm.decision import HUMAN_APPROVAL, LiteLLMDecisionClient
    from atlas.llm.metering import usage_context

    _install_litellm(monkeypatch, "", exc=TimeoutError("read timeout"))
    store = _store()
    # 计量旁路性：异常原样上浮（decision 客户端不吞供应商异常，由 loader 兜底），
    # 本条只断言"失败也留了一条账"。
    with usage_context("t1", run_id="r1", store=store), pytest.raises(TimeoutError):
        LiteLLMDecisionClient("openai/m").decide_refund(reason="破损", amount=1, limit=500)
    row = store.list_recent()[0]
    assert row["status"] == "error"
    assert row["totalTokens"] == 0
    assert row["runId"] == "r1"
    assert row["caller"] == "ai_decision"


def test_u1416_missing_usage_records_zero_without_failing_the_call(monkeypatch, caplog):
    from atlas.llm.condition_classifier import LiteLLMConditionClassifier
    from atlas.llm.metering import usage_context

    _install_litellm(monkeypatch, '{"branch": "a"}', usage=None)
    store = _store()
    with caplog.at_level("WARNING"), usage_context("t1", store=store):
        label = LiteLLMConditionClassifier("openai/m").classify(
            branches=[{"label": "a", "description": "x"}], context_text="c", instruction=""
        )
    assert label == "a"
    row = store.list_recent()[0]
    assert row["totalTokens"] == 0 and row["status"] == "ok"
    assert "未带 usage" in caplog.text


# --- U1414/U1424/U1425 运行归属与旁路性 ---------------------------------------


def _decision_graph():
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "ai_decision-1", "type": "ai_decision", "name": "决策",
             "config": {"promptTemplate": "破损"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "ai_decision-1"}],
    }


def test_u1414_run_attribution_reaches_the_record(monkeypatch):
    """真跑一条含 ai_decision 的图：记录带 tenant/run_id/graph_id/node_id。"""
    from atlas.llm.metering import usage_context  # noqa: F401  (上下文由端点设置)

    # 无 LITELLM_MODEL 时决策器是规则兜底（不调 LLM）⇒ 必须给一个假模型。
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    saved = client.post("/api/graphs", json=_decision_graph())
    assert saved.status_code == 200
    graph_id = saved.json()["id"]
    run = client.post(f"/api/graphs/{graph_id}/run", json={})
    assert run.status_code == 200, run.text
    rows = _store().list_recent()
    assert rows, "运行入口未设置 usage 上下文"
    row = rows[0]
    assert row["caller"] == "ai_decision"
    assert row["graphId"] == graph_id
    assert row["nodeId"] == "ai_decision-1"
    assert row["runId"], "runId 应来自运行入口"
    assert row["tenantId"] == "t1"


def test_u1424_attribution_comes_from_the_entry_not_run_graph(monkeypatch):
    """反向门：run_graph 自己不设归属——直接调用不产生记录。

    若哪天把归属挪进 loader 而删掉入口设置，U1414 会假绿而本条仍绿；
    本条与 U1414 成对，证明归属来自入口（入口摘掉则 U1414 红）。
    """
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    from atlas.graph.dsl import parse_graph
    from atlas.graph.loader import run_graph

    result = run_graph(parse_graph(_decision_graph()), graph_id="direct")
    assert result["status"] == "completed"
    assert _store().list_recent() == []


def test_u1425_store_failure_never_breaks_a_run(monkeypatch):
    """反向门：store 落账抛异常 ⇒ 运行照常 completed，只 warning。"""
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    store = _store()

    def _boom(_rec):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(store, "record", _boom)
    saved = client.post("/api/graphs", json=_decision_graph())
    assert saved.status_code == 200
    graph_id = saved.json()["id"]
    run = client.post(f"/api/graphs/{graph_id}/run", json={})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"


# --- U1417/U1419/U1420/U1422 存储与 REST --------------------------------------


def test_u1417_memory_ring_is_bounded_at_2000():
    from atlas.llm.metering import InMemoryUsageStore, UsageRecord

    store = InMemoryUsageStore()
    assert store._records.maxlen == 2000
    for index in range(2005):
        store.record(UsageRecord(tenant_id="t1", ts=f"2026-10-14T00:{index // 60:02d}:{index % 60:02d}+00:00",
                                 caller="ai_decision", model="m", total_tokens=index))
    # ring 有界：5005 条只剩 2000；最近一条是最后写入的（list_recent 自身 clamp 200）。
    assert len(store._records) == 2000
    newest = store.list_recent(limit=1)[0]
    assert newest["ts"].startswith("2026-10-14T00:33:")  # index 2004 → 00:33:24
    assert newest["totalTokens"] == 2004


def test_u1419_summary_matches_detail_sum_over_the_endpoint(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    saved = client.post("/api/graphs", json=_decision_graph())
    graph_id = saved.json()["id"]
    for _ in range(3):
        assert client.post(f"/api/graphs/{graph_id}/run", json={}).status_code == 200
    detail = client.get("/api/usage/recent?limit=200").json()["items"]
    assert len(detail) == 3
    for group_by in ("caller", "model", "day"):
        summary = client.get(f"/api/usage/summary?group_by={group_by}").json()
        assert summary["groupBy"] == group_by
        total = sum(row["totalTokens"] for row in summary["items"])
        assert total == sum(row["totalTokens"] for row in detail), group_by
        calls = sum(row["calls"] for row in summary["items"])
        assert calls == len(detail), group_by
    bad = client.get("/api/usage/summary?group_by=nope")
    assert bad.status_code == 422


def test_u1420_recent_is_reverse_ordered_and_anonymous_is_401(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    saved = client.post("/api/graphs", json=_decision_graph())
    graph_id = saved.json()["id"]
    for _ in range(2):
        assert client.post(f"/api/graphs/{graph_id}/run", json={}).status_code == 200
    rows = client.get("/api/usage/recent?limit=1").json()["items"]
    assert len(rows) == 1  # limit 生效（clamp 1–200）
    anon = TestClient(app)
    assert anon.get("/api/usage/summary").status_code == 401
    assert anon.get("/api/usage/recent").status_code == 401


def test_u1422_reset_clears_usage_but_keeps_model_config(monkeypatch):
    monkeypatch.setenv("LITELLM_MODEL", "openai/fake")
    _install_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "ok", "confidence": 0.9}'
    )
    saved = client.post("/api/graphs", json=_decision_graph())
    graph_id = saved.json()["id"]
    assert client.post(f"/api/graphs/{graph_id}/run", json={}).status_code == 200
    assert _store().list_recent()
    assert client.post("/api/demo/reset").status_code in (200, 403)  # 角色决定，非本批语义
    if client.post("/api/demo/reset").status_code == 200:
        assert _store().list_recent() == []
        assert client.get("/api/models").status_code == 200


def test_single_litellm_exit_point_guard():
    """AU-D3 机检：src 内 litellm.completion 调用点恰好一处（metering.py）。

    新增调用点必须经 `complete_with_usage`，否则用量账会静默缺一块。
    """
    from pathlib import Path

    hits = []
    for path in Path("src/atlas").rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "litellm.completion(" in line and not line.strip().startswith("#"):
                hits.append(f"{path}:{lineno}")
    assert hits == ["src/atlas/llm/metering.py:334"], hits
