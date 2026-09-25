"""docs/65 K-A：数据生命周期 retention 与过期会话清扫。

进程内档天然无保留期语义（进程重启即清空、跨重启不保留数据），本模块只在 PG
档（ATLAS_STORAGE_BACKEND=pg）生效；PG 侧实际 DELETE 在
`PgBackend.prune_expired()`（storage/pg.py），这里负责 env 组装与启动钩子。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# 表 → 分表保留期 env（缺省回退全局 ATLAS_RETENTION_DAYS，再回退表默认值）
TABLE_ENVS: dict[str, str] = {
    "audit_events": "ATLAS_RETENTION_AUDIT_DAYS",
    "runs": "ATLAS_RETENTION_RUNS_DAYS",
    "webhook_deliveries": "ATLAS_RETENTION_WEBHOOK_DELIVERIES_DAYS",
    "interruptions": "ATLAS_RETENTION_INTERRUPTIONS_DAYS",
    "graph_versions": "ATLAS_RETENTION_GRAPH_VERSIONS_DAYS",
    "openapi_imports": "ATLAS_RETENTION_OPENAPI_IMPORTS_DAYS",
    "message_deliveries": "ATLAS_RETENTION_MESSAGE_DELIVERIES_DAYS",
}

DEFAULT_DAYS: dict[str, int] = {
    "audit_events": 90,
    "runs": 90,
    "webhook_deliveries": 30,
    "interruptions": 30,
    "graph_versions": 365,
    "openapi_imports": 365,
    "message_deliveries": 30,
}

_GLOBAL_DAYS_ENV = "ATLAS_RETENTION_DAYS"
_GLOBAL_DAYS_DEFAULT = 90


def _days_for(table: str) -> int:
    env = TABLE_ENVS[table]
    raw = os.environ.get(env) or os.environ.get(_GLOBAL_DAYS_ENV)
    if raw is None:
        return DEFAULT_DAYS[table]
    try:
        value = int(raw)
    except ValueError:
        logger.warning("retention env %s=%r 非整数，回退默认 %d 天", env, raw, DEFAULT_DAYS[table])
        return DEFAULT_DAYS[table]
    return max(1, value)


def assemble_cutoffs() -> dict[str, str]:
    """读 env 组装 {表名: UTC ISO 截止串}；每表保留期至少 1 天（env 负数按 1 计）。"""
    now = datetime.now(timezone.utc)
    return {
        table: (now - timedelta(days=_days_for(table))).isoformat()
        for table in TABLE_ENVS
    }


def run_retention_once() -> dict[str, int] | None:
    """服务启动钩子（lifespan）：PG 档执行一次 retention，失败只 warning 不阻断启动。

    进程内档返回 None（no-op）。与 recover_pending 同款语义（docs/65 §2 接线）：
    运维清扫绝不挡服务起。
    """
    from atlas.iam.registry import STORAGE_BACKEND

    if STORAGE_BACKEND != "pg":
        return None
    try:
        from atlas.storage.pg import get_pg_backend

        return get_pg_backend().prune_expired(assemble_cutoffs())
    except Exception as exc:  # 无 DATABASE_URL / PG 未就绪时不阻断启动
        logger.warning("retention 扫描跳过：%s", exc)
        return None
