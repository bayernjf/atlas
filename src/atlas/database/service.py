"""通用 SQL 数据适配器客户端（04 §4.7 权威契约 / 06 §6.7 运行时）。

适配器专用出站连接（ATLAS_DATABASE_URL），与平台自身 DATABASE_URL 物理
隔离；未配置时由调用方回退到内置 SQLite demo（demo_engine）。query 走
只读事务并 rollback，execute 提交返 rowcount。SQLAlchemy 异常不抛出，
折叠为 DatabaseAdapterError(DB_SQL_ERROR)。

只读强制（docs/32 §4）：``query`` 通道在 SQLite/PG 两档都先过静态 SQL
审查（:func:`atlas.database.guard.assert_read_only_sql`，仅放行单条
SELECT/WITH…SELECT，拒多语句与写/DDL），PG 档另叠加连接级
postgresql_readonly 纵深；由外部 ATLAS_DATABASE_URL 构建的 client 标记
read_only=True，``execute`` 在触库前 fail-closed 抛 DB_WRITE_FORBIDDEN；
内置 demo engine（demo=True）read_only=False，其建表/播种走 engine 直连
不经适配器 execute，不受影响。
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from datetime import datetime, timezone

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from .guard import ReadOnlyViolation, assert_read_only_sql

# 进程内已创建的 demo 临时目录；dispose_demo_engine 移除单个，进程退出兜底全清。
_DEMO_TEMP_DIRS: list[str] = []


def _cleanup_demo_temp_dirs() -> None:
    for tmp_dir in list(_DEMO_TEMP_DIRS):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    _DEMO_TEMP_DIRS.clear()


atexit.register(_cleanup_demo_temp_dirs)

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
    """SQLite 临时文件库（WAL＋多连接池），供离线 demo/单测零依赖使用。

    项目级审查发现 #10（docs/125 §2.3，U1387）：原 ``:memory:`` ＋ StaticPool
    单连接令 SSE worker 线程与请求线程共享同一连接/事务。现每次在 mkdtemp
    临时目录内建独立文件库（调用一次＝全新独立库，现语义不变），默认
    QueuePool 多连接＝真隔离，busy timeout 30s（写串行时等待而非立刻
    ``database is locked``），connect 监听器对每条连接开 WAL/NORMAL。

    ``check_same_thread=False`` 保留：QueuePool 会把同一条底层连接串行交给
    不同线程复用，pysqlite 的线程亲和校验必须放开（池仍保证一条连接同一时刻
    只被一个线程使用，隔离来自多连接而非单连接共享事务）。临时目录由
    :func:`dispose_demo_engine` 释放，未显式释放时进程退出 atexit 兜底清理。
    """
    tmp_dir = tempfile.mkdtemp(prefix="atlas-demo-db-")
    _DEMO_TEMP_DIRS.append(tmp_dir)
    db_path = os.path.join(tmp_dir, "demo.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    if seed:
        seed_demo_orders(engine)
    return engine


def dispose_demo_engine(engine: Engine) -> None:
    """关闭 demo engine 连接池并删除其临时目录（审查发现 #10）。

    非临时文件库（外部 SQLite 路径等）只 dispose、不删目录。
    """
    database = engine.url.database or ""
    engine.dispose()
    if not database or database == ":memory:":
        return
    tmp_dir = os.path.dirname(os.path.abspath(database))
    if os.path.basename(tmp_dir).startswith("atlas-demo-db-"):
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if tmp_dir in _DEMO_TEMP_DIRS:
            _DEMO_TEMP_DIRS.remove(tmp_dir)


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
    raise DatabaseAdapterError("DATABASE_PARAMS_INVALID", "params 必须是对象或对象数组（绑定参数）")


def _validate_limit(limit: object) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise DatabaseAdapterError("DATABASE_LIMIT_TYPE_INVALID", "limit 必须是 1-1000 的整数")
    if limit < 1 or limit > MAX_LIMIT:
        raise DatabaseAdapterError("DATABASE_LIMIT_RANGE_INVALID", "limit 必须在 1-1000 之间")
    return limit


class DatabaseClient:
    def __init__(
        self,
        engine: Engine,
        url: str = "",
        demo: bool = False,
        read_only: bool | None = None,
    ) -> None:
        self.engine = engine
        self.url = url
        self.demo = demo
        # 外部数据源（demo=False）默认只读；内置 demo engine 可写（播种/写能力）。
        # read_only 可显式覆盖（测试用）。
        self.read_only = (not demo) if read_only is None else read_only
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
            assert_read_only_sql(sql_text)
        except ReadOnlyViolation as exc:
            raise DatabaseAdapterError(exc.code, f"query 通道仅允许只读 SELECT：{exc}") from exc

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
        if self.read_only:
            # 外部数据源连接只读：写能力在触达数据库前 fail-closed
            raise DatabaseAdapterError(
                "DB_WRITE_FORBIDDEN", "外部数据源连接为只读，禁止 execute 写/DDL 操作"
            )

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

    def close(self) -> None:
        """释放连接池（内置 demo 库**不删**临时目录）。

        close 的调用方是 FastAPI lifespan 关闭，而 ``with TestClient(app)`` 会在同一
        进程内反复进出 lifespan（tests/test_api_demo.py 三处）：import 期单例
        ``_db_client`` 在退出块后仍会被后续请求复用，若 close 连目录一起删，重连即
        ``unable to open database file``。demo 库运行期只读，临时目录由 atexit 兜底
        回收；一次性 engine 要连目录一起释放时显式调 :func:`dispose_demo_engine`。
        """
        self.engine.dispose()

    def _record(self, operation: str, sql: str, **extra: object) -> None:
        self.last_operation = {
            "operation": operation,
            "sql": sql,
            "at": datetime.now(timezone.utc).isoformat(),
            **extra,
        }
