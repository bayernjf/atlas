"""A/B 实验对照判定（打包 BR＝D77，docs/134；docs/127 里程碑 G）。

实验＝叠加在灰度面上的**意图/判定层**：分流零新代码复用 RoutingStore canary
（同一 `bucket_key`/`hash_hit`/fail-safe，BR-D1；`router.py` 一字不改），本模块只承载
实验实体、臂统计与显著性判定。判定＝两比例 z 检验（G-P1 拍板，α=0.05 双侧，
stdlib `math.erf` 零新依赖，BR-D8）；三指标与 gate 同 id 同分母（BR-D3）：
`business_rates` 公开化后由本模块复用，gate 行为零改动。

纯函数段（`arm_stats`/`two_proportion_z`/`judge`/`required_samples`）无 IO；
判定窗口＝实验 createdAt 起累计（非 gate 的滚动 observeMinutes——安全门要新数据，
判定要全量数据）。判定只读建议、conclude 人决（BR-D4）：verdict 与 action
不一致照实记录，不存在任何自动推广通道。
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..monitoring.metrics import is_healthy
from .gate import business_rates
from .models import GateMetricId, RolloutConfig

# α=0.05 双侧的 z 临界值（|z| > 1.96 判显著）
Z_ALPHA = 1.96
# 检验功效 power=0.8 对应的 z_β（单侧；required_samples 指导性估算用）
Z_BETA = 0.8416

ExperimentStatus = Literal["running", "concluded"]
ConclusionAction = Literal["adopt_a", "adopt_b"]
VerdictStatus = Literal["collecting", "inconclusive", "significant"]


class Verdict(BaseModel):
    """主指标显著性判定（GET detail 活算为建议；conclude 时刻冻结入库）。

    三态：collecting（任一臂样本 < minSamples，不指 winner）→ inconclusive
    （样本足但 |z| ≤ 1.96）→ significant（|z| > 1.96，winner＝率更低臂，
    三指标皆"低者为优"）。reason 全中文（配置校验中文聚合先例，BR-D8）。
    """

    status: VerdictStatus
    winner: Literal["A", "B"] | None = None
    metric: GateMetricId
    rateA: float | None = None
    rateB: float | None = None
    z: float | None = None
    pValue: float | None = None
    reason: str = ""


class Experiment(BaseModel):
    """A/B 实验实体（docs/134 §2.1；camelCase 直进直出 REST，GateMetric 先例）。

    生命周期三段：POST 创建即 running（写配置＋启动分流一步完成，无 draft 中间态）
    → GET detail 活判定 → POST conclude（冻结 verdict＋action＋concludedAt）。
    priorConfig＝启动前 rollout 配置快照（conclude 恢复用，BR-D5）。
    """

    id: str  # exp-N（内存档进程内计数；PG 档走全局 storage_id_seq）
    graphId: str
    versionA: int
    versionB: int
    trafficPercent: int = Field(default=50, ge=1, le=99)  # canary 百分比＝B 臂流量
    primaryMetric: GateMetricId
    minSamples: int = Field(default=30, ge=1)  # 每臂主指标样本门（固定地平线纪律）
    status: ExperimentStatus = "running"
    priorConfig: RolloutConfig | None = None
    verdict: Verdict | None = None
    conclusionAction: ConclusionAction | None = None
    conclusionReason: str | None = None
    createdBy: str = ""
    createdAt: str
    concludedAt: str | None = None


class ArmStats(BaseModel):
    """单臂统计（判定输入；计数供 z 检验，率供展示；分母与 gate 同口径，BR-D3）。

    samples＝臂内全部运行（resolved_version == 版本；error 分母）；
    businessSamples＝有业务结果的运行（两业务率分母，`business_rates` 同一实现）；
    cancelled 计入健康（`is_healthy` 同源）。
    """

    samples: int = 0
    businessSamples: int = 0
    errorCount: int = 0
    manualCount: int = 0
    diffCount: int = 0
    errorRate: float | None = None
    manualRate: float | None = None
    diffRate: float | None = None


def arm_stats(runs: list[Any], version: int) -> ArmStats:
    """按 resolved_version 取臂并算三率口径计数；rate 经 `business_rates` 同一实现（BR-D3）。"""
    arm = [run for run in runs if getattr(run, "resolved_version", None) == version]
    samples = len(arm)
    error_count = sum(1 for run in arm if not is_healthy(run))
    manual_rate, diff_rate, business_samples = business_rates(arm)
    business = [run for run in arm if getattr(run, "business", None) is not None]
    return ArmStats(
        samples=samples,
        businessSamples=business_samples,
        errorCount=error_count,
        manualCount=sum(1 for run in business if run.business.manual_escalated),
        diffCount=sum(1 for run in business if run.business.amount_diff),
        errorRate=(error_count / samples) if samples else None,
        manualRate=manual_rate,
        diffRate=diff_rate,
    )


def primary_counts(stats: ArmStats, metric: GateMetricId) -> tuple[int, int]:
    """主指标的 (事件数 x, 样本数 n)：error 分母＝臂内全部、业务率分母＝有业务结果。"""
    if metric == "run_error_rate":
        return stats.errorCount, stats.samples
    if metric == "manual_escalation_rate":
        return stats.manualCount, stats.businessSamples
    return stats.diffCount, stats.businessSamples  # refund_amount_diff_rate


def _phi(x: float) -> float:
    """标准正态分布 CDF（stdlib `math.erf` 实现，零新依赖）。"""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def two_proportion_z(
    x_a: int, n_a: int, x_b: int, n_b: int
) -> tuple[float | None, float | None]:
    """两比例 z 检验（pooled，双侧）：返 (z, p_value)；退化返 (None, None)。

    pooled `p=(xA+xB)/(nA+nB)`，`se=√(p(1-p)(1/nA+1/nB))`，`z=(pB−pA)/se`，
    双侧 `p=2(1−Φ(|z|))`。退化＝任一臂 n=0 或 se=0（两臂全无事件/全事件，
    检验统计量无定义）→ (None, None) 不炸（G-P1；与卡方 2×2 数学等价 z²=χ²）。
    """
    if n_a <= 0 or n_b <= 0:
        return None, None
    p_pool = (x_a + x_b) / (n_a + n_b)
    se = math.sqrt(p_pool * (1.0 - p_pool) * (1.0 / n_a + 1.0 / n_b))
    if se == 0.0:
        return None, None
    z = ((x_b / n_b) - (x_a / n_a)) / se
    p_value = 2.0 * (1.0 - _phi(abs(z)))
    return z, p_value


def judge(exp: Experiment, stats_a: ArmStats, stats_b: ArmStats) -> Verdict:
    """三态判定（docs/134 §2.3）：样本不足 collecting → |z|≤1.96 inconclusive → significant。

    winner＝主指标率**更低**的臂（三指标皆低者为优）；collecting/inconclusive
    一律不指 winner。判定为建议性（BR-D4），人可在 conclude 时改判。
    """
    metric = exp.primaryMetric
    x_a, n_a = primary_counts(stats_a, metric)
    x_b, n_b = primary_counts(stats_b, metric)
    rate_a = (x_a / n_a) if n_a else None
    rate_b = (x_b / n_b) if n_b else None
    if n_a < exp.minSamples or n_b < exp.minSamples:
        return Verdict(
            status="collecting",
            metric=metric,
            rateA=rate_a,
            rateB=rate_b,
            reason=(
                f"样本采集中：主指标 {metric} 每臂需 ≥{exp.minSamples}，"
                f"当前 A={n_a}、B={n_b}；达线前不指胜臂"
                "（固定地平线纪律：勿在样本不足时凭波动提前 conclude）"
            ),
        )
    z, p_value = two_proportion_z(x_a, n_a, x_b, n_b)
    if z is None or p_value is None:
        return Verdict(
            status="inconclusive",
            metric=metric,
            rateA=rate_a,
            rateB=rate_b,
            reason="两臂主指标比例相同，检验统计量退化（se=0），无法判定显著性",
        )
    if abs(z) <= Z_ALPHA:
        return Verdict(
            status="inconclusive",
            metric=metric,
            rateA=rate_a,
            rateB=rate_b,
            z=z,
            pValue=p_value,
            reason=(
                f"差异未达显著：|z|={abs(z):.2f} ≤ {Z_ALPHA}（α=0.05 双侧，"
                f"p={p_value:.3f}），不指胜臂"
            ),
        )
    winner: Literal["A", "B"] = "A" if (rate_a or 0.0) < (rate_b or 0.0) else "B"
    return Verdict(
        status="significant",
        winner=winner,
        metric=metric,
        rateA=rate_a,
        rateB=rate_b,
        z=z,
        pValue=p_value,
        reason=(
            f"差异显著：|z|={abs(z):.2f} > {Z_ALPHA}（α=0.05 双侧，p={p_value:.3f}）；"
            f"{winner} 臂主指标率 {(_format_rate(rate_a if winner == 'A' else rate_b))} "
            f"低于另一臂（低率为优）"
        ),
    )


def required_samples(baseline_rate: float, mde: float) -> int | None:
    """每臂近似所需样本（α=0.05 双侧、power=0.8；GET detail 指导性展示，不做闸门）。

    `n=2(z_α/2+z_β)²·p̄(1−p̄)/δ²`，`p̄=baseline+δ/2`；
    baseline∉(0,1) 开区间或 mde≤0 或 p̄≥1 → None（无法给出有意义估算）。
    """
    if baseline_rate <= 0.0 or baseline_rate >= 1.0 or mde <= 0.0:
        return None
    p_bar = baseline_rate + mde / 2.0
    if p_bar <= 0.0 or p_bar >= 1.0:
        return None
    n = 2.0 * (Z_ALPHA + Z_BETA) ** 2 * p_bar * (1.0 - p_bar) / (mde**2)
    return math.ceil(n)


def _format_rate(rate: float | None) -> str:
    return f"{rate:.1%}" if rate is not None else "—"
