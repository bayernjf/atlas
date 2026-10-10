# -*- coding: utf-8 -*-
"""打包 AK（docs/122 D-4/D-5）：写入策略 update_policy 与召回衰减 decay。

进程内档在这一份里逐条测；PG 档的对拍在 `test_memory_pg_integration.py`（同一组判据、
两档必须给同一个答案）。三条最值得说的判据都带正控：
- 去重不是"写两次都成功"，而是**只剩一条且 id/created_at 不变**；
- 版本策略是**新建一条**并把 version 递增，不是覆盖；
- 衰减只改名次、不改集合，且**关掉时与接入前逐字一致**。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from atlas.memory.items import MemoryStore
from atlas.memory.models import MemoryValidationError

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def store(monkeypatch):
    """固定时钟写入口：created_at 由 _utc_now 决定，不钉住就没法测衰减。"""
    stamps = iter([NOW.isoformat(), (NOW + timedelta(days=1)).isoformat(),
                   (NOW + timedelta(days=2)).isoformat(), (NOW + timedelta(days=3)).isoformat(),
                   (NOW + timedelta(days=4)).isoformat(), (NOW + timedelta(days=5)).isoformat()])
    monkeypatch.setattr("atlas.memory.items._utc_now", lambda: next(stamps))
    return MemoryStore()


def _remember(store, content, scope, **kwargs):
    return store.remember(kind="fact", content=content, scope=scope, **kwargs)


# --------------------------------------------------------------------------- #
# U1337 overwrite
# --------------------------------------------------------------------------- #


def test_u1337_overwrite_updates_in_place_and_keeps_the_original_timestamp(store):
    first = _remember(store, "对象 A 当前状态：待审核", {"object_id": "A"})
    second = _remember(
        store, "对象 A 当前状态：已退款", {"object_id": "A"},
        update_policy="overwrite", dedup_keys=["object_id"],
    )
    assert second["id"] == first["id"], "命中即原地更新，不是又记一条"
    assert second["created_at"] == first["created_at"], "created_at 不变＝'这条事实被更新了'"
    assert second["content"] == "对象 A 当前状态：已退款"
    listed = store.list()
    assert len(listed) == 1, listed


def test_u1337b_overwrite_without_dedup_keys_suppresses_only_exact_duplicates(store):
    """正控：不给去重键时，**同内容不同 scope** 不能被合并——否则对象 A 会吃掉对象 B 的事实。"""
    _remember(store, "已退过款", {"object_id": "A"})
    _remember(store, "已退过款", {"object_id": "B"})
    assert len(store.list()) == 2
    _remember(store, "已退过款", {"object_id": "A"})
    assert len(store.list()) == 2, "同 scope 同内容才该被抑制"


def test_u1337c_dedup_key_missing_from_scope_does_not_match(store):
    """去重键在新条目 scope 里不存在＝不命中（不能拿旧条目的值来凑）。"""
    _remember(store, "有 object_id 的事实", {"object_id": "A"})
    _remember(store, "没带 object_id", {"user_id": "u"}, dedup_keys=["object_id"])
    assert len(store.list()) == 2


# --------------------------------------------------------------------------- #
# U1338 version
# --------------------------------------------------------------------------- #


def test_u1338_version_creates_a_new_row_with_an_incremented_version(store):
    first = _remember(store, "规则 v1", {"object_id": "R"},
                      update_policy="version", dedup_keys=["object_id"])
    second = _remember(store, "规则 v2", {"object_id": "R"},
                       update_policy="version", dedup_keys=["object_id"])
    third = _remember(store, "规则 v3", {"object_id": "R"},
                      update_policy="version", dedup_keys=["object_id"])
    assert first.get("metadata", {}).get("version") is None, "首条不硬塞版本号"
    assert second["metadata"]["version"] == "2"
    assert third["metadata"]["version"] == "3"
    assert len({first["id"], second["id"], third["id"]}) == 3, "version 策略必须新建条目"


# --------------------------------------------------------------------------- #
# U1339 ask
# --------------------------------------------------------------------------- #


def test_u1339_ask_returns_the_conflict_and_writes_nothing(store):
    existing = _remember(store, "旧结论", {"object_id": "A"})
    answer = _remember(store, "新结论", {"object_id": "A"},
                       update_policy="ask", dedup_keys=["object_id"])
    assert answer["conflict"] is True
    assert answer["existing_id"] == existing["id"]
    assert answer["existing"]["content"] == "旧结论"
    assert len(store.list()) == 1, "ask 不许静默写入"
    assert store.list()[0]["content"] == "旧结论"


def test_u1339b_ask_with_no_match_writes_normally(store):
    answer = _remember(store, "新结论", {"object_id": "Z"},
                       update_policy="ask", dedup_keys=["object_id"])
    assert "conflict" not in answer and answer["content"] == "新结论"


# --------------------------------------------------------------------------- #
# U1340 / U1341 decay
# --------------------------------------------------------------------------- #


def _two_equal_score_memories(store) -> None:
    """同内容、不同 scope＝原始 cosine 完全相同，于是名次只可能由衰减改变（判据最干净）。"""
    _remember(store, "客户偏好极速退款", {"object_id": "old"})   # created NOW
    _remember(store, "客户偏好极速退款", {"object_id": "new"})   # created NOW+1d


def test_u1340_decay_reorders_by_age_and_uses_the_injected_clock(store):
    _two_equal_score_memories(store)
    plain = store.recall("客户偏好极速退款", top_k=2)
    assert [item["scope"]["object_id"] for item in plain] == ["old", "new"], "基线＝插入序"

    # 年龄差取 1 天、半衰期 30 天：分数仍落在 0.9 量级，6 位小数的 score 足够还原比值。
    decayed = store.recall(
        "客户偏好极速退款", top_k=2,
        decay={"enabled": True, "half_life_days": 30}, now_override=NOW + timedelta(days=3),
    )
    assert [item["scope"]["object_id"] for item in decayed] == ["new", "old"], decayed
    assert decayed[0]["score"] > decayed[1]["score"]
    # 半衰期公式：old 比 new 老一天，权重比应为 0.5^(1/30)
    ratio = decayed[1]["score"] / decayed[0]["score"]
    assert abs(ratio - 0.5 ** (1 / 30)) < 1e-4, ratio


def test_u1341_decay_off_is_byte_identical_to_the_pre_batch_ranking(store):
    _two_equal_score_memories(store)
    baseline = store.recall("客户偏好极速退款", top_k=2)
    assert store.recall("客户偏好极速退款", top_k=2, decay=None) == baseline
    assert store.recall("客户偏好极速退款", top_k=2,
                        decay={"enabled": False, "half_life_days": 5}) == baseline


def test_decay_only_reorders_and_breaks_ties_toward_recency(store):
    """原始分数相同（同内容）时，任何衰减都会把名次推向"更新的那条在前"。

    这条同时钉住两件事：衰减不改**集合**，且它是可预期的排序因子而不是噪声。
    """
    _two_equal_score_memories(store)
    baseline = store.recall("客户偏好极速退款", top_k=2)
    decayed = store.recall("客户偏好极速退款", top_k=2,
                           decay={"enabled": True, "half_life_days": 3650},
                           now_override=NOW + timedelta(days=5))
    assert {item["id"] for item in decayed} == {item["id"] for item in baseline}
    assert [item["scope"]["object_id"] for item in decayed] == ["new", "old"]


def test_decay_does_not_change_the_result_set_only_the_order(store):
    """两条都该被检索到（内容相近），衰减后集合不变、名次按新→旧重排。"""
    _remember(store, "客户偏好极速退款", {"object_id": "old"})
    _remember(store, "客户偏好极速退款流程已上线", {"object_id": "new"})
    before = [item["scope"]["object_id"] for item in store.recall("客户偏好极速退款", top_k=5)]
    after = [
        item["scope"]["object_id"]
        for item in store.recall("客户偏好极速退款", top_k=5,
                                 decay={"enabled": True, "half_life_days": 1},
                                 now_override=NOW + timedelta(days=900))
    ]
    assert sorted(before) == sorted(after) == ["new", "old"]
    assert after == ["new", "old"], "半衰期 1 天、差一天 ⇒ 旧条目必然被压到后面"


# --------------------------------------------------------------------------- #
# 入参校验（两档共用同一份 validate_*）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("policy", ["merge", "OVERWRITE", 1, object()])
def test_invalid_update_policy_is_rejected(store, policy):
    with pytest.raises(MemoryValidationError):
        _remember(store, "x", {"object_id": "A"}, update_policy=policy)


@pytest.mark.parametrize("keys", ["object_id", [""], [1], ["a", "b"] * 6])
def test_invalid_dedup_keys_are_rejected(store, keys):
    with pytest.raises(MemoryValidationError):
        _remember(store, "x", {"object_id": "A"}, dedup_keys=keys)


@pytest.mark.parametrize("decay", [True, {"enabled": True, "half_life_days": 0},
                                   {"enabled": True, "half_life_days": 4000},
                                   {"enabled": True, "half_life_days": "30"}])
def test_invalid_decay_is_rejected(store, decay):
    with pytest.raises(MemoryValidationError):
        store.recall("q", decay=decay)


def test_unparsable_created_at_is_not_dropped_and_not_decayed(monkeypatch):
    """时间戳是字符串字段，脏值不该让一条记忆从检索里蒸发（按不衰减处理）。"""
    store = MemoryStore()
    store.remember(kind="fact", content="客户偏好极速退款", scope={"object_id": "A"})
    store._items[0]["created_at"] = "not-a-date"  # noqa: SLF001 直接造脏值，公开接口做不到
    results = store.recall("客户偏好极速退款", decay={"enabled": True, "half_life_days": 1},
                           now_override=NOW + timedelta(days=900))
    assert [item["id"] for item in results] == [store._items[0]["id"]]  # noqa: SLF001
