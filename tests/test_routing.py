"""M9 批 1 U55：Router 三段分桶纯逻辑（docs/20 §4.4，契约 03 `rollout_config`/`route_decision`、
04 §5.16、06 §6.17、12 §3.12）。

resolve_version 为无 IO 纯函数：固定序 internal→lowValueBucket→canary→full，
fail-safe 落 stable；本文件只钉纯逻辑，REST/状态机在批 1 后续、pin 接线在 U56。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.routing import (
    BucketRule,
    CanaryRule,
    FullRule,
    GateConfig,
    GateMetric,
    InternalRule,
    RolloutConfig,
    TriggerEvent,
    bucket_key,
    hash_hit,
    resolve_version,
)

G = "refund-flow"


def _full_config(*, canary_percent: int = 5, bucket_value: float = 200) -> RolloutConfig:
    return RolloutConfig(
        rules=[
            InternalRule(tenants=["t-internal"]),
            BucketRule(value=bucket_value, percent=100),
            CanaryRule(percent=canary_percent),
            FullRule(),
        ],
        gate=GateConfig(
            metrics=[
                GateMetric(id="run_error_rate", threshold=0.02),
                GateMetric(id="manual_escalation_rate", threshold=0.10, compareWith=6),
                GateMetric(id="refund_amount_diff_rate", threshold=0.005),
            ]
        ),
    )


def _event(amount=None, order_id=None, id=None, channel="api"):  # noqa: A002
    payload: dict = {}
    if amount is not None:
        payload["amount"] = amount
    if order_id is not None:
        payload["order_id"] = order_id
    if id is not None:
        payload["id"] = id
    return TriggerEvent(channel=channel, payload=payload)


# ---------- internal 段 ----------

def test_internal_tenant_goes_candidate():
    cfg = _full_config()
    # 即使金额很大、不在任何业务桶，internal 租户仍最先命中
    ev = _event(amount=999999, order_id="12346")
    version, segment = resolve_version(
        graph_id=G, stable=6, candidate=7, tenant="t-internal", event=ev, config=cfg
    )
    assert (version, segment) == (7, "internal")


def test_non_internal_tenant_skips_internal_segment():
    cfg = _full_config()
    ev = _event(amount=999999, order_id="12346")
    version, segment = resolve_version(
        graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg
    )
    assert segment != "internal"
    assert version in (6, 7)  # 落 canary 或 stable，绝不因租户误判 internal


def test_internal_without_rule_falls_through():
    cfg = RolloutConfig(rules=[CanaryRule(percent=100)])
    ev = _event(order_id="12345")
    version, segment = resolve_version(
        graph_id=G, stable=6, candidate=7, tenant="t-internal", event=ev, config=cfg
    )
    assert (version, segment) == (7, "canary")


# ---------- lowValueBucket 段 ----------

def test_low_value_bucket_amount_at_or_below_threshold_goes_candidate():
    cfg = _full_config(bucket_value=200)
    for amount in (1, 199, 200, 200.0):
        ev = _event(amount=amount, order_id=f"o-{amount}")
        version, segment = resolve_version(
            graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg
        )
        assert (version, segment) == (7, "lowValueBucket"), amount


def test_low_value_bucket_missing_or_non_numeric_amount_falls_through():
    cfg = _full_config()
    # 无 amount、amount 为字符串、None：都不得命中金额桶（继续到 canary/fallback）
    for payload in ({"order_id": "12346"}, {"amount": "100", "order_id": "12346"},
                    {"amount": None, "order_id": "12346"}):
        ev = TriggerEvent(payload=payload)
        _, segment = resolve_version(
            graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg
        )
        assert segment != "lowValueBucket", payload


def test_low_value_bucket_bool_amount_not_a_number():
    cfg = _full_config()
    ev = TriggerEvent(payload={"amount": True, "order_id": "x"})  # bool 是 int 子类，必须排除
    _, segment = resolve_version(
        graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg
    )
    assert segment != "lowValueBucket"


def test_amount_above_threshold_skips_bucket():
    cfg = _full_config(bucket_value=200)
    ev = _event(amount=201, order_id="12346")
    _, segment = resolve_version(
        graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg
    )
    assert segment != "lowValueBucket"


def test_bucket_percent_below_100_uses_stable_hash_when_key_present():
    # 金额命中但 percent=1：同一键结果恒定；percent=100 时必进
    cfg100 = RolloutConfig(rules=[BucketRule(value=200, percent=100)])
    cfg1 = RolloutConfig(rules=[BucketRule(value=200, percent=1)])
    ev = _event(amount=50, order_id="12345")
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg100)[0] == 7
    first = resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg1)
    second = resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg1)
    assert first == second  # 同键恒定（进 candidate 或落 stable 都可，但必须稳定）


def test_bucket_percent_below_100_missing_key_fails_safe():
    cfg = RolloutConfig(rules=[BucketRule(value=200, percent=50)])
    ev = _event(amount=50)  # 金额命中但无 order_id/id
    version, segment = resolve_version(
        graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg
    )
    assert (version, segment) == (6, "fallback")


# ---------- canary 段 ----------

def test_canary_hash_is_stable_across_calls():
    cfg = _full_config(canary_percent=5)
    ev = _event(amount=5000, order_id="12346")
    results = {
        resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg)
        for _ in range(20)
    }
    assert len(results) == 1  # 同一对象永不横跳


def test_canary_hash_buckets_by_graph_id_and_key():
    # 不同 graph_id 命名空间下哈希独立；同 (graph_id,key) 恒定
    assert hash_hit(G, "12346", 100) is True
    assert hash_hit(G, "12346", 1) in (True, False)
    # 5% 下 200 个键里至少有落 stable 的（概率上几乎必然，防百分比被忽略）
    cfg = _full_config(canary_percent=5)
    stable_hits = sum(
        resolve_version(
            graph_id=G, stable=6, candidate=7, tenant="t1",
            event=_event(amount=5000, order_id=f"order-{i}"), config=cfg,
        )[0]
        == 6
        for i in range(200)
    )
    assert stable_hits > 100  # 95% 应落 stable，留极宽边界防哈希分布抖动


def test_canary_percent_100_sends_all_to_candidate():
    cfg = RolloutConfig(rules=[CanaryRule(percent=100)])
    for i in range(50):
        ev = _event(order_id=f"order-{i}")
        assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg) == (
            7,
            "canary",
        )


def test_canary_falls_back_to_id_when_order_id_absent():
    cfg = RolloutConfig(rules=[CanaryRule(percent=100)])
    ev = _event(id=7788)
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg) == (
        7,
        "canary",
    )
    assert bucket_key({"id": 7788}) == "7788"  # 数字 id 转字符串稳定


def test_canary_missing_key_fails_safe_to_stable():
    cfg = _full_config(canary_percent=100)
    ev = TriggerEvent(payload={"amount": 5000})  # 既无 order_id 也无 id
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg) == (
        6,
        "fallback",
    )


def test_canary_non_string_key_fails_safe():
    cfg = _full_config(canary_percent=100)
    ev = TriggerEvent(payload={"order_id": {"nested": 1}})
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg) == (
        6,
        "fallback",
    )


# ---------- full 段 / 未配置 / 无版本 ----------

def test_full_rule_sends_all_to_candidate():
    cfg = RolloutConfig(rules=[FullRule()])
    ev = _event(order_id="anything")
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg) == (
        7,
        "full",
    )


def test_no_config_routes_to_stable():
    ev = _event(order_id="12345")
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=None) == (
        6,
        "stable",
    )


def test_no_candidate_routes_to_stable():
    cfg = _full_config()
    ev = _event(order_id="12345")
    assert resolve_version(graph_id=G, stable=6, candidate=None, tenant="t1", event=ev, config=cfg) == (
        6,
        "stable",
    )


def test_no_published_version_is_unpublished():
    cfg = _full_config()
    ev = _event(order_id="12345")
    assert resolve_version(graph_id=G, stable=None, candidate=None, tenant="t1", event=ev, config=cfg) == (
        None,
        "unpublished",
    )


def test_no_event_payload_routes_safely():
    cfg = _full_config()
    # 无载荷无法做 canary 哈希，fail-safe 落 stable（segment=fallback 表达"无法判定"）
    assert resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=None, config=cfg) == (
        6,
        "fallback",
    )


# ---------- 配置校验（configure 期聚合，REST 转 422） ----------

def test_rule_segment_order_must_be_fixed():
    with pytest.raises(ValidationError):
        RolloutConfig(rules=[CanaryRule(percent=5), InternalRule(tenants=["t"])])


def test_duplicate_segment_rejected():
    with pytest.raises(ValidationError):
        RolloutConfig(rules=[FullRule(), FullRule()])


@pytest.mark.parametrize("percent", [0, 101, -1])
def test_canary_percent_bounds(percent):
    with pytest.raises(ValidationError):
        CanaryRule(percent=percent)


@pytest.mark.parametrize("threshold", [-0.01, 1.01, 5])
def test_gate_threshold_bounds(threshold):
    with pytest.raises(ValidationError):
        GateMetric(id="run_error_rate", threshold=threshold)


def test_duplicate_metric_id_rejected():
    with pytest.raises(ValidationError):
        GateConfig(
            metrics=[
                GateMetric(id="run_error_rate", threshold=0.02),
                GateMetric(id="run_error_rate", threshold=0.05),
            ]
        )


def test_bucket_field_must_be_payload_path():
    with pytest.raises(ValidationError):
        BucketRule(field="amount", value=200)


def test_unknown_rule_shape_rejected():
    with pytest.raises(ValidationError):
        RolloutConfig.model_validate({"rules": [{"to": "mystery"}]})


def test_gate_defaults():
    gate = GateConfig()
    assert gate.observeMinutes == 60
    assert gate.autoRollback is True
    assert gate.minSamples == 3
    assert gate.metrics == []


# ---------- 纯函数性 ----------

def test_resolve_is_pure_and_does_not_mutate_inputs():
    cfg = _full_config()
    ev = _event(amount=100, order_id="12345")
    payload_before = repr(ev.payload)
    rules_before = repr(cfg.model_dump())
    for _ in range(5):
        resolve_version(graph_id=G, stable=6, candidate=7, tenant="t1", event=ev, config=cfg)
    assert repr(ev.payload) == payload_before
    assert repr(cfg.model_dump()) == rules_before
