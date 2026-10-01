"""PostgreSQL 连接层（记忆分层的关系/向量存储，依据 docs/11 §1）。

W1 范围：引擎/会话工厂 + 连接健康探针 + pgvector 可用性检查。
短期工作记忆没有独立存储层——其职责由 run 的 ``outputs``/``globals`` 承担，Redis 未引入
（docs/10 §4 T31），LangGraph 也未配 checkpointer；长期事实记忆已落进程内与 PG 两档
（``memory/items.py`` / ``storage/pg.py:PgMemoryStore``），其余档见 docs/14 D35。

连接 URL 必须经 DATABASE_URL 环境变量提供（见 .env.example），
不接受硬编码连接串。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from typing import TypeVar

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def create_database_engine(url: str | None = None, *, pool_size: int = 10) -> Engine:
    database_url = url or settings.require_database_url()
    parsed = make_url(database_url)
    if parsed.drivername != "postgresql+psycopg":
        raise RuntimeError(
            f"unsupported DB driver {parsed.drivername!r}; use 'postgresql+psycopg://' (psycopg 3)"
        )
    return create_engine(database_url, pool_size=pool_size, pool_pre_ping=True)


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def ping(engine: Engine) -> bool:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


# docs/79（打包 S）§1 D-2/D-3：空卷首启的 PG recovery 窗口有界重试。
# 可重试集合＝冷启/恢复/连接类文案；SQL 语法、约束、权限、认证失败等一律不重试。
CONNECTIVITY_RETRY_MARKERS: tuple[str, ...] = (
    "recovery mode",
    "starting up",
    "connection refused",
    "could not connect",
    "connection failed",
    "connection reset",
    "server closed the connection",
    "terminating connection",
)

# "connection failed:" 是 libpq 连接期失败的通用前缀，**认证失败与库不存在也用它**，
# 所以必须显式否定——它们是配置错误，重试只会把失败延后到预算耗尽。
CONNECTIVITY_NON_RETRYABLE_MARKERS: tuple[str, ...] = (
    "password authentication failed",
    "authentication failed",
    "no pg_hba.conf entry",
    "does not exist",
    "permission denied",
)

_DB_READY_TIMEOUT_ENV = "ATLAS_DB_READY_TIMEOUT_SECONDS"
_DB_READY_INTERVAL_ENV = "ATLAS_DB_READY_INTERVAL_SECONDS"
_DB_READY_TIMEOUT_DEFAULT = 120.0
_DB_READY_INTERVAL_DEFAULT = 2.0


def is_retryable_connectivity_error(exc: BaseException) -> bool:
    """判断异常是否为可重试的**连通性**类失败（docs/79 D-2）。

    只认 SQLAlchemy 的 `OperationalError` / `InterfaceError`，且消息命中冷启/恢复/
    连接类文案，并**不**命中认证/库不存在等配置类否定标记。SQL 语法、约束、权限、
    认证失败等不是本类，调用方应立即失败。
    """
    if not isinstance(exc, (OperationalError, InterfaceError)):
        return False
    text_ = str(exc).lower()
    orig = getattr(exc, "orig", None)
    if orig is not None:
        text_ = f"{text_} {orig}".lower()
    if any(marker in text_ for marker in CONNECTIVITY_NON_RETRYABLE_MARKERS):
        return False
    return any(marker in text_ for marker in CONNECTIVITY_RETRY_MARKERS)


def _positive_seconds(raw: str | None, default: float, env_name: str) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r 非数字，回退缺省 %.1fs", env_name, raw, default)
        return default
    if value < 0:
        logger.warning("%s=%r 为负，回退缺省 %.1fs", env_name, raw, default)
        return default
    return value


def database_ready_timeout_seconds() -> float:
    """有界等待的总预算（docs/79 D-3；缺省 120s，覆盖实测 90.21s recovery 窗口）。"""
    return _positive_seconds(
        os.environ.get(_DB_READY_TIMEOUT_ENV), _DB_READY_TIMEOUT_DEFAULT, _DB_READY_TIMEOUT_ENV
    )


def database_ready_interval_seconds() -> float:
    """两次重试之间的固定间隔（docs/79 D-3；缺省 2s）。"""
    return _positive_seconds(
        os.environ.get(_DB_READY_INTERVAL_ENV), _DB_READY_INTERVAL_DEFAULT, _DB_READY_INTERVAL_ENV
    )


def wait_for_database(
    operation: Callable[[], T],
    *,
    timeout_seconds: float | None = None,
    interval_seconds: float | None = None,
    retry_logger: logging.Logger | None = None,
) -> T:
    """在有界窗口内重复调用 `operation()` 直到成功（docs/79 D-2/D-3）。

    只重试连通性类错误（见 `is_retryable_connectivity_error`）；其它异常立即原样抛出。
    预算耗尽仍不通 ⇒ **原样抛最后一次异常**（fail-closed），绝不无限等。
    成功返回 `operation()` 的返回值；每次可重试失败与最终成功各记一条日志。
    """
    log = retry_logger or logger
    budget = max(
        0.0,
        timeout_seconds if timeout_seconds is not None else database_ready_timeout_seconds(),
    )
    interval = max(
        0.0,
        interval_seconds if interval_seconds is not None else database_ready_interval_seconds(),
    )
    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            result = operation()
        except Exception as exc:
            if not is_retryable_connectivity_error(exc):
                raise
            waited = time.monotonic() - started
            if waited >= budget:
                log.warning(
                    "数据库连通性等待超预算（已等 %.1fs、尝试 %d 次），fail-closed 抛出：%s",
                    waited,
                    attempts,
                    exc,
                )
                raise
            log.warning(
                "数据库暂不可连（已等 %.1fs、尝试 %d 次），%.1fs 后重试：%s",
                waited,
                attempts,
                interval,
                exc,
            )
            if interval > 0:
                time.sleep(interval)
            continue
        waited = time.monotonic() - started
        if waited > 0:
            log.info("数据库连通性恢复：等待 %.1fs、尝试 %d 次后成功", waited, attempts)
        return result


def pgvector_version(engine: Engine) -> str | None:
    """返回已安装的 vector 扩展版本；扩展缺失时返回 None。"""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).first()
    return row[0] if row else None


def get_session(engine: Engine) -> Iterator[Session]:
    factory = session_factory(engine)
    with factory() as session:
        yield session
