"""通用 SQL 数据适配器客户端（04 §4.7 权威契约 / 06 §6.7 运行时）。

适配器专用出站连接（ATLAS_DATABASE_URL），与平台自身 DATABASE_URL 物理
隔离；未配置时由调用方回退到内置 SQLite demo（demo_engine）。query 走
只读事务并 rollback，execute 提交返 rowcount。SQLAlchemy 异常不抛出，
折叠为 DatabaseAdapterError(DB_SQL_ERROR)。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool

ALLOWED_SCHEMES = ("postgresql+psycopg", "sqlite")
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000


class DatabaseAdapterError(Exception):
    """参数校验/执行失败，code 对应 StructuredError.code。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def seed_demo_orders(engine: Engine) -> None:
    """在内置 demo 库建 orders 表并播种两笔与 mock 订单同形的演示数据。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS orders ("
                "order_id TEXT PRIMARY KEY, reason TEXT, amount INTEGER, status TEXT)"
            )
        )
        conn.execute(text("DELETE FROM orders"))
        conn.execute(
            text(
                "INSERT INTO orders (order_id, reason, amount, status) "
                "VALUES (:order_id, :reason, :amount, :status)"
            ),
            [
                {
                    "order_id": "12345",
                    "reason": "商品破损",
                    "amount": 299,
                    "status": "pending_refund",
                },
                {
                    "order_id": "12346",
                    "reason": "不想要了",
                    "amount": 5000,
                    "status": "pending_refund",
                },
            ],
        )


def demo_engine(seed: bool = True) -> Engine:
    """SQLite 单连接内存库（StaticPool），供离线 demo/单测零依赖使用。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if seed:
        seed_demo_orders(engine)
    return engine


def _validate_sql(sql: object) -> str:
    if not isinstance(sql, str) or not sql.strip():
        raise DatabaseAdapterError("MISSING_PARAMETER", "缺少 sql")
    return sql


def _validate_params(params: object) -> object:
    if params is None:
        return {}
    if isinstance(params, dict):
        return params
    if isinstance(params, list) and all(isinstance(item, dict) for item in params):
        return params
    raise DatabaseAdapterError("INVALID_PARAMETER", "params 必须是对象或对象数组（绑定参数）")


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise DatabaseAdapterError("INVALID_PARAMETER", "limit 必须是 1-1000 的整数")
    if limit < 1 or limit > MAX_LIMIT:
        raise DatabaseAdapterError("INVALID_PARAMETER", "limit 必须在 1-1000 之间")
    return limit


class DatabaseClient:
    def __init__(self, engine: Engine, url: str = "", demo: bool = False) -> None:
        self.engine = engine
        self.url = url
        self.demo = demo
        self.last_operation: dict[str, object] | None = None

    @classmethod
    def from_env(cls) -> "DatabaseClient | None":
        """从 ATLAS_DATABASE_URL 构建连接；未配置返回 None（调用方回退 demo）。"""
        raw = os.getenv("ATLAS_DATABASE_URL", "").strip()
        if not raw:
            return None
        parsed = make_url(raw)
        if parsed.drivername not in ALLOWED_SCHEMES:
            raise ValueError(
                f"ATLAS_DATABASE_URL 不支持的 scheme：{parsed.drivername}，"
                f"允许：{', '.join(ALLOWED_SCHEMES)}"
            )
        return cls(create_engine(raw, pool_pre_ping=True), url=raw)

    @property
    def masked_url(self) -> str:
        if not self.url:
            return "sqlite:///:memory:"
        return make_url(self.url).render_as_string(hide_password=True)

    def query(
        self,
        sql: object,
        params: object = None,
        limit: object = DEFAULT_LIMIT,
    ) -> dict[str, object]:
        sql_text = _validate_sql(sql)
        bound_params = _validate_params(params)
        limit_value = _validate_limit(limit)

        try:
            with self.engine.connect() as conn:
                if self.engine.dialect.name == "postgresql":
                    conn = conn.execution_options(postgresql_readonly=True)
                cursor = conn.execute(text(sql_text), bound_params)
                columns = list(cursor.keys())
                fetched = cursor.fetchmany(limit_value + 1)
                truncated = len(fetched) > limit_value
                rows = [dict(zip(columns, row)) for row in fetched[:limit_value]]
                conn.rollback()
        except DatabaseAdapterError:
            raise
        except SQLAlchemyError as exc:
            raise DatabaseAdapterError("DB_SQL_ERROR", f"SQL 执行失败：{exc}") from exc

        self._record("query", sql_text, row_count=len(rows), truncated=truncated)
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }

    def execute(self, sql: object, params: object = None) -> dict[str, object]:
        sql_text = _validate_sql(sql)
        bound_params = _validate_params(params)

        try:
            with self.engine.begin() as conn:
                cursor = conn.execute(text(sql_text), bound_params)
                rowcount = cursor.rowcount
        except DatabaseAdapterError:
            raise
        except SQLAlchemyError as exc:
            raise DatabaseAdapterError("DB_SQL_ERROR", f"SQL 执行失败：{exc}") from exc

        if rowcount is None or rowcount < 0:
            rowcount = 0
        self._record("execute", sql_text, rowcount=rowcount)
        return {"rowcount": rowcount}

    def reseed_demo(self) -> None:
        """重置内置 demo 库；显式配置的外部库永不被调用方触碰。"""
        if self.demo:
            seed_demo_orders(self.engine)
            self.last_operation = None

    def _record(self, operation: str, sql: str, **extra: object) -> None:
        self.last_operation = {
            "operation": operation,
            "sql": sql,
            "at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
