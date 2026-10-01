"""可调策略参数白名单（docs/88 §4 ``TunableParam``，§2 D-4）。

v1 **恰四条**硬编码常量，对应 docs/88 §0.4 实测出的四处散落参数：
`approval_limit`（图全局变量）、节点 `confidenceThreshold`、监控 `RuleConfig` 阈值、
每图 `GateConfig` 阈值。

**fail-closed**：白名单外的任何 `param_key` 一律拒绝（`is_whitelisted` 为假即整份候选作废，
不做「部分采纳」）；白名单内但取值越 `bounds` 同样作废。

`promptTemplate` **不在白名单**——docs/88 D-4 特别条款：提示词是无界文本，v1 只允许产出
「建议文本」供人阅读，不写入任何节点 config（见 ``candidate.ReflectionCandidate.prompt_suggestions``）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

TunableScope = Literal["graph_variable", "node_config", "monitor_rule", "gate_config"]


class Bounds(BaseModel):
    """允许的取值区间（闭区间）；候选越界即整份作废。"""

    min: float
    max: float


class TunableParam(BaseModel):
    """白名单条目。

    ``current`` 是**代码里的参考缺省**，不是运行时实况：图全局变量读作字符串 ``"500"``
    （`template/graphs.py:43`），节点/规则/门控读作数值。门控阈值在 `GateConfig.metrics`
    缺省为空列表（`routing/models.py:104`），没有代码缺省，故 `current=None`——真值在
    候选生成时由调用方按图读取。
    """

    key: str
    scope: TunableScope
    current: float | str | None
    bounds: Bounds


# docs/88 §0.4 的四条，逐字对应，不增不减。
TUNABLE_WHITELIST: dict[str, TunableParam] = {
    "approval_limit": TunableParam(
        key="approval_limit",
        scope="graph_variable",
        current="500",
        bounds=Bounds(min=0, max=1_000_000),
    ),
    "node.confidenceThreshold": TunableParam(
        key="node.confidenceThreshold",
        scope="node_config",
        current=0.6,
        bounds=Bounds(min=0.0, max=1.0),
    ),
    "monitor.failure_rate.rate": TunableParam(
        key="monitor.failure_rate.rate",
        scope="monitor_rule",
        current=0.5,
        bounds=Bounds(min=0.0, max=1.0),
    ),
    "gate.run_error_rate": TunableParam(
        key="gate.run_error_rate",
        scope="gate_config",
        current=None,
        bounds=Bounds(min=0.0, max=1.0),
    ),
}


def is_whitelisted(param_key: str) -> bool:
    """白名单内为真；否则整份候选作废（fail-closed）。"""
    return param_key in TUNABLE_WHITELIST


def in_bounds(param_key: str, value: object) -> bool:
    """取值在闭区间内为真；非数值或未知键一律为假（fail-closed）。"""
    param = TUNABLE_WHITELIST.get(param_key)
    if param is None:
        return False
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return param.bounds.min <= float(value) <= param.bounds.max
