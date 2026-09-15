"""内置流程模板目录（04 §5.10 权威实现）。

v1 为随代码版本发布的只读目录：无 DB、无 CRUD、不受 /api/demo/reset 影响。
模板 id 稳定，前端只应假设 TEMPLATES 中列出的 id。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from atlas.template.graphs import (
    approval_timeout_reject_graph,
    http_orders_branch_graph,
    refund_template_graph,
    sql_approval_write_graph,
    sql_query_notify_graph,
)


class TemplateMeta(BaseModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    graph: dict[str, Any]


TEMPLATES: tuple[TemplateMeta, ...] = (
    TemplateMeta(
        id="refund-auto",
        name="退款自动审批",
        description="电商退款 golden 流程：AI 按原因与金额决策，限额内自动退款、超额转人工。",
        tags=["退款", "AI 决策", "shop"],
        graph=refund_template_graph(),
    ),
    TemplateMeta(
        id="http-orders-branch",
        name="HTTP 拉单 + 条件分流",
        description="通用 HTTP 拉取演示订单，按 HTTP 状态码分流发送成功通知或失败告警。",
        tags=["HTTP", "条件分支", "消息"],
        graph=http_orders_branch_graph(),
    ),
    TemplateMeta(
        id="sql-query-notify",
        name="SQL 查询 + 条件通知",
        description="按阈值查询 demo 订单库（绑定参数），有大额订单则通知运营，否则发巡检正常消息。运行时输入 min_amount。",
        tags=["数据库", "条件分支", "消息"],
        graph=sql_query_notify_graph(),
    ),
    TemplateMeta(
        id="sql-approval-write",
        name="SQL 写入审批后执行",
        description="写库操作先经人工审批：通过后执行 UPDATE，拒绝（含超时）则发消息通知。运行时输入 order_id。",
        tags=["数据库", "人机协作", "审批"],
        graph=sql_approval_write_graph(),
    ),
    TemplateMeta(
        id="approval-timeout-reject",
        name="审批超时默认拒绝演示",
        description="人机协作节点 10 秒不审批自动拒绝，分别走通过/拒绝两条消息通知分支。",
        tags=["人机协作", "超时", "消息"],
        graph=approval_timeout_reject_graph(),
    ),
)


def list_templates() -> list[TemplateMeta]:
    """返回全部内置模板（目录常量，调用方不得修改）。"""
    return list(TEMPLATES)


def get_template(template_id: str) -> TemplateMeta | None:
    """按 id 取模板，未知 id 返 None（REST 层映射 404）。"""
    return next((template for template in TEMPLATES if template.id == template_id), None)
