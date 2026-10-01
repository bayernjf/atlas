"""只读证据包装配（docs/88 §4 ``ReflectionEvidence``，D-3）。

一次反思 pass 的输入：把**已存在**的观测产物读出来拼成一份纯投影——运行记录、
发布门禁报告、影子对比、未闭合告警。**不新增存储、不新增采集**，只是换一个视角读。

证据只读，且**不参与任何放量/发布判定**（routing 的唯一放量路径仍是手动 promote，
见 `routing/store.py`）；这里的数字仅供人看与供候选生成。

样本不足（无运行记录且无告警且无报告）时返回 ``None``：调用方据此产出
``status="no_evidence"`` 的 `ReflectionReport`，不生成候选（fail-closed）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..monitoring import is_healthy, summarize, summarize_business

# 单次 pass 读取的运行条数上限；与 monitoring ring 容量（RUN_RING_SIZE=200）对齐。
RUN_LIMIT = 200
# 证据里保留的失败节点/告警条数上限，防报告过长。
FAILED_NODE_LIMIT = 10
ALERT_LIMIT = 10


class VersionStat(BaseModel):
    """按已发布版本分组的运行与业务指标（docs/88 §4 `per_version`）。

    `error_rate` 与监控一致，取 `1 - success_rate`（`monitoring.is_healthy` 判定）；
    `business` 为该版本有业务结果的运行的三率段，一条都没有时为 None（源：
    `monitoring.summarize_business` 的 `per_version`）。
    """

    version: int | None
    runs: int
    error_rate: float | None = None
    business: dict[str, float | None] | None = None


class FailedNodeStat(BaseModel):
    """失败节点计数（源：`monitoring.summarize`）。"""

    node_id: str
    count: int


class ReplayStat(BaseModel):
    """最近一次发布门禁报告的回放结果（源：`replay.compare`，经 gate 报告聚合）。

    `report_store.list_summary` 已剥离逐例 cases，故只保留通过数/总数；
    门禁的 `passed` 即逐例 `matches` 为真的条数（`recording/gate.py`）。
    """

    matches: int
    total: int


class ShadowStat(BaseModel):
    """影子对比汇总（源：`ShadowComparison`）。

    `total` 为带人工结果的影子运行条数；`match` 三态——全对为 True、有任一不符为
    False、无可比样本为 None（同 docs/88 §4 的 `true|false|null`）。
    """

    match: bool | None
    total: int


class AlertStat(BaseModel):
    """未闭合告警（源：`Alert`）；`action` 取 `action["type"]`，无动作为 None。"""

    rule: str
    count: int
    action: str | None = None


class EvidenceWindow(BaseModel):
    since: str | None = None
    until: str | None = None


class ReflectionEvidence(BaseModel):
    tenant_id: str
    graph_id: str
    base_version: int
    window: EvidenceWindow
    per_version: list[VersionStat] = []
    failed_nodes: list[FailedNodeStat] = []
    replay: ReplayStat | None = None
    shadow: ShadowStat | None = None
    alerts: list[AlertStat] = []

    def is_empty(self) -> bool:
        """无任何证据（无运行、无报告、无告警）——据此判 no_evidence。"""
        return not self.per_version and self.replay is None and not self.alerts


def build_evidence(
    services: Any,
    *,
    tenant_id: str,
    graph_id: str,
    base_version: int,
    since: str | None = None,
    until: str | None = None,
    run_limit: int = RUN_LIMIT,
) -> ReflectionEvidence | None:
    """装配一次 pass 的证据包；完全无证据时返回 None。

    ``services`` 为租户服务包（`iam.registry.TenantServices`），只读其中
    `monitoring` / `report_store` / `shadow_store` 三个 ring。
    """
    runs = list(services.monitoring.list_runs(graph_id, limit=run_limit))
    reports = list(services.report_store.list_summary(graph_id))
    shadows = list(services.shadow_store.list(graph_id, limit=run_limit))
    alerts = list(services.monitoring.list_alerts())

    per_version = _per_version(runs, graph_id)
    failed_nodes = _failed_nodes(runs)
    replay = _replay(reports)
    shadow = _shadow(shadows)
    alert_stats = _alerts(alerts, graph_id)

    evidence = ReflectionEvidence(
        tenant_id=tenant_id,
        graph_id=graph_id,
        base_version=base_version,
        window=EvidenceWindow(since=since, until=until),
        per_version=per_version,
        failed_nodes=failed_nodes,
        replay=replay,
        shadow=shadow,
        alerts=alert_stats,
    )
    return None if evidence.is_empty() else evidence


def _per_version(runs: list[Any], graph_id: str) -> list[VersionStat]:
    """每个已发布版本一行——**含无业务结果的运行**（否则纯编排图永远无证据）。

    `summarize_business` 的 `per_version` 只覆盖有业务结果的运行，故这里按运行自行分组、
    再把三率并进来（键相同即为同一版本）。
    """
    if not runs:
        return []
    rates = {
        row.get("resolved_version"): row
        for row in summarize_business(runs).get("per_version", [])
        if row.get("graph_id") == graph_id
    }
    grouped: dict[int | None, list[Any]] = {}
    for run in runs:
        grouped.setdefault(run.resolved_version, []).append(run)
    stats: list[VersionStat] = []
    for version, version_runs in grouped.items():
        healthy = sum(1 for run in version_runs if is_healthy(run))
        row = rates.get(version)
        stats.append(
            VersionStat(
                version=version,
                runs=len(version_runs),
                error_rate=1 - healthy / len(version_runs),
                business=(
                    {
                        "samples": row["samples"],
                        "auto_refund_rate": row["auto_refund_rate"],
                        "manual_escalation_rate": row["manual_escalation_rate"],
                        "refund_amount_diff_rate": row["refund_amount_diff_rate"],
                    }
                    if row
                    else None
                ),
            )
        )
    return stats


def _failed_nodes(runs: list[Any]) -> list[FailedNodeStat]:
    if not runs:
        return []
    summary = summarize(runs)
    return [
        FailedNodeStat(node_id=row["node_id"], count=int(row["count"]))
        for row in summary.get("failed_nodes", [])[:FAILED_NODE_LIMIT]
    ]


def _replay(reports: list[dict[str, Any]]) -> ReplayStat | None:
    """取最近一次门禁报告（list_summary 已倒序）。"""
    if not reports:
        return None
    latest = reports[0]
    return ReplayStat(matches=int(latest.get("passed") or 0), total=int(latest.get("total") or 0))


def _shadow(shadows: list[dict[str, Any]]) -> ShadowStat | None:
    compared = [
        item.get("comparison")
        for item in shadows
        if isinstance(item.get("comparison"), dict) and item["comparison"].get("match") is not None
    ]
    if not compared:
        return None
    return ShadowStat(match=all(row.get("match") is True for row in compared), total=len(compared))


def _alerts(alerts: list[Any], graph_id: str) -> list[AlertStat]:
    rows = [
        AlertStat(rule=alert.rule_id, count=int(alert.count), action=_action_type(alert.action))
        for alert in alerts
        if alert.graph_id == graph_id and alert.status != "resolved"
    ]
    rows.sort(key=lambda row: row.count, reverse=True)
    return rows[:ALERT_LIMIT]


def _action_type(action: dict | None) -> str | None:
    if not isinstance(action, dict):
        return None
    value = action.get("type")
    return value if isinstance(value, str) else None
