"""M9 批 1 U55（状态机部分）：RoutingStore rollout 状态机 + 分流计数（契约 03 `rollout_config`、
06 §6.17、12 §3.12；ADR T22③ 进程内实例照 T20 TaskStore）。

纯逻辑（直接构造 RoutingStore，versions 显式传入——store 不持有 GraphStore）。
"""

from __future__ import annotations

import pytest

from atlas.routing import (
    BucketRule,
    CanaryRule,
    FullRule,
    InternalRule,
    RolloutConfig,
    RolloutError,
    RoutingStore,
    TriggerEvent,
)

G = "refund-flow"


def _config(*, with_full: bool = True) -> RolloutConfig:
    rules = [
        InternalRule(tenants=["t-internal"]),
        BucketRule(value=200, percent=100),
        CanaryRule(percent=100),  # 测试确定性：100% canary，full 段是否生效由状态机决定
    ]
    if with_full:
        rules.append(FullRule())
    return RolloutConfig(rules=rules)


def _ev(**payload) -> TriggerEvent:
    return TriggerEvent(channel="webhook", payload=payload)


# ---------- configure / snapshot ----------

def test_configure_does_not_start():
    store = RoutingStore()
    state = store.configure(G, _config())
    assert state.status == "idle"
    assert state.stable is None and state.candidate is None
    assert state.config is not None


def test_snapshot_unknown_graph_is_idle_default():
    store = RoutingStore()
    state = store.snapshot(G)
    assert state.status == "idle" and state.config is None
    assert state.traffic["segments"]["canary"] == 0


def test_snapshot_is_deep_copied():
    store = RoutingStore()
    store.configure(G, _config())
    snap = store.snapshot(G)
    snap.status = "full"
    snap.traffic["candidate"] = 99
    assert store.snapshot(G).status == "idle"
    assert store.snapshot(G).traffic["candidate"] == 0


# ---------- start ----------

def test_start_requires_config():
    store = RoutingStore()
    with pytest.raises(RolloutError):
        store.start(G, [1, 2])


def test_start_requires_two_versions():
    store = RoutingStore()
    store.configure(G, _config())
    with pytest.raises(RolloutError):
        store.start(G, [1])
    state = store.start(G, [1, 2])
    assert (state.stable, state.candidate, state.status) == (1, 2, "canary")
    assert state.started_at is not None


def test_start_picks_latest_two_versions():
    store = RoutingStore()
    store.configure(G, _config())
    state = store.start(G, [1, 2, 3, 4])
    assert (state.stable, state.candidate) == (3, 4)


def test_start_only_from_idle():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [1, 2])
    with pytest.raises(RolloutError):
        store.start(G, [2, 3])


# ---------- promote ----------

def test_promote_only_from_canary():
    store = RoutingStore()
    with pytest.raises(RolloutError):
        store.promote(G)  # 未配置
    store.configure(G, _config())
    with pytest.raises(RolloutError):
        store.promote(G)  # idle
    store.start(G, [1, 2])
    assert store.promote(G).status == "full"
    with pytest.raises(RolloutError):
        store.promote(G)  # full 不可再 promote


def test_no_automatic_promote_path_exists():
    # 契约：放量唯一路径是手动 promote；store 上不存在任何 auto-promote 方法
    assert not hasattr(RoutingStore, "auto_promote")
    assert not hasattr(RoutingStore, "promote_automatically")


# ---------- rollback ----------

def test_rollback_requires_active_rollout():
    store = RoutingStore()
    with pytest.raises(RolloutError):
        store.rollback(G)  # 无 entry
    store.configure(G, _config())
    with pytest.raises(RolloutError):
        store.rollback(G)  # idle 未启动


def test_rollback_from_canary_and_full():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [1, 2])
    state = store.rollback(G, actor="auto", reason="run_error_rate 越阈")
    assert state.status == "rolled_back"
    assert state.rollback_actor == "auto"
    assert state.rolled_back_at is not None
    assert "run_error_rate" in state.rollback_reason

    store2 = RoutingStore()
    store2.configure(G, _config())
    store2.start(G, [1, 2])
    store2.promote(G)
    assert store2.rollback(G).status == "rolled_back"


def test_rollback_is_idempotent_and_keeps_first_reason():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [1, 2])
    first = store.rollback(G, actor="auto", reason="first")
    second = store.rollback(G, actor="manual", reason="second")
    assert second.status == "rolled_back"
    assert second.rollback_reason == "first"
    assert second.rolled_back_at == first.rolled_back_at


# ---------- resolve 与状态语义 ----------

def test_resolve_unknown_graph_is_unpublished():
    store = RoutingStore()
    assert store.resolve(G, tenant="t1", event=_ev(order_id="x")) == (None, "unpublished")


def test_resolve_idle_without_versions_is_unpublished():
    store = RoutingStore()
    store.configure(G, _config())
    assert store.resolve(G, tenant="t1", event=_ev(order_id="x")) == (None, "unpublished")


def test_resolve_canary_routes_by_segments_and_counts():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [6, 7])

    assert store.resolve(G, tenant="t-internal", event=_ev(amount=9999, order_id="a")) == (7, "internal")
    assert store.resolve(G, tenant="t1", event=_ev(amount=100, order_id="b")) == (7, "lowValueBucket")
    assert store.resolve(G, tenant="t1", event=_ev(amount=5000, order_id="c")) == (7, "canary")
    assert store.resolve(G, tenant="t1", event=_ev(amount=5000)) == (6, "fallback")

    snap = store.snapshot(G)
    assert snap.traffic["candidate"] == 3
    assert snap.traffic["stable"] == 1
    seg = snap.traffic["segments"]
    assert (seg["internal"], seg["lowValueBucket"], seg["canary"], seg["fallback"]) == (1, 1, 1, 1)


def test_canary_state_full_rule_is_not_effective():
    # 配置含 full 段，但 canary 状态下未命中前三段的流量必须落 stable，不得被 full 提前全量
    store = RoutingStore()
    store.configure(G, _config(with_full=True))
    store.start(G, [6, 7])
    # canary percent 很小，找一个稳定落 stable 的键
    import hashlib

    def hits(key: str, percent: int) -> bool:
        return int(hashlib.sha256(f"{G}:{key}".encode()).hexdigest(), 16) % 100 < percent

    store.configure(
        G,
        RolloutConfig(rules=[InternalRule(tenants=[]), BucketRule(value=0.0001, percent=100),
                             CanaryRule(percent=1), FullRule()]),
    )
    stable_key = next(f"k{i}" for i in range(500) if not hits(f"k{i}", 1))
    version, segment = store.resolve(G, tenant="t1", event=_ev(amount=9999, order_id=stable_key))
    assert (version, segment) == (6, "stable")


def test_resolve_full_state_sends_all_to_candidate():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [6, 7])
    store.promote(G)
    assert store.resolve(G, tenant="t1", event=_ev(amount=9999)) == (7, "full")
    assert store.resolve(G, tenant="t-other", event=_ev()) == (7, "full")
    assert store.snapshot(G).traffic["segments"]["full"] == 2


def test_resolve_rolled_back_sends_all_new_traffic_to_stable():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [6, 7])
    store.rollback(G, actor="auto", reason="metric breach")
    # 即使是 internal 租户/低金额单，回滚后新流量一律 stable（candidate 撤流）
    assert store.resolve(G, tenant="t-internal", event=_ev(amount=1, order_id="z")) == (6, "stable")
    assert store.resolve(G, tenant="t1", event=_ev(amount=1, order_id="y")) == (6, "stable")
    snap = store.snapshot(G)
    assert snap.traffic["stable"] == 2 and snap.traffic["candidate"] == 0


def test_reset_clears_all_states():
    store = RoutingStore()
    store.configure(G, _config())
    store.start(G, [1, 2])
    store.reset()
    assert store.snapshot(G).status == "idle"
    assert store.resolve(G, tenant="t1", event=_ev(order_id="x")) == (None, "unpublished")
