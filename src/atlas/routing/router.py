"""入站 Router 三段分桶纯函数（M9，docs/20 §4.4 / 19 §2.5；契约 03 `route_decision`、04 §5.16）。

固定求值序（不可重排，models.RolloutConfig 已在配置期校验顺序）：
  internal（租户 allowlist）→ lowValueBucket（payload.amount 低金额业务桶）
  → canary（订单 id 稳定哈希百分比）→ full（全量）；未命中/未配置/无 candidate → stable。

fail-safe：任何无法可靠分桶的情形（桶键缺失/非字符串、金额字段非数字）一律落 stable，
绝不把无法识别的流量推向新版本（19 §2.5 金融灰度安全前提）。纯函数、无 IO、不改计数
（分流计数在 store.RoutingStore.resolve），U55 钉死全部分支。
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import BucketRule, CanaryRule, RolloutConfig, TriggerEvent

# 分桶结果段名（fallback＝本应进灰度但因载荷不可靠 fail-safe 落 stable）
SEGMENT_INTERNAL = "internal"
SEGMENT_BUCKET = "lowValueBucket"
SEGMENT_CANARY = "canary"
SEGMENT_FULL = "full"
SEGMENT_STABLE = "stable"
SEGMENT_FALLBACK = "fallback"
SEGMENT_UNPUBLISHED = "unpublished"


def _dig(payload: dict[str, Any], field: str) -> Any:
    """按点分路径取值；field 约定以 'payload.' 开头（models 已校验），剥前缀后逐级下钻。"""
    path = field[len("payload.") :] if field.startswith("payload.") else field
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _is_number(value: Any) -> bool:
    """金额判定：int/float 但排除 bool（bool 是 int 子类）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def bucket_key(payload: dict[str, Any]) -> str | None:
    """canary/百分比桶的稳定键：payload.order_id 缺省 payload.id；非字符串/缺失返 None。"""
    for key in ("order_id", "id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            # 数字订单号允许（转字符串参与哈希，同一数字稳定）
            return str(value)
    return None


def hash_hit(graph_id: str, key: str, percent: int) -> bool:
    """稳定哈希分桶：sha256('graph_id:key')%100 < percent；同对象恒定不横跳。"""
    digest = hashlib.sha256(f"{graph_id}:{key}".encode("utf-8")).hexdigest()
    return int(digest, 16) % 100 < percent


def _bucket_rule_hit(graph_id: str, rule: BucketRule, payload: dict[str, Any]) -> tuple[bool, bool]:
    """返 (金额条件命中, 是否因缺桶键 fail-safe)。金额命中后按 percent 稳定哈希放行。"""
    amount = _dig(payload, rule.field)
    if not _is_number(amount) or amount > rule.value:
        return False, False
    if rule.percent >= 100:
        return True, False
    key = bucket_key(payload)
    if key is None:
        return False, True  # 金额命中但无法稳定取键 → fail-safe
    return hash_hit(graph_id, key, rule.percent), False


def resolve_version(
    *,
    graph_id: str,
    stable: int | None,
    candidate: int | None,
    tenant: str,
    event: TriggerEvent | None,
    config: RolloutConfig | None,
) -> tuple[int | None, str]:
    """按固定序解析入站事件应走的发布版本，返 (releaseVersion, segment)。

    - 未配置 config 或无 candidate：(stable, "stable")；stable 亦无 → (None, "unpublished")
    - internal 命中 → (candidate, "internal")
    - lowValueBucket 金额命中 → (candidate, "lowValueBucket")；缺桶键 fail-safe → (stable, "fallback")
    - canary 哈希命中 → (candidate, "canary")；桶键缺失/未命中 → stable（缺失记 "fallback"）
    - full 段存在 → (candidate, "full")
    - 其余 → (stable, "stable")
    """
    if stable is None and candidate is None:
        return None, SEGMENT_UNPUBLISHED
    if config is None or candidate is None:
        return stable, SEGMENT_STABLE

    payload = event.payload if event is not None else {}

    internal = config.rule(SEGMENT_INTERNAL)
    if internal is not None and tenant in internal.tenants:
        return candidate, SEGMENT_INTERNAL

    bucket = config.rule(SEGMENT_BUCKET)
    if bucket is not None:
        hit, unsafe = _bucket_rule_hit(graph_id, bucket, payload)
        if unsafe:
            return stable, SEGMENT_FALLBACK
        if hit:
            return candidate, SEGMENT_BUCKET

    canary: CanaryRule | None = config.rule(SEGMENT_CANARY)
    if canary is not None:
        key = bucket_key(payload)
        if key is None:
            return stable, SEGMENT_FALLBACK
        if hash_hit(graph_id, key, canary.percent):
            return candidate, SEGMENT_CANARY
        return stable, SEGMENT_STABLE

    if config.rule(SEGMENT_FULL) is not None:
        return candidate, SEGMENT_FULL

    return stable, SEGMENT_STABLE
