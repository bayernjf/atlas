"""入站路由与灰度发布模型（M9，docs/20 §4.4 / ADR T22；契约 04 §5.16、03 `rollout_config`）。

形状来源 docs/19 §2.3.3（提案转权威）。v1 **不做 when 表达式解析器**：19 JSON 里的
`"when": "tenant in allowlist"` / `"when": "payload.amount <= 200"` 字符串条件落为
结构化字段（InternalRule.tenants / BucketRule.field+op+value），求值在 `router.py`。

字段命名照 19 §2.3.3 JSON 与 M7 Envelope 先例用 camelCase（REST PUT/GET 直收直发，
前端与文档字节一致）；配置校验在模型层聚合中文 ValueError，REST 层转 422。
纯 stdlib + pydantic，零新依赖。
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, model_validator

# 入站渠道（v1 仅经现有 /run[/stream] 承载，无真实 ingress 服务器，D32 沙盘）
Channel = Literal["api", "webhook", "im", "embed"]

# 门控业务/系统指标 id（19 §2.3.3 gate.metrics；阈值随每图 RolloutConfig，不进全局 RuleConfig）
GateMetricId = Literal[
    "run_error_rate",
    "manual_escalation_rate",
    "refund_amount_diff_rate",
]

# rollout 运行态（store.py 状态机）
RolloutStatus = Literal["idle", "canary", "full", "rolled_back"]

_SEGMENT_ORDER = ("internal", "lowValueBucket", "canary", "full")


class TriggerEvent(BaseModel):
    """入站触发事件（03 `route_decision`）；tenant 由会话 Principal 定，不进请求体。"""

    channel: Channel = "api"
    payload: dict[str, Any] = Field(default_factory=dict)


class InternalRule(BaseModel):
    """内部租户 allowlist 段：命中租户的事件全量走 candidate。"""

    to: Literal["internal"] = "internal"
    tenants: list[str] = Field(default_factory=list)


class BucketRule(BaseModel):
    """业务桶段（金融硬条款：按业务桶而非纯百分比）：v1 单一低金额桶。

    field 为 payload 内点分路径（契约固定 "payload.amount"）；命中金额条件后按
    percent 稳定哈希放行（默认 100 全进），桶键缺失且 percent<100 时 fail-safe 落 stable。
    """

    to: Literal["lowValueBucket"] = "lowValueBucket"
    field: str = "payload.amount"
    op: Literal["<="] = "<="
    value: float
    percent: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def _check_field(self) -> "BucketRule":
        if not self.field.startswith("payload."):
            raise ValueError("低金额桶 field 必须是 payload.<键> 路径（v1 固定 payload.amount）")
        return self


class CanaryRule(BaseModel):
    """百分比灰度段：订单 id 哈希稳定分桶，同一对象不跨版本跳动。"""

    to: Literal["canary"] = "canary"
    percent: int = Field(ge=1, le=100)


class FullRule(BaseModel):
    """全量放量段（promote 后生效）。"""

    to: Literal["full"] = "full"


RolloutRule = Union[InternalRule, BucketRule, CanaryRule, FullRule]
_RULE_ADAPTER: TypeAdapter[Any] = TypeAdapter(RolloutRule)


class GateMetric(BaseModel):
    """单条门控指标阈值（19 §2.3.3）。

    compareWith 为对照 stable 版本号（int；19 JSON 的 "v6" 是记号示意，落码对齐 M6/M10
    无 v 前缀口径）；minSamples 缺省取 GateConfig.minSamples。
    """

    id: GateMetricId
    threshold: float = Field(ge=0.0, le=1.0)
    compareWith: int | None = None
    minSamples: int | None = Field(default=None, ge=1)


class GateConfig(BaseModel):
    """门控配置：观察窗 + 自动回滚开关 + 指标阈值（03 `rollout_config` gate 段）。"""

    observeMinutes: int = Field(default=60, ge=1)
    autoRollback: bool = True
    minSamples: int = Field(default=3, ge=1)
    metrics: list[GateMetric] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_metrics_unique(self) -> "GateConfig":
        ids = [metric.id for metric in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("gate.metrics 指标 id 不可重复")
        return self


class RolloutConfig(BaseModel):
    """灰度发布配置（19 §2.3.3 rollout；v1 strategy/inFlightPolicy 各仅一个合法值）。"""

    strategy: Literal["progressive"] = "progressive"
    rules: list[RolloutRule] = Field(default_factory=list)
    gate: GateConfig = Field(default_factory=GateConfig)
    inFlightPolicy: Literal["pin-to-version"] = "pin-to-version"

    @model_validator(mode="after")
    def _check_rule_order(self) -> "RolloutConfig":
        # 固定序 internal→lowValueBucket→canary→full，每类至多一个、顺序不可重排。
        seen: list[str] = []
        for rule in self.rules:
            segment = rule.to  # type: ignore[attr-defined]
            if segment in seen:
                raise ValueError(f"灰度规则段 {segment} 重复，每段至多一条")
            seen.append(segment)
        if seen != sorted(seen, key=_SEGMENT_ORDER.index):
            raise ValueError(
                "灰度规则段顺序必须为 internal→lowValueBucket→canary→full（固定序，不可重排）"
            )
        return self

    def rule(self, segment: str) -> Any:
        """按段名取规则（无该段返 None）。"""
        return next((rule for rule in self.rules if rule.to == segment), None)  # type: ignore[attr-defined]


def parse_rule(raw: Any) -> RolloutRule:
    """判别式解析单条规则（to 字段为 discriminator）；非法形状抛 pydantic ValidationError。"""
    return _RULE_ADAPTER.validate_python(raw)
