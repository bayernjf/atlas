"""LLM 用量计量单测（docs/129 打包 AU＝D70；原子② `llm/metering.py`）。

mock litellm 照 `tests/test_decision_client.py` 先例：把假模块塞进 sys.modules
（`complete_with_usage` 是函数内 `import litellm`，塞模块即替换）。
"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.llm.metering import (
    InMemoryUsageStore,
    UsageRecord,
    complete_with_usage,
    current_context,
    usage_context,
    usage_node,
)


class _FakeResponse:
    """属性与下标皆可读的假回包（litellm ModelResponse 两样都支持）。"""

    def __init__(self, content: str, usage: dict | None = None):
        self._payload = {
            "choices": [{"message": {"content": content}}],
            "usage": usage,
        }

    def __getitem__(self, key):
        return self._payload[key]

    @property
    def usage(self):
        return self._payload["usage"]


def _install_fake_litellm(monkeypatch, response=None, exc=None):
    calls: list[dict] = []

    def _completion(**kwargs):
        calls.append(kwargs)
        if exc is not None:
            raise exc
        return response

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return calls


def _kwargs() -> dict:
    return {"model": "openai/test-model", "messages": []}


# --- helper：有上下文记一条，token 逐字来自回包 -------------------------------


def test_helper_records_usage_from_response(monkeypatch):
    store = InMemoryUsageStore()
    _install_fake_litellm(
        monkeypatch,
        _FakeResponse("ok", {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}),
    )
    with usage_context("t1", run_id="r1", graph_id="g1", store=store):
        content, usage = complete_with_usage(_kwargs(), caller="ai_decision", model="m1")
    assert content == "ok"
    assert usage["total_tokens"] == 17
    records = store.list_recent()
    assert len(records) == 1
    rec = records[0]
    assert rec["caller"] == "ai_decision"
    assert rec["model"] == "m1"
    assert rec["promptTokens"] == 12
    assert rec["completionTokens"] == 5
    assert rec["totalTokens"] == 17
    assert rec["status"] == "ok"
    assert rec["tenantId"] == "t1"
    assert rec["runId"] == "r1"
    assert rec["graphId"] == "g1"
    assert rec["latencyMs"] >= 0.0


def test_helper_without_context_records_nothing(monkeypatch):
    store = InMemoryUsageStore()
    _install_fake_litellm(monkeypatch, _FakeResponse("ok", {"prompt_tokens": 1}))
    # AU-D4：无上下文（loader 直跑/单测）⇒ 不记录，且调用本身照常成功。
    content, _usage = complete_with_usage(_kwargs(), caller="ai_decision", model="m1")
    assert content == "ok"
    assert current_context() is None
    assert store.list_recent() == []


def test_context_without_store_records_nothing(monkeypatch):
    _install_fake_litellm(monkeypatch, _FakeResponse("ok", {"prompt_tokens": 1}))
    with usage_context("t1") as ctx:
        assert ctx.store is None
        complete_with_usage(_kwargs(), caller="ai_decision", model="m1")
    assert current_context() is None


def test_usage_node_overrides_node_id_only(monkeypatch):
    store = InMemoryUsageStore()
    _install_fake_litellm(
        monkeypatch,
        _FakeResponse("ok", {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}),
    )
    with usage_context("t1", run_id="r1", graph_id="g1", store=store):
        with usage_node("ai-1"):
            complete_with_usage(_kwargs(), caller="ai_decision", model="m1")
        # 内层退出后外层上下文原样恢复（node_id 不残留）
        assert current_context().node_id == ""
    rec = store.list_recent()[0]
    assert rec["nodeId"] == "ai-1"
    assert rec["runId"] == "r1"
    assert rec["graphId"] == "g1"


def test_usage_node_without_outer_context_is_noop(monkeypatch):
    _install_fake_litellm(monkeypatch, _FakeResponse("ok", {"prompt_tokens": 2}))
    with usage_node("ai-1"):
        assert current_context() is None


# --- 失败与缺 usage 两条边 -----------------------------------------------------


def test_error_path_records_error_row_and_reraises(monkeypatch):
    store = InMemoryUsageStore()
    _install_fake_litellm(monkeypatch, exc=TimeoutError("read timeout"))
    with usage_context("t1", run_id="r1", store=store):
        with pytest.raises(TimeoutError):
            complete_with_usage(_kwargs(), caller="ai_decision", model="m1")
    rec = store.list_recent()[0]
    assert rec["status"] == "error"
    assert rec["totalTokens"] == 0
    assert rec["runId"] == "r1"


def test_missing_usage_records_zero_and_warns(monkeypatch, caplog):
    store = InMemoryUsageStore()
    _install_fake_litellm(monkeypatch, _FakeResponse("ok", None))
    with caplog.at_level("WARNING"), usage_context("t1", store=store):
        content, usage = complete_with_usage(_kwargs(), caller="condition_llm", model="m1")
    assert content == "ok"
    assert usage["total_tokens"] == 0
    rec = store.list_recent()[0]
    assert rec["status"] == "ok"
    assert rec["totalTokens"] == 0
    assert "未带 usage" in caplog.text


def test_total_falls_back_to_prompt_plus_completion(monkeypatch):
    store = InMemoryUsageStore()
    _install_fake_litellm(
        monkeypatch, _FakeResponse("ok", {"prompt_tokens": 7, "completion_tokens": 4})
    )
    with usage_context("t1", store=store):
        _content, usage = complete_with_usage(_kwargs(), caller="nl_generate", model="m1")
    assert usage["total_tokens"] == 11


# --- 内存档 store：ring 有界＋倒序＋聚合对账 -------------------------------------


def test_in_memory_store_ring_is_bounded():
    store = InMemoryUsageStore(ring_size=10)
    for index in range(25):
        store.record(UsageRecord(tenant_id="t1", ts=f"2026-10-14T00:00:{index:02d}+00:00",
                                 caller="ai_decision", model="m", total_tokens=index))
    recent = store.list_recent()
    assert len(recent) == 10
    assert recent[0]["ts"].endswith(":24+00:00")  # 倒序：最新在前
    assert recent[-1]["ts"].endswith(":15+00:00")  # 最旧 0-14 已被覆盖


def test_summarize_groups_and_matches_detail_sum():
    store = InMemoryUsageStore()
    rows = [
        ("2026-10-13T10:00:00+00:00", "ai_decision", "m1", 10, 5, 15),
        ("2026-10-13T11:00:00+00:00", "condition_llm", "m1", 20, 6, 26),
        ("2026-10-14T09:00:00+00:00", "ai_decision", "m2", 30, 7, 37),
    ]
    for ts, caller, model, pt, ct, tt in rows:
        store.record(UsageRecord(tenant_id="t1", ts=ts, caller=caller, model=model,
                                 prompt_tokens=pt, completion_tokens=ct, total_tokens=tt))
    by_caller = {row["key"]: row for row in store.summarize(group_by="caller")}
    assert by_caller["ai_decision"]["totalTokens"] == 52
    assert by_caller["ai_decision"]["calls"] == 2
    assert by_caller["condition_llm"]["totalTokens"] == 26
    by_model = {row["key"]: row for row in store.summarize(group_by="model")}
    assert by_model["m1"]["totalTokens"] == 41
    by_day = {row["key"]: row for row in store.summarize(group_by="day")}
    assert by_day["2026-10-13"]["calls"] == 2
    assert by_day["2026-10-14"]["calls"] == 1
    # 时间窗：只取 10-14
    window = store.summarize(since="2026-10-14T00:00:00+00:00", group_by="caller")
    assert [row["key"] for row in window] == ["ai_decision"]
    assert window[0]["totalTokens"] == 37


def test_summarize_errors_counted():
    store = InMemoryUsageStore()
    store.record(UsageRecord(tenant_id="t1", ts="2026-10-14T00:00:00+00:00",
                             caller="ai_decision", model="m", status="error"))
    row = store.summarize()[0]
    assert row["errors"] == 1
    assert row["calls"] == 1


def test_reset_clears_records():
    store = InMemoryUsageStore()
    store.record(UsageRecord(tenant_id="t1", ts="2026-10-14T00:00:00+00:00",
                             caller="ai_decision", model="m"))
    assert store.list_recent()
    store.reset()
    assert store.list_recent() == []
    assert store.summarize() == []
