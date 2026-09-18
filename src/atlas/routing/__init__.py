"""入站路由与灰度发布（M9，docs/20 §4.4 / ADR T22；契约 04 §5.16、06 §6.17）。

- models：TriggerEvent / RolloutConfig 四段规则 / GateConfig（v1 无 when DSL）
- router：resolve_version 固定序三段分桶纯函数 + 稳定哈希
- store：RoutingStore 每图 rollout 状态机
- gate：evaluate_after_run 指标门控自动回滚（批 3）
"""

from .models import (
    BucketRule,
    CanaryRule,
    FullRule,
    GateConfig,
    GateMetric,
    InternalRule,
    RolloutConfig,
    RolloutStatus,
    TriggerEvent,
    parse_rule,
)
from .router import (
    SEGMENT_BUCKET,
    SEGMENT_CANARY,
    SEGMENT_FALLBACK,
    SEGMENT_FULL,
    SEGMENT_INTERNAL,
    SEGMENT_STABLE,
    SEGMENT_UNPUBLISHED,
    bucket_key,
    hash_hit,
    resolve_version,
)
from .store import RolloutError, RolloutState, RoutingStore

__all__ = [
    "BucketRule",
    "CanaryRule",
    "FullRule",
    "GateConfig",
    "GateMetric",
    "InternalRule",
    "RolloutConfig",
    "RolloutError",
    "RolloutState",
    "RolloutStatus",
    "RoutingStore",
    "TriggerEvent",
    "parse_rule",
    "SEGMENT_BUCKET",
    "SEGMENT_CANARY",
    "SEGMENT_FALLBACK",
    "SEGMENT_FULL",
    "SEGMENT_INTERNAL",
    "SEGMENT_STABLE",
    "SEGMENT_UNPUBLISHED",
    "bucket_key",
    "hash_hit",
    "resolve_version",
]
