"""PostgreSQL 连接层（记忆分层的关系/向量存储，依据 docs/11 §1）。

W1 范围：引擎/会话工厂 + 连接健康探针 + pgvector 可用性检查。
短期工作记忆（Redis）、Checkpointer 与各记忆表在后续周次接入。

连接 URL 必须经 DATABASE_URL 环境变量提供（见 .env.example），
不接受硬编码连接串。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from .settings import settings


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
