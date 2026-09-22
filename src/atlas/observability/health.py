"""就绪探针（docs/34 §五 P1；compose healthcheck / 反代就绪判断用）。

/health 是存活探针（进程在跑即 200），/ready 是就绪探针（依赖可用才 200）：
- 内存档无外部依赖，恒就绪；
- PG 档对数据库执行 SELECT 1，失败返 503，负载均衡据此摘流。

探针不鉴权（编排系统/反代不带 token），不泄露任何业务数据。
"""

from __future__ import annotations

from typing import Any

from atlas.iam.registry import STORAGE_BACKEND


def check_ready() -> tuple[bool, dict[str, Any]]:
    """返回 (是否就绪, 探针详情)。任何异常都折算为未就绪，绝不向调用方抛错。"""
    if STORAGE_BACKEND == "pg":
        try:
            from sqlalchemy import text

            from atlas.storage.pg import get_pg_backend

            engine = get_pg_backend().engine
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True, {"storage": "pg", "database": "ok"}
        except Exception as exc:  # 就绪探针必须 fail-closed 且不抛栈
            return False, {"storage": "pg", "database": "unavailable", "error": type(exc).__name__}
    return True, {"storage": "memory", "database": "n/a"}
