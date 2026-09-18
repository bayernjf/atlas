"""内置交互卡片目录（M8；04 §5.6 追加段 / 03 ``card_template`` / 12 §3.11 权威）。

v1 为随代码版本发布的只读目录：无 DB、无 CRUD、不受 ``/api/demo/reset`` 影响
（照 ``atlas.template`` 包）。卡片 id 稳定，前端只应假设 ``CARDS`` 中列出的 id。
v1 仅一张 ``refund-approval``（退款审批）。

卡片是图的附属实体（03 第六类实体）：``sections.bindings`` 的 ``{{路径}}`` 走
M0/L2 同一套作用域/插值语义（编辑期 L2 校验在前端，运行期渲染复用
``graph.interpolation.interpolate``）；``actions`` 只回写本审批的
decision/comment，不回写任意节点。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class FieldBinding(BaseModel):
    """只读展示行：label + 一个 ``{{路径}}`` 模板值。"""

    label: str
    value: str  # {{路径}}，复用 graph.interpolation.interpolate（缺失 fail-soft 保留占位）


class FieldsSection(BaseModel):
    """只读字段区（插值后逐行展示，不可编辑）。"""

    type: Literal["fields"]
    bindings: list[FieldBinding] = Field(default_factory=list)


class FormSection(BaseModel):
    """可编辑表单字段（v1 仅 textarea/input，扁平 object，无递归/数组/条件区块）。"""

    type: Literal["textarea", "input"]
    name: str  # action.output 以 {{form.<name>}} 引用
    label: str | None = None
    required: bool = False
    default: str = ""


class CardAction(BaseModel):
    """固定动作按钮：output 声明如何映射为审批 decision/comment。"""

    id: str  # approve / reject
    label: str
    style: Literal["primary", "danger", "default"] = "default"
    output: dict[str, Any]  # {"decision": "approved"|"rejected", "comment"?: "{{form.comment}}"}
    channels: dict[str, Any] | None = None  # 如 {"email": {"render": "link"}}


class CardFallback(BaseModel):
    """渠道降级提示：IM 详情兜底链接、邮件超时提示。"""

    im: dict[str, Any] | None = None  # {"detailUrl": "/approvals/{token}"}
    email: dict[str, Any] | None = None  # {"timeoutHint": true}


class CardTemplate(BaseModel):
    id: str  # kebab-case，目录内唯一（v1: refund-approval）
    name: str
    channels: list[Literal["web", "im", "email"]]
    sections: list[FieldsSection | FormSection] = Field(default_factory=list)
    actions: list[CardAction] = Field(default_factory=list)
    fallback: CardFallback | None = None


REFUND_APPROVAL = CardTemplate(
    id="refund-approval",
    name="退款审批卡片",
    channels=["web", "im", "email"],
    sections=[
        FieldsSection(
            type="fields",
            bindings=[
                FieldBinding(label="订单号", value="{{trigger-1.context.payload.order_id}}"),
                FieldBinding(label="退款金额", value="{{trigger-1.context.payload.amount}}"),
                FieldBinding(label="退款原因", value="{{trigger-1.context.payload.reason}}"),
                FieldBinding(label="AI 建议", value="{{ai_decision-1.decision.reason}}"),
                FieldBinding(label="审批限额", value="{{global.approval_limit}}"),
            ],
        ),
        FormSection(type="textarea", name="comment", label="审批意见", required=False, default=""),
    ],
    actions=[
        CardAction(
            id="approve",
            label="同意退款",
            style="primary",
            output={"decision": "approved", "comment": "{{form.comment}}"},
        ),
        CardAction(
            id="reject",
            label="拒绝退款",
            style="danger",
            output={"decision": "rejected", "comment": "{{form.comment}}"},
        ),
    ],
    fallback=CardFallback(
        im={"detailUrl": "/approvals/{token}"},
        email={"timeoutHint": True},
    ),
)


CARDS: tuple[CardTemplate, ...] = (REFUND_APPROVAL,)


def list_cards() -> list[CardTemplate]:
    """返回全部内置卡片（目录常量，调用方不得修改）。"""
    return list(CARDS)


def get_card(card_id: str) -> CardTemplate | None:
    """按 id 取卡片，未知 id 返 None（REST 映射 404 / 编译期映射 422）。"""
    return next((card for card in CARDS if card.id == card_id), None)
