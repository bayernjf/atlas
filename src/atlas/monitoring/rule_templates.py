"""内置告警规则模板目录（docs/59 F-1 权威实现）。

v1 为随代码版本发布的只读目录：无 DB、无 CRUD、不按租户分区、不受
/api/demo/reset 影响（与 template/catalog.py、cards 同一类全局只读基础设施）。
每个模板的 config 是完整 RuleConfig 形状（四段内置规则齐备），必须能通过
alerts.validate_rules；「一键应用」即前端取 config 后走 PUT
/api/monitoring/rules 全量替换，本模块不提供任何写通道。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .alerts import RuleConfig


class RuleTemplateMeta(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any]


def _config(
    *,
    consecutive_threshold: int = 3,
    failure_window: int = 20,
    failure_min_samples: int = 5,
    failure_rate: float = 0.5,
    custom: list[dict[str, Any]] | None = None,
    escalation_ack_minutes: int | None = None,
    recovery_healthy_streak: int = 1,
    recovery_cooldown_minutes: int | None = None,
) -> dict[str, Any]:
    """构造完整 RuleConfig dict（四段内置规则齐备，缺省全开）。"""
    return RuleConfig(
        run_error={"enabled": True},  # type: ignore[arg-type]
        node_failed={"enabled": True},  # type: ignore[arg-type]
        consecutive_failures={"enabled": True, "threshold": consecutive_threshold},  # type: ignore[arg-type]
        failure_rate={  # type: ignore[arg-type]
            "enabled": True,
            "window": failure_window,
            "min_samples": failure_min_samples,
            "rate": failure_rate,
        },
        custom=custom or [],
        escalation_ack_minutes=escalation_ack_minutes,
        recovery_healthy_streak=recovery_healthy_streak,
        recovery_cooldown_minutes=recovery_cooldown_minutes,
    ).model_dump()


RULE_TEMPLATES: tuple[RuleTemplateMeta, ...] = (
    RuleTemplateMeta(
        id="default-balanced",
        name="默认均衡",
        description="内置四规则全开：连续失败 3 次、近 20 次失败率 ≥50% 告警；不自动升级，供一键复位默认。",
        tags=["默认", "均衡"],
        config=_config(),
    ),
    RuleTemplateMeta(
        id="strict-sre",
        name="严格 SRE",
        description="更敏感：连续失败 2 次、近 10 次失败率 ≥30% 即告警；warning 15 分钟未确认升级 critical，连续 2 次健康才恢复、30 分钟冷却抑制抖动。",
        tags=["严格", "升级", "flapping"],
        config=_config(
            consecutive_threshold=2,
            failure_window=10,
            failure_min_samples=3,
            failure_rate=0.3,
            escalation_ack_minutes=15,
            recovery_healthy_streak=2,
            recovery_cooldown_minutes=30,
        ),
    ),
    RuleTemplateMeta(
        id="demo-lenient",
        name="演示宽松",
        description="适合演示/沙盘：连续失败 5 次、近 20 次至少 10 次样本且失败率 ≥80% 才告警，不自动升级，减少噪音。",
        tags=["宽松", "演示"],
        config=_config(
            consecutive_threshold=5,
            failure_window=20,
            failure_min_samples=10,
            failure_rate=0.8,
        ),
    ),
    RuleTemplateMeta(
        id="custom-quickstart",
        name="自定义规则上手",
        description="在内置四规则基础上追加两条自定义表达式示例：任一节点失败即 critical、运行超过 5 秒 warning，可在规则编辑器中再调整。",
        tags=["自定义", "示例", "DSL"],
        config=_config(
            custom=[
                {
                    "cid": "tpl-node-failed",
                    "name": "任一节点失败即告警",
                    "enabled": True,
                    "expression": "{{failedCount}} > 0",
                    "severity": "critical",
                },
                {
                    "cid": "tpl-slow-run",
                    "name": "运行超过 5 秒",
                    "enabled": True,
                    "expression": "{{durationMs}} > 5000",
                    "severity": "warning",
                },
            ]
        ),
    ),
)


def list_rule_templates() -> tuple[RuleTemplateMeta, ...]:
    """返回全部内置规则模板（顺序稳定）。"""
    return RULE_TEMPLATES


def get_rule_template(template_id: str) -> RuleTemplateMeta | None:
    """按 id 取模板；未知 id 返回 None（API 映射 404）。"""
    return next((t for t in RULE_TEMPLATES if t.id == template_id), None)
