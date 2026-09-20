"""M11 批 1 单元测试（docs/26 §9.2，U72–U83）：本地确定性 embedder + 进程内 MemoryStore。"""

from __future__ import annotations

import math

import pytest

from atlas.memory.embeddings import (
    EMBED_DIM,
    LocalDeterministicEmbedder,
    cosine_similarity,
    get_embedding_provider,
    tokenize,
)
from atlas.memory.items import MemoryStore, scope_contains
from atlas.memory.models import MemoryValidationError


# ---- U72–U76：embedder ----


def test_u72_embedder_deterministic_same_text_same_vector():
    embedder = LocalDeterministicEmbedder()
    a = embedder.embed_one("用户偏好顺丰发货")
    b = embedder.embed_one("用户偏好顺丰发货")
    assert a == b
    assert len(a) == EMBED_DIM == 256


def test_u73_different_text_different_vector():
    embedder = LocalDeterministicEmbedder()
    a = embedder.embed_one("refund broken item")
    b = embedder.embed_one("天气晴朗适合散步")
    assert a != b


def test_u74_vectors_are_l2_normalized_and_empty_is_zero():
    embedder = LocalDeterministicEmbedder()
    vector = embedder.embed_one("normalized vector check")
    norm = math.sqrt(sum(v * v for v in vector))
    assert norm == pytest.approx(1.0, abs=1e-9)
    zero = embedder.embed_one("")
    assert zero == [0.0] * EMBED_DIM
    assert cosine_similarity(zero, vector) == 0.0


def test_u75_cosine_self_is_one_unrelated_is_low():
    embedder = LocalDeterministicEmbedder()
    vector = embedder.embed_one("customer wants refund for damaged goods")
    assert cosine_similarity(vector, vector) == pytest.approx(1.0, abs=1e-9)
    other = embedder.embed_one("今天天气不错")
    # 中英无共享 token；signed hashing 碰撞可能产生小负值/小正值，绝对值应很小
    assert abs(cosine_similarity(vector, other)) < 0.2
    assert -1.0 <= cosine_similarity(vector, other) <= 1.0


def test_u76_chinese_bigram_improves_recall():
    embedder = LocalDeterministicEmbedder()
    # bigram 让"顺丰"作为整体在两句话中共享；仅 unigram 也命中但 bigram 提升共享权重
    a = embedder.embed_one("用户喜欢顺丰发货")
    b = embedder.embed_one("顺丰")
    assert cosine_similarity(a, b) > 0.0
    assert "顺丰" in tokenize("用户喜欢顺丰发货")  # bigram 存在
    # 英文小写切词
    assert tokenize("Hello World 42") == ["hello", "world", "42"]


def test_embed_batch_matches_single():
    embedder = LocalDeterministicEmbedder()
    texts = ["fact one", "偏好二"]
    vectors = embedder.embed(texts)
    assert vectors == [embedder.embed_one(t) for t in texts]


def test_get_embedding_provider_is_local_by_default(monkeypatch):
    monkeypatch.delenv("ATLAS_EMBEDDING_PROVIDER", raising=False)
    provider = get_embedding_provider()
    assert isinstance(provider, LocalDeterministicEmbedder)
    monkeypatch.setenv("ATLAS_EMBEDDING_PROVIDER", "litellm")
    with pytest.raises(RuntimeError):
        get_embedding_provider()


# ---- U77–U81：remember / recall ----


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def test_u77_remember_fields_and_mem_n_counter(store):
    item = store.remember(kind="fact", content="订单 o-1 已退款 50 元")
    assert item["id"] == "mem-1"
    assert item["kind"] == "fact"
    assert item["content"] == "订单 o-1 已退款 50 元"
    assert item["confidence"] == 1.0
    assert item["source"] == "tool"
    assert item["scope"] == {}
    assert item["metadata"] == {}
    assert "embedding" not in item
    assert "created_at" in item
    second = store.remember(kind="preference", content="用户偏好顺丰")
    assert second["id"] == "mem-2"


def test_u78_recall_ranking_and_top_k(store):
    store.remember(kind="fact", content="用户要求退款")
    store.remember(kind="fact", content="完全无关的天气预报内容")
    store.remember(kind="preference", content="用户要求尽快退款到账")
    results = store.recall("退款", top_k=2)
    assert len(results) == 2
    assert results[0]["score"] >= results[1]["score"]
    assert "退款" in results[0]["content"]
    assert all("score" in item for item in results)
    assert "embedding" not in results[0]


def test_u79_recall_kind_filter(store):
    store.remember(kind="fact", content="顺丰偏好事实")
    store.remember(kind="preference", content="用户偏好顺丰快递")
    facts = store.recall("顺丰", kind="fact")
    assert all(item["kind"] == "fact" for item in facts)
    assert len(facts) == 1
    preferences = store.recall("顺丰", kind="preference")
    assert all(item["kind"] == "preference" for item in preferences)


def test_u80_scope_subset_filter(store):
    store.remember(kind="fact", content="订单状态已退款", scope={"user_id": "u-1", "order_id": "o-1"})
    store.remember(kind="fact", content="订单状态已退款", scope={"user_id": "u-2", "order_id": "o-2"})
    results = store.recall("订单状态已退款", scope={"user_id": "u-1"})
    assert len(results) == 1
    assert results[0]["scope"]["user_id"] == "u-1"
    # 多键子集：全部键值须包含
    assert scope_contains({"a": "1", "b": "2"}, {"a": "1"})
    assert not scope_contains({"a": "1"}, {"a": "1", "b": "2"})
    assert not scope_contains({"a": "1"}, {"a": "9"})


def test_u81_min_score_filters(store):
    store.remember(kind="fact", content="共享词召回")
    # 与任何记忆都无共享 token 的查询
    results = store.recall("xyzzy qwerty", min_score=0.5)
    assert results == []
    # min_score=0 且无共享词时仍不命中（零向量余弦为 0，含 0 阈值时也不召回无分词命中项——
    # 此处查询有英文 token，与中文记忆零重叠，score=0.0 被 >=0 保留但 top_k 截断前为空集语义）
    results_zero = store.recall("xyzzy qwerty", min_score=0.0)
    assert all(item["score"] == 0.0 for item in results_zero) or results_zero == []


def test_recall_empty_returns_empty_list(store):
    assert store.recall("任何内容") == []


# ---- U82：校验 ----


def test_u82_validation_errors(store):
    with pytest.raises(MemoryValidationError):
        store.remember(kind="bogus", content="x")
    with pytest.raises(MemoryValidationError):
        store.remember(kind="fact", content="   ")
    with pytest.raises(MemoryValidationError):
        store.remember(kind="fact", content="x" * 2001)
    with pytest.raises(MemoryValidationError):
        store.remember(kind="fact", content="x", confidence=1.5)
    with pytest.raises(MemoryValidationError):
        store.remember(kind="fact", content="x", confidence=-0.1)
    with pytest.raises(MemoryValidationError):
        store.remember(kind="fact", content="x", confidence=True)  # bool 拒绝
    with pytest.raises(MemoryValidationError):
        store.remember(kind="fact", content="x", scope={"k": 1})  # 非字符串值
    with pytest.raises(MemoryValidationError):
        store.recall("   ")
    with pytest.raises(MemoryValidationError):
        store.recall("x", top_k=0)
    with pytest.raises(MemoryValidationError):
        store.recall("x", top_k=21)
    with pytest.raises(MemoryValidationError):
        store.recall("x", top_k=True)
    with pytest.raises(MemoryValidationError):
        store.recall("x", min_score=2.0)


def test_confidence_default_and_custom(store):
    item = store.remember(kind="fact", content="默认置信度")
    assert item["confidence"] == 1.0
    item_low = store.remember(kind="fact", content="低置信度", confidence=0.5)
    assert item_low["confidence"] == 0.5


# ---- U83：list / delete / clear ----


def test_u83_delete_exists_and_missing(store):
    item = store.remember(kind="fact", content="待删除记忆")
    assert store.delete(item["id"]) is True
    assert store.delete(item["id"]) is False
    assert store.list() == []


def test_list_newest_first_with_kind_filter_and_limit(store):
    store.remember(kind="fact", content="第一条事实")
    store.remember(kind="preference", content="第一条偏好")
    store.remember(kind="fact", content="第二条事实")
    items = store.list()
    assert [i["content"] for i in items] == ["第二条事实", "第一条偏好", "第一条事实"]
    facts = store.list(kind="fact")
    assert all(i["kind"] == "fact" for i in facts)
    assert len(facts) == 2
    limited = store.list(limit=1)
    assert len(limited) == 1
    with pytest.raises(ValueError):
        store.list(limit=0)


def test_clear_resets_items_and_counter(store):
    store.remember(kind="fact", content="临时记忆")
    store.clear()
    assert store.list() == []
    item = store.remember(kind="fact", content="重置后记忆")
    assert item["id"] == "mem-1"
