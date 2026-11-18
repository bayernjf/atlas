"""SQLAlchemy 2.0 ORM models（集中管理；与 ``db/migrations/*.sql`` DDL 逐字映射）。

本模块只做**映射**：建表权威在 ``db/migrations``（迁移运行器 ``storage/migrations.py``
按文件名顺序幂等应用），``Base.metadata`` 从不用于 ``create_all``——因此这里的
``primary_key``/``server_default`` 标注只在 ORM 层生效（身份键、INSERT 列省略），
不参与任何 DDL。

落码批次（docs/09 骨架外的渐进 ORM 化，方案 C）：
- 批 1 试点：``message_templates``（迁移 044）、``llm_usage``（迁移 049+058），
  分别替换 ``message/pg_template_store.py`` 与 ``llm/pg_store.py`` 的内部 SQL。
- 批 2（规划中，暂不实施）：``storage/pg.py`` 内的简单表（如 iam_users/iam_sessions）
  逐个补 model；复杂语句（``ON CONFLICT`` rowcount 认领、pgvector ``<=>``、
  MAX+1 撞号重试）保留原生 SQL 与 ORM 同 session 混用。

类型映射纪律：时间列在 DDL 中是 ``TEXT``（ISO-8601 字符串），一律 ``Mapped[str]``
+ ``Text``，**不要**改成 ``DateTime``——字符串比较/字典序是既有行为契约。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Float, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """共享 declarative base（映射专用，不建表）。"""


class MessageTemplateORM(Base):
    """迁移 044 ``message_templates``：租户消息通知模板（docs/98 打包 A2）。

    数据库 PK (tenant_id, id)；``seq`` 租户内行锁自增（``ORDER BY seq DESC LIMIT 1
    FOR UPDATE``，非 PG 序列）；``name`` 租户内 LOWER 唯一由
    ``ux_message_templates_tenant_name`` 表达式索引承担（IntegrityError → 409）。
    """

    __tablename__ = "message_templates"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class LlmUsageORM(Base):
    """迁移 049 + 058 ``llm_usage``：LLM 用量成本账（docs/129 打包 AU）。

    数据库**无主键**（仅 (tenant_id, ts) 索引）；ORM 层以全列复合 primary_key 满足
    mapper 要求——因为从不 ``create_all``，该标注不落 DDL。读取一律走**列级
    select**（返回 Row、不经 identity map）：完全相同的重复行（同租户同 ts 同调用
    形状）不会被 identity map 去重，行数语义与原生 SQL 一致。
    """

    __tablename__ = "llm_usage"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[str] = mapped_column(Text, primary_key=True)
    caller: Mapped[str] = mapped_column(Text, primary_key=True)
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    graph_id: Mapped[str] = mapped_column(Text, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(Text, primary_key=True)
    # 迁移 058 补列（DEFAULT ''）：store.record 不写、靠 server_default，行为不变。
    assignment_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    billing_source: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
