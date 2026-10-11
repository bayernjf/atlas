"""docs/65 K-A：数据生命周期 retention 与过期会话清扫。

进程内档天然无保留期语义（进程重启即清空、跨重启不保留数据），本模块只在 PG
档（ATLAS_STORAGE_BACKEND=pg）生效；PG 侧实际 DELETE 在
`PgBackend.prune_expired()`（storage/pg.py），这里负责 env 组装与启动钩子。

长期记忆表 memory_items（docs/14 D35 保留期裁剪安全切片）也走这套钩子，但缺省**不过期**、
不跟随全局保留期，只有显式设置 ATLAS_RETENTION_MEMORY_DAYS 才裁剪（见 _memory_cutoff）。
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

# 长期记忆（docs/14 D35 保留期裁剪安全切片）与上面 7 张运维表刻意不同：
#  - 缺省**不过期**（env 未设置 → cutoff=None，prune 整表跳过），长期记忆是产品核心价值，
#    不能像 runs/投递日志那样到点默认清掉；
#  - **不跟随**全局 ATLAS_RETENTION_DAYS，必须用本变量独立显式开启，否则运维为回收磁盘
#    设的全局保留期会在一次启动清扫里静默删掉用户长期事实/偏好；
#  - 非法值 fail-safe 到"不过期"并 warning（宁保留不误删，与常规表回退默认天数相反）。
MEMORY_TABLE = "memory_items"
MEMORY_DAYS_ENV = "ATLAS_RETENTION_MEMORY_DAYS"


def _memory_cutoff(now: datetime) -> str | None:
    raw = os.environ.get(MEMORY_DAYS_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        days = int(raw)
    except ValueError:
        logger.warning(
            "retention env %s=%r 非整数，长期记忆保留期不启用（fail-safe 为不过期）",
            MEMORY_DAYS_ENV, raw,
        )
        return None
    if days < 1:
        logger.warning(
            "retention env %s=%r 小于 1，长期记忆保留期不启用（至少 1 天；缺省语义为不过期）",
            MEMORY_DAYS_ENV, raw,
        )
        return None
    return (now - timedelta(days=days)).isoformat()


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


def assemble_cutoffs() -> dict[str, str | None]:
    """读 env 组装 {表名: UTC ISO 截止串 | None}；每表保留期至少 1 天（env 负数按 1 计）。

    7 张运维表缺省有默认保留期；长期记忆表 memory_items 缺省为 None（不过期），
    仅当显式设置 ATLAS_RETENTION_MEMORY_DAYS 时才给出截止串（见 _memory_cutoff）。
    """
    now = datetime.now(timezone.utc)
    cutoffs: dict[str, str | None] = {
        table: (now - timedelta(days=_days_for(table))).isoformat()
        for table in TABLE_ENVS
    }
    cutoffs[MEMORY_TABLE] = _memory_cutoff(now)
    return cutoffs


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
